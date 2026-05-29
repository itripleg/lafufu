# Legacy monolith lipsync — side-by-side comparison

The legacy monolith at `C:\dev\lafufu-jb\dynamixel.py` (lines 1838–1955)
has a known-working lipsync. The modular rewrite reimplemented it differently,
and the result is the desync you're chasing. This document captures the
differences ahead of the bench session.

The testbed's **04 Monolith** mode is a faithful port — running it on the Pi
tells you whether the algorithm is the bug (port the missing pieces) or
whether something else has changed in the audio / servo stack.

---

## The dominant differences

### 1. RMS normalisation — fixed vs content-adaptive

| | Legacy | Production |
|---|---|---|
| How | `floor = p10(rms)`, `ceil = p95(rms)` computed in a PRE-PASS over the whole WAV | `rms_min = 0.005`, `rms_max = 0.30` fixed constants |
| Per chunk | `x = (rms - floor) / (ceil - floor)` | `x = (rms - rms_min) / (rms_max - rms_min)` |
| Effect | Mouth opens fully on every utterance, regardless of how loud the speech is recorded | A quiet WAV (e.g. softer Piper voice) never opens the mouth past ~50%; sounds + looks mealy |

**Verdict:** likely the **biggest single visual difference**. Fixed bounds
mean the mouth's open percentage is at the mercy of recording loudness;
percentiles make it utterance-relative.

### 2. Deadzone

Legacy: `if x <= 0.05: target = 0  else: target = (x - 0.05) / 0.95`.

Production: no deadzone. Every non-zero RMS produces some mouth opening.

**Effect:** the legacy mouth stays cleanly shut during room tone / breath
between syllables. Without it, the mouth micro-flutters in pauses.

### 3. Gamma curve

Legacy: `target = target ** 0.70` after normalisation + deadzone.

Production: linear.

**Effect:** with gamma < 1, a moderate-volume sound produces a more-open
mouth than linear mapping would. Reads as "this sound matters." Without it,
quieter consonants undershoot — mouth looks slack on talky syllables.

### 4. aplay invocation mode

Legacy: `subprocess.Popen(["aplay", "file.wav"])`. The whole WAV is on
disk; aplay reads the header, configures itself, and plays at full speed
without external pacing.

Production: `subprocess.Popen(["aplay", "-q", "-D", device, "-f", "S16_LE",
"-c", "1", "-r", str(rate), "--buffer-size=…", "--period-size=…"],
stdin=PIPE)` — streamed one chunk at a time over stdin as Piper emits them.

**Effect:** in stdin mode, ALSA waits for one PERIOD before producing
audible sound — production sets period_size = 40 ms, so there's a ~40 ms
latency floor before any chunk is heard. Legacy avoids that entirely by
handing aplay the whole file. (See also: the agent's `prepend 80 ms silence`
fix in commit `754be8d`, which is a workaround for the same family of
issue, not a root-cause fix.)

### 5. Motor pacing

Legacy:

```python
t0 = time.time()
for i, data in enumerate(chunks):
    target_t = t0 + (i + 1) * dt
    now = time.time()
    if target_t > now:
        time.sleep(target_t - now)
    # ... compute envelope, write jaw
```

The motor loop is pacing itself against the **wall-clock time aplay was
spawned**. If a chunk computation takes too long, the next sleep is
shorter — the loop catches up.

Production: each iteration does its work, then sleeps `dt`. Drift
accumulates. Worse, the per-iteration sleep doesn't account for chunk-write
time over stdin, so the consumer's pacing can fall out of sync with the
producer's emit rate when Piper bursts.

**Effect:** even tiny per-iteration drift (a few ms each) accumulates over
several seconds of speech and shows up as a visible offset.

### 6. Chunk rate (FPS)

Legacy: FPS = 20 → chunk_ms = 50 ms.

Production: chunk_ms = 40 ms (chosen to match Piper's emit cadence).

**Effect:** small. 50 ms gives slightly chunkier motion but lets the servo
settle between updates; 40 ms is smoother but pushes the servo harder.
Probably not load-bearing on its own.

### 7. Envelope time constants

Legacy: attack = 30 ms / release = 80 ms.

Production: attack = 20 ms / release = 80 ms (`animator.lipsync.attack_ms`,
`animator.lipsync.release_ms`).

**Effect:** very close. Legacy is slightly slower attack — barely perceptible.
**These two are already live-tunable** via the admin UI without any code
changes; tuning them is the quickest first experiment.

---

## What to port back (priority order)

If the testbed's **04 Monolith** mode looks tight on the Pi and the
other modes don't:

1. **Percentile normalisation** (`p10/p95` over the utterance) — the
   biggest mouth-amplitude fix. Code change in
   `packages/agent/src/lafufu_agent/pipeline.py` (the lipsync RMS path).
   Trade-off: requires holding the whole utterance's RMS values before
   playback can start, or running a sliding-window approximation.
2. **Deadzone + gamma** — small, isolated, no architectural change.
3. **Tune attack/release live** via `animator.lipsync.attack_ms` and
   `release_ms` to match the legacy values (30 / 80). Free; do this first.
4. **File-mode aplay** — biggest architectural change. May only be worth
   it if the algorithm changes alone don't close the gap. Would require
   buffering the whole utterance to disk before playback in
   `packages/agent/src/lafufu_agent/__main__.py` (`_AplayPlayer`), which
   fights the streaming TTS approach.
5. **Wall-clock motor pacing** — only relevant if (4) is also done.

---

## What's the same — don't touch

- DXL jaw IDs / open / close positions (`JAW_OPEN_POS=1594`,
  `JAW_CLOSE_POS=1811` in both — same calibration).
- Servo protocol (`dynamixel_sdk` protocol 2.0, address 116 for goal
  position).
- Asymmetric attack/release envelope shape (one-pole exponential, both).

---

## Source pointers

- Legacy lipsync function: `C:\dev\lafufu-jb\dynamixel.py` lines
  1838–1955 (`play_wav_with_lipsync`).
- Legacy constants: same file, lines 87–96 (`LIPSYNC_*` and
  `MOUTH_*`).
- Legacy helpers: `audio_rms` (line 868), `rms_from_bytes` (876),
  `percentile_sorted` (904).
- This testbed's port: `debug/lipsync/algorithms.py` (`run_monolith`
  + `MonolithCfg`).
- Production lipsync RMS path: `packages/agent/src/lafufu_agent/pipeline.py`.
- Production audio output: `packages/agent/src/lafufu_agent/__main__.py`
  (`_AplayPlayer`).
