"""03_gate.py — binary mouth open/close on an RMS threshold.

The simplest possible "talking" puppet: mouth fully OPEN when RMS is above
GATE_THRESHOLD, fully CLOSED below it. No amplitude tracking at all.

Why bother:
  - If the GATE looks tightly sync'd with the audio, the timing pipeline
    (aplay buffer + servo response + chunk loop) is fine and the desync
    you've been seeing is in the AMPLITUDE-tracking layer (RMS smoothing,
    normalize, envelope).
  - If even the GATE looks early/late, the bug is in the audio buffer or
    the servo response — not in the algorithm.

Tune GATE_THRESHOLD by ear/eye: the mouth should open with the first
audible syllable and close in pauses, not in between syllables.

Run on the Pi:
    uv run python debug/lipsync/03_gate.py /path/to/sample.wav
"""

from __future__ import annotations

import contextlib
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
GATE_THRESHOLD = 0.02  # RMS threshold; tweak by listening
OPEN_PCT = 1.0  # how far to open when gate fires (0.6..1.0 is usable)
ALSA_DEVICE = "default"
# --------------------------------


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: 03_gate.py <audio.wav>")
    wav_path = sys.argv[1]

    reader, info = open_wav(wav_path)
    chunk_frames = max(1, info.sample_rate * CHUNK_MS // 1000)
    buffer_frames = info.sample_rate * ALSA_BUFFER_MS // 1000
    period_frames = info.sample_rate * ALSA_PERIOD_MS // 1000
    chunks = list(iter_chunks(reader, chunk_frames))
    reader.close()

    bus = JawBus.open()
    proc = aplay_popen(info.sample_rate, buffer_frames, period_frames, ALSA_DEVICE)

    try:
        dt = CHUNK_MS / 1000.0
        offset_chunks = OFFSET_MS // CHUNK_MS
        next_tick = time.monotonic()
        for i, chunk in enumerate(chunks):
            j = i + offset_chunks
            rms = chunk_rms(chunks[j]) if 0 <= j < len(chunks) else 0.0
            pct = OPEN_PCT if rms > GATE_THRESHOLD else 0.0
            bus.write_goal(open_pct_to_dxl(pct))

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
