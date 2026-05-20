# Lafufu — Production-Readiness Code Review

**Date:** 2026-05-20
**Branch reviewed:** `lafufu-drag-controls`
**Scope:** Entire monorepo — 5 Python packages (`agent`, `animator`, `control`, `printer`, `shared`), the SolidJS web SPA, and all deployment/infra/CI.
**Method:** Six parallel domain reviewers performing static review of every source and test file, plus central verification (test suite, lint, typecheck) run by the coordinator.

---

## 1. Verdict

**Not production-ready.** The codebase is unusually well-structured and well-commented for a "vibe coded" project — clean module boundaries, thoughtful test doubles, defensive instincts in many places. But it has a **systemic security gap** (nothing is authenticated, anywhere) and a cluster of **resilience/concurrency defects** that will cause the robot to hang, crash-loop, or get stuck in a wrong state under entirely ordinary failure conditions (NATS blip, USB hiccup, LLM timeout, mic unplugged).

The good news: the problems are concentrated in a small number of **recurring themes**. Fix the themes and most of the individual findings collapse together. This is roughly **2–4 weeks of focused hardening**, not a rewrite.

### Verification snapshot (run by coordinator, 2026-05-20)

| Check | Result |
|---|---|
| `uv run pytest` | ✅ **154 passed**, 6 warnings, 31s |
| `uv run ruff check .` | ✅ All checks passed |
| `web` → `tsc --noEmit` | ✅ 0 errors |
| `web` → `vitest run` | ✅ **18 passed** (3 files) |

Tooling is green — but the suite tests almost exclusively **happy paths**. Every domain reviewer independently flagged that **failure and recovery paths are essentially untested**. Green CI here is not evidence of production readiness.

### Severity tally (across all domains)

| Severity | Count (approx.) | Meaning |
|---|---|---|
| 🔴 Critical | ~20 (≈8 distinct root causes) | Ship-blockers |
| 🟠 High | ~35 | Fix before any real deployment |
| 🟡 Medium | ~40 | Fix before scaling / hardening pass |
| 🟢 Low / nits | ~45 | Cleanup, do opportunistically |

---

## 2. Cross-cutting themes — read this first

Individual findings are listed per-domain in §4. But the same root causes recur everywhere. Fixing these themes is the actual production-readiness work.

### Theme 1 — 🔴 Nothing is authenticated. Anywhere.
The single biggest issue, found independently by **four** reviewers (control, web, shared, infra).

- The control HTTP API binds `0.0.0.0:8080` with **no auth, no CORS policy, no CSRF protection, no rate limiting**. Every endpoint is open: restart systemd services (via `sudo`), rewrite settings, upload files, drive servos, print.
- The NATS bus (`nats-server.production.conf`) binds **all interfaces** with **no authorization, no per-subject permissions, no TLS**. The "production" config is byte-identical to dev except store paths. Anyone on the LAN can `subscribe(">")` and read every transcript, or publish forged `*.intent.*` messages to move the hardware.
- The `/ws` bridge forwards **browser-supplied NATS subscribe patterns straight onto the bus with no allow-list** — a browser can subscribe to `>` and exfiltrate the entire bus.
- The web SPA has no login; `/admin` is not gated.

