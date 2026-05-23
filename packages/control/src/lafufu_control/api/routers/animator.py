"""Admin → animator intent proxy."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ...models import Frame

router = APIRouter()


class PreviewBody(BaseModel):
    name: Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
    position: int


class ExpressionBody(BaseModel):
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
def expression(body: ExpressionBody, req: Request):
    req.app.state.nats_publish("animator.intent.play_expression", body.model_dump())
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
