from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client_factory(tmp_path):
    def make():
        engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
        init_db(engine)
        published: list[tuple[str, dict]] = []
        client = TestClient(
            create_app(
                engine=engine,
                nats_publish=lambda s, p: published.append((s, p)),
            )
        )
        return client, published

    return make


def test_restart_known_service(client_factory):
    client, published = client_factory()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stderr = b""
        r = client.post("/api/system/services/agent/restart")
    assert r.status_code == 200
    called_args = run_mock.call_args[0][0]
    assert called_args == ["systemctl", "restart", "lafufu-agent"]
    assert any(s == "system.service.restarting" for s, _ in published)


def test_restart_unknown_service_400(client_factory):
    client, _ = client_factory()
    r = client.post("/api/system/services/notreal/restart")
    assert r.status_code == 400


def test_animator_preview_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/animator/preview", json={"name": "jaw", "position": 1700})
    assert r.status_code == 202
    assert any(s == "animator.intent.preview" for s, _ in published)


def test_animator_expression_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/animator/expression", json={"name": "happy"})
    assert r.status_code == 202
    assert any(s == "animator.intent.play_expression" for s, _ in published)


def test_agent_text_message_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/agent/text_message", json={"text": "hello"})
    assert r.status_code == 202
    assert any(s == "agent.intent.text_message" for s, _ in published)
