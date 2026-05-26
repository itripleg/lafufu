"""Admin → animator intent proxy."""

import contextlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from lafufu_animator import pose as _pose
from pydantic import BaseModel
from sqlmodel import Session, select

from ...animation.compile import compile_expression, required_frame_names
from ...animation.seed import apply_expression_seed, apply_frame_seed
from ...models import Expression, Frame
from ...models.setting import Setting

router = APIRouter()


class PreviewBody(BaseModel):
    name: Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
    position: int


class LegacyExpressionBody(BaseModel):
    name: str
    intensity: float = 1.0


class GestureBody(BaseModel):
    name: Literal["nod_yes", "nod_no", "look_around"]


class FrameBody(BaseModel):
    name: str | None = None  # name from URL on PUT; required in body on POST
    head_lr: int
    head_ud: int
    eye: int
    jaw: int
    brow: int
    image: str | None = None
    description: str | None = None


@router.post("/preview", status_code=202)
def preview(body: PreviewBody, req: Request):
    req.app.state.nats_publish("animator.intent.preview", body.model_dump())
    return {"ok": True}


class SetPoseBody(BaseModel):
    """Atomic full-pose set — replaces a fan-out of 5 preview calls. Each
    /preview only updates one servo from the bus's current pose, so 5 parallel
    previews race: only the last servo to land sticks. Use this when the
    caller already has a complete pose (e.g. clicking a frame in the gallery)."""

    head_lr: int
    head_ud: int
    eye: int
    jaw: int
    brow: int


@router.post("/set_pose", status_code=202)
def set_pose(body: SetPoseBody, req: Request):
    req.app.state.nats_publish(
        "animator.intent.set_pose",
        {"pose": body.model_dump()},
    )
    return {"ok": True}


@router.post("/expression", status_code=202)
def expression(body: LegacyExpressionBody, req: Request):
    """Back-compat: legacy callers (the /pet page) POST {name, intensity}.
    Resolve the named expression through the DB + compiler and publish the
    new AnimatorIntentPlayExpression shape so the animator can consume it."""
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, body.name)
        if e is None:
            raise HTTPException(
                404,
                detail={"error_code": "not_found", "message": f"no expression {body.name!r}"},
            )
        need = list(required_frame_names(e))
        frames = {f.name: f for f in s.exec(select(Frame).where(Frame.name.in_(need))).all()}
        missing = [n for n in need if n not in frames]
        if missing:
            raise HTTPException(
                409,
                detail={
                    "error_code": "missing_frames",
                    "message": f"expression references unknown frames: {missing}",
                },
            )
        payload = compile_expression(e, frames)
    req.app.state.nats_publish("animator.intent.play_expression", payload.model_dump())
    return {"ok": True}


@router.post("/gesture", status_code=202)
def gesture(body: GestureBody, req: Request):
    req.app.state.nats_publish("animator.intent.gesture", body.model_dump())
    return {"ok": True}


def _f2d(f: Frame) -> dict:
    return {
        "name": f.name,
        "head_lr": f.head_lr,
        "head_ud": f.head_ud,
        "eye": f.eye,
        "jaw": f.jaw,
        "brow": f.brow,
        "image": f.image,
        "description": f.description,
        "is_builtin": f.is_builtin,
    }


@router.get("/frames")
def list_frames(req: Request):
    with Session(req.app.state.engine) as s:
        rows = s.exec(select(Frame).order_by(Frame.name)).all()
        return {"items": [_f2d(f) for f in rows]}


@router.post("/frames")
def create_frame(body: FrameBody, req: Request):
    if not body.name:
        raise HTTPException(
            400, detail={"error_code": "missing_name", "message": "frame name is required"}
        )
    with Session(req.app.state.engine) as s:
        if s.get(Frame, body.name) is not None:
            raise HTTPException(
                409, detail={"error_code": "exists", "message": f"frame {body.name!r} exists"}
            )
        f = Frame(
            name=body.name,
            head_lr=body.head_lr,
            head_ud=body.head_ud,
            eye=body.eye,
            jaw=body.jaw,
            brow=body.brow,
            image=body.image,
            description=body.description,
        )
        s.add(f)
        s.commit()
        s.refresh(f)
        out = _f2d(f)
    req.app.state.nats_publish("frames.changed", {"kind": "create", "name": body.name})
    return out


