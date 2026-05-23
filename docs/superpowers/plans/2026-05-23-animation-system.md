# Lafufu Animation System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded procedural expressions in `packages/animator/.../expressions.py` with a user-editable Frame + Expression keyframe animation system, backed by a neutral top-level image library shared between printer letterheads and animation sprites.

**Architecture:** Two new DB tables (`Frame`, `Expression`) in control's SQLite are authored via the Body admin page. Expressions compile to a resolved play payload dispatched via NATS to the animator service, which runs a small keyframe player with easing curves and `once`/`loop`/`shuffle` playback. Images live at `data/images/{letterheads,sprites}/` and `assets/images/{letterheads,sprites}/` — a generic `/images/{bucket}/…` API; existing `/printer/letterheads/…` stay as back-compat shims.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, NATS, pytest, SolidJS + Tailwind, vitest.

**Spec:** `docs/superpowers/specs/2026-05-23-animation-system-design.md`

---

## File structure

### Backend
- Modify: `packages/shared/src/lafufu_shared/paths.py` — `image_*_dir()` helpers.
- Modify: `packages/shared/src/lafufu_shared/schemas.py` — new `AnimatorPlayStep` and `AnimatorEventFrame`; replace `AnimatorIntentPlayExpression`.
- Create: `packages/control/src/lafufu_control/api/image_library.py` — shared upload helpers + generic `/images/{bucket}/…` router.
- Modify: `packages/control/src/lafufu_control/api/routers/printer.py` — delegate to `image_library`; keep `/printer/letterheads/…` as back-compat shim.
- Modify: `packages/control/src/lafufu_control/api/routers/animator.py` — frame + expression CRUD + play + activate.
- Create: `packages/control/src/lafufu_control/models/frame.py`
- Modify: `packages/control/src/lafufu_control/models/expression.py` — replace scaffold.
- Modify: `packages/control/src/lafufu_control/models/__init__.py` — re-export `Frame`.
- Create: `packages/control/src/lafufu_control/migration.py` — letterhead-data relocation.
- Create: `packages/control/src/lafufu_control/animation/__init__.py`
- Create: `packages/control/src/lafufu_control/animation/compile.py`
- Create: `packages/control/src/lafufu_control/animation/seed.py`
- Modify: `packages/control/src/lafufu_control/bootstrap.py` — invoke seed.
- Modify: `packages/control/src/lafufu_control/api/app.py` — mount routers + run migration.
- Create: `packages/animator/src/lafufu_animator/easing.py`
- Create: `packages/animator/src/lafufu_animator/keyframe_player.py`
- Modify: `packages/animator/src/lafufu_animator/service.py` — keyframe-player loop replaces procedural loops.

### Tests
- `packages/shared/tests/test_paths.py`
- `packages/control/tests/test_image_library.py`
- `packages/control/tests/test_migration.py`
- `packages/control/tests/test_api_frames.py`
- `packages/control/tests/test_api_expressions.py`
- `packages/control/tests/test_animation_compile.py`
- `packages/animator/tests/test_easing.py`
- `packages/animator/tests/test_keyframe_player.py`
- `packages/animator/tests/test_service.py` — update for new schema.
- `packages/control/tests/test_api_printer.py` — adapt for back-compat shim + new disk layout.

### Frontend
- Modify: `web/src/admin/body_panel.tsx` — thin orchestrator.
- Create: `web/src/admin/frames_section.tsx`
- Create: `web/src/admin/expressions_section.tsx`
- Create: `web/src/admin/image_picker.tsx`
- Modify: `web/src/shared/api.ts` — frame/expression/image methods.



---

## Phase 1 — Shared image library

Goal: relocate the printer image directory to data/images/ with a bucket-aware generic API; existing printer letterhead UI keeps working via back-compat shims.

### Task 1.1 — Image-library path helpers

Modify packages/shared/src/lafufu_shared/paths.py. Create packages/shared/tests/test_paths.py.

- [ ] **Step 1 — Failing tests**: assert image_letterheads_dir, image_sprites_dir, image_letterheads_defaults_dir, image_sprites_defaults_dir exist; each ends in (images, letterheads|sprites); has data or assets in the path.
- [ ] **Step 2 — Run, expect 4 fails**: .venv/Scripts/pytest packages/shared/tests/test_paths.py -v
- [ ] **Step 3 — Implement the four helpers**: each returns _REPO_ROOT / data-or-assets / images / bucket. Append to paths.py.
- [ ] **Step 4 — Tests pass**.
- [ ] **Step 5 — Commit**: feat(shared): image-library path helpers.

