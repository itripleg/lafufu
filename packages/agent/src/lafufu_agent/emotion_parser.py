"""Parse LLM replies of the form `[emotion] body text`."""

import re
from typing import get_args

from lafufu_shared.schemas import Emotion

_VALID_EMOTIONS: set[str] = set(get_args(Emotion))

# Small models are inconsistent about the emotion tag: they drop the brackets,
# swap them for parens or markdown asterisks, or prefix an "emotion:" label.
# Any form we fail to strip here gets read aloud by TTS, so match them all.
_DELIMITED_RE = re.compile(
    r"^\s*[\[(*]+\s*(?:emotion\s*[:=]\s*)?([a-zA-Z]+)\s*[\])*]+\s*\n?",
    re.IGNORECASE,
)
_BARE_RE = re.compile(
    r"^\s*(?:emotion\s*[:=]\s*)?([a-zA-Z]+)\s*\n+",
    re.IGNORECASE,
)


def parse(reply: str) -> tuple[str, str]:
    """Return (emotion, body). Unknown/missing tags → 'neutral'.

    Tolerates `[happy]`, `(happy)`, `*happy*`, `emotion: happy`, or a bare
    `happy` on its own first line so the tag never leaks into spoken TTS.
    """
    m = _DELIMITED_RE.match(reply)
    if m:
        tag = m.group(1).lower()
        body = reply[m.end() :].strip()
        return (tag if tag in _VALID_EMOTIONS else "neutral"), body

    # Without delimiters, only strip a leading word when it is itself a valid
    # emotion — otherwise we'd swallow the first word of an ordinary sentence.
    m = _BARE_RE.match(reply)
    if m and m.group(1).lower() in _VALID_EMOTIONS:
        return m.group(1).lower(), reply[m.end() :].strip()

    return "neutral", reply.strip()
