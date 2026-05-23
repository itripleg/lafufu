# Lafufu Animation System — design

> **Status:** design · **Date:** 2026-05-23

## Overview

Replace the hardcoded procedural expressions (`packages/animator/.../expressions.py`)
with a user-editable keyframe animation system. Operators can capture poses
from the live sliders, sequence them into expressions, and have those
expressions drive both the servos and (optionally) a sprite display.

Pattern reference: `motherhaven/components/landing/terminal/maven/visuals/SpriteAnimator.tsx`
— maven's expression editor (sequenced frames with per-step duration/delay
overrides on per-expression defaults).

## Three concepts

1. **Frame** — `{ name, pose (5 servo positions), image? }`. Built by positioning
   the live sliders and saving; image is an optional sprite reference.
2. **Expression** — an ordered sequence of frame steps with timing/easing and a
   playback mode (`once` / `loop` / `shuffle`). Optionally bound to one of the
   agent's emotion tags (`idle`, `agree`, `happy`, …).
3. **Image library** — the printer's image directory grows a second bucket
   for animation sprites; both buckets live under one shared upload backend
   so an image is usable cross-feature (a sprite can be printed, a letterhead
   could in theory be a frame image). See *Image library* below.

## Goals

- All current expressions (idle, agree, disagree, happy, sad, angry, surprised,
  neutral) ship as seeded keyframe expressions and remain user-editable.
- The `shuffle` playback mode preserves idle's "living random walk" feel
  (random frame selection from the expression's pool with jittered timing) —
  the procedural sinusoidal idle loop is retired.
- Body admin page is laid out like the Settings page: one panel with two
  clear sections (Frames, Expressions); the existing servo sliders move inside
  the Frames section as the live editor.

## Non-goals (this build)

- The printer letterhead **gallery** is untouched on the page; only the
  on-disk layout grows a sibling `sprites/` bucket (no migration of existing
  letterhead files).
- `/pet` page does *not* swap its 3D model for a sprite display in this build.
  The data path is ready (frames carry images; expression playback broadcasts
  the active frame on NATS), so the `/pet` change is a clean follow-up.
- No auth on the new sprite upload endpoint (consistent with the letterhead
  upload — flagged `# TODO(auth)`).
- Botango-format authoring tools are not adopted. The system instead exposes
  an import surface so externally-authored animations (botango or other) can
  be brought in — see *External import* below. Concrete botango converter
  awaits the botango format spec.

## Data model

Two new DB tables in control's SQLite (replacing the two unused scaffold
models `Expression` and `Behavior` that were never wired up):

### `Frame`
| field | type | notes |
|---|---|---|
| `name` | str, PK, max 100 | unique; URL-safe filename rules |
| `head_lr` | int | absolute DXL position |
| `head_ud` | int | absolute DXL position |
| `eye` | int | absolute DXL position |
| `jaw` | int | absolute DXL position |
| `brow` | int | absolute DXL position |
| `image` | str ⏵ nullable, max 160 | image library ref `{kind}/{name}`; usually a sprite but a letterhead is allowed |
| `description` | str ⏵ nullable, max 500 | |

### `Expression`
| field | type | notes |
|---|---|---|
| `name` | str, PK, max 100 | unique |
| `playback` | str | `once` / `loop` / `shuffle` |
| `default_duration_ms` | int | per-step fallback |
| `default_delay_ms` | int | per-step fallback |
| `default_easing` | str | `linear` / `ease-in-out` (default) / `ease-in` / `ease-out` |
| `steps_json` | str | JSON: `[{frame, duration_ms?, delay_ms?, easing?}, …]` — per-step overrides on the defaults; ordered |
| `emotion` | str ⏵ nullable | `idle` / `happy` / `sad` / `angry` / `surprised` / `neutral` / `agree` / `disagree`; unique-not-null (one active expression per emotion) |
| `description` | str ⏵ nullable | |

