# Unified Expression Registry & Live State Sync — design

> **Status:** design · **Date:** 2026-05-25

## Overview

The Lafufu pet (the "draggable tamagotchi") has the right pieces — agent, animator, draggable head/eyes/jaw, admin UI for editing expressions, a DB-backed Frame/Expression model, and a keyframe player that supports `once` / `loop` / `shuffle` / `random_walk` playback. The previous animation-system rebuild (2026-05-23 spec) landed the data-model unification: built-ins are already DB rows seeded from `packages/control/src/lafufu_control/animation/seed.py`, expressions reference `Frame` rows by name, and `idle` is already a registered expression with `playback="random_walk"` whose `steps_json` carries the `{intensity, speed, pause_chance}` config the admin UI sliders edit.

What's still fragmented or wired wrong:

1. **The agent → pose link is a no-op.** The animator subscribes to `agent.reply` (`service.py:164`), but the handler at `service.py:407` is intentionally idle with a self-CONCERN comment: `_on_agent_reply no longer maps emotion → expression`. So when the LLM emits `[disagree]`, Lafufu doesn't shake its head.
2. **No "built-in" concept on rows.** `seed.py` inserts all 8 built-in expressions and 15 built-in frames on first run, but the seed function bails if *any* row exists (`if has_frames or has_expressions: return`). Once a user creates anything, future built-in additions silently never appear. There's no `is_builtin` flag, no way to reset a corrupted built-in, no delete/rename guard.
3. **Servo ranges are still duplicated.** `packages/animator/src/lafufu_animator/pose.py:CLAMP` is the truth in Python; `web/src/pet/servo_ranges.ts` is a hardcoded copy. The two drift independently.
4. **Frontend lacks live reactivity.** Settings already publish `config.changed.<key>` over NATS, but frames and expressions don't publish any change event. Create a frame in one tab, the expression-creation dropdown in another tab still doesn't see it without a refresh.
5. **Dead code from the previous architecture.** `packages/animator/src/lafufu_animator/expressions.py` (the old offset+motion definitions and `compute_target`/`get_offsets` helpers) is only referenced by `packages/animator/tests/test_expressions.py`. It's a parallel hardcoded source of truth that no longer drives playback but still claims to.

This design fills in the agent→pose wire, adds the missing built-in lifecycle (flag, reset, delete/rename guards), unifies servo configuration, introduces a small reactive-resource pattern so list views stop going stale, and removes the dead expressions module.

## Goals

- **Agent's `[name]` tag drives the pose.** The animator's existing `_on_agent_reply` handler does a DB lookup on the parsed `emotion` field and publishes a `play_expression` intent if the name matches a row. Unknown name → no-op + log.
- **Built-ins are non-deletable, non-renameable, and resettable.** A new `is_builtin` flag on `Frame` and `Expression` enforces this. A `POST /…/reset` endpoint restores the row from the seed source.
- **Saving an expression overrides the built-in by name.** Already implicitly true (one row per name); explicitly preserved as a property of the design.
- **`idle` (random_walk) stays in the registry.** It's a built-in expression with `playback="random_walk"` and sliders that edit its config. It can be reset to defaults like any other built-in.
- **Servo config has one source of truth (`pose.py`)** exposed over the API. The duplicated `servo_ranges.ts` is deleted.
- **Frontend list resources are live.** Frames, expressions, and servo config refetch automatically when the backend publishes a `<resource>.changed` event. No more refresh-required staleness.
- **Dead code goes.** `packages/animator/src/lafufu_animator/expressions.py` and `packages/animator/tests/test_expressions.py` are removed.

## Non-goals (this build)

- No new tamagotchi gameplay (mood state, hunger, persistence-over-time). The pet's interaction model stays as it is.
- No changes to the keyframe player itself. `KeyframePlayer` already does the right thing for all four playback modes including `random_walk`; we only feed it the right rows.
- No codegen of TypeScript types from Python. The API contract is the seam; the frontend just fetches.
- No new `/pet`-page features for triggering user-created expressions. The agent→pose link is the only new triggering path in this build.
- No auth on the new endpoints; consistent with the rest of the admin surface (flagged in the production-readiness review).
- No destructive migration of the existing on-disk `dev-control.db`. The only schema changes are additive `is_builtin` columns on `Frame` and `Expression`; existing rows survive and get `is_builtin=true` backfilled where their name matches a seed entry.
- **Not in scope: renaming `idle` → `walk`.** Captured in open questions for follow-up.

