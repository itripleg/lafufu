# Unified Expression Registry & Live State Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the agent → animator link, add a built-in lifecycle (is_builtin flag, reset, delete/rename guards), expose servo config over the API, and make frontend list resources reactively reflect backend changes — fixing the "expressions feel out of sync" and "Labubu doesn't react to its own words" pain.

**Architecture:** Add `is_builtin` to `Frame` and `Expression`; make `seed_animations()` per-row idempotent. New `POST /api/animator/.../reset` endpoints restore from seed. Existing `DELETE` and `PUT` routes refuse on `is_builtin=true`. The control plane (which already subscribes to `agent.reply` for chat history) gains a second branch that looks up the reply's emotion as an expression name and publishes `animator.intent.play_expression`. New `GET /api/animator/config` exposes `pose.py` ranges + idle defaults. The CRUD routers publish `expressions.changed` / `frames.changed` on mutations. A small `createReactiveResource(fetchFn, topics)` helper in `web/src/shared/` wraps Solid `createResource` with NATS-subscribe refetch — adopted by expressions, frames, and the new servo-config resource.

**Tech Stack:** Python 3 + FastAPI + SQLModel (control), asyncio + nats-py (animator/agent), TypeScript + Solid.js + Vite + vitest (web), pytest (Python tests).

**Spec:** `docs/superpowers/specs/2026-05-25-unified-expression-registry-design.md`

---

## File Map

**Modify:**
- `packages/control/src/lafufu_control/models/frame.py` — add `is_builtin`
- `packages/control/src/lafufu_control/models/expression.py` — add `is_builtin`
- `packages/control/src/lafufu_control/animation/seed.py` — per-row idempotence, set `is_builtin`, expose `apply_*_seed` helpers
- `packages/control/src/lafufu_control/api/routers/animator.py` — `/reset` endpoints, built-in guards, `/config` endpoint, publish `*.changed` events on CRUD
- `packages/control/src/lafufu_control/service.py` — control's `on_reply` handler also resolves expression and publishes play intent; `_publish_idle_expression` looks up by `name="idle"` instead of `emotion="idle"`
- `packages/animator/src/lafufu_animator/service.py:407-413` — remove the `_on_agent_reply` no-op stub (control handles it now)
- `packages/agent/src/lafufu_agent/emotion_parser.py` — drop `_VALID_EMOTIONS` set and `neutral` fallback
- `web/src/admin/expressions_section.tsx` — adopt `createReactiveResource` for `expressions` and `frames`; show Reset on built-ins; hide Delete on built-ins; drop hardcoded `EMOTIONS` array
- `web/src/pet/head_drag.ts` — take `ranges` as a parameter
- `web/src/pet/pet.tsx` — read ranges from `useServoConfig()` resource
- `web/tests/head_drag.test.ts` — pass `ranges` to the new pure function
- `web/src/shared/api.ts` — add `resetExpression`, `resetFrame`, `getAnimatorConfig`

**Create:**
- `web/src/shared/reactive_resource.ts` — `createReactiveResource` helper
- `web/src/shared/use_servo_config.ts` — `useServoConfig` resource
- `packages/control/tests/test_animator_builtin.py` — guard + reset tests
- `packages/control/tests/test_animator_config.py` — `/config` endpoint test
- `packages/control/tests/test_animator_changed_events.py` — CRUD publishes `*.changed`
- `packages/control/tests/test_agent_reply_to_pose.py` — control resolves emotion → play intent
- `packages/control/tests/test_seed_idempotence.py` — per-row seed idempotence
- `web/tests/reactive_resource.test.ts` — helper unit test
- `packages/agent/tests/test_emotion_parser.py` — verify parser no longer defaults to neutral (or update existing)

**Delete:**
- `web/src/pet/servo_ranges.ts`
- `packages/animator/src/lafufu_animator/expressions.py`
- `packages/animator/tests/test_expressions.py`

---

### Task 1: Add `is_builtin` column to Frame and Expression

**Files:**
- Modify: `packages/control/src/lafufu_control/models/frame.py`
- Modify: `packages/control/src/lafufu_control/models/expression.py`
- Modify: `packages/control/src/lafufu_control/db.py`
- Test: `packages/control/tests/test_models_is_builtin.py`

- [ ] **Step 1: Write the failing test**

Create `packages/control/tests/test_models_is_builtin.py`:

```python
from lafufu_control.models import Expression, Frame
from lafufu_control.db import create_engine_for_path, init_db
from sqlmodel import Session


def test_frame_is_builtin_defaults_false(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Frame(name="x", head_lr=0, head_ud=0, eye=0, jaw=0, brow=0))
        s.commit()
        f = s.get(Frame, "x")
        assert f.is_builtin is False


def test_expression_is_builtin_defaults_false(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Expression(name="x"))
        s.commit()
        e = s.get(Expression, "x")
        assert e.is_builtin is False


def test_frame_is_builtin_can_be_true(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    with Session(engine) as s:
        s.add(Frame(name="x", head_lr=0, head_ud=0, eye=0, jaw=0, brow=0, is_builtin=True))
        s.commit()
        assert s.get(Frame, "x").is_builtin is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_models_is_builtin.py -v`
Expected: FAIL with `AttributeError` or unexpected kwarg `is_builtin`.

- [ ] **Step 3: Add `is_builtin` to Frame**

Edit `packages/control/src/lafufu_control/models/frame.py`:

```python
from sqlmodel import Field, SQLModel


class Frame(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    head_lr: int
    head_ud: int
    eye: int
    jaw: int
    brow: int
    image: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    is_builtin: bool = Field(default=False)
```

- [ ] **Step 4: Add `is_builtin` to Expression**

Edit `packages/control/src/lafufu_control/models/expression.py`:

```python
from sqlmodel import Field, SQLModel


class Expression(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    playback: str = Field(default="once", max_length=20)
    default_duration_ms: int = 250
    default_delay_ms: int = 80
    default_easing: str = Field(default="ease-in-out", max_length=30)
    steps_json: str = Field(default="[]")
    emotion: str | None = Field(default=None, max_length=40, unique=True)
    description: str | None = Field(default=None, max_length=500)
    is_builtin: bool = Field(default=False)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_models_is_builtin.py -v`
Expected: all 3 PASS.

- [ ] **Step 6: Add additive migration for existing DBs**

SQLModel's `create_all` only creates a table if it doesn't exist; it does NOT add columns to an existing table. For existing `dev-control.db` data, add an explicit `ALTER TABLE` migration inside `init_db`.

First read `packages/control/src/lafufu_control/db.py` to confirm `init_db`'s current shape. Then add (or integrate) a migration block after `SQLModel.metadata.create_all(engine)`:

```python
def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
    # Additive migrations for existing on-disk DBs.
    with engine.connect() as conn:
        for table in ("frame", "expression"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "is_builtin" not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0"
                )
        conn.commit()
```

- [ ] **Step 7: Run all control tests to verify nothing broke**

Run: `uv run --package lafufu-control pytest packages/control/tests/ -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/control/src/lafufu_control/models/frame.py packages/control/src/lafufu_control/models/expression.py packages/control/src/lafufu_control/db.py packages/control/tests/test_models_is_builtin.py
git commit -m "feat(control): add is_builtin flag to Frame and Expression"
```

---

### Task 2: Per-row idempotent seed with `is_builtin=True`

**Files:**
- Modify: `packages/control/src/lafufu_control/animation/seed.py`
- Test: `packages/control/tests/test_seed_idempotence.py`

- [ ] **Step 1: Write the failing test**

Create `packages/control/tests/test_seed_idempotence.py`:

