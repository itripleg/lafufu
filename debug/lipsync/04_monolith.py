"""04_monolith.py — faithful port of the legacy monolith lipsync.

Sourced from C:\\dev\\lafufu-jb\\dynamixel.py:1838-1955. This is the
known-working reference. If THIS doesn't sync on the Pi, the desync is
mechanical/audio (servo, ALSA, aplay). If it DOES sync but the other
modes don't, the export tells you which layers to port to production.

Key differences from Envelope mode:
  - per-WAV percentile-normalised RMS (content-adaptive: a quiet WAV
    still opens the mouth fully)
  - explicit deadzone + gamma (perceptual loudness curve)
  - file-mode aplay (`aplay file.wav`) — NOT stdin streaming
  - wall-clock motor pacing against the moment aplay was spawned

Run on the Pi:
    uv run python debug/lipsync/04_monolith.py /tmp/lipsync/test.wav

Algorithm lives in algorithms.py and is shared with server.py.
"""

from __future__ import annotations

import argparse

from algorithms import MonolithCfg, run_monolith


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="path to 16-bit PCM mono WAV")
    p.add_argument("--fps", type=int, default=MonolithCfg.fps)
    p.add_argument("--deadzone", type=float, default=MonolithCfg.deadzone)
    p.add_argument("--gamma", type=float, default=MonolithCfg.gamma)
    p.add_argument("--p-low", type=float, default=MonolithCfg.p_low)
    p.add_argument("--p-high", type=float, default=MonolithCfg.p_high)
    p.add_argument("--attack-ms", type=int, default=MonolithCfg.attack_ms)
    p.add_argument("--release-ms", type=int, default=MonolithCfg.release_ms)
    p.add_argument("--alsa-device", default=MonolithCfg.alsa_device)
    a = p.parse_args()
    run_monolith(
        MonolithCfg(
            fps=a.fps,
            deadzone=a.deadzone,
            gamma=a.gamma,
            p_low=a.p_low,
            p_high=a.p_high,
            attack_ms=a.attack_ms,
            release_ms=a.release_ms,
            alsa_device=a.alsa_device,
        ),
        a.wav,
    )


if __name__ == "__main__":
    main()
