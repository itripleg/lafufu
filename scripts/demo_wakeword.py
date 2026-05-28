"""Dev-machine wake-word demo.

Opens the default microphone, runs openwakeword against the audio, prints a
live confidence bar, and shouts "WAKE!" when the model crosses its threshold.
Lets you sanity-check the openwakeword integration on a laptop before flashing
the Pi.

First-run setup (openwakeword is now in the agent's required deps, so a plain
sync is sufficient — no extras flag needed):
    uv sync --all-packages

Run it:
    uv run python scripts/demo_wakeword.py

Pick a different model with --model (any name openwakeword ships in its default
set — "hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "weather", "timer"),
and tighten or loosen sensitivity with --threshold.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pyaudio
from openwakeword import utils
from openwakeword.model import Model

# openwakeword expects 16 kHz mono int16 PCM, fed in 80 ms windows (1280 samples).
SAMPLE_RATE = 16_000
CHUNK = 1280


def main() -> int:
    ap = argparse.ArgumentParser(description="Live openwakeword detector.")
    ap.add_argument("--model", default="hey_jarvis", help="default-set model name")
    ap.add_argument("--threshold", type=float, default=0.5, help="fire when score >= this")
    ap.add_argument(
        "--cooldown",
        type=float,
        default=0.8,
        help="seconds to mute after a hit so one utterance doesn't fire twice",
    )
    args = ap.parse_args()

    print("Caching openwakeword default models (one-time on first run)...")
    try:
        utils.download_models()
    except Exception as e:
        print(f"  warning: download_models() failed: {e}")

    print(f"Loading {args.model!r} via onnxruntime...")
    model = Model(wakeword_models=[args.model], inference_framework="onnx")

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    say = args.model.replace("_", " ")
    print(f'\nListening for "{say}" — Ctrl+C to stop.  (threshold={args.threshold})\n')

    try:
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)
            scores = model.predict(audio)
            score = float(scores.get(args.model, 0.0))

            bar = "#" * int(score * 40)
            sys.stdout.write(f"\r{score:0.3f} |{bar:<40}|")
            sys.stdout.flush()

            if score >= args.threshold:
                print(f'\n  *** WAKE! "{say}" (score={score:0.3f}) ***\n')
                # Clear the model's internal buffer so the same utterance
                # doesn't immediately re-fire on the next chunk.
                model.reset()
                time.sleep(args.cooldown)
    except KeyboardInterrupt:
        print("\nbye.")
        return 0
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
