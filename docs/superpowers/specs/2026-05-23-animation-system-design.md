# Lafufu Animation System — design

> **Status:** design · **Date:** 2026-05-23

## Overview

Replace the hardcoded procedural expressions (`packages/animator/.../expressions.py`)
with a user-editable keyframe animation system. Operators capture poses from
the live sliders, sequence them into expressions, and have those expressions
drive both the servos and (optionally) a sprite display.

**Canonical runtime model: Bottango.** Bottango is an existing open-source
animation system with a well-tested bezier-curve player and a documented
file format. We adopt its `ServoChannel` / `ServoKeyframe` / `ServoAnimation`
shape as the **single canonical runtime model** and lift its reference player
(BezierCurve, Effector) rather than write our own from scratch. Our hand-built
UI is *one* way to populate that model; a bottango export is the *other*.
Same player downstream.

## Three concepts (UI layer)

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
- **Bottango-format imports** are first-class — externally authored
  animations land in the same DB and run on the same player.

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
- No live Bottango REST polling. Bottango isn't running on the Pi at playback
  time — file-drop / paste-into-import is the only ingest mode.
- No bottango effector kinds we don't have hardware for in this build
  (stepper, RGB, on/off). The parser tolerates them but the player ignores
  them — keeps the door open for later expansion.

## Architecture — two layers

```
   ┌────────────────────────────┐    ┌────────────────────────────┐
   │ Authoring schema (DB)      │    │ Bottango export (file)     │
   │  Frame · Expression        │    │  command-string array      │
   └──────────────┬─────────────┘    └─────────────┬──────────────┘
                  │ compile                        │ parse
                  ▼                                ▼
       ┌────────────────────────────────────────────────────┐
       │  Canonical runtime: ServoAnimation                 │
       │   { channels[], keyframes[], duration_ms }         │
       │   (Bottango shape — same as the OSS player consumes)│
       └─────────────────────────┬──────────────────────────┘
                                 │ play
                                 ▼
              ┌──────────────────────────────────┐
              │  Player (lifted from Bottango)   │
              │  BezierCurve + Effector +        │
              │  Lafufu DXL-bus callbacks        │
              └──────────────────────────────────┘
```

The DB stays small and operator-friendly (Frame, Expression) — but is just
*one* author for the canonical runtime model. Bottango exports compile to
the same canonical model. The player only ever consumes the canonical model,
which is the bottango wire shape.

## Data model

### Authoring schema (control's SQLite)

Two new tables, replacing the two unused scaffold models `Expression` and
`Behavior` that were never wired up.

#### `Frame`
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

#### `Expression`
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

#### Imported bottango animations

A third table holds animations brought in from bottango exports as-is
(no re-authoring through Frame/Expression — they go straight to canonical):

| field | type | notes |
|---|---|---|
| `name` | str, PK, max 100 | |
| `source` | str | `bottango` initially |
| `canonical_json` | str | the compiled `ServoAnimation` JSON |
| `original_payload` | str ⏵ nullable | the raw bottango export, kept so we can re-parse if the parser improves |
| `playback` | str | `once` / `loop` / `shuffle` (extracted/defaulted at import time) |
| `emotion` | str ⏵ nullable | optional emotion binding, same rules as Expression |

Imported animations show up alongside Expressions in the gallery but are
read-only in the UI (edit them in bottango, re-import).

### Canonical runtime (in-memory + NATS wire shape)

This mirrors the dataclass set from bottango's OSS reference exactly —
matches what bottango's `Effector` expects:

```python
@dataclass
class ServoChannel:
    id: str
    kind: str                   # "dxl_servo" for Lafufu; other kinds tolerated, ignored
    min_signal: float           # for dxl_servo: DXL ticks at min
    max_signal: float           # for dxl_servo: DXL ticks at max
    max_rate: float             # signal change per second; clamps how fast we slew
    start_signal: float

@dataclass
class ServoKeyframe:
    channel_id: str
    start_ms: int
    duration_ms: int            # 0 for instant
    kind: str                   # "bezier" | "instant" | "on_off" | "trigger" | "color"
    # bezier — already normalized to 0..1; the player lerps to channel min/max:
    start_movement: float | None = None
    end_movement: float | None = None
    start_control: tuple[int, float] | None = None    # (ms_offset, mv_offset)
    end_control: tuple[int, float] | None = None
    on: bool | None = None
    start_color: tuple[int, int, int] | None = None
    end_color: tuple[int, int, int] | None = None

@dataclass
class ServoAnimation:
    name: str
    duration_ms: int
    channels: list[ServoChannel]
    keyframes: list[ServoKeyframe]
    # Lafufu additions on top of the bottango shape:
    playback: str = "once"      # once / loop / shuffle
    image_per_frame: list[str | None] = field(default_factory=list)  # parallel to step boundaries, for sprite broadcast
```

