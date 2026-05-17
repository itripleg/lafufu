"""Whisper STT wrapper."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Whisper:
    """Lazy-loads the Whisper model on first transcribe()."""

    def __init__(self, model_name: str = "tiny") -> None:
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        import whisper  # lazy

        log.info("whisper.loading model=%s", self.model_name)
        self._model = whisper.load_model(self.model_name)
        log.info("whisper.loaded model=%s", self.model_name)

    def transcribe(self, audio_path: str | Path) -> str:
        if self._model is None:
            self.load()
        result = self._model.transcribe(str(audio_path), fp16=False, language="en")
        return result.get("text", "").strip()