```python
from lafufu_control.animation.seed import seed_animations, SEED_FRAMES, SEED_EXPRESSIONS
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models import Expression, Frame
from sqlmodel import Session, select


def _engine(tmp_path):
    e = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(e)
    return e


def test_seed_on_empty_db_inserts_all_with_is_builtin(tmp_path):
    engine = _engine(tmp_path)
    seed_animations(engine)
    with Session(engine) as s:
        frames = s.exec(select(Frame)).all()
        exprs = s.exec(select(Expression)).all()
        assert len(frames) == len(SEED_FRAMES)
        assert len(exprs) == len(SEED_EXPRESSIONS)
        assert all(f.is_builtin for f in frames)
        assert all(e.is_builtin for e in exprs)


def test_seed_does_not_clobber_user_edits(tmp_path):
    engine = _engine(tmp_path)
    seed_animations(engine)
    with Session(engine) as s:
        e = s.get(Expression, "happy")
        e.description = "user-edited"
        s.add(e)
        s.commit()
    seed_animations(engine)  # second run
    with Session(engine) as s:
        assert s.get(Expression, "happy").description == "user-edited"


def test_seed_runs_after_user_creates_a_row(tmp_path):
    """Regression: old bail-if-anything-exists seed skipped built-ins entirely
    when a user had created a single custom expression. Per-row should still
    insert built-ins."""
    engine = _engine(tmp_path)
    with Session(engine) as s:
        s.add(Expression(name="my_custom"))
        s.commit()
    seed_animations(engine)
    with Session(engine) as s:
        for name, *_ in SEED_EXPRESSIONS:
            assert s.get(Expression, name) is not None, f"missing seed: {name}"
        assert s.get(Expression, "my_custom").is_builtin is False


def test_seed_backfills_is_builtin_on_pre_existing_rows(tmp_path):
    """If the seed ran before is_builtin existed, existing rows need the flag
    set when the new seed runs."""
    engine = _engine(tmp_path)
    with Session(engine) as s:
        first_seed_name = SEED_EXPRESSIONS[0][0]
        s.add(Expression(name=first_seed_name, is_builtin=False))
        s.commit()
    seed_animations(engine)
    with Session(engine) as s:
        assert s.get(Expression, first_seed_name).is_builtin is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_seed_idempotence.py -v`
Expected: failures — the current seed bails entirely if any row exists and never sets `is_builtin`.

- [ ] **Step 3: Rewrite `seed_animations` to be per-row idempotent**

Replace the body of `seed_animations` in `packages/control/src/lafufu_control/animation/seed.py`:

```python
def seed_animations(engine) -> None:
    """Per-row upsert: insert missing seed rows, backfill is_builtin on
    pre-existing seed-named rows. Never clobbers user edits."""
    with Session(engine) as s:
        for name, pose in SEED_FRAMES.items():
            existing = s.get(Frame, name)
            if existing is None:
                s.add(Frame(name=name, is_builtin=True, **pose))
            elif not existing.is_builtin:
                existing.is_builtin = True
                s.add(existing)
        for (
            name,
            playback,
            dur_ms,
            delay_ms,
            easing,
            frame_names,
            emotion,
        ) in SEED_EXPRESSIONS:
            existing = s.get(Expression, name)
            if existing is None:
                if playback == "random_walk":
                    steps_json = json.dumps(IDLE_RANDOM_WALK_CONFIG)
                else:
                    steps_json = json.dumps([{"frame": n} for n in frame_names])
                s.add(
                    Expression(
                        name=name,
                        playback=playback,
                        default_duration_ms=dur_ms,
                        default_delay_ms=delay_ms,
                        default_easing=easing,
                        steps_json=steps_json,
                        emotion=emotion,
                        is_builtin=True,
                    )
                )
            elif not existing.is_builtin:
                existing.is_builtin = True
                s.add(existing)
        s.commit()
```

- [ ] **Step 4: Add seed-overwrite helpers used by reset endpoints**

Append to `packages/control/src/lafufu_control/animation/seed.py`:

```python
def apply_frame_seed(s: Session, name: str) -> Frame:
    """Overwrite an existing Frame row from its SEED_FRAMES entry. Caller
    holds the session open; commit is the caller's responsibility."""
    pose = SEED_FRAMES.get(name)
    if pose is None:
        raise KeyError(f"no seed for frame {name!r}")
    f = s.get(Frame, name)
    if f is None:
        f = Frame(name=name, is_builtin=True, **pose)
        s.add(f)
        return f
    for k, v in pose.items():
        setattr(f, k, v)
    f.is_builtin = True
    s.add(f)
    return f


def apply_expression_seed(s: Session, name: str) -> Expression:
    """Overwrite an existing Expression row from its SEED_EXPRESSIONS entry."""
    seed = next((row for row in SEED_EXPRESSIONS if row[0] == name), None)
    if seed is None:
        raise KeyError(f"no seed for expression {name!r}")
    _, playback, dur_ms, delay_ms, easing, frame_names, emotion = seed
    if playback == "random_walk":
        steps_json = json.dumps(IDLE_RANDOM_WALK_CONFIG)
    else:
        steps_json = json.dumps([{"frame": n} for n in frame_names])
    e = s.get(Expression, name)
    if e is None:
        e = Expression(name=name, is_builtin=True)
        s.add(e)
    e.playback = playback
    e.default_duration_ms = dur_ms
    e.default_delay_ms = delay_ms
    e.default_easing = easing
    e.steps_json = steps_json
    e.emotion = emotion
    e.is_builtin = True
    s.add(e)
    return e


def is_builtin_frame_name(name: str) -> bool:
    return name in SEED_FRAMES


def is_builtin_expression_name(name: str) -> bool:
    return any(row[0] == name for row in SEED_EXPRESSIONS)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_seed_idempotence.py -v`
Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/control/src/lafufu_control/animation/seed.py packages/control/tests/test_seed_idempotence.py
git commit -m "feat(control): per-row idempotent seed, set is_builtin, add seed helpers"
```

---

### Task 3: Reset endpoints for built-in Frame and Expression

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/animator.py`
- Test: `packages/control/tests/test_animator_reset.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/control/tests/test_animator_reset.py`:

```python
import pytest
from fastapi.testclient import TestClient
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app)


def test_reset_expression_restores_factory_steps(client):
    r = client.put("/api/animator/expressions/happy", json={
        "playback": "once",
        "default_duration_ms": 9999,
        "default_delay_ms": 9999,
        "default_easing": "linear",
        "steps": [],
        "random_walk_config": None,
        "emotion": "happy",
        "description": "edited",
    })
    assert r.status_code == 200
    assert r.json()["default_duration_ms"] == 9999

    r = client.post("/api/animator/expressions/happy/reset")
    assert r.status_code == 200
    body = r.json()
    # Built-in happy seeds with default_duration_ms=800 (see seed.py)
    assert body["default_duration_ms"] == 800
    assert body["playback"] == "loop"


def test_reset_frame_restores_factory_pose(client):
    r = client.put("/api/animator/frames/idle_calm", json={
        "head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0,
    })
    assert r.status_code == 200
    r = client.post("/api/animator/frames/idle_calm/reset")
    assert r.status_code == 200
    # IDLE is {"head_lr": 2063, …} in seed.py — reset should bring head_lr back
    assert r.json()["head_lr"] == 2063


def test_reset_expression_rejects_non_builtin(client):
    r = client.post("/api/animator/expressions", json={"name": "user_one", "steps": []})
    assert r.status_code == 200
    r = client.post("/api/animator/expressions/user_one/reset")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "not_builtin"


def test_reset_frame_rejects_non_builtin(client):
    r = client.post("/api/animator/frames", json={
        "name": "user_frame",
        "head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0,
    })
    assert r.status_code == 200
    r = client.post("/api/animator/frames/user_frame/reset")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "not_builtin"


def test_reset_expression_missing_returns_404(client):
    r = client.post("/api/animator/expressions/zzz_nope/reset")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_reset.py -v`
