"""Wake-word detector that gates Whisper.

Wraps openwakeword so the mic loop can ignore everything until someone says
the keyword. Optional dependency — install with `uv sync --extra wakeword`.
"""

from __future__ import annotations

import importlib.util
import logging

import numpy as np

log = logging.getLogger(__name__)


def has_openwakeword() -> bool:
    return importlib.util.find_spec("openwakeword") is not None


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
