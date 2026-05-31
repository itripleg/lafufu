"""Pydantic schemas for every NATS event payload. The single source of truth.

These schemas are validated on receive (bad payloads → drop + log) and exported
to TypeScript at build time so the frontend shares the same types.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ----- Enums (literal unions) -----

# Emotion was previously a closed Literal type. The expression registry
# (control's DB) is now the validity check — see resolve_emotion_to_play_intent.
# Keeping `Emotion` as `str` lets the LLM emit any name without a Pydantic
# crash; unknown names get logged + no-op'd downstream.
Emotion = str
ServiceName = Literal["agent", "animator", "printer", "control"]
ServoName = Literal["head_lr", "head_ud", "eye", "jaw", "brow"]
GestureName = Literal["nod_yes", "nod_no", "look_around"]

AgentStateName = Literal[
    "warming",
    "idle",
    "wake_listening",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "degraded",
    "shutdown",
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


class AnimatorPlayStep(BaseModel):
    pose: AnimatorPose
    image: str | None = None
    duration_ms: int | None = None
    delay_ms: int | None = None
    easing: str | None = None


class RandomWalkConfig(BaseModel):
    """Sinusoidal living-presence motion config — for playback='random_walk'.

    Three knobs total; each is normalised so 1.0 = "natural default".
    """

    intensity: float = Field(default=1.0, ge=0.0, le=2.0)
    """Scales per-servo amplitude. 0 = no motion; 1 = pre-rewrite defaults."""
    speed: float = Field(default=1.0, ge=0.1, le=4.0)
    """Inversely scales segment duration. 2 = twice as fast; 0.5 = half speed."""
    pause_chance: float = Field(default=0.30, ge=0.0, le=1.0)
    """Fraction of segments that hold idle instead of moving."""


class AnimatorIntentPlayExpression(BaseModel):
    name: str
    playback: Literal["once", "loop", "shuffle", "random_walk"] = "once"
    steps: list[AnimatorPlayStep] = []
    default_duration_ms: int = 250
    default_delay_ms: int = 80
    default_easing: str = "ease-in-out"
    random_walk_config: RandomWalkConfig | None = None


class AnimatorEventFrame(BaseModel):
    expression: str
    step_index: int
    frame: str
    image: str | None = None
    started_at_ms: int


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


class PrinterIntentComposeFortune(BaseModel):
    """Compose a fortune onto the printer's *active* letterhead + font and
    print it.

    Unlike ``PrinterIntentCompose``, the letterhead and font are NOT supplied
    by the publisher — the printer service resolves them from its own
    active-asset pointers. This keeps the agent (the publisher) decoupled from
    printer filesystem layout and means no untrusted path crosses the bus: the
    caller only provides the fortune body + lucky info.
    """

    text: str = Field(max_length=_TEXT_MAX)
    lucky_subway_stop: str | None = Field(default=None, max_length=128)
    lucky_numbers: list[int] | None = Field(default=None, max_length=10)
    title: str | None = Field(default=None, max_length=256)


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