### Task 1.2 — Move bundled letterheads

- [ ] Step 1: git mv assets/printer/letterheads/*.png assets/images/letterheads/; mkdir assets/images/sprites + README placeholder.
- [ ] Step 2: edit printer_default_letterheads_dir() in paths.py to return _REPO_ROOT / assets / images / letterheads.
- [ ] Step 3: .venv/Scripts/pytest packages/control/tests/test_api_printer.py -v — all PASS (printer endpoints serve from new disk path).
- [ ] Step 4: commit refactor(images): move bundled letterheads to assets/images/letterheads.

### Task 1.3 — Shared image_library module

Create packages/control/src/lafufu_control/api/image_library.py exposing:

- Constants: ALLOWED_IMAGE_MIME (png/jpeg/webp), IMAGE_EXTS, MAX_IMAGE_BYTES = 10MB, BUCKETS = (letterheads, sprites), _MEDIA dict.
- Helpers (lift bodies from printer.py): media_type(p), safe_name(name) (rejects /, backslash, .., empty), sanitize_upload_name(raw, default_stem, ext), atomic_write(target, data), bucket_dir(bucket, kind) (raises HTTPException on bad bucket/kind, resolves to assets/images vs data/images via lafufu_shared.paths helpers).
- APIRouter with: GET /images/{bucket}, GET /images/{bucket}/{kind}/{name} (FileResponse), POST /images/{bucket}/upload (multipart, PIL.verify validation, atomic_write), DELETE /images/{bucket}/upload/{name} (204).

- [ ] Step 1: write the module.
- [ ] Step 2: smoke  expects (letterheads, sprites).
- [ ] Step 3: commit feat(control): shared image_library module + /images endpoints.

### Task 1.4 — Wire printer.py through image_library

In packages/control/src/lafufu_control/api/routers/printer.py:
- Delete the local _atomic_write, _sanitize_upload_name, _safe_name, _media_type functions.
- Replace with: from ..image_library import atomic_write as _atomic_write, bucket_dir as _shared_bucket_dir, media_type as _media_type, safe_name as _safe_name, sanitize_upload_name as _sanitize_upload_name.
- Rewrite _letterhead_dir(kind) to return _shared_bucket_dir("letterheads", kind).

- [ ] Step 1: make the edits.
- [ ] Step 2: .venv/Scripts/pytest packages/control/tests/test_api_printer.py -v — all PASS.
- [ ] Step 3: commit refactor(printer): delegate uploads to image_library.

### Task 1.5 — Register router + add tests

Modify packages/control/src/lafufu_control/api/app.py:
  from .image_library import router as images_router
  app.include_router(images_router, prefix="/api")

Create packages/control/tests/test_image_library.py with the standard client fixture (monkeypatch LAFUFU_PRINTER_DATA_DIR, init sqlite engine, TestClient via create_app). Tests:
- test_list_letterheads_bucket — GET /api/images/letterheads returns built-in names (card.png, white.png) under kind=default.
- test_list_sprites_bucket — GET /api/images/sprites returns only IMAGE_EXTS items.
- test_upload_serve_delete_sprite — POST a generated PIL PNG, GET it, DELETE, confirm removed.
- test_unknown_bucket_404 — GET /api/images/wat returns 404.

- [ ] Step 1: write tests (4 fails until router mounted).
- [ ] Step 2: register router in app.py.
- [ ] Step 3: tests pass.
- [ ] Step 4: commit feat(control): register /images router with tests.

### Task 1.6 — Runtime migration of letterhead data

Create packages/control/src/lafufu_control/migration.py with migrate_letterhead_data() that:
- No-ops if data/images/letterheads/ exists and non-empty.
- Moves each file under data/printer/uploads/* to data/images/letterheads/*.
- Moves data/printer/letterhead.png to data/images/active_letterhead.png if target absent.
- Reads data/printer/active_letterhead (old format "{kind}/{name}"), prepends "letterheads/" to make "letterheads/{kind}/{name}", writes to data/images/active_letterhead, deletes old.

Create packages/control/tests/test_migration.py:
- test_moves_uploads_and_pointer — seed old layout in tmp_path (monkeypatch LAFUFU_PRINTER_DATA_DIR), run migration, assert new layout values + pointer bucket prefix.
- test_idempotent — pre-populate new layout, run migration, assert nothing clobbered.

Wire into create_app: at the very top of the function call migrate_letterhead_data().

- [ ] Steps: failing tests → implement → tests pass → wire → commit feat(control): one-shot letterhead migration to data/images.

---

## Phase 2 — Frame + Expression DB and CRUD

### Task 2.1 — Frame SQLModel

Create packages/control/src/lafufu_control/models/frame.py with a Frame SQLModel: name (PK, max 100), 5 int columns (head_lr, head_ud, eye, jaw, brow), image (nullable str, max 160), description (nullable str, max 500). Re-export from models/__init__.py. Smoke import. Commit feat(control): Frame SQLModel.

### Task 2.2 — Replace scaffold Expression model

Rewrite packages/control/src/lafufu_control/models/expression.py with: name (PK), playback (str default "once"), default_duration_ms (250), default_delay_ms (80), default_easing (str default "ease-in-out"), steps_json (str default "[]"), emotion (nullable, unique), description (nullable). Commit feat(control): real Expression SQLModel.

### Task 2.3 — Frame CRUD endpoints

Append to packages/control/src/lafufu_control/api/routers/animator.py (ensure HTTPException and Request imported). Add a FrameBody Pydantic model and four endpoints:

  GET /frames -> list_frames returns {items: [...]}
  POST /frames -> create_frame, 400 if no name, 409 if exists, persist Frame(**body)
  PUT /frames/{name} -> update_frame, 404 if absent, setattr each field
  DELETE /frames/{name} (status 204) -> idempotent delete

Create packages/control/tests/test_api_frames.py with the standard client fixture (monkeypatch LAFUFU_PRINTER_DATA_DIR to tmp, init sqlite, TestClient). Three tests: create+list, update, delete. Use a _pose(**over) helper returning idle defaults. TDD: failing → implement → pass → commit feat(animator): Frame CRUD endpoints.

### Task 2.4 — Pose snapshot endpoint

Append:

  @router.post("/frames/{name}/snapshot")
  def snapshot_frame(name, req):
      pose = getattr(req.app.state, "last_pose", None)
      if not pose: raise HTTPException(409, "no live pose available yet")
      # upsert Frame(name, **pose)
      return {"ok": True, "name": name}

Test test_snapshot_current_pose: set client.app.state.last_pose to a known dict, POST snapshot, verify the frame appears with that pose. TDD → commit feat(animator): pose snapshot endpoint.

### Task 2.5 — Expression CRUD endpoints

Append ExpressionStep and ExpressionBody Pydantic models and four endpoints (list/post/put/delete) mirroring the Frame pattern. _e2d(e) helper converts Expression to dict with steps as parsed JSON. POST/PUT serialise steps via json.dumps([st.model_dump(exclude_none=True) for st in body.steps]). Tests in packages/control/tests/test_api_expressions.py: create_expression (validates playback, len(steps), emotion), update_expression_steps (changes playback + step duration_ms), delete_expression. TDD → commit feat(animator): Expression CRUD endpoints.


---

## Phase 3 — Animator keyframe player

### Task 3.1 — Update AnimatorIntentPlayExpression schema

In packages/shared/src/lafufu_shared/schemas.py, replace the existing AnimatorIntentPlayExpression with three classes:

  AnimatorPlayStep — pose (AnimatorPose), image (nullable str), duration_ms (nullable int), delay_ms (nullable int), easing (nullable str).
  AnimatorIntentPlayExpression — name, playback (Literal once/loop/shuffle, default once), steps (list[AnimatorPlayStep]), default_duration_ms (250), default_delay_ms (80), default_easing ("ease-in-out").
  AnimatorEventFrame — expression, step_index, frame, image (nullable), started_at_ms.

Smoke import. Commit feat(shared): resolved play-expression schema.

### Task 3.2 — Expression compiler

Create packages/control/src/lafufu_control/animation/__init__.py (empty) and animation/compile.py with two functions:

  compile_expression(expr, frames_by_name) -> AnimatorIntentPlayExpression
    Parse steps_json. For each step, look up the named frame (KeyError on miss). Build AnimatorPlayStep with pose=AnimatorPose(head_lr=f.head_lr, ...), image=f.image, and per-step override fields from the raw step dict. Return AnimatorIntentPlayExpression with playback/defaults propagated.

  required_frame_names(expr) -> Iterable[str]
    Returns the frame names referenced in steps_json (one entry per step).

Create test_animation_compile.py with three tests:
- test_compile_resolves_frames_to_poses (two frames, override preserved, default falls through to None on un-overridden step)
- test_compile_missing_frame_raises (pytest.raises KeyError)
- test_compile_preserves_image_from_frame (image string flows from Frame to AnimatorPlayStep)

TDD → commit feat(control): Expression compiler.

### Task 3.3 — /expressions/{name}/play endpoint

Append to the animator router:

  @router.post("/expressions/{name}/play", status_code=202)
  def play_expression(name, req):
      # session.get(Expression, name) -> 404 if missing
      # need = list(required_frame_names(e))
      # frames = {f.name: f for f in select(Frame).where(Frame.name.in_(need)).all()}
      # missing = [n for n in need if n not in frames]; if missing: 409
      # payload = compile_expression(e, frames)
      # nats_publish("animator.intent.play_expression", payload.model_dump())
      # return {"ok": True}

Append test_play_publishes_resolved_payload to test_api_expressions.py — build a fresh TestClient with a capturing nats_publish lambda, create frame + expression, POST /play, assert captured payload has the right name/playback/steps[0].pose.head_lr.

TDD → commit feat(animator): play endpoint resolves + publishes.

### Task 3.4 — Easing curves helper

Create packages/animator/src/lafufu_animator/easing.py with two functions:

  _clamp01(t) -> float in [0, 1]
  ease(curve, t) — returns t**2 for ease-in, 1-(1-t)**2 for ease-out, t**2*(3-2t) smoothstep for ease-in-out, t (identity) for linear or any unknown curve.

Create test_easing.py with six tests: linear identity (endpoints + 0.5), ease-in below 0.5 at t=0.5, ease-out above 0.5 at t=0.5, ease-in-out exactly 0.5 at t=0.5, unknown curve falls through to linear, input clamping at -1 (→0) and 2 (→1).

TDD → commit feat(animator): easing curves enum.


### Task 3.5 — KeyframePlayer (pure logic)

Create packages/animator/src/lafufu_animator/keyframe_player.py with a dataclass:

  @dataclass
  class KeyframePlayer:
      payload: AnimatorIntentPlayExpression
      start_pose: AnimatorPose
      now_ms: int
      rng_seed: int | None = None
      _shuffle_plan: list[tuple[int, int, int]] = field(default_factory=list)
      _rng: random.Random = field(default_factory=random.Random)

  __post_init__ — seeds rng, precomputes self._cycle_len = sum of (dur + delay) across steps.
  pose_at(now_ms) -> AnimatorPose:
      elapsed = now_ms - self.now_ms
      if no steps: return start_pose
      if playback == "shuffle": return self._shuffle_pose(elapsed)
      if playback == "once" and elapsed >= cycle_len: return last step pose
      if cycle_len: elapsed = elapsed % cycle_len
      return self._linear_pose(elapsed)
  is_done(now_ms) -> bool: True only when playback=="once" and elapsed>=cycle_len.

Helpers in the same module:
  _dur(step, p) / _delay(step, p) / _curve(step, p) — pick per-step override or expression default
  _lerp(a, b, t) = round(a + (b-a) * t)
  _interp(prev, target, t) — returns AnimatorPose with each servo lerped + clamped via pose.clamp_dxl

_linear_pose(elapsed) walks (dur, hold) blocks from cursor=0. Within a block's dur, interp between prev_pose_for(idx) and step.pose using ease(_curve(step), elapsed_in_block / dur). Within hold, returns step.pose. After all blocks, returns last step pose.

_shuffle_pose(elapsed) lazily appends random (step_idx, dur, hold) blocks to self._shuffle_plan until covered. dur = max(20, int(_dur(step,p) * rng.uniform(0.8, 1.2))), hold = max(0, int(_delay(step,p) * rng.uniform(0.8, 1.2))). Walks the plan like _linear_pose, carrying prev_pose between blocks; start_pose is the initial prev_pose.

Create test_keyframe_player.py with three tests:
- test_single_step_interpolates_from_start_to_target: payload default_duration_ms=200, one step pose head_lr=2200, start_pose head_lr=2000. Assert pose_at(0).head_lr == 2000, pose_at(200).head_lr == 2200, 2050 < pose_at(100).head_lr < 2150. is_done(200) is True.
- test_loop_wraps: playback "loop", is_done(400) False; pose_at(300).head_lr between 2050 and 2200 (mid second cycle).
- test_shuffle_advances_with_jitter: 4 step poses with distinct head_lr values, rng_seed=1, sample pose_at at 5 time points spanning multiple windows; assert >=2 distinct head_lr values observed and no exceptions raised.

TDD → commit feat(animator): KeyframePlayer with once/loop/shuffle + easing.

### Task 3.6 — Wire the player into AnimatorService

In packages/animator/src/lafufu_animator/service.py:
- Add: from .keyframe_player import KeyframePlayer; import time (if missing).
- __init__: add self._active_player: KeyframePlayer | None = None and self._active_expression_name: str | None = None.
- Replace _on_play_expression body: set self._last_intent_mono = time.monotonic(); instantiate KeyframePlayer(payload=msg, start_pose=self._current_pose, now_ms=int(time.monotonic()*1000)); set self._active_expression_name = msg.name.
- Delete _idle_animation_loop and _expression_animation_loop entirely.
- Add _keyframe_player_loop (TICK_DT=0.05 = 20Hz): if _has_u2d2 and _active_player is not None, compute target = _active_player.pose_at(now_ms). If time.monotonic() - _last_rms_ts <= 0.5, preserve current jaw (target = target.model_copy(update={jaw: self._current_pose.jaw})). await _safe_apply(target). If _active_player.is_done(now_ms), capture name, clear _active_player + _active_expression_name, publish AnimatorEvent(event="gesture_done", name=...) on ANIMATOR_EVENT_GESTURE_DONE.
- on_startup: spawn only _keyframe_player_loop. on_shutdown: cancel it.
- Idle fallback: when _active_player is None and idle is enabled, replay the cached idle payload (loaded once on startup via a NATS request/reply to control, or via direct DB read if engine is shared; keep simple by caching the resolved idle AnimatorIntentPlayExpression payload on first play).

Update tests in test_service.py:
- Replace any probe of self._current_expression with self._active_expression_name.
- Add test_play_expression_drives_target_through_keyframes: publish a small AnimatorIntentPlayExpression (two steps, playback "once", default_duration_ms=120), wait ~200ms, assert bus.last_position("head_lr") matches the second step target within +/-2 ticks.
- Drop tests of the retired procedural loops.

TDD/fix-drift → commit feat(animator): replace procedural loops with keyframe player.

### Task 3.7 — Seed eight built-in expressions

Create packages/control/src/lafufu_control/animation/seed.py. Define:

  IDLE = {head_lr=2063, head_ud=3082, eye=2045, jaw=1728, brow=2075}
  _offset(**deltas) -> dict with each key clamped to IDLE[key] + delta.

  SEED_FRAMES dict (15 named poses):
    agree_low (head_ud +40, brow +10), agree_high (head_ud -15, brow +10),
    disagree_left (head_lr +55, brow -5), disagree_right (head_lr -55, brow -5),
    happy_a (head_ud -30, jaw -40, brow +18), happy_b (head_lr +15, head_ud -25, jaw -40, brow +18),
    sad_a (head_ud +60, eye +5, brow -18), sad_b (head_ud +68, eye +5, brow -18),
    angry_a (head_ud -20, jaw -20, brow -22), angry_b (head_lr +8, head_ud -20, jaw -20, brow -22),
    surprised_held (head_ud -40, jaw -80, brow +20),
    idle_calm (no offset), idle_glance_l (head_lr +12, eye -40), idle_glance_r (head_lr -12, eye +40), idle_look_up (head_ud -20).

  SEED_EXPRESSIONS list of 8 tuples (name, playback, default_dur_ms, default_delay_ms, easing, frame_names_list, emotion):
    agree: once 220 60 ease-in-out [agree_low,agree_high,agree_low,agree_high,agree_low] agree
    disagree: once 220 60 ease-in-out [disagree_left,disagree_right,disagree_left,disagree_right] disagree
    happy: loop 800 300 ease-in-out [happy_a,happy_b] happy
    sad: loop 1500 600 ease-in-out [sad_a,sad_b] sad
    angry: loop 180 50 linear [angry_a,angry_b] angry
    surprised: once 250 1500 ease-out [surprised_held] surprised
    neutral: once 300 100 ease-in-out [idle_calm] neutral
    idle: shuffle 1200 400 ease-in-out [idle_calm,idle_glance_l,idle_glance_r,idle_look_up,idle_calm] idle

  seed_animations(engine) opens a Session; if any Frame or Expression already exists, no-op; otherwise insert all SEED_FRAMES and SEED_EXPRESSIONS (steps_json = json.dumps([{"frame": n} for n in frame_names_list])).

Wire into packages/control/src/lafufu_control/bootstrap.py at the end of the existing bootstrap function: from .animation.seed import seed_animations; seed_animations(engine).

Add test_seed_inserts_eight_emotions to test_api_expressions.py — call seed_animations(engine), list via API, assert the eight expected emotion bindings are present, then call seed_animations(engine) again and assert item count unchanged.

Commit feat(control): seed eight built-in keyframe expressions.

### Task 3.8 — Activate-emotion endpoint

Append to animator router:

  @router.post("/expressions/{name}/activate")
  def activate_expression(name, req):
      # session.get(Expression, name) -> 404 if missing
      # if not e.emotion: 400 ("expression must have an emotion to activate")
      # for other in select(Expression).where(Expression.emotion == e.emotion):
      #     if other.name != e.name: other.emotion = None; s.add(other)
      # s.commit()
      return {"ok": True}

Test test_activate_emotion_clears_previous_owner: create two expressions v1 and v2, both bound to "happy" (use a direct PUT to bypass any uniqueness check during setup). POST /v1/activate, assert only v1 has emotion=happy in the list.

Commit feat(animator): /expressions/{name}/activate.

---

## Phase 4 — Frontend: Frames section

### Task 4.1 — api.ts additions

Modify `web/src/shared/api.ts`. Add these exported types:

```typescript
export type ImageAsset = { kind: "default" | "upload"; name: string; size_bytes: number };
export type FrameDTO = {
  name: string;
  head_lr: number; head_ud: number; eye: number; jaw: number; brow: number;
  image: string | null;
  description: string | null;
};
export type ExpressionStepDTO = {
  frame: string;
  duration_ms?: number; delay_ms?: number; easing?: string;
};
export type ExpressionDTO = {
  name: string;
  playback: "once" | "loop" | "shuffle";
  default_duration_ms: number;
  default_delay_ms: number;
  default_easing: string;
  steps: ExpressionStepDTO[];
  emotion: string | null;
  description: string | null;
};
```

And methods on the `api` object — each is a one-liner over the existing `req()` / multipart helpers, mirroring the printer methods already in the file:

- `listImages(bucket)` → `GET /images/{bucket}` returning `{items: ImageAsset[]}`
- `imageFileUrl(bucket, kind, name)` → returns string URL (no fetch, just builds the path)
- `uploadImage(bucket, file: File)` → `POST /images/{bucket}/upload` via FormData
- `deleteImage(bucket, name)` → `DELETE /images/{bucket}/upload/{name}`
- `listFrames()` → `GET /animator/frames`
- `createFrame(body)` → `POST /animator/frames`
- `updateFrame(name, body)` → `PUT /animator/frames/{name}`
- `deleteFrame(name)` → `DELETE /animator/frames/{name}`
- `snapshotFrame(name)` → `POST /animator/frames/{name}/snapshot`
- `listExpressions()` → `GET /animator/expressions`
- `createExpression(body)` → `POST /animator/expressions`
- `updateExpression(name, body)` → `PUT /animator/expressions/{name}`
- `deleteExpression(name)` → `DELETE /animator/expressions/{name}`
- `playExpression(name)` → `POST /animator/expressions/{name}/play`
- `activateExpression(name)` → `POST /animator/expressions/{name}/activate`

Verify: `cd web && npx tsc --noEmit` exits 0.

Commit: `feat(web): api methods for frames/expressions/images`.

### Task 4.2 — ImagePicker component

Create `web/src/admin/image_picker.tsx`. Props:

```typescript
type Props = {
  bucket: "letterheads" | "sprites";
  current?: string | null;
  onPick: (ref: string | null) => void;
};
```

Behavior:

- `createResource(bucket, b => api.listImages(b))` for items.
- Top row: hidden `<input type=file accept="image/*">` triggered by an "Upload" button. On change, call `api.uploadImage(bucket, file)`, then `refetch()`. Add a "Clear" button beside it that calls `onPick(null)`.
- Grid: Tailwind `grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2`. Each tile is a `<button>` with an `<img class="object-contain h-20 w-full">`. `src = api.imageFileUrl(bucket, item.kind, item.name)`.
- The selected tile (computed as `current === "{bucket}/{item.kind}/{item.name}"`) gets an amber border (`border-amber-400 border-2`); others get neutral border.
- Clicking a tile calls `onPick(`{bucket}/{item.kind}/{item.name}`)`.

Verify: `cd web && npx tsc --noEmit` exits 0.

Commit: `feat(web): shared ImagePicker`.

### Task 4.3 — FramesSection component

Create `web/src/admin/frames_section.tsx`. Props: `{ nats: NatsWs }`.

Signals:

- `frames` (`FrameDTO[]`) — populated from `api.listFrames()` on mount.
- `selectedName` (string | null).
- `pose` (`Record<"head_lr"|"head_ud"|"eye"|"jaw"|"brow", number>`).
- `pickerOpen` (bool).

Helpers:

- `selected = createMemo(() => frames().find(f => f.name === selectedName()) ?? null)`.
- `reload = async () => setFrames((await api.listFrames()).items)`.
- When `selectedName()` changes, copy the frame's pose into the `pose` signal.

Layout — Tailwind `grid gap-4 grid-cols-[1fr_1.4fr_1fr]`:

1. **Left column** — scroll-warm list of frame names. Header includes a "Snapshot" button that `window.prompt`s for a name, then calls `api.snapshotFrame(name)` → `reload()` → `setSelectedName(name)`. Each list item is a `<button>` that sets `selectedName`; the selected one gets `bg-amber-500/20`.
2. **Middle column** — selected-frame card:
   - Image preview: parse `selected().image` as `{bucket}/{kind}/{name}` and call `api.imageFileUrl(...)`. If null, show a placeholder.
   - "Pick image" button toggles `pickerOpen()`. When open, mount `<ImagePicker bucket="sprites" current={selected().image} onPick={ref => { selected().image = ref; setFrames([...frames()]); setPickerOpen(false); }} />`.
   - "Save" button calls `api.updateFrame(selected().name, {...pose(), image: selected().image, description: selected().description})` then `reload()`.
   - "Delete" button calls `api.deleteFrame(selected().name)` after `window.confirm`, then clears selection and reloads.
3. **Right column** — five sliders, one per servo. Ranges (calibrated DXL min/max):
   - `head_lr` 1828..2298
   - `head_ud` 2885..3278
   - `eye` 1960..2130
   - `jaw` 1534..1728
   - `brow` 2051..2099

   On `input`, update `pose` and schedule a 40ms-debounced `api.animatorPreview(servoName, value)` — same live-preview path that already exists for the body sliders. Use a single shared `previewTimer` ref to debounce across all sliders.

Verify: `cd web && npx tsc --noEmit && npx vitest run` exit 0.

Commit: `feat(web): FramesSection`.

### Task 4.4 — Plug FramesSection into BodyPanel

Rewrite `web/src/admin/body_panel.tsx` as a thin orchestrator:

```typescript
import { Component } from "solid-js";
import { NatsWs } from "../shared/nats_ws";
import { FramesSection } from "./frames_section";

export const BodyPanel: Component<{ nats: NatsWs }> = (props) => (
  <div class="flex flex-col gap-6">
    <FramesSection nats={props.nats} />
    {/* ExpressionsSection lands in Phase 5 */}
  </div>
);
```

Delete the old procedural-expressions UI and any orphaned helpers/imports in `body_panel.tsx`.

Verify: `cd web && npx tsc --noEmit && npx vitest run` exit 0.

Commit: `refactor(web): BodyPanel hosts FramesSection`.

---

## Phase 5 — Frontend: Expressions section

### Task 5.1 — ExpressionsSection component

Create `web/src/admin/expressions_section.tsx`. Signals:

- `expressions` (`ExpressionDTO[]`).
- `frames` (`FrameDTO[]`) — needed for the "add step" picker.
- `selectedName` (string | null).

`onMount`:

```typescript
const [exps, frs] = await Promise.all([api.listExpressions(), api.listFrames()]);
setExpressions(exps.items);
setFrames(frs.items);
```

Layout — Tailwind `grid gap-4 grid-cols-[1fr_2fr]`:

1. **Left column** — gallery list. Header has a "New expression" button that `window.prompt`s a name and POSTs an empty expression. Each list item shows:
   - Line 1: the expression name (bold).
   - Line 2 (muted): `{playback} · {n} step(s){emotion ? ` · ${emotion}` : ""}`.

   Clicking sets `selectedName`. Selected item gets `bg-amber-500/20`.

2. **Right column** — selected-expression editor:
   - Top row: "Play" button (`api.playExpression(selected().name)`), "Save" button, "Delete" button.
   - `<select>` for `playback` (once / loop / shuffle), bound to `selected().playback`.
   - `<select>` for `emotion` (idle / agree / disagree / happy / sad / angry / surprised / neutral / none).
   - Steps row: render `selected().steps` as a flex-wrap row of chips. Each chip shows the frame name + a small "×" button that splices the step out. Mutating in place + `setExpressions([...expressions()])` pokes reactivity.
   - "Add frame" row beneath: render every `frames()` name as a click-to-append button. Click → `selected().steps.push({frame: name})` + `setExpressions([...expressions()])`.

`save` calls:

```typescript
await api.updateExpression(selected().name, {
  playback: selected().playback,
  default_duration_ms: selected().default_duration_ms,
  default_delay_ms: selected().default_delay_ms,
  default_easing: selected().default_easing,
  steps: selected().steps,
  emotion: selected().emotion,
  description: selected().description,
});
```

Mount in `body_panel.tsx` after `FramesSection`:

```typescript
import { ExpressionsSection } from "./expressions_section";
// ...
<ExpressionsSection nats={props.nats} />
```

Verify: `cd web && npx tsc --noEmit && npx vitest run` exit 0.

Commit: `feat(web): ExpressionsSection`.

### Task 5.2 — Drag-to-reorder steps

Install:

```bash
cd web && npm install @thisbeyond/solid-dnd
```

This is the SolidJS-native equivalent of `@dnd-kit/sortable` used by `maven-demo`.

In `expressions_section.tsx`, wrap the steps `<For>` in `<DragDropProvider><DragDropSensors><SortableProvider>`. Each step uses `createSortable(`step-${i}`)`. On drag-end (`event.draggable.id`, `event.droppable.id` like `"step-N"`):

```typescript
const from = parseInt(String(event.draggable.id).split("-")[1]);
const to = parseInt(String(event.droppable.id).split("-")[1]);
const steps = selected().steps;
const [moved] = steps.splice(from, 1);
steps.splice(to, 0, moved);
setExpressions([...expressions()]);
```

Verify: `cd web && npx tsc --noEmit` exits 0. Manually drag two chips and confirm order persists after save+reload.

Commit: `feat(web): drag-to-reorder expression steps`.

---

## Phase 6 — Verification + deploy

### Task 6.1 — Full backend pass

- [ ] `.venv/Scripts/pytest packages/ -q` — expect 0 failures.
- [ ] `.venv/Scripts/ruff check packages/` — clean.
- [ ] If `packages/animator/tests/test_service.py` references the retired procedural loops, update it to assert against the keyframe player path instead.

### Task 6.2 — Full frontend pass

- [ ] `cd web && npx tsc --noEmit`
- [ ] `cd web && npx vitest run`

### Task 6.3 — Rebuild static bundle + manual smoke

- [ ] `cd web && npm run build`
- [ ] `git checkout -- packages/control/src/lafufu_control/static/lafufu-bg.mp4` (preserve the out-of-band asset, same workflow as previous deploys).
- [ ] Hit `/admin` (proxied to the Pi) — confirm:
  - Frames + Expressions sections render under the Body tab.
  - Snapshot adds a frame using the current servo positions.
  - Sliders move Lafufu live (NATS preview path still wired).
  - Playing a built-in expression cycles through its steps and visibly moves the robot.
  - With no active expression, idle is the fallback and Lafufu drifts gently.

### Task 6.4 — Commit + push + deploy to Pi

- [ ] Stage new + updated files explicitly (skip `.claude/`, loose user files).
- [ ] `git commit -m "feat: animation system (frames + expressions + keyframe player)"`
- [ ] Push `feature/animation-system`, open PR, merge to `main`.
- [ ] On the Pi (`ssh lafufu@172.20.10.11`): `cd /srv/lafufu && git pull && sudo systemctl restart lafufu-control lafufu-animator`.
- [ ] Smoke the deployed UI from the dev box one more time.

---

## Risks during execution

- **Player tuning.** The seeded expressions reproduce the procedural feel with 2-6 hand-authored keyframes. Expect to tune the idle pool size and the agree-cycle count after seeing it on the robot — bake in time for a tuning pass after Task 6.3.
- **Schema drift on `play_expression`.** Any external publisher still sending `{name, intensity}` will be rejected by the new schema. The existing `/animator/expression` POST endpoint must stay for back-compat — verify in Phase 3 that the legacy body still resolves through the new `compile_expression` path.
- **Migration safety.** The runtime migration moves files on the Pi (Task 1.6). Dry-run against a copy of `data/printer/` before the real deploy; back up `data/` first.
- **Idle starvation.** When no expression is active, `_keyframe_player_loop` should fall back to the expression bound to `emotion="idle"`. The implementer adds this fallback hook in Task 3.6 — if forgotten, Lafufu freezes between commands.
- **Bottango compatibility scope creep.** The spec marks bottango as forward-compat sketch only. If a reviewer pushes to wire it in this PR, defer — it lands in a follow-up plan after the handmade player is proven on hardware.

