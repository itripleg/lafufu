"""01_direct.py — direct RMS -> jaw, no envelope, no smoothing.

For each audio chunk: compute RMS, linearly map to jaw open %, write
instantly. Simplest possible algorithm — a baseline for comparing the
other modes against.

Run on the Pi:
    uv run python debug/lipsync/01_direct.py /tmp/test.wav
    uv run python debug/lipsync/01_direct.py /tmp/test.wav --offset-ms 80

All knobs are CLI flags; defaults match the production agent. Algorithm
lives in algorithms.py and is shared with server.py.
"""

from __future__ import annotations

import argparse

from algorithms import DirectCfg, run_direct


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="path to 16-bit PCM mono WAV")
    p.add_argument("--chunk-ms", type=int, default=DirectCfg.chunk_ms)
    p.add_argument("--alsa-buffer-ms", type=int, default=DirectCfg.alsa_buffer_ms)
    p.add_argument("--alsa-period-ms", type=int, default=DirectCfg.alsa_period_ms)
    p.add_argument("--offset-ms", type=int, default=DirectCfg.offset_ms)
    p.add_argument("--rms-min", type=float, default=DirectCfg.rms_min)
    p.add_argument("--rms-max", type=float, default=DirectCfg.rms_max)
    p.add_argument("--alsa-device", default=DirectCfg.alsa_device)
    a = p.parse_args()
    run_direct(
        DirectCfg(
            chunk_ms=a.chunk_ms,
            alsa_buffer_ms=a.alsa_buffer_ms,
            alsa_period_ms=a.alsa_period_ms,
            offset_ms=a.offset_ms,
            rms_min=a.rms_min,
            rms_max=a.rms_max,
            alsa_device=a.alsa_device,
        ),
        a.wav,
    )


if __name__ == "__main__":
    main()
