"""Admin → agent intent proxy (headless text input + direct TTS) + Ollama discovery."""

import json
import os
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

_EMOTIONS = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]

OLLAMA_URL = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

router = APIRouter()


# Lazy import so the control service can run on machines without PyAudio
# installed (PyAudio is an agent dep, not a control dep). Resolved at
# request time inside the endpoint.
def get_pyaudio():
    from lafufu_agent.audio_capture import get_pyaudio as _impl

    return _impl()


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


@router.get("/input-devices")
async def list_input_devices(_: Request):
    """List PyAudio input devices the agent can bind its mic to.

    First entry is always the ``auto`` sentinel — selecting it falls
    through to the existing PREFER -> PyAudio default -> first-non-avoided
    chain. Other entries' ``name`` field is the numeric PyAudio index as a
    string (matching how ``LAFUFU_INPUT_DEVICE`` and ``agent.input_device``
    parse the value).
    """
    devices: list[dict] = [
        {"name": "auto", "label": "auto — system default", "channels": 0},
    ]
    try:
        p = get_pyaudio()
    except Exception as e:
        # PyAudio not importable on this host (control sometimes runs on
        # machines without ALSA / PortAudio). Return just the sentinel so
        # the dropdown still renders.
        return {"devices": devices, "error": str(e)}

    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append(
                {
                    "name": str(int(info["index"])),
                    "label": info.get("name", f"device {i}"),
                    "channels": int(info.get("maxInputChannels", 0)),
                }
            )
    return {"devices": devices}


# Canonical Whisper model names + their approximate download sizes (MB).
# Both OpenAI Whisper and faster-whisper download against these identifiers,
# so no real "list models" API exists on either backend — the set is fixed.
# Sizes are openai-whisper's published .pt sizes; faster-whisper variants are
# slightly smaller (CT2 format) but close enough for a download-warning UI.
_WHISPER_MODELS: list[tuple[str, int]] = [
    ("tiny.en", 39),
    ("tiny", 39),
    ("base.en", 74),
    ("base", 74),
    ("small.en", 244),
    ("small", 244),
    ("medium.en", 769),
    ("medium", 769),
    ("large-v3", 1550),
    ("large-v2", 1550),
    ("large", 1550),
]


@router.get("/whisper-models")
async def list_whisper_models(_: Request):
    """List the canonical Whisper / faster-whisper model identifiers + which
    are already cached on disk.

    Used by the admin settings form to populate a dropdown for
    ``agent.whisper_model`` with a download-size warning per model. Neither
    backend exposes a "list installed models" API — they download lazily on
    first use against the canonical names — so the set is hardcoded and the
    cached-detection looks at openai-whisper's ``~/.cache/whisper/*.pt`` cache.
    """
    # openai-whisper writes to ~/.cache/whisper/<name>.pt by default; the
    # download_root parameter can override but our agent process uses the
    # default. faster-whisper stores under HuggingFace's hub cache (deeper
    # nesting); we don't probe it here — better-than-nothing for one backend
    # is fine for a download-warning hint.
    cache_dir = Path.home() / ".cache" / "whisper"
    cached_names: set[str] = set()
    if cache_dir.is_dir():
        for pt in cache_dir.glob("*.pt"):
            cached_names.add(pt.stem)

    return {
        "models": [
            {"name": name, "size_mb": size, "cached": name in cached_names}
            for name, size in _WHISPER_MODELS
        ]
    }


class TextMessageBody(BaseModel):
    # Spoken/chat text is short; bound prevents oversized NATS payloads.
    text: str = Field(max_length=2000)


class SpeakTextBody(BaseModel):
    # Spoken/chat text is short; bound prevents oversized NATS payloads.
    text: str = Field(max_length=2000)
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
