# Production-readiness follow-up — remaining items from the 2026-05-20 review

> **Status:** design · **Date:** 2026-05-26

## Overview

The 2026-05-20 production-readiness review (`docs/2026-05-20-production-readiness-review.md`) catalogued ~20 critical + 35 high findings across 10 themes. Six days later, after PRs #18, #20, and #21, the audit summary is:

- ✅ Resolved: C4 (DXL comm-result checks), C5 (servo profile-limit registers)
- 🟡 Partial: C1, C2, C6, C7, C9, C12 — scaffolding done, gaps remain
- ❌ Open: C3, C8, C10, C11 plus auth-default posture

**Auth is deferred** — operator deployment context doesn't require it yet. C1/C2 stay partial; bind-to-loopback, token-default-set, and the WS subscribe allow-list are not in scope for this design.

This spec organises the remaining work into prioritised, independently-shippable changes. Each item has its own implementation, files touched, and test plan — they don't need to land together.

## Goals

- Close the last critical-tier blocker (**C3** — DXL blocking I/O) before the next round of field testing.
- Make recovery paths actually fire when LLM / mic / NATS hiccup (currently the code reaches `degraded` only on a narrow set of hardware errors).
- Land the small surgical fixes (C8, C10, C11) that the May 20 review documented but no PR has touched yet.
- Tighten input validation around the bus-driven config surface so a malformed `lp_options` or `media` string can't shell out.
- Document remaining scope explicitly so future "is this done?" questions have a checked answer.

## Non-goals (this build)

- **No auth changes.** Operator deferral. Token-default-set, WS pattern allow-list, and `host` binding stay where they are. If/when auth becomes a requirement, see Theme 1 of the May 20 review for the full design direction.
- **No Alembic / migration framework.** The additive-column pattern PR #20 introduced in `init_db` is sufficient for this iteration. If columns ever need backfill or rename, revisit.
- **No structured logging migration.** printf-style strings stay; observability spec covers what to add, not what to replace.
- **No metrics endpoint** beyond `/healthz`. Prometheus / Grafana wiring is out of scope.
- **No multi-host hardening.** Bus + control assume same machine.

---

## Priority 1 — Critical to address before next field test

These items either pin CPU, hide failures, or freeze the robot.

### P1.1 — DXL serial I/O off the event loop (C3, Theme 2)

**The bug.** `packages/animator/src/lafufu_animator/dxl_bus.py` calls the synchronous `dynamixel_sdk` SDK directly. `service.py:_stepper_loop` invokes `_bus.write()` at ~150 Hz on the asyncio loop thread. A single slow servo (timeout, error-byte recovery) stalls the loop — heartbeats stop, lipsync hangs, NATS publishes queue up.

**Design.** Move all serial I/O behind a single-threaded worker with a command queue. The animator's asyncio code submits `BusCommand` objects and awaits their futures via `loop.run_in_executor(executor=worker, ...)` or `asyncio.to_thread` (Python 3.13's `to_thread` uses the default thread pool — fine for a one-thread-per-bus pattern as long as we dedicate the executor).

Concrete shape:

```python
# new file: packages/animator/src/lafufu_animator/dxl_worker.py
class DxlWorker:
    """Owns the serial port. All writes/reads happen on its single thread.
    Asyncio callers submit a coroutine via .write_async()/.read_async() and
    await the result. Errors raise normally on the caller's loop."""

    def __init__(self, bus: DxlBus) -> None:
        self._bus = bus
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dxl")

    async def write(self, name: str, position: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._bus.write, name, position)

    async def read(self, name: str) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._bus.read, name)

    async def close(self) -> None:
        # Drain in-flight work, then shut down. Called from on_shutdown
        # AFTER cancelling all loop tasks that might submit more work.
        self._executor.shutdown(wait=True)
        with contextlib.suppress(Exception):
            self._bus.close()
```

The `AnimatorService` keeps a `DxlBus` but submits all writes through `DxlWorker`. The `_stepper_loop` becomes `await worker.write(servo, pos)` per servo per tick.

**Files touched:**
- Create: `packages/animator/src/lafufu_animator/dxl_worker.py`
- Modify: `packages/animator/src/lafufu_animator/service.py` — wrap `self._bus` access in worker calls; close worker in `on_shutdown` (after task cancel + gather)
- Modify: `packages/animator/tests/test_service.py` — `FakeDxlBus` already works synchronously; existing tests should pass unchanged. Add a test that confirms a 200ms blocking write on the worker doesn't stall a 100Hz pose publish on the loop.

