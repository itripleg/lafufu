"""03_gate.py — binary mouth open/close on an RMS threshold.

The simplest possible talking puppet. If the GATE looks tightly
sync'd but DIRECT / ENVELOPE don't, the desync is in amplitude
tracking, not timing or servo response.

Run on the Pi:
    uv run python debug/lipsync/03_gate.py /tmp/test.wav
    uv run python debug/lipsync/03_gate.py /tmp/test.wav --gate-threshold 0.03

Algorithm lives in algorithms.py and is shared with server.py.
"""

from __future__ import annotations

import argparse

from algorithms import GateCfg, run_gate


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="path to 16-bit PCM mono WAV")
    p.add_argument("--chunk-ms", type=int, default=GateCfg.chunk_ms)
    p.add_argument("--alsa-buffer-ms", type=int, default=GateCfg.alsa_buffer_ms)
    p.add_argument("--alsa-period-ms", type=int, default=GateCfg.alsa_period_ms)
    p.add_argument("--offset-ms", type=int, default=GateCfg.offset_ms)
    p.add_argument("--gate-threshold", type=float, default=GateCfg.gate_threshold)
    p.add_argument("--open-pct", type=float, default=GateCfg.open_pct)
    p.add_argument("--alsa-device", default=GateCfg.alsa_device)
    a = p.parse_args()
    run_gate(
        GateCfg(
            chunk_ms=a.chunk_ms,
            alsa_buffer_ms=a.alsa_buffer_ms,
            alsa_period_ms=a.alsa_period_ms,
            offset_ms=a.offset_ms,
            gate_threshold=a.gate_threshold,
            open_pct=a.open_pct,
            alsa_device=a.alsa_device,
        ),
        a.wav,
    )


if __name__ == "__main__":
    main()
