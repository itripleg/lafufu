from lafufu_agent.emotion_parser import parse


def test_parses_emotion_tag_at_start():
    e, t = parse("[happy]\nHello world")
    assert e == "happy"
    assert t == "Hello world"


def test_parses_with_trailing_whitespace():
    e, t = parse("[surprised]   \nWhoa!")
    assert e == "surprised"
    assert t == "Whoa!"


def test_no_tag_returns_neutral():
    e, t = parse("Just text without a tag.")
    assert e == "neutral"
    assert t == "Just text without a tag."


def test_unknown_tag_returns_neutral():
    e, t = parse("[confused]\nHmm.")
    assert e == "neutral"
    # Tag is stripped even if not matched
    assert t == "Hmm."


def test_multiline_body_preserved():
    e, t = parse("[sad]\nLine one.\nLine two.")
    assert e == "sad"
    assert t == "Line one.\nLine two."


def test_case_insensitive_tag():
    e, _t = parse("[HAPPY]\nWoo")
    assert e == "happy"


def test_strips_surrounding_whitespace():
    e, t = parse("  [agree]\nyes  ")
    assert e == "agree"
    assert t == "yes"
