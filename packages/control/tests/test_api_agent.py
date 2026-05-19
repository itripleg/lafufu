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
