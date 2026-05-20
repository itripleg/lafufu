# Chat Persistence & Interaction Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every Lafufu conversation to the control database with a measured reply delay, expose it over a read API, and upgrade the admin Chat widget to load history and show the live pipeline stage.

**Architecture:** A flat `ChatMessage` table; the control service inserts one row per `agent.transcript` / `agent.reply` NATS event (off the event loop) and computes a transcript→reply delay; a guarded `GET /api/chat/messages` endpoint serves it; the agent gains a distinct `transcribing` state; the SolidJS Chat widget hydrates from the API and reflects the agent's live state.

**Tech Stack:** Python 3.13, SQLModel/SQLite, FastAPI, NATS (nats-py), pytest; SolidJS + TypeScript + Vite.

**Design spec:** `docs/superpowers/specs/2026-05-20-chat-persistence-design.md`

**Branch:** `chat-persistence` (already created).

> **Note on DB queries:** the control package's house style for SQLModel reads
> is `session.exec(select(Model)).all()`. Follow that existing pattern — see
> `service.py`'s `_rebroadcast_all_settings` for the reference call.

---

## File Structure

Create:
- `packages/control/src/lafufu_control/models/chat.py` — `ChatMessage` SQLModel table.
- `packages/control/src/lafufu_control/api/routers/chat.py` — read API router.
- `packages/control/tests/test_chat.py` — model, API, and persistence tests.

Modify:
- `packages/control/src/lafufu_control/models/__init__.py` — export `ChatMessage`.
- `packages/control/src/lafufu_control/db.py` — `init_db` imports the `chat` model module.
- `packages/control/src/lafufu_control/api/app.py` — mount the guarded `chat` router.
- `packages/control/src/lafufu_control/service.py` — persistence subscriptions + reply-delay.
- `packages/shared/src/lafufu_shared/schemas.py` — add `transcribing` to `AgentStateName`.
- `packages/shared/src/lafufu_shared/topics.py` — add `AGENT_STATE_TRANSCRIBING`.
- `packages/agent/src/lafufu_agent/pipeline.py` — `run_one_cycle` gains a `publish_listening` flag.
- `packages/agent/src/lafufu_agent/__main__.py` — `RealMic.record_until_silence` returns audio.
- `packages/agent/src/lafufu_agent/service.py` — publish `transcribing`, transcribe via `self.stt`.
- `web/src/shared/api.ts` — `chatMessages()`.
- `web/src/admin/chat_log.tsx` — load history, per-message timestamps, live stage indicator.

---

## Task 1: ChatMessage model + DB wiring

**Files:**
- Create: `packages/control/src/lafufu_control/models/chat.py`
- Modify: `packages/control/src/lafufu_control/models/__init__.py`
- Modify: `packages/control/src/lafufu_control/db.py` (the import line inside `init_db`)
- Test: `packages/control/tests/test_chat.py`

