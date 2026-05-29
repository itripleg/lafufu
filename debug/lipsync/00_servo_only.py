"""00_servo_only.py — exercise the jaw with NO audio.

Sweeps the jaw open <-> closed at a fixed frequency. Use this FIRST: if
the servo can't track the sinusoid cleanly at a given frequency, no
amplitude-following lipsync algorithm will look better than that.

Run on the Pi:
    uv run python debug/lipsync/00_servo_only.py
    uv run python debug/lipsync/00_servo_only.py --freq-hz 6 --duration-s 8

All knobs are CLI flags; defaults match the production agent's chunk
cadence. Algorithm lives in algorithms.py and is shared with server.py.
"""

from __future__ import annotations

import argparse

from algorithms import ServoOnlyCfg, run_servo_only


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--freq-hz", type=float, default=ServoOnlyCfg.freq_hz)
    p.add_argument("--duration-s", type=float, default=ServoOnlyCfg.duration_s)
    p.add_argument("--tick-hz", type=int, default=ServoOnlyCfg.tick_hz)
    a = p.parse_args()
    run_servo_only(ServoOnlyCfg(freq_hz=a.freq_hz, duration_s=a.duration_s, tick_hz=a.tick_hz))


if __name__ == "__main__":
    main()
