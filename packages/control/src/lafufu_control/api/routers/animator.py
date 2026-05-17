"""Admin → animator intent proxy."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class PreviewBody(BaseModel):
    name: Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
    position: int


class ExpressionBody(BaseModel):
    name: str
    intensity: float = 1.0


class GestureBody(BaseModel):
    name: Literal["nod_yes", "nod_no", "look_around"]


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
