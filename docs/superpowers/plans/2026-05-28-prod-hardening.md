# Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task is TDD: write the failing test first, watch it fail, implement, watch it pass, commit.

**Goal:** Close the field-reliability, durability, lifecycle, and API-safety gaps surfaced by the 2026-05-28 production re-audit, so Lafufu can be turned over for prod (trusted-LAN kiosk) without freezing/bricking under ordinary failures or losing operator data.

**Architecture:** Five Python packages (`agent`, `animator`, `control`, `printer`, `shared`) + SolidJS web SPA, communicating over NATS, on a Raspberry Pi. Services are `BaseService` subclasses running asyncio loops. The animator drives Dynamixel servos over a USB serial U2D2 via `dynamixel_sdk`. Control hosts the FastAPI HTTP API + WS bridge + SQLite settings/chat/animation DB.

**Tech Stack:** Python 3.13, asyncio, `nats-py`, `dynamixel_sdk`, FastAPI/uvicorn, SQLModel/SQLite (WAL), pytest (`uv run pytest`), pre-commit ruff. Run all tests from the repo root with `uv run pytest` (matches CI). Per-package: `uv run --project packages/<pkg> pytest packages/<pkg>/tests/`.

**Branch:** Create a fresh branch off `main` for this work (e.g. `chore/prod-hardening`). Do NOT commit to `main` directly. PR #25 (wakeword cleanup) may still be open — rebase on it or `main`, whichever is current.

---

## Out of scope (explicitly deferred)

- **Lipsync sync "modes"** (envelope / direct / gate selectable strategy). The operator wants this done *after* this hardening pass, with hardware in hand. DO NOT touch lipsync behavior in this plan.
- **Auth enablement.** Token auth (`LAFUFU_API_TOKEN` + `require_auth`) already exists and is intentionally left OFF until turnover; the operator flips it on then. Do NOT change the auth default. The WS allow-list (Task 7) is in scope because it's a gap that persists even with auth ON.
- **Full NATS multi-host accounts/TLS.** Single-host loopback is the deployment model.

## Hardware-validation note

**Task 1 (DXL worker thread) and Task 2 (reconnect) change how the animator talks to real servos.** All logic here is covered by `FakeDxlBus` tests, but the worker-thread offload and reconnect MUST be smoke-tested on the physical robot before turnover (move servos via the admin sliders; unplug/replug the U2D2 and confirm recovery). Flag this to the operator at the end of execution.

## Audit provenance

Findings below come from the 2026-05-28 re-audit (5 parallel domain reviewers) of `main`. The canonical older review is `docs/2026-05-20-production-readiness-review.md`; several of its items are already fixed (auth, NATS loopback, sudoers install, servo accel-limiting, WAL+busy_timeout, startup ordering, many failure-path tests). This plan covers only what's STILL open.

---

## File Structure

| File | Tasks | Responsibility |
|---|---|---|
| `packages/animator/src/lafufu_animator/dxl_bus.py` | 1, 2 | Real DXL bus — gains a worker thread + timeout + reconnect |
| `packages/animator/src/lafufu_animator/service.py` | 1, 2, 3 | Stepper loop enqueues writes; shutdown awaits tasks + closes bus |
| `packages/shared/src/lafufu_shared/testing.py` | 1, 2 | `FakeDxlBus` gains the new interface (enqueue/timeout/reconnect hooks) |
| `packages/agent/src/lafufu_agent/service.py` | 3, 9 | Shutdown awaits mic task; `_set_alsa_volume` off-loop |
| `packages/agent/src/lafufu_agent/__main__.py` | 4 | `_AplayPlayer` gains `close()` that reaps the subprocess |
| `packages/control/src/lafufu_control/service.py` | 3, 9 | Shutdown drains; `_rebroadcast_all_settings` off-loop |
| `packages/control/src/lafufu_control/db.py` | 5, 6 | Backup-on-startup + schema-version guard |
| `packages/control/src/lafufu_control/api/ws_bridge.py` | 7 | Subscribe allow-list |
| `packages/control/src/lafufu_control/api/routers/agent.py`, `printer.py` | 8 | Bounded text fields |
| `packages/control/src/lafufu_control/api/routers/settings.py` | 9 | `value_type` validation |
| `deploy/systemd/*.service`, `deploy/nats/*.conf` | 10 | TimeoutStopSec, WatchdogSec, journald cap, btcast user |
| `packages/shared/src/lafufu_shared/nats_helper.py` | 11 | Connection lifecycle callbacks |
| `deploy/install.sh`, `deploy/deploy.sh` (new), `README.md` | 12 | Deploy robustness |

---

## PHASE 1 — DXL field reliability (highest priority)

### Task 1: Move DXL serial I/O onto a dedicated worker thread

