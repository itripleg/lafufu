"""Admin → animator intent proxy."""

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ...animation.compile import compile_expression, required_frame_names
from ...models import Expression, Frame

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
        return _f2d(f)


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
        return _f2d(f)


@router.delete("/frames/{name}", status_code=204)
def delete_frame(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        f = s.get(Frame, name)
        if f is not None:
            s.delete(f)
            s.commit()
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


class ExpressionBody(BaseModel):
    name: str | None = None  # required in body for POST; URL provides it for PUT
    playback: str = "once"
    default_duration_ms: int = 250
    default_delay_ms: int = 80
    default_easing: str = "ease-in-out"
    steps: list[ExpressionStep] = []
    emotion: str | None = None
    description: str | None = None


def _e2d(e: Expression) -> dict:
    return {
        "name": e.name,
        "playback": e.playback,
        "default_duration_ms": e.default_duration_ms,
        "default_delay_ms": e.default_delay_ms,
        "default_easing": e.default_easing,
        "steps": json.loads(e.steps_json or "[]"),
        "emotion": e.emotion,
        "description": e.description,
    }


def _steps_to_json(steps: list[ExpressionStep]) -> str:
    return json.dumps([st.model_dump(exclude_none=True) for st in steps])


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
            steps_json=_steps_to_json(body.steps),
            emotion=body.emotion,
            description=body.description,
        )
        s.add(e)
        s.commit()
        s.refresh(e)
        return _e2d(e)


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
        e.steps_json = _steps_to_json(body.steps)
        e.emotion = body.emotion
        e.description = body.description
        s.add(e)
        s.commit()
        s.refresh(e)
        return _e2d(e)


@router.delete("/expressions/{name}", status_code=204)
def delete_expression(name: str, req: Request):
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is not None:
            s.delete(e)
            s.commit()
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


@router.post("/expressions/{name}/activate")
def activate_expression(name: str, req: Request):
    """Make this expression the owner of its emotion binding, evicting any
    previous expression that held the same emotion."""
    with Session(req.app.state.engine) as s:
        e = s.get(Expression, name)
        if e is None:
            raise HTTPException(
                404,
                detail={"error_code": "not_found", "message": f"no expression {name!r}"},
            )
        if not e.emotion:
            raise HTTPException(
                400,
                detail={
                    "error_code": "no_emotion",
                    "message": "expression must have an emotion to activate",
                },
            )
        # Strip the emotion from any other expression that currently owns it.
        others = s.exec(select(Expression).where(Expression.emotion == e.emotion)).all()
        for other in others:
            if other.name != e.name:
                other.emotion = None
                s.add(other)
        s.commit()
        emotion = e.emotion  # capture before session closes
    return {"ok": True, "name": name, "emotion": emotion}