@router.put("/frames/{name}")
def update_frame(name: str, body: FrameBody, req: Request):
    with Session(req.app.state.engine) as s:
        f = s.get(Frame, name)
        if f is None:
            raise HTTPException(
                404, detail={"error_code": "not_found", "message": f"no frame {name!r}"}
            )
        f.head_lr = body.head_lr
        f.head_ud = body.head_ud
        f.eye = body.eye
        f.jaw = body.jaw
        f.brow = body.brow
        f.image = body.image
        f.description = body.description
        s.add(f)
        s.commit()
        s.refresh(f)
        out = _f2d(f)
    req.app.state.nats_publish("frames.changed", {"kind": "update", "name": name})
    return out


@router.delete("/frames/{name}", status_code=204)
def delete_frame(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        f = s.get(Frame, name)
        if f is None:
            return None  # idempotent
        if f.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "is_builtin",
                    "message": f"frame {name!r} is a built-in and cannot be deleted; reset it instead",
                },
            )
        # Refuse if any expression references this frame — otherwise /play would
        # 409 on the orphan reference later. Scan via LIKE on the JSON column;
        # cheap given the table size.
        like = f'%"frame": "{name}"%'
        referencing = s.exec(select(Expression).where(Expression.steps_json.like(like))).all()
        # LIKE may match false positives if a frame name is a substring of another
        # name — confirm by parsing steps_json.
        actual = [
            e.name
            for e in referencing
            if name in (s["frame"] for s in json.loads(e.steps_json or "[]"))
        ]
        if actual:
            raise HTTPException(
                409,
                detail={
                    "error_code": "frame_in_use",
                    "message": f"frame {name!r} is used by: {', '.join(actual)}",
                    "referenced_by": actual,
                },
            )
        s.delete(f)
        s.commit()
    req.app.state.nats_publish("frames.changed", {"kind": "delete", "name": name})
    return None


@router.post("/frames/{name}/snapshot")
def snapshot_frame(name: str, req: Request):
    pose = getattr(req.app.state, "last_pose", None)
    if not pose:
        raise HTTPException(
            409,
            detail={"error_code": "no_live_pose", "message": "no live pose available yet"},
        )
    with Session(req.app.state.engine) as s:
        f = s.get(Frame, name)
        if f is None:
            f = Frame(name=name, **pose)
            s.add(f)
        else:
            for k in ("head_lr", "head_ud", "eye", "jaw", "brow"):
                setattr(f, k, pose[k])
            s.add(f)
        s.commit()
    return {"ok": True, "name": name}


class ExpressionStep(BaseModel):
    frame: str
    duration_ms: int | None = None
    delay_ms: int | None = None
    easing: str | None = None


class RandomWalkConfigBody(BaseModel):
    intensity: float = 1.0
    speed: float = 1.0
    pause_chance: float = 0.30


class ExpressionBody(BaseModel):
    name: str | None = None  # required in body for POST; URL provides it for PUT
    playback: str = "once"
    default_duration_ms: int = 250
    default_delay_ms: int = 80
    default_easing: str = "ease-in-out"
    steps: list[ExpressionStep] = []
    random_walk_config: RandomWalkConfigBody | None = None
    emotion: str | None = None
    description: str | None = None


_DEFAULT_RW_CONFIG = {"intensity": 1.0, "speed": 1.0, "pause_chance": 0.30}


def _e2d(e: Expression) -> dict:
    # steps_json is polymorphic by playback: a list of step dicts for the
    # step-based modes, a config dict for random_walk. Expose them as
    # separate top-level fields so the frontend has a uniform shape.
    raw = json.loads(e.steps_json or ("{}" if e.playback == "random_walk" else "[]"))
    if e.playback == "random_walk":
        steps: list[dict] = []
        rwc = raw if isinstance(raw, dict) else dict(_DEFAULT_RW_CONFIG)
    else:
        steps = raw if isinstance(raw, list) else []
        rwc = None
    return {
        "name": e.name,
        "playback": e.playback,
        "default_duration_ms": e.default_duration_ms,
        "default_delay_ms": e.default_delay_ms,
        "default_easing": e.default_easing,
        "steps": steps,
        "random_walk_config": rwc,
        "emotion": e.emotion,
        "description": e.description,
        "is_builtin": e.is_builtin,
    }


def _expression_steps_json(body: ExpressionBody) -> str:
    """Serialize the playback-mode-specific payload into steps_json."""
    if body.playback == "random_walk":
        cfg = body.random_walk_config or RandomWalkConfigBody()
        return json.dumps(cfg.model_dump())
    return json.dumps([st.model_dump(exclude_none=True) for st in body.steps])


