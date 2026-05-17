"""Silence-based VAD: tracks RMS, decides when an utterance has ended."""

try:
    import audioop  # stdlib (Python ≤3.12)
except ModuleNotFoundError:
    import audioop_lts as audioop  # backport for Python 3.13+


def audio_rms(pcm16_bytes: bytes) -> float:
    """Return RMS of int16 little-endian PCM audio. 2 bytes per sample."""
    if not pcm16_bytes:
        return 0.0
    return float(audioop.rms(pcm16_bytes, 2))


class SilenceDetector:
    """Track speech onset + silence-based end-of-utterance.

    `silence_threshold`: RMS below this counts as silent.
    `silent_chunks_required`: consecutive silent chunks to trigger end.
    """

    def __init__(self, silence_threshold: int = 800, silent_chunks_required: int = 30) -> None:
        self.silence_threshold = silence_threshold
        self.silent_chunks_required = silent_chunks_required
        self.silent_count = 0
        self.started = False

    def observe(self, chunk: bytes) -> float:
        """Process one chunk, return its RMS. Mutates internal state."""
        rms = audio_rms(chunk)
        if rms >= self.silence_threshold:
            self.silent_count = 0
            self.started = True
        elif self.started:
            self.silent_count += 1
        return rms

    def is_done(self, _rms: float) -> bool:
        """True when we've seen enough silence after speech started."""
        return self.started and self.silent_count >= self.silent_chunks_required

    def reset(self) -> None:
        self.silent_count = 0
        self.started = False
