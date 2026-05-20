# Chat Persistence & Interaction Visibility — Design

**Date:** 2026-05-20
**Status:** Approved (brainstorm) → ready for implementation plan

## Goal

Persist Lafufu's conversations to the control database, record how long each
reply took, and upgrade the admin "Chat" widget to load that history and show
the live pipeline stage (transcribing / thinking / speaking) instead of a
hardcoded "thinking".

## Background

Lafufu interactions are one-shot: a trigger word starts a single exchange — the
visitor speaks (or an operator types), Lafufu replies (and may print a fortune
card). The agent runs one cycle and publishes it over NATS as `agent.transcript`
(what the visitor said) and `agent.reply` (what Lafufu said); voice and text
interactions use the same cycle and the same events. The control service's
WebSocket bridge only fans these out live to browsers; nothing is stored.

Consequently the admin Chat widget (`web/src/admin/chat_log.tsx`) is live-only:
its `entries` list starts empty on every mount and a refresh loses the whole
conversation. The round-trip time it shows (`elapsedMs`) is measured in the
browser, only for widget-initiated chats, and is never saved. While a cycle
runs, the widget shows a hardcoded "thinking…" with no indication of the actual
stage.

## Scope

In scope:

- A `ChatMessage` table in the control DB; the control service persists every
  `agent.transcript` / `agent.reply` (voice and text alike).
- A server-measured reply delay, persisted per reply.
- A read endpoint `GET /api/chat/messages`.
- Upgrading `chat_log.tsx`: load history on mount, show per-message timestamps,
  and show the live pipeline stage with a timer.
- A new `transcribing` agent state so STT is a visible step.

Out of scope (deliberate — may be added later):

- No "was it printed" flag — the logged reply text is the card's content.
- No retention/pruning — the log grows; the widget loads the most recent 100.
- No multi-turn "conversation" grouping for LLM memory — a flat message log;
  a `conversation_id` column can be added when memory is built.
- No *persisted* per-stage breakdown — only the total reply delay is stored;
  the per-stage view is live-only.
- No new admin page — the existing widget is the view.

## Design

### Data model — `ChatMessage`

New SQLModel table (`packages/control/src/lafufu_control/models/chat.py`):

| column | type | notes |
|---|---|---|
| `id` | int, PK | autoincrement |
| `role` | str(16) | `user` \| `lafufu` \| `puppet` |
| `text` | str(8000) | the message text |
| `emotion` | str(32) \| None | reply emotion tag; `None` for user messages |
| `source` | str(32) \| None | reply source (`llm`/`puppet`/`system`); `None` for user messages |
| `reply_delay_ms` | int \| None | server-measured transcript→reply time; set on LLM/system replies, `None` for user and puppet rows |
| `created_at` | datetime | UTC, server-stamped on insert, indexed |

It is an *additive* table — `init_db`'s existing `SQLModel.metadata.create_all`
creates it on next start; no migration framework is needed. `db.py`'s `init_db`
imports the new model module; `models/__init__.py` exports `ChatMessage`.

### Persistence & reply delay

`ControlService.on_startup` adds two NATS subscriptions via the existing
`nats_helper.subscribe_model`. The persistence layer holds one piece of state,
`_last_transcript_at: datetime | None`:

- `agent.transcript` (`AgentTranscript`) → set `_last_transcript_at = now`;
  insert `ChatMessage(role="user", text=msg.text)`.
- `agent.reply` (`AgentReply`) → insert
  `ChatMessage(role = "puppet" if source=="puppet" else "lafufu", text, emotion, source, reply_delay_ms)`.
  `reply_delay_ms` is `(now - _last_transcript_at)` in milliseconds **when** the
  reply is non-puppet and `_last_transcript_at` is set and recent (gap < 120 s);
  otherwise `None`. `_last_transcript_at` is cleared after a reply consumes it.

Temporal pairing is safe because the agent's `_cycle_lock` serializes cycles —
a reply always follows its own transcript with no interleaving. Each NATS event
still produces exactly one row; the delay is the only thing that links them.

Each insert runs via `asyncio.to_thread` so the blocking SQLite write never
stalls the event loop (consistent with the production-readiness review). A
failed insert is logged and swallowed — it must not kill the subscription or
the service. No agent changes for persistence.

### Read API

New router `packages/control/src/lafufu_control/api/routers/chat.py`, mounted at
`/api/chat` in `create_app` with the same `require_auth` dependency as the other
routers:

