# Lafufu Animation System — design

> **Status:** design · **Date:** 2026-05-23

## Overview

Replace the hardcoded procedural expressions (`packages/animator/.../expressions.py`)
with a user-editable keyframe animation system. Operators capture poses from
the live sliders, sequence them into expressions, and have those expressions
drive both the servos and (optionally) a sprite display.

**Primary system: our own.** Frame + Expression authored in the admin UI;
a small keyframe player in the animator service interpolates between frame
poses with easing.

**Bottango: forward-compat nice-to-have.** Bottango is a polished external
animation tool whose exports look really good. We've never used it on this
robot, so we don't commit to it being our internal model. Instead, we shape
our data model so a bottango import is a plausible follow-up — lossy where
necessary (bezier curves → easing curves, multi-channel hardware → our 5
servos) but not architecturally blocked. The bridge is sketched at the end
of this doc; implementing it is out of scope here.

## Three concepts

1. **Frame** — `{ name, pose (5 servo positions), image? }`. Built by
   positioning the live sliders and saving; image is an optional sprite
   reference.
2. **Expression** — an ordered sequence of frame steps with timing/easing and
   a playback mode (`once` / `loop` / `shuffle`). Optionally bound to one of
   the agent's emotion tags (`idle`, `agree`, `happy`, …).
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
  clear sections (Frames, Expressions); the existing servo sliders move
  inside the Frames section as the live editor.
- The data model leaves room for a future bottango importer without rework
  — channel-naming, easing curves, timing units are all chosen with that
  bridge in mind.

## Non-goals (this build)

- The printer letterhead **gallery** is untouched on the page; only the
  on-disk layout grows a sibling `sprites/` bucket (no migration of existing
  letterhead files).
- `/pet` page does *not* swap its 3D model for a sprite display in this
  build. The data path is ready (frames carry images; expression playback
  broadcasts the active frame on NATS), so the `/pet` change is a clean
  follow-up.
- No auth on the new sprite upload endpoint (consistent with the letterhead
  upload — flagged `# TODO(auth)`).
- **No bottango import or player integration** in this build. The forward-
  compat bridge is sketched below as a follow-up plan.
- We do **not** lift or vendor any bottango source. We don't know that code
  yet and aren't committing to it.

## Data model

Two new DB tables in control's SQLite, replacing the two unused scaffold
models `Expression` and `Behavior` that were never wired up.

### `Frame`
| field | type | notes |
|---|---|---|
| `name` | str, PK, max 100 | unique; URL-safe filename rules |
| `head_lr` | int | absolute DXL position |
| `head_ud` | int | absolute DXL position |
| `eye` | int | absolute DXL position |
| `jaw` | int | absolute DXL position |
| `brow` | int | absolute DXL position |
| `image` | str ⏵ nullable, max 160 | image library ref `{kind}/{name}`; usually a sprite |
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

Images live on the **frame**, not the expression. If a frame has no image,
no sprite event is broadcast for that step; subscribers (the future `/pet`)
hold their previous image.

`steps_json` rather than a separate `ExpressionStep` table — fewer joins,
mirrors the existing `Behavior.actions_json` pattern.

## Image library

The printer's image directory hosts *two* buckets, both backed by the same
upload code:

```
data/printer/
  uploads/      ← letterhead uploads (existing, unchanged on disk)
  sprites/      ← new: animation frame images
assets/printer/
  letterheads/  ← built-in letterheads (existing)
  sprites/      ← optional: built-in starter sprites
```

Both buckets share the upload backend (atomic write, name sanitisation, type
validation) but each is browsed through its own gallery in the UI. An image
is referenced library-wide as `{kind}/{name}`:

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
  `letterhead` if the operator wants.

