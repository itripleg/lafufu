"""Format Lafufu replies / transcripts for the thermal printer."""

from datetime import datetime

_MAX_BODY_CHARS = 2000
_SEPARATOR = "-" * 30


def _strip_trailing_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def format_reply(*, text: str, emotion: str, ts: datetime) -> str:
    """One reply, with timestamp + emotion header."""
    body = _strip_trailing_ws(text)[:_MAX_BODY_CHARS]
    header = f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [{emotion}]"
    return f"{header}\n{_SEPARATOR}\n{body}\n{_SEPARATOR}\n\n"


def format_transcript(entries: list[dict[str, str]]) -> str:
    """Multi-turn transcript dump."""
    lines: list[str] = ["LAFUFU TRANSCRIPT", _SEPARATOR]
    for e in entries:
        role = e.get("role", "?")
        text = _strip_trailing_ws(e.get("text", ""))
        lines.append(f"{role.upper()}: {text}")
    lines.append(_SEPARATOR)
    return "\n".join(lines) + "\n\n"
