"""Adaptive lipsync RMS normalization.

The legacy monolith rendered each TTS utterance to a WAV up front, then
normalized every frame's RMS against that utterance's own 10th/95th
percentiles — so the jaw always used its full travel and tracked *relative*
loudness regardless of the voice's absolute level.

The modular agent streams audio chunk-by-chunk and cannot see the whole
utterance, so the original refactor fell back to a fixed ``rms / 8000``
divisor — non-adaptive: a quiet voice barely moved the mouth, a loud one
clipped it wide open.

``LipsyncNormalizer`` restores the legacy behaviour in a streaming form: it
keeps a rolling window of recent RMS values and normalizes against the
window's percentiles. The window persists across utterances, so it stays
warm — only the very first utterance after start-up sees a cold window.

The sorted view of the window is maintained in parallel using ``bisect.insort``
(O(n) insertion) rather than re-sorting the full deque on every call
(O(n log n) + heap allocation at 25 Hz on a Pi).
"""

import bisect
from collections import deque


def percentile_sorted(values_sorted: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted list (matches the legacy
    monolith's ``percentile_sorted``)."""
    if not values_sorted:
        return 0.0
    p = max(0.0, min(1.0, p))
    idx = round(p * (len(values_sorted) - 1))
    return values_sorted[idx]


class LipsyncNormalizer:
    """Maps a raw RMS sample to a 0..1 mouth-open target.

    ``window``   — number of recent RMS samples to normalize against (~4s at
                   the 40ms chunk cadence).
    ``deadzone`` — normalized values at/below this snap fully closed, so quiet
                   gaps cleanly close the mouth.
    ``min_span`` — floor on the (ceil-floor) range in raw RMS units; guards a
                   cold or near-silent window from a divide-by-near-zero blow-up.
    """

    def __init__(
        self,
        window: int = 100,
        p_low: float = 0.10,
        p_high: float = 0.95,
        deadzone: float = 0.05,
        min_span: float = 500.0,
    ) -> None:
        self._win: deque[float] = deque(maxlen=window)
        self._sorted: list[float] = []
        self._maxlen = window
        self._p_low = p_low
        self._p_high = p_high
        self._deadzone = deadzone
        self._min_span = min_span

    def update(self, raw_rms: float) -> float:
        """Record a raw RMS sample and return its 0..1 mouth-open target."""
        raw = max(0.0, float(raw_rms))

        # If the window is full, the oldest value is about to be evicted from
        # the deque. Remove it from the sorted list before appending the new one.
        if len(self._win) == self._maxlen:
            evicted = self._win[0]
            idx = bisect.bisect_left(self._sorted, evicted)
            if idx < len(self._sorted):
                self._sorted.pop(idx)

        self._win.append(raw)
        bisect.insort(self._sorted, raw)

        floor = percentile_sorted(self._sorted, self._p_low)
        ceil = percentile_sorted(self._sorted, self._p_high)
        span = max(ceil - floor, self._min_span)

        x = (raw - floor) / span
        x = max(0.0, min(1.0, x))
        if x <= self._deadzone:
            return 0.0
        return (x - self._deadzone) / (1.0 - self._deadzone)