**Problem (audit Critical #1, #3):** `AnimatorService._move_to_pose` → `DxlBus.write` (`dxl_bus.py:134`) is a synchronous blocking USB serial round-trip executed directly on the asyncio event-loop thread from the 30 Hz `_stepper_loop` and from NATS handlers. One slow/wedged servo (or an unplugged U2D2 where the SDK packet read blocks) freezes heartbeats, lipsync, pose-publish, and NATS draining — the whole animator hangs until the SDK gives up. There is no per-transaction timeout.

**Fix approach:** Give `DxlBus` a single dedicated worker thread that owns the serial port. Public `write(name, position)` becomes non-blocking: it coalesces the latest goal-position per servo into a thread-safe pending dict and signals the worker. The worker drains pending writes at its own pace and performs the blocking SDK calls off the event loop. `read(name)` (used only at startup seeding) stays synchronous but is wrapped so callers run it via `asyncio.to_thread` — OR keep `read` synchronous and only call it before the loop starts (it already is). Add a `_TXN_TIMEOUT_S` budget: the worker uses the SDK's port timeout (set via `port.setPacketTimeoutMillis`) so a wedged transaction returns an error instead of blocking forever.

Coalescing (latest-wins per servo) is correct here because the stepper already sends a fresh full pose every tick; stale intermediate positions have no value.

**Files:**
- Modify: `packages/animator/src/lafufu_animator/dxl_bus.py`
- Modify: `packages/animator/src/lafufu_animator/service.py` (stepper loop: `write` is now fire-and-forget; remove the per-write `OSError`→degraded inline handling and instead consume a bus health flag the worker sets)
- Modify: `packages/shared/src/lafufu_shared/testing.py` (`FakeDxlBus`: match the new non-blocking `write` + `start()/close()` + `healthy` property)
- Test: `packages/animator/tests/test_dxl_bus.py`, `packages/animator/tests/test_service.py`

- [ ] **Step 1: Read the current bus + stepper to capture the exact interface.**
Run: `sed -n '1,200p' packages/animator/src/lafufu_animator/dxl_bus.py` and the `_stepper_loop` + `_move_to_pose` + `on_startup` in `service.py`. Note every method the service calls on the bus (`open`, `write`, `read`, `enable_torque`, `disable_torque`, `configure_limits`, `close`) and where `_has_u2d2` is set.

- [ ] **Step 2: Write the failing test for non-blocking write + worker drain.**
In `packages/animator/tests/test_dxl_bus.py`, using the real `DxlBus` with a stubbed `PortHandler`/`PacketHandler` (inject fakes so no hardware is needed — follow the existing test's stubbing style), assert:
```python
def test_write_is_nonblocking_and_worker_applies_latest(monkeypatch):
    # A packet handler whose write blocks 200ms, to prove write() returns immediately.
    applied = []
    pkt = _make_stub_packet_handler(on_write=lambda dxl_id, pos: (time.sleep(0.2), applied.append((dxl_id, pos))))
    bus = DxlBus(port="x", baud=57600, packet_handler=pkt, port_handler=_StubPort())
    bus.open(); bus.start()
    t0 = time.monotonic()
    bus.write("jaw", 1700)
    bus.write("jaw", 1750)  # coalesces — latest wins
    assert time.monotonic() - t0 < 0.05, "write() must not block on the serial round-trip"
    _wait_until(lambda: applied, timeout=1.0)
    bus.close()
    # The worker applied the LATEST jaw goal, not necessarily both.
    assert applied[-1][1] == 1750
```
Add `_make_stub_packet_handler`, `_StubPort`, `_wait_until` helpers if not present. The DxlBus constructor must accept injectable `packet_handler`/`port_handler` for testability (add optional kwargs defaulting to None → real ones).

- [ ] **Step 3: Run it, expect failure.**
Run: `uv run --project packages/animator pytest packages/animator/tests/test_dxl_bus.py::test_write_is_nonblocking_and_worker_applies_latest -v`
Expected: FAIL (current `write` blocks / no `start`).

- [ ] **Step 4: Implement the worker thread in `dxl_bus.py`.**
- Add a `threading.Thread` started by `start()`, stopped by `close()` (set a `threading.Event`, join with a 2s timeout).
- `write(name, position)`: under a `threading.Lock`, set `self._pending[name] = clamped_position`; set a `threading.Event` to wake the worker. Return immediately.
- Worker loop: wait on the event (with a short timeout, e.g. 0.1s), snapshot+clear `_pending` under the lock, and for each (name, pos) do the blocking `packet.write4ByteTxRx`. On comm error/exception set `self._healthy = False` and record the error; do NOT raise out of the thread.
- Set the SDK port packet timeout so a single transaction can't block more than ~`_TXN_TIMEOUT_S` (e.g. `port.setPacketTimeoutMillis(200)`); if the SDK build lacks it, document and rely on the OS serial timeout.
- Expose `@property healthy(self) -> bool: return self._healthy`.
- Keep `read`, `enable_torque`, `disable_torque`, `configure_limits` synchronous (called only at startup, before `start()`), but guard them so they no-op cleanly if the worker is running.

- [ ] **Step 5: Update `FakeDxlBus` in `shared/testing.py`** to the same interface: non-blocking `write` (record into a dict), `start()`/`close()` no-ops or thread-free, `healthy` property (default True, settable by tests), keep existing `reconnect()`.

- [ ] **Step 6: Rewire `AnimatorService`.**
- `on_startup`: after `open()`/`configure_limits()`/`enable_torque()`/seed, call `self._bus.start()`.
- `_stepper_loop`: replace the `_move_to_pose` try/except-OSError block with: compute the eased pose, call `self._bus.write(...)` per servo (non-blocking), then set `self._has_u2d2 = self._bus.healthy` and publish `degraded` on a True→False transition.
- `on_shutdown`: (Task 3 will finalize) call `self._bus.close()`.

- [ ] **Step 7: Run the full animator suite.**
Run: `uv run --project packages/animator pytest packages/animator/tests/ -q`
Expected: PASS (fix any test that assumed synchronous write-then-read semantics; the stepper no longer raises on write).

- [ ] **Step 8: Commit.**
```bash
git add packages/animator/src/lafufu_animator/dxl_bus.py packages/animator/src/lafufu_animator/service.py packages/shared/src/lafufu_shared/testing.py packages/animator/tests/test_dxl_bus.py packages/animator/tests/test_service.py
git commit -m "perf(animator): move DXL serial I/O to a dedicated worker thread (non-blocking writes + txn timeout)"
```

**Acceptance:** `write()` returns in <50 ms regardless of serial latency; a 30 Hz stepper tick never blocks the event loop on USB; a wedged transaction marks the bus unhealthy instead of hanging the service.

---

### Task 2: Auto-reconnect the DXL bus after a transient disconnect

**Problem (audit Critical #2):** Once a write errors (USB unplug), `_healthy`/`_has_u2d2` goes False and nothing ever restores it — the head is bricked until the process restarts. `reconnect()` exists only on `FakeDxlBus`, never on the real bus, never called by the service.

**Fix approach:** In the worker thread, when `_healthy` is False, attempt a bounded reopen (close port, reopen, re-`configure_limits`, re-`enable_torque`) every `_RECONNECT_INTERVAL_S` (e.g. 3 s). On success set `_healthy = True`. The animator's stepper picks up `healthy` and clears `degraded` automatically.

**Files:**
- Modify: `packages/animator/src/lafufu_animator/dxl_bus.py`
- Modify: `packages/animator/src/lafufu_animator/service.py` (already reads `self._bus.healthy`; ensure a False→True transition re-publishes `idle`/active state)
- Test: `packages/animator/tests/test_dxl_bus.py`, `packages/animator/tests/test_service.py`

- [ ] **Step 1: Write the failing test.**
```python
def test_worker_reconnects_after_transient_failure():
    pkt = _make_stub_packet_handler()
    port = _StubPort()
    bus = DxlBus(port="x", baud=57600, packet_handler=pkt, port_handler=port)
    bus.open(); bus.start()
    bus._healthy = False            # simulate a write failure flipping health
    port.fail_open_times = 0        # next reopen succeeds
    _wait_until(lambda: bus.healthy, timeout=5.0)  # worker reopened on its own
    assert bus.healthy is True
    bus.close()
```
(Have `_StubPort` count `openPort()` calls and optionally fail the first N.)

- [ ] **Step 2: Run it, expect failure** (`bus.healthy` stays False forever).
Run: `uv run --project packages/animator pytest packages/animator/tests/test_dxl_bus.py::test_worker_reconnects_after_transient_failure -v`

- [ ] **Step 3: Implement reconnect in the worker loop.** When `not self._healthy`, and at most every `_RECONNECT_INTERVAL_S`, run `_try_reconnect()`: `closePort()` (suppress errors) → `openPort()` + `setBaudRate()` → `configure_limits()` → `enable_torque()`; on full success `self._healthy = True` and log `dxl.reconnected`. Throttle so it doesn't spin.

- [ ] **Step 4: Add a service test** in `test_service.py`: with `FakeDxlBus`, flip `bus._healthy=False` then back True (or call its `reconnect()`), drive a stepper tick or two, and assert the service re-publishes a non-degraded state. (Use the existing `test_degrades_gracefully_when_bus_disconnects` as the template; add the recovery half.)

- [ ] **Step 5: Run animator suite + commit.**
```bash
uv run --project packages/animator pytest packages/animator/tests/ -q
git add packages/animator/src/lafufu_animator/dxl_bus.py packages/animator/src/lafufu_animator/service.py packages/animator/tests/test_dxl_bus.py packages/animator/tests/test_service.py
git commit -m "fix(animator): DXL bus auto-reconnects on the worker thread after USB disconnect"
```

**Acceptance:** Unplugging the U2D2 marks the service `degraded`; replugging restores motion within a few seconds with no process restart.

---

## PHASE 2 — Lifecycle & shutdown

### Task 3: Graceful shutdown — await cancelled tasks + close the bus + torque-off ordering

**Problem (audit High):** `BaseService.run()` only awaits the heartbeat task on shutdown; every service-owned task is `cancel()`'d and never awaited. In the animator, the 5 background tasks can write to the bus *after* `disable_torque()` (racy re-energize), and `DxlBus.close()` (now exists) is never called → serial FD leak each restart. In the agent, `_mic_loop_task.cancel()` has no await and can race `_mic.close()`.

**Fix approach:** Add an awaited-cancellation helper and use it in each `on_shutdown`, in the correct order: (1) cancel + await background tasks so nothing touches the bus/mic anymore, (2) then disable torque + `close()` the bus / close the mic + speaker.

**Files:**
- Modify: `packages/animator/src/lafufu_animator/service.py` (`on_shutdown`)
- Modify: `packages/agent/src/lafufu_agent/service.py` (`on_shutdown`)
- Modify: `packages/control/src/lafufu_control/service.py` (`on_shutdown`)
- Test: `packages/animator/tests/test_service.py`, `packages/agent/tests/test_service.py`

- [ ] **Step 1: Write the failing animator test.**
```python
async def test_shutdown_awaits_tasks_before_closing_bus(nats_server):
    bus = FakeDxlBus()
    svc = AnimatorService(bus=bus, nats_url=nats_server)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
    assert bus.closed is True, "DxlBus.close() must be called on shutdown"
    assert bus.torque_disabled is True
    # No background task is still pending.
    for t in (svc._stepper_task, svc._pose_publish_task, svc._keyframe_player_task,
              svc._lipsync_watchdog_task):
        assert t is None or t.done()
```
(Add `closed`/`torque_disabled` flags to `FakeDxlBus`.)

- [ ] **Step 2: Run it, expect failure** (`bus.closed` is False — close never called).

- [ ] **Step 3: Implement.** In `animator on_shutdown`:
```python
async def on_shutdown(self) -> None:
    for t in (self._pose_publish_task, self._lipsync_watchdog_task,
              self._keyframe_player_task, self._stepper_task, self._idle_request_task):
        if t:
            t.cancel()
    # Await them so no task writes to the bus after we disable torque.
    await asyncio.gather(
        *[t for t in (self._pose_publish_task, self._lipsync_watchdog_task,
                      self._keyframe_player_task, self._stepper_task, self._idle_request_task) if t],
        return_exceptions=True,
    )
    with contextlib.suppress(Exception):
        self._bus.disable_torque()
    with contextlib.suppress(Exception):
        self._bus.close()
```
In `agent on_shutdown`: cancel `_mic_loop_task`, `await` it (suppress CancelledError), THEN `self._mic.close()` and speaker close. In `control on_shutdown`: set `should_exit`, and `await asyncio.to_thread(engine.dispose)` (or `engine.dispose()`) to release the DB pool.

- [ ] **Step 4: Add agent shutdown test** asserting the mic-loop task is awaited (done) before `mic.close()` is called (track call order with a flag on a fake mic).

- [ ] **Step 5: Run animator + agent suites + commit.**
```bash
uv run --project packages/animator pytest packages/animator/tests/ -q
uv run --project packages/agent pytest packages/agent/tests/ -q
git add packages/animator/src/lafufu_animator/service.py packages/agent/src/lafufu_agent/service.py packages/control/src/lafufu_control/service.py packages/animator/tests/test_service.py packages/agent/tests/test_service.py packages/shared/src/lafufu_shared/testing.py
git commit -m "fix: await background tasks before releasing resources on shutdown; close DXL bus + DB pool"
```

**Acceptance:** Clean shutdown calls `DxlBus.close()` after all tasks finish; no task writes post-torque-off; DB pool disposed.

---

### Task 4: Reap the aplay subprocess on agent shutdown

**Problem (audit Medium):** `_AplayPlayer` (the Pi production speaker path, `agent/__main__.py`) never `wait()`s its `aplay` Popen and has no `close()`, so `on_shutdown`'s `hasattr(_speaker_play, "close")` guard is a no-op for it → orphan `aplay` processes accumulate across restarts.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py` (`_AplayPlayer`)
- Test: `packages/agent/tests/test_pipeline.py` (it already stubs Popen for `_AplayPlayer`)

- [ ] **Step 1: Write failing test** — construct `_AplayPlayer` with a stub Popen that records `terminate()`/`wait()`; call `player.play(b"..")` then `player.close()`; assert the proc was terminated and waited.
- [ ] **Step 2: Run, expect failure** (`close` AttributeError or no terminate).
- [ ] **Step 3: Implement `_AplayPlayer.close()`** — if `self._proc` is not None: close stdin (suppress), `terminate()`, `wait(timeout=1)`, on TimeoutExpired `kill()`; set `_proc=None`. (Keep `end()` as the per-utterance drain; `close()` is the hard stop for shutdown.)
- [ ] **Step 4: Run agent suite + commit.**
```bash
uv run --project packages/agent pytest packages/agent/tests/ -q
git add packages/agent/src/lafufu_agent/__main__.py packages/agent/tests/test_pipeline.py
git commit -m "fix(agent): _AplayPlayer.close() reaps the aplay subprocess so it doesn't orphan on shutdown"
```

**Acceptance:** Agent shutdown leaves no orphan `aplay` process.

---

## PHASE 3 — Data durability

### Task 5: Back up the SQLite DB on control startup (rotating copies)

**Problem (audit High):** `/var/lafufu/db.sqlite` (settings, chats, animations, letterheads) has no backup anywhere. SD-card corruption or a bad change loses all tuning permanently.

**Fix approach:** On control startup, after `init_db`, make a timestamped backup copy into `<data_dir>/backups/` using SQLite's online backup API (`sqlite3` `Connection.backup`) so it's consistent under WAL. Keep the most recent N (e.g. 7). This is cheap, dependency-free, and recovers the 95% case (corruption/bad-deploy).

**Files:**
- Modify: `packages/control/src/lafufu_control/db.py` (add `backup_db(db_path, keep=7)`)
- Modify: `packages/control/src/lafufu_control/service.py` (`on_startup`: call it after `init_db`)
- Test: `packages/control/tests/test_db.py`

- [ ] **Step 1: Write failing test.**
```python
def test_backup_db_creates_rotating_copies(tmp_path):
    db = tmp_path / "db.sqlite"
    eng = create_engine_for_path(str(db)); init_db(eng)
    from lafufu_control.db import backup_db
    for _ in range(9):
        backup_db(str(db), keep=7)
    backups = sorted((tmp_path / "backups").glob("db-*.sqlite"))
    assert 1 <= len(backups) <= 7           # rotation caps the count
    # A backup is a valid, openable SQLite file with the schema.
    import sqlite3
    con = sqlite3.connect(str(backups[-1]))
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
```
- [ ] **Step 2: Run, expect failure** (`backup_db` undefined).
- [ ] **Step 3: Implement `backup_db`** using `sqlite3.connect(src).backup(sqlite3.connect(dest))`, timestamped filename `db-YYYYMMDD-HHMMSS.sqlite` under `Path(db_path).parent / "backups"` (mkdir), then prune oldest beyond `keep`. Wrap in try/except → log a warning on failure (a backup failure must NOT block startup).
- [ ] **Step 4: Wire into `control on_startup`** after `init_db(engine)`: `backup_db(str(settings.db_path()))`.
- [ ] **Step 5: Run control suite + commit.**
```bash
uv run --project packages/control pytest packages/control/tests/ -q
git add packages/control/src/lafufu_control/db.py packages/control/src/lafufu_control/service.py packages/control/tests/test_db.py
git commit -m "feat(control): rotating SQLite backup on startup so operator data survives corruption"
```

**Acceptance:** Each control start writes a consistent DB snapshot to `/var/lafufu/backups/`, capped at 7.

---

### Task 6: Schema-version guard (detect code/DB drift)

**Problem (audit High):** Migration story is `create_all` + two hardcoded `ADD COLUMN`s. A future schema change silently won't apply to a deployed DB; the mismatch surfaces as an opaque runtime `OperationalError` with no version detection.

**Fix approach:** Lightweight, not Alembic. Store an integer `schema_version` in a tiny `_meta` table (or reuse the Setting table with an internal key `bootstrap.schema_version`). On startup, compare the code's `CURRENT_SCHEMA_VERSION` constant to the stored value: if stored < current, log a loud warning naming both versions and pointing at the backup just taken (Task 5); if stored > current (DB newer than code — downgrade), refuse to start with a clear error. This converts silent drift into a diagnosable signal. Actual column migrations remain manual but are now *detected*.

**Files:**
- Modify: `packages/control/src/lafufu_control/db.py` (constant + `check_schema_version(engine)`)
- Modify: `packages/control/src/lafufu_control/service.py` (`on_startup` after backup)
- Test: `packages/control/tests/test_db.py`

- [ ] **Step 1: Write failing tests** for three cases: fresh DB (stamps current, no warning), stored<current (logs warning via caplog, does not raise), stored>current (raises RuntimeError). Use `bootstrap.schema_version` so it's hidden by the existing `is_internal_key` filter.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement.** `CURRENT_SCHEMA_VERSION = 1`. `check_schema_version(engine)`: read the row; if absent, insert current; if stored < current → `log.warning(...)`; if stored > current → `raise RuntimeError(...)`.
- [ ] **Step 4: Wire into `on_startup`** after `backup_db`. (Confirm `bootstrap.schema_version` is hidden from `/api/settings`, snapshot, and rebroadcast — it is, via the centralized `is_internal_key`.)
- [ ] **Step 5: Run control suite + commit.**
```bash
uv run --project packages/control pytest packages/control/tests/ -q
git add packages/control/src/lafufu_control/db.py packages/control/src/lafufu_control/service.py packages/control/tests/test_db.py
git commit -m "feat(control): schema-version guard turns silent code/DB drift into a loud, diagnosable signal"
```

**Acceptance:** A version mismatch is logged (or refused on downgrade), not an opaque OperationalError later.

---

## PHASE 4 — API / bus safety

### Task 7: WS subscribe allow-list

**Problem (audit High, auth-adjacent):** `WsBridge._add_sub` (`ws_bridge.py`) forwards any browser-supplied subscribe pattern straight to `nats.subscribe(pattern)` with no allow-list. An authed-but-curious client can `{"op":"sub","topics":[">"]}` and read the entire bus (every transcript, etc.). Persists even with auth on. NOTE: the SPA legitimately needs the firehose (`system_pulse.tsx` subscribes to `>`), so the allow-list must permit the read-only firehose while blocking nothing useful — the goal is a server-side guard that only permits known-good subjects/prefixes, and the firehose `>` is a deliberate allowed entry gated by the (soon-on) auth.

**Fix approach:** Define an allow-list of permitted subscribe patterns (the exact subjects the SPA uses + the `>` firehose). Reject anything else with a logged warning and no subscription. Since the bridge is publish-incapable already (only `sub`/`unsub`), this closes the read-exfil path to a known set.

**Files:**
- Modify: `packages/control/src/lafufu_control/api/ws_bridge.py`
- Test: `packages/control/tests/test_ws_bridge.py`

- [ ] **Step 1: Enumerate the SPA's real subscriptions.** Grep `web/src` for `.subscribe(`: collect every pattern (`agent.state.*`, `agent.reply`, `agent.transcript`, `agent.tts.rms`, `agent.wakeword.state`, `system.heartbeat.*`, `*.state.*`, `system.service.*`, `animator.pose`, `>`). These form the allow-list.
- [ ] **Step 2: Write failing test.**
```python
async def test_ws_rejects_unlisted_subscribe_pattern(...):
    # subscribing to an allowed pattern works; subscribing to "secret.>" is rejected
    # (no NATS subscription created, ws stays open).
```
- [ ] **Step 3: Run, expect failure.**
- [ ] **Step 4: Implement** `ALLOWED_WS_PATTERNS` (a set of literal patterns + a small matcher) in `ws_bridge.py`; in `_add_sub`/the `op=="sub"` handler, skip + log `ws.subscribe.rejected pattern=%s` for anything not allowed. Keep the existing ref-counting for allowed ones.
- [ ] **Step 5: Run control suite + commit.**
```bash
uv run --project packages/control pytest packages/control/tests/ -q
git add packages/control/src/lafufu_control/api/ws_bridge.py packages/control/tests/test_ws_bridge.py
git commit -m "fix(control): allow-list WS subscribe patterns so a client can't sub to arbitrary subjects"
```

**Acceptance:** A WS client can subscribe only to the known SPA patterns + firehose; unknown patterns are rejected and logged.

---

### Task 8: Bound operator text inputs

**Problem (audit Medium):** `TextMessageBody.text`, `SpeakTextBody.text` (`routers/agent.py`) and `ComposeReq.text` (`routers/printer.py`) are unbounded `str`. A multi-MB string gets published to NATS / composited by PIL → oversized-frame / DoS on the kiosk.

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/agent.py`, `printer.py`
- Test: `packages/control/tests/test_api_agent.py`, `test_api_printer.py`

- [ ] **Step 1: Write failing tests** — POST a 100_001-char text to each endpoint; expect HTTP 422.
- [ ] **Step 2: Run, expect failure** (currently 200/202).
- [ ] **Step 3: Implement** Pydantic `Field(max_length=N)` on each text field — agent text/speak `max_length=2000` (a spoken reply is short), printer compose `max_length=4000` (matches the DB column cap). Pick values that fit the Setting/use; document the choice in a one-line comment.
- [ ] **Step 4: Run control suite + commit.**
```bash
uv run --project packages/control pytest packages/control/tests/ -q
git add packages/control/src/lafufu_control/api/routers/agent.py packages/control/src/lafufu_control/api/routers/printer.py packages/control/tests/test_api_agent.py packages/control/tests/test_api_printer.py
git commit -m "fix(control): bound operator text inputs (agent/speak/printer) to prevent oversized-payload DoS"
```

**Acceptance:** Over-length text returns 422; never reaches NATS/PIL.

---

### Task 9: Remove remaining event-loop blockers + validate setting value_type

**Problem (audit Medium):** `control _rebroadcast_all_settings` runs sync `Session(engine)` reads in an `async def`; `agent _set_alsa_volume` runs `subprocess.run` (timeout=3) in an async handler; `settings _encode` accepts any `value_type` string (typo → corrupt data persists).

**Files:**
- Modify: `packages/control/src/lafufu_control/service.py` (`_rebroadcast_all_settings`)
- Modify: `packages/agent/src/lafufu_agent/service.py` (`_on_config_volume` → `_set_alsa_volume`)
- Modify: `packages/control/src/lafufu_control/api/routers/settings.py` (`_encode`)
- Test: `packages/control/tests/test_settings_router.py`, existing agent tests

- [ ] **Step 1 (value_type): failing test** — PUT a setting with `value_type="jsonn"` (typo); expect 422 (not silent str fallthrough).
- [ ] **Step 2: Implement** an allowed set `{"str","int","float","bool","json"}`; `_encode`/`SettingIn` validation rejects others with 422.
- [ ] **Step 3 (off-loop): refactor** the SQLite read in `_rebroadcast_all_settings` to `await asyncio.to_thread(_read_rows)`; wrap `_set_alsa_volume`'s `subprocess.run` call site with `await asyncio.to_thread(...)`. (Both are low-frequency; this removes the anti-pattern + the 3 s worst-case loop stall.)
- [ ] **Step 4: Run control + agent suites + commit.**
```bash
uv run --project packages/control pytest packages/control/tests/ -q
uv run --project packages/agent pytest packages/agent/tests/ -q
git add packages/control/src/lafufu_control/service.py packages/agent/src/lafufu_agent/service.py packages/control/src/lafufu_control/api/routers/settings.py packages/control/tests/test_settings_router.py
git commit -m "fix: off-load remaining sync I/O from event loops; reject unknown setting value_type"
```

**Acceptance:** No blocking SQLite/subprocess on an event loop; bad `value_type` is rejected at the API.

---

## PHASE 5 — Ops & observability

### Task 10: systemd hardening — TimeoutStopSec, WatchdogSec, journald cap, btcast user

**Problem (audit Medium):** control unit lacks `TimeoutStopSec` (90 s default blocks `lafufu.target` teardown); no `WatchdogSec` anywhere despite a 5 s heartbeat (a hung process is never auto-restarted); no journald size cap (unbounded log growth over weeks); `lafufu-btcast.service` runs as root while all others run as `lafufu`.

**Files:**
- Modify: `deploy/systemd/lafufu-control.service`, `lafufu-btcast.service`, and add a journald drop-in or document `journald.conf` `SystemMaxUse`.
- (No test — infra files. Verify by inspection + `install.sh` copies them.)

- [ ] **Step 1:** Add `TimeoutStopSec=15s` to `lafufu-control.service` (match agent). Add `User=lafufu`/`Group=lafufu` to `lafufu-btcast.service` (verify btcast doesn't actually need root for `bluetoothctl`; if it does, document why and leave + add a comment). 
- [ ] **Step 2:** Add a journald cap: create `deploy/systemd/journald-lafufu.conf` (`[Journal]\nSystemMaxUse=200M`) and have `install.sh` copy it to `/etc/systemd/journald.conf.d/`. (WatchdogSec requires the services to call `sd_notify(WATCHDOG=1)` — out of scope to wire the notify; instead add a comment in the units noting WatchdogSec is deferred until sd_notify is wired, OR add `Restart=on-failure` + `RuntimeMaxSec` if appropriate. Keep this conservative — don't add WatchdogSec without the notify or systemd will kill healthy services.)
- [ ] **Step 3:** Update `deploy/install.sh` to copy the journald drop-in; `systemctl daemon-reload`.
- [ ] **Step 4: Commit.**
```bash
git add deploy/systemd/ deploy/install.sh
git commit -m "ops: control TimeoutStopSec, journald size cap, btcast non-root (or documented), install copies drop-in"
```

**Acceptance:** Control stops within 15 s; logs are size-capped; btcast runs unprivileged unless documented otherwise.

---

### Task 11: NATS connection lifecycle callbacks (observability)

**Problem (audit Low):** NATS auto-reconnects forever (good) but `disconnected_cb`/`reconnected_cb`/`closed_cb`/`error_cb` are unwired, so a flapping link is invisible in logs.

**Files:**
- Modify: `packages/shared/src/lafufu_shared/nats_helper.py` (the `connect`/`connect_with_retry` options)
- Test: `packages/shared/tests/` (assert the callbacks are passed to `nats.connect`)

- [ ] **Step 1: Failing test** — patch `nats.connect` and assert `connect_with_retry` passes non-None `disconnected_cb`, `reconnected_cb`, `closed_cb`, `error_cb`.
- [ ] **Step 2: Implement** module-level async callbacks that `log.warning("nats.disconnected")`, `log.info("nats.reconnected")`, etc., and pass them into `nats.connect(...)`.
- [ ] **Step 3: Run shared suite + commit.**
```bash
uv run --project packages/shared pytest packages/shared/tests/ -q
git add packages/shared/src/lafufu_shared/nats_helper.py packages/shared/tests/
git commit -m "obs(shared): wire NATS connection lifecycle callbacks so link flaps are visible in logs"
```

**Acceptance:** Disconnect/reconnect events are logged on every service.

---

### Task 12: Deploy robustness

**Problem (audit High/Medium):** `README.md:13` references a non-existent `./scripts/dev_run_all.sh`; the real deploy is ad-hoc `git pull --ff-only` on the Pi which aborts on a dirty tree/local commit with no recovery guidance. (Recurred this session: the Pi was found running stale code because a pull/restart step was skipped.)

**Files:**
- Create: `deploy/deploy.sh` (idempotent Pi-side update)
- Modify: `README.md` (fix the stale reference; document the real flow)
- (No unit test — shell script; keep it `set -euo pipefail` + clear echoes.)

- [ ] **Step 1:** Write `deploy/deploy.sh`: assert it's run on the Pi (checks `/srv/lafufu`), `git fetch`, detect a dirty working tree → print a clear message + `git stash` guidance and abort (do NOT auto-discard), `git pull --ff-only`, `uv sync --all-packages`, build web bundle if `web/` changed, `sudo /usr/bin/systemctl restart lafufu-control.service lafufu-agent.service lafufu-animator.service`, then `systemctl is-active` each and echo the result. Match the NOPASSWD sudoers entries (exact service names).
- [ ] **Step 2:** Fix `README.md` — remove the `dev_run_all.sh` reference; document: local dev (`uv sync --all-packages`, `dev-up.ps1` if present), and Pi deploy (`deploy/deploy.sh`).
- [ ] **Step 3: Commit.**
```bash
git add deploy/deploy.sh README.md
git commit -m "ops: idempotent deploy.sh (dirty-tree guard + pull + sync + restart + health) and fix README drift"
```

**Acceptance:** `deploy/deploy.sh` updates a Pi cleanly or fails loudly with recovery guidance; README matches reality.

---

## Self-review checklist (run before handing to execution)

- [ ] Every audit Tier-1/Tier-2 finding maps to a task: blocking I/O→T1, reconnect→T2, txn timeout→T1, shutdown await/close→T3, aplay reap→T4, DB backup→T5, schema drift→T6, WS allow-list→T7, text bounds→T8, off-loop+value_type→T9, systemd/journald/btcast→T10, NATS callbacks→T11, deploy→T12.
- [ ] No lipsync changes anywhere (deferred).
- [ ] No auth-default changes (deferred); WS allow-list (T7) is the only auth-adjacent item and it's independent of the token.
- [ ] Each task is TDD (failing test first) except infra-only T10/T12 (verified by inspection + install copy).
- [ ] Run `uv run pytest` at the repo ROOT after each phase — that's what CI runs and catches cross-package regressions.

## Execution order & risk

1. **Phase 1 (T1, T2)** first — highest field-reliability value, but highest risk and **needs hardware validation tonight** before turnover. If hardware time is short, T2 (reconnect) + the txn-timeout half of T1 are the must-haves; the full worker-thread offload can be staged.
2. **Phases 2–4** are low-risk, fully unit-testable, no hardware needed — safe to land before tonight.
3. **Phase 5** is infra/docs — land anytime.

Suggested: land Phases 2–4 + T11 today (no hardware), validate T1/T2 on the Pi tonight, do T10/T12 around the deploy.
