"""Verify the control plane translates an agent.reply with a known emotion
into an animator.intent.play_expression payload, and ignores unknown ones.
Tests the pure resolver helper directly; the NATS publish side is exercised
via the manual end-to-end walkthrough."""

from lafufu_control.animation.seed import seed_animations
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.service import resolve_emotion_to_play_intent


def test_known_emotion_resolves_to_play_intent(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    payload = resolve_emotion_to_play_intent(engine, emotion="disagree")
    assert payload is not None
    assert payload["name"] == "disagree"
    assert "playback" in payload


def test_unknown_emotion_returns_none(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    payload = resolve_emotion_to_play_intent(engine, emotion="zzz_unknown")
    assert payload is None


def test_empty_emotion_returns_none(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    assert resolve_emotion_to_play_intent(engine, emotion="") is None
    assert resolve_emotion_to_play_intent(engine, emotion=None) is None
