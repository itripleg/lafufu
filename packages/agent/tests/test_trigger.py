"""Pure-helper tests for the trigger-mode interaction loop."""

from __future__ import annotations

import pytest
from lafufu_agent.trigger import InteractionMode, TriggerConfig, is_affirmative


class TestIsAffirmative:
    @pytest.mark.parametrize(
        "transcript",
        [
            "yes",
            "Yes",
            "YES PLEASE",
            "yeah",
            "yep",
            "yup",
            "sure",
            "please",
            "ok",
            "okay",
            "do it",
            "of course",
            "absolutely",
            "y",
            "yeah print it",
            "  yes  ",
        ],
    )
    def test_recognizes_affirmative(self, transcript: str) -> None:
        assert is_affirmative(transcript) is True

    @pytest.mark.parametrize(
        "transcript",
        [
            "no",
            "no thanks",
            "nope",
            "not now",
            "",
            "   ",
            "maybe",
            "i don't know",
            "skip it",
        ],
    )
    def test_rejects_non_affirmative(self, transcript: str) -> None:
        assert is_affirmative(transcript) is False

    @pytest.mark.parametrize(
        "transcript",
        [
            # Common negations that contain an affirmative keyword as a prefix
            # or phrase. The classifier must NOT treat these as a yes — they
            # are the dominant false-positive class for ask-mode printing.
            "of course not",
            "absolutely not",
            "yeah no",
            "yes never mind",
            "sure no problem",
            "yep don't print it",
            "yes don't",
            "ok never mind",
        ],
    )
    def test_negation_overrides_affirmative_keyword(self, transcript: str) -> None:
        assert is_affirmative(transcript) is False

    @pytest.mark.parametrize(
        "transcript",
        [
            "yes, please",
            "yes,",
            "yeah!",
            "sure.",
            "okay,",
            "yes please.",
        ],
    )
    def test_affirmative_with_internal_punctuation(self, transcript: str) -> None:
        """Affirmatives followed by comma, period, or exclamation must still match.
        _TRIM_RE only strips leading/trailing punctuation from the whole string,
        not from individual tokens — 'yes,' would tokenize as 'yes,' (with comma)."""
        assert is_affirmative(transcript), (
            f"'{transcript}' should be affirmative but is_affirmative returned False"
        )

    @pytest.mark.parametrize(
        "transcript",
        [
            "no, please don't",
            "yes, but not really",
            "sure, never mind",
        ],
    )
    def test_negation_overrides_punctuation_affirmative(self, transcript: str) -> None:
        """A negation token must still override even when the affirmative has punctuation."""
        assert not is_affirmative(transcript), (
            f"'{transcript}' should NOT be affirmative but is_affirmative returned True"
        )


class TestInteractionModeFromEnv:
    def test_default_is_continuous(self) -> None:
        assert InteractionMode.from_env({}) is InteractionMode.CONTINUOUS

    def test_explicit_continuous(self) -> None:
        assert (
            InteractionMode.from_env({"LAFUFU_INTERACTION_MODE": "continuous"})
            is InteractionMode.CONTINUOUS
        )

    def test_trigger(self) -> None:
        assert (
            InteractionMode.from_env({"LAFUFU_INTERACTION_MODE": "trigger"})
            is InteractionMode.TRIGGER
        )

    def test_case_insensitive(self) -> None:
        assert (
            InteractionMode.from_env({"LAFUFU_INTERACTION_MODE": "TRIGGER"})
            is InteractionMode.TRIGGER
        )

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="LAFUFU_INTERACTION_MODE"):
            InteractionMode.from_env({"LAFUFU_INTERACTION_MODE": "bogus"})


class TestTriggerConfigFromEnv:
    def test_all_defaults(self) -> None:
        cfg = TriggerConfig.from_env({})
        assert cfg.phrase
        assert cfg.emotion == "neutral"
        assert cfg.rounds == 1
        assert cfg.print_mode == "ask"
        assert cfg.print_prompt

    def test_explicit_values(self) -> None:
        cfg = TriggerConfig.from_env(
            {
                "LAFUFU_TRIGGER_PHRASE": "Ask, traveler.",
                "LAFUFU_TRIGGER_EMOTION": "happy",
                "LAFUFU_TRIGGER_ROUNDS": "3",
                "LAFUFU_TRIGGER_PRINT": "auto",
                "LAFUFU_TRIGGER_PRINT_PROMPT": "Want a slip?",
            },
        )
        assert cfg.phrase == "Ask, traveler."
        assert cfg.emotion == "happy"
        assert cfg.rounds == 3
        assert cfg.print_mode == "auto"
        assert cfg.print_prompt == "Want a slip?"

    def test_rounds_must_be_positive_int(self) -> None:
        with pytest.raises(ValueError, match="LAFUFU_TRIGGER_ROUNDS"):
            TriggerConfig.from_env({"LAFUFU_TRIGGER_ROUNDS": "0"})
        with pytest.raises(ValueError, match="LAFUFU_TRIGGER_ROUNDS"):
            TriggerConfig.from_env({"LAFUFU_TRIGGER_ROUNDS": "-1"})
        with pytest.raises(ValueError, match="LAFUFU_TRIGGER_ROUNDS"):
            TriggerConfig.from_env({"LAFUFU_TRIGGER_ROUNDS": "abc"})

    def test_invalid_print_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="LAFUFU_TRIGGER_PRINT"):
            TriggerConfig.from_env({"LAFUFU_TRIGGER_PRINT": "always"})

    def test_unknown_emotion_accepted_passthrough(self) -> None:
        """Any string is now accepted — the control plane's DB lookup is the
        validity check, not a hardcoded set in trigger.py."""
        cfg = TriggerConfig.from_env({"LAFUFU_TRIGGER_EMOTION": "drunk"})
        assert cfg.emotion == "drunk"

    @pytest.mark.parametrize(
        "emotion", ["happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"]
    )
    def test_valid_emotions_accepted(self, emotion: str) -> None:
        cfg = TriggerConfig.from_env({"LAFUFU_TRIGGER_EMOTION": emotion})
        assert cfg.emotion == emotion
