"""Admin → agent intent proxy (headless text input + direct TTS) + Ollama discovery."""

import json
import os
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_EMOTIONS = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]

OLLAMA_URL = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

router = APIRouter()


@router.get("/models")
async def list_models(_: Request):
    """List Ollama models available on the Pi (via /api/tags).

    Used by the admin settings form to populate a dropdown for agent.llm_model.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "ollama_unreachable", "message": str(e)},
        ) from e
    # Ollama returns {"models": [{"name": "qwen2.5:7b", "size": ..., "modified_at": ...}, ...]}
    models = data.get("models", [])
    return {
        "models": [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
            }
            for m in models
            if m.get("name")
        ]
    }


@router.get("/stt_backends")
async def list_stt_backends(_: Request):
    """List installed STT backends.

    Used by the admin settings form to populate a dropdown for agent.stt_backend.
    Importing lafufu_agent here couples the control package to agent — that's
    acceptable because they ship in the same monorepo + venv.
    """
    from lafufu_agent.stt import available_backends

    return {"backends": available_backends()}


@router.get("/voices")
async def list_voices(_: Request):
    """List Piper voice models (.onnx files) in LAFUFU_MODELS_DIR.

    Used by the admin settings form to populate a dropdown for agent.voice_model.
    Each entry's ``name`` is the bare filename without ``.onnx`` — that's the
    value the setting takes. Voices without a companion ``.onnx.json`` are
    returned with ``has_config: false`` so the UI can filter them out (Piper
    requires the config to load).
    """
    models_dir = Path(os.environ.get("LAFUFU_MODELS_DIR", "/srv/lafufu/models"))
    if not models_dir.is_dir():
        return {"voices": []}

    voices = []
    for onnx_path in sorted(models_dir.glob("*.onnx")):
        cfg_path = onnx_path.with_suffix(".onnx.json")
        sample_rate = None
        if cfg_path.is_file():
            try:
                with cfg_path.open(encoding="utf-8") as f:
                    sample_rate = json.load(f).get("audio", {}).get("sample_rate")
            except (OSError, json.JSONDecodeError):
                # Best-effort metadata — listing must not fail on a malformed config.
                pass
        voices.append(
            {
                "name": onnx_path.stem,
                "label": onnx_path.stem,
                "sample_rate": sample_rate,
                "size_bytes": onnx_path.stat().st_size,
                "has_config": cfg_path.is_file(),
            }
        )
    return {"voices": voices}


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
