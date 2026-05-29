import pytest
from fastapi.testclient import TestClient
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app)


def test_reset_expression_restores_factory_steps(client):
    r = client.put(
        "/api/animator/expressions/happy",
        json={
            "playback": "once",
            "default_duration_ms": 9999,
            "default_delay_ms": 9999,
            "default_easing": "linear",
            "steps": [],
            "random_walk_config": None,
            "emotion": "happy",
            "description": "edited",
        },
    )
    assert r.status_code == 200
    assert r.json()["default_duration_ms"] == 9999

    r = client.post("/api/animator/expressions/happy/reset")
    assert r.status_code == 200
    body = r.json()
    # Built-in happy seeds with default_duration_ms=150 (see seed.py)
    assert body["default_duration_ms"] == 150
    assert body["playback"] == "loop"


def test_reset_frame_restores_factory_pose(client):
    r = client.put(
        "/api/animator/frames/idle_calm",
        json={
            "head_lr": 0,
            "head_ud": 0,
            "eye": 0,
            "jaw": 0,
            "brow": 0,
        },
    )
    assert r.status_code == 200
    r = client.post("/api/animator/frames/idle_calm/reset")
    assert r.status_code == 200
    # IDLE is {"head_lr": 2063, ...} in seed.py — reset should bring head_lr back
    assert r.json()["head_lr"] == 2063


def test_reset_expression_rejects_non_builtin(client):
    r = client.post("/api/animator/expressions", json={"name": "user_one", "steps": []})
    assert r.status_code == 200
    r = client.post("/api/animator/expressions/user_one/reset")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "not_builtin"


def test_reset_frame_rejects_non_builtin(client):
    r = client.post(
        "/api/animator/frames",
        json={
            "name": "user_frame",
            "head_lr": 0,
            "head_ud": 0,
            "eye": 0,
            "jaw": 0,
            "brow": 0,
        },
    )
    assert r.status_code == 200
    r = client.post("/api/animator/frames/user_frame/reset")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "not_builtin"


def test_reset_expression_missing_returns_404(client):
    r = client.post("/api/animator/expressions/zzz_nope/reset")
    assert r.status_code == 404


def test_reset_frame_missing_returns_404(client):
    r = client.post("/api/animator/frames/zzz_nope/reset")
    assert r.status_code == 404
