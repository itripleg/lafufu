"""Seed default settings on first run.

Inserts default tunables into the Settings table if absent.
Existing values are never overwritten.
"""

import logging

from sqlmodel import Session, select

from .models.setting import Setting

log = logging.getLogger(__name__)


# (key, value, value_type, description)
DEFAULTS: list[tuple[str, str, str, str]] = [
    # Agent
    (
        "agent.silence_threshold",
        "1500",
        "int",
        "RMS threshold below which audio counts as silence (VAD). Higher = less sensitive to ambient noise. Default 1500.",
    ),
    (
        "agent.silence_seconds",
        "1.5",
        "float",
        "Seconds of continuous silence that end an utterance.",
    ),
    (
        "agent.auto_listen",
        "false",
        "bool",
        "Whether the mic loop starts automatically on agent boot. When false, agent only responds to text intents.",
    ),
    (
        "agent.system_prompt",
        "You are Lafufu, a mischievous and playful humanoid creature. Reply in no more than 20 words. Always output an emotion tag in brackets first (happy, sad, angry, surprised, neutral, agree, disagree), then the response. Never use emojis.",
        "str",
        "Personality prompt sent to the LLM with every request.",
    ),
    (
        "agent.llm_model",
        "qwen2.5:7b",
        "str",
        "Ollama model name. Changing requires agent service restart.",
    ),
    # Animator
    (
        "animator.idle_animation.enabled",
        "true",
        "bool",
        "When true, animator applies subtle living-presence motion when not actively driven.",
    ),
    # Printer
    (
        "printer.auto_print",
        "false",
        "bool",
        "When true, every agent reply is auto-printed. Default false prevents runaway prints during mic feedback.",
    ),
    # TTS
    (
        "tts.length_scale",
        "0.95",
        "float",
        "Piper length scale. Less than 1 = faster speech, greater than 1 = slower.",
    ),
]


def seed_default_settings(engine) -> None:
    """Insert any missing default settings. Idempotent."""
    inserted = 0
    with Session(engine) as s:
        existing = {row.key for row in s.exec(select(Setting)).all()}
        for key, value, value_type, description in DEFAULTS:
            if key in existing:
                continue
            s.add(Setting(key=key, value=value, value_type=value_type, description=description))
            inserted += 1
        if inserted:
            s.commit()
            log.info("settings.seeded count=%d", inserted)
        else:
            log.info("settings.bootstrap.no_new_settings")
