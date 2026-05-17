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
