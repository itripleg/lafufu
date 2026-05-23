"""Tests for the animator frame CRUD endpoints."""

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


# Idle defaults — handy for building plausible frame bodies in tests.
def _pose(**over):
    base = {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075}
    base.update(over)
    return base


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(tmp_path / "printer"))
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    c.published = published  # type: ignore[attr-defined]
    return c


def test_create_and_list_frame(client):
    """POST a frame, then GET /frames returns it with the right pose."""
    body = {"name": "agree_low", **_pose(head_ud=3122)}
    r = client.post("/api/animator/frames", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["name"] == "agree_low"
    assert payload["head_ud"] == 3122

    r2 = client.get("/api/animator/frames")
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "agree_low"


def test_update_frame(client):
    body = {"name": "look_up", **_pose()}
    client.post("/api/animator/frames", json=body)

    new_body = {**_pose(head_ud=3052), "image": "sprites/upload/foo.png"}
    r = client.put("/api/animator/frames/look_up", json=new_body)
    assert r.status_code == 200
    assert r.json()["head_ud"] == 3052
    assert r.json()["image"] == "sprites/upload/foo.png"


def test_delete_frame(client):
    body = {"name": "gone", **_pose()}
    client.post("/api/animator/frames", json=body)
    r = client.delete("/api/animator/frames/gone")
    assert r.status_code == 204
    # Idempotent — second delete also 204.
    r2 = client.delete("/api/animator/frames/gone")
    assert r2.status_code == 204
    # And list is empty.
    r3 = client.get("/api/animator/frames")
    assert r3.json()["items"] == []
