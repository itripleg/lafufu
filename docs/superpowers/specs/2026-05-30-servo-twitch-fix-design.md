# Servo twitch / "struggle after a while" — root cause + fix design

**Date:** 2026-05-30
**Status:** Mechanism B (event-loop blocking) **IMPLEMENTED on `main`** — servo writes moved to a dedicated writer thread + `dt` clamp; the regression test passes (xfail marker removed). Pending: live hardware verification on the real Lafufu, and Mechanism A (the testbed bus-guard + combined debug GUI).
**Author:** diagnosis session (Claude)

## Symptom

On the Pi, the servos are **twitchy and "struggle after a while."** Onset
correlated with a bench session: deploy → lipsync tuning via the debug
testbed (operator found "04 Monolith / legacy" mode best) → servos began
struggling. Operator also noticed "two web apps running, possibly competing
for resources."

## Investigation summary

### Eliminated (with evidence)

- **"Recent commits broke the animator."** ❌ All 10 commits on
  `debug/lipsync-testbed` (the branch the Pi runs) touch only `debug/` and
  `web/`. `git log main..debug/lipsync-testbed -- packages/animator` is
  **empty**. The production servo code (`motion.py`, `service.py`,
  `dxl_bus.py`) is identical to `main`.
- **"The testbed corrupted the servos."** ❌ The testbed's `JawBus`
  (`debug/lipsync/common.py`) writes only **RAM** registers
  (`TORQUE_ENABLE`=64, `GOAL_POSITION`=116) on the **jaw servo only** (ID 4),
  and disables torque on exit (atexit + SIGINT/SIGTERM). No EEPROM writes →
  nothing persists across the testbed exiting; it cannot directly command
  head/eye/brow.

### Confirmed root cause — Mechanism B (event-loop blocking)