- [ ] **Step 1: Write the failing test.** Create `packages/control/tests/test_chat.py` with `test_chat_message_round_trips(tmp_path)`: build an engine with `create_engine_for_path(str(tmp_path / "t.sqlite"))`, call `init_db(engine)`; in a `Session(engine)` add `ChatMessage(role="lafufu", text="the city remembers you", emotion="neutral", source="llm", reply_delay_ms=1234)` and commit; in a fresh `Session` read it back with `session.exec(select(ChatMessage)).one()`; assert `id is not None`, `role == "lafufu"`, `text == "the city remembers you"`, `emotion == "neutral"`, `source == "llm"`, `reply_delay_ms == 1234`, and `isinstance(created_at, datetime)`. Imports: `from datetime import datetime`; `from lafufu_control.db import create_engine_for_path, init_db`; `from lafufu_control.models.chat import ChatMessage`; `from sqlmodel import Session, select`.

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest packages/control/tests/test_chat.py -q` → `ModuleNotFoundError: No module named 'lafufu_control.models.chat'`.

- [ ] **Step 3: Create the model.** `packages/control/src/lafufu_control/models/chat.py` — a module docstring, then `from datetime import UTC, datetime` and `from sqlmodel import Field, SQLModel`, then class `ChatMessage(SQLModel, table=True)` with exactly these fields:
  - `id: int | None = Field(default=None, primary_key=True)`
  - `role: str = Field(max_length=16)`  # "user" | "lafufu" | "puppet"
  - `text: str = Field(max_length=8000)`
  - `emotion: str | None = Field(default=None, max_length=32)`
  - `source: str | None = Field(default=None, max_length=32)`  # "llm"|"puppet"|"system"; None for user
  - `reply_delay_ms: int | None = Field(default=None)`
  - `created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)`

- [ ] **Step 4: Export the model.** In `models/__init__.py` add `from .chat import ChatMessage` and put `"ChatMessage"` in `__all__`, keeping it alphabetical: `["Behavior", "ChatMessage", "Expression", "Plugin", "Setting"]`.

- [ ] **Step 5: Register with init_db.** In `db.py`, the import inside `init_db` becomes `from .models import behavior, chat, expression, plugin, setting  # noqa: F401`.

- [ ] **Step 6: Run, expect PASS.** `uv run pytest packages/control/tests/test_chat.py -q` → 1 passed.

- [ ] **Step 7: Commit.** `git add` the new model, `models/__init__.py`, `db.py`, `test_chat.py`; `git commit -m "feat(control): ChatMessage model for conversation persistence"`.

---

## Task 2: Chat read API

**Files:**
- Create: `packages/control/src/lafufu_control/api/routers/chat.py`
- Modify: `packages/control/src/lafufu_control/api/app.py`
- Test: `packages/control/tests/test_chat.py`

- [ ] **Step 1: Write the failing tests.** Append to `test_chat.py`. Add a helper `_client(tmp_path)`: build + `init_db` an engine, then `app = create_app(engine=engine, nats_publish=lambda s, p: None)`, return `(TestClient(app), engine)`. Add `_seed(engine, rows)`: open a `Session`, `add` each row, commit. Tests:
  - `test_messages_endpoint_returns_oldest_first(tmp_path)`: seed three `ChatMessage` rows with `created_at` of a fixed base time and base+1s, base+2s and texts "first"/"second"/"third"; `GET /api/chat/messages`; assert status 200 and the response `messages` texts equal `["first", "second", "third"]`.
  - `test_messages_endpoint_clamps_limit(tmp_path)`: seed 5 rows; assert `?limit=2` yields 2 messages, `?limit=0` yields 1, `?limit=9999` yields 5.
  - `test_messages_endpoint_empty(tmp_path)`: assert `GET /api/chat/messages` on an empty DB returns exactly `{"messages": []}`.
  Imports to add: `from datetime import UTC, datetime, timedelta`; `from fastapi.testclient import TestClient`; `from lafufu_control.api.app import create_app`.

- [ ] **Step 2: Run, expect FAIL.** The three new tests get HTTP 404 (router not mounted).

- [ ] **Step 3: Create the router.** `packages/control/src/lafufu_control/api/routers/chat.py`: module docstring; `from fastapi import APIRouter, Request`; `from sqlmodel import Session, select`; `from ..models.chat import ChatMessage`; `router = APIRouter()`. Define `list_messages(req: Request, limit: int = 100) -> dict` decorated `@router.get("/messages")`:
  - clamp: `limit = max(1, min(limit, 500))`.
  - open `Session(req.app.state.engine)`; build the statement `select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)`; execute it with `session.exec(stmt).all()` and wrap in `list(...)`.
  - `rows.reverse()` to get oldest-first.
  - return `{"messages": [...]}` where each element is a dict with keys `id`, `role`, `text`, `emotion`, `source`, `reply_delay_ms`, and `created_at` set to `r.created_at.isoformat()`.