**Risks.**
- Increased per-call latency from cross-thread submission (~30-100 µs). At 150 Hz stepper rate this is negligible.
- Existing `_read_present_pose` at startup runs synchronously today; switching to worker means startup pose read becomes async. Sequence the call after the worker is up.
- The `FakeDxlBus` in tests is sync. The worker wrapping makes everything async at the call site, so existing tests using `_bus.write()` directly still work — only the service-layer calls change.

**Effort:** ~1 day. The worker is small; most of the work is the audit of every `self._bus` call site (about 12 in service.py).

### P1.2 — Agent `run_one_cycle` exception handler (C6, Theme 3)

**The bug.** `packages/agent/src/lafufu_agent/pipeline.py:run_one_cycle` has no top-level `try/except`. If `ollama.chat()` raises, state was last set to `"thinking"` and is never reset. The admin UI shows `thinking` forever until something else moves it.

**Design.** Wrap the body of `run_one_cycle` in try/except. On exception: publish `degraded` state + `SystemError` event + reset to `idle`. The existing `_mic_loop` catch already exists at `service.py:704` but it's a service-level safety net; the per-cycle handler is what surfaces the failure to the operator immediately.

```python
async def run_one_cycle(self, ...) -> None:
    try:
        # ... existing body ...
    except Exception as e:
        self.log.exception("run_one_cycle.failed")
        await self._publish_state("degraded")
        await self._publish_error("agent.cycle.failed", str(e))
        # Force-reset to idle so the next mic onset can fire
        await self._publish_state("idle")
        raise  # Re-raise so _mic_loop's safety net logs the cycle count
```

Add `_publish_error()` helper if it doesn't exist — publishes `system.error` with service name + error code + message.

**Files touched:**
- Modify: `packages/agent/src/lafufu_agent/pipeline.py` — wrap `run_one_cycle`
- Maybe modify: `packages/agent/src/lafufu_agent/service.py` — add `_publish_error` helper
- Modify: `packages/agent/tests/test_pipeline.py` — test that an `ollama.chat` raise leaves state at `idle`, not `thinking`

**Effort:** ~2 hours.

### P1.3 — Graceful shutdown gather-and-await pattern (Theme 4)

**The bug.** Every service's `on_shutdown` does `for t in tasks: t.cancel()` with no `await asyncio.gather(*tasks, return_exceptions=True)`. The cancelled task may still be inside `bus.write()` when `close()` fires on the same port — corrupts the serial stream, leaves servos torque-enabled. Animator never calls `DxlBus.close()` at all.

**Design.** Universal pattern:

```python
async def on_shutdown(self) -> None:
    self._shutdown.set()
    tasks = [t for t in (self._task_a, self._task_b, ...) if t]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    # NOW it's safe to tear down devices
    with contextlib.suppress(Exception):
        self._bus.disable_torque()
    await self._worker.close()  # the new DxlWorker from P1.1
```

Apply to all three services: agent, animator, control.

**Files touched:**
- Modify: `packages/animator/src/lafufu_animator/service.py:on_shutdown`
- Modify: `packages/agent/src/lafufu_agent/service.py:on_shutdown`
- Modify: `packages/control/src/lafufu_control/service.py:on_shutdown` (if it has background tasks)

Tests already exist for the lifecycle — should keep passing as long as the order is correct (cancel → await → close devices).

**Effort:** ~1 hour. Pure mechanical change.

### P1.4 — SQLite `busy_timeout` (C9 remainder)

**The bug.** Concurrent writes (settings PUT from admin + chat persist from agent) can race; whichever loses gets a `database is locked` 500. The chat persist path has been moved off the event loop (resolved part of C9), but no `busy_timeout` means concurrent contention still surfaces.

**Design.** One line in `db.py:create_engine_for_path`:

```python
engine = create_engine(...)
@event.listens_for(engine, "connect")
def _set_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA busy_timeout = 5000")  # 5 s
    cur.execute("PRAGMA journal_mode = WAL")   # already set elsewhere; idempotent
    cur.close()
```

**Files touched:**
- Modify: `packages/control/src/lafufu_control/db.py`

**Tests:** add a test that two concurrent writes to the same key succeed (one waits up to 5 s).

**Effort:** ~30 minutes.

---

## Priority 2 — Reliability + observability

These items don't block field testing, but they make failure modes diagnosable and recoverable.

### P2.1 — NATS lifecycle callbacks (Theme 3)

**The gap.** `nats_helper.connect_with_retry` doesn't wire `disconnected_cb` / `reconnected_cb` / `closed_cb` / `error_cb`. A dead bus link looks alive; the connection-loss class of failures is invisible.

