"""Admin → agent intent proxy (headless text input)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class TextMessageBody(BaseModel):
    text: str


@router.post("/text_message", status_code=202)
def text_message(body: TextMessageBody, req: Request):
    req.app.state.nats_publish("agent.intent.text_message", body.model_dump())
    return {"ok": True}