- [ ] **Step 4: Mount it.** In `app.py` add `from .routers import chat as chat_router` to the router-import group. After the existing `app.include_router(printer_router.router, ...)` line, add `app.include_router(chat_router.router, prefix="/api/chat", tags=["chat"], dependencies=guarded)`. `guarded` is the existing `[Depends(require_auth)]` list — the chat router is auth-guarded like every other data router.

- [ ] **Step 5: Run, expect PASS.** `uv run pytest packages/control/tests/test_chat.py -q` → 4 passed.

- [ ] **Step 6: Commit.** `git add` the router, `app.py`, `test_chat.py`; `git commit -m "feat(control): GET /api/chat/messages read endpoint"`.

---

## Task 3: Control persistence + reply delay

**Files:**
- Modify: `packages/control/src/lafufu_control/service.py`
- Test: `packages/control/tests/test_chat.py`

- [ ] **Step 1: Write the failing tests.** Append to `test_chat.py`; add `import pytest` and `from lafufu_control.service import ControlService`.
  - `test_compute_reply_delay_for_llm_reply()`: `svc = ControlService()`; `svc._last_transcript_at = datetime.now(UTC) - timedelta(seconds=2)`; `delay = svc._compute_reply_delay_ms("llm")`; assert `delay is not None` and `1800 <= delay <= 2400`.
  - `test_compute_reply_delay_none_for_puppet()`: with a recent `_last_transcript_at`, assert `svc._compute_reply_delay_ms("puppet") is None`.
  - `test_compute_reply_delay_none_without_transcript()`: `svc._last_transcript_at = None`; assert `svc._compute_reply_delay_ms("llm") is None`.
  - `test_compute_reply_delay_none_when_gap_too_large()`: `_last_transcript_at` 600 s ago; assert result is `None`.
  - `test_persist_chat_inserts_row(tmp_path)` (decorate `@pytest.mark.asyncio`): build + `init_db` an engine; `svc = ControlService()`; `await svc._persist_chat(engine, role="user", text="hello", emotion=None, source=None, reply_delay_ms=None)`; read the row back with `session.exec(select(ChatMessage)).one()`; assert `role == "user"` and `text == "hello"`.

- [ ] **Step 2: Run, expect FAIL.** `AttributeError` — `ControlService` has no `_compute_reply_delay_ms` / `_persist_chat` / `_last_transcript_at`.

- [ ] **Step 3: Add imports and state.** In `service.py` add `from datetime import UTC, datetime` to the imports, and `from .models.chat import ChatMessage` alongside the existing `from .models.setting import Setting`. In `ControlService.__init__`, after `self._app = None`, add `self._last_transcript_at: datetime | None = None` with a one-line comment that it is the arrival time of the most recent `agent.transcript`, cleared by the reply that consumes it.

- [ ] **Step 4: Add the helpers.** Add two methods to `ControlService`, placed just before `main_loop`:
  - `_compute_reply_delay_ms(self, source: str) -> int | None`: if `source == "puppet"` or `self._last_transcript_at is None`, return `None`; compute `gap = (datetime.now(UTC) - self._last_transcript_at).total_seconds()`; if `gap < 0` or `gap >= 120`, return `None`; else return `int(gap * 1000)`. Docstring: transcript→reply delay; puppet replies and stale (≥120 s) gaps yield `None`.
  - `async _persist_chat(self, engine, *, role, text, emotion, source, reply_delay_ms) -> None`: define a nested sync `_insert()` that opens `Session(engine)`, `s.add(ChatMessage(role=role, text=text, emotion=emotion, source=source, reply_delay_ms=reply_delay_ms))`, `s.commit()`; run it with `await asyncio.to_thread(_insert)` inside a `try/except Exception as e` that calls `self.log.warning("chat.persist.failed role=%s error=%s", role, e)`. A failed insert must be logged, never raised.

- [ ] **Step 5: Run the helper tests, expect PASS.** `uv run pytest packages/control/tests/test_chat.py -q` → 9 passed.