**Design.** Add the four callbacks in `packages/shared/src/lafufu_shared/nats_helper.py`, each logging at warning/info level. Optionally publish `system.nats.{disconnected,reconnected,closed}` events so the admin UI can surface bus health.

```python
async def connect_with_retry(url: str, name: str, ...) -> nats.NATS:
    async def disconnected_cb():
        log.warning("nats.disconnected service=%s", name)
    async def reconnected_cb():
        log.info("nats.reconnected service=%s url=%s", name, url)
    # ...
    return await nats.connect(
        url, name=name,
        disconnected_cb=disconnected_cb,
        reconnected_cb=reconnected_cb,
        closed_cb=closed_cb,
        error_cb=error_cb,
        max_reconnect_attempts=-1,
    )
```

**Files touched:**
- Modify: `packages/shared/src/lafufu_shared/nats_helper.py`

**Effort:** ~1 hour. No new tests required — the existing connect-with-retry test exercises the path.

### P2.2 — DXL reconnect path (Theme 3 / Theme 7)

**The gap.** `_has_u2d2` flips to `False` on any bus error and is never reset. A transient USB hiccup (cable bumped, hub disconnect) permanently bricks the head until full service restart.

**Design.** Add a background reconnect task that, when `_has_u2d2` is False, periodically attempts to re-open the bus and re-configure. On success, set `_has_u2d2 = True` and publish state transition `degraded` → `idle`.

```python
async def _dxl_reconnect_loop(self) -> None:
    while not self._shutdown.is_set():
        await asyncio.sleep(5)
        if self._has_u2d2:
            continue
        try:
            self._bus.open()
            self._bus.configure_limits()
            self._bus.enable_torque()
            self._has_u2d2 = True
            await self._publish_state("idle")
            self.log.info("dxl.reconnected")
        except Exception as e:
            self.log.debug("dxl.reconnect.attempt_failed error=%s", e)
```

The existing dead helper `_safe_apply_immediate` (called out as dead in the May 20 review) may be the right home for the reconnect-and-retry call site. Verify and resurrect.

**Files touched:**
- Modify: `packages/animator/src/lafufu_animator/service.py` — new `_dxl_reconnect_loop`, started from `on_startup`, cancelled in `on_shutdown`

**Tests:** simulate a USB disconnect via `FakeDxlBus.disconnect()` (already exists), confirm the loop reconnects after `_bus.reconnect()` is called.

**Effort:** ~3 hours. Coordinate with P1.1 (worker thread).

### P2.3 — `/healthz` + `WatchdogSec` (Theme 9)

**The gap.** No HTTP healthcheck. systemd has 5s heartbeat but no `WatchdogSec`, so a hung-but-not-crashed process is never killed.

**Design.** Two parts:

**(a) `/healthz` endpoint** in control. Returns `200 {"ok": true, "services": {...}}` based on the heartbeat snapshot the control service already tracks. If any subordinate service hasn't heartbeat'd in 30 s, return `503`.

```python
@app.get("/healthz")
def healthz(req: Request):
    snapshot = req.app.state.service_status
    now = time.time()
    stale = {
        name: row for name, row in snapshot.items()
        if row.get("last_seen") and now - row["last_seen"] > 30
    }
    if stale:
        return JSONResponse({"ok": False, "stale": list(stale)}, status_code=503)
    return {"ok": True, "services": list(snapshot)}
```

**(b) `WatchdogSec` in systemd units** + `sd_notify("WATCHDOG=1")` calls from each service's heartbeat publish. Pattern (only for services that already publish heartbeats):

```python
import sdnotify
notifier = sdnotify.SystemdNotifier()
notifier.notify("WATCHDOG=1")
```

Adds the `sdnotify` Python dep. Set `WatchdogSec=15s` in each `.service` unit. systemd kills + restarts the process if it doesn't notify within the window.

**Files touched:**
- Modify: `packages/control/src/lafufu_control/api/routers/system.py` (or new `health.py`)
- Modify: All three `deploy/systemd/lafufu-*.service` — add `WatchdogSec=15s`
- Modify: `packages/shared/src/lafufu_shared/base_service.py` — wire `sd_notify` into the existing heartbeat publisher

**Effort:** ~2-3 hours. The sd_notify integration is the riskiest part — needs Linux-only testing.

---

## Priority 3 — Small surgical fixes

Each of these is < 1 hour. Worth batching into a single PR.

### P3.1 — Printer `lp` argument sanitation (C8, Theme 6)

**The bug.** `packages/printer/src/lafufu_printer/service.py:133-134` appends `self.lp_options.split()` to the `lp` argv. `lp_options` flows from a NATS config publish — an attacker who can publish `config.changed.printer.lp_options` can pass arbitrary `lp` flags.