@router.get("/config")
def get_animator_config(req: Request):
    """Servo config for the frontend: ranges (CLAMP), factory idle positions,
    and any operator overrides from the settings table."""
    ranges = {k: list(v) for k, v in _pose.CLAMP.items()}
    idle_defaults = {
        "head_lr": _pose.HEAD_IDLE_LR_DXL,
        "head_ud": _pose.HEAD_IDLE_UD_DXL,
        "eye": _pose.EYE_IDLE_DXL,
        "jaw": _pose.MOUTH_CLOSE_DXL,
        "brow": _pose.BROW_IDLE_DXL,
    }
    overrides: dict[str, int] = {}
    with Session(req.app.state.engine) as s:
        for servo in ("head_lr", "head_ud", "eye", "jaw", "brow"):
            row = s.get(Setting, f"animator.{servo}.default")
            if row is not None:
                with contextlib.suppress(TypeError, ValueError):
                    overrides[servo] = int(row.value)
    return {"ranges": ranges, "idle_defaults": idle_defaults, "idle_overrides": overrides}


@router.get("/expressions")
def list_expressions(req: Request):
    with Session(req.app.state.engine) as s:
        rows = s.exec(select(Expression).order_by(Expression.name)).all()
        return {"items": [_e2d(e) for e in rows]}


@router.post("/expressions")
def create_expression(body: ExpressionBody, req: Request):
    if not body.name:
        raise HTTPException(
            400,
            detail={"error_code": "missing_name", "message": "expression name is required"},
        )
    with Session(req.app.state.engine) as s:
        if s.get(Expression, body.name) is not None:
            raise HTTPException(
                409,
                detail={"error_code": "exists", "message": f"expression {body.name!r} exists"},
            )
        e = Expression(
            name=body.name,
            playback=body.playback,
            default_duration_ms=body.default_duration_ms,
            default_delay_ms=body.default_delay_ms,
            default_easing=body.default_easing,
            steps_json=_expression_steps_json(body),
            emotion=body.emotion,
            description=body.description,
        )
        s.add(e)
        s.commit()
        s.refresh(e)
        out = _e2d(e)
    req.app.state.nats_publish("expressions.changed", {"kind": "create", "name": body.name})
    return out


@router.put("/expressions/{name}")
def update_expression(name: str, body: ExpressionBody, req: Request):
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is None:
            raise HTTPException(
                404,
                detail={"error_code": "not_found", "message": f"no expression {name!r}"},
            )
        e.playback = body.playback
        e.default_duration_ms = body.default_duration_ms
        e.default_delay_ms = body.default_delay_ms
        e.default_easing = body.default_easing
        e.steps_json = _expression_steps_json(body)
        e.emotion = body.emotion
        e.description = body.description
        s.add(e)
        s.commit()
        s.refresh(e)
        out = _e2d(e)
    req.app.state.nats_publish("expressions.changed", {"kind": "update", "name": name})
    return out


@router.delete("/expressions/{name}", status_code=204)
def delete_expression(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is None:
            return None  # idempotent
        if e.is_builtin:
            raise HTTPException(
                400,
                detail={
                    "error_code": "is_builtin",
                    "message": f"expression {name!r} is a built-in and cannot be deleted; reset it instead",
                },
            )
        # Refuse if this expression is bound to an emotion — the agent service
        # (and idle bootstrap) rely on emotion → expression resolution; deleting
        # a bound expression silently orphans those code paths. Operator must
        # unbind via PUT first, or activate a replacement.
        if e.emotion:
            raise HTTPException(
                409,
                detail={
                    "error_code": "expression_bound",
                    "message": (
                        f"expression {name!r} is bound to emotion {e.emotion!r}; "
                        "clear the emotion (PUT with emotion=null) or activate "
                        "another expression for this emotion first"
                    ),
                    "emotion": e.emotion,
                },
            )
        s.delete(e)
        s.commit()
    req.app.state.nats_publish("expressions.changed", {"kind": "delete", "name": name})
    return None


@router.post("/expressions/{name}/play", status_code=202)
def play_expression(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is None:
            raise HTTPException(
                404,
                detail={"error_code": "not_found", "message": f"no expression {name!r}"},
            )
        need = list(required_frame_names(e))
        frames = {f.name: f for f in s.exec(select(Frame).where(Frame.name.in_(need))).all()}
        missing = [n for n in need if n not in frames]
        if missing:
            raise HTTPException(
                409,
                detail={
                    "error_code": "missing_frames",
                    "message": f"expression references unknown frames: {missing}",
                },
            )
        payload = compile_expression(e, frames)
    req.app.state.nats_publish("animator.intent.play_expression", payload.model_dump())
    return {"ok": True, "name": name}


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
        out = _e2d(e)
    req.app.state.nats_publish("expressions.changed", {"kind": "reset", "name": name})
    return out


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
        out = _f2d(f)
    req.app.state.nats_publish("frames.changed", {"kind": "reset", "name": name})
    return out