- [ ] **Step 6: Wire the NATS subscriptions.** In `on_startup`, immediately after the `on_pose` subscription block (the `await nats_helper.subscribe_model(self.nats, topics.ANIMATOR_POSE, schemas.AnimatorPose, on_pose)` call), add two nested async handlers and subscribe them. They close over the `engine` local exactly like the existing `on_snapshot_request`.
  - `on_transcript(subject, msg: schemas.AgentTranscript)`: set `self._last_transcript_at = datetime.now(UTC)`; `await self._persist_chat(engine, role="user", text=msg.text, emotion=None, source=None, reply_delay_ms=None)`.
  - `on_reply(subject, msg: schemas.AgentReply)`: `role = "puppet" if msg.source == "puppet" else "lafufu"`; `delay_ms = self._compute_reply_delay_ms(msg.source)`; `self._last_transcript_at = None`; `await self._persist_chat(engine, role=role, text=msg.text, emotion=msg.emotion, source=msg.source, reply_delay_ms=delay_ms)`.
  - Subscribe via `nats_helper.subscribe_model` to `topics.AGENT_TRANSCRIPT` with `schemas.AgentTranscript` and `on_transcript`, and `topics.AGENT_REPLY` with `schemas.AgentReply` and `on_reply`.

- [ ] **Step 7: Run the full control suite.** `uv run pytest packages/control -q` → all green.

- [ ] **Step 8: Commit.** `git add service.py test_chat.py`; `git commit -m "feat(control): persist agent transcripts + replies with reply delay"`.

---

## Task 4: Add the transcribing agent state

**Files:**
- Modify: `packages/shared/src/lafufu_shared/schemas.py` (the `AgentStateName` literal, lines 18-20)
- Modify: `packages/shared/src/lafufu_shared/topics.py` (after `AGENT_STATE_LISTENING`)
- Test: `packages/shared/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test.** Append `test_agent_state_accepts_transcribing()` to `test_schemas.py`: `from lafufu_shared.schemas import AgentState`; build `AgentState(state="transcribing")`; assert `.state == "transcribing"`.

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest packages/shared/tests/test_schemas.py::test_agent_state_accepts_transcribing -v` → Pydantic `ValidationError` (`transcribing` not a permitted `AgentStateName`).

- [ ] **Step 3: Extend the literal.** In `schemas.py`, insert `"transcribing"` into `AgentStateName` between `"listening"` and `"thinking"`, so the literal members are: `"warming", "idle", "listening", "transcribing", "thinking", "speaking", "degraded", "shutdown"`.

- [ ] **Step 4: Add the topic constant.** In `topics.py`, immediately after the line `AGENT_STATE_LISTENING = f"{AGENT_STATE}.listening"`, add `AGENT_STATE_TRANSCRIBING = f"{AGENT_STATE}.transcribing"`.

- [ ] **Step 5: Run, expect PASS.** `uv run pytest packages/shared/tests/test_schemas.py -q`.

- [ ] **Step 6: Commit.** `git add schemas.py topics.py test_schemas.py`; `git commit -m "feat(shared): add transcribing agent state"`.

---

## Task 5: run_one_cycle gains a publish_listening flag

**Files:**
- Modify: `packages/agent/src/lafufu_agent/pipeline.py` (`run_one_cycle`)
- Test: `packages/agent/tests/test_pipeline.py`

The split-lock voice flow (Task 6) publishes `listening` then `transcribing` itself and reuses `run_one_cycle` for the LLM + speak phase. Without this flag, `run_one_cycle` would re-publish `listening` and flicker the stage backwards.

- [ ] **Step 1: Write the failing test.** Append `test_run_one_cycle_can_skip_listening_state(nats_server)` to `test_pipeline.py`. Connect a NATS client; subscribe to `f"{topics.AGENT_STATE}.*"` collecting the last `.`-token of each `msg.subject` into a list; build `VoicePipeline(nats_client=<a connection>, mic=FakeMic(), ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]), piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]))`; `await pipeline.run_one_cycle(publish_listening=False)`; `await asyncio.sleep(0.2)`; drain. Assert `"listening"` is NOT in the collected states and `"thinking"` IS. (`FakeMic` and `nats_server` already exist in this test file.)

