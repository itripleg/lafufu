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