**Impact:** Any device on the same network (the Pi's own hotspot included) has full unauthenticated remote control of the robot. **This must be fixed before the device is exposed to any network you do not fully control.**

**Fix direction:** bind control to `127.0.0.1` + NATS to `127.0.0.1`; add a token/session auth dependency on all `/api` and `/ws` routes; allow-list WS subscribe patterns; gate `/admin` client-side as defense-in-depth. If multi-host is ever needed, add NATS accounts + per-subject permissions + TLS.

### Theme 2 — 🔴 Blocking I/O on the asyncio event loop
Multiple services do synchronous I/O directly on the single event-loop thread:
- **Animator:** every Dynamixel serial round-trip (`dxl_bus.py`) is blocking USB I/O, called from 30 Hz animation loops and every NATS handler. One slow/timing-out servo freezes heartbeats, lipsync, NATS draining — the whole service.
- **Control:** `_rebroadcast_all_settings` runs synchronous SQLite reads in an `async def` on the loop.
- **Agent:** `_set_alsa_volume` runs `subprocess.run` inside an async config handler.

**Fix direction:** push all blocking I/O to `asyncio.to_thread` / `run_in_executor`, or a dedicated worker thread with a command queue (the right model for the DXL bus).

### Theme 3 — 🔴 No fault recovery — services hang, crash-loop, or get stuck
The system has graceful-*degradation* concepts but the *recovery* half is missing:
- **Agent:** any LLM/STT/TTS exception strands the agent on `thinking`/`speaking` forever — the defined `degraded` state is never used. A mic unplugged mid-session crash-loops forever because the dead stream handle is cached and never re-probed.
- **Animator:** once the DXL bus errors, `_has_u2d2` flips to `False` and **nothing ever sets it back** — a transient USB hiccup permanently bricks the head until a full restart. The recovery helper `_safe_apply_immediate` exists but is dead code, never called.
- **Shared:** NATS connection loss is invisible — no `disconnected_cb`/`reconnected_cb`/`closed_cb`/`error_cb` wired; a dead bus link looks "alive."
- **Web:** WebSocket reconnects forever but never surfaces "offline" to the user; no WebGL context-loss handling.

**Fix direction:** wrap each work cycle so exceptions reset state to `idle`/`degraded` and emit a `SystemError`; add periodic reconnect for the DXL bus; wire NATS lifecycle callbacks; surface connection state in the UI.

### Theme 4 — 🟠 Graceful shutdown is broken in three services
`agent`, `animator`, and `control` all **cancel background tasks without awaiting them**. The cancelled task may still be inside a blocking executor call (mic read, serial write) — then the service closes the device/port out from under the still-running thread. This races and can corrupt the serial stream, leave servos torque-enabled, or segfault PortAudio. The animator never calls `DxlBus.close()` at all — the USB serial handle leaks on every shutdown.

**Fix direction:** standard pattern everywhere — signal stop, `await asyncio.gather(*tasks, return_exceptions=True)`, *then* close devices.

### Theme 5 — 🟠 Concurrency races
- **Control WS bridge:** `_add_sub`/`_remove_sub` ref-counting has a check-then-act race across an `await` (leaks NATS subs, corrupts the count); concurrent `_fanout` coroutines call `send_json` on the *same* WebSocket (Starlette `send` is not concurrency-safe).
- **Agent:** `_on_config_auto_listen` can spawn a second mic loop while the first executor thread is still reading the same PyAudio stream.
- **Printer:** two concurrent print intents both flip state to `printing` and interleave `lp` calls — the printer is a single physical resource with no lock.
- **Control:** `_snapshot_pending` debounce flag is fragile across `await` points.

**Fix direction:** per-connection single-writer outbound queue for WS; `asyncio.Lock` around ref-counting and printer jobs; await mic-loop teardown before restart.

### Theme 6 — 🟠 Input validation gaps at trust boundaries
- `ConfigChanged.value` is typed `Any` — the central config-propagation message has zero type safety; every consumer re-parses defensively and out-of-range values (`rotate=999`, `scale_pct=-50`) reach application code.
- Printer `lp_options`/`media`/`adjust_*` flow from NATS config into the `lp` subprocess argv with no allow-list — an attacker who can publish config can pass arbitrary `lp` flags.
- The web app dispatches inbound NATS frames as `payload: any` with no validation — a malformed frame can break the dispatch loop for all topics.
- Servo drag commands are clamped **only client-side**; the backend must clamp server-side — a client clamp cannot be trusted for hardware safety.

### Theme 7 — 🔴/🟠 Hardware safety (animator)
Position clamping is the *only* safety property and it is solid. But there is **no torque / velocity / acceleration / current limiting** — the Dynamixel hardware control-table limits (Profile Velocity/Acceleration, Velocity Limit, PWM Limit, Min/Max Position) are never written. A large target jump slews the servo at maximum speed — mechanically violent, capable of stalling/stripping gears. And because comm results are never checked, a servo in an **overload/overheat error-latch is invisible** — the code keeps commanding it, sustaining the stall.

### Theme 8 — 🟠 No schema versioning, no DB migrations
- No message schema carries a protocol/version field — rolling upgrades across the NATS bus have no compatibility contract.
- The control DB is created via `SQLModel.metadata.create_all` only — it **never alters existing tables**. Any future column change silently fails to apply to a deployed Pi. No Alembic, no versioning.

### Theme 9 — 🟠 Observability gaps
- "JSON structured logging" stops at the envelope — every log message is a printf-formatted string, not structured key/values.
- No metrics, no `/healthz` endpoint, no systemd `WatchdogSec` (despite a 5s heartbeat already existing — a hung-but-not-crashed process is never restarted).
- NATS connection state is never logged.

### Theme 10 — 🟢 Dead code & doc drift
`vad.py` (unused production-path), `_safe_apply_immediate`, `get_session`, `server_now`, `types.gen.ts` (generated but never imported), the `Behavior`/`Expression`/`Plugin` DB tables (migrated, no routers), Tailwind color config (unused). README points at a non-existent `scripts/dev_run_all.sh`. The sudoers file's header says "installed by install.sh" — `install.sh` never installs it.

---

## 3. Consolidated 🔴 Critical blockers

Deduplicated across domains, in fix-priority order:

| # | Blocker | Domains | Fix theme |
|---|---|---|---|
| C1 | No authentication on control API, NATS bus, or WS bridge; binds `0.0.0.0` | control, web, shared, infra | Theme 1 |
| C2 | WS bridge forwards untrusted browser subscribe patterns (`>`) onto the bus | control | Theme 1 |
| C3 | Blocking DXL serial I/O freezes the animator event loop | animator | Theme 2 |
| C4 | DXL writes ignore comm-result/error byte — dead/stalled servo is invisible | animator | Theme 7 |
| C5 | No servo torque/velocity/accel limiting — full-speed slews, sustained stalls | animator | Theme 7 |
| C6 | Agent LLM/STT/TTS failure strands state machine; no recovery | agent | Theme 3 |
| C7 | Agent hardware/model init crash exits with no NATS error, crash-loops | agent | Theme 3 |
| C8 | Printer path allowlist contradicts its docstrings; `lp` argument injection from NATS config | printer | Theme 6 |
| C9 | Blocking SQLite I/O in request path; no `busy_timeout` → `database is locked` 500s | control | Theme 2 |
| C10 | sudoers file never installed by `install.sh` → restart button 500s; doc lies | infra | Theme 10 |
| C11 | systemd crash-loop limiter (`StartLimitBurst`) in wrong section → silently inert | infra | — |
| C12 | No WebGL context-loss handling → dead-context render spam pins CPU | web | Theme 3 |

---

## 4. Per-domain detailed findings

The six domain reports follow verbatim. Use these as the working reference; `file:line` citations are precise.

---

### 4.1 Voice Agent (`packages/agent`)

**Summary:** Reasonably well-structured — clean Protocol-based decoupling, thoughtful streaming/queue logic in `pipeline.speak`, sensible STT/LLM fallbacks, decent happy-path tests. **Not production-ready.** Biggest gaps are error resilience: a single LLM/STT/TTS exception publishes `system.error` and can crash-loop the service, the state machine gets stuck on `thinking`/`speaking` when a backend fails, and the defined `degraded` state is never used. Concrete bugs in `pipeline.speak` cancellation, an `_AplayPlayer` zombie-process leak, no timeout/validation on blocking mic calls. Failure paths essentially untested.

#### 🔴 Critical
- **[pipeline.py:75-82]** LLM/TTS failures escape `run_one_cycle` and leave the agent stuck mid-state. If `ollama.chat()` raises, the exception propagates; state was last set to `"thinking"` and is never reset. Mic loop logs+sleeps 1s but state stays `thinking` forever in the admin UI. Text-intent path has it worse (no try/except at all). Fix: wrap `run_one_cycle` body in try/except that publishes `degraded`/`idle` + a `SystemError`.
- **[service.py:328-341]** `_on_text_message` has no error handling — relies on `subscribe_model` swallowing. Operator gets no feedback, agent stuck on `thinking`, no observability the request failed.
- **[pipeline.py:142,144]** `asyncio.run_coroutine_threadsafe(...).result()` in the producer thread can deadlock or raise on shutdown. If the loop is closing, `run_coroutine_threadsafe` raises `RuntimeError` in the thread, the `finally: queue.put(None)` also raises, and `await producer_fut` surfaces it. No test for loop-shutdown-during-synth. Fix: catch `RuntimeError`; treat a closed loop as "cancelled."
- **[__main__.py:287-315 / service.py]** No top-level fault tolerance for hardware/model init. Missing Piper `.onnx`, Whisper load failure, or no input device throws before `svc.run()` — bare traceback, nothing on NATS, invisible systemd crash-loop. Fix: wrap init, publish `degraded`/`SystemError` before exit, or start degraded.

#### 🟠 High
- **[__main__.py:269-284]** `_AplayPlayer` leaks zombie `aplay` processes — `end()` never `wait()`s; over hundreds of utterances the process table fills with `<defunct>`. No `close()` either.
- **[__main__.py:112-157]** `wait_for_onset`/`record_until_silence` have no exception handling around `stream.read()`. A mic unplugged raises `OSError`; `_ensure_stream` cached the dead stream, so every retry fails forever. Fix: on `OSError`, call `self.close()` to re-probe.
- **[pipeline.py:97-108]** No timeout/retry on `ollama.chat()` beyond httpx's 60s — a 60s hang blocks `_cycle_lock`, no spoken fallback. Fix: ~25-30s timeout, one retry, fallback line.
- **[stt.py:90-102,130-142]** STT `transcribe` has no error handling, no audio-length guard; Whisper hallucination not caught by the trivial-transcript guard.
- **[service.py:296-308]** `_on_config_auto_listen` cancels the mic-loop task without awaiting — can spawn a second mic loop while the first executor thread still reads the same PyAudio stream (undefined behavior).
- **[service.py:315-323]** `on_shutdown` cancels the mic-loop task without awaiting, then closes the stream under the still-running executor thread — races, can segfault PortAudio.

#### 🟡 Medium
- **[pipeline.py:38-47]** `MicProtocol` declares only `listen_once` but the real path uses `wait_for_onset`/`record_until_silence` (`hasattr`-sniffed) — misleading contract.
- **[service.py:331-340,392-398]** Throwaway `_OnceMic` class defined inside the handler on every call, duplicated three places.
- **[__main__.py:263-267]** `_AplayPlayer.play` swallows `BrokenPipeError` silently — audio lost with zero logging.
- **[pipeline.py:121]** Final partial chunk still sleeps a full `chunk_dt`; RMS `ts` drift.
- **[service.py:200-210]** `_set_alsa_volume` runs `subprocess.run` synchronously inside an async handler — blocks the loop on every volume drag.
- **[__main__.py:75-89]** Device sample-rate fallback can pick a Whisper-incompatible rate; no sanity floor.
- **[emotion_parser.py:13-20]** Regexes only match `[a-zA-Z]+` — `[very happy]` leaks the literal brackets into TTS.

#### 🟢 Low
- Function-local imports of hard deps (`collections`, `numpy`, `emotion_parser.parse`); inconsistent `make_stt` defaults (`tiny` vs `tiny.en`); magic-string `getattr` defaults duplicated; **`vad.py` is effectively dead code** (only exercised by `test_vad.py`); scattered `# type: ignore` that should be fixed types.

#### Security notes
- Prompt injection is unmitigated by design — transcribed speech goes straight into the LLM. Largely acceptable for a kiosk (LLM output only drives TTS/emotion), **except** the IP intent reads aloud and **prints** the LAN IP + admin URL to any bystander who says "what's your IP" — consider gating it.
- Text intents (`SPEAK_TEXT`) are full voice puppet control with no auth — a NATS-layer concern (Theme 1).
- No secrets in this package (all backends local). Subprocess argv built as lists (no shell) — safe from injection.

#### Test coverage gaps
No failure-path tests for LLM (timeout/refused/malformed), STT (raising/hallucinating/load failure), or mic (`OSError`, unplugged). `RealMic`/`wait_for_onset`/`record_until_silence` have **zero** unit tests. No `_AplayPlayer` recovery/zombie tests. No config-handler bad-value tests. No graceful-shutdown test. No test that a crashing cycle resets state (it currently doesn't).

#### Strengths
Clean Protocol/duck-typed decoupling; `pipeline.speak` streaming (bounded queue, producer cancellation, drain) is genuinely thoughtful and well-tested; blocking calls correctly pushed to executors (with a test proving it); `make_stt` fallback chain; config-snapshot-on-startup; ALSA stderr suppression, pre-roll buffering, split-lock design; good structured `key=value` logging style.

#### Top recommendations
1. Wrap `run_one_cycle` + each intent handler in error handling that resets state and emits `SystemError`; use the `degraded` state.
2. Add resilience to `Ollama.chat` — shorter timeout, one retry, spoken fallback; same try/except around `stt.transcribe`.
3. On mic `OSError`, call `RealMic.close()` so the next cycle re-probes.
4. Fix mic-loop cancellation races — signal stop, await the task before closing the stream.
5. Reap `aplay` subprocesses; add `close()`.
6. Harden `pipeline.speak` for loop shutdown (catch `RuntimeError`).
7. Add failure-path tests + a test asserting state returns to `idle`/`degraded` after a cycle crash.

---

### 4.2 Animator / Servo Control (`packages/animator`)

**Summary:** Reasonably structured — pure pose math cleanly separated, clamping applied consistently, stepper-loop easing sound. But it carries a **critical hardware-safety gap**: blocking serial I/O directly inside async loops, zero comm-result checking (a dead servo is silently ignored), and no torque/velocity/profile limiting. Several resilience holes and protocol mismatches. **Not production-ready.**

#### 🔴 Critical
- **[dxl_bus.py:79-94 / service.py:257-491]** Blocking serial I/O inside the event loop — `dynamixel_sdk` synchronous `writeNByteTxRx`/`read4ByteTxRx` called from `_stepper_loop`, idle/expression loops, and every NATS handler, all on the loop thread. One slow/timing-out servo stalls heartbeat, NATS draining, lipsync, everything. Fix: run all bus I/O in a dedicated thread / executor.
- **[dxl_bus.py:90-101]** `write()`/`read()` ignore the Dynamixel comm result and hardware error byte. An unplugged/overloaded/overheat-latched servo returns non-`COMM_SUCCESS` but is treated as success — `_has_u2d2` stays `True`, service reports `idle` while the head is dead. Fix: check `comm_result`/`dxl_error`, raise on failure, read/clear the error register.
- **[dxl_bus.py / service.py:79-101]** No servo safety limiting beyond position clamp — Profile Velocity/Acceleration, Velocity Limit, PWM/current limit, control-table Min/Max Position are never written. A large target jump slews at max speed — mechanically violent, gear-stripping. Software easing is the only limiter and is bypassable/killable. Fix: write conservative profile + limits on `enable_torque`.
- **[dxl_bus.py:79-81]** `enable_torque()` lacks the `self._port is None` guard the other methods have — a reconnect attempt throws an unhelpful `AttributeError` instead of clean `OSError`.
- **[service.py:15-19,95]** `DxlBusProtocol` is missing `enable_torque` — `on_startup` calls it; only saved by `FakeDxlBus` happening to implement it. Unchecked contract gap.

#### 🟠 High
- **[service.py:240-242,295-351]** No rate limiting on the drag-puppeteer path (`_on_preview`) or `_on_tts_rms` — a buggy/malicious web client can flood `animator.intent.preview` unbounded.
- **[dxl_bus.py — whole file]** No reconnect/fault-recovery path. Once `open()` fails or a write throws, `_has_u2d2` flips `False` and **nothing sets it back**. `_safe_apply_immediate` (the recovery helper) is **dead code, never called**. A transient USB hiccup permanently degrades the robot until restart.
- **[service.py:174-186]** `on_shutdown` cancels tasks without awaiting — `disable_torque()` runs concurrently with an in-flight `write()` on the same non-thread-safe port, corrupting the serial stream; torque-off may not land.
- **[service.py — no `close()`]** `DxlBus.close()` is never called — the USB serial handle leaks on every shutdown; a crash-restart may fail to reopen the port.
- **[service.py:344-351]** `_on_tts_rms` trusts `msg.ts` for `dt` — but `ts` resets to ~0 each new utterance, producing negative or huge `dt` and snapping the envelope. `_last_rms_ts` (utterance-relative) is also compared against `time.monotonic()` (wall clock) for the "is speaking" check — meaningless comparison.
- **[service.py:194-209]** Idle-override config handler fights the easing loop; no validation that the config `key` matches the `servo`.

#### 🟡 Medium
- `open()` probes only servo ID 1 to declare the whole bus alive; auto-detect probes dozens of COM ports × bauds with blocking timeouts at startup; `_stepper_loop` re-publishes `degraded` 30×/s while the bus is down; writes all 5 servos every tick even when unchanged (150 writes/s idle); broad `except Exception` hides real bugs and busy-spins; `on_startup` swallows `AttributeError`/`TypeError` from `open()`; expression amplitudes can exceed servo range (asymmetric clip / dwell against the limit); `LipsyncEnvelope.step` accepts unbounded `dt`.

#### 🟢 Low
`_safe_apply_immediate` dead code; `read()` never called; function-local `import math`/`random` (math already imported); `apply_offsets` exported+tested but unused; `# type: ignore` on `_publish_state`; `is_expired` can overrun duration by ~50ms; no explicit non-zero exit on crash; magic `comm_result == 0`; **`numpy>=1.26` declared but never imported** — drop it.

#### Security notes
Intent payloads are pydantic-validated and double-clamped — good defense in depth. `AnimatorIntentPlayExpression.name` is free-form `str` (unknown → `neutral`, no crash, but attacker-controlled text echoed in `gesture_done`). Drag-puppeteer path has no rate limiting and no auth at this layer (Theme 1). Config handlers trust the NATS subject to identify the servo — spoofable if NATS is untrusted.

#### Hardware safety notes
Position clamping is consistent and correct — the strongest safety property. **No torque/velocity/accel/current limiting** — the single biggest gap. Comm-result checking absent — overload/overheat latch invisible, code keeps commanding a stalled motor. Torque-off on shutdown exists but races un-awaited writes and may not take effect; `close()` never called. No emergency-stop/fault-trip path. `_move_to_pose` snaps servos with no easing at startup.

#### Test coverage gaps
`FakeDxlBus` simulates no comm failures, partial servo failure, error bytes, or slow I/O. The real `DxlBus` has **zero tests** — auto-detect, baud probing, comm-result handling, port guards all untested. No disconnect→reconnect service-level test. No `on_shutdown` torque/await/close test. `_on_tts_rms` decreasing-`ts` untested. `_lipsync_watchdog` untested.

#### Top recommendations
1. Move all DXL serial I/O off the event loop (worker thread + command queue).
2. Check comm-result + hardware error byte on every I/O; raise on failure; read/clear overload/overheat latches.
3. Configure hardware safety limits on torque-enable (Profile Velocity/Accel, Velocity Limit, Min/Max Position, PWM/current).
4. Add a bus reconnect path; wire up or delete `_safe_apply_immediate`.
5. Fix shutdown: await cancelled tasks, then `disable_torque()` + `close()`; add `enable_torque`/`close` to the Protocol.
6. Fix the `ts`/`dt` clock confusion in `_on_tts_rms`.
7. Reduce bus spam (state on transition only, skip unchanged writes); add real-bus + reconnect tests.

---

### 4.3 Control API & WebSocket Bridge (`packages/control`)

**Summary:** Cleanly structured FastAPI control plane with thoughtful touches (atomic writes, path-traversal guards, ref-counted WS subs, snapshot coalescing). **Not production-ready.** Zero authentication on a network-facing surface that restarts systemd units via `sudo`, mutates config, uploads files, and injects onto the NATS bus. No CORS, no rate limiting, no health check, no DB migrations, and several real concurrency bugs.

#### 🔴 Critical
- **[api/app.py:20-51]** No authentication or authorization on any endpoint — service restart (`sudo`), settings mutation, uploads, intents all open. Full remote control for anyone who can reach host:port. `TODO(auth)` comments at printer.py:244/353 acknowledge it but it applies to the whole surface.
- **[api/app.py:21]** No CORS policy and no CSRF protection — combined with no auth, state-changing POSTs are reachable from any web origin via form submission.
- **[ws_bridge.py:51-56,89]** WS bridge forwards untrusted browser topic patterns directly into `nats.subscribe` — a browser can subscribe to `>` and exfiltrate the whole bus; no cap on patterns per connection.
- **[routers + service.py:65-68]** Every router lets an unauthenticated client publish onto fixed NATS subjects; `config.changed.<key>` lets a client set *any* setting to *any* value — config poisoning across services.
- **[settings.py / snapshot.py / db.py]** Synchronous SQLite with `check_same_thread=False`, no `busy_timeout`, no connect `timeout` — concurrent writes raise `database is locked` → 500s under any real concurrency.

#### 🟠 High
- **[service.py:143-159]** `_snapshot_pending` plain-bool debounce is fragile across `await` points.
- **[service.py:170-185]** `_rebroadcast_all_settings` runs synchronous `Session`/`exec` on the event loop — stalls the loop.
- **[ws_bridge.py:114-133]** Fan-out write race — sequential `send_json` means one slow client blocks all others; two concurrent `_fanout` coroutines interleave `send_json` on the *same* WebSocket (Starlette `send` is not concurrency-safe → corrupted frames).
- **[ws_bridge.py:92-112]** `_remove_sub` fires `unsubscribe()` as untracked `create_task` — task leak, swallowed errors, possible duplicate subscriptions.
- **[ws_bridge.py:77-90]** `_add_sub` has a check-then-act race across `await nats.subscribe` — two concurrent `sub` frames leak a subscription and corrupt the ref-count.
- **[db.py:8-29]** No migrations — `create_all` never alters existing tables; any future column change silently fails on a deployed Pi. No Alembic.
- **[system.py:29-33]** Restarting `control` itself restarts the process serving the request — undefined state. No special-casing.
- **[printer.py:431-479]** `test_print` writes the calibration grid to a fixed shared path with `img.save` directly (not the atomic helper) — concurrent requests race, readers see a half-written PNG.

#### 🟡 Medium
- `PUT` accepts arbitrary `key`/`value_type` with no validation — unbounded junk settings rows, `value_type:"banana"` accepted; `config.changed` publish is fire-and-forget (DB updated, broadcast silently lost if NATS down); `on_state_raw` creates unbounded `service_status` rows from bus noise; `snapshot` returns the live dict reference (mutation-during-iteration risk); WS endpoint has no idle timeout / max message size / connection cap; `list_letterheads` (a GET) does disk writes; `list_stt_backends` raises unhandled `ImportError` 500; no shutdown timeout on `serve_task`, no WS bridge teardown; host hardcoded `0.0.0.0`, port `int()` cast can throw opaque `ValueError`.

#### 🟢 Low
`OLLAMA_URL` read at import time; unused `_: Request` params; **`patch_setting` `if body.value_type:` is always truthy** — a PATCH omitting `value_type` silently resets type to `"str"` (latent partial-update bug); `delete_setting` 404 lacks `error_code`; `get_session` dead code; `server_now` dead code; `"ws"` prefix match is too loose; `Behavior`/`Expression`/`Plugin` tables are entirely dead schema (no routers); repeated lazy PIL imports.

#### Security notes
No auth/authz whatsoever (binds `0.0.0.0:8080`). No CSRF. No CORS. WS bridge is a bus-exfiltration vector. Bus injection via `config.changed`. No rate limiting anywhere. `sudo -n systemctl restart` is a privileged escalation reachable unauthenticated (unit names *are* dict-validated — no command injection there). Path traversal: `_safe_name` is solid and tested; SPA fallback relies on Starlette `..` normalization (a `realpath` containment check would be more defensive). Error messages leak raw exception strings / stderr / internal paths. No SQL injection (parameterized throughout).

#### Database notes
SQLite WAL is reasonable for a single Pi — but **no `busy_timeout`**, so concurrent writers get `database is locked` 500s instead of waiting. No migrations. Dead `Behavior`/`Expression`/`Plugin` schema. `Setting.value max_length=4000` — verify `DEFAULT_SYSTEM_PROMPT` length; consider a `Text` column.

#### Test coverage gaps
No auth/CORS/CSRF tests (nothing to test yet). WS bridge concurrency under-tested — the `_add_sub` race, concurrent-`_fanout` write race, unsubscribe-failure not covered. `snapshot.py` has **no tests at all**. `system.py` failure paths (nonzero return, timeout, self-restart) untested. `bootstrap.py` seed idempotency untested. `agent.py` Ollama-proxy `503` path untested. Snapshot coalescing / `_decode_setting_value` / lifecycle handlers untested. No graceful-shutdown test.

#### Top recommendations
1. Add authentication + authorization to the entire control API + CSRF for state-changing routes. Nothing else matters until this exists.
2. Lock down the WS bridge — allow-list browser topic patterns (reject `>`), cap patterns/connections.
3. Fix WS concurrency — per-connection single-writer queue, `asyncio.Lock` on ref-counting, await/track unsubscribe.
4. Harden SQLite — `PRAGMA busy_timeout` + connect `timeout`; move blocking DB reads off the loop.
5. Add a migration framework (Alembic); decide the fate of the dead tables.
6. Add an explicit CORS policy and a `/healthz` endpoint.
7. Validate router inputs — `value_type` enum, reject unknown keys, special-case `restart control`, catch `ImportError`, sanitize error messages.
8. Close test gaps — snapshot router, `system.py` failures, the PATCH partial-update bug, WS ref-count race, shutdown.

---

### 4.4 Shared Library & Printer (`packages/shared`, `packages/printer`)

**Summary:** The **shared library** is well-structured with good schema bounds and a sensible base-service lifecycle, but has high-blast-radius bugs: a partially-initialized service crashes the heartbeat loop, NATS connection loss is unobservable, `subscribe_model` silently drops messages with no metrics. The **printer** is weaker: its path allowlist contradicts its own docstrings, `lp_options`/`media` flow unsanitized into a subprocess argv, and state events race. **Neither is production-ready** without the Critical/High fixes.

#### 🔴 Critical
- **[printer/service.py:43-54,298-381]** Path allowlist contradicts documented behavior — docstrings say allowed roots are the data dir **+ tempdir**, but `_path_within_allowed_roots` only checks the data dir. `compose_fortune` writes to `tempfile.mkstemp` (the temp dir). Anyone wiring compose output through `PRINTER_INTENT_PRINT_FILE` hits a silent "file path not allowed." Pick one source of truth.
- **[printer/service.py:112,133 & cups_client.py:55-111]** Unsanitized config injected into the `lp` subprocess argv — `lp_options` (from a NATS `ConfigChanged`) is `.split()` and appended to argv; `media` is a free `str`. No shell, so no shell injection — but an attacker who can publish config can pass arbitrary `lp` flags or a value starting with `-`. Validate `media` against `_MEDIA_INCHES`, bound the numeric adjusts, drop/allow-list `lp_options`.
- **[shared/base_service.py:55-88,146]** Heartbeat loop crashes a partially-started service / runs forever against a dead bus. If the NATS link is permanently dead, every tick logs a warning but the loop never escalates — the service looks "alive" to nothing. Add a consecutive-failure counter that drives a degraded state or process exit.

#### 🟠 High
- **[shared/nats_helper.py:17-38]** `connect_with_retry` docstring says "Never gives up" but only the *initial* connect is retried; no `error_cb`/`disconnected_cb`/`reconnected_cb`/`closed_cb` wired — zero observability into connection flapping; a `CLOSED` client never reconnects.
- **[shared/base_service.py:114-121,71-77]** Heartbeat re-publishes `_last_state_payload` every tick **forever** — a stale `error` state with an old detail gets re-broadcast every 5s indefinitely, confusing the admin UI. Re-emit only for a bounded post-boot window.
- **[printer/service.py:249-371]** Printer state transitions race — state is published on different subjects (`printer.state.printing/idle/error`); a `printer.state.*` subscriber is not guaranteed cross-subject ordering, so the UI can see `idle` before `error`. Publish on a single subject with state in the payload + a sequence number.
- **[printer/service.py:263-281]** Concurrent print jobs corrupt state — two intents both flip to `printing` and interleave `lp` calls on a single physical printer. Serialize behind an `asyncio.Lock`.
- **[printer/service.py:165-168]** `PRINTER_INTENT_TEST_PAGE` handler **prints nothing** — it only re-publishes state despite the topic name and a test targeting it. Dead/incorrect feature.
- **[printer/cups_client.py:67,125]** Job-ID parsing is fragile — `out.split()[3]` assumes exact `lp` output; locale/translation/format variation gives the wrong token or `IndexError`. Use an anchored regex.
- **[shared/settings.py:11-15]** `data_dir()` does a filesystem side effect (`mkdir`) on every call — a pure-looking getter that raises `OSError` on a read-only FS. Separate path computation from `ensure_data_dir()`.

#### 🟡 Medium
- `ConfigChanged.value: Any` defeats validation entirely (the central config type, zero type safety); no schema versioning / no explicit `model_config` extra-handling; `_on_agent_reply` auto-prints **every** reply including `puppet`/`system` source; `Image.open` has no decompression-bomb guard; `_fit_font` reloads the font from disk ~35× per compose; Windows has no graceful-shutdown path (silently suppressed); `drain()` has no timeout; `publish_model` doesn't flush — terminal `SystemError` publishes can be lost on exit; `_make_setattr_handler` applies no range validation (`rotate=999`, `scale_pct=-50` accepted); `lpstat -p` parsing is locale-dependent.

#### 🟢 Low
Partial `__all__` in `shared/__init__.py`; repeated function-local `import os`/`tempfile`/`Path`/`PIL`; `JsonFormatter` drops all `extra={}` structured fields (structure stops at the envelope); two `# type: ignore` on `event_name`; `formatter.format_reply` uses naive local time; import-time font path resolution; `PrinterIntentPrintFile.path` / `PrinterIntentPrintTranscript.transcript` lack length caps unlike sibling schemas.

#### Security notes
Subprocess argument injection via NATS config (see Critical). Path traversal: `_resolve_font` correctly rejects separators, `_path_within_allowed_roots` resolves symlinks — good (modulo the data-dir/temp-dir mismatch). `netinfo.primary_lan_ip` depends on `8.8.8.8` being routable — returns `None` on an isolated LAN even with a valid LAN IP. `prompts.py` system prompt is benign but does not instruct the model to resist instruction-override. NATS is connected with no credentials — the "anyone who can publish" threat is real on a shared LAN (Theme 1).

#### Schema & config design notes
No version field on any message — rolling upgrades have no compatibility contract. `ConfigChanged.value: Any` is the weakest schema point. Text fields are otherwise well-bounded with good DoS rationale, but `path`/`transcript`/`expression name` lack caps — inconsistent. Unknown-field handling relies on the Pydantic default; make it explicit (`extra="forbid"` for intents, `extra="ignore"` for events).

#### Test coverage gaps
`connect_with_retry` retry path untested. `subscribe_model` handler-exception branch untested. `BaseService` crash path (`SystemError` publish + re-raise), `on_shutdown` failure, signal handling untested. Heartbeat re-emission logic untested. No printer concurrency test. **`cups_client.py` has no test file at all** — all the `lp`/`lpstat` parsing is uncovered. `composer.py` only indirectly tested. `paths.py`, `logging_setup.py`, `settings.py`, `export_schemas.py` untested. **Test infra fragility:** wall-clock `time.sleep(0.5)` / `asyncio.sleep` after spawning `nats-server`, and hardcoded ports (4233/4234/4260) — will flake on a loaded CI runner or a slow Pi, and collide if suites run in parallel.

#### Strengths
Well-commented schema bounds with explicit DoS rationale; the `BaseService` state/lifecycle re-emission is a genuine, well-documented solution to a real race; blocking work correctly offloaded with `asyncio.to_thread`; `subscribe_model` isolates handler exceptions; strong path-safety design; unusually good "why" comments; temp files cleaned up in `finally`; clean `pyproject.toml`.

#### Top recommendations
1. Fix the path-allowlist / temp-dir contradiction.
2. Sanitize all printer config that reaches `lp` (allow-list `media`, bound numerics, drop/allow-list `lp_options`).
3. Serialize printer jobs with a lock; publish state on one subject with a sequence number.
4. Make NATS connection loss observable + survivable — wire lifecycle callbacks, add a heartbeat-failure counter, `flush()` terminal publishes.
5. Bound the heartbeat state re-emission to a post-boot window.
6. Implement or remove `PRINTER_INTENT_TEST_PAGE`.
7. Harden subprocess output parsing (anchored regexes); add the missing `cups_client.py` tests.
8. Tighten `ConfigChanged` and add schema versioning.

---

### 4.5 Web Frontend (`web/`)

**Summary:** Well-organized SolidJS SPA with careful reactivity (stores vs signals, optimistic UI, draft persistence) and good cleanup discipline. But a real production blocker: **no auth on any control surface** — anyone who can reach the SPA can drive servos, restart services, change settings, print. Other gaps: no inbound NATS message validation, no WebGL context-loss handling, no connection state surfaced on `/face` and `/pet`.

#### 🔴 Critical
- **[shared/api.ts / shared/nats_ws.ts]** No authentication on any control surface — every endpoint and the NATS WebSocket are reachable with no credential; `/admin` is not gated (`app.tsx:15`). On the Pi's hotspot, anyone can restart services, rewrite settings, puppeteer servos, print.
- **[pet/pet.tsx:130-196,363-375]** Drag controls send raw, unauthenticated, unvalidated servo commands at ~25 Hz — `flushPreview` POSTs DXL positions on every throttle tick. The clamp in `head_drag.ts` is the *only* safety and it is client-side. The backend must clamp/validate against `SERVO_RANGES` server-side and authenticate the caller.
- **[pet/pet_scene.ts:79-85,292-378]** No WebGL context-loss handling — an unconditional rAF loop renders against a dead context every frame after context loss, spamming errors and pinning CPU with no recovery. Register `webglcontextlost`/`webglcontextrestored`.

#### 🟠 High
- **[shared/nats_ws.ts:39-49]** No validation of inbound NATS frames — `JSON.parse` then `payload: any` dispatched blindly; handlers read `f.payload.text`/`.emotion`/`.uptime_s` directly. A malformed frame throws inside a handler and can break the dispatch loop for all topics. Validate `topic`/`payload`; wrap each handler in try/catch.
- **[shared/nats_ws.ts:51-58]** Reconnect never surfaces a disconnected state — `/face` and `/pet` silently show stale data with no offline indicator; the admin "live" dot is a one-way latch (never flips back).
- **[shared/nats_ws.ts:51-57]** Reconnect timer is not cancelable; no upper bound, no jitter — clients reconnecting after a Pi reboot thundering-herd in lockstep.
- **[pet/pet_scene.ts:391-403]** `dispose()` leaks — shared materials disposed multiple times; `renderer.dispose()` does not free the GL context, so repeated mount/unmount eventually hits the ~16-context browser limit. Call `forceContextLoss()`.
- **[face/face.tsx:118-129]** `tick` rAF loop never stops while idle — burns a full animation-frame loop forever on a 24/7 kiosk just to converge pulse to 0.
- **[admin/body_panel.tsx:117-127]** A `createEffect` owning a `setInterval` recreates the interval on every keystroke — churny, resets the 1s cadence mid-tuning.

#### 🟡 Medium
- `system_pulse.tsx` double-`JSON.stringify`s 200 firehose payloads per render (`>` subscription) — steady CPU; `face.tsx` autoplay-blocked path has no tap-to-play affordance (frozen first frame); `pet.tsx` optimistic chat echo can duplicate (no dedup, unlike `chat_log.tsx`); `flashHint`/hint ids use `Date.now()` and can collide; `service_status.tsx` clock-skew math can show negative ages; admin "live" indicator is a one-way latch; `api.ts` `req` assumes any non-204 body is JSON (raw `SyntaxError` on HTML); `chat_log.tsx` mutates textarea `.value` imperatively then syncs the signal; `devicemotion` shake `shakeAcc` only decays when events arrive.

#### 🟢 Low
**`types.gen.ts` is generated but never imported anywhere** — the whole `gen:types` codegen + `json-schema-to-typescript` dep produce a dead artifact; **Tailwind `colors` config is unused dead config** and disagrees with `index.css` and `design.ts` (three sources of truth); `void lsKeys;` no-op; `pet_scene.ts` `setEmotion` `tFade` param ignored; pervasive `catch (e: any)` then `e.message`; fonts loaded from Google Fonts CDN (offline Pi falls back to system fonts); heavy reliance on inline `style` objects.

#### Security notes
No auth anywhere. No XSS via `innerHTML` — all untrusted data rendered as JSX text nodes (good). No secrets in the client bundle (confirmed). NATS payloads untrusted/unvalidated before reaching the DOM (safe from XSS but a bad frame breaks dispatch). WebSocket URL correctly upgrades to `wss` on `https`. Upload size limits (`≤10MB`/`≤5MB`) are claimed in the UI but `file.size` is never checked before upload — server must enforce.

#### Performance notes (Pi / low-power client)
Very high-poly geometry for a procedural toy face (head `SphereGeometry(1.2,96,96)` ≈18k verts) — 32–48 segments would look identical; pet rAF renders every frame unconditionally; `face.tsx` rAF runs forever at idle; `system_pulse` double-stringifies 200 payloads/render; `body_panel` recreates an interval per keystroke.

#### Build & dependency notes
Deps are current-ish and clean (`three 0.184`, `vite 5.4.21`, `solid-js 1.9.13`, `vitest 1.6.1`, `typescript 5.9.3`); no known-critical advisories at cutoff; run `npm audit` in CI. `tsconfig.json` is appropriately strict. **The `build` script's `gen:types` step shells out to `uv run python -m lafufu_shared.export_schemas`** — making the web build hard-depend on a working Python+uv env; CI building only the frontend would fail. And the generated file is unused — fragile cross-language dependency for zero current benefit. `gen_types.mjs` does brittle string-surgery dedup.

#### Test coverage gaps
Only 3 pure-function test files; **zero component tests**. **`NatsWs` class is untested** — reconnect/backoff, subscribe ref-counting, resub-on-open, malformed-message handling all uncovered (highest-value gap). No tests for `api.ts` `errorMessage`/`req`, `parseValue`, `parseNums`, `parseBool`.

#### Strengths
Clean separation of pure logic (`head_drag.ts`, `design.ts`) — testable and tested; excellent "why" comments; consistent and correct `onCleanup` discipline (every subscription/interval/rAF/listener traced is cleaned up); thoughtful UX resilience (optimistic UI with revert, draft persistence, puppeteer grace window, dirty-state tracking); good FastAPI `detail` error surfacing; `prefers-reduced-motion` honored, ARIA roles present; empty/loading/fallback states exist.

#### Top recommendations
1. Add auth/authz to the control API + NATS WebSocket; gate `/admin`.
2. Validate and clamp servo commands server-side — the client clamp can't be trusted for hardware safety.
3. Add WebGL context-loss/restore handling; `forceContextLoss()` in `dispose()`.
4. Validate inbound NATS frames; wrap handlers in try/catch.
5. Surface connection state — `onStatus` hook, offline indicator on `/face` and `/pet`, two-way admin "live" dot.
6. Either wire `types.gen.ts` into the API/NATS layer (would catch #4) or delete the codegen.
7. Add `NatsWs` tests (reconnect, ref-counting, malformed messages) + `api.ts`/`parseValue` tests.
8. Pause idle rAF loops; lower pet geometry segment counts; fix the per-keystroke interval recreation.

---

### 4.6 Deployment, Infrastructure, CI & Cross-Cutting Security

**Summary:** Structurally reasonable for a single-board hobby robot — dedicated `lafufu` user, declarative systemd units, a sensible install script. **Not production-ready.** The control surface binds `0.0.0.0:8080` with zero auth; the sudoers privilege-escalation file is shipped but **never installed**; systemd units have **no sandboxing**; the crash-loop rate-limit directives are in the wrong section (silently ignored); CI does no security scanning, dependency auditing, or frontend build.

#### 🔴 Critical
- **[control/service.py:46]** Control API binds `0.0.0.0` with no auth (see §4.3). Bind to `127.0.0.1` + authenticated reverse proxy, or add token auth before any non-localhost exposure.
- **[deploy/install.sh:79-88]** **Sudoers file is never installed** — `deploy/sudoers/lafufu-services` exists and `system.py` depends on `sudo -n systemctl restart`, but `install.sh` never copies it to `/etc/sudoers.d/`. The file's own header *falsely claims* it is "Installed by deploy/install.sh." On a fresh install the restart button 500s. Fix: `install -m 0440 -o root -g root … /etc/sudoers.d/lafufu-services` + `visudo -cf` validation.
- **[deploy/nats/nats-server.production.conf:1-10]** NATS open to the LAN — `port: 4222`, no `listen` directive (binds `0.0.0.0`), no `authorization`, no per-subject permissions, no TLS; `http_port: 8222` exposes monitoring LAN-wide. The "production" config equals the dev config. Any LAN host can subscribe to `>` or publish forged intents. Fix: `listen: "127.0.0.1:4222"`, `http: "127.0.0.1:8222"`.

#### 🟠 High
- **[systemd/lafufu-agent.service:22-23 + animator]** `StartLimitBurst`/`StartLimitIntervalSec` are in `[Service]` — in modern systemd these are **`[Unit]`** directives; placed under `[Service]` they are **silently ignored**, so crash-loop limiting does not work. control/printer/kiosk have no start-limit directives at all.
- **[systemd/*.service]** **No sandboxing on any unit** — no `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`, `RestrictAddressFamilies`, `MemoryMax`, `CPUQuota`, `LimitNOFILE` (only `nats.service` sets `LimitNOFILE`). The ML-loading agent and upload-accepting control service especially need `MemoryMax=`.
- **[systemd/lafufu-btcast.service:1-15]** btcast runs as **root** — every other unit runs as `lafufu`; this one omits `User=`. Bluetooth control does not require root. Unnecessary root surface.
- **[systemd/nats.service + lafufu.target]** Startup ordering inconsistent — `nats.service` has no `Before=lafufu.target`; per-service units `Wants=` (weak) NATS while `lafufu.target` `Requires=` it; kiosk orders `After=graphical-session.target` which may not exist on Pi-OS Lite.
- **[systemd/*.service]** **No `WatchdogSec`** — services publish a 5s NATS heartbeat but nothing feeds the systemd watchdog, so a hung-but-not-crashed process is never restarted.

#### 🟡 Medium
- `smoke.sh` uses `nc` which `install.sh` never installs (fails "command not found"); `smoke.sh` has only `set -e` (no `-u`/`-o pipefail`); `lafufu-btcast.sh` has only `set -u`; `install.sh` pipes `curl … | sh` (uv installer) and `curl -L … | tar xz` (NATS) with no checksum verification; **CI never runs `npm run build`**; CI has no npm caching, no Python type-checking, no security/dependency audit (`pip-audit`/`npm audit`/CodeQL); README points at a non-existent `scripts/dev_run_all.sh`.

#### 🟢 Low
`sniff_nats.py` is a committed LAN-wildcard debug subscriber (move to `tools/` or remove); dev NATS config uses a CWD-relative `store_dir`; `.claude/` is untracked but uncovered by `.gitignore`; `.gitattributes` only covers `*.sh` + systemd (add binary markers for `assets/*`); kiosk unit hardcodes `XDG_RUNTIME_DIR=/run/user/1000`.

#### Systemd hardening assessment
Below baseline. **Positives:** six of seven units run as unprivileged `lafufu`, `printer` uses `Group=lp`, `Restart=on-failure`+`RestartSec` everywhere, the kiosk unit has a smart `ExecStartPre` readiness gate. **Negatives:** zero sandboxing; no resource limits; crash-loop limiter inert (wrong section); btcast runs as root; no `WatchdogSec`; `Requires=`/`Wants=` inconsistency.

#### Sudoers / privilege-escalation assessment
The rule itself is reasonably scoped — passwordless `systemctl restart` for exactly four named units + `journalctl -u lafufu-{…}*`, restart verbs pinned to fully-qualified unit names with no wildcards (good — prevents `restart anything`). Two concerns: the `journalctl -u lafufu-agent*` **wildcard** is looser than necessary (prefer exact `.service` names); and the file is **never installed** (Critical) so the privilege boundary `system.py` relies on does not exist on a real deployment. Systemic risk: because the control API is unauthenticated and LAN-exposed, this rule effectively makes "restart these four services" an **unauthenticated LAN capability** once installed.

#### NATS bus security assessment
Effectively no security. Dev and "production" configs are identical apart from store paths/limits — both bind `0.0.0.0` on `4222` (client) and `8222` (HTTP monitoring). No `authorization`, no `accounts`, no per-subject `permissions`, no TLS. `max_payload` at the 1 MB default. Anyone on the network can subscribe to `>` and publish forged `*.intent.*` to drive the hardware. Minimum fix: bind both listeners to `127.0.0.1`; for multi-host, add per-service credentials + subject permissions + TLS.

#### Secrets management assessment
Low risk today — there are almost no secrets (STT/LLM/TTS are all local; no API keys). `git log --all` + a repo-wide grep find nothing committed; `.gitignore` correctly excludes `.env*`, `/data/`, `*.sqlite*`, model files. **Gaps for the future:** no unit uses `EnvironmentFile=` — all config is inline `Environment=` lines, so the first cloud API key a developer adds will likely be inlined into a world-readable `.service` file. Establish an `EnvironmentFile=/etc/lafufu/secrets.env` (mode 0600) convention *now*, before the first secret arrives.

#### CI/CD assessment
Partial. **Positives:** pinned `actions/checkout@v4` + `astral-sh/setup-uv@v3`, uv caching, runs `ruff check` + `ruff format --check` + `pytest`; the web job runs `npm ci` + `typecheck` + `vitest`; `.pre-commit-config.yaml` pins `ruff-pre-commit@v0.15.0`. **Gaps:** the **frontend production build (`npm run build`) is never run in CI** — the exact step `install.sh` depends on; **no security scanning / dependency audit**; no Python static type-checking (ruff is a linter, not a type checker); `setup-node@v4` without `cache: npm`; NATS server downloaded via unpinned-checksum `curl | tar`; whether CI gates merges depends on branch-protection config not in the repo.

#### Strengths
`install.sh` uses `set -euo pipefail`, is idempotent, handles fresh-install + `--update`; dedicated unprivileged user for six of seven services; `system.py` avoids command injection via a hardcoded dict; `printer.py` upload handling is genuinely careful (traversal rejection, size caps, real byte-validation, atomic writes); both lockfiles committed, `npm ci` used; kiosk readiness gate; NATS binary version-pinned.

#### Top recommendations
1. Bind control to `127.0.0.1` + add auth to all `/api` and `/ws` routes.
2. Bind NATS to `127.0.0.1` (both `4222` and `8222`).
3. Actually install the sudoers file from `install.sh` (with `visudo -cf` validation), or remove the sudo dependency.
4. Move `StartLimitBurst`/`StartLimitIntervalSec` to `[Unit]` on every unit; add them to control/printer/kiosk.
5. Add systemd sandboxing to every unit + `MemoryMax`/`CPUQuota` on agent and control.
6. Run `lafufu-btcast` as `lafufu`, not root.
7. CI hardening — run `npm run build`, add `cache: npm`, add `npm audit` + a Python dependency audit, verify branch protection.
8. Fix `smoke.sh` (`nc` dependency, `set -euo pipefail`).
9. Add `WatchdogSec` + `sd_notify` pings to long-running services.
10. Fix README drift; correct the false "installed by install.sh" comment in the sudoers file.

---

## 5. Prioritized roadmap to production

A suggested phasing. Phase 1 is non-negotiable before any networked deployment.

### Phase 1 — Security & safety (ship-blockers) — ~1 week
- **Auth.** Add a token/session auth dependency to all `/api` + `/ws` routes; gate `/admin`. Bind control to `127.0.0.1`; bind NATS (`4222` + `8222`) to `127.0.0.1`.
- **WS bridge.** Allow-list browser subscribe patterns (reject `>`); cap patterns/connections.
- **Servo safety.** Server-side clamp/validate all servo commands; write hardware Profile Velocity/Accel + Min/Max Position + current limits on torque-enable; check DXL comm-result/error byte.
- **Install correctness.** Install the sudoers file from `install.sh`; fix the `[Unit]`/`[Service]` crash-limiter placement.

### Phase 2 — Resilience & correctness — ~1–1.5 weeks
- Wrap every service work-cycle so exceptions reset state to `degraded`/`idle` + emit `SystemError` (agent especially).
- Move all blocking I/O off the event loop (DXL worker thread; SQLite via `to_thread`).
- Add fault recovery: DXL bus reconnect; NATS lifecycle callbacks + heartbeat-failure escalation; mic device re-probe on `OSError`.
- Fix graceful shutdown in agent/animator/control (await tasks before closing devices); call `DxlBus.close()`.
- Fix the concurrency races: WS ref-count lock + single-writer queue; printer job lock; mic-loop teardown.
- SQLite `busy_timeout`; sanitize printer `lp` config; resolve the path-allowlist contradiction.

### Phase 3 — Hardening & operability — ~1 week
- systemd sandboxing + `MemoryMax`/`CPUQuota` + `WatchdogSec` (+ `sd_notify`).
- DB migrations (Alembic); schema versioning on the message envelope.
- `/healthz` endpoint; structured logging with real key/value fields; connection-state UI.
- CI: `npm run build`, dependency audit, npm caching; standardize shell scripts on `set -euo pipefail`.
- WebGL context-loss handling; pause idle rAF loops; lower pet geometry poly count.

### Phase 4 — Test coverage & cleanup — ongoing
- Failure-path tests in every domain (LLM/STT/mic failures, DXL disconnect→reconnect, printer concurrency, WS ref-count race, graceful shutdown).
- Untested modules: `cups_client.py`, `NatsWs`, real `DxlBus`, `snapshot.py`, `paths.py`, `settings.py`.
- Replace wall-clock `sleep` test races with event-based waits; randomize/uniquify test ports.
- Delete dead code: `vad.py`, `_safe_apply_immediate`, `get_session`, `server_now`, `types.gen.ts` (or wire it in), unused Tailwind colors, dead `Behavior`/`Expression`/`Plugin` tables; fix README drift.

---

## 6. What's genuinely good

This is not a project in trouble — it is a well-built project that hasn't been hardened yet. Worth preserving:

- **Clean architecture.** Protocol/duck-typed decoupling makes the whole system testable without hardware or a broker; the `lafufu_shared.testing` fakes are well done.
- **Real engineering judgement** in the hard parts: `pipeline.speak`'s bounded-queue streaming with producer cancellation, the per-servo time-constant easing model, the `BaseService` state re-emission to beat a slow subscriber, atomic file writes, the SolidJS store-vs-signal choices.
- **Defensive instincts** already present: position clamping everywhere, path-traversal guards, upload byte-validation, schema bounds with explicit DoS rationale, blocking work offloaded to executors in most places.
- **Unusually good comments** — they explain *why*, not *what* — and a consistent structured-logging style.
- **Discipline:** committed lockfiles, `npm ci`, pinned pre-commit + CI actions, an idempotent install script, `tsconfig` strict mode, clean ruff config — and as of this review, **all 154 Python tests, ruff, web typecheck, and 18 web tests pass.**

The gap between "vibe coded" and "production" here is **hardening, not rebuilding.**

---

*Review method: 6 parallel domain reviewers (static analysis of every source + test file) coordinated by Claude Code, plus centrally-run `pytest` / `ruff` / `tsc` / `vitest`. All `file:line` citations reflect the `lafufu-drag-controls` branch as of 2026-05-20.*