- [ ] **Step 2: Run, expect FAIL.** `TypeError: run_one_cycle() got an unexpected keyword argument 'publish_listening'`.

- [ ] **Step 3: Add the flag.** In `pipeline.py`, change the signature to `async def run_one_cycle(self, publish_listening: bool = True) -> None:`. The method's first action is currently `await self._publish_state("listening")`; guard it: `if publish_listening:` then the publish. Nothing else in the method changes.

- [ ] **Step 4: Run the new test, expect PASS.** `uv run pytest packages/agent/tests/test_pipeline.py::test_run_one_cycle_can_skip_listening_state -v`.

- [ ] **Step 5: Run the pipeline suite.** `uv run pytest packages/agent/tests/test_pipeline.py -q` → all green (default `publish_listening=True` preserves existing behavior).

- [ ] **Step 6: Commit.** `git add pipeline.py test_pipeline.py`; `git commit -m "refactor(agent): run_one_cycle publish_listening flag"`.

---

## Task 6: Publish the transcribing state during STT

**Files:**
- Modify: `packages/agent/src/lafufu_agent/__main__.py` (`RealMic.record_until_silence`, `RealMic.listen_once`)
- Modify: `packages/agent/src/lafufu_agent/service.py` (`_voice_cycle_with_split_lock`)
- Test: `packages/agent/tests/test_service.py`

Atomic task: `RealMic.record_until_silence` changes its return type from `str` to a numpy audio array, and the agent service is updated in the SAME task to transcribe that audio itself. Splitting these would leave the build broken between tasks.

- [ ] **Step 1: Write the failing test.** Append `test_voice_cycle_publishes_transcribing_state(nats_server)` to `test_service.py`. Define a local class `_OnsetMic` with: `wait_for_onset()` returning the tuple `(True, [])`; `record_until_silence(pre_roll)` returning `numpy.zeros(1600, dtype=numpy.float32)`; `listen_once()` returning `""`. Build `AgentService(mic=_OnsetMic(), ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]), piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]), nats_url=nats_server, stt=FakeWhisper(fixed_reply="hello there"))` (`FakeWhisper` is importable from `lafufu_shared.testing`). Connect a NATS client; subscribe to `f"{topics.AGENT_STATE}.*"` collecting state tails. `task = asyncio.create_task(svc.run())`; sleep 0.3; `svc.start_mic_loop()`; sleep 0.6; drain; `svc._shutdown.set()`; `await asyncio.wait_for(task, timeout=3)`. Assert `"transcribing"` is in the collected states. `import numpy` inside the test.

- [ ] **Step 2: Run, expect FAIL.** `"transcribing"` never appears in the collected states.

- [ ] **Step 3: RealMic.record_until_silence returns audio.** In `__main__.py`, in `RealMic.record_until_silence`: keep the recording loop and the resample exactly as they are, but instead of the final `return self.stt.transcribe(audio_np)`, return the float32 numpy array itself (the value currently named `audio_np`). When there are no frames, return `np.zeros(0, dtype=np.float32)` instead of `""`. Change the return annotation to the string `"np.ndarray"`. Then update `RealMic.listen_once`: after `wait_for_onset`, do `audio = self.record_until_silence(pre_roll)`; if `audio.size == 0` return `""`; otherwise `return self.stt.transcribe(audio)`.