Expected: 404s on all reset endpoints — they don't exist yet.

- [ ] **Step 3: Add the reset endpoints**

In `packages/control/src/lafufu_control/api/routers/animator.py`, add an import near the top:

```python
from ...animation.seed import apply_expression_seed, apply_frame_seed
```

Append these handlers to the file:

```python
@router.post("/expressions/{name}/reset")
def reset_expression(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is None:
            raise HTTPException(
                404, detail={"error_code": "not_found", "message": f"no expression {name!r}"}
            )
        if not e.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "not_builtin",
                    "message": f"expression {name!r} is not a built-in and cannot be reset",
                },
            )
        apply_expression_seed(s, name)
        s.commit()
        e = s.get(Expression, name)
        return _e2d(e)


@router.post("/frames/{name}/reset")
def reset_frame(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        f = s.get(Frame, name)
        if f is None:
            raise HTTPException(
                404, detail={"error_code": "not_found", "message": f"no frame {name!r}"}
            )
        if not f.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "not_builtin",
                    "message": f"frame {name!r} is not a built-in and cannot be reset",
                },
            )
        apply_frame_seed(s, name)
        s.commit()
        f = s.get(Frame, name)
        return _f2d(f)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_reset.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/control/src/lafufu_control/api/routers/animator.py packages/control/tests/test_animator_reset.py
git commit -m "feat(control): add /expressions/{name}/reset and /frames/{name}/reset endpoints"
```

---

### Task 4: Delete & rename guards for built-ins; expose `is_builtin` in API responses

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/animator.py`
- Test: `packages/control/tests/test_animator_builtin_guards.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/control/tests/test_animator_builtin_guards.py`:

```python
import pytest
from fastapi.testclient import TestClient
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app)


def test_delete_builtin_expression_rejected(client):
    r = client.delete("/api/animator/expressions/happy")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "is_builtin"


def test_delete_builtin_frame_rejected(client):
    r = client.delete("/api/animator/frames/idle_calm")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "is_builtin"


def test_delete_user_expression_ok(client):
    client.post("/api/animator/expressions", json={"name": "mine", "steps": []})
    r = client.delete("/api/animator/expressions/mine")
    assert r.status_code == 204


def test_list_expressions_includes_is_builtin(client):
    r = client.get("/api/animator/expressions")
    items = r.json()["items"]
    happy = next(x for x in items if x["name"] == "happy")
    assert happy["is_builtin"] is True
    # Create a user one
    client.post("/api/animator/expressions", json={"name": "user_two", "steps": []})
    r = client.get("/api/animator/expressions")
    user = next(x for x in r.json()["items"] if x["name"] == "user_two")
    assert user["is_builtin"] is False


def test_list_frames_includes_is_builtin(client):
    r = client.get("/api/animator/frames")
    items = r.json()["items"]
    idle = next(x for x in items if x["name"] == "idle_calm")
    assert idle["is_builtin"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_builtin_guards.py -v`
Expected: failures on the guard and `is_builtin` field tests.

- [ ] **Step 3: Add the delete guards**

In `packages/control/src/lafufu_control/api/routers/animator.py`, find `delete_expression` and add the guard as the FIRST check inside the session block (before the existing `emotion` bound check):

```python
        if e.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "is_builtin",
                    "message": f"expression {name!r} is a built-in and cannot be deleted; reset it instead",
                },
            )
```

Insert the analogous guard at the top of `delete_frame`'s session block (after the `if f is None: return None` short-circuit, before the `frame_in_use` LIKE check):

```python
        if f.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "is_builtin",
                    "message": f"frame {name!r} is a built-in and cannot be deleted; reset it instead",
                },
            )
```

- [ ] **Step 4: Expose `is_builtin` in API responses**

In `packages/control/src/lafufu_control/api/routers/animator.py`, update `_f2d` and `_e2d` to include the flag. For `_f2d`, append a single field to the returned dict:

```python
        "is_builtin": f.is_builtin,
```

For `_e2d`, same — append `"is_builtin": e.is_builtin,` to the returned dict (placement at the bottom alongside `"description"` keeps the diff small).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_builtin_guards.py -v`
Expected: all 5 PASS.

- [ ] **Step 6: Run full control test suite**

Run: `uv run --package lafufu-control pytest packages/control/tests/ -v`
Expected: all PASS (no regressions from the `_e2d`/`_f2d` field addition).

- [ ] **Step 7: Commit**

```bash
git add packages/control/src/lafufu_control/api/routers/animator.py packages/control/tests/test_animator_builtin_guards.py
git commit -m "feat(control): is_builtin in API responses; delete-guards for built-in rows"
```

---

### Task 5: Publish `expressions.changed` and `frames.changed` on CRUD

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/animator.py`
- Test: `packages/control/tests/test_animator_changed_events.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/control/tests/test_animator_changed_events.py`:

```python
import pytest
from fastapi.testclient import TestClient
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


def _setup(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    published: list[tuple[str, dict]] = []
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    return TestClient(app), published


def test_create_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.post("/api/animator/frames", json={
        "name": "new_frame",
        "head_lr": 1, "head_ud": 1, "eye": 1, "jaw": 1, "brow": 1,
    })
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "create", "name": "new_frame"}) in pub


def test_update_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.put("/api/animator/frames/idle_calm", json={
        "head_lr": 9, "head_ud": 9, "eye": 9, "jaw": 9, "brow": 9,
    })
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "update", "name": "idle_calm"}) in pub


def test_delete_user_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    c.post("/api/animator/frames", json={
        "name": "tmp", "head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0,
    })
    pub.clear()
    r = c.delete("/api/animator/frames/tmp")
    assert r.status_code == 204
    assert ("frames.changed", {"kind": "delete", "name": "tmp"}) in pub


def test_reset_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.post("/api/animator/frames/idle_calm/reset")
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "reset", "name": "idle_calm"}) in pub


