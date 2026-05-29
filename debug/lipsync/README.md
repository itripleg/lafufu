# Lipsync debug testbed

A small toolkit for tuning mouth/audio sync on the bench. Nothing in here
imports `lafufu_agent`, `lafufu_animator`, NATS, asyncio, or the motion
smoother — it all talks **directly** to the U2D2 over `dynamixel_sdk` and
to `aplay` over a subprocess pipe. The point is to bypass our own layers
so you can isolate which one is the source of desync.

When the settings here look good, port the numbers into the running Lafufu
(`animator.lipsync.attack_ms` / `release_ms` / `offset_ms`) and see if you
get the same result. If the agent looks **worse** than the matching mode
here with the same numbers, the extra desync is being added by one of our
layers — that's the lipsync follow-up spec'd as **T13** in the
prod-hardening plan.

---

## Run on the Pi only

These scripts need the U2D2 and a speaker. Run them on the Pi
(`/srv/lafufu`), not your dev machine. **Stop the agent + animator
first** so they don't fight you for the bus or the audio device:

```bash
sudo systemctl stop lafufu-agent lafufu-animator
```

Bring them back when done: `sudo systemctl start lafufu-agent lafufu-animator`.

## Safety

- Jaw clamps in `common.py` are calibrated for **the existing Lafufu**
  (`JAW_OPEN_POS=1594`, `JAW_CLOSE_POS=1811`). For a different head or
  recalibrated servo, edit those two constants first.
- `JawBus` installs `SIGINT` / `SIGTERM` handlers and an `atexit` hook
  that disable torque on exit — Ctrl-C or stopping the server will not
  leave torque on. If something gets `kill -9`'d, power-cycle the U2D2.
- The scripts push goal positions **without any motion smoother**. If the
  servo jerks or stalls, kill the run.

---

## Option A: web UI (easier)

The recommended way to drive the testbed:

```bash
cd /srv/lafufu
sudo systemctl stop lafufu-agent lafufu-animator
uv run python debug/lipsync/server.py
# browse to http://<pi-ip>:8090/
```

The page has:

- A **Generate audio** form (runs Piper with the production voice) and a
  picker for previously generated WAVs.
- One tab per mode (00 Servo / 01 Direct / 02 Envelope / 03 Gate). Each
  tab shows the algorithm's knobs as number inputs and a Run button.
- A persistent **Stop** button + a live log panel.

The server runs one thing at a time and uses a `threading.Event` for clean
cancellation — pressing Stop ends the current run, closes the mouth, and
disables torque. Same safety hooks as the CLI scripts.

## Option B: CLI (fast iteration via shell)

The 4 CLI scripts call the same algorithm functions as the web UI; the
defaults match the production agent's cadence. Override any knob with
`--flag`:

```bash
# Sweep the jaw with no audio, at 6 Hz.
uv run python debug/lipsync/00_servo_only.py --freq-hz 6 --duration-s 8

# Direct RMS mode on a WAV; bump the offset.
uv run python debug/lipsync/01_direct.py /tmp/lipsync/test.wav --offset-ms 80

# Envelope, tighter attack + longer release.
uv run python debug/lipsync/02_envelope.py /tmp/lipsync/test.wav \
    --attack-ms 15 --release-ms 120 --offset-ms 80

# Binary gate (simplest sync).
uv run python debug/lipsync/03_gate.py /tmp/lipsync/test.wav \
    --gate-threshold 0.025 --offset-ms 80
```

Run any script with `--help` to see every flag and its default.

### Generating a test WAV from the CLI

```bash
mkdir -p /tmp/lipsync
echo "this is a test of the mouth sync" | \
    /srv/lafufu/.venv/bin/piper \
        --model /srv/lafufu/models/lafufu_voice.onnx \
        --output_file /tmp/lipsync/test.wav
```

The scripts expect **16-bit PCM mono**, which is what Piper outputs.

---

## The modes

