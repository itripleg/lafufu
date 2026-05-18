"""Admin → agent intent proxy (headless text input + direct TTS)."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

_EMOTIONS = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]

router = APIRouter()


class TextMessageBody(BaseModel):
    text: str


class SpeakTextBody(BaseModel):
    text: str
    emotion: _EMOTIONS = "neutral"


@router.post("/text_message", status_code=202)
def text_message(body: TextMessageBody, req: Request):
    """Send text as user input — LLM generates reply, TTS speaks it."""
    req.app.state.nats_publish("agent.intent.text_message", body.model_dump())
    return {"ok": True}


@router.post("/speak_text", status_code=202)
def speak_text(body: SpeakTextBody, req: Request):
    """Speak text directly via TTS, bypassing the LLM.

    For long-form or scripted speech where Lafufu should say exactly what
    was typed (puppet mode).
    """
    req.app.state.nats_publish("agent.intent.speak_text", body.model_dump())
    return {"ok": True}
