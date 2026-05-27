"""Tests for the agent's __main__ startup helpers.

These cover the "degrade gracefully to RMS-based onset" contract for the wake
word construction block. Two failure modes are exercised:

  1. The `lafufu_agent.wakeword` module itself fails to import (e.g. broken
     numpy ABI, syntax error introduced by a future refactor). The agent must
     log a warning and proceed with `wake_detector = None`, NOT propagate
     ImportError out of main().

  2. The detector's `.load()` call raises (e.g. missing onnx asset, openwakeword
     can't find the model). The warning must point the operator at concrete
     remediation steps — the admin UI setting, and `uv sync` for the dep.
"""

from __future__ import annotations

import logging
import sys
import types

# ---------- Finding #10: wakeword module import failure ----------


def test_wakeword_module_import_failure_does_not_crash_main_imports(monkeypatch, caplog):
    """If `from .wakeword import ...` raises ImportError, the helper must
    swallow it, log a warning, and return (None, None) so the agent falls
    back to RMS-based onset instead of crashing."""
    from lafufu_agent import __main__ as main_mod

    # Replace lafufu_agent.wakeword with a stub module whose attribute access
    # raises ImportError — simulates a partially-broken install where the
    # module is importable as a sentinel but the symbols we want are gone.
    class _BrokenModule(types.ModuleType):
        def __getattr__(self, _name):
            raise ImportError(f"simulated wakeword import failure for {_name!r}")

    broken = _BrokenModule("lafufu_agent.wakeword")
    monkeypatch.setitem(sys.modules, "lafufu_agent.wakeword", broken)

    caplog.set_level(logging.WARNING, logger="lafufu_agent.__main__")
    make_factory, detector = main_mod._build_wake_detector_or_none()

    assert make_factory is None
    assert detector is None
    assert any("wakeword.module_import_failed" in rec.getMessage() for rec in caplog.records), (
        f"Expected wakeword.module_import_failed warning, got: {[r.getMessage() for r in caplog.records]}"
    )


# ---------- Finding #11: load_failed warning needs remediation hints ----------


def test_load_failed_warning_includes_remediation_hints(monkeypatch, caplog):
    """When the wakeword factory raises during `.load()`, the warning must
    name (a) the admin UI setting and (b) `uv sync` so the operator knows
    where to look next instead of just seeing "load_failed" with no context.
    """
    # Force has_openwakeword() True so the construction block enters the
    # factory branch, and make the factory itself raise.
    import lafufu_agent.wakeword as ww_mod
    from lafufu_agent import __main__ as main_mod

    monkeypatch.setattr(ww_mod, "has_openwakeword", lambda: True)

    class _BoomDetector:
        def __init__(self, *_a, **_kw):
            pass

        def load(self):
            raise RuntimeError("boom: simulated load failure")

    monkeypatch.setattr(ww_mod, "OpenWakeWordDetector", _BoomDetector)
    monkeypatch.setattr(ww_mod, "resolve_model_ref", lambda s: s)

    caplog.set_level(logging.WARNING, logger="lafufu_agent.__main__")
    make_factory, detector = main_mod._build_wake_detector_or_none()

    # Construction should swallow the failure: factory is kept (live-swap can
    # try other models later), detector is None.
    assert detector is None
    assert make_factory is not None

    load_failed_msgs = [
        rec.getMessage() for rec in caplog.records if "wakeword.load_failed" in rec.getMessage()
    ]
    assert load_failed_msgs, "Expected a wakeword.load_failed warning to be logged"
    msg = load_failed_msgs[-1]
    assert "agent.wakeword.model" in msg, f"Warning missing admin-UI setting hint: {msg!r}"
    assert "uv sync" in msg, f"Warning missing `uv sync` reinstall hint: {msg!r}"
