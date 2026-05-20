"""Pydantic schemas for every NATS event payload. The single source of truth.

These schemas are validated on receive (bad payloads → drop + log) and exported
to TypeScript at build time so the frontend shares the same types.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ----- Enums (literal unions) -----

Emotion = Literal["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]
ServiceName = Literal["agent", "animator", "printer", "control"]
ServoName = Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
GestureName = Literal["nod_yes", "nod_no", "look_around"]

AgentStateName = Literal[
    "warming", "idle", "listening", "thinking", "speaking", "degraded", "shutdown"
]
AnimatorStateName = Literal["idle", "active", "degraded"]
PrinterStateName = Literal["idle", "printing", "error", "offline"]

# ----- Agent -----


class AgentState(BaseModel):
    state: AgentStateName
    detail: str | None = None


class AgentTranscript(BaseModel):
    text: str
    timestamp: float


class AgentReply(BaseModel):
    text: str
    emotion: Emotion
    # Where this reply originated. 'llm' = generated from a chat cycle,
    # 'puppet' = direct text-to-speech via speak_text intent (operator
    # typed exactly what Lafufu should say), 'system' = a built-in intent
    # answered directly by the agent (e.g. the "what's your IP" query).
    source: Literal["llm", "puppet", "system"] = "llm"


class AgentTtsRms(BaseModel):
    ts: float = Field(description="monotonic seconds since start of utterance")
    rms: float = Field(ge=0.0, le=1.0)
    mouth_target: float = Field(ge=0.0, le=1.0)


# Text-field cap. Generous enough for any reasonable chat / TTS line; tight
# enough that a misbehaving (or accidentally-pasted) megabyte payload doesn't
# OOM Ollama / Piper on a Pi.
_TEXT_MAX = 4096


class AgentIntentTextMessage(BaseModel):
    text: str = Field(max_length=_TEXT_MAX)


class AgentIntentSpeakText(BaseModel):
    """Speak text directly via TTS, bypassing the LLM (raw passthrough)."""

    text: str = Field(max_length=_TEXT_MAX)
    emotion: Emotion = "neutral"


# ----- Animator -----


# DXL servo positions span the 12-bit range 0..4095. Bounds catch garbage
# values at parse time; the animator service still clamps to per-servo
# calibrated ranges before writing to the bus.
_DXL_MIN = 0
_DXL_MAX = 4095


class AnimatorPose(BaseModel):
    head_lr: int = Field(ge=_DXL_MIN, le=_DXL_MAX)
    head_ud: int = Field(ge=_DXL_MIN, le=_DXL_MAX)
    eye: int = Field(ge=_DXL_MIN, le=_DXL_MAX)
    jaw: int = Field(ge=_DXL_MIN, le=_DXL_MAX)
    brow: int = Field(ge=_DXL_MIN, le=_DXL_MAX)


class AnimatorState(BaseModel):
    state: AnimatorStateName
    detail: str | None = None
    has_u2d2: bool = False


class AnimatorIntentSetPose(BaseModel):
    pose: AnimatorPose
    duration_s: float = 0.25


class AnimatorIntentPreview(BaseModel):
    name: ServoName
    position: int = Field(ge=_DXL_MIN, le=_DXL_MAX)


class AnimatorIntentPlayExpression(BaseModel):
    name: str
    intensity: float = 1.0


class AnimatorIntentGesture(BaseModel):
    name: GestureName


class AnimatorEvent(BaseModel):
    event: Literal["gesture_done", "lipsync_start", "lipsync_end"]
    name: str | None = None


# ----- Printer -----


class PrinterIntentPrintText(BaseModel):
    text: str = Field(max_length=_TEXT_MAX)
    title: str | None = Field(default=None, max_length=256)


class PrinterIntentPrintTranscript(BaseModel):
    transcript: list[dict[str, str]]


class PrinterIntentPrintFile(BaseModel):
    """Print an image file directly (e.g. the uploaded letterhead)."""

    path: str
    title: str | None = None


class PrinterIntentCompose(BaseModel):
    """Composite text (fortune-card style) onto the uploaded letterhead
    and print the result."""

    letterhead_path: str = Field(max_length=512)
    text: str = Field(max_length=_TEXT_MAX)
    lucky_subway_stop: str | None = Field(default=None, max_length=128)
    lucky_numbers: list[int] | None = Field(default=None, max_length=10)
    title: str | None = Field(default=None, max_length=256)
    # Font filename, resolved by the printer service against its font dirs.
    # None → the composer's bundled default.
    font: str | None = Field(default=None, max_length=128)


class PrinterState(BaseModel):
    state: PrinterStateName
    detail: str | None = None
    printer_name: str | None = None


class PrinterEvent(BaseModel):
    event: Literal["job_started", "job_done", "paper_out", "jam"]
    job_id: str | None = None


# ----- Config + system -----


class ConfigChanged(BaseModel):
    key: str
    value: Any
    source: str


class SystemHeartbeat(BaseModel):
    service: ServiceName
    ts: float
    uptime_s: float


class SystemError(BaseModel):
    service: ServiceName
    error_kind: str
    message: str
    details: dict[str, Any] | None = None


class SystemServiceEvent(BaseModel):
    """system.service.{starting|ready|restarting|stopped}"""

    service: ServiceName
    event: Literal["starting", "ready", "restarting", "stopped"]
    detail: str | None = None
