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

# Any token here in the transcript flips the result to non-affirmative, even
# if an affirmative keyword also appears. Catches the common false-positive
# class: "of course not", "absolutely not", "yeah no", "yes never mind",
# "sure no problem". Better to under-print than over-print.
_NEGATIONS = frozenset(
    [
        "no",
        "not",
        "never",
        "nope",
        "nah",
        "skip",
        "cancel",
        "don't",
        "dont",
    ]
)


def validate_emotion(value: str) -> str:
    """Normalise the trigger emotion. Any non-empty name is accepted — the
    control plane's DB lookup is the validity check; an unknown name no-ops
    with a warning rather than crashing here."""
    return value.strip().lower()


def validate_print_mode(value: str) -> PrintMode:
    norm = value.strip().lower()
    if norm not in _PRINT_MODES:
        raise ValueError(f"print_mode={value!r} is not one of {list(_PRINT_MODES)}")
    return norm  # type: ignore[return-value]


def validate_rounds(value) -> int:
    """Coerce + validate trigger rounds. Accepts int or any int-coercible str."""
    n = int(value)
    if n < 1:
        raise ValueError(f"rounds={value!r} must be >= 1")
    return n


# Strip leading/trailing punctuation that's commonly hallucinated by Whisper.
_TRIM_RE = re.compile(r"^[^\w]+|[^\w]+$")
# Strip leading/trailing punctuation from individual tokens (e.g. "yes," → "yes").
_TOKEN_TRIM_RE = re.compile(r"^[^\w']+|[^\w']+$")


def is_affirmative(transcript: str) -> bool:
    """Coarse yes/no classifier for the ask-to-print prompt."""
    norm = _TRIM_RE.sub("", transcript.strip().lower())
    if not norm:
        return False
    tokens = [_TOKEN_TRIM_RE.sub("", t) for t in norm.split() if t]
    tokens = [t for t in tokens if t]  # drop any empty strings after stripping
    if any(t in _NEGATIONS for t in tokens):
        return False
    if norm in _AFFIRMATIVES:
        return True
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
            rounds = validate_rounds(rounds_raw)
        except ValueError as e:
            if "must be >= 1" in str(e):
                raise ValueError(f"LAFUFU_TRIGGER_ROUNDS={rounds_raw!r} must be >= 1") from e
            raise ValueError(f"LAFUFU_TRIGGER_ROUNDS={rounds_raw!r} is not an integer") from e

        print_mode_raw = env.get("LAFUFU_TRIGGER_PRINT", "ask")
        try:
            print_mode = validate_print_mode(print_mode_raw)
        except ValueError as e:
            raise ValueError(
                f"LAFUFU_TRIGGER_PRINT={print_mode_raw.strip().lower()!r} is not one of {list(_PRINT_MODES)}"
            ) from e

        emotion = validate_emotion(env.get("LAFUFU_TRIGGER_EMOTION", "neutral"))

        return cls(
            phrase=env.get("LAFUFU_TRIGGER_PHRASE", _DEFAULT_PHRASE),
            emotion=emotion,
            rounds=rounds,
            print_mode=print_mode,
            print_prompt=env.get("LAFUFU_TRIGGER_PRINT_PROMPT", _DEFAULT_PRINT_PROMPT),
        )