| # | Mode | What it isolates |
|---|------|------------------|
| 00 | Servo only | Servo response, with NO audio at all. |
| 01 | Direct | RMS → jaw, instant. No envelope. The baseline. |
| 02 | Envelope | RMS → attack/release envelope → jaw. Closest to production. |
| 03 | Gate | RMS threshold → binary open/close. No amplitude tracking. |
| 04 | Monolith (legacy) | Faithful port of the **known-working** lipsync from `C:\dev\lafufu-jb\dynamixel.py`. The gold reference. |

If the testbed's 04 Monolith mode looks tight on the Pi but Envelope (the
production analog) doesn't, port the missing pieces (percentile RMS
normalisation, deadzone, gamma) into the agent — see
[`legacy-comparison.md`](./legacy-comparison.md) for the side-by-side.

## Suggested A/B procedure

1. **Servo only** first. Try `freq_hz` = 2, 4, 6, 8. The frequency at
   which the servo starts to lag / overshoot / quantize is the ceiling
   for any amplitude-following mode — nothing downstream can sync better
   than that.
2. **Gate** next. Binary open/close should look tight against the audio's
   syllables. If THIS looks off, the desync is in audio buffering or the
   servo's response — not in the algorithm. Tune `offset_ms` first (try
   +40, +80, +120, -40 to find rough alignment), then `gate_threshold`
   by ear/eye.
3. **Direct**. Keep the offset you settled on. Tune `rms_min` / `rms_max`
   so the mouth opens roughly with the loudness of the speech.
4. **Envelope**. Same offset + RMS bounds as Direct. Now layer in
   `attack_ms` and `release_ms`. Bigger release = mouth lingers open
   (looks more natural). Smaller attack = snappier on transients.

## Knobs

- `chunk_ms` — how often the runner updates the jaw + pushes a chunk to
  aplay. 40 ms matches Piper's chunk cadence.
- `alsa_buffer_ms` / `alsa_period_ms` — `aplay --buffer-size` and
  `--period-size`. ALSA starts playback once one PERIOD is buffered, so
  **PERIOD sets first-audible-sample latency**. Production uses
  1000 ms / 40 ms.
- `offset_ms` — positive: jaw is driven by RMS from N ms **ahead** of
  the audio cursor (mouth leads audio); negative: mouth lags. Tune to
  compensate for the consistent lead/lag you see.
- `rms_min` / `rms_max` — loudness window mapped to closed/open.
- `attack_ms` / `release_ms` (envelope) — exponential time constants.
  Envelope moves ~63 % toward the target after one time constant.
- `gate_threshold` / `open_pct` (gate) — RMS above which the mouth pops
  open, and how far.

## Apply the numbers to the live Lafufu

Once a mode looks good, set the matching settings in the admin UI
(`/admin`, **Animator** section):

- `animator.lipsync.attack_ms` (1–200)
- `animator.lipsync.release_ms` (5–400)
- `animator.lipsync.offset_ms` (0–500)

Then trigger a normal voice cycle on Lafufu and compare. If it looks
**worse** than the matching testbed mode with identical numbers, that
extra desync is one of our layers (motion smoother, asyncio scheduling,
NATS fan-out timing) — see T13 in the prod-hardening plan.

---

## How it's wired

- `common.py` — bus / aplay / WAV / RMS helpers and the `JawBus` class
  (with the Ctrl-C-safe torque-off hook).
- `algorithms.py` — the **single source of truth** for the four
  algorithms. Each takes a small dataclass config + an optional
  `threading.Event` for cancellation, and blocks until done.
- `00_servo_only.py` / `01_direct.py` / `02_envelope.py` / `03_gate.py` —
  thin CLI wrappers (argparse + call the algorithm).
- `server.py` — single-file FastAPI app + inline HTML. Calls the
  algorithms directly in a daemon thread (no subprocess); polls
  `/api/status` so the page reflects state. Default port 8090.

So if you find a bug in an algorithm, fixing it in `algorithms.py`
fixes both the CLI and the web UI.
