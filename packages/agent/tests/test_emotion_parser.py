from lafufu_agent.emotion_parser import parse


def test_known_emotion_extracted():
    emotion, body = parse("[happy] hello world")
    assert emotion == "happy"
    assert body == "hello world"


def test_unknown_emotion_passes_through_verbatim():
    """The DB lookup downstream is the validity check. Parser should NOT
    default to 'neutral' anymore — that hid typos and silently masked
    missing expression registrations."""
    emotion, body = parse("[zzz_unknown] some text")
    assert emotion == "zzz_unknown"
    assert body == "some text"


def test_no_tag_returns_empty_emotion():
    emotion, body = parse("just text with no tag")
    assert emotion == ""
    assert body == "just text with no tag"


def test_alternate_delimiters_still_extracted():
    assert parse("(disagree) nope")[0] == "disagree"
    assert parse("*sad* aww")[0] == "sad"


def test_emotion_label_prefix():
    assert parse("emotion: angry rage")[0] == "angry"
