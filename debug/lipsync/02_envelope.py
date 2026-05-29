"""02_envelope.py — RMS -> attack/release envelope -> jaw.

Same as `01_direct.py`, but the jaw follows the RMS through an
attack/release envelope (one-pole exponential smoother).

  - ATTACK_MS  : how fast the envelope rises toward the target
                 (smaller = snappier on transients)
  - RELEASE_MS : how fast it falls back to zero after the sound stops
                 (bigger = mouth lingers open, looks more "natural")

This is closest to what the production agent does. Tweak ATTACK_MS and
RELEASE_MS until it looks good — those numbers go back into
animator.lipsync.attack_ms / animator.lipsync.release_ms settings.

Run on the Pi:
    uv run python debug/lipsync/02_envelope.py /path/to/sample.wav
"""

from __future__ import annotations

import contextlib
import math
import sys
import time

from common import (
    JawBus,
    aplay_popen,
    chunk_rms,
    iter_chunks,
    open_pct_to_dxl,
    open_wav,
)

# --- CONFIG (edit and re-run) ---
CHUNK_MS = 40
ALSA_BUFFER_MS = 1000
ALSA_PERIOD_MS = 40
OFFSET_MS = 0  # +N: jaw uses RMS from N ms AHEAD of audio; -N: behind
RMS_MIN = 0.005
RMS_MAX = 0.30
ATTACK_MS = 20  # rise time constant (~63% toward target per tau)
RELEASE_MS = 80  # fall time constant
ALSA_DEVICE = "default"
# --------------------------------


def _alpha(time_constant_ms: float, dt_ms: float) -> float:
    """Per-tick alpha for a one-pole exponential smoother given a time const."""
    if time_constant_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_ms / time_constant_ms)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: 02_envelope.py <audio.wav>")
    wav_path = sys.argv[1]

    reader, info = open_wav(wav_path)
    chunk_frames = max(1, info.sample_rate * CHUNK_MS // 1000)
    buffer_frames = info.sample_rate * ALSA_BUFFER_MS // 1000
    period_frames = info.sample_rate * ALSA_PERIOD_MS // 1000
    chunks = list(iter_chunks(reader, chunk_frames))
    reader.close()

    bus = JawBus.open()
    proc = aplay_popen(info.sample_rate, buffer_frames, period_frames, ALSA_DEVICE)

    a_up = _alpha(ATTACK_MS, CHUNK_MS)
    a_down = _alpha(RELEASE_MS, CHUNK_MS)
    envelope = 0.0

    try:
        dt = CHUNK_MS / 1000.0
        offset_chunks = OFFSET_MS // CHUNK_MS
        next_tick = time.monotonic()
        for i, chunk in enumerate(chunks):
            j = i + offset_chunks
            rms = chunk_rms(chunks[j]) if 0 <= j < len(chunks) else 0.0
            target = (rms - RMS_MIN) / max(1e-6, RMS_MAX - RMS_MIN)
            target = max(0.0, min(1.0, target))
            alpha = a_up if target > envelope else a_down
            envelope += alpha * (target - envelope)
            bus.write_goal(open_pct_to_dxl(envelope))

            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                break

            next_tick += dt
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        bus.write_goal(open_pct_to_dxl(0.0))
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.wait(timeout=3)
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
        bus.close()


if __name__ == "__main__":
    main()
