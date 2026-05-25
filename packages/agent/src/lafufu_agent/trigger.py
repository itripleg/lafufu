"""Trigger-mode interaction loop helpers.

Pure data + a single transcript classifier. The orchestration that drives one
session (wake -> opening -> N rounds -> optional print) lives in
``AgentService._trigger_session`` because it ties together the mic, STT, LLM,
TTS, and printer; the testable bits are isolated here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

PrintMode = Literal["none", "auto", "ask"]
_PRINT_MODES: tuple[PrintMode, ...] = ("none", "auto", "ask")

_DEFAULT_PHRASE = "Welcome, traveler. Ask, and the cards shall reveal."
_DEFAULT_PRINT_PROMPT = "Would you like a printed fortune?"

# Keep this list short and well-known. "ask" mode is a coarse y/n gate, not an
# NLU pipeline — false negatives are fine (Lafufu just doesn't print), false
# positives waste paper. Tune by use, not by adding fuzzy matches.
_AFFIRMATIVES = frozenset(
    [
        "y",
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "please",
        "ok",
        "okay",
        "absolutely",
    ]
)
# Affirmative *phrases* (multi-word) checked with substring after normalisation.
_AFFIRMATIVE_PHRASES = ("do it", "of course")

# Strip leading/trailing punctuation that's commonly hallucinated by Whisper.
_TRIM_RE = re.compile(r"^[^\w]+|[^\w]+$")


def is_affirmative(transcript: str) -> bool:
    """Coarse yes/no classifier for the ask-to-print prompt."""
    norm = _TRIM_RE.sub("", transcript.strip().lower())
    if not norm:
        return False
    if norm in _AFFIRMATIVES:
        return True
    tokens = norm.split()
    if tokens and tokens[0] in _AFFIRMATIVES:
        return True
    return any(phrase in norm for phrase in _AFFIRMATIVE_PHRASES)


class InteractionMode(StrEnum):
    CONTINUOUS = "continuous"
    TRIGGER = "trigger"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> InteractionMode:
        raw = env.get("LAFUFU_INTERACTION_MODE", "continuous").strip().lower()
        try:
            return cls(raw)
        except ValueError as e:
            raise ValueError(
                f"LAFUFU_INTERACTION_MODE={raw!r} is not one of {[m.value for m in cls]}"
            ) from e


@dataclass(frozen=True)
class TriggerConfig:
    """Configuration for trigger-mode interactions, loaded from env at boot."""

    phrase: str
    emotion: str
    rounds: int
    print_mode: PrintMode
    print_prompt: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> TriggerConfig:
        rounds_raw = env.get("LAFUFU_TRIGGER_ROUNDS", "1")
        try:
            rounds = int(rounds_raw)
        except ValueError as e:
            raise ValueError(f"LAFUFU_TRIGGER_ROUNDS={rounds_raw!r} is not an integer") from e
        if rounds < 1:
            raise ValueError(f"LAFUFU_TRIGGER_ROUNDS={rounds_raw!r} must be >= 1")

        print_mode_raw = env.get("LAFUFU_TRIGGER_PRINT", "ask").strip().lower()
        if print_mode_raw not in _PRINT_MODES:
            raise ValueError(
                f"LAFUFU_TRIGGER_PRINT={print_mode_raw!r} is not one of {list(_PRINT_MODES)}"
            )

        return cls(
            phrase=env.get("LAFUFU_TRIGGER_PHRASE", _DEFAULT_PHRASE),
            emotion=env.get("LAFUFU_TRIGGER_EMOTION", "neutral"),
            rounds=rounds,
            print_mode=print_mode_raw,  # type: ignore[arg-type]
            print_prompt=env.get("LAFUFU_TRIGGER_PRINT_PROMPT", _DEFAULT_PRINT_PROMPT),
        )