Images live on the **frame**, not the expression — there is no expression-level
fallback image. If a frame has no image, no sprite event is broadcast for
that step; subscribers (the future `/pet`) hold their previous image.

`steps_json` rather than a separate `ExpressionStep` table — fewer joins, mirrors
the existing `Behavior.actions_json` pattern, matches maven-demo's flat config.

### Image library

The printer's image directory extends to host *two* buckets, both backed by
the same upload code:

```
data/printer/
  uploads/      ← letterhead uploads (existing, unchanged on disk)
  sprites/      ← new: animation frame images
assets/printer/
  letterheads/  ← built-in letterheads (existing)
  sprites/      ← optional: built-in starter sprites
```

The two buckets share the upload backend (atomic write, name sanitisation,
type validation) but each is browsed through its own gallery in the UI. An
image is referenced library-wide as `{kind}/{name}`:

| `kind` | meaning |
|---|---|
| `letterhead` | uploaded letterhead under `data/printer/uploads/` |
| `sprite`     | uploaded sprite under `data/printer/sprites/` |
| `builtin-letterhead` | shipped under `assets/printer/letterheads/` |
| `builtin-sprite`     | shipped under `assets/printer/sprites/` (if any) |

Cross-feature reuse:
- **Printer compose** still resolves only `letterhead` / `builtin-letterhead`.
- **Printer print-file** can target any `kind` — a sprite is printable.
- **Animation frames** typically reference a `sprite`, but can pick a
  `letterhead` if the operator wants (rare but allowed).

Shared upload helpers (`_atomic_write`, `_sanitize_upload_name`, `_safe_name`,
`_media_type`) lift out of the printer router into
`lafufu_control.api.upload_utils` and serve both the letterhead and sprite
endpoints. No duplication.

## Backend

### Control API (new endpoints)

| route | description |
|---|---|
| `GET /animator/frames` | list all frames |
| `POST /animator/frames` | create from a posted pose (or from current live pose if no body) |
| `PUT /animator/frames/{name}` | update pose / image / description |
| `DELETE /animator/frames/{name}` | delete (fails if referenced by an expression) |
| `POST /animator/frames/{name}/snapshot` | overwrite the frame with the current live pose |
| `GET /animator/expressions` | list with active/built-in flags |
| `POST /animator/expressions` | create |
| `PUT /animator/expressions/{name}` | update (steps, defaults, playback, emotion, image) |
| `DELETE /animator/expressions/{name}` | delete |
| `POST /animator/expressions/{name}/play` | resolve + publish `animator.intent.play_expression` |
| `POST /animator/expressions/{name}/activate` | bind it to its `emotion` (clears any other binding for that emotion) |
| `POST /animator/expressions/import` | accept an external-format payload, convert, insert (see *External import*) |
| `GET /printer/sprites` | list sprites (`builtin-sprite` defaults + `sprite` uploads), same shape as `/printer/letterheads` |
| `POST /printer/sprite` | multipart upload to `data/printer/sprites/` |
| `GET /printer/sprites/{kind}/{name}` | serve a sprite file |
| `DELETE /printer/sprites/upload/{name}` | delete an uploaded sprite |

Sprite endpoints live under `/printer/` — they share the printer's image
backend and let a sprite be printed by the same `print_file` intent.

The current `POST /animator/expression` (proxies the play_expression intent
by name) is kept for backward compatibility — internally it resolves to the
new flow.

### External import

`POST /animator/expressions/import` accepts a payload describing an
externally-authored animation; a pluggable converter maps it to our internal
Expression + Frame shape and inserts.

```python
class ImportRequest(BaseModel):
    source: str               # "botango" / "json" / etc.
    payload: dict | str       # the external format's content
    overwrite: bool = False
```

Converters live under `lafufu_control.animation.converters/` (one module per
source). The internal canonical shape — `{frames: [...], expressions: [...]}`
with the JSON schema published at `GET /animator/schema` — is the lowest-
friction target.

**Botango compatibility** depends on what botango exposes; this is unknown
until we see the format. Three plausible cases:

| Botango exports | Effort to import |
|---|---|
| JSON keyframe poses (per-channel positions + timing/easing) | Small — direct field mapping |
| Animation curves / bezier per channel | Medium — sample to discrete keyframes |
| Proprietary/binary timeline | Large — needs reverse engineering or a SDK |

Action item: get the botango export-format docs or a sample file. The
import surface is built in this build (so it isn't bolted on later); the
concrete botango converter ships once we have the format.

### NATS

- **`animator.intent.play_expression`** — schema changes from
  `{name, intensity}` to a fully-resolved payload:
  ```python
  class AnimatorIntentPlayExpression(BaseModel):
      name: str
      playback: Literal["once", "loop", "shuffle"]
      steps: list[AnimatorPlayStep]
      default_duration_ms: int
      default_delay_ms: int
      default_easing: str

  class AnimatorPlayStep(BaseModel):
      pose: AnimatorPose                # already resolved from frame
      image: str | None = None          # sprite ref, for the broadcast event
      duration_ms: int | None = None    # null → use expression default
      delay_ms: int | None = None
      easing: str | None = None
  ```
  Control resolves frames → poses before publishing, so the animator never
  reaches into control's DB. The old `intensity` field is dropped.

- **`animator.event.frame`** — new event published once per step as the
  animation plays: `{ expression, step_index, frame, image, started_at }`.
  Drives any future `/pet` sprite sync; harmless to ignore otherwise.

### Animator keyframe player

A new background loop in `AnimatorService` replaces both
`_idle_animation_loop` and `_expression_animation_loop`:

- Tracks a `_current_expression: PlayingExpression | None`. A `play_expression`
  intent sets it.
- Per tick, computes the interpolated pose between the previous step's pose
  and the current step's pose using `t / ease_ms` and the easing curve.
- When a step completes, holds for `delay_ms`, then advances:
  - `once` — stops at the end and emits `gesture_done`.
  - `loop` — wraps to step 0.
  - `shuffle` — picks a random step from the same expression with jittered
    timing (±20% on duration/delay). Idle uses this.
- Idle is a *normal expression* tagged `emotion="idle"`; the animator falls
  back to it when no other expression is active (replacing the current
  procedural idle loop).
- The agent's `[agree]` reply path resolves "agree" → the expression tagged
  `emotion="agree"` → publishes the play_expression intent. Same shape as
  today, just data-driven instead of hardcoded.
- The jaw stays under TTS RMS control while lipsync is active (same guard the
  current expression loop already uses).

### Seed migration

A one-shot seed in `control/bootstrap.py` inserts the eight built-in
expressions from the values currently in `expressions.py`. Each is authored
as 2–6 frames per expression (e.g. agree = `agree_a` → `agree_b` looping for
~3 cycles). The seed only runs when the `Frame`/`Expression` tables are
empty so it never overwrites user edits.

## Frontend — Body page layout

Body becomes a `Panel` shaped like the Settings panel — a single panel with
two stacked sections.

### Frames section
```
┌──────────────────────────────────────────────────────────────────┐
│  Frames        [+ snapshot current pose]                         │
├─────────────────────┬─────────────────┬──────────────────────────┤
│ frames gallery      │ selected frame  │   live sliders           │
│ (scroll list,       │ ──────────────  │   head_lr  ─────●──      │
│  card-shaped tiles) │   name: smile   │   head_ud  ──●─────      │
│ ┌──┐ ┌──┐ ┌──┐      │   sprite [pick] │   eye      ────●───      │
│ │f1│ │f2│ │f3│      │   [save] [dup]  │   jaw      ─●──────      │
│ └──┘ └──┘ └──┘ …    │   [×  delete]   │   brow     ────●───      │
└─────────────────────┴─────────────────┴──────────────────────────┘
```
- Sliders drive the servos live. Snapshot creates a Frame from the current
  pose; Save updates the selected Frame from the current sliders.
- Slider redesign: narrower track + bigger hit target + smoothed thumb
  motion (the outstanding "too wide, janky" complaint). The servos themselves
  remain eased on the stepper as today — easing-during-transitions is an
  *expression playback* concern, not a slider concern. Implementation uses
  Tailwind utilities (now in the project) rather than inline styles.
- Sprite picker opens a small inline picker drawn from `/printer/sprites`
  (the shared image-library backend), with a tab to also pick a letterhead.

### Expressions section
```
┌──────────────────────────────────────────────────────────────────┐
│  Expressions    [+ new]                                          │
├──────────────────┬───────────────────────────────────────────────┤
│ expressions      │  selected:  agree                              │
│ gallery          │  [▶ play] [⏸] · playback: once/loop/shuffle    │
│ ┌────┐ ┌────┐    │  emotion: [agree ▾]   defaults: 250/80ms       │
│ │idle│ │agree│   │  ──── steps (drag-orderable) ────              │
│ │loop│ │once │   │  [f7][f3][f7][f3]    @dnd-kit/sortable          │
│ └────┘ └────┘ …  │  per-step ease/hold/easing on click            │
└──────────────────┴───────────────────────────────────────────────┘
```
Click an expression to load it. Duplicate-to-customize (the user can take
the built-in `agree` and make their own variation without losing the
original).

## Build order — phased so each commit is reviewable

1. **Image library + backend foundation** — extract shared upload helpers
   into `upload_utils`; add `/printer/sprites` endpoints; add Frame +
   Expression DB tables and `/animator/frames` + `/animator/expressions`
   CRUD; pose-snapshot endpoint.
2. **Animator keyframe player** — new `_keyframe_player_loop`, updated
   `play_expression` schema, seed built-ins, retire the procedural loops.
   Existing emotion → expression name routing keeps working.
3. **Frontend — Frames section** — slider redesign, snapshot, save, gallery,
   sprite picker.
4. **Frontend — Expressions section** — gallery, step builder, drag-reorder,
   playback controls.
5. **Sprite uploads wired up** — upload UI in the sprite picker.
6. **Import surface** — `/animator/expressions/import` endpoint + converter
   plug-in interface + a canonical-JSON converter. Botango converter lands
   here once we have the format spec.

## Testing

- Backend: control router unit tests for each CRUD endpoint; animator
  service test that `play_expression` drives `head_*` through an
  oscillation; the existing `test_resolve_font_*` pattern extends to
  `test_resolve_sprite_*`.
- Frontend: vitest for the existing surfaces is unaffected; the new builder
  has tested helpers (the step-reorder logic, the default-fallback resolver).
- Manual: built-in expressions visually match the current behaviour on the
  robot (idle still feels alive thanks to shuffle).

## Risks / open items

- **Idle "alive" tuning.** Shuffle with jittered timing is the bet — if it
  feels too repetitive, the fix is to author more idle frames (8–12 instead
  of 4) and widen the jitter, not to revert to procedural.
- **Easing math.** First-order exponential easing in the current stepper is
  preserved on the bus level; the player adds per-step interpolation on top.
  We pick the easing curve once per step from a small enum (no bezier UI
  in this build).
- **Seed authoring.** Mapping today's sinusoidal `agree` (amp 40, freq 1.2,
  ~3 cycles) to 4-6 keyframes is a judgement call. The seeded values are
  user-editable, so "close enough" is fine — the user tunes from there.
- **Schema migration.** `play_expression`'s NATS schema changes shape
  (resolved payload, no `intensity`). Backward compat is via control: it
  resolves the old `{name, intensity}` from the existing `/animator/expression`
  endpoint into the new shape, so external callers don't break.
- **Botango format unknown.** The import surface (phase 6) is built blind to
  the actual botango payload. If botango exports something far from our
  keyframe model (e.g. dense bezier curves, layered tracks), the converter
  may need to sample or approximate. We can't quantify this until we see a
  sample export.