**Design.** Allow-list at the boundary:

```python
_ALLOWED_LP_OPTIONS = frozenset({
    "-o", "media", "fit-to-page", "page-bottom", "page-top",
    "page-left", "page-right", "scaling", "orientation-requested",
    # … the actual set the operator ever uses …
})

def _sanitize_lp_options(raw: str) -> list[str]:
    parts = raw.split()
    out: list[str] = []
    for p in parts:
        # Split "key=value" on first = for allow-list lookup
        key = p.split("=", 1)[0]
        if key not in _ALLOWED_LP_OPTIONS:
            log.warning("printer.lp_options.rejected token=%r", p)
            continue
        out.append(p)
    return out
```

Apply the same to `media` (just validate against `_MEDIA_INCHES` keys).

**Files:** `packages/printer/src/lafufu_printer/service.py`

### P3.2 — `install.sh` sudoers install (C10)

**The bug.** `deploy/install.sh` never copies the sudoers fragment, so the admin "restart service" button 500s on a fresh install.

**Design.** Add one block to `install.sh`:

```bash
install -m 0440 -o root -g root \
    "$REPO/deploy/sudoers/lafufu-services" \
    /etc/sudoers.d/lafufu-services
visudo -c -f /etc/sudoers.d/lafufu-services  # syntax check
```

**Files:** `deploy/install.sh`. Also delete the misleading "installed by install.sh" comment header from the sudoers file itself if the install changes anything substantive — keep it accurate.

### P3.3 — `StartLimitBurst` section header (C11)

**The bug.** `lafufu-agent.service` and `lafufu-animator.service` put `StartLimitBurst` / `StartLimitIntervalSec` under `[Service]`. On modern systemd these need to be in `[Unit]` — silently inert otherwise.

**Design.** Move both directives:

```ini
[Unit]
Description=…
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
…
```

**Files:** both `.service` units.

### P3.4 — Three.js context-loss handler for `/face` (C12 remainder)

**The gap.** `/pet` no longer uses Three.js (pet_scene.ts deleted). `/face` still does and has no `webglcontextlost` listener — losing the context (browser tab backgrounding, GPU reset) leaves the render loop spinning against a dead context, pinning CPU.

**Design.** In `web/src/face/face.tsx`, attach listeners at scene setup:

```ts
canvas.addEventListener("webglcontextlost", (e) => {
  e.preventDefault();  // signals we want to restore
  console.warn("WebGL context lost — halting render loop");
  renderLoopHalted = true;
});
canvas.addEventListener("webglcontextrestored", () => {
  console.info("WebGL context restored — resuming render");
  rebuildSceneResources();  // re-upload geometry, textures
  renderLoopHalted = false;
});
```

**Files:** `web/src/face/face.tsx` (or wherever the Three.js setup is).

### P3.5 — WS bridge ref-count race (Theme 5)

**The bug.** `packages/control/src/lafufu_control/api/ws_bridge.py:_add_sub` / `_remove_sub` ref-counting has a check-then-act race across an `await`. Leaks NATS subs.

**Design.** Wrap the entire `_add_sub`/`_remove_sub` body in a shared `asyncio.Lock`:

```python
class WsBridge:
    def __init__(self, ...):
        self._ref_lock = asyncio.Lock()

    async def _add_sub(self, pattern: str) -> None:
        async with self._ref_lock:
            # ... existing check + (await) subscribe + increment ...

    async def _remove_sub(self, pattern: str) -> None:
        async with self._ref_lock:
            # ... existing decrement + (await) unsubscribe ...
```

**Files:** `packages/control/src/lafufu_control/api/ws_bridge.py`

### P3.6 — Printer job lock (Theme 5)

**The bug.** Two concurrent print intents both flip state to `printing` and interleave `lp` calls. The printer is a single physical resource with no lock.

**Design.** `asyncio.Lock()` on the `PrinterService`, acquired for the duration of the `_print_one` call. If a second intent arrives mid-print, queue it (FIFO) or reject (depending on user preference). Reject is simpler:

```python
async def _on_print(self, msg):
    if self._job_lock.locked():
        self.log.warning("printer.busy — dropping intent")
        await self._publish_state("busy")
        return
    async with self._job_lock:
        await self._print_one(...)
```

**Files:** `packages/printer/src/lafufu_printer/service.py`

### P3.7 — `ConfigChanged.value: Any` typing (Theme 6)

**The bug.** `packages/shared/src/lafufu_shared/schemas.py` defines `ConfigChanged.value: Any`. Every consumer re-parses defensively. Out-of-range values reach application code.

