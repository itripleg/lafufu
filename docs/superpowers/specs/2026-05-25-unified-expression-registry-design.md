# Unified Expression Registry & Live State Sync — design

> **Status:** design · **Date:** 2026-05-25

## Overview

The Lafufu pet (the "draggable tamagotchi") has the right pieces — agent, animator, draggable head/eyes/jaw, admin UI for editing expressions — but the state is fragmented across at least four boundaries:

1. **Emotion names** are defined separately in `packages/animator/.../expressions.py` (hardcoded dict), `packages/agent/.../emotion_parser.py` (validity set), and `web/src/admin/expressions_section.tsx` (dropdown options + a phantom `idle` entry). They drift.
2. **Servo ranges** are defined in `packages/animator/.../pose.py:CLAMP` and re-declared in `web/src/pet/servo_ranges.ts`. They drift.
3. **Built-in expressions** (hardcoded Python dict, offset+sin-motion shape) and **user-saved expressions** (DB rows, keyframe shape) are parallel universes. Saving a row named `happy` does not override the built-in `happy`.
4. **Agent → animator routing is missing.** The LLM emits `[disagree]`; the parser pulls the name out and stuffs it into `AgentReply` for the chat label and TTS; **nothing publishes a play_expression intent for it**. Labubu literally cannot react to its own words.

The frontend also lacks live reactivity: creating a frame in one tab does not make it appear in another tab's expression-creation dropdown without refresh.

This design unifies the data model, wires the agent's emotion tags through to the animator, exposes servo configuration over the API, and introduces a small reactive-resource pattern so every list-view in the UI reflects backend changes immediately.

## Goals

- **One source of truth for expressions.** The DB `Expression` table is canonical; built-ins are seeded rows. Saving an expression named `happy` overrides the built-in `happy` because there is only one row.
- **Built-ins are non-deletable but resettable.** A `Reset to defaults` button on built-in rows restores the factory definition from a single Python seed file.
- **Built-ins use the same keyframe schema as user-saved expressions.** No more dual-shape playback engine.
- **Agent's `[name]` tag drives the pose.** Animator subscribes to `agent.reply`, looks up `emotion` in the DB, and plays the matching expression. Unknown name → no-op + log.
- **Servo config has one source of truth (`pose.py`)** and the frontend fetches it over the API. The hardcoded `servo_ranges.ts` is deleted.
- **Frontend list resources are live.** Frames, expressions, and servo config all refetch automatically when the backend publishes a `<resource>.changed` event. No more refresh-required staleness.
- **User-created expressions remain supported** (they are the "additional functionality" referenced for future `/pet`-area triggering — not in scope here, but the data model accommodates them).

## Non-goals (this build)

- No new tamagotchi gameplay (mood state, hunger, persistence-over-time, ambient behaviors). The pet's interaction model stays as it is.
- No rebuild of the playback engine beyond removing the offset+motion code path. Keyframe playback already works and is keeping its current shape.
- No codegen of TypeScript types from Python. The API contract is the seam; frontend just fetches.
- No new `/pet`-page features for triggering user-created expressions. The agent→pose link is the only new triggering path in this build.
- No auth on the new endpoints; consistent with the rest of the admin surface (flagged for the production-readiness work).
- No destructive migration of the existing on-disk `dev-control.db`. The only schema change is an additive `is_builtin` column on `Frame` and `Expression`; existing rows survive untouched. Built-ins are upserted into the unseeded DB on next start.

## Background — what exists today

Confirmed by inspection of the current tree:

- **`packages/animator/src/lafufu_animator/expressions.py:54-110`** — hardcoded `_EXPRESSIONS` dict: 8 emotions, each with `ServoOffsets` (deltas from idle) and optional sinusoidal `motion`. Compiled into a playback at runtime.
- **`packages/agent/src/lafufu_agent/emotion_parser.py`** — `_VALID_EMOTIONS` set + regex extraction. Unknown → `neutral`. Result handed to `speak()` and stuffed into `AgentReply` as `emotion: str`.
- **`packages/control/src/lafufu_control/models/expression.py`** — `Expression(name PK, playback, default_*_ms, default_easing, steps_json, emotion, description)`. `steps_json` is `[{frame, duration_ms?, delay_ms?, easing?}, …]` — each step references a `Frame` row by name.
- **`packages/control/src/lafufu_control/models/frame.py`** — `Frame(name PK, head_lr, head_ud, eye, jaw, brow, image, description)`. Named pose.
- **`packages/animator/src/lafufu_animator/pose.py`** — `CLAMP` ranges, `DXL_*_IDLE_POS` constants. Sole source of truth in Python.
- **`web/src/pet/servo_ranges.ts`** — hardcoded duplicate of `CLAMP`. Drift risk.
- **`web/src/admin/expressions_section.tsx:54-63`** — `EMOTIONS` array: `idle / agree / disagree / happy / sad / angry / surprised / neutral`. `idle` exists nowhere in Python.
- **NATS topics in use:** `animator.pose`, `animator.intent.*`, `agent.reply`, `config.changed.*`. The `config.changed.*` family is the seam we'll extend for live frontend updates.

Recent commits (`0fd3797`, `80c8c66`, `30130b3`, `b9a2a74`, `d1a9ac9`) are motion-tuning and drag fixes; they don't touch the boundaries this design changes.

## Data model

Two existing tables, both gaining a single field:

### `Frame` — add `is_builtin: bool`
| field | type | notes |
|---|---|---|
| `name` | str PK, max 100 | existing |
| `head_lr`, `head_ud`, `eye`, `jaw`, `brow` | int | existing |
| `image`, `description` | str ⏵ nullable | existing |
| **`is_builtin`** | **bool, default false** | **new.** Built-in frames cannot be deleted or renamed; can be edited; can be reset. |

### `Expression` — add `is_builtin: bool`; `emotion` deprecated
| field | type | notes |
|---|---|---|
| `name` | str PK, max 100 | existing; this is what the agent's `[name]` tag resolves against |
| `playback`, `default_duration_ms`, `default_delay_ms`, `default_easing` | existing | |
| `steps_json` | str (JSON) | existing; `[{frame, duration_ms?, delay_ms?, easing?}, …]` — refers to Frame rows by name |
| `emotion` | str ⏵ nullable | **deprecated.** Same role `name` now plays. Stop reading it; remove in a follow-up migration after one release cycle of compatibility. |
| `description` | str ⏵ nullable | existing |
| **`is_builtin`** | **bool, default false** | **new.** Same semantics as on `Frame`. |

### Built-in seed source

A new file `packages/animator/src/lafufu_animator/builtin_expressions.py` is the single Python-side source of factory defaults. It contains two pure-data lists:

```python
BUILTIN_FRAMES: list[FrameSeed] = [
    {"name": "_idle", "head_lr": 2063, "head_ud": 3082, "eye": 2040, "jaw": 1700, "brow": 2071},
    {"name": "_nod-down", ...},
    {"name": "_shake-left", ...},
    {"name": "_shake-right", ...},
    # …one per pose used by built-in expressions
]

BUILTIN_EXPRESSIONS: list[ExpressionSeed] = [
    {"name": "neutral",   "playback": "once", "steps": [{"frame": "_idle", "duration_ms": 200}]},
    {"name": "agree",     "playback": "once", "steps": [
        {"frame": "_idle"}, {"frame": "_nod-down"}, {"frame": "_idle"}, {"frame": "_nod-down"}, {"frame": "_idle"}]},
    {"name": "disagree",  "playback": "once", "steps": [
        {"frame": "_idle"}, {"frame": "_shake-left"}, {"frame": "_shake-right"}, {"frame": "_shake-left"}, {"frame": "_idle"}]},
    {"name": "happy", ...},
    {"name": "sad", ...},
    {"name": "angry", ...},
    {"name": "surprised", ...},
]
```