Shared upload helpers (`_atomic_write`, `_sanitize_upload_name`, `_safe_name`,
`_media_type`) lift out of the printer router into
`lafufu_control.api.upload_utils` and serve both endpoints.

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
| `PUT /animator/expressions/{name}` | update (steps, defaults, playback, emotion) |
| `DELETE /animator/expressions/{name}` | delete |
| `POST /animator/expressions/{name}/play` | resolve + publish `animator.intent.play_expression` |
| `POST /animator/expressions/{name}/activate` | bind it to its `emotion` (clears any other binding for that emotion) |
| `GET /printer/sprites` | list sprites (`builtin-sprite` defaults + `sprite` uploads), same shape as `/printer/letterheads` |
| `POST /printer/sprite` | multipart upload to `data/printer/sprites/` |
| `GET /printer/sprites/{kind}/{name}` | serve a sprite file |
| `DELETE /printer/sprites/upload/{name}` | delete an uploaded sprite |

Sprite endpoints live under `/printer/` — they share the printer's image
backend and let a sprite be printed by the same `print_file` intent.

The current `POST /animator/expression` (proxies the play_expression intent
by name) is kept for backward compatibility — internally it resolves to the
new flow.

### NATS

**`animator.intent.play_expression`** — schema changes from
`{name, intensity}` to a fully-resolved payload (control resolves frames →
poses before publishing, so the animator never reaches into control's DB):

```python
class AnimatorIntentPlayExpression(BaseModel):
    name: str
    playback: Literal["once", "loop", "shuffle"]
    steps: list[AnimatorPlayStep]
    default_duration_ms: int
    default_delay_ms: int
    default_easing: str   # linear / ease-in-out / ease-in / ease-out

class AnimatorPlayStep(BaseModel):
    pose: AnimatorPose                # resolved from frame
    image: str | None = None          # sprite ref for the broadcast event
    duration_ms: int | None = None    # null → use expression default
    delay_ms: int | None = None
    easing: str | None = None
```

The old `intensity` field is dropped. Backward compat for callers that still
publish `{name, intensity}` is handled by control: it resolves and re-emits
in the new shape before the animator sees it.

**`animator.event.frame`** — new event published once per step as the
animation plays: `{ expression, step_index, frame, image, started_at }`.
Drives any future `/pet` sprite sync; harmless to ignore otherwise.

### Animator — our keyframe player

A new background loop in `AnimatorService` replaces both
`_idle_animation_loop` and `_expression_animation_loop`:

- Tracks a `_current_expression: PlayingExpression | None`. A
  `play_expression` intent sets it.
- Per tick (20 Hz, matching the retired loops), computes the interpolated pose between the previous
  step's pose and the current step's pose using `t / duration_ms` shaped by
  the easing curve. Easing curves are a small enum (linear / ease-in /
  ease-out / ease-in-out) implemented as a 4-entry lookup table on the unit
  interval — easy to extend later.
- When a step's duration completes, holds for `delay_ms`, then advances:
  - `once` — stops at the end and emits `gesture_done`.
  - `loop` — wraps to step 0.
  - `shuffle` — picks a random step from the same expression with jittered
    timing (±20% on duration/delay). Idle uses this.
- Idle is a *normal expression* tagged `emotion="idle"`; the animator falls
  back to it when no other expression is active (replacing the current
  procedural idle loop).
- The agent's `[agree]` reply path resolves "agree" → the expression tagged
  `emotion="agree"` → publishes the play_expression intent. Same shape as
  today externally, data-driven internally.
- The jaw stays under TTS RMS control while lipsync is active (same guard
  the current expression loop already uses).

### Seed migration

A one-shot seed in `control/bootstrap.py` inserts the eight built-in
expressions from the values currently in `expressions.py`. Each is authored
as 2–6 frames per expression (e.g. agree = `agree_a` → `agree_b` looping
for ~3 cycles). The seed only runs when the `Frame`/`Expression` tables are
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
  motion (the outstanding "too wide, janky" complaint). The servos
  themselves remain eased on the stepper as today — easing-during-
  transitions is an *expression playback* concern, not a slider concern.
  Implementation uses Tailwind utilities (now in the project) rather than
  inline styles.
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
   into `upload_utils`; add `/printer/sprites` endpoints; add `Frame` +
   `Expression` DB tables and `/animator/frames` + `/animator/expressions`
   CRUD; pose-snapshot endpoint.
2. **Animator keyframe player** — new `_keyframe_player_loop`, updated
   `play_expression` schema, seed built-ins, retire the procedural loops.
   Existing emotion → expression name routing keeps working.
3. **Frontend — Frames section** — slider redesign, snapshot, save, gallery,
   sprite picker.
4. **Frontend — Expressions section** — gallery, step builder, drag-reorder,
   playback controls.
5. **Sprite uploads wired up** — upload UI in the sprite picker.

## Bottango forward-compat bridge (out of scope — sketch)

A future plan can add bottango imports without re-architecting. The shape of
the bridge:

- **Endpoint:** `POST /animator/expressions/import` accepts a bottango
  export (array of comma-separated command strings) plus an optional
  channel-mapping override. Parses, converts, inserts as a new Expression
  + Frames (or as a row in a separate `imported_animations` table — TBD
  when the bridge is built).
- **Channel mapping** — Lafufu has 5 fixed DXL servos; a bottango export
  has its own channel ids. A default lookup table handles common spellings
  (`jaw`, `leftBrow`/`rightBrow` → `brow`, `headLR`/`headRotation` →
  `head_lr`, etc.); unmapped channels are reported back to the operator so
  they know to rename in bottango or extend the table.
- **Curve mapping** — bottango's bezier-per-keyframe carries more shape than
  our small easing-curve enum. The importer samples each bezier to the
  nearest matching curve (or to `linear` as a fallback). Lossy but
  workable; documented as "import-time approximation."
- **Effector kinds Lafufu doesn't have** (stepper, RGB, on/off) are
  parsed and ignored, not rejected — so re-importing the same file after
  Lafufu sprouts new hardware would just work.
- **Player integration** — the imported animation compiles down to our
  internal step+pose model (sampling the bezier at the step boundaries +
  inferring easing from the segment shape). Same player downstream; no
  bottango runtime code lifted.
- **Verification gate** — never ship the importer without round-tripping
  a real bottango export against the fixture, since the format details in
  the user-supplied reference doc were reverse-engineered. The export is
  the source of truth.

This is **not** built in this build, but the data model and player are
designed so adding the bridge later is a single phase with no rework.

## Testing

- Backend control routers — unit tests per CRUD endpoint.
- Animator player — fixture test that a known Expression produces the
  expected per-tick poses (interpolation + easing eval). Goldens for the
  built-in expressions verify the seed didn't drift.
- Compilation — table-driven tests covering each easing curve and each
  playback mode.
- Frontend — vitest for existing surfaces is unaffected; the new builder
  has tested helpers (step reorder, default-fallback resolver).
- Manual — built-in expressions visually match the current behaviour on the
  robot; idle still feels alive thanks to shuffle.

## Risks / open items

- **Idle "alive" tuning.** Shuffle + jittered timing is the bet. If it
  feels repetitive in practice, the fix is more idle frames (8–12 instead
  of 4) and wider jitter, not reverting to procedural.
- **Easing math.** First-order exponential easing in the current stepper
  is preserved on the bus level; the player adds per-step interpolation on
  top. We pick the easing curve once per step from a small enum (no bezier
  UI in this build).
- **Seed authoring.** Mapping today's sinusoidal `agree` (amp 40, freq
  1.2, ~3 cycles) to 4-6 keyframes is a judgement call. The seeded values
  are user-editable, so "close enough" is fine — the operator tunes from
  there.
- **Schema migration.** `play_expression`'s NATS schema changes shape
  (resolved payload, no `intensity`). Backward compat is via control: it
  resolves the old `{name, intensity}` from the existing
  `/animator/expression` endpoint into the new shape, so external callers
  don't break.
- **Bottango bridge approximation.** Sampling bezier curves to our
  easing-curve enum is necessarily lossy. The bridge is a follow-up; the
  primary system is the handmade one, and we'll judge bridge fidelity
  against real exports when (if) we build it.