**Design.** Add bounded variants per setting *family* — `ConfigChanged.value: str | int | float | bool` (a union, not `Any`). Tighter than `Any`, looser than schema-per-key. Consumers still defensively cast but the network-edge type rejects obvious garbage (lists, dicts).

**Files:** `packages/shared/src/lafufu_shared/schemas.py`

### P3.8 — Server-side servo position clamping (Theme 6)

**The bug.** `web/src/pet/head_drag.ts` clamps before sending; `ws_bridge.py` and animator trust the value. A misbehaving client (or a curl with a bad request) can drive servos past safe limits.

**Design.** In `packages/control/src/lafufu_control/api/routers/animator.py:preview` (and `set_pose`), clamp `body.position` against `_pose.CLAMP[body.name]` before publishing the intent.

**Files:** `packages/control/src/lafufu_control/api/routers/animator.py`

### P3.9 — Dead code & doc cleanup (Theme 10)

- Delete `packages/agent/src/lafufu_agent/vad.py` if no production path uses it (verify via grep).
- Delete `get_session` in `packages/control/src/lafufu_control/db.py` if no production call site exists.
- Fix the sudoers file's header comment claiming "installed by install.sh" — either update install.sh (P3.2) or change the comment.
- Remove dead `Behavior` / `Plugin` SQLModel tables if their routers were never wired up (verify).

**Files:** various.

---

## Suggested implementation order

1. **P1.4 (busy_timeout)** — 30 minutes, immediate stability win.
2. **P1.2 (agent run_one_cycle handler)** — 2 hours, surfaces invisible LLM failures.
3. **P3.1, P3.2, P3.3, P3.5, P3.6, P3.8 (small surgical batch)** — 1 PR, ~3 hours total. Closes 6 May-20 findings in one merge.
4. **P1.3 (graceful shutdown gather-and-await)** — 1 hour, pure mechanical.
5. **P2.1 (NATS lifecycle)** — 1 hour, plus observability.
6. **P1.1 (DXL worker)** — 1 day. Biggest, most impactful. Should land before P2.2 (DXL reconnect) since they share scaffolding.
7. **P2.2 (DXL reconnect)** — 3 hours, requires P1.1's worker.
8. **P2.3 (/healthz + WatchdogSec)** — 2-3 hours, Linux-side testing required.
9. **P3.4 (Three.js context-loss)** — 1 hour, browser-side testing.
10. **P3.7, P3.9 (typing + cleanup)** — final sweep.

Total: ~2-3 days of focused work, spread across ~5 PRs. Each step ships independently; later steps don't depend on earlier ones structurally (except P2.2 needing P1.1).

## Items deliberately deferred

- **Auth** (Theme 1, C1 default-open posture, C2 WS pattern allow-list). Will need design when the deployment context changes.
- **Alembic migration framework** (Theme 8). The additive-column pattern PR #20 introduced is sufficient until we need a non-additive change.
- **Structured logging** (Theme 9). printf-style strings stay.
- **`_AplayPlayer` zombie-process leak** (May 20 review §4.1 high). The `aplay` process is short-lived; current behaviour produces `<defunct>` entries but doesn't fill the table at any realistic rate. Address if/when it becomes an actual symptom.
- **`_VALID_EMOTIONS` etc. — already resolved by PR #20.** Listed for closure.

## Testing strategy

Each item ships with its own test:
- **P1.x:** unit + integration tests for the new behaviour, asserting both the happy path and the failure path.
- **P2.x:** integration tests against fake hardware (`FakeDxlBus.disconnect()` for P2.2, fake nats client for P2.1).
- **P3.x:** small targeted tests for each surgical fix.

After each PR, run `uv run pytest` from workspace root (per `~/.claude/projects/.../memory/feedback_pytest_workspace_root.md`) before declaring done.

## Risks & open questions

- **P1.1 (DXL worker) — performance.** Cross-thread submission overhead at 150 Hz. Should benchmark on the Pi before assuming it's fine. If latency creeps above ~5 ms per write, the worker needs to batch writes per tick rather than one-per-call.
- **P2.3 (sd_notify) — Linux only.** Local development on Windows won't exercise the watchdog path; CI will only run it if we add a Linux integration job.
- **P3.6 (printer job lock) — reject vs queue.** Reject is simpler; queue is friendlier UX. If the admin UI doesn't surface "printer busy" clearly, the user might think the second print failed silently. Pick once we see real usage.
- **The May 20 review itself is the ground truth.** Anything not flagged in that review may still be a real bug — this spec only addresses items the review catalogued.