Frame names for built-ins are prefixed `_` by convention to make them visually distinct in the admin UI (and signal "edit me carefully, an expression depends on this"). The convention is documentation only — the `is_builtin` flag is the actual enforcement.

The sinusoidal motion the old `_EXPRESSIONS` dict used (agree-nod, disagree-shake) is **pre-baked into discrete keyframes** in the seed. A small helper `bake_sin_axis(name_prefix, axis, amplitude, freq_hz, duration_s, frames=8)` lives in the seed file and produces the frame+step entries. The runtime no longer evaluates sinusoidal motion.

### Seed flow

On `lafufu_control` startup:
1. For each entry in `BUILTIN_FRAMES`: **upsert by name only if absent.** Set `is_builtin=true` on the row. Never clobber an existing row on every start.
2. For each entry in `BUILTIN_EXPRESSIONS`: same upsert-if-absent. Set `is_builtin=true`.

Because seeding is upsert-if-absent, a user's edits to a built-in row persist across restarts.

### Reset, delete, rename

- `POST /animator/expressions/{name}/reset` — only allowed if the row has `is_builtin=true`. Restores `steps_json`, `playback`, `default_*_ms`, `default_easing`, `description` from the seed entry of the same `name`. The seed for missing rows is also re-applied (e.g., if the user previously edited and the built-in frame definitions changed).
- `POST /animator/frames/{name}/reset` — same shape, restores the frame pose from the seed.
- `DELETE /animator/expressions/{name}` and `DELETE /animator/frames/{name}` — return **400** if `is_builtin=true`. Otherwise normal delete.
- `PATCH /animator/expressions/{name}` and `PATCH /animator/frames/{name}` — return **400** if the patch attempts to change `name` on an `is_builtin=true` row. Other fields may be edited.

### Animator playback simplification

After seeding, the animator's intent-handler resolves expressions **exclusively** by reading the DB. The hardcoded `_EXPRESSIONS` dict in `expressions.py` and the offset+motion compilation path are **deleted**. Playback is keyframe-only: read `Expression` row → resolve `steps_json` frame names against `Frame` table → drive `PoseSmoother`.

This is the biggest visible-regression risk in the migration — see the testing strategy below.

## Agent → pose link

A new NATS subscriber in the animator service listens to `agent.reply`. On message:

```
emotion = msg.emotion              # already parsed by emotion_parser
if not emotion:                    # parser returned nothing
    return                          # current pose / idle holds
row = db.get(Expression, name=emotion)
if row is None:
    log.warning("agent.reply emotion=%r not found in expression registry", emotion)
    return                          # no fallback to neutral; current pose holds
publish("animator.intent.play_expression", {"name": emotion})
```