- [ ] **Step 4: Agent service transcribes + publishes transcribing.** In `service.py`, in `_voice_cycle_with_split_lock`, replace the body from `async with self._cycle_lock:` onward. Inside the lock: `audio = await loop.run_in_executor(None, self._mic.record_until_silence, pre_roll)`; if `getattr(audio, "size", 0) == 0`, `await self._publish_state("idle")` and return; `await self._publish_state("transcribing")`; `transcript = await loop.run_in_executor(None, self.stt.transcribe, audio)`; `clean = (transcript or "").strip()`; if `len(clean) < 2`, `await self._publish_state("idle")` and return; then build the existing one-shot `_OnceMic` whose `listen_once` returns `clean`, construct a `VoicePipeline` with it, and `await tmp.run_one_cycle(publish_listening=False)`. The lines above the lock (the `_pipeline is None` guard, the `not hasattr(self._mic, "wait_for_onset")` fast-path, the `await self._publish_state("listening")` and the `wait_for_onset` executor call) are unchanged.

- [ ] **Step 5: Run the new test, expect PASS.** `uv run pytest packages/agent/tests/test_service.py::test_voice_cycle_publishes_transcribing_state -v`.

- [ ] **Step 6: Run the full agent suite.** `uv run pytest packages/agent -q` → all green. (`RealMic` has no direct unit tests; the existing `_SilentMic` test returns `False` from `wait_for_onset`, so the new transcribe path is not reached there.)

- [ ] **Step 7: Commit.** `git add __main__.py service.py test_service.py`; `git commit -m "feat(agent): publish transcribing state during STT"`.

---

## Task 7: Chat widget loads persisted history

**Files:**
- Modify: `web/src/shared/api.ts`
- Modify: `web/src/admin/chat_log.tsx`

No SolidJS component-test harness exists in the repo; verify with the TypeScript compiler, the existing Vitest suite, and a manual check.

- [ ] **Step 1: API client.** In `api.ts` add an exported type `ChatRow` with fields `id: number`, `role: "user" | "lafufu" | "puppet"`, `text: string`, `emotion: string | null`, `source: string | null`, `reply_delay_ms: number | null`, `created_at: string`. Add to the `api` object: `chatMessages: () => req<{ messages: ChatRow[] }>("GET", "/chat/messages")`.

- [ ] **Step 2: Entry gains id + a mapper.** In `chat_log.tsx`: add an optional `id?: number` field to the `Entry` interface (comment it as the DB row id, absent for live entries). Change the api import to `import { api, type ChatRow } from "../shared/api";`. Add a `rowToEntry(r: ChatRow): Entry` helper just below `fmtElapsed` that returns `{ id: r.id, role: r.role, text: r.text, emotion: r.emotion ?? undefined, ts: Date.parse(r.created_at), elapsedMs: r.reply_delay_ms ?? undefined }`.

- [ ] **Step 3: Hydrate on mount.** Make the `onMount` callback `async`. At its very start, inside a `try`, `const { messages } = await api.chatMessages();` then `setEntries(messages.map(rowToEntry));` then a `queueMicrotask` that scrolls `scrollEl` to the bottom. A `catch {}` swallows failures (network / 401) so the widget degrades to live-only — its current behavior. The existing draft-input hydration and the NATS `subscribe` calls follow, unchanged. (The loaded history and the live stream may overlap by at most one message in the gap between the API call and the subscription; the existing `appendDedup` — role + text within 500 ms — absorbs it.)

- [ ] **Step 4: Per-message timestamp.** In the entry header row (the `<div class="f-mono">` inside `<For each={entries()}>`), immediately after the `<span>{e.role}</span>`, add a `<span style={{ color: "var(--c-stone)" }}>` that renders `new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })`.

- [ ] **Step 5: Verify.** `cd web && node_modules/.bin/tsc --noEmit` → exit 0, no type errors. `cd web && node_modules/.bin/vitest run` → existing suite green.

- [ ] **Step 6: Commit.** `git add api.ts chat_log.tsx`; `git commit -m "feat(web): chat widget loads persisted history"`.

---

## Task 8: Chat widget shows the live pipeline stage

**Files:**
- Modify: `web/src/admin/chat_log.tsx`

