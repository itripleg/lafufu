"""STT backends with pluggable selector.

Two implementations:
  - OpenAIWhisper: the original openai-whisper package (CPU-friendly tiny.en).
  - FasterWhisper: CTranslate2-based reimplementation (~3-4x faster on aarch64).

Both implement the same protocol and accept numpy float32 audio (16kHz mono)
directly — no temp-file round-trip required.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class SttProtocol(Protocol):
    backend_id: str
    model_name: str

    def load(self) -> None: ...
    def warmup(self) -> float: ...
    def transcribe(self, audio: np.ndarray) -> str: ...


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def available_backends() -> list[dict]:
    """Report which backends are importable. Used by the admin /stt_backends endpoint."""
    return [
        {
            "id": "openai-whisper",
            "label": "openai-whisper (reference)",
            "available": _has_module("whisper"),
        },
        {
            "id": "faster-whisper",
            "label": "faster-whisper (CTranslate2, ~3-4x faster)",
            "available": _has_module("faster_whisper"),
        },
    ]


def make_stt(backend: str, model_name: str = "tiny") -> SttProtocol:
    """Build an STT instance for the given backend id.

    Unknown / unavailable backends fall back to openai-whisper so a broken
    setting can never brick the agent.
    """
    if backend == "faster-whisper" and _has_module("faster_whisper"):
        return FasterWhisper(model_name=model_name)
    if backend != "openai-whisper":
        log.warning("stt.backend.fallback requested=%s -> openai-whisper", backend)
    return OpenAIWhisper(model_name=model_name)


class OpenAIWhisper:
    """Reference openai-whisper backend. Accepts numpy float32 16kHz audio."""

    backend_id = "openai-whisper"

    def __init__(self, model_name: str = "tiny") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        import whisper

        log.info("stt.load backend=%s model=%s", self.backend_id, self.model_name)
        self._model = whisper.load_model(self.model_name)

    def warmup(self) -> float:
        t0 = time.monotonic()
        self.load()
        silence = np.zeros(8000, dtype=np.float32)
        self._model.transcribe(silence, fp16=False, language="en", temperature=0.0)
        return time.monotonic() - t0

    def transcribe(self, audio) -> str:
        """audio: float32 numpy array (mono 16kHz) OR a file path."""
        if self._model is None:
            self.load()
        result = self._model.transcribe(
            audio if not isinstance(audio, (str, Path)) else str(audio),
            fp16=False,
            language="en",
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return result.get("text", "").strip()


class FasterWhisper:
    """faster-whisper (CTranslate2) backend. Same interface as OpenAIWhisper."""

    backend_id = "faster-whisper"

    def __init__(self, model_name: str = "tiny.en") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        log.info("stt.load backend=%s model=%s", self.backend_id, self.model_name)
        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def warmup(self) -> float:
        t0 = time.monotonic()
        self.load()
        silence = np.zeros(8000, dtype=np.float32)
        for _ in self._model.transcribe(silence, language="en", beam_size=1)[0]:
            pass
        return time.monotonic() - t0

    def transcribe(self, audio) -> str:
        if self._model is None:
            self.load()
        target = audio if not isinstance(audio, (str, Path)) else str(audio)
        segments, _info = self._model.transcribe(
            target,
            language="en",
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            temperature=0.0,
        )
        return "".join(s.text for s in segments).strip()
