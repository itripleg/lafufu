"""01_direct.py — direct RMS -> jaw, no envelope, no smoothing, no offset.

Simplest possible algorithm. For each audio chunk:
  1. compute the chunk's RMS (loudness)
  2. linearly map [RMS_MIN .. RMS_MAX] to [closed .. open]
  3. write that position to the jaw INSTANTLY

If THIS script sync'd cleanly but the agent does not, the desync is in our
agent pipeline (envelope, motion smoother, asyncio scheduling, NATS).
If even this is off, the desync is in audio buffering (ALSA buffer/period)
or the servo response itself — try `00_servo_only.py` to check the servo.

Run on the Pi:
    uv run python debug/lipsync/01_direct.py /path/to/sample.wav

Generate a sample WAV:
    echo "this is a test of the mouth sync" | \\
        piper --model /srv/lafufu/models/lafufu_voice.onnx \\
              --output-file /tmp/test.wav
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
CHUNK_MS = 40  # how often we update jaw + push to aplay (matches Piper)
ALSA_BUFFER_MS = 1000  # aplay --buffer-size in ms
ALSA_PERIOD_MS = 40  # aplay --period-size — first-audible-sample latency
OFFSET_MS = 0  # +N: jaw uses RMS from N ms AHEAD of audio; -N: behind
RMS_MIN = 0.005  # below this RMS -> mouth closed
RMS_MAX = 0.30  # at/above this RMS -> mouth fully open
ALSA_DEVICE = "default"  # try "plughw:1,0" etc if needed
# --------------------------------


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: 01_direct.py <audio.wav>")
    wav_path = sys.argv[1]

    reader, info = open_wav(wav_path)
    chunk_frames = max(1, info.sample_rate * CHUNK_MS // 1000)
    buffer_frames = info.sample_rate * ALSA_BUFFER_MS // 1000
    period_frames = info.sample_rate * ALSA_PERIOD_MS // 1000

    # Pre-read all chunks so we can look AHEAD or BEHIND the playback cursor
    # cheaply (for OFFSET_MS). Sample WAVs for testing are short.
    chunks = list(iter_chunks(reader, chunk_frames))
    reader.close()

    bus = JawBus.open()
    proc = aplay_popen(info.sample_rate, buffer_frames, period_frames, ALSA_DEVICE)

    try:
        dt = CHUNK_MS / 1000.0
        offset_chunks = OFFSET_MS // CHUNK_MS  # signed
        next_tick = time.monotonic()
        for i, chunk in enumerate(chunks):
            j = i + offset_chunks
            rms = chunk_rms(chunks[j]) if 0 <= j < len(chunks) else 0.0
            pct = (rms - RMS_MIN) / max(1e-6, RMS_MAX - RMS_MIN)
            pct = max(0.0, min(1.0, pct))
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

        # Close mouth and let aplay drain.
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