Replace the hardcoded "thinking…" indicator with the agent's real current stage, driven by `agent.state.*` events that already flow through the WS bridge.

- [ ] **Step 1: Stage signal + subscription.** Add `const [stage, setStage] = createSignal<{ name: string; since: number } | null>(null);` near the other signals. Add a `let unsubS: (() => void) | undefined;` handle. In `onMount`, after the existing `agent.reply` subscription, subscribe to `"agent.state.*"`: read `name = String(f.payload?.state ?? "")`; if `name` is `"transcribing"`, `"thinking"`, or `"speaking"`, call `setStage` with `{ name, since: Date.now() }` but keep the existing object when the name is unchanged (so the timer does not reset) — e.g. `setStage(cur => cur?.name === name ? cur : { name, since: Date.now() })`; otherwise `setStage(null)`. In `onCleanup`, add `unsubS?.();`.

- [ ] **Step 2: Tick while staged.** The `tickTimer` interval callback currently ticks `setNowTick` only `if (pendingSince() !== null)`. Change the condition to `if (pendingSince() !== null || stage() !== null)`.

- [ ] **Step 3: Render the indicator.** Replace the pending-indicator block (the `<Show when={pendingSince()}>` that renders `lafufu · thinking…`). New condition: `<Show when={stage() ?? (pendingSince() !== null ? { name: "thinking", since: pendingSince()! } : null)}>`. Inside, render the same bubble markup as before, but the label text becomes `lafufu · {s().name}… ⧗ {fmtElapsed(nowTick() - s().since)}` (where `s` is the `<Show>` render-prop accessor). This shows the real agent stage when one is active and falls back to a `thinking` label for widget-initiated chat sends.

- [ ] **Step 4: Empty-state condition.** The "no messages yet" `<Show>` condition is currently `entries().length === 0 && pendingSince() === null`. Add `&& stage() === null` so the empty state hides while a stage is active.

- [ ] **Step 5: Verify.** `cd web && node_modules/.bin/tsc --noEmit` → exit 0. `cd web && node_modules/.bin/vitest run` → green.

- [ ] **Step 6: Commit.** `git add chat_log.tsx`; `git commit -m "feat(web): chat widget shows live pipeline stage"`.

---

## Task 9: Full verification

- [ ] **Step 1:** `uv run pytest -q` → all packages green (control, agent, shared, animator, printer).
- [ ] **Step 2:** `uv run ruff check .` → `All checks passed!`.
- [ ] **Step 3:** `cd web && node_modules/.bin/tsc --noEmit && node_modules/.bin/vitest run` → tsc exit 0, Vitest green.
- [ ] **Step 4 (optional, requires the running stack):** send a chat from the admin Chat widget; confirm a row via `GET /api/chat/messages`; refresh the admin page and confirm the conversation persists; speak a voice utterance and confirm the indicator cycles `transcribing → thinking → speaking`.

---

## Self-Review

**Spec coverage:** `ChatMessage` table → Task 1; control persists transcript/reply → Task 3; server-measured reply delay (puppet→None, 120 s guard) → Task 3; `GET /api/chat/messages` (oldest-first, clamp, auth-guarded) → Task 2; `transcribing` agent state → Tasks 4 + 6; widget history + timestamps → Task 7; widget live stage indicator → Task 8. Out-of-scope items (printed flag, pruning, conversation grouping, persisted per-stage timing) correctly absent.

**Placeholder scan:** none — every step names exact files, exact field/method definitions, and exact commands with expected output.

**Type consistency:** `ChatMessage` fields (`role`, `text`, `emotion`, `source`, `reply_delay_ms`, `created_at`) are identical across the model (T1), the router serialization (T2), `_persist_chat` (T3), and the `ChatRow` type + `rowToEntry` mapper (T7). `run_one_cycle(publish_listening=...)` is defined in T5 and called in T6. `_compute_reply_delay_ms` / `_persist_chat` / `_last_transcript_at` are defined and used consistently in T3.
