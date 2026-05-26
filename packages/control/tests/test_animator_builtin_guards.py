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


def test_delete_builtin_expression_rejected(client):
    r = client.delete("/api/animator/expressions/happy")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "is_builtin"


def test_delete_builtin_frame_rejected(client):
    r = client.delete("/api/animator/frames/idle_calm")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "is_builtin"


def test_delete_user_expression_ok(client):
    client.post("/api/animator/expressions", json={"name": "mine", "steps": []})
    r = client.delete("/api/animator/expressions/mine")
    assert r.status_code == 204


def test_list_expressions_includes_is_builtin(client):
    r = client.get("/api/animator/expressions")
    items = r.json()["items"]
    happy = next(x for x in items if x["name"] == "happy")
    assert happy["is_builtin"] is True
    client.post("/api/animator/expressions", json={"name": "user_two", "steps": []})
    r = client.get("/api/animator/expressions")
    user = next(x for x in r.json()["items"] if x["name"] == "user_two")
    assert user["is_builtin"] is False


def test_list_frames_includes_is_builtin(client):
    r = client.get("/api/animator/frames")
    items = r.json()["items"]
    idle = next(x for x in items if x["name"] == "idle_calm")
    assert idle["is_builtin"] is True
