"""Tests for /api/agent/* endpoints."""

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