Two deliberate choices:
- **No fallback to `neutral`.** Unknown emotion → no-op. The current pose / idle continues. (Fallback-to-neutral hides typos and silently masks the absence of a registered expression. We'd rather see the log line.)
- **Reuse the existing `play_expression` intent path.** This is the same path the admin UI's ▶ Play button uses. One playback entry point, well-tested.

The `emotion_parser._VALID_EMOTIONS` set and its default-to-`neutral` fallback are **removed**. The parser now just extracts whatever is in `[brackets]`. The DB lookup is the validity check.

Edge case — the agent emits a reply on every turn, so the animator subscriber must not enqueue rapid-fire intents that fight each other. The animator's existing intent-handler already serializes plays; we rely on that. If a new `play_expression` arrives mid-playback, the existing handler interrupts cleanly. No new debouncing needed.

## Servo config endpoint

New endpoint **`GET /animator/config`**:

```json
{
  "ranges":         {"head_lr": [1828, 2298], "head_ud": [2885, 3278], "eye": [1995, 2085], "jaw": [1594, 1811], "brow": [2056, 2087]},
  "idle_defaults":  {"head_lr": 2063, "head_ud": 3082, "eye": 2040, "jaw": 1700, "brow": 2071},
  "idle_overrides": {"head_lr": 2070}
}
```

- `ranges` reads `pose.py:CLAMP` — the single Python source of truth, unchanged.
- `idle_defaults` reads `pose.py:DXL_*_IDLE_POS`.
- `idle_overrides` reads the settings table for `animator.<servo>.default` keys.

**Frontend changes:**
- New `useServoConfig()` resource (see "Frontend reactivity pattern" below). One fetch on app load; live-updates on `config.changed.servos`.
- `web/src/pet/servo_ranges.ts` is **deleted**. `web/src/pet/head_drag.ts` and `web/src/pet/pet.tsx` import ranges from the resource.
- The drag math in `head_drag.ts` becomes a pure function taking `ranges` as a parameter; the resource feeds it at call time.

## NATS change events

The backend publishes when these mutate:

| topic | when published | payload |
|---|---|---|
| `expressions.changed` | any expression CRUD or reset | `{kind: "create"\|"update"\|"delete"\|"reset", name: string}` |
| `frames.changed` | any frame CRUD or reset | `{kind, name}` |
| `config.changed.servos` | any settings write under `animator.<servo>.*` | `{}` (frontend just refetches the config blob) |

Payloads are intentionally tiny. Listeners refetch the whole list — we don't try to be clever about deltas yet. If list sizes grow enough that this becomes a problem, the topic schema can grow `before`/`after` fields without breaking existing subscribers.

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

- `expressions` resource in `expressions_section.tsx` → `["expressions.changed"]`
- `frames` resource (wherever the frame-picker is rendered) → `["frames.changed"]`
- New `useServoConfig()` → `["config.changed.servos"]`
- Where useful, an `animator state` resource (current pose / active expression name) → `["animator.pose", "animator.expression.start", "animator.expression.end"]` so the UI can show "currently playing: agree".

The `EMOTIONS` array in `expressions_section.tsx` is **deleted**. The dropdown for "which expression is this bound to" reads its options from the live expressions list (the registry). The phantom `idle` entry disappears with the array.

## Cleanups bundled with the change

These belong to the same scope ("code simplification pass on touched files") rather than a separate ticket:

- `web/src/pet/servo_ranges.ts` — **delete.**
- `web/src/admin/expressions_section.tsx` — delete the `EMOTIONS` constant; drive the dropdown from the live registry.
- `packages/agent/src/lafufu_agent/emotion_parser.py` — delete `_VALID_EMOTIONS` and the default-to-`neutral` branch.
- `packages/animator/src/lafufu_animator/expressions.py` — delete the `_EXPRESSIONS` dict and the offset+motion playback compilation. The file may then be removable entirely; if anything still depends on the `Expression` (Python class) type, keep just that.
- `Expression.emotion` — mark deprecated in code comments and follow-up-remove in a later migration; nothing should read it once the agent→pose path lands.
- `pet.tsx` — fold any servo-range references into the new resource; drop dead constants.

## Migration order

Each step is independently mergeable; there is no flag-day.

1. **Schema migration** — add `is_builtin` to `Frame` and `Expression`. Existing rows default to `false`. No data change.
2. **Built-in seed** — write `builtin_expressions.py`, the seed function, the reset endpoints, the delete-guard and rename-guard for `is_builtin=true` rows. After this step, the DB contains all built-ins as keyframe-based rows. Animator playback **still uses the old `_EXPRESSIONS` dict** — we haven't switched yet.
3. **Switch playback to DB** — animator's intent-handler resolves expression by DB row, not by `_EXPRESSIONS`. Delete the dict and the offset+motion code. **This is the regression-prone step** — see testing strategy.
4. **Agent → pose wiring** — animator subscribes to `agent.reply`, routes to `play_expression`. Clean `emotion_parser` (drop `_VALID_EMOTIONS` and neutral fallback).
5. **Servo config endpoint + frontend fetch** — ship `GET /animator/config`, ship `useServoConfig()`, delete `servo_ranges.ts`.
6. **Reactive resource helper + adopt** — ship the helper. Migrate the `expressions` resource and the `frames` resource first (directly addresses the "new frame doesn't appear in expression-creation dropdown" bug).
7. **Backend change events** — publish `expressions.changed`, `frames.changed`, `config.changed.servos` from the CRUD handlers. Live reactivity flips on at this step.
8. **Final cleanup** — delete the `EMOTIONS` array, run code-simplifier on the touched files, sweep for any residual dead references.

A user installing each step in sequence sees no functional regression at any boundary. Step 3 is where the visual behavior of built-ins could subtly change (sinusoidal → discrete keyframes), which is why we test the conversion in isolation before merging.

## Testing strategy

- **Keyframe-equivalence test for built-ins.** A Python unit test loads each `BUILTIN_EXPRESSIONS` entry, runs the keyframe playback against a fake `PoseSmoother` for the expression's duration, and compares the produced servo trajectory against the old offset+motion playback for the same emotion. Tolerance is set by eye (e.g., max-deviation < 5 DXL ticks on any axis at any sample). This is the gate for merging step 3. If a built-in's visual behavior is unacceptably lossy, increase its keyframe count in the seed before merging.
- **Agent → pose integration test.** Publish a fake `agent.reply` with `emotion="disagree"` on a test NATS bus; assert an `animator.intent.play_expression {name: "disagree"}` intent is emitted within a short window. Repeat with `emotion="zzz_unknown"` and assert *no* intent is emitted and a warning is logged.
- **Reset endpoint test.** Edit a built-in expression's `steps_json` via PATCH; call the reset endpoint; assert the row matches the seed entry exactly. Repeat for a built-in frame.
- **Delete/rename guard tests.** DELETE on a built-in returns 400; PATCH with a `name` change on a built-in returns 400.
- **Frontend live-update smoke.** With two browser tabs open on the admin, create a frame in tab A; assert (manual or DOM-level test) that tab B's frame-picker dropdown contains the new frame without a refresh, within the round-trip of `frames.changed`.
- **Visual regression on the pet.** With the animator and pet page running, trigger each built-in expression via the admin's ▶ Play button and confirm the head/eyes/jaw animate as before. This is human-verified — no automated harness for the 3D view in this build.

## Risks & open questions

- **Sinusoidal → keyframe fidelity.** The biggest unknown is whether the pre-baked keyframe approximations of `agree-nod` and `disagree-shake` look meaningfully different from the live-evaluated sinusoidal motion. Mitigation: the keyframe-equivalence test above, plus a deliberate human-eye check during step 3 with the option to increase keyframe count or adjust easing if the discrete version reads as jerky.
- **Idle is not in the registry.** The animator's "always-on baseline" pose (currently `_idle_payload`) is conceptually **distinct from triggerable expressions**. The agent doesn't say `[idle]`; it's the state the pet rests in. In this design idle stays as animator-internal behavior — it is *not* a DB row, *not* in the registry, *not* in the dropdown. The phantom `idle` entry in the frontend `EMOTIONS` array is deleted with the rest of that constant. The animator's idle logic continues to live in code (it may read `pose.py`'s `DXL_*_IDLE_POS` plus settings overrides, exposed via `GET /animator/config`'s `idle_defaults` / `idle_overrides`). If the ambient gentle-drift behavior currently produced by the old sinusoidal motion overlay is missed, it is re-implemented as part of the idle code path, not as a triggerable expression.
- **`name` collisions on user save.** If the agent emits `[my-custom-name]` and a user has saved an expression named `my-custom-name`, it plays — by design. We should communicate this clearly in the admin UI so users understand the registry is shared with the agent's vocabulary.
- **The `Expression.emotion` column.** Deprecated, not deleted in this build. A follow-up cleanup removes the column once we're confident nothing reads it.
- **No auth.** The new `/animator/config` and reset endpoints inherit the unauthenticated admin surface. Flagged in production-readiness review; not addressed here.
