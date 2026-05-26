"""Seed default settings on first run.

Inserts default tunables into the Settings table if absent.
Existing values are never overwritten.
"""

import logging

from lafufu_shared.prompts import DEFAULT_SYSTEM_PROMPT
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
        DEFAULT_SYSTEM_PROMPT,
        "str",
        "Personality prompt sent to the LLM with every request.",
    ),
    (
        "agent.llm_model",
        "qwen2.5:7b",
        "str",
        "Ollama model name. Changing requires agent service restart.",
    ),
    (
        "agent.stt_backend",
        "openai-whisper",
        "str",
        "Speech-to-text backend. 'openai-whisper' is the reference; 'faster-whisper' is CTranslate2-based and ~3-4x faster on the Pi. Switch live from the admin UI.",
    ),
    (
        "agent.whisper_model",
        "tiny.en",
        "str",
        "STT model name. For openai-whisper: tiny/base/small/medium/large (or .en variants). For faster-whisper: same names work. Changing live forces a reload on the next utterance.",
    ),
    (
        "agent.voice_model",
        "lafufu_voice",
        "str",
        "Piper TTS voice (bare filename, no .onnx). Files live in LAFUFU_MODELS_DIR (default /srv/lafufu/models). Switched live by the agent — next utterance uses the new voice.",
    ),
    (
        "agent.interaction_mode",
        "continuous",
        "str",
        "Interaction loop mode. 'continuous' = listen anything, optionally auto-print. 'trigger' = wake-word-gated guided fortune (requires agent.wakeword.enabled=true).",
    ),
    (
        "agent.trigger.phrase",
        "Welcome, traveler. Ask, and the cards shall reveal.",
        "str",
        "Trigger-mode opening line Lafufu speaks after the wake word fires.",
    ),
    (
        "agent.trigger.emotion",
        "neutral",
        "str",
        "Emotion (face animation) for the trigger-mode opening line. One of: happy, sad, angry, surprised, neutral, agree, disagree.",
    ),
    (
        "agent.trigger.rounds",
        "1",
        "int",
        "Trigger-mode: number of back-and-forth rounds AFTER the opening. 1 = single Q&A; 2+ = conversation.",
    ),
    (
        "agent.trigger.print_mode",
        "ask",
        "str",
        "Trigger-mode print behavior at session end. 'none' = never print; 'auto' = always print the last reply; 'ask' = Lafufu asks the visitor.",
    ),
    (
        "agent.trigger.print_prompt",
        "Would you like a printed fortune?",
        "str",
        "Trigger-mode: line Lafufu speaks before the y/n print listen. Only used when agent.trigger.print_mode='ask'.",
    ),
    (
        "agent.wakeword.enabled",
        "true",
        "bool",
        "Whether the wake-word gate is active. When true, the mic ignores everything until the configured keyword fires (Whisper stays idle). Required for trigger mode.",
    ),
    (
        "agent.wakeword.model",
        "assets/wakeword/lafufu.onnx",
        "str",
        "openwakeword model name (one of the bundled defaults, e.g. 'hey_jarvis_v0.1') or a path to a custom .onnx (resolved relative to the agent's working directory).",
    ),
    (
        "agent.wakeword.threshold",
        "0.5",
        "float",
        "Wake-word confidence threshold (0.0-1.0). Lower = more sensitive (more false positives); higher = needs clearer pronunciation.",
    ),
    (
        "agent.input_device",
        "auto",
        "str",
        "Mic device. 'auto' uses the PREFER list -> PyAudio default -> first non-AVOID chain. Otherwise: a numeric PyAudio device index or a name substring (case-insensitive).",
    ),
    # Animator
    (
        "animator.idle_animation.enabled",
        "true",
        "bool",
        "When true, animator applies subtle living-presence motion when not actively driven.",
    ),
    (
        "animator.head_lr.default",
        "2063",
        "int",
        "Default head left/right servo position (DXL units, 1828=right..2298=left). Moves the robot live when changed.",
    ),
    (
        "animator.head_ud.default",
        "3082",
        "int",
        "Default head up/down servo position (DXL units, 2885=up..3278=down). Moves the robot live when changed.",
    ),
    (
        "animator.eye.default",
        "2045",
        "int",
        "Default eye servo position (DXL units, 1995=left..2085=right). Moves the robot live when changed.",
    ),
    (
        "animator.jaw.default",
        "1811",
        "int",
        "Default jaw closed position (DXL units, 1594=open..1811=closed). Moves the robot live when changed.",
    ),
    (
        "animator.brow.default",
        "2075",
        "int",
        "Default brow position (DXL units, 2056=down..2087=up). Moves the robot live when changed.",
    ),
    # Lipsync envelope tuning. attack = how fast the mouth opens to a louder
    # sample; release = how fast it closes back to quieter. offset shifts the
    # whole envelope in time relative to audio playback — bump it up if the
    # mouth still moves before you hear the speech.
    (
        "animator.lipsync.attack_ms",
        "30",
        "int",
        "Mouth-open speed in ms (5=snappy..200=sluggish). Smaller = jaw tracks loud syllable onsets more tightly.",
    ),
    (
        "animator.lipsync.release_ms",
        "80",
        "int",
        "Mouth-close speed in ms (5=fast..400=slow). Smaller = jaw snaps shut between syllables; larger = lingering open shape.",
    ),
    (
        "animator.lipsync.offset_ms",
        "0",
        "int",
        "Delay applied to each RMS event before driving the jaw (0..500 ms). Bump up if the mouth still moves before audio is heard.",
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
        "printer.dead_zone_top_mm",
        "3",
        "int",
        "Physical dead zone at the TOP of each label where the print head can't reach (mm). Image content is pushed below this so nothing gets clipped. Phomemo 4x6 = ~3mm.",
    ),
    (
        "printer.dead_zone_bottom_mm",
        "0",
        "int",
        "Same as above but for the BOTTOM edge. Increase if the last lines of your print get cut off.",
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
