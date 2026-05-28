"""Wake-word detector that gates Whisper.

Wraps openwakeword so the mic loop can ignore everything until someone says
the keyword.

Keep openwakeword imports lazy (only inside `OpenWakeWordDetector.load()`):
`__main__.py` imports this module then calls `has_openwakeword()` as a
degrade-to-RMS feature flag. A module-level `from openwakeword.model import
Model` would raise on hosts where openwakeword is missing/corrupt BEFORE that
guard runs, silently breaking the fall-back-to-RMS contract.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Drive-letter prefix (e.g. "C:\", "d:/"). Compiled once at import.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_absolute_cross_platform(value: str) -> bool:
    """Return True for paths any reasonable OS would call absolute, even when
    the host's Path.is_absolute() disagrees: '/srv/...' is not absolute under
    WindowsPath, and 'C:\\...' is not absolute under PosixPath. A path stored
    by one OS and read on another would otherwise get misrouted into the
    workspace-root branch.

    A leading lone backslash ('\\foo') is the Windows drive-relative-root
    convention with no POSIX analog, so it counts as absolute ONLY on Windows.
    """
    if value.startswith("/"):
        return True
    if _DRIVE_LETTER_RE.match(value):
        return True
    if value.startswith("\\") and sys.platform == "win32":
        return True
    return False


def has_openwakeword() -> bool:
    return importlib.util.find_spec("openwakeword") is not None


def resolve_model_ref(value: str, *, walk_start: Path | None = None) -> str:
    """Normalize a wake-word model reference to something OpenWakeWordDetector can load.

    - Empty / falsy input → returns "" (caller picks default).
    - Absolute path → returned unchanged.
    - Bundled name (no path separator AND not ending in .onnx/.tflite) → returned
      unchanged so openwakeword looks it up in its bundled-model directory.
      openwakeword's bundled identifiers are bare STEMS (e.g. "hey_jarvis_v0.1")
      with no extension, so a `.onnx` or `.tflite` suffix is treated as a path
      signal — NOT a bundled-name signal. An operator who types `my_custom.onnx`
      thinking it's a bundled name gets a workspace-root-prefixed path back; use
      the bare stem for a bundled identifier.
    - Relative path → resolved against the workspace root, found by walking up
      from `walk_start` (defaults to this module's __file__) until a directory
      with BOTH pyproject.toml AND a 'packages' subdirectory. If no marker is
      found, the value is returned unchanged AND a WARNING is logged — otherwise
      openwakeword resolves against CWD and fails opaquely.

    `walk_start` is exposed for tests; production callers always omit it.
    """
    if not value:
        return ""
    # Strip once up front: leading/trailing whitespace from env vars / copy-paste
    # breaks Path.is_absolute() and the bundled-name short-circuit downstream.
    value = value.strip()
    if not value:
        return ""
    # Cross-platform absolute check must run BEFORE Path.is_absolute(), which is
    # OS-dependent (see _looks_absolute_cross_platform).
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
