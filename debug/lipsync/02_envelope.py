"""02_envelope.py — RMS -> attack/release envelope -> jaw.

Tune `--attack-ms` (rise) and `--release-ms` (fall) until the mouth
feels right. These numbers go back into `animator.lipsync.attack_ms` /
`animator.lipsync.release_ms` on the running Lafufu.

Run on the Pi:
    uv run python debug/lipsync/02_envelope.py /tmp/test.wav
    uv run python debug/lipsync/02_envelope.py /tmp/test.wav --attack-ms 30 --release-ms 120

Algorithm lives in algorithms.py and is shared with server.py.
"""

from __future__ import annotations

import argparse

from algorithms import EnvelopeCfg, run_envelope


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="path to 16-bit PCM mono WAV")
    p.add_argument("--chunk-ms", type=int, default=EnvelopeCfg.chunk_ms)
    p.add_argument("--alsa-buffer-ms", type=int, default=EnvelopeCfg.alsa_buffer_ms)
    p.add_argument("--alsa-period-ms", type=int, default=EnvelopeCfg.alsa_period_ms)
    p.add_argument("--offset-ms", type=int, default=EnvelopeCfg.offset_ms)
    p.add_argument("--rms-min", type=float, default=EnvelopeCfg.rms_min)
    p.add_argument("--rms-max", type=float, default=EnvelopeCfg.rms_max)
    p.add_argument("--attack-ms", type=int, default=EnvelopeCfg.attack_ms)
    p.add_argument("--release-ms", type=int, default=EnvelopeCfg.release_ms)
    p.add_argument("--alsa-device", default=EnvelopeCfg.alsa_device)
    a = p.parse_args()
    run_envelope(
        EnvelopeCfg(
            chunk_ms=a.chunk_ms,
            alsa_buffer_ms=a.alsa_buffer_ms,
            alsa_period_ms=a.alsa_period_ms,
            offset_ms=a.offset_ms,
            rms_min=a.rms_min,
            rms_max=a.rms_max,
            attack_ms=a.attack_ms,
            release_ms=a.release_ms,
            alsa_device=a.alsa_device,
        ),
        a.wav,
    )


if __name__ == "__main__":
    main()
