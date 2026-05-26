from fastapi.testclient import TestClient
from lafufu_control.animation.seed import seed_animations
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


def _setup(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)
    published: list[tuple[str, dict]] = []
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    return TestClient(app), published


def test_create_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.post(
        "/api/animator/frames",
        json={
            "name": "new_frame",
            "head_lr": 1,
            "head_ud": 1,
            "eye": 1,
            "jaw": 1,
            "brow": 1,
        },
    )
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "create", "name": "new_frame"}) in pub


def test_update_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.put(
        "/api/animator/frames/idle_calm",
        json={
            "head_lr": 9,
            "head_ud": 9,
            "eye": 9,
            "jaw": 9,
            "brow": 9,
        },
    )
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "update", "name": "idle_calm"}) in pub


def test_delete_user_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    c.post(
        "/api/animator/frames",
        json={
            "name": "tmp",
            "head_lr": 0,
            "head_ud": 0,
            "eye": 0,
            "jaw": 0,
            "brow": 0,
        },
    )
    pub.clear()
    r = c.delete("/api/animator/frames/tmp")
    assert r.status_code == 204
    assert ("frames.changed", {"kind": "delete", "name": "tmp"}) in pub


def test_reset_frame_publishes_changed(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.post("/api/animator/frames/idle_calm/reset")
    assert r.status_code == 200
    assert ("frames.changed", {"kind": "reset", "name": "idle_calm"}) in pub


def test_expression_lifecycle_publishes(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    c.post("/api/animator/expressions", json={"name": "ex1", "steps": []})
    assert ("expressions.changed", {"kind": "create", "name": "ex1"}) in pub
    pub.clear()
    c.put(
        "/api/animator/expressions/ex1",
        json={
            "playback": "once",
            "default_duration_ms": 250,
            "default_delay_ms": 80,
            "default_easing": "ease-in-out",
            "steps": [],
            "random_walk_config": None,
            "emotion": None,
            "description": "x",
        },
    )
    assert ("expressions.changed", {"kind": "update", "name": "ex1"}) in pub
    pub.clear()
    c.delete("/api/animator/expressions/ex1")
    assert ("expressions.changed", {"kind": "delete", "name": "ex1"}) in pub
    pub.clear()
    c.post("/api/animator/expressions/happy/reset")
    assert ("expressions.changed", {"kind": "reset", "name": "happy"}) in pub


def test_delete_missing_does_not_publish(tmp_path):
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.delete("/api/animator/frames/zzz_never_existed")
    assert r.status_code in (204, 404)
    assert ("frames.changed", {"kind": "delete", "name": "zzz_never_existed"}) not in pub


def test_delete_builtin_frame_does_not_publish(tmp_path):
    """Rejecting deletion of a built-in must not publish frames.changed."""
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.delete("/api/animator/frames/idle_calm")
    assert r.status_code == 400
    assert not any(s == "frames.changed" for s, _ in pub)


def test_delete_builtin_expression_does_not_publish(tmp_path):
    """Rejecting deletion of a built-in expression must not publish expressions.changed."""
    c, pub = _setup(tmp_path)
    pub.clear()
    r = c.delete("/api/animator/expressions/happy")
    assert r.status_code == 400
    assert not any(s == "expressions.changed" for s, _ in pub)
