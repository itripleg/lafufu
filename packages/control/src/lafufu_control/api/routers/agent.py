"""Admin → agent intent proxy (headless text input + direct TTS) + Ollama discovery."""

import json
import os
from pathlib import Path
from typing import Literal, NamedTuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from lafufu_shared.prompts import DEFAULT_SYSTEM_PROMPT, FORTUNE_TELLER_PROMPT
from pydantic import BaseModel, Field
from sqlmodel import Session

from ...models.setting import Setting
from .settings import apply_setting

_EMOTIONS = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]

OLLAMA_URL = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

router = APIRouter()


# Lazy import so the control service can run on machines without PyAudio
# installed (PyAudio is an agent dep, not a control dep). Resolved at
# request time inside the endpoint.
def get_pyaudio():
    from lafufu_agent.audio_capture import get_pyaudio as _impl

    return _impl()


def list_output_cards():
    # Lazy import + thin wrapper so the agent dep stays optional and tests can
    # patch this symbol. Uses the SAME enumeration the agent's resolver does
    # (aplay -l), so the dropdown values match what `auto` actually picks.
    from lafufu_agent.audio_output import list_output_cards as _impl

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


@router.get("/output-devices")
async def list_output_devices(_: Request):
    """List ALSA playback cards the agent can send TTS audio to.

    First entry is the ``auto`` sentinel — it picks the first NON-HDMI card
    (HDMI is never auto-selected: Pi HDMI adds lag and the typical monitor has
    no speakers). Its label names the card ``auto`` currently resolves to. The
    remaining entries are bare ALSA card short-names — the resolver wraps them
    as ``plughw:CARD=<name>,DEV=0``. HDMI cards are listed so the operator CAN
    pick one explicitly, but flagged so they're not chosen by accident.
    """
    cards = list_output_cards()
    first_non_hdmi = next((c for c in cards if "hdmi" not in c.lower()), None)
    auto_label = f"auto — {first_non_hdmi}" if first_non_hdmi else "auto — no non-HDMI device"
    devices: list[dict] = [{"name": "auto", "label": auto_label}]
    for c in cards:
        devices.append({"name": c, "label": f"{c} (HDMI)" if "hdmi" in c.lower() else c})
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


# ─────────────────────── prompt switcher ──────────────────
#
# Two built-in presets. `agent.system_prompt` (seeded in bootstrap) stays the
# LIVE value the agent consumes; the active preset's text is mirrored into it.
# Selecting / editing / restoring the ACTIVE preset writes agent.system_prompt
# through `apply_setting`, which publishes config.changed.agent.system_prompt so
# the running agent live-reloads.

PromptId = Literal["street_oracle", "fortune_teller"]

_PRESET_KEY_ACTIVE = "agent.prompt_preset"
_PRESET_KEY_LIVE = "agent.system_prompt"


class _Preset(NamedTuple):
    """A built-in prompt preset: display label, the canonical (shipped) text
    used as the restore-to-default target, and the settings key holding the
    operator-editable saved text."""

    label: str
    canonical_text: str
    setting_key: str


# id -> preset. Insertion order (street_oracle, fortune_teller) is the API order.
_PRESETS: dict[str, _Preset] = {
    "street_oracle": _Preset("Street Oracle", DEFAULT_SYSTEM_PROMPT, "agent.prompt.street_oracle"),
    "fortune_teller": _Preset(
        "Fortune Teller", FORTUNE_TELLER_PROMPT, "agent.prompt.fortune_teller"
    ),
}


class PromptSelectBody(BaseModel):
    id: PromptId


class PromptEditBody(BaseModel):
    # Cap matches the agent text bound + the Setting.value DB column (max 4000).
    text: str = Field(max_length=4000)


def _read_value(engine, key: str, fallback: str = "") -> str:
    with Session(engine) as s:
        row = s.get(Setting, key)
        return row.value if row is not None else fallback


def _prompts_payload(engine) -> dict:
    """The GET /prompts response — active preset + both presets' saved text."""
    active = _read_value(engine, _PRESET_KEY_ACTIVE, "street_oracle")
    presets = []
    for pid, preset in _PRESETS.items():
        text = _read_value(engine, preset.setting_key, preset.canonical_text)
        presets.append(
            {
                "id": pid,
                "label": preset.label,
                "text": text,
                "is_default": text == preset.canonical_text,
            }
        )
    return {"active": active, "presets": presets}


@router.get("/prompts")
def get_prompts(req: Request):
    """List both built-in presets, the active one, and each preset's saved text."""
    return _prompts_payload(req.app.state.engine)


@router.post("/prompts/select")
def select_prompt(body: PromptSelectBody, req: Request):
    """Switch the active preset. Sets agent.prompt_preset AND mirrors the
    preset's saved text into agent.system_prompt (the live value) so the agent
    reloads it via config.changed.agent.system_prompt."""
    engine = req.app.state.engine
    publish = req.app.state.nats_publish
    preset = _PRESETS[body.id]
    text = _read_value(engine, preset.setting_key, preset.canonical_text)
    apply_setting(engine, publish, _PRESET_KEY_ACTIVE, body.id, "str")
    apply_setting(engine, publish, _PRESET_KEY_LIVE, text, "str")
    return _prompts_payload(engine)


@router.put("/prompts/{prompt_id}")
def edit_prompt(prompt_id: PromptId, body: PromptEditBody, req: Request):
    """Save edited text for a preset. If it's the active preset, also mirror it
    into the live agent.system_prompt."""
    engine = req.app.state.engine
    publish = req.app.state.nats_publish
    preset = _PRESETS[prompt_id]
    apply_setting(engine, publish, preset.setting_key, body.text, "str")
    if _read_value(engine, _PRESET_KEY_ACTIVE, "street_oracle") == prompt_id:
        apply_setting(engine, publish, _PRESET_KEY_LIVE, body.text, "str")
    return _prompts_payload(engine)


@router.post("/prompts/{prompt_id}/restore")
def restore_prompt(prompt_id: PromptId, req: Request):
    """Reset a preset's saved text to its shipped canonical text. If it's the
    active preset, also restore the live agent.system_prompt."""
    engine = req.app.state.engine
    publish = req.app.state.nats_publish
    preset = _PRESETS[prompt_id]
    apply_setting(engine, publish, preset.setting_key, preset.canonical_text, "str")
    if _read_value(engine, _PRESET_KEY_ACTIVE, "street_oracle") == prompt_id:
        apply_setting(engine, publish, _PRESET_KEY_LIVE, preset.canonical_text, "str")
    return _prompts_payload(engine)
