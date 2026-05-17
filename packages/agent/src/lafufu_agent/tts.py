"""Piper TTS wrapper.

Returns a list of (audio_chunk_bytes, mouth_target_0to1) tuples so the agent
can stream playback + publish RMS to NATS as it goes.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Piper:
    def __init__(self, model_path: Path, chunk_ms: int = 40) -> None:
        self.model_path = Path(model_path)
        self.chunk_ms = chunk_ms
        self._voice = None
        self._sample_rate = 22050  # piper default; refined on load
        self._sample_width = 2

    def load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice  # lazy

        self._voice = PiperVoice.load(str(self.model_path))
        self._sample_rate = self._voice.config.sample_rate

    def synthesize(self, text: str) -> list[tuple[bytes, float]]:
        """Synthesize text → list of (audio_chunk, mouth_target).

        Each chunk is ~`chunk_ms` of int16 PCM, mouth_target ∈ [0,1].
        """
        if self._voice is None:
            self.load()
        # Piper streams audio_int16_bytes
        audio = b"".join(self._voice.synthesize_stream_raw(text))
        return self._chunkify(audio)

    def _chunkify(self, audio: bytes) -> list[tuple[bytes, float]]:
        import audioop  # lazy; audioop-lts on Python 3.13+

        bytes_per_sample = self._sample_width
        samples_per_chunk = int(self._sample_rate * self.chunk_ms / 1000)
        bytes_per_chunk = samples_per_chunk * bytes_per_sample
        out: list[tuple[bytes, float]] = []
        # Normalize RMS by max int16 (32767) → [0,1]
        for i in range(0, len(audio), bytes_per_chunk):
            chunk = audio[i : i + bytes_per_chunk]
            if not chunk:
                continue
            rms = audioop.rms(chunk, bytes_per_sample)
            normalized = min(1.0, rms / 8000.0)  # 8000 RMS ≈ comfortable speech
            out.append((chunk, normalized))
        return out

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def sample_width(self) -> int:
        return self._sample_width
