"""STT protocol + backend selector tests."""

import pytest
from lafufu_agent.stt import (
    available_backends,
    make_stt,
)


def test_available_backends_returns_known_ids():
    avail = available_backends()
    ids = {b["id"] for b in avail}
    assert "openai-whisper" in ids
    assert "faster-whisper" in ids
    for b in avail:
        assert set(b.keys()) >= {"id", "label", "available"}
        assert isinstance(b["available"], bool)


def test_make_stt_unknown_backend_falls_back_to_openai_whisper():
    stt = make_stt("nonsense-backend", model_name="tiny")
    assert stt.backend_id == "openai-whisper"


def test_make_stt_respects_explicit_backend():
    if not any(b["id"] == "faster-whisper" and b["available"] for b in available_backends()):
        pytest.skip("faster-whisper not installed")
    stt = make_stt("faster-whisper", model_name="tiny.en")
    assert stt.backend_id == "faster-whisper"


def test_stt_protocol_methods_exist():
    stt = make_stt("openai-whisper", model_name="tiny")
    assert hasattr(stt, "load")
    assert hasattr(stt, "warmup")
    assert hasattr(stt, "transcribe")
    import inspect

    sig = inspect.signature(stt.transcribe)
    assert "audio" in sig.parameters
