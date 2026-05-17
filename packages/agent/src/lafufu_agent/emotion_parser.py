"""Parse LLM replies of the form `[emotion]\nbody text`."""

import re
from typing import get_args

from lafufu_shared.schemas import Emotion

_VALID_EMOTIONS: set[str] = set(get_args(Emotion))
_TAG_RE = re.compile(r"^\s*\[([a-zA-Z]+)\]\s*\n?", re.MULTILINE)


def parse(reply: str) -> tuple[str, str]:
    """Return (emotion, body). Unknown/missing tags → 'neutral'."""
    m = _TAG_RE.match(reply)
    if not m:
        return "neutral", reply.strip()
    tag = m.group(1).lower()
    body = reply[m.end() :].strip()
    if tag not in _VALID_EMOTIONS:
        return "neutral", body
    return tag, body