## Background — current state (verified)

- **`packages/control/src/lafufu_control/animation/seed.py:25-83`** — `SEED_FRAMES` (15 entries) and `SEED_EXPRESSIONS` (8 entries: `agree`, `disagree`, `happy`, `sad`, `angry`, `surprised`, `neutral`, `idle`). The `idle` entry has `playback="random_walk"` and `steps_json = json.dumps(IDLE_RANDOM_WALK_CONFIG)` (`{intensity: 1.0, speed: 1.0, pause_chance: 0.30}`).
- **`packages/control/src/lafufu_control/animation/seed.py:86-92`** — `seed_animations` bails entirely if any row exists. Not idempotent per-row.
- **`packages/control/src/lafufu_control/models/expression.py` / `frame.py`** — `Expression(name PK, playback, default_*_ms, default_easing, steps_json, emotion, description)` and `Frame(name PK, head_lr, head_ud, eye, jaw, brow, image, description)`. No `is_builtin` field.
- **`packages/animator/src/lafufu_animator/keyframe_player.py`** — full playback engine; supports `once` / `loop` / `shuffle` / `random_walk`. The `random_walk` mode reads `payload.random_walk_config: RandomWalkConfig` (lines 144, 181-258).
- **`packages/animator/src/lafufu_animator/service.py:164`** — `_on_agent_reply` is wired as the `agent.reply` subscriber.
- **`packages/animator/src/lafufu_animator/service.py:407-413`** — handler body:
  ```python
  async def _on_agent_reply(self, subject, msg):
      # CONCERN: _on_agent_reply no longer maps emotion → expression.
      # The agent now needs to publish play_expression itself with a
      # pre-resolved AnimatorIntentPlayExpression payload.
      self._last_intent_mono = time.monotonic()
  ```
  The agent does **not** publish play_expression today either, so the link is broken in both directions; this design closes it inside the animator.
- **`web/src/admin/expressions_section.tsx:52-53, 273-317`** — admin UI already has the `random_walk` sliders (`intensity`, `speed`, `pause_chance`) wired through the `random_walk_config` field on `ExpressionDTO`.
- **`web/src/pet/servo_ranges.ts:6-12`** — hardcoded `{head_lr: [1828,2298], head_ud: [2885,3278], eye: [1995,2085], jaw: [1594,1811], brow: [2056,2087]}`. Duplicates `pose.py:CLAMP`.
- **`packages/control/src/lafufu_control/api/routers/settings.py:1`** — settings router already publishes `config.changed.<key>` to NATS on PATCH/PUT. The pattern exists; we extend it.
- **`packages/agent/src/lafufu_agent/emotion_parser.py`** — extracts `[name]` from LLM output. `_VALID_EMOTIONS` set defaults unknown names to `neutral`. This fallback hides typos and silently masks the absence of a registered expression — we drop it.
- **`packages/animator/src/lafufu_animator/expressions.py`** — dead module; only `tests/test_expressions.py` imports it. Removed in this build.

## Data model

Two existing tables, one new field each. No new tables.

### `Frame` — add `is_builtin: bool`
| field | type | notes |
|---|---|---|
| `name` | str PK, max 100 | existing |
| `head_lr`, `head_ud`, `eye`, `jaw`, `brow` | int | existing |
| `image`, `description` | str ⏵ nullable | existing |
| **`is_builtin`** | **bool, default false** | **new.** Built-in frames cannot be deleted or renamed; can be edited; can be reset. |

