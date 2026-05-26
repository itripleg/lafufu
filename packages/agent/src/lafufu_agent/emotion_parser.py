"""Parse LLM replies of the form `[emotion] body text`.

Validity of the emotion name is checked downstream against the expression
registry — this parser just extracts whatever name the model emitted.
"""

import re

# Small models are inconsistent about the emotion tag: they drop the brackets,
# swap them for parens or markdown asterisks, or prefix an "emotion:" label.
# Any form we fail to strip here gets read aloud by TTS, so match them all.
_DELIMITED_RE = re.compile(
    r"^\s*[\[(*]+\s*(?:emotion\s*[:=]\s*)?([a-zA-Z_]+)\s*[\])*]+\s*\n?",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"^\s*emotion\s*[:=]\s*([a-zA-Z_]+)\s*\n*",
    re.IGNORECASE,
)


def parse(reply: str) -> tuple[str, str]:
    """Return (emotion, body). When no tag is present, emotion is ''.

    Tolerates `[happy]`, `(happy)`, `*happy*`, `emotion: happy`. Does NOT
    strip a bare leading word — too risky to swallow the first word of an
    ordinary sentence without a delimiter.
    """
    m = _DELIMITED_RE.match(reply)
    if m:
        return m.group(1).lower(), reply[m.end() :].strip()
    m = _LABEL_RE.match(reply)
    if m:
        return m.group(1).lower(), reply[m.end() :].strip()
    return "", reply.strip()
