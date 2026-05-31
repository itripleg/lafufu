"""Guard the two built-in prompt presets.

Both must keep the leading ``[emotion]`` tag contract — the agent's
emotion_parser strips a single square-bracket tag before the reply, and the
animator drives the face from it. A preset that drops the tag instruction
would make every reply render as 'neutral' with the tag spoken aloud.
"""

from lafufu_shared import prompts

_TAGS = ["[happy]", "[sad]", "[angry]", "[surprised]", "[neutral]", "[agree]", "[disagree]"]


def test_both_presets_are_nonempty():
    assert prompts.DEFAULT_SYSTEM_PROMPT.strip()
    assert prompts.FORTUNE_TELLER_PROMPT.strip()


def test_both_presets_keep_the_emotion_tag_contract():
    for prompt in (prompts.DEFAULT_SYSTEM_PROMPT, prompts.FORTUNE_TELLER_PROMPT):
        assert "square bracket" in prompt
        # Every valid emotion tag is enumerated so the model knows the set.
        for tag in _TAGS:
            assert tag in prompt


def test_street_oracle_default_drops_the_mic_noise_paragraph():
    # The noisy-room guidance was removed from the canonical Street Oracle
    # default; restore-to-default must not reintroduce it.
    assert "noisy room" not in prompts.DEFAULT_SYSTEM_PROMPT


def test_fortune_teller_is_distinct_from_street_oracle():
    assert prompts.FORTUNE_TELLER_PROMPT != prompts.DEFAULT_SYSTEM_PROMPT
    assert "fortune" in prompts.FORTUNE_TELLER_PROMPT.lower()