### `Expression` — add `is_builtin: bool`
| field | type | notes |
|---|---|---|
| `name` | str PK, max 100 | existing; this is what the agent's `[name]` tag resolves against |
| `playback` | str | existing; `once` / `loop` / `shuffle` / `random_walk` |
| `default_duration_ms`, `default_delay_ms`, `default_easing` | existing | |
| `steps_json` | str (JSON) | existing; for keyframe playback, `[{frame, duration_ms?, delay_ms?, easing?}, …]`. For `random_walk` playback, `{intensity, speed, pause_chance}`. |
| `emotion` | str ⏵ nullable | existing; same role `name` now plays. Stop reading it; mark deprecated in code comment for follow-up removal. |
| `description` | str ⏵ nullable | existing |
| **`is_builtin`** | **bool, default false** | **new.** Same semantics as on `Frame`. |

### Seed source

`packages/control/src/lafufu_control/animation/seed.py` is the existing source of factory defaults — **kept and extended.** Changes:

1. The names listed in `SEED_FRAMES` and `SEED_EXPRESSIONS` become the canonical "is_builtin" set. The seed file is the only authoritative list of which names are built-in.
2. `seed_animations()` becomes per-row idempotent: instead of bailing if *any* row exists, it upserts each seed entry **only if a row of that name does not exist**, and sets `is_builtin=true` on the inserted row. This unblocks future built-in additions and lets a deleted DB re-seed correctly.
3. On startup, also backfill `is_builtin=true` on any existing rows whose names match the seed set (one-time migration; the seed function itself does this if it finds a matching pre-existing row that lacks the flag).
4. The seed file gains two pure helpers, `apply_frame_seed(s, name)` and `apply_expression_seed(s, name)`, that overwrite a single existing row from the seed entry. These are the implementations behind the reset endpoints.

`packages/animator/src/lafufu_animator/expressions.py` and `packages/animator/tests/test_expressions.py` are **deleted** in this build. They were the prior offset+motion source of truth, no longer wired into playback.

### Reset, delete, rename