- `GET /api/chat/messages?limit=100` — the most recent `limit` messages
  (clamped 1–500), **oldest-first**, as
  `{"messages": [{id, role, text, emotion, source, reply_delay_ms, created_at}]}`.

### Live pipeline-stage display

**Agent** — add a `transcribing` state. The voice cycle currently publishes
`listening` → `thinking` → `speaking` → `idle`, with STT folded inside the
"listening" phase. Add `transcribing`, published while STT runs (after recording
ends, before the LLM call), so transcription is a distinct visible step. This
requires adding `transcribing` to the `AgentStateName` literal in
`packages/shared/src/lafufu_shared/schemas.py`. (STT runs inside the mic
component today; exposing a clean seam for the state publish is an
implementation-plan detail.) The new value is additive — consumers that don't
recognize it fall through to their default.

**Widget** — `chat_log.tsx` subscribes to `agent.state.*` (already available via
the WS bridge). Its pending indicator shows the current stage label
(`transcribing… / thinking… / speaking…`) with a timer that resets on each
stage change, replacing the hardcoded "thinking…". This is live-only — no
per-stage data is persisted.

### Widget — history & timestamps

- `api.ts` gains `chatMessages()`.
- `onMount`: call `api.chatMessages()`, map rows into the existing `Entry[]`
  shape (carrying `reply_delay_ms` into the displayed delay), set them as the
  initial `entries` — *then* subscribe to live NATS as today. A failed load
  degrades gracefully to live-only (current behavior).
- `Entry` gains an optional `id` (from the DB row). The loaded history and the
  live stream can overlap by at most one message in the millisecond gap between
  the API call and the subscription; the existing `appendDedup` (role + text
  within 500 ms) absorbs that.
- Each message shows a compact local-time stamp derived from `created_at` (live
  messages use arrival time). Reply rows show their delay: `reply_delay_ms` for
  loaded history, the existing client `elapsedMs` for live widget chats.

## Data flow

```
visitor speaks / admin sends chat
   → agent: agent.state.transcribing / .thinking / .speaking ─┐
   → agent: agent.transcript ─────────────────────────────────┤
   → agent: agent.reply ──────────────────────────────────────┤
                                                              ├─→ NATS
   control.on_startup subscriptions ──→ ChatMessage rows + reply_delay_ms (SQLite)
   control ws_bridge ─────────────────→ live fan-out to browsers (unchanged)

admin opens the Chat widget
   → GET /api/chat/messages ──→ recent ChatMessage rows ──→ entries[]
   → live agent.transcript / agent.reply ──→ appends new entries
   → live agent.state.* ──→ current-stage indicator with a timer
```

## Error handling

- DB insert failure on persist: log a warning, continue. `subscribe_model`
  already isolates handler exceptions; the persist helper additionally guards
  its own write.
- Reply delay: `None` whenever there is no recent triggering transcript (puppet
  replies, or a gap over 120 s) — never a bogus number.
- API: empty `messages` list when the table is empty; `limit` clamped to 1–500.
- Widget: an `api.chatMessages()` failure (network / 401) is caught; the widget
  falls back to starting empty and live-only — it never breaks. An unrecognized
  `agent.state` value shows no stage label rather than erroring.

## Testing

- `_persist_message` / reply-delay logic: a transcript then a reply yields a
  reply row with a populated `reply_delay_ms`; a puppet reply yields `None`; a
  reply with no prior transcript yields `None`.
- Read API (FastAPI `TestClient`, as in `test_system_router.py`): seed rows,
  `GET /api/chat/messages`, assert oldest-first ordering and the `limit` clamp.
- The existing control and agent test suites must stay green — the additive
  table and the additive `transcribing` state do not affect existing behavior.

## Files

Create:

- `packages/control/src/lafufu_control/models/chat.py`
- `packages/control/src/lafufu_control/api/routers/chat.py`
- `packages/control/tests/test_api_chat.py`

Modify:

- `packages/control/src/lafufu_control/models/__init__.py` — export `ChatMessage`
- `packages/control/src/lafufu_control/db.py` — `init_db` imports `chat`
- `packages/control/src/lafufu_control/api/app.py` — mount the guarded `chat` router
- `packages/control/src/lafufu_control/service.py` — persistence subscriptions, reply-delay measurement
- `packages/shared/src/lafufu_shared/schemas.py` — add `transcribing` to `AgentStateName`
- `packages/agent/src/lafufu_agent/` — publish the `transcribing` state around STT (exact site per the plan)
- `web/src/shared/api.ts` — `chatMessages()`
- `web/src/admin/chat_log.tsx` — hydrate from the API, per-message timestamps, live stage indicator
