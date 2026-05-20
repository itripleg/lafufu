import pytest
from lafufu_shared import schemas
from pydantic import ValidationError


def test_agent_reply_valid():
    r = schemas.AgentReply(text="hello", emotion="happy")
    assert r.text == "hello"
    assert r.emotion == "happy"


def test_agent_reply_invalid_emotion():
    with pytest.raises(ValidationError):
        schemas.AgentReply(text="hi", emotion="confused")


def test_animator_pose_round_trip():
    p = schemas.AnimatorPose(head_lr=2063, head_ud=3082, eye=2045, jaw=1728, brow=2075)
    j = p.model_dump_json()
    p2 = schemas.AnimatorPose.model_validate_json(j)
    assert p2 == p


def test_animator_intent_preview_validates_servo_name():
    schemas.AnimatorIntentPreview(name="jaw", position=1700)
    with pytest.raises(ValidationError):
        schemas.AnimatorIntentPreview(name="elbow", position=0)


def test_agent_tts_rms_required_fields():
    r = schemas.AgentTtsRms(ts=0.1, rms=0.4, mouth_target=0.6)
    assert 0 <= r.mouth_target <= 1


def test_system_heartbeat_has_service():
    h = schemas.SystemHeartbeat(service="agent", ts=1.0, uptime_s=10.0)
    assert h.service == "agent"


# ─── Input bounds (defense in depth + DoS protection) ──────────────────────


def test_agent_text_message_caps_length():
    """A 10MB text message would OOM Ollama on a Pi. Schema should reject."""
    schemas.AgentIntentTextMessage(text="hello")  # ok
    with pytest.raises(ValidationError):
        schemas.AgentIntentTextMessage(text="x" * 10_000_000)


def test_agent_speak_text_caps_length():
    schemas.AgentIntentSpeakText(text="ok")  # ok
    with pytest.raises(ValidationError):
        schemas.AgentIntentSpeakText(text="x" * 10_000_000)


def test_printer_intent_print_text_caps_length():
    schemas.PrinterIntentPrintText(text="ok")  # ok
    with pytest.raises(ValidationError):
        schemas.PrinterIntentPrintText(text="x" * 10_000_000)


def test_animator_intent_preview_bounds():
    """DXL positions are 0..4095. Out-of-band values were accepted unbounded
    and only clamped downstream; defense-in-depth bounds catch them earlier."""
    schemas.AnimatorIntentPreview(name="jaw", position=2000)  # ok
    with pytest.raises(ValidationError):
        schemas.AnimatorIntentPreview(name="jaw", position=-1)
    with pytest.raises(ValidationError):
        schemas.AnimatorIntentPreview(name="jaw", position=10_000)


def test_animator_pose_bounds():
    schemas.AnimatorPose(head_lr=2063, head_ud=3082, eye=2045, jaw=1728, brow=2075)  # ok
    with pytest.raises(ValidationError):
        schemas.AnimatorPose(head_lr=-5, head_ud=3082, eye=2045, jaw=1728, brow=2075)
    with pytest.raises(ValidationError):
        schemas.AnimatorPose(head_lr=2063, head_ud=99999, eye=2045, jaw=1728, brow=2075)


def test_agent_reply_source_system_is_valid():
    r = schemas.AgentReply(text="hi", emotion="neutral", source="system")
    assert r.source == "system"


def test_agent_reply_source_rejects_unknown():
    with pytest.raises(ValidationError):
        schemas.AgentReply(text="hi", emotion="neutral", source="bogus")