- `POST /animator/expressions/{name}/reset` — only valid if the row has `is_builtin=true`. Calls `apply_expression_seed(s, name)` plus seeds any of the expression's referenced frame names that don't exist (defends against a user having deleted a built-in frame the seed expression references — though that's blocked too once frames have the flag).
- `POST /animator/frames/{name}/reset` — same shape, restores the frame pose from the seed.
- `DELETE /animator/expressions/{name}` and `DELETE /animator/frames/{name}` — return **400** if `is_builtin=true`. Otherwise normal delete.
- `PATCH /animator/expressions/{name}` and `PATCH /animator/frames/{name}` — return **400** if the patch attempts to change `name` on an `is_builtin=true` row. All other fields remain editable, including `steps_json` (so the idle slider edits still flow through).

## Agent → pose link

The animator's `_on_agent_reply` handler at `service.py:407` is filled in. Replacing the current no-op with:

```python
async def _on_agent_reply(self, subject, msg: schemas.AgentReply) -> None:
    self._last_intent_mono = time.monotonic()
    name = (msg.emotion or "").strip()
    if not name:
        return                          # parser returned nothing; current pose holds
    row = self._expression_loader.get_by_name(name)
    if row is None:
        self.log.warning("agent.reply emotion=%r not found in expression registry", name)
        return                          # no fallback to neutral; current pose holds
    intent = self._build_play_intent(row)   # same payload shape the admin ▶ Play uses
    await self._handle_play_expression(intent)   # reuse existing path
```

Three deliberate choices:

- **No fallback to `neutral`.** Unknown emotion → no-op. The current pose / idle continues. Fallback-to-neutral hides typos. We'd rather see the log line.
- **Reuse the existing `play_expression` intent path.** This is the same code path the admin ▶ Play button uses. One playback entry point, well-tested, already handles serialization against concurrent intents.
- **`emotion_parser` cleanup.** The `_VALID_EMOTIONS` set and its default-to-`neutral` branch are deleted. The parser just extracts whatever is in `[brackets]`. The DB lookup is the only validity check.

The `# CONCERN:` comment in `service.py` is removed; this implementation IS the resolution.

How the animator gets a DB connection — the animator service is a separate process from `lafufu_control` (which owns the DB). The cleanest seam is the existing control-side router: the animator calls a small `GET /animator/expressions/{name}` endpoint that returns the row already adapted to `AnimatorIntentPlayExpression` shape, then publishes that as an intent to its own bus. This keeps the animator process database-free and matches how the admin ▶ Play button already works.

## Servo config endpoint

New endpoint **`GET /animator/config`**:

```json
{
  "ranges":         {"head_lr": [1828, 2298], "head_ud": [2885, 3278], "eye": [1995, 2085], "jaw": [1594, 1811], "brow": [2056, 2087]},
  "idle_defaults":  {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075},
  "idle_overrides": {"head_lr": 2070}
}
```

- `ranges` reads `pose.py:CLAMP` — the single Python source of truth, unchanged.
- `idle_defaults` reads `pose.py:DXL_*_IDLE_POS`.
- `idle_overrides` reads the settings table for `animator.<servo>.default` keys.

**Frontend changes:**
- New `useServoConfig()` resource (see "Frontend reactivity pattern" below). One fetch on app load; live-updates on `config.changed.<key>` events that match `animator.*`.
- `web/src/pet/servo_ranges.ts` is **deleted**. `web/src/pet/head_drag.ts` and `web/src/pet/pet.tsx` import ranges from the resource.
- The drag math in `head_drag.ts` becomes a pure function taking `ranges` as a parameter; the resource feeds it at call time.

## NATS change events

`config.changed.<key>` already publishes from `packages/control/src/lafufu_control/api/routers/settings.py` on PATCH/PUT. We extend the same pattern to frame and expression mutations:

| topic | when published | payload |
|---|---|---|
| `expressions.changed` | any expression CRUD or reset | `{kind: "create"\|"update"\|"delete"\|"reset", name: string}` |
| `frames.changed` | any frame CRUD or reset | `{kind, name}` |
| `config.changed.<key>` | **(existing)** any settings write | existing payload |

Payloads are intentionally tiny. Listeners refetch the whole list — no clever deltas. If list sizes ever grow enough to make full refetch painful, the topic schema can grow `before`/`after` fields without breaking subscribers.

## Frontend reactivity pattern

New helper at `web/src/shared/reactive_resource.ts`:

```ts
import { createResource, onCleanup, onMount } from "solid-js";
import { nats } from "./nats_ws";

export function createReactiveResource<T>(
  fetchFn: () => Promise<T>,
  topics: string[],
) {
  const [data, { refetch }] = createResource(fetchFn);
  onMount(() => {
    const unsubs = topics.map((t) => nats.subscribe(t, () => refetch()));
    onCleanup(() => unsubs.forEach((u) => u()));
  });
  return data;
}
```

Adopted by every list resource that today goes stale:

- `expressions` resource in `expressions_section.tsx` → `["expressions.changed"]` (fixes the dropdown-not-updating bug)
- `frames` resource (wherever the frame-picker is rendered) → `["frames.changed"]`
- New `useServoConfig()` → `["config.changed.animator.head_lr", "config.changed.animator.head_ud", "config.changed.animator.eye", "config.changed.animator.jaw", "config.changed.animator.brow"]`. Or, equivalently, a single wildcard subscription if `nats_ws` supports it.
- Where useful, an `animator state` resource (current pose / active expression name) — same pattern listening to `animator.pose` and `animator.expression.*` events. Adoption optional in this build.

## Cleanups bundled with the change

These belong to the same scope ("code simplification pass on touched files"):

- `web/src/pet/servo_ranges.ts` — **delete.**
- `packages/animator/src/lafufu_animator/expressions.py` — **delete** (offset+motion module, unused except by its test).
- `packages/animator/tests/test_expressions.py` — **delete** (only consumer of the above).
- `packages/agent/src/lafufu_agent/emotion_parser.py` — delete `_VALID_EMOTIONS` and the default-to-`neutral` branch.
- `Expression.emotion` — mark deprecated in a code comment; follow-up-remove in a later migration.
- `pet.tsx`, `head_drag.ts` — fold servo-range references into the new resource; drop dead constants.

## Migration order

Each step is independently mergeable; there is no flag-day.

1. **Schema migration** — add nullable `is_builtin` column to `Frame` and `Expression` (default false). Existing rows survive untouched.
2. **Seed function rewrite** — make `seed_animations()` per-row idempotent, set `is_builtin=true` on the inserted rows, and backfill the flag on any pre-existing rows whose names match the seed set. After this step, the DB has the right flag state.
3. **Lifecycle endpoints** — add `/reset` endpoints; add 400-on-builtin guards to existing DELETE and rename-via-PATCH paths.
4. **Frontend Reset/Delete UI** — show a "Reset to defaults" button on built-in rows; hide the Delete button on built-in rows. Use the new endpoints.
5. **Agent → pose wiring** — fill in the animator's `_on_agent_reply` handler; clean `emotion_parser`. Delete the `# CONCERN:` comment.
6. **Servo config endpoint + frontend fetch** — ship `GET /animator/config`, ship `useServoConfig()`, delete `servo_ranges.ts`.
7. **Reactive resource helper + adoption** — ship the helper. Migrate the `expressions` and `frames` resources first (directly addresses the "new frame doesn't appear in expression-creation dropdown" bug).
8. **Backend change events** — publish `expressions.changed` and `frames.changed` from the CRUD handlers. Live reactivity flips on at this step.
9. **Dead-code removal** — delete `expressions.py` and its test.
10. **Code-simplifier pass** on the touched files.

## Testing strategy

- **Seed idempotence test.** Insert one user-created expression, run `seed_animations()`, assert the user row is untouched and all seed names are present with `is_builtin=true`. Re-run; assert no duplicate work.
- **Reset endpoint test.** Edit a built-in expression's `steps_json` via PATCH; call `/reset`; assert the row matches the seed entry exactly. Repeat for a built-in frame. Confirm `/reset` returns 400 for a non-built-in row.
- **Guard tests.** DELETE on a built-in returns 400; PATCH with a `name` change on a built-in returns 400.
- **Agent → pose integration test.** Publish a fake `agent.reply` with `emotion="disagree"` on a test NATS bus; assert an `animator.intent.play_expression {name: "disagree"}` is emitted. Repeat with `emotion="zzz_unknown"` and assert no intent is emitted and a warning is logged.
- **`idle` round-trip.** Edit the idle expression's `random_walk_config` via PATCH (intensity 1.0 → 0.5); confirm the next idle playback uses the new value (smoke test on a running animator). Then call `/reset`; confirm intensity is back to 1.0 and the sliders reflect it after the next `expressions.changed` event.
- **Frontend live-update smoke.** With two browser tabs open on the admin, create a frame in tab A; assert tab B's frame-picker dropdown contains the new frame without a refresh, within the round-trip of `frames.changed`.

## Risks & open questions

- **`name` collisions on user save.** If the agent emits `[my-custom-name]` and a user has saved an expression named `my-custom-name`, it plays — by design. The admin UI's expression-list should make this connection obvious (e.g., a small "agent-callable" tag on every row, since every row is now agent-callable). Treated as a copy/UI polish task, not a blocker.
- **The animator → DB seam.** The animator service is a separate process; this design has it call `GET /animator/expressions/{name}` from the control plane to resolve the row. That adds an in-process HTTP hop per `agent.reply`. Acceptable for now (the rate is low — once per agent turn), but if it becomes a bottleneck or coupling smell, the cleaner alternative is to have the control plane do the resolution and publish `animator.intent.play_expression` directly (mirrors how the admin ▶ Play button works). Decision deferred to implementation — pick whichever is simpler when the code is in front of us.
- **Rename `idle` → `walk`?** The user has floated this. It's a renaming-only change but `idle` is a built-in name and the seed file references it. Not in scope here; if desired, do it as a one-line follow-up that updates the seed + writes a one-shot DB rename for existing rows. Captured for follow-up.
- **The `Expression.emotion` column.** Deprecated, not deleted in this build. A follow-up cleanup removes the column once we're confident nothing reads it.
- **No auth.** The new `/animator/config`, `/reset`, and CRUD endpoints inherit the unauthenticated admin surface. Flagged in the production-readiness review; not addressed here.