**Lafufu's 5 servos as bottango channels** — seeded at startup, never
user-editable:

| id | kind | min_signal | max_signal | max_rate | start_signal |
|---|---|---|---|---|---|
| `head_lr` | dxl_servo | 1828 | 2298 | from `taus` | from idle pose |
| `head_ud` | dxl_servo | 2885 | 3278 | from `taus` | from idle pose |
| `eye`     | dxl_servo | 1960 | 2130 | from `taus` | from idle pose |
| `jaw`     | dxl_servo | 1534 | 1728 | from `taus` | from idle pose |
| `brow`    | dxl_servo | 2051 | 2099 | from `taus` | from idle pose |

`max_rate` is derived from the existing per-servo time constants in
`AnimatorService.DEFAULT_TAUS` so we don't lose the jaw's fast/head's
smooth-slewing tuning.

## Compilation (Expression → ServoAnimation)

For each Expression step:

1. Cursor `t = 0` at expression start.
2. For each step `(frame, duration_ms, delay_ms, easing)`:
   - For each of the 5 channels, emit a `ServoKeyframe`:
     - `kind = "bezier"`
     - `start_ms = t`
     - `duration_ms = step.duration_ms`
     - `start_movement = (prev_frame.channel - min) / (max - min)`
     - `end_movement = (frame.channel - min) / (max - min)`
     - Control points derived from `easing` (lookup table — linear/ease-in/ease-out/ease-in-out → bezier control offsets).
   - `image_per_frame.append(frame.image)`
   - `t += duration_ms + delay_ms`
3. `ServoAnimation.duration_ms = t`
4. Playback mode (`once` / `loop` / `shuffle`) propagates.

The first step's `prev_frame` is whatever pose Lafufu is currently in (read
from `_current_pose`) — so transitions in/out of an expression look smooth.

`shuffle` is handled at *playback* time, not compilation: the player picks
the next step at random with jittered timing, using the per-step keyframes
authored in order as the pool.

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
| `GET /animator/expressions` | list (authoring + imported), with active/built-in flags |
| `POST /animator/expressions` | create |
| `PUT /animator/expressions/{name}` | update (steps, defaults, playback, emotion) |
| `DELETE /animator/expressions/{name}` | delete |
| `POST /animator/expressions/{name}/play` | compile to ServoAnimation + publish `animator.intent.play_animation` |
| `POST /animator/expressions/{name}/activate` | bind it to its `emotion` (clears any other binding for that emotion) |
| `POST /animator/expressions/import` | accept a bottango export (or other source), parse, store as an *imported animation*, optionally bind to an emotion |
| `GET /printer/sprites` | list sprites (`builtin-sprite` defaults + `sprite` uploads), same shape as `/printer/letterheads` |
| `POST /printer/sprite` | multipart upload to `data/printer/sprites/` |
| `GET /printer/sprites/{kind}/{name}` | serve a sprite file |
| `DELETE /printer/sprites/upload/{name}` | delete an uploaded sprite |

Sprite endpoints live under `/printer/` — they share the printer's image
backend and let a sprite be printed by the same `print_file` intent.

The current `POST /animator/expression` (proxies the play_expression intent
by name) is kept for backward compatibility — internally it resolves to the
new flow.

### Animator — lifted bottango player

The animator service replaces both `_idle_animation_loop` and
`_expression_animation_loop` with a player built on top of the bottango OSS
reference. The plan:

1. Vendor the OSS bottango Python driver into
   `packages/animator/src/lafufu_animator/bottango_player/` (or a sibling
   package, decision at implementation time). Keep upstream's file boundaries
   intact for diff-ability against future bottango updates.
