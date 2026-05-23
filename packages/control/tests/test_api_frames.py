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


def test_delete_frame_refuses_when_referenced(client):
    """A frame referenced by any expression can't be deleted — otherwise
    /play would 409 on the orphan reference."""
    client.post("/api/animator/frames", json={"name": "used_a", **_pose()})
    client.post(
        "/api/animator/expressions",
        json={"name": "uses_a", "steps": [{"frame": "used_a"}]},
    )
    r = client.delete("/api/animator/frames/used_a")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error_code"] == "frame_in_use"
    assert "uses_a" in detail["referenced_by"]

    # After the expression is gone, the frame is deletable again.
    client.delete("/api/animator/expressions/uses_a")
    r2 = client.delete("/api/animator/frames/used_a")
    assert r2.status_code == 204


def test_snapshot_current_pose(client):
    """POST /frames/{name}/snapshot upserts using app.state.last_pose."""
    # Tests can poke app.state directly via the underlying app.
    client.app.state.last_pose = _pose(head_lr=2150)

    r = client.post("/api/animator/frames/from_snap/snapshot")
    assert r.status_code == 200
    assert r.json()["name"] == "from_snap"

    # The frame now exists with the snapshot pose.
    r2 = client.get("/api/animator/frames")
    items = {f["name"]: f for f in r2.json()["items"]}
    assert "from_snap" in items
    assert items["from_snap"]["head_lr"] == 2150


def test_snapshot_409_when_no_pose(client):
    """Without a live pose set, snapshot 409s instead of writing garbage."""
    # ensure attribute is unset
    if hasattr(client.app.state, "last_pose"):
        delattr(client.app.state, "last_pose")
    r = client.post("/api/animator/frames/empty/snapshot")
    assert r.status_code == 409
