# Chat Persistence — Design

**Date:** 2026-05-20
**Status:** Approved (brainstorm) → ready for implementation plan

## Goal

Persist Lafufu's conversations to the control database, and upgrade the admin
"Chat" widget to load that history instead of starting empty on every refresh.

## Background

Lafufu interactions are one-shot: a trigger word starts a single exchange — the
visitor speaks, Lafufu replies (and may print a fortune card). The agent
publishes each exchange over NATS as `agent.transcript` (what the visitor said)
and `agent.reply` (what Lafufu said). The control service's WebSocket bridge
only fans these out live to browsers; nothing is stored.

Consequently the admin Chat widget (`web/src/admin/chat_log.tsx`) is live-only:
its `entries` list starts empty on every mount and a page refresh loses the
whole conversation. There is no record of past readings.

## Scope

In scope:

- A `ChatMessage` table in the control DB.
- The control service persisting every `agent.transcript` / `agent.reply` to it.
- A read endpoint `GET /api/chat/messages`.
- Upgrading `chat_log.tsx` to hydrate from that endpoint and show per-message
  timestamps.

Out of scope (deliberate — may be added later):

- No "was it printed" flag — the logged reply text is the card's content.
- No retention/pruning — the log grows; the widget loads the most recent 100.
- No multi-turn "conversation" grouping for LLM memory — a flat message log;
  grouping (a `conversation_id` column) can be added when memory is built.
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
| `created_at` | datetime | UTC, server-stamped on insert, indexed |

It is an *additive* table — `init_db`'s existing `SQLModel.metadata.create_all`
creates it on next start; no migration framework is needed. `db.py`'s `init_db`
imports the new model module; `models/__init__.py` exports `ChatMessage`.

### Persistence

`ControlService.on_startup` adds two NATS subscriptions via the existing
`nats_helper.subscribe_model`:

- `agent.transcript` (`AgentTranscript`) → insert
  `ChatMessage(role="user", text=msg.text)`.
- `agent.reply` (`AgentReply`) → insert
  `ChatMessage(role = "puppet" if source=="puppet" else "lafufu", text, emotion, source)`.

Each NATS event maps to exactly one row — no transcript↔reply pairing logic.
The insert runs via `asyncio.to_thread` so the blocking SQLite write never
stalls the event loop (consistent with the production-readiness review). A
failed insert is logged and swallowed — it must not kill the subscription or
the service. No agent changes.

### Read API

New router `packages/control/src/lafufu_control/api/routers/chat.py`, mounted at
`/api/chat` in `create_app` with the same `require_auth` dependency as the other
routers:

- `GET /api/chat/messages?limit=100` — returns the most recent `limit` messages
  (clamped 1–500), **oldest-first**, as
  `{"messages": [{id, role, text, emotion, source, created_at}]}`.

### Widget — `chat_log.tsx`

- `api.ts` gains `chatMessages()`.
- `onMount`: call `api.chatMessages()`, map rows into the existing `Entry[]`
  shape, set them as the initial `entries` — *then* subscribe to live NATS as
  today. A failed load degrades gracefully to live-only (current behavior).
- `Entry` gains an optional `id` (from the DB row). The loaded history and the
  live stream can overlap by at most one message in the millisecond gap between
  the API call and the subscription; the existing `appendDedup` (role + text
  within 500 ms) absorbs that.
- "More detailed": each message shows a compact local-time stamp derived from
  `created_at` (live messages use the arrival time). This — together with
  surviving a refresh — is the visible upgrade.

## Data flow

```
visitor speaks / admin sends chat
   → agent: agent.transcript ─┐
   → agent: agent.reply ──────┤
                              ├─→ NATS
   control.on_startup subscriptions ──→ ChatMessage rows (SQLite)
   control ws_bridge ─────────────────→ live fan-out to browsers (unchanged)

admin opens the Chat widget
   → GET /api/chat/messages ──→ recent ChatMessage rows ──→ entries[]
   → live NATS subscription ──→ appends new entries on top
```

## Error handling

- DB insert failure on persist: log a warning, continue. `subscribe_model`
  already isolates handler exceptions; the persist helper additionally guards
  its own write.
- API: empty `messages` list when the table is empty; `limit` clamped to 1–500.
- Widget: an `api.chatMessages()` failure (network / 401) is caught; the widget
  falls back to starting empty and live-only — it never breaks.

## Testing

- `_persist_message` helper: insert a row, read it back — role / text / emotion
  / source / `created_at` correct.
- Read API (FastAPI `TestClient`, as in `test_system_router.py`): seed rows,
  `GET /api/chat/messages`, assert oldest-first ordering and the `limit` clamp.
- The existing control test suite must stay green — the new additive table does
  not affect the existing tables.

## Files

Create:

- `packages/control/src/lafufu_control/models/chat.py`
- `packages/control/src/lafufu_control/api/routers/chat.py`
- `packages/control/tests/test_api_chat.py`

Modify:

- `packages/control/src/lafufu_control/models/__init__.py` — export `ChatMessage`
- `packages/control/src/lafufu_control/db.py` — `init_db` imports `chat`
- `packages/control/src/lafufu_control/api/app.py` — mount the guarded `chat` router
- `packages/control/src/lafufu_control/service.py` — persistence subscriptions + helper
- `web/src/shared/api.ts` — `chatMessages()`
- `web/src/admin/chat_log.tsx` — hydrate from the API, per-message timestamps
