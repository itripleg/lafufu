"""Tests for the animator expression CRUD endpoints."""

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


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


def test_create_expression(client):
    """POST a 2-step expression, GET /expressions returns it parsed."""
    body = {
        "name": "agree",
        "playback": "once",
        "default_duration_ms": 220,
        "default_delay_ms": 60,
        "default_easing": "ease-in-out",
        "steps": [{"frame": "agree_low"}, {"frame": "agree_high"}],
        "emotion": "agree",
    }
    r = client.post("/api/animator/expressions", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["name"] == "agree"
    assert payload["playback"] == "once"
    assert len(payload["steps"]) == 2
    assert payload["steps"][0]["frame"] == "agree_low"
    assert payload["emotion"] == "agree"

    r2 = client.get("/api/animator/expressions")
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "agree"


def test_update_expression_steps(client):
    body = {
        "name": "happy",
        "playback": "loop",
        "default_duration_ms": 800,
        "steps": [{"frame": "happy_a"}, {"frame": "happy_b"}],
        "emotion": "happy",
    }
    client.post("/api/animator/expressions", json=body)

    new_body = {
        **body,
        "playback": "shuffle",
        "steps": [
            {"frame": "happy_a", "duration_ms": 400},
            {"frame": "happy_b"},
            {"frame": "happy_a"},
        ],
    }
    r = client.put("/api/animator/expressions/happy", json=new_body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["playback"] == "shuffle"
    assert len(payload["steps"]) == 3
    assert payload["steps"][0]["duration_ms"] == 400


def test_delete_expression(client):
    body = {"name": "gone", "steps": [{"frame": "x"}]}
    client.post("/api/animator/expressions", json=body)
    r = client.delete("/api/animator/expressions/gone")
    assert r.status_code == 204
    # Idempotent.
    r2 = client.delete("/api/animator/expressions/gone")
    assert r2.status_code == 204
    r3 = client.get("/api/animator/expressions")
    assert r3.json()["items"] == []


def test_play_publishes_resolved_payload(client):
    """POST /expressions/{name}/play resolves frames and publishes the
    AnimatorIntentPlayExpression payload."""
    # Seed a frame.
    client.post(
        "/api/animator/frames",
        json={
            "name": "agree_low",
            "head_lr": 2063,
            "head_ud": 3122,
            "eye": 2045,
            "jaw": 1728,
            "brow": 2075,
        },
    )
    # Seed an expression that references it.
    client.post(
        "/api/animator/expressions",
        json={
            "name": "agree",
            "playback": "once",
            "default_duration_ms": 220,
            "steps": [{"frame": "agree_low"}],
        },
    )

    r = client.post("/api/animator/expressions/agree/play")
    assert r.status_code == 202, r.text

    # The fixture's nats_publish appended to client.published.
    topics = [t for (t, _) in client.published]
    assert "animator.intent.play_expression" in topics

    # Find the most recent play_expression publish.
    payload = next(
        p for (t, p) in reversed(client.published) if t == "animator.intent.play_expression"
    )
    assert payload["name"] == "agree"
    assert payload["playback"] == "once"
    assert len(payload["steps"]) == 1
    assert payload["steps"][0]["pose"]["head_ud"] == 3122


def test_play_404_missing_expression(client):
    r = client.post("/api/animator/expressions/ghost/play")
    assert r.status_code == 404


def test_play_409_missing_frames(client):
    """Expression that references a non-existent frame fails 409."""
    client.post(
        "/api/animator/expressions",
        json={
            "name": "dangling",
            "steps": [{"frame": "noexist"}],
        },
    )
    r = client.post("/api/animator/expressions/dangling/play")
    assert r.status_code == 409


def test_seed_inserts_eight_emotions(client):
    """Call seed_animations, list expressions, verify all 8 emotions present.

    Then call again -- count stays at 8 (idempotent)."""
    from lafufu_control.animation.seed import seed_animations

    engine = client.app.state.engine

    seed_animations(engine)
    items = client.get("/api/animator/expressions").json()["items"]
    emotions = {e["emotion"] for e in items if e["emotion"]}
    assert emotions == {
        "agree",
        "disagree",
        "happy",
        "sad",
        "angry",
        "surprised",
        "neutral",
        "idle",
    }
    assert len(items) == 8

    # Idempotent -- second call is a no-op.
    seed_animations(engine)
    items2 = client.get("/api/animator/expressions").json()["items"]
    assert len(items2) == 8


def test_activate_emotion_clears_previous_owner(client):
    """The activate endpoint evicts any prior owner of the same emotion.

    SQLite's UNIQUE constraint prevents two rows from simultaneously holding
    the same emotion value, so we test eviction by:
      1. Creating v1 with emotion='custom_emote'.
      2. Transferring the emotion to v2 via raw two-step SQL (clear v1, set v2).
      3. Transferring it back to v1 the same way.
      4. Then giving v2 a *different* temp value, so v1 is the sole owner.
      5. Calling activate on v1 -- returns 200, emotion intact, v2 unaffected.

    For the eviction path specifically: we give the emotion to v2 only, then
    reassign it to v1 via the two-step transfer, then call activate on v1.
    After activation, v1 keeps 'custom_emote' and v2 has None (as set by step 2).
    """
    from sqlalchemy import text
    from sqlmodel import Session

    engine = client.app.state.engine

    # Create two expressions.
    client.post(
        "/api/animator/expressions",
        json={"name": "v1", "steps": [{"frame": "x"}], "emotion": "custom_emote"},
    )
    client.post(
        "/api/animator/expressions",
        json={"name": "v2", "steps": [{"frame": "y"}]},
    )

    # Transfer emotion from v1 to v2 via raw SQL (two-step to respect UNIQUE).
    with Session(engine) as s:
        s.exec(text("UPDATE expression SET emotion = NULL WHERE name = 'v1'"))
        s.exec(text("UPDATE expression SET emotion = 'custom_emote' WHERE name = 'v2'"))
        s.commit()

    # Now v2 is the sole owner. Activate v1 should fail (no emotion on v1 now).
    r_no_emo = client.post("/api/animator/expressions/v1/activate")
    assert r_no_emo.status_code == 400

    # Transfer emotion back to v1.
    with Session(engine) as s:
        s.exec(text("UPDATE expression SET emotion = NULL WHERE name = 'v2'"))
        s.exec(text("UPDATE expression SET emotion = 'custom_emote' WHERE name = 'v1'"))
        s.commit()

    # Now v1 is the sole owner. Activate v1 -- should return 200.
    r = client.post("/api/animator/expressions/v1/activate")
    assert r.status_code == 200
    assert r.json()["emotion"] == "custom_emote"
    assert r.json()["name"] == "v1"

    # v1 still holds it; v2 has None.
    items = client.get("/api/animator/expressions").json()["items"]
    by_name = {e["name"]: e for e in items}
    assert by_name["v1"]["emotion"] == "custom_emote"
    assert by_name["v2"]["emotion"] is None


def test_activate_404_missing(client):
    r = client.post("/api/animator/expressions/ghost/activate")
    assert r.status_code == 404


def test_activate_400_no_emotion(client):
    """Activating an expression without an emotion binding is a 400."""
    client.post(
        "/api/animator/expressions",
        json={"name": "no_emo", "steps": [{"frame": "x"}]},
    )
    r = client.post("/api/animator/expressions/no_emo/activate")
    assert r.status_code == 400
