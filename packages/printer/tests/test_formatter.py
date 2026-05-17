from datetime import datetime

from lafufu_printer.formatter import format_reply, format_transcript


def test_format_reply_includes_text_and_emotion():
    out = format_reply(text="Hello world", emotion="happy", ts=datetime(2026, 5, 17, 14, 30))
    assert "Hello world" in out
    assert "happy" in out.lower()
    assert "2026" in out


def test_format_reply_strips_trailing_whitespace_per_line():
    out = format_reply(text="Hi    \nWorld   ", emotion="neutral", ts=datetime.now())
    assert "    " not in out  # no trailing 4-space runs


def test_format_transcript_includes_roles():
    out = format_transcript(
        [
            {"role": "user", "text": "Are you alive?"},
            {"role": "assistant", "text": "Mostly!"},
        ]
    )
    assert "user" in out.lower()
    assert "assistant" in out.lower()
    assert "Are you alive?" in out
    assert "Mostly!" in out


def test_format_reply_truncates_extreme_length():
    long = "x" * 5000
    out = format_reply(text=long, emotion="neutral", ts=datetime.now())
    assert len(out) < 4000  # printer doesn't need a novel
