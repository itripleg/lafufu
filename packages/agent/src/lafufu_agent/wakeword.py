"""Wake-word detector that gates Whisper.

Wraps openwakeword so the mic loop can ignore everything until someone says
the keyword. `openwakeword` is a required dep of `lafufu-agent`, but this
module does NOT eagerly import it — only `OpenWakeWordDetector.load()` does.
That's load-bearing: `__main__.py` does `from .wakeword import ...` and then
calls `has_openwakeword()` as a degrade-to-RMS feature flag. If a future
refactor hoists `from openwakeword.model import Model` to the top of this
module, the import will raise on hosts where openwakeword is missing/corrupt
BEFORE the guard runs — and the "wakeword.dep_missing → fall back to RMS"
contract in __main__.py silently breaks. Keep openwakeword imports lazy.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Drive-letter prefix (e.g. "C:\", "d:/"). Compiled once at import.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_absolute_cross_platform(value: str) -> bool:
    """Return True for values that any reasonable OS would consider absolute,
    even when the host OS's Path.is_absolute() disagrees. POSIX-rooted paths
    ('/srv/...') are False under WindowsPath; drive-letter paths ('C:\\...')
    are False under PosixPath. A cross-platform DB share that puts a
    Windows-style path on a POSIX host (or vice versa) would otherwise get
    silently misrouted into the workspace-root branch."""
    return value.startswith("/") or value.startswith("\\") or bool(_DRIVE_LETTER_RE.match(value))


def has_openwakeword() -> bool:
    return importlib.util.find_spec("openwakeword") is not None


def resolve_model_ref(value: str, *, walk_start: Path | None = None) -> str:
    """Normalize a wake-word model reference to something OpenWakeWordDetector can load.

    - Empty / falsy input → returns "" (caller picks default).
    - Absolute path → returned unchanged.
    - Bundled name (no path separator AND not ending in .onnx/.tflite) → returned
      unchanged so openwakeword looks it up in its bundled-model directory.
    - Relative path → resolved against the workspace root, located by walking up
      from `walk_start` (defaults to this module's __file__) until a directory
      containing BOTH pyproject.toml AND a 'packages' subdirectory is found. If
      no marker is found, the value is returned unchanged AND a WARNING is
      logged — that case reproduces the pre-fix breakage where the agent crashes
      because openwakeword tries to resolve against CWD, so operators need a
      breadcrumb to diagnose it.

    `walk_start` is exposed for tests; production callers always omit it.
    """
    if not value:
        return ""
    # Env-var / copy-paste leaves leading/trailing whitespace that would make
    # Path(...).is_absolute() return False on otherwise-absolute paths AND
    # would prevent the bundled-name short-circuit from matching. Strip once
    # here so every downstream branch sees a clean value.
    value = value.strip()
    if not value:
        return ""
    # Cross-platform absolute check BEFORE Path.is_absolute(), because that
    # method is OS-dependent: '/srv/x' is not absolute under WindowsPath, and
    # 'C:\x' is not absolute under PosixPath. Either of those would otherwise
    # fall through to the workspace-root walk and get mangled.
    if _looks_absolute_cross_platform(value):
        return value
    p = Path(value)
    if p.is_absolute():
        return value
    lower = value.lower()
    has_sep = ("/" in value) or ("\\" in value)
    if not has_sep and not (lower.endswith(".onnx") or lower.endswith(".tflite")):
        return value
    here = (walk_start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return str(parent / value)
    log.warning(
        "wakeword.resolve.no_workspace_root value=%s — returning unchanged; "
        "openwakeword will try to resolve against CWD",
        value,
    )
    return value


class OpenWakeWordDetector:
    """Holds an openwakeword Model and reports per-chunk max scores.

    Audio must be 16kHz, mono, int16 PCM bytes. Frame length is flexible —
    openwakeword buffers internally to 80ms windows.
    """

    def __init__(
        self,
        model_name: str = "hey_jarvis_v0.1",
        threshold: float = 0.5,
        inference_framework: str = "onnx",
    ) -> None:
        self.model_name = model_name
        self.threshold = float(threshold)
        self.inference_framework = inference_framework
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from openwakeword.model import Model

        log.info("wakeword.load model=%s framework=%s", self.model_name, self.inference_framework)
        self._model = Model(
            wakeword_models=[self.model_name],
            inference_framework=self.inference_framework,
        )

    def feed(self, pcm16_16k: bytes) -> float:
        """Feed a chunk of 16kHz mono int16 PCM. Returns the highest score
        emitted by the model for this frame (across all enabled keywords).
        """
        if self._model is None:
            self.load()
        if not pcm16_16k:
            return 0.0
        arr = np.frombuffer(pcm16_16k, dtype=np.int16)
        scores = self._model.predict(arr)
        if not scores:
            return 0.0
        return float(max(scores.values()))

    def reset(self) -> None:
        """Clear the model's internal buffer (e.g. after a successful trigger
        so leftover audio doesn't immediately re-fire)."""
        if self._model is not None and hasattr(self._model, "reset"):
            self._model.reset()
