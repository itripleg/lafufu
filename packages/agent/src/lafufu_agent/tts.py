"""Piper TTS wrapper.

Two APIs:
  synthesize(text)         -> list of (chunk_bytes, mouth_target_0to1) - buffered
  synthesize_stream(text)  -> generator yielding the same tuples as Piper produces
                              them, for low-latency streaming playback
"""

import logging
from collections.abc import Iterator
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
        """Buffered: join all audio, rechunk, return list. Used by tests + legacy callers."""
        return list(self.synthesize_stream(text))

    def synthesize_stream(self, text: str) -> Iterator[tuple[bytes, float]]:
        """Stream: yield (chunk, mouth_target) tuples as Piper synthesizes.

        Buffers across Piper's internal chunk boundaries so emitted chunks are
        all exactly `chunk_ms` long (the animator depends on a steady cadence).
        The final partial chunk is yielded as-is.
        """
        if self._voice is None:
            self.load()

        try:
            import audioop
        except ModuleNotFoundError:
            import audioop_lts as audioop

        bytes_per_sample = self._sample_width
        samples_per_chunk = int(self._sample_rate * self.chunk_ms / 1000)
        bytes_per_chunk = samples_per_chunk * bytes_per_sample

        buf = bytearray()
        for piper_chunk in self._voice.synthesize(text):
            buf.extend(piper_chunk.audio_int16_bytes)
            while len(buf) >= bytes_per_chunk:
                out = bytes(buf[:bytes_per_chunk])
                del buf[:bytes_per_chunk]
                rms = audioop.rms(out, bytes_per_sample)
                yield out, min(1.0, rms / 8000.0)
        if buf:
            tail = bytes(buf)
            rms = audioop.rms(tail, bytes_per_sample)
            yield tail, min(1.0, rms / 8000.0)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def sample_width(self) -> int:
        return self._sample_width