def test_expression_lifecycle_publishes(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    c.post("/api/animator/expressions", json={"name": "ex1", "steps": []})
    assert ("expressions.changed", {"kind": "create", "name": "ex1"}) in pub
    pub.clear()
    c.put("/api/animator/expressions/ex1", json={
        "playback": "once", "default_duration_ms": 250, "default_delay_ms": 80,
        "default_easing": "ease-in-out", "steps": [], "random_walk_config": None,
        "emotion": None, "description": "x",
    })
    assert ("expressions.changed", {"kind": "update", "name": "ex1"}) in pub
    pub.clear()
    c.delete("/api/animator/expressions/ex1")
    assert ("expressions.changed", {"kind": "delete", "name": "ex1"}) in pub
    pub.clear()
    c.post("/api/animator/expressions/happy/reset")
    assert ("expressions.changed", {"kind": "reset", "name": "happy"}) in pub


def test_delete_missing_does_not_publish(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.delete("/api/animator/frames/zzz_never_existed")
    assert r.status_code in (204, 404)
    assert ("frames.changed", {"kind": "delete", "name": "zzz_never_existed"}) not in pub
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_changed_events.py -v`
Expected: all FAIL — no `*.changed` topics are published yet.

- [ ] **Step 3: Add the publishes**

In `packages/control/src/lafufu_control/api/routers/animator.py`, add a publish call to each mutating handler. **Publish AFTER the session block closes and only on the success path.**

For `create_frame`:

```python
        s.add(f)
        s.commit()
        s.refresh(f)
        out = _f2d(f)
    req.app.state.nats_publish("frames.changed", {"kind": "create", "name": body.name})
    return out
```

Apply the same pattern to:

- `update_frame` → publish `("frames.changed", {"kind": "update", "name": name})`
- `delete_frame` → publish `("frames.changed", {"kind": "delete", "name": name})` **only when a row was actually deleted**. The existing `if f is None: return None` early-return must NOT publish; also the `is_builtin` and `frame_in_use` rejection paths must NOT publish. Only after the actual `s.delete(f); s.commit()` succeeds.
- `reset_frame` → publish `("frames.changed", {"kind": "reset", "name": name})`
- `create_expression` → `("expressions.changed", {"kind": "create", "name": body.name})`
- `update_expression` → `("expressions.changed", {"kind": "update", "name": name})`
- `delete_expression` → publish only after the real delete commits (after `is_builtin` and `emotion` guards pass): `("expressions.changed", {"kind": "delete", "name": name})`
- `reset_expression` → `("expressions.changed", {"kind": "reset", "name": name})`

Do NOT publish for `play_expression`, `activate_expression`, `snapshot_frame`, `preview`, `set_pose`, `gesture`, or the legacy `/expression` (intent endpoints, not state changes).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_changed_events.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/control/src/lafufu_control/api/routers/animator.py packages/control/tests/test_animator_changed_events.py
git commit -m "feat(control): publish frames.changed and expressions.changed on CRUD"
```

---

### Task 6: `GET /api/animator/config` endpoint

**Files:**
- Modify: `packages/control/src/lafufu_control/api/routers/animator.py`
- Test: `packages/control/tests/test_animator_config.py`

- [ ] **Step 1: Confirm pose.py constant names**

Open `packages/animator/src/lafufu_animator/pose.py` and confirm the exact names of the idle-position constants. The seed file's IDLE dict references `head_lr=2063, head_ud=3082, eye=2045, jaw=1728, brow=2075`. The same values likely live in pose.py under names like `HEAD_IDLE_LR_DXL`, `HEAD_IDLE_UD_DXL`, etc. — but they may be named differently (e.g., `DXL_HEAD_LR_IDLE`). Substitute the actual names in Step 3 below.

- [ ] **Step 2: Write the failing tests**

Create `packages/control/tests/test_animator_config.py`:

```python
import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app)


def test_get_config_returns_ranges_and_defaults(client):
    r = client.get("/api/animator/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"ranges", "idle_defaults", "idle_overrides"}
    for k in ("head_lr", "head_ud", "eye", "jaw", "brow"):
        assert k in body["ranges"]
        lo, hi = body["ranges"][k]
        assert lo < hi
    from lafufu_animator.pose import CLAMP
    assert body["ranges"]["head_lr"] == list(CLAMP["head_lr"])


def test_get_config_idle_overrides_reflect_settings(client):
    client.put("/api/settings/animator.head_lr.default", json={
        "value": 2077, "value_type": "int",
    })
    r = client.get("/api/animator/config")
    assert r.status_code == 200
    assert r.json()["idle_overrides"].get("head_lr") == 2077
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_config.py -v`
Expected: 404 on `/api/animator/config`.

- [ ] **Step 4: Add the endpoint**

In `packages/control/src/lafufu_control/api/routers/animator.py`, add near the other GET routes. Substitute the real constant names from Step 1:

```python
from lafufu_animator import pose as _pose
from ...models.setting import Setting


@router.get("/config")
def get_animator_config(req: Request):
    """Servo config for the frontend: ranges (CLAMP), factory idle positions,
    and any operator overrides from the settings table."""
    ranges = {k: list(v) for k, v in _pose.CLAMP.items()}
    idle_defaults = {
        "head_lr": _pose.HEAD_IDLE_LR_DXL,
        "head_ud": _pose.HEAD_IDLE_UD_DXL,
        "eye":     _pose.EYE_IDLE_DXL,
        "jaw":     _pose.JAW_IDLE_DXL,
        "brow":    _pose.BROW_IDLE_DXL,
    }
    overrides: dict[str, int] = {}
    with Session(req.app.state.engine) as s:
        for servo in ("head_lr", "head_ud", "eye", "jaw", "brow"):
            row = s.get(Setting, f"animator.{servo}.default")
            if row is not None:
                try:
                    overrides[servo] = int(row.value)
                except (TypeError, ValueError):
                    pass
    return {"ranges": ranges, "idle_defaults": idle_defaults, "idle_overrides": overrides}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_animator_config.py -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/control/src/lafufu_control/api/routers/animator.py packages/control/tests/test_animator_config.py
git commit -m "feat(control): GET /api/animator/config exposes ranges + idle defaults + overrides"
```

---

### Task 7: Fix idle bootstrap to look up by name (not emotion)

**Files:**
- Modify: `packages/control/src/lafufu_control/service.py`

- [ ] **Step 1: Read the existing bootstrap function**

Open `packages/control/src/lafufu_control/service.py` and locate `_publish_idle_expression` (around line 25-41). It currently queries by `Expression.emotion == "idle"`.

- [ ] **Step 2: Change the lookup to name="idle"**

Replace:

```python
e = s.exec(select(Expression).where(Expression.emotion == "idle")).first()
```

with:

```python
e = s.get(Expression, "idle")
```

The surrounding compile + publish logic stays unchanged. This decouples the idle bootstrap from the deprecated `emotion` column.

- [ ] **Step 3: Run all control tests**

Run: `uv run --package lafufu-control pytest packages/control/tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/control/src/lafufu_control/service.py
git commit -m "fix(control): bootstrap idle expression by name (drops dep on deprecated emotion field)"
```

---

### Task 8: Control plane resolves agent.reply emotion → play_expression

**Files:**
- Modify: `packages/control/src/lafufu_control/service.py`
- Test: `packages/control/tests/test_agent_reply_to_pose.py`

- [ ] **Step 1: Write the failing test**

Create `packages/control/tests/test_agent_reply_to_pose.py`:

```python
"""Verify the control plane translates an agent.reply with a known emotion
into an animator.intent.play_expression payload, and ignores unknown ones.
Tests the pure resolver helper directly; the NATS publish side is exercised
via the manual end-to-end walkthrough."""

import pytest
from lafufu_control.animation.seed import seed_animations
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.service import resolve_emotion_to_play_intent


def test_known_emotion_resolves_to_play_intent(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    payload = resolve_emotion_to_play_intent(engine, emotion="disagree")
    assert payload is not None
    assert payload["name"] == "disagree"
    assert "playback" in payload


def test_unknown_emotion_returns_none(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    payload = resolve_emotion_to_play_intent(engine, emotion="zzz_unknown")
    assert payload is None


def test_empty_emotion_returns_none(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    assert resolve_emotion_to_play_intent(engine, emotion="") is None
    assert resolve_emotion_to_play_intent(engine, emotion=None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_agent_reply_to_pose.py -v`
Expected: ImportError on `resolve_emotion_to_play_intent`.

- [ ] **Step 3: Extract a helper and expand `on_reply`**

In `packages/control/src/lafufu_control/service.py`, ADD a module-level helper near the existing `_publish_idle_expression`:

```python
def resolve_emotion_to_play_intent(engine, emotion: str | None) -> dict | None:
    """Return the AnimatorIntentPlayExpression payload (as a dict) for the
    expression named `emotion`, or None if the name is empty/unknown/broken.
    Pure: caller is responsible for publishing the result on NATS."""
    name = (emotion or "").strip()
    if not name:
        return None
    with Session(engine) as s:
        e = s.get(Expression, name)
        if e is None:
            return None
        need = list(required_frame_names(e))
        frames = {f.name: f for f in s.exec(select(Frame).where(Frame.name.in_(need))).all()}
        if any(n not in frames for n in need):
            return None
        return compile_expression(e, frames).model_dump()
```

In `ControlService.on_startup`, locate the existing `on_reply` handler (around line 186-204). After the `_persist_chat` call, append the emotion → play_intent branch:

```python
        payload = resolve_emotion_to_play_intent(engine, msg.emotion)
        if payload is None:
            if (msg.emotion or "").strip():
                self.log.warning(
                    "agent.reply emotion=%r not found in expression registry; skipping pose",
                    msg.emotion,
                )
            return
        data = json.dumps(payload).encode("utf-8")
        await self.nats.publish(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, data)
```

If `self.log` doesn't exist on `BaseService`, swap for `logging.getLogger(__name__).warning(...)` (import `logging` at the top of the file if needed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package lafufu-control pytest packages/control/tests/test_agent_reply_to_pose.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Run the full control suite**

Run: `uv run --package lafufu-control pytest packages/control/tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/control/src/lafufu_control/service.py packages/control/tests/test_agent_reply_to_pose.py
git commit -m "feat(control): resolve agent.reply emotion → play_expression intent"
```

---

### Task 9: Remove the animator's `_on_agent_reply` no-op handler

**Files:**
- Modify: `packages/animator/src/lafufu_animator/service.py`

- [ ] **Step 1: Locate the subscription and the handler**

Open `packages/animator/src/lafufu_animator/service.py`. Two changes:

1. **Around line 160-170** — the subscription registration wires `_on_agent_reply` to the `agent.reply` topic. Find it (near the other `nats_helper.subscribe_model` calls) and delete the registration.
2. **Around line 407-413** — the handler itself. Delete the entire `_on_agent_reply` method.

- [ ] **Step 2: Check whether `_last_intent_mono` is still bumped elsewhere**

Before deleting the handler, the `_last_intent_mono = time.monotonic()` line inside `_on_agent_reply` may be load-bearing for the keyframe player loop's intent-quiet deferral. Check by running:

Run: Search for `_last_intent_mono` in `packages/animator/`.

If `_last_intent_mono` is written by other handlers (`_on_preview`, `_on_set_pose`, `_on_play_expression`, etc.), removal is safe — control's new branch publishes `animator.intent.play_expression`, and `_on_play_expression` will bump `_last_intent_mono` itself.

If `_on_agent_reply` is the ONLY writer, preserve the bump by replacing the body with a single line instead of deleting the method:

```python
    async def _on_agent_reply(self, subject, msg) -> None:
        self._last_intent_mono = time.monotonic()
```

…and keep the subscription. Note this in the commit message.

- [ ] **Step 3: Run animator tests**

Run: `uv run --package lafufu-animator pytest packages/animator/tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/animator/src/lafufu_animator/service.py
git commit -m "refactor(animator): remove agent.reply no-op handler (control resolves now)"
```

---

### Task 10: Clean up `emotion_parser` — drop the `neutral` fallback

**Files:**
- Modify: `packages/agent/src/lafufu_agent/emotion_parser.py`
- Test: `packages/agent/tests/test_emotion_parser.py`

- [ ] **Step 1: Update or add the test**

If `packages/agent/tests/test_emotion_parser.py` exists, update its cases. If not, create it:

```python
from lafufu_agent.emotion_parser import parse


def test_known_emotion_extracted():
    emotion, body = parse("[happy] hello world")
    assert emotion == "happy"
    assert body == "hello world"


def test_unknown_emotion_passes_through_verbatim():
    """The DB lookup downstream is the validity check. Parser should NOT
    default to 'neutral' anymore — that hid typos and silently masked
    missing expression registrations."""
    emotion, body = parse("[zzz_unknown] some text")
    assert emotion == "zzz_unknown"
    assert body == "some text"


def test_no_tag_returns_empty_emotion():
    emotion, body = parse("just text with no tag")
    assert emotion == ""
    assert body == "just text with no tag"


def test_alternate_delimiters_still_extracted():
    assert parse("(disagree) nope")[0] == "disagree"
    assert parse("*sad* aww")[0] == "sad"


def test_emotion_label_prefix():
    assert parse("emotion: angry rage")[0] == "angry"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --package lafufu-agent pytest packages/agent/tests/test_emotion_parser.py -v`
Expected: `test_unknown_emotion_passes_through_verbatim` and `test_no_tag_returns_empty_emotion` FAIL — current behavior returns `"neutral"`.

- [ ] **Step 3: Rewrite the parser**

Replace `packages/agent/src/lafufu_agent/emotion_parser.py`:

```python
"""Parse LLM replies of the form `[emotion] body text`.

Validity of the emotion name is checked downstream against the expression
registry — this parser just extracts whatever name the model emitted.
"""

import re

# Small models are inconsistent about the emotion tag: they drop the brackets,
# swap them for parens or markdown asterisks, or prefix an "emotion:" label.
# Any form we fail to strip here gets read aloud by TTS, so match them all.
_DELIMITED_RE = re.compile(
    r"^\s*[\[(*]+\s*(?:emotion\s*[:=]\s*)?([a-zA-Z_]+)\s*[\])*]+\s*\n?",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"^\s*emotion\s*[:=]\s*([a-zA-Z_]+)\s*\n*",
    re.IGNORECASE,
)


def parse(reply: str) -> tuple[str, str]:
    """Return (emotion, body). When no tag is present, emotion is ''.

    Tolerates `[happy]`, `(happy)`, `*happy*`, `emotion: happy`. Does NOT
    strip a bare leading word — too risky to swallow the first word of an
    ordinary sentence without a delimiter.
    """
    m = _DELIMITED_RE.match(reply)
    if m:
        return m.group(1).lower(), reply[m.end():].strip()
    m = _LABEL_RE.match(reply)
    if m:
        return m.group(1).lower(), reply[m.end():].strip()
    return "", reply.strip()
```

(Note: the `_BARE_RE` path is also removed. It relied on `_VALID_EMOTIONS` to decide whether to strip — without that gate, the bare path would eat ordinary sentence-starting words like "Hello" or "Today". Better to require an explicit delimiter.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package lafufu-agent pytest packages/agent/tests/test_emotion_parser.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Run the agent suite**

Run: `uv run --package lafufu-agent pytest packages/agent/tests/ -v`
Expected: PASS. If any pipeline/integration tests assumed unknown → "neutral", update them: they should now assert unknown → `""` and verify downstream behavior accordingly.

- [ ] **Step 6: Commit**

```bash
git add packages/agent/src/lafufu_agent/emotion_parser.py packages/agent/tests/test_emotion_parser.py
git commit -m "refactor(agent): emotion_parser no longer defaults to neutral (DB is the validity check)"
```

---

### Task 11: Add API client methods for the new endpoints

**Files:**
- Modify: `web/src/shared/api.ts`
- Possibly modify: `web/src/shared/types.gen.ts` or wherever DTOs live

- [ ] **Step 1: Add the new methods**

Open `web/src/shared/api.ts`. Inside the `api` object literal (around lines 222-253), add:

```ts
  // Reset built-ins
  resetExpression: (name: string) =>
    req<ExpressionDTO>(
      "POST",
      `/animator/expressions/${encodeURIComponent(name)}/reset`,
    ),
  resetFrame: (name: string) =>
    req<FrameDTO>(
      "POST",
      `/animator/frames/${encodeURIComponent(name)}/reset`,
    ),

  // Servo config
  getAnimatorConfig: () =>
    req<{
      ranges: Record<string, [number, number]>;
      idle_defaults: Record<string, number>;
      idle_overrides: Record<string, number>;
    }>("GET", "/animator/config"),
```

- [ ] **Step 2: Add `is_builtin` to the DTO types**

Search for the `ExpressionDTO` and `FrameDTO` type definitions. They may be inline in `api.ts` or in a separate types file (`types.gen.ts` is generated; if so, look for the source).

Add `is_builtin: boolean;` to each. If the field lives in `types.gen.ts` and is regenerated from a Python schema, the cleanest path is to add it by hand to the TS DTO (the field is a SQLModel column, not a `lafufu_shared` schema, so the codegen probably doesn't cover it).

- [ ] **Step 3: Typecheck**

```
cd web
npm run typecheck
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/shared/api.ts
git commit -m "feat(web): api client methods for /reset and /config; is_builtin on DTOs"
```

(Adjust the `git add` to whichever files actually changed — include `types.gen.ts` if you edited it.)

---

### Task 12: `createReactiveResource` helper

**Files:**
- Create: `web/src/shared/reactive_resource.ts`
- Test: `web/tests/reactive_resource.test.ts`

- [ ] **Step 1: Identify the NATS singleton**

Before writing the helper, find where the `NatsWs` instance lives. Read `web/src/main.tsx` and `web/src/shared/`. The helper needs to call `nats.subscribe(topic, handler)` against the same singleton the rest of the app uses.

If no singleton exists yet, expose one — e.g., create `web/src/shared/nats.ts`:

```ts
import { NatsWs } from "./nats_ws";
export const nats = new NatsWs();
nats.start();
```

…and import the singleton into `main.tsx` so it boots once. (If a singleton already exists, use its import path in the helper below.)

- [ ] **Step 2: Write the failing test**

Create `web/tests/reactive_resource.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { createRoot } from "solid-js";
import { createReactiveResource } from "../src/shared/reactive_resource";

const subs = new Map<string, Set<() => void>>();

vi.mock("../src/shared/nats", () => ({
  nats: {
    subscribe: (topic: string, handler: () => void) => {
      const set = subs.get(topic) ?? new Set();
      set.add(handler);
      subs.set(topic, set);
      return () => set.delete(handler);
    },
  },
}));

function emit(topic: string) {
  for (const h of subs.get(topic) ?? []) h();
}

describe("createReactiveResource", () => {
  it("fetches once on mount and refetches when a subscribed topic fires", async () => {
    let calls = 0;
    const fetchFn = vi.fn(async () => {
      calls += 1;
      return { value: calls };
    });
    await createRoot(async (dispose) => {
      const data = createReactiveResource(fetchFn, ["foo.changed"]);
      await new Promise((r) => setTimeout(r, 0));
      expect(data()).toEqual({ value: 1 });
      emit("foo.changed");
      await new Promise((r) => setTimeout(r, 0));
      expect(data()).toEqual({ value: 2 });
      dispose();
    });
  });

  it("unsubscribes on cleanup", async () => {
    const fetchFn = vi.fn(async () => 0);
    await createRoot(async (dispose) => {
      createReactiveResource(fetchFn, ["bar.changed"]);
      expect(subs.get("bar.changed")?.size).toBe(1);
      dispose();
    });
    expect(subs.get("bar.changed")?.size ?? 0).toBe(0);
  });
});
```

Adjust the `vi.mock` path to match where the singleton actually lives.

- [ ] **Step 3: Run the test to verify it fails**

From `web/`: `npm test -- reactive_resource`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the helper**

Create `web/src/shared/reactive_resource.ts`:

```ts
import { createResource, onCleanup, onMount } from "solid-js";
import { nats } from "./nats";

/**
 * Solid resource that refetches when any of the listed NATS topics fires.
 *
 * Use for list-views that should reactively follow backend state changes
 * (e.g. expressions, frames, servo config). Payloads are ignored — listeners
 * just refetch the full list.
 */
export function createReactiveResource<T>(
  fetchFn: () => Promise<T>,
  topics: string[],
) {
  const [data, { refetch }] = createResource(fetchFn);
  onMount(() => {
    const unsubs = topics.map((t) =>
      nats.subscribe(t, () => {
        void refetch();
      }),
    );
    onCleanup(() => unsubs.forEach((u) => u()));
  });
  return data;
}
```

Adjust the `import` path to the actual singleton location.

- [ ] **Step 5: Run the test to verify it passes**

From `web/`: `npm test -- reactive_resource`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/shared/reactive_resource.ts web/tests/reactive_resource.test.ts
git commit -m "feat(web): createReactiveResource helper — Solid resource + NATS refetch"
```

(If you also added `web/src/shared/nats.ts`, include it in the add.)

---

### Task 13: Adopt `createReactiveResource` for expressions and frames lists

**Files:**
- Modify: `web/src/admin/expressions_section.tsx`

- [ ] **Step 1: Replace the two `createResource` calls**

Open `web/src/admin/expressions_section.tsx`. Update imports:

```ts
import {
  Component,
  createMemo,
  createSignal,
  For,
  Show,
} from "solid-js";
// (keep the solid-dnd imports as-is)
import {
  api,
  type ExpressionDTO,
  type ExpressionStepDTO,
  type FrameDTO,
} from "../shared/api";
import { toast } from "../shared/toast";
import { createReactiveResource } from "../shared/reactive_resource";
```

Replace the existing resource bindings:

```ts
  const expressions = createReactiveResource(
    async () => (await api.listExpressions()).items,
    ["expressions.changed"],
  );
  const frames = createReactiveResource(
    async () => (await api.listFrames()).items,
    ["frames.changed"],
  );
```

The `refetchExpr` callback (previously returned from `createResource`) is no longer needed — the backend's `expressions.changed` publish triggers refetch automatically. **Remove every `refetchExpr` reference and every `await refetchExpr()` line** from `onNew`, `onSave`, `onDelete`.

`localEdits` and `effective` stay as-is — they're the local override layer for in-progress edits and aren't covered by the NATS refetch.

- [ ] **Step 2: Typecheck and manual smoke-test**

```
cd web
npm run typecheck
npm run dev
```

In the browser, open the admin page in two tabs. In tab A, create an expression. Tab B should show it within ~100ms without any refresh. Same for frame creation.

(Human-verified — no automated harness for this round-trip.)

- [ ] **Step 3: Commit**

```bash
git add web/src/admin/expressions_section.tsx
git commit -m "feat(web): expressions and frames lists auto-refetch on backend changes"
```

---

### Task 14: `useServoConfig` resource + delete `servo_ranges.ts`

**Files:**
- Create: `web/src/shared/use_servo_config.ts`
- Modify: `web/src/pet/head_drag.ts`
- Modify: `web/src/pet/pet.tsx`
- Modify: `web/tests/head_drag.test.ts`
- Delete: `web/src/pet/servo_ranges.ts`

- [ ] **Step 1: Read the current `head_drag.ts` and its test**

The new function shape: pure functions that take `ranges` as an explicit parameter (no module-level import of `SERVO_RANGES`). Read `web/src/pet/head_drag.ts` and `web/tests/head_drag.test.ts` first to learn the actual function names and signatures — substitute them below if they differ from the placeholders.

- [ ] **Step 2: Update the head_drag test to require an explicit `ranges` argument**

Modify `web/tests/head_drag.test.ts`. Pass an explicit `ranges` object to the function under test:

```ts
import { describe, expect, it } from "vitest";
import { dxlFromDragDelta } from "../src/pet/head_drag";  // substitute the actual export name

const RANGES = {
  head_lr: [1828, 2298] as [number, number],
  head_ud: [2885, 3278] as [number, number],
  eye:     [1995, 2085] as [number, number],
  jaw:     [1594, 1811] as [number, number],
  brow:    [2056, 2087] as [number, number],
};

describe("dxlFromDragDelta", () => {
  it("clamps to head_lr range", () => {
    const v = dxlFromDragDelta("head_lr", 9999, RANGES);
    expect(v).toBe(RANGES.head_lr[1]);
  });
  // …port the existing assertions, passing RANGES through every call
});
```

- [ ] **Step 3: Run the test to verify it fails**

```
cd web
npm test -- head_drag
```

Expected: FAIL — function doesn't accept a `ranges` param yet.

- [ ] **Step 4: Make `head_drag.ts` accept `ranges` as a parameter**

Open `web/src/pet/head_drag.ts`. For every function that currently reads `SERVO_RANGES[…]`, add a `ranges: ServoRanges` parameter and use `ranges[…]` instead. Remove `import { SERVO_RANGES } from "./servo_ranges"`.

Pattern:

```ts
export type ServoRanges = Record<
  "head_lr" | "head_ud" | "eye" | "jaw" | "brow",
  [number, number]
>;

export function dxlFromDragDelta(
  servo: keyof ServoRanges,
  deltaPx: number,
  ranges: ServoRanges,
): number {
  const [lo, hi] = ranges[servo];
  // existing math, with lo/hi replacing SERVO_RANGES[servo][0]/[1]
  // …
}
```

- [ ] **Step 5: Run the test to verify it passes**

```
cd web
npm test -- head_drag
```

Expected: PASS.

- [ ] **Step 6: Create the `useServoConfig` resource**

Create `web/src/shared/use_servo_config.ts`:

```ts
import { api } from "./api";
import { createReactiveResource } from "./reactive_resource";

/**
 * Servo config (ranges + idle defaults + operator overrides) from the
 * control plane. Refetches when any animator.<servo>.default setting changes.
 */
export function useServoConfig() {
  return createReactiveResource(api.getAnimatorConfig, [
    "config.changed.animator.head_lr.default",
    "config.changed.animator.head_ud.default",
    "config.changed.animator.eye.default",
    "config.changed.animator.jaw.default",
    "config.changed.animator.brow.default",
  ]);
}
```

- [ ] **Step 7: Update `pet.tsx` to use the resource**

Open `web/src/pet/pet.tsx`. Replace any `import { SERVO_RANGES } from "./servo_ranges"` with `import { useServoConfig } from "../shared/use_servo_config"`. Bind the resource once inside the component:

```ts
const config = useServoConfig();
```

At every call site that used `SERVO_RANGES`, read `config()?.ranges` and gate on availability. Pattern:

```ts
onDragMove((delta) => {
  const ranges = config()?.ranges;
  if (!ranges) return;  // first paint, config still loading
  const dxl = dxlFromDragDelta("head_lr", delta, ranges as ServoRanges);
  // …
});
```

If the existing code unconditionally relies on `SERVO_RANGES` at module level (e.g., for an initial CSS transform), restructure so the dependent code path only runs after `config()` resolves.

- [ ] **Step 8: Delete `servo_ranges.ts`**

```bash
git rm web/src/pet/servo_ranges.ts
```

Verify nothing else references it:

Run: Search for `servo_ranges` in `web/src`.
Expected: zero hits.

- [ ] **Step 9: Typecheck and smoke-test**

```
cd web
npm run typecheck
npm test
npm run dev
```

Open the pet page. Drag the head left, right, up, down. Confirm the drag clamps at the same boundaries it did before. Confirm jaw and eye drag still work. If the page hangs on initial load (config not arriving), check that the NATS singleton is wired and that `api.getAnimatorConfig` reaches the endpoint.

- [ ] **Step 10: Commit**

```bash
git add web/src/shared/use_servo_config.ts web/src/pet/head_drag.ts web/src/pet/pet.tsx web/tests/head_drag.test.ts
git rm web/src/pet/servo_ranges.ts
git commit -m "feat(web): useServoConfig resource; head_drag takes ranges as param; delete servo_ranges.ts"
```

---

### Task 15: Reset button on built-ins; hide Delete on built-ins; (builtin) tag in list

**Files:**
- Modify: `web/src/admin/expressions_section.tsx`

- [ ] **Step 1: Wire the Reset action**

In `expressions_section.tsx`, add an `onReset` handler alongside `onPlay`, `onSave`, `onDelete`:

```ts
  const onReset = async () => {
    const e = selectedEff();
    if (!e) return;
    if (!window.confirm(`Reset "${e.name}" to factory defaults? Your edits will be lost.`)) return;
    try {
      await api.resetExpression(e.name);
      // expressions.changed publish will refetch automatically; clear local edits
      setLocalEdits(null);
      toast.ok(`reset ${e.name}`);
    } catch (err: unknown) {
      toast.err("reset failed", (err as Error)?.message ?? String(err));
    }
  };
```

- [ ] **Step 2: Show Reset on built-ins, hide Delete on built-ins**

Find the action-button row containing `▶ Play` / `Save` / `Delete`. Replace the `Delete` button with two `Show`-conditional buttons:

```tsx
                  <Show when={e().is_builtin}>
                    <button
                      type="button"
                      onClick={onReset}
                      class="px-3 py-1 border border-blue-700 text-blue-300 rounded hover:bg-blue-900/30 ml-auto"
                    >
                      Reset to defaults
                    </button>
                  </Show>
                  <Show when={!e().is_builtin}>
                    <button
                      type="button"
                      onClick={onDelete}
                      class="px-3 py-1 border border-red-800 text-red-300 rounded hover:bg-red-900/30 ml-auto"
                    >
                      Delete
                    </button>
                  </Show>
```

- [ ] **Step 3: Surface the built-in flag in the list row**

In the left list, add a small `(builtin)` tag next to the name:

```tsx
                <div class="font-mono">
                  {e.name}
                  <Show when={e.is_builtin}>
                    <span class="ml-2 text-xs text-stone-500">(builtin)</span>
                  </Show>
                </div>
```

- [ ] **Step 4: Typecheck and smoke-test**

```
cd web
npm run typecheck
npm run dev
```

In the admin:
- Select `happy` (built-in). Confirm "Reset to defaults" appears instead of "Delete".
- Edit the duration to 9999, hit Save, then hit Reset to defaults. Confirm the duration returns to 800.
- Create a user expression. Confirm it gets a "Delete" button, not "Reset".

- [ ] **Step 5: Commit**

```bash
git add web/src/admin/expressions_section.tsx
git commit -m "feat(web): Reset-to-defaults button on built-ins; hide Delete; (builtin) tag in list"
```

---

### Task 16: Drop the hardcoded `EMOTIONS` array

**Files:**
- Modify: `web/src/admin/expressions_section.tsx`

- [ ] **Step 1: Delete the constant**

Remove the `EMOTIONS` array (around line 54-63) entirely.

- [ ] **Step 2: Drive the emotion dropdown from the registry**

In the editor JSX, find the Emotion select. Replace the `<For each={EMOTIONS}>…</For>` with a `For` over the live expression names — the canonical agent-callable list:

```tsx
                    <select
                      value={e().emotion ?? ""}
                      onChange={(ev) => {
                        const v = ev.currentTarget.value;
                        mutateSelected({ emotion: v === "" ? null : v });
                      }}
                      class="bg-stone-800 border border-stone-600 rounded px-1 py-0.5"
                    >
                      <option value="">(none)</option>
                      <For each={expressions() ?? []}>
                        {(x) => <option value={x.name}>{x.name}</option>}
                      </For>
                    </select>
```

(Note: the `emotion` field is deprecated by this design, but the UI still allows binding for back-compat in this build. The dropdown contents now reflect reality — the actual set of registered expression names, including any user-created ones.)

- [ ] **Step 3: Typecheck and smoke-test**

```
cd web
npm run typecheck
npm run dev
```

Confirm the Emotion dropdown shows every registered expression and no longer has a phantom `idle` option that doesn't map to anything.

- [ ] **Step 4: Commit**

```bash
git add web/src/admin/expressions_section.tsx
git commit -m "refactor(web): drop hardcoded EMOTIONS array; drive dropdown from live registry"
```

---

### Task 17: Delete the dead `expressions.py` module and its test

**Files:**
- Delete: `packages/animator/src/lafufu_animator/expressions.py`
- Delete: `packages/animator/tests/test_expressions.py`

- [ ] **Step 1: Verify zero live consumers**

Search for these import patterns inside `packages/` (excluding the file and its test):

- `from lafufu_animator import expressions`
- `from .expressions import`
- references to `expressions.get`, `expressions.compute_target`, `expressions.get_offsets`, `expressions.apply_offsets`, `expressions.list_names`, `expressions.is_expired`

Expected: every hit is inside `packages/animator/tests/test_expressions.py` or the module itself.

If any live consumer exists outside the test, STOP and report — the module isn't dead and the design is wrong. Otherwise proceed:

- [ ] **Step 2: Delete the files**

```bash
git rm packages/animator/src/lafufu_animator/expressions.py
git rm packages/animator/tests/test_expressions.py
```

- [ ] **Step 3: Run the animator suite**

Run: `uv run --package lafufu-animator pytest packages/animator/tests/ -v`
Expected: all PASS (one fewer test file).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(animator): delete dead expressions.py module (offset+motion path retired)"
```

---

### Task 18: Code-simplifier pass on touched files

**Files (review-only):**
- `packages/control/src/lafufu_control/api/routers/animator.py`
- `packages/control/src/lafufu_control/service.py`
- `packages/control/src/lafufu_control/animation/seed.py`
- `packages/agent/src/lafufu_agent/emotion_parser.py`
- `packages/animator/src/lafufu_animator/service.py`
- `web/src/admin/expressions_section.tsx`
- `web/src/pet/head_drag.ts`
- `web/src/pet/pet.tsx`
- `web/src/shared/reactive_resource.ts`
- `web/src/shared/use_servo_config.ts`

- [ ] **Step 1: Dispatch the code-simplifier skill on the modified files**

Use the `code-simplifier:code-simplifier` skill via the Agent tool. Prompt template:

> Review and simplify the recently-modified files in this branch:
>
> [list the files above]
>
> Focus: remove now-dead code paths exposed by the unification (any references to the deleted `expressions.py`, leftover compatibility shims, vestigial constants); collapse duplicated state where a single source now exists; tighten function shapes that became simpler after the agent→pose link landed.
>
> Do NOT change behavior. Run the test suites after each file you edit (`uv run --package lafufu-control pytest`, `uv run --package lafufu-animator pytest`, `uv run --package lafufu-agent pytest`, `cd web && npm test`) and stop if anything goes red.

- [ ] **Step 2: Review the proposed diff before accepting**

Review each proposed change. Reject any change that:
- Removes a comment that explains non-obvious behavior.
- Refactors beyond the touched files (out of scope).
- Combines steps that should remain separate for readability.

- [ ] **Step 3: Run full test suites after the simplifier pass**

```
uv run --package lafufu-control pytest packages/control/tests/ -v
uv run --package lafufu-animator pytest packages/animator/tests/ -v
uv run --package lafufu-agent pytest packages/agent/tests/ -v
cd web && npm test && npm run typecheck
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: code-simplifier pass on files touched by the registry unification"
```

(Adjust `-A` to a specific file list if the simplifier touched anything unrelated.)

---

### Task 19: End-to-end manual verification

This task isn't TDD — it's a human-driven walkthrough confirming the three user pain points are resolved.

- [ ] **Step 1: Boot the stack**

In one terminal: start `lafufu-control` (via the project's usual command, likely `uv run --package lafufu-control python -m lafufu_control.cli` — confirm by reading the package's `pyproject.toml`). In another: start the animator. In a third: start the agent if it's needed for the agent→pose check.

If a unified launcher exists (`scripts/dev.sh`, a make target, etc.), use that.

- [ ] **Step 2: Verify the agent → pose link**

Send the agent a prompt that elicits a disagreement. Expected: Lafufu shakes its head as the response lands.

If a fake-LLM-reply harness exists for testing, use it to inject `[disagree] no way` and `[happy] yes!`; confirm head motion in both cases.

Inject `[asdf] gibberish`. Expected: no pose change, a warning log line like `agent.reply emotion='asdf' not found in expression registry`.

- [ ] **Step 3: Verify save → built-in override**

In the admin: edit the `happy` expression (change duration to 1500). Save. Hit ▶ Play and confirm the new duration is what plays. Hit Reset to defaults. Confirm Play reverts to the factory 800ms.

- [ ] **Step 4: Verify live frame reactivity**

Open the admin in two browser tabs. In tab A, create a new frame via the snapshot path. Switch to tab B WITHOUT refreshing. The new frame should appear in the expression-creation frame picker within ~100ms.

- [ ] **Step 5: Verify the servo-range refactor didn't break drag**

Open the /pet page. Drag the head left, right, up, down. Confirm the head clamps at the same boundaries as before. Confirm jaw and eye drag still work.

- [ ] **Step 6: Push for review**

```bash
git push -u origin <branch>
gh pr create --title "feat: unified expression registry + live state sync" --body "$(cat <<'EOF'
## Summary

- Closes the agent → animator gap: the control plane now resolves the
  agent's [emotion] tag against the expression registry and publishes
  play_expression. Labubu reacts to its own words.
- Adds is_builtin flag + reset endpoint + delete/rename guards. Built-ins
  can be edited but not deleted; "Reset to defaults" button restores from
  seed.
- Removes the duplicated servo_ranges.ts; the frontend now fetches
  ranges/idle defaults from GET /api/animator/config and live-updates
  through NATS.
- Adds createReactiveResource — every list view (expressions, frames,
  servo config) auto-refetches on backend changes. No more refresh-required
  staleness in the admin.
- Deletes the dead expressions.py module and its test.

Spec: docs/superpowers/specs/2026-05-25-unified-expression-registry-design.md
Plan: docs/superpowers/plans/2026-05-26-unified-expression-registry.md

## Test plan

- [ ] All test suites pass (control, animator, agent, web)
- [ ] Manual: agent → disagree triggers head shake
- [ ] Manual: agent → unknown emotion no-ops and logs warning
- [ ] Manual: save+reset works on a built-in
- [ ] Manual: new frame in tab A appears in tab B without refresh
- [ ] Manual: head drag clamps correctly on /pet
EOF
)"
```

---

## Self-review

**Spec coverage:** Every spec section is covered.
- Data model / `is_builtin` + seed → Tasks 1, 2.
- Reset endpoints → Task 3.
- Delete/rename guards → Task 4.
- NATS change events → Task 5.
- Servo config endpoint → Task 6.
- Idle bootstrap by name (not emotion) → Task 7.
- Agent → pose resolution → Tasks 8 (control), 9 (animator cleanup).
- emotion_parser cleanup → Task 10.
- API client methods + DTO updates → Task 11.
- `createReactiveResource` helper + tests → Task 12.
- Adopt for expressions/frames lists → Task 13.
- `useServoConfig` + delete `servo_ranges.ts` + head_drag refactor → Task 14.
- Reset UI + hide Delete + `(builtin)` tag → Task 15.
- Drop EMOTIONS array → Task 16.
- Delete dead `expressions.py` → Task 17.
- Code-simplifier pass → Task 18.
- E2E manual verification → Task 19.

**Type/name consistency:** `is_builtin` (snake_case backend, also passed through to TS) used throughout. `createReactiveResource` named consistently. `useServoConfig` returns a resource directly. Python helpers `apply_frame_seed` / `apply_expression_seed` / `resolve_emotion_to_play_intent` defined where claimed.

**Placeholder scan:** No "TBD"/"TODO"/"fill in" markers. The two places that say "adapt to actual code" (Tasks 9 and 14) explicitly instruct the engineer to read the surrounding code first; they're not asking the engineer to invent the implementation, just to substitute local names after reading.
