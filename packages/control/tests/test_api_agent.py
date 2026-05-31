"""Tests for /api/agent/* endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
    return TestClient(app)


def test_stt_backends_endpoint_returns_list(client):
    """GET /api/agent/stt_backends returns the available STT backends."""
    r = client.get("/api/agent/stt_backends")
    assert r.status_code == 200
    body = r.json()
    assert "backends" in body
    ids = {b["id"] for b in body["backends"]}
    assert "openai-whisper" in ids
    assert "faster-whisper" in ids


def test_voices_endpoint_lists_onnx_files(client, tmp_path, monkeypatch):
    """GET /api/agent/voices lists .onnx files in LAFUFU_MODELS_DIR and reports
    whether each has a companion .onnx.json (Piper needs both to load)."""
    monkeypatch.setenv("LAFUFU_MODELS_DIR", str(tmp_path))

    # voice_a: full pair — has_config True + sample_rate parsed from json
    (tmp_path / "voice_a.onnx").write_bytes(b"\x00" * 32)
    (tmp_path / "voice_a.onnx.json").write_text(
        '{"audio": {"sample_rate": 22050}}', encoding="utf-8"
    )
    # voice_b: .onnx only — has_config False, sample_rate None
    (tmp_path / "voice_b.onnx").write_bytes(b"\x00" * 16)
    # non-onnx ignored
    (tmp_path / "readme.txt").write_text("ignore me")

    r = client.get("/api/agent/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    by_name = {v["name"]: v for v in voices}
    assert set(by_name) == {"voice_a", "voice_b"}
    assert by_name["voice_a"]["has_config"] is True
    assert by_name["voice_a"]["sample_rate"] == 22050
    assert by_name["voice_a"]["size_bytes"] == 32
    assert by_name["voice_b"]["has_config"] is False
    assert by_name["voice_b"]["sample_rate"] is None


def test_voices_endpoint_empty_when_dir_missing(client, tmp_path, monkeypatch):
    """GET /api/agent/voices returns [] when LAFUFU_MODELS_DIR doesn't exist."""
    monkeypatch.setenv("LAFUFU_MODELS_DIR", str(tmp_path / "does-not-exist"))
    r = client.get("/api/agent/voices")
    assert r.status_code == 200
    assert r.json() == {"voices": []}


def test_voices_endpoint_survives_malformed_config(client, tmp_path, monkeypatch):
    """A malformed .onnx.json must not break the listing — sample_rate is None."""
    monkeypatch.setenv("LAFUFU_MODELS_DIR", str(tmp_path))
    (tmp_path / "voice_x.onnx").write_bytes(b"\x00")
    (tmp_path / "voice_x.onnx.json").write_text("{ not json", encoding="utf-8")

    r = client.get("/api/agent/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert len(voices) == 1
    assert voices[0]["name"] == "voice_x"
    assert voices[0]["has_config"] is True  # file exists, just unparseable
    assert voices[0]["sample_rate"] is None


def _make_client():
    app = create_app(engine=MagicMock(), nats_publish=lambda *a, **kw: None, api_token="")
    return TestClient(app)


def test_input_devices_returns_auto_first():
    """`auto` sentinel is the first entry so operators always see it as default."""
    fake_p = MagicMock()
    fake_p.get_device_count.return_value = 0
    with patch("lafufu_control.api.routers.agent.get_pyaudio", return_value=fake_p):
        r = _make_client().get("/api/agent/input-devices")
    assert r.status_code == 200
    devices = r.json()["devices"]
    assert devices[0]["name"] == "auto"
    assert "system default" in devices[0]["label"].lower()


def test_input_devices_enumerates_pyaudio():
    """Real-shape devices show up with numeric index strings as `name`."""
    fake_p = MagicMock()
    fake_p.get_device_count.return_value = 3
    fake_p.get_device_info_by_index.side_effect = [
        {"index": 0, "maxInputChannels": 2, "name": "Microphone Array"},
        {"index": 1, "maxInputChannels": 0, "name": "Speakers"},  # output, skipped
        {"index": 2, "maxInputChannels": 1, "name": "USB Mic"},
    ]
    with patch("lafufu_control.api.routers.agent.get_pyaudio", return_value=fake_p):
        r = _make_client().get("/api/agent/input-devices")
    assert r.status_code == 200
    devices = r.json()["devices"]
    # auto + 2 inputs (index 1 is output-only, skipped)
    assert [d["name"] for d in devices] == ["auto", "0", "2"]
    assert devices[1]["label"] == "Microphone Array"
    assert devices[2]["label"] == "USB Mic"


def test_output_devices_auto_first_names_first_non_hdmi():
    """`auto` is first and its label names the first NON-HDMI card it resolves
    to — HDMI cards are listed (operator may pick one) but flagged, never auto."""
    with patch(
        "lafufu_control.api.routers.agent.list_output_cards",
        return_value=["vc4hdmi0", "vc4hdmi1", "USB"],
    ):
        r = _make_client().get("/api/agent/output-devices")
    assert r.status_code == 200
    devices = r.json()["devices"]
    assert [d["name"] for d in devices] == ["auto", "vc4hdmi0", "vc4hdmi1", "USB"]
    # auto resolves to the first non-HDMI card (USB), never an HDMI card.
    assert "USB" in devices[0]["label"]
    assert "vc4hdmi" not in devices[0]["label"]
    # HDMI entries are flagged so the operator knows what they're picking.
    hdmi = next(d for d in devices if d["name"] == "vc4hdmi0")
    assert "hdmi" in hdmi["label"].lower()


def test_output_devices_auto_never_claims_hdmi_when_only_hdmi():
    """With only HDMI cards present, `auto` must NOT name an HDMI card as its
    target (the 'never auto-HDMI' guarantee)."""
    with patch(
        "lafufu_control.api.routers.agent.list_output_cards",
        return_value=["vc4hdmi0", "vc4hdmi1"],
    ):
        r = _make_client().get("/api/agent/output-devices")
    devices = r.json()["devices"]
    assert devices[0]["name"] == "auto"
    assert "vc4hdmi" not in devices[0]["label"]


def test_text_message_rejects_overlong_text(client):
    """POST /api/agent/text_message with text > 2000 chars must return 422."""
    r = client.post("/api/agent/text_message", json={"text": "a" * 2001})
    assert r.status_code == 422


def test_speak_text_rejects_overlong_text(client):
    """POST /api/agent/speak_text with text > 2000 chars must return 422."""
    r = client.post("/api/agent/speak_text", json={"text": "a" * 2001})
    assert r.status_code == 422
