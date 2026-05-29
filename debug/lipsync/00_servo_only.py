"""00_servo_only.py — exercise the jaw with NO audio.

Sweeps the jaw open <-> closed at a fixed frequency. No audio at all.

USE THIS FIRST. If the servo can't track the sinusoid cleanly at the
chosen frequency (with no algorithm/audio in the way), every other
script will look wrong too — the bottleneck is the servo response
(PROFILE_VELOCITY/ACCELERATION, the U2D2 link, mechanical drag), not
the lipsync algorithm.

Run on the Pi:
    uv run python debug/lipsync/00_servo_only.py

Knobs at the top — edit and re-run.
"""

from __future__ import annotations

import math
import time

from common import JawBus, open_pct_to_dxl

# --- CONFIG (edit and re-run) ---
FREQ_HZ = 4.0  # full open->close->open cycles per second
DURATION_S = 5.0  # total run time
TICK_HZ = 30  # how often we send a fresh goal position
# Try FREQ_HZ from 1 to 10 — at what point does the jaw start to lag /
# overshoot / quantize? That number is the realistic ceiling for any
# amplitude-following lipsync, regardless of algorithm.
# --------------------------------


def main() -> None:
    bus = JawBus.open()
    try:
        dt = 1.0 / TICK_HZ
        t0 = time.monotonic()
        next_tick = t0
        while True:
            t = time.monotonic() - t0
            if t >= DURATION_S:
                break
            # 0..1 sine wave
            pct = 0.5 * (1.0 + math.sin(2 * math.pi * FREQ_HZ * t))
            bus.write_goal(open_pct_to_dxl(pct))
            next_tick += dt
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
        # Park at closed and hold briefly so the operator sees a clean stop.
        bus.write_goal(open_pct_to_dxl(0.0))
        time.sleep(0.5)
    finally:
        bus.close()


if __name__ == "__main__":
    main()
