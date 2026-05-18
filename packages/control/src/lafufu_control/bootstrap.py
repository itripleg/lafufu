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
    (
        "printer.media",
        "4x6",
        "str",
        "Paper size name for lp. Default 4x6 matches the Phomemo label stock. Other valid values for this printer: 4x8, 2x1, Round108, Round144, Custom.WIDTHxHEIGHT.",
    ),
    (
        "printer.adjust_vertical",
        "0",
        "int",
        "Phomemo driver vertical offset (-20..+20). Negative shifts the print UP. One step ≈ 1mm on most label printers.",
    ),
    (
        "printer.adjust_horizontal",
        "0",
        "int",
        "Phomemo driver horizontal offset (-20..+20). Negative shifts LEFT.",
    ),
    (
        "printer.feed_offset",
        "0",
        "int",
        "Phomemo feed-offset (-20..+20). Adjusts where the label feeds before printing — use if the whole image is consistently too high/low.",
    ),
    (
        "printer.rotate",
        "0",
        "int",
        "Phomemo rotation: 0=none, 1=90°, 2=180°, 3=270°.",
    ),
    (
        "printer.scale_pct",
        "100",
        "int",
        "Image scale percent. 100 = fit-to-page default. Lower if the print spills off the edge of the card.",
    ),
    (
        "printer.lp_options",
        "",
        "str",
        "Raw extra `lp` options appended after the structured ones above (escape hatch). Whitespace-separated.",
    ),
    # TTS
    (
        "tts.length_scale",
        "0.95",
        "float",
        "Piper length scale. Less than 1 = faster speech, greater than 1 = slower.",
    ),
    # Speaker
    (
        "speaker.volume",
        "80",
        "int",
        "USB speaker playback volume (0-100%). Applied to ALSA mixer 'PCM' on card 'USB'.",
    ),
    (
        "speaker.alsa_card",
        "USB",
        "str",
        "ALSA card name for the playback device (run `aplay -l` to see options).",
    ),
    (
        "speaker.alsa_control",
        "PCM",
        "str",
        "ALSA simple mixer control name on speaker.alsa_card (run `amixer -c <card> scontrols`).",
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