2. Lift unchanged:
   - `BezierCurve.py` — cubic bezier evaluation.
   - `OnOffCurve.py` / `ColorCurve.py` / `TriggerCurve.py` — other curve
     types (kept for parser tolerance even though we don't drive that hardware).
   - `Effector.py` — per-channel state machine (`update()`, `speedLimitSignal()`).
3. Replace `CallbacksAndConfiguration.py` with a Lafufu adapter that:
   - Maps each channel's `signal` (DXL ticks) into our `_target_pose` slot
     for that servo on every `handleEffectorSetSignal`.
   - Bridges `max_rate` / `speedLimitSignal()` from the bottango model into
     our existing per-servo `tau` so the hardware response stays as smooth
     as today.
4. **Skip** `MainLoop.py`, `CommandParse.py`, `SocketDriverLerp.py` — those
   parse live wire commands; we feed the player ready-made
   `ServoChannel`/`ServoKeyframe` objects from our DB or import.

A 50 Hz tick around `for effector in effectors: effector.update(now_ms)`
drives playback. `playback` is layered on top:
- `once` — stop after `duration_ms`, emit `gesture_done`.
- `loop` — reset cursor to 0.
- `shuffle` — when the current step's window expires, splice the next step's
  keyframes from a randomly picked source step into the active set with
  ±20% jittered timing. (Implementation: the shuffle layer rewrites the
  effector's next-keyframe pointer; the bottango Effector itself is unchanged.)

Idle is just an Expression tagged `emotion="idle"` with `playback="shuffle"`;
the animator falls back to it when nothing else is active. The agent's
`[agree]` reply path resolves "agree" → the expression tagged
`emotion="agree"` → publishes the play_animation intent. Same shape as today
externally, data-driven internally.

The jaw stays under TTS RMS control while lipsync is active (same guard the
current expression loop already uses) — the player adapter respects a
"channel locked" flag for the jaw and skips writes there during lipsync.

### NATS

- **`animator.intent.play_animation`** (replaces `animator.intent.play_expression`)
  carries a fully-compiled `ServoAnimation`:
  ```python
  class AnimatorIntentPlayAnimation(BaseModel):
      animation: ServoAnimationPayload   # full canonical shape
  ```
  Control compiles Expression → ServoAnimation (or pulls cached canonical
  for imported animations) before publishing — the animator never reaches
  into control's DB.

- **`animator.event.frame`** — new event published as the animation crosses
  each step boundary: `{ animation_name, step_index, image, started_at }`.
  Drives any future `/pet` sprite sync; harmless to ignore otherwise.

- **Backward compat:** the existing `animator.intent.play_expression`
  shape is kept on the wire for one release. The animator routes it
  through control's compile path when received, then logs a deprecation
  warning. Removed in a follow-up.

## External import — bottango

`POST /animator/expressions/import` accepts a bottango export, parses it,
and stores it as a row in the *imported animations* table (separate from
Expression). UI shows imported animations in the gallery, badged as
"imported · bottango", read-only edit-wise (re-author in bottango → re-import).

### Parser

A pure-Python parser handles the bottango command-string array. Concrete
opcode handling (per the bottango spec):

| opcode | meaning | how we handle |
|---|---|---|
| `tSYN` | time-zero anchor | accumulate into `time_anchor`, add to subsequent `start_ms` |
| `rSVPin` / `rSVI2C` | declare servo | `ServoChannel(kind="pin_servo"|"i2c_servo")` — kept around even though we don't drive PWM/I2C directly; the player just doesn't move a hardware leg we don't have |
| `rSTDir` | stepper | parsed, ignored at playback (no hardware match) |
| `rECC` / `rECOnOff` / `rECColor` | generic / binary / RGB | parsed, ignored unless we add hardware |
| `sC` | bezier keyframe | `ServoKeyframe(kind="bezier")` — `start_movement = int(p[3])/8192`, etc. Control points decoded per spec. |
| `sCI` | instant move | `ServoKeyframe(kind="instant", duration_ms=0)` |
| `sCO` / `sCT` / `sCC` / `sCCI` | on/off / trigger / color tweens | parsed into corresponding kinds; the player evaluates only those it has effectors for |
| `xE` / `xC` / `STOP` | lifecycle resets | tolerated, no-op |

The `/8192` integer encoding is normalized at the parser boundary — the
canonical model only carries `float ∈ [0, 1]`. Never propagate `/8192` past
the parser.

### Channel mapping for imports

A bottango export's channel ids (e.g. `jaw`, `leftBrow`) need to map onto
Lafufu's 5 fixed channels. The importer applies a small mapping table:

```python
BOTTANGO_TO_LAFUFU = {
    "jaw": "jaw",
    "brow": "brow", "leftBrow": "brow", "rightBrow": "brow",
    "headLR": "head_lr", "headRotation": "head_lr",
    "headUD": "head_ud", "headTilt": "head_ud",
    "eye": "eye", "eyes": "eye",
}
```

Unmapped channels are *kept in the canonical model* (the player ignores them)
so a re-import after expanding the table picks them up. The import response
reports which channels were mapped vs left orphaned, so the operator knows
to either rename in bottango or extend the table.

### Sample-export verification

The spec was written without a verified sample bottango export against this
machine. Before shipping the importer:

1. Get a real bottango export (one channel, two keyframes is enough).
2. Run it through the parser; assert the round-tripped `ServoAnimation`
   matches by-hand math.
3. Pin that export as a fixture in `packages/animator/tests/fixtures/`.

If the actual export contradicts this spec, the export is the source of
truth — update the spec and the parser.

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
│  Expressions    [+ new] [↥ import bottango]                      │
├──────────────────┬───────────────────────────────────────────────┤
│ expressions      │  selected:  agree                              │
│ gallery          │  [▶ play] [⏸] · playback: once/loop/shuffle    │
│ ┌────┐ ┌────┐    │  emotion: [agree ▾]   defaults: 250/80ms       │
│ │idle│ │agree│   │  ──── steps (drag-orderable) ────              │
│ │loop│ │once │   │  [f7][f3][f7][f3]    @dnd-kit/sortable          │
│ └────┘ └────┘ …  │  per-step ease/hold/easing on click            │
└──────────────────┴───────────────────────────────────────────────┘
```
Click an expression to load it. Duplicate-to-customize. Imported animations
show in the same gallery with a small "bottango" badge; clicking shows a
read-only summary (channel count, duration) plus play/activate controls —
the step editor is hidden for those (edit in bottango, re-import).

## Build order — phased so each commit is reviewable

1. **Image library + backend foundation** — extract shared upload helpers
   into `upload_utils`; add `/printer/sprites` endpoints; add `Frame` +
   `Expression` DB tables and `/animator/frames` + `/animator/expressions`
   CRUD; pose-snapshot endpoint. *No animator changes yet.*
2. **Bottango player lift** — vendor the OSS bottango Python driver
   (`BezierCurve.py`, `Effector.py`, friends); write the Lafufu
   `CallbacksAndConfiguration` adapter wiring `Effector` writes to
   `_target_pose`; replace `_idle_animation_loop` and
   `_expression_animation_loop` with the 50 Hz tick over effectors.
3. **Compilation + new NATS intent** — Expression → ServoAnimation
   compiler; new `animator.intent.play_animation` schema and handler;
   `animator.event.frame` event; seed the eight built-in expressions and
   move idle/emotions onto the player. Old `play_expression` intent kept
   as a deprecation-routed alias.
4. **Frontend — Frames section** — slider redesign, snapshot, save, gallery,
   sprite picker.
5. **Frontend — Expressions section** — gallery, step builder, drag-reorder,
   playback controls.
6. **Sprite uploads wired up** — upload UI in the sprite picker.
7. **Bottango importer** — `POST /animator/expressions/import` endpoint,
   pure-Python parser for all opcodes, channel mapping table, *imported
   animations* DB table + read-only gallery card, fixture test against a
   real sample export.

## Testing

- Backend control routers — unit tests per CRUD endpoint; `/import` round-
  trips a fixture bottango export.
- Animator player — fixture-based test that a known `ServoAnimation`
  produces the expected per-tick poses (bezier eval + speed limit). Goldens
  for the built-in expressions verify the seed didn't drift.
- Compilation — table-driven tests of `Expression → ServoAnimation` covering
  all four easing curves and each playback mode.
- Frontend — vitest for the existing surfaces is unaffected; the new builder
  has tested helpers (step reorder, default-fallback resolver).
- Manual — built-in expressions visually match the current behaviour on the
  robot; idle still feels alive thanks to shuffle.

## Risks / open items

- **Bottango source needs to land on the dev box.** The OSS driver lives
  off this machine right now. Phase 2 starts by cloning the upstream repo
  locally, picking a pinned commit, and vendoring the relevant files —
  with the upstream LICENSE preserved and attributed. Until then phase 2
  can't start.
- **Bottango licensing.** Permissive per the user's note, but I haven't
  read the actual LICENSE on this machine. Verifying license terms and
  attribution requirements is the first task of phase 2.
- **Channel mapping for imports.** Operators authoring in bottango will
  use whatever channel names they pick; our default `BOTTANGO_TO_LAFUFU`
  table handles common spellings, but exotic naming requires extending
  the table (or renaming in bottango). The importer reports unmapped
  channels so failure mode is obvious, not silent.
- **Shuffle layer on top of Effector.** Bottango's Effector expects a
  monotonic timeline; our shuffle splices keyframes onto it dynamically.
  Risk: re-entry timing seams between shuffled steps. Mitigation: each
  shuffle "splice" starts from the current pose (we have it) so seams
  are eased, not jumped.
- **Schema migration.** `play_expression` → `play_animation` is a wire
  schema change. Backward compat is via the routed-deprecation alias;
  external publishers get a transition release.
- **Idle "alive" tuning.** Shuffle + jittered timing is the bet. If it
  feels repetitive in practice, the fix is more idle frames (8-12 instead
  of 4) and wider jitter, not reverting to procedural.
- **Sample-export verification.** The parser in this spec is written from
  the docs alone. Phase 7 must round-trip a real export before merging.