`AnimatorService._stepper_loop` (`packages/animator/src/lafufu_animator/service.py`)
runs at `stepper_hz` (default 30) and each tick calls `_move_to_pose()`,
which issues **5 synchronous Dynamixel round-trips** (`bus.write` →
`DxlBus.write` → `write4ByteTxRx`, a write **plus a blocking wait for each
servo's status packet**) **directly on the asyncio event loop** — no
executor, unlike the agent service which correctly offloads its blocking I/O.

The smoother uses **wall-clock `dt`** (`now - last`). When the loop is
starved — by these very writes, by serial-bus contention, or by CPU pressure
(a Chromium renderer was observed at 76% of a core from an extra browser
tab) — `dt` spikes and `smooth_damp` (`motion.py`) converts the fat `dt` into
a large position step → a visible lurch. Repeated, this is "twitchy," and it
worsens as load accumulates ("struggle after a while").

**Confirmed deterministically** by `packages/animator/tests/test_stepper_nonblocking.py`
(already in the tree): it runs the real stepper loop against a bus whose
writes block 30 ms each and asserts the event loop stays responsive to a 5 ms
heartbeat. On current code it FAILS — the loop is stalled ~168 ms per tick
(5 × ~30 ms). No hardware required.

### Suspected — Mechanism A (acute trigger, two writers on one bus)

The debug testbed (`debug/lipsync/server.py`, port **8090**, separate web UI)
drives the servo bus **directly and unsmoothed**. Its own docs warn to
`sudo systemctl stop lafufu-agent lafufu-animator` first. If it ran while
`lafufu-animator` was still up (or systemd restarted the animator mid-session),
**two processes wrote the same serial port simultaneously** → interleaved
Dynamixel packets → corrupted writes / retries → twitch on every servo, plus
`_has_u2d2` flapping to degraded. This explains the "two web apps" observation
(control on :8080 + testbed on :8090) and multi-servo twitch. Confirm on
hardware (procedure below).

## Acceptance criteria

1. `packages/animator/tests/test_stepper_nonblocking.py` — currently marked
   `@pytest.mark.xfail(strict=True)` because the bug is live (it asserts max
   event-loop gap < 100 ms while the bus is written; current code stalls
   ~168 ms). When the fix lands, the test will *pass*, and `strict=True` turns
   that xpass into a CI failure — **remove the xfail marker** so it becomes a
   normal passing regression test. The **assertion** is the contract; if the
   fix changes `_stepper_loop` from a coroutine to a thread, adapt only how the
   test *starts* the stepper, not the assertion.
2. All existing `packages/animator/tests/*` still pass (`uv run pytest` at repo root).
3. The debug testbed refuses to open the bus while `lafufu-animator` is active,
   and its web UI shows lafufu service/process state with start/stop controls.

## Fix B — move servo I/O off the event loop (durable prod fix)

**Primary design: a dedicated writer thread that exclusively owns the bus.**

- A single OS thread runs the step+write loop: snapshot the latest
  `_target_pose`, run `PoseSmoother.step`, write the 5 servos, wait
  `stepper_dt` (via a `threading.Event.wait(timeout)` so shutdown is prompt),
  repeat. It measures `dt` from its **own** clock, decoupling servo timing
  from event-loop scheduling and CPU contention.
- Async handlers (`_on_tts_rms`, `_on_preview`, `_on_set_pose`, idle/keyframe
  loops) keep only setting `_target_pose` — already the existing pattern
  (`_safe_apply` just assigns the target). The change is contained to the
  stepper + bus ownership.
- The writer thread becoming the sole in-process writer also closes the
  in-process half of Mechanism A.

**Defense-in-depth (do this regardless of threading model):** **clamp the
stepper `dt`** to a small multiple of nominal (e.g. `min(dt, 2 * stepper_dt)`)
before passing it to `smooth_damp`, so a single long gap can never produce a
lurch even if one slips through.

**Cross-thread fields to handle carefully:**
- `_target_pose` — written by the event loop, read by the writer thread.
  Reference read/write of an immutable `AnimatorPose` is atomic under the GIL;
  a `threading.Lock` is optional but fine.
- `_current_pose` — written by the writer thread (`_move_to_pose`), read by
  event-loop handlers. Same atomicity note; eventual consistency is acceptable.
- `_has_u2d2` and the degraded-state publish — on a write `OSError` the thread
  must NOT `await` NATS directly. Capture the loop with
  `asyncio.get_running_loop()` at startup and use
  `loop.call_soon_threadsafe(...)` to schedule `_publish_state("degraded", ...)`
  on the loop.

**Lifecycle:**
- Start the writer thread in `on_startup` **after** `open()` / `configure_limits()`
  / `enable_torque()` and after seeding the smoother from the present pose.
- In `on_shutdown`, signal+**join** the writer thread **before** `disable_torque()`
  / `close()`, mirroring the current "await tasks before closing bus" guarantee
  so nothing writes after torque-off.

**Alternative (smaller diff, acceptable):** keep `_stepper_loop` a coroutine
but offload the write via a single-thread executor:
`await loop.run_in_executor(self._writer_executor, self._write_pose_blocking, new_pose)`.
A single-thread executor preserves write ordering and prevents overlap. This
keeps the test driving unchanged. If chosen, still add the `dt` clamp, and note
that `dt` will include the awaited write time — the clamp is what prevents the
resulting lurch.

**Files:** `packages/animator/src/lafufu_animator/service.py` (stepper +
lifecycle), possibly `motion.py` (accept/clamp `dt`). No change to `dxl_bus.py`
or the NATS handlers' contract.

## Fix A — prevent two-writers-on-one-bus (combined debug GUI)

Upgrade `debug/lipsync/server.py`:

- **Bus guard:** before `JawBus.open()`, run `systemctl is-active lafufu-animator`
  (and `lafufu-agent`); if active, return a clear error ("stop lafufu-animator
  first — it owns the servo bus") instead of opening a second writer.
- **Process/control panel** in the existing web UI showing, live:
  - each `lafufu-*` service's active state (`systemctl is-active`),
  - the running `python -m lafufu_*` PIDs,
  - what's listening on :8080 / :8090,
  - **Start/Stop buttons** for the lafufu services. Passwordless `systemctl`
    for the `lafufu` user already exists via the sudoers fragment installed by
    `deploy/install.sh` (`/etc/sudoers.d/lafufu-services`) — reuse it; do not
    broaden it. Add new endpoints (e.g. `POST /api/services/{name}/{start|stop}`,
    `GET /api/services`) with the service name validated against a fixed allowlist.
- Keep the existing audio/servo run controls; surface the same start/stop there.

**Files:** `debug/lipsync/server.py` (endpoints + HTML/JS panel). Verify the
sudoers allowlist covers `start`/`stop`/`is-active` for the lafufu units; if it
only covers `restart`, extend `deploy/sudoers/lafufu-services` (and document it).

## Hardware confirmation (when the U2D2 + Dynamixels are reconnected)

1. With only the animator running (testbed/:8090 NOT running, one browser tab):
   servos should be **smooth**. Confirms the steady state is healthy.
2. Open the studio tab to drive CPU up: if twitch returns on *current* code,
   Mechanism B is confirmed live; after Fix B it should stay smooth.
3. Start the testbed without stopping the animator on *current* code: twitch
   appears (Mechanism A). After Fix A, the testbed refuses to open the bus.

## Where this lands

The fix touches prod (`packages/animator`) and tooling (`debug/lipsync`). The
canonical branch for the forthcoming `lafufu-prod` consolidation is **not yet
chosen** (see the consolidation effort). Implement on the agreed canonical
branch so it isn't superseded. The acceptance test is branch-independent.

## Out of scope

- Lipsync *quality* (weak/slurred mouth) — a separate, known follow-up
  (content-adaptive RMS / deadzone / gamma; see
  `debug/lipsync/legacy-comparison.md` and the prod-hardening plan T13).
- The `lafufu-prod` repo consolidation itself.
