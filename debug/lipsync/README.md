# Lipsync debug testbed

A directory of **standalone Python scripts** for tuning mouth/audio sync on the
bench. None of these import `lafufu_agent`, `lafufu_animator`, NATS,
asyncio, or the motion smoother — they talk **directly** to the U2D2 over
`dynamixel_sdk` and to `aplay` over a subprocess pipe. The whole point is
to bypass our own layers so you can isolate which one is the source of
desync.

Once a script's settings look right, port the numbers back to the production
settings (`animator.lipsync.attack_ms`, `animator.lipsync.release_ms`,
`animator.lipsync.offset_ms`, etc.) and see if Lafufu lands in the same place.

---

## Run on the Pi only

These scripts need the U2D2 + speaker. Run them on the Pi (`/srv/lafufu`),
not your dev machine.

**Stop the agent + animator first** so they don't fight you for the bus or
the audio device:

```bash
sudo systemctl stop lafufu-agent lafufu-animator
```

(Bring them back when done with `systemctl start`.)

## Safety

- The jaw clamps in `common.py` are calibrated for **the existing Lafufu**
  (open=1594, close=1811). If you're working with a different head or
  recalibrated servo, edit `JAW_OPEN_POS` / `JAW_CLOSE_POS` first.
- `JawBus` installs `SIGINT` / `SIGTERM` handlers and an `atexit` hook that
  disable torque on exit — Ctrl-C will not leave torque on. If you do
  somehow kill the script with `kill -9`, manually disable torque
  (`uv run python -c "from dynamixel_sdk import ...; ..."` or just power-cycle).
- If a servo gets stuck or the head jerks, kill the script — the script
  pushes blindly. There is no motion smoother in the loop.

## Get test audio

Use the production Piper voice for representative spectra:

```bash
echo "this is a test of the mouth sync, it should look natural" | \
    /srv/lafufu/.venv/bin/piper \
        --model /srv/lafufu/models/lafufu_voice.onnx \
        --output-file /tmp/test.wav
```

The scripts expect **16-bit PCM mono**, which is what Piper outputs.

## The scripts

| # | Script               | What it isolates                                      |
|---|----------------------|-------------------------------------------------------|
| 00 | `00_servo_only.py`  | Servo response only — sweep open<->close, no audio.   |
| 01 | `01_direct.py`      | RMS -> jaw, instant. No envelope. No smoothing.       |
| 02 | `02_envelope.py`    | RMS -> attack/release envelope -> jaw.                |
| 03 | `03_gate.py`        | RMS threshold -> binary open/close. No amplitude.     |

Each script has a `--- CONFIG ---` block at the top with every knob.
Edit, save, re-run. No CLI flags beyond the audio file path.

## Suggested A/B procedure

1. **`00_servo_only.py`** first. Sweep at `FREQ_HZ = 2`, then 4, 6, 8. Find
   the frequency at which the servo starts to lag, overshoot, or quantize.
   That's the realistic ceiling for any amplitude-following mode.
2. **`03_gate.py`** next. Binary open/close should look tight against the
   audio's syllables. If THIS looks off, the desync is in the audio
   buffering or the servo's response — not in the algorithm. Tune
   `OFFSET_MS` first (try +40, +80, +120, -40 to find rough alignment),
   then `GATE_THRESHOLD` by ear/eye.
3. **`01_direct.py`**. Same `OFFSET_MS` as the gate settled on. Tune
   `RMS_MIN` / `RMS_MAX` so the mouth opens roughly with the loudness of
   the speech.
4. **`02_envelope.py`**. Same `OFFSET_MS` + `RMS_MIN/MAX` as direct.
   Now layer in `ATTACK_MS` and `RELEASE_MS`. Bigger release = mouth
   lingers open (looks more like a real mouth). Smaller attack = mouth
   responds to transients faster.

## Knobs and what they mean

- `CHUNK_MS` — how often the script updates the jaw + pushes a chunk to
  `aplay`. 40 ms matches Piper's chunk cadence. Smaller = more servo
  traffic; larger = chunkier mouth motion.
- `ALSA_BUFFER_MS` / `ALSA_PERIOD_MS` — `aplay --buffer-size` and
  `--period-size` in ms. ALSA starts playback once one PERIOD is
  buffered, so PERIOD sets the **first-audible-sample latency**.
  Production currently uses 1000 ms buffer / 40 ms period.
- `OFFSET_MS` — positive: jaw is driven by RMS from N ms **ahead** of
  the audio cursor (mouth leads); negative: mouth lags. Tune this to
  compensate for whatever consistent lead/lag you see.
- `RMS_MIN` / `RMS_MAX` — the loudness window mapped to closed/open.
  Voices vary; tune by ear.
- `ATTACK_MS` / `RELEASE_MS` (envelope mode) — exponential time
  constants. Roughly: the envelope reaches ~63 % of the gap to its
  target after one time constant.
- `GATE_THRESHOLD` / `OPEN_PCT` (gate mode) — the RMS above which the
  mouth pops open, and how far open it goes.

## When you find numbers that work

Port them into the running Lafufu via the admin UI (`/admin`, then the
**Animator** panel) — these are the settings keys:

- `animator.lipsync.attack_ms` (1–200)
- `animator.lipsync.release_ms` (5–400)
- `animator.lipsync.offset_ms` (0–500)

Then run a normal voice cycle on Lafufu and compare. If the agent looks
*worse* than the matching script with the same numbers, the additional
desync is being added by one of our layers (the asyncio scheduling, the
motion smoother, NATS chunk fan-out timing). That's the lipsync follow-up
work spec'd as **T13** in the prod-hardening plan.
