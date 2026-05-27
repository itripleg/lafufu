import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.models.setting import Setting
from sqlmodel import Session


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
    return TestClient(app)


@pytest.fixture
def client_with_engine(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t_internal.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda subject, payload: None)
    return TestClient(app), engine


def _insert(engine, key: str, value: str, value_type: str, description: str | None = None):
    with Session(engine) as s:
        s.add(Setting(key=key, value=value, value_type=value_type, description=description))
        s.commit()


def test_list_settings_empty(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == []


def test_create_setting(client):
    r = client.put("/api/settings/agent.tts.speed", json={"value": 0.85, "value_type": "float"})
    assert r.status_code == 200
    assert r.json()["key"] == "agent.tts.speed"
    assert r.json()["value"] == "0.85"


def test_patch_publishes_config_changed(client, tmp_path):
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t2.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    c.put("/api/settings/k", json={"value": "v1", "value_type": "str"})
    published.clear()
    r = c.patch("/api/settings/k", json={"value": "v2"})
    assert r.status_code == 200
    assert r.json()["value"] == "v2"
    assert len(published) == 1
    assert published[0][0].startswith("config.changed.k")
    assert published[0][1]["value"] == "v2"


def test_get_missing_404(client):
    r = client.get("/api/settings/missing")
    assert r.status_code == 404


def test_delete_setting(client):
    client.put("/api/settings/k", json={"value": "x", "value_type": "str"})
    r = client.delete("/api/settings/k")
    assert r.status_code == 204
    assert client.get("/api/settings/k").status_code == 404


def test_list_settings_excludes_bootstrap_internal_keys(client_with_engine):
    c, engine = client_with_engine
    _insert(engine, "agent.silence_threshold", "1500", "int", "silence threshold ms")
    _insert(
        engine,
        "bootstrap.migrations.wakeword_lafufu_v1",
        "1",
        "str",
        "migration bookkeeping",
    )
    r = c.get("/api/settings")
    assert r.status_code == 200
    keys = [row["key"] for row in r.json()]
    assert "agent.silence_threshold" in keys
    assert "bootstrap.migrations.wakeword_lafufu_v1" not in keys


def test_get_setting_404s_for_bootstrap_internal_key(client_with_engine):
    c, engine = client_with_engine
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.get("/api/settings/bootstrap.migrations.wakeword_lafufu_v1")
    assert r.status_code == 404


def test_delete_setting_404s_for_bootstrap_internal_key(client_with_engine):
    c, engine = client_with_engine
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.delete("/api/settings/bootstrap.migrations.wakeword_lafufu_v1")
    assert r.status_code == 404
    # row must still exist in DB (untouched)
    with Session(engine) as s:
        assert s.get(Setting, "bootstrap.migrations.wakeword_lafufu_v1") is not None


def test_put_setting_404s_for_bootstrap_internal_key(client_with_engine):
    c, engine = client_with_engine
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.put(
        "/api/settings/bootstrap.migrations.wakeword_lafufu_v1",
        json={"value": "2", "value_type": "str"},
    )
    assert r.status_code == 404
    # value untouched
    with Session(engine) as s:
        row = s.get(Setting, "bootstrap.migrations.wakeword_lafufu_v1")
        assert row is not None
        assert row.value == "1"
