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


def test_snapshot_excludes_bootstrap_internal_keys(client_with_engine):
    """The /api/state/snapshot payload must hide internal bookkeeping rows too
    — not just the settings CRUD API. Otherwise the migration flag leaks to the
    browser via the seed snapshot even though GET /api/settings hides it."""
    c, engine = client_with_engine
    _insert(engine, "agent.silence_threshold", "1500", "int", "silence threshold ms")
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.get("/api/state/snapshot")
    assert r.status_code == 200
    keys = [row["key"] for row in r.json()["settings"]]
    assert "agent.silence_threshold" in keys
    assert "bootstrap.migrations.wakeword_lafufu_v1" not in keys


def test_get_setting_404s_for_bootstrap_internal_key(client_with_engine):
    c, engine = client_with_engine
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.get("/api/settings/bootstrap.migrations.wakeword_lafufu_v1")
    assert r.status_code == 404


def test_patch_setting_404s_for_bootstrap_internal_key(client_with_engine):
    """PATCH parity with PUT — the iter-4 coverage left PATCH only indirectly
    tested via the 422-vs-404 leak lock; pin the happy-path 404 + row-untouched."""
    c, engine = client_with_engine
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")
    r = c.patch(
        "/api/settings/bootstrap.migrations.wakeword_lafufu_v1",
        json={"value": "2"},
    )
    assert r.status_code == 404
    with Session(engine) as s:
        row = s.get(Setting, "bootstrap.migrations.wakeword_lafufu_v1")
        assert row is not None and row.value == "1"


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


def test_put_internal_key_does_not_leak_via_404_vs_422_split(client):
    """Lock the response shape for internal `bootstrap.*` keys so the
    422-vs-404 differential a prober could exploit stays closed.

    A PUT with a body that FAILS Pydantic validation must NOT return 422 for
    an internal key while returning 404 for a non-existent non-internal key
    (or vice versa). If those differed, a prober could send garbage to
    `/api/settings/bootstrap.xxx`, observe 422, and infer the prefix is
    real-but-protected.

    The current design intentionally lets FastAPI parse the body first (so
    422 fires for malformed input regardless of key existence), then returns
    404 only for valid bodies aimed at unknown/internal keys. Both an
    internal key and an unknown non-internal key produce 422 for invalid
    bodies — so the prober can't distinguish them via the 422-vs-404 split.

    This test pins that behavior so a future refactor that accidentally
    reorders the existence check before body validation gets caught."""
    invalid_bodies = [
        "not-an-object",  # not a JSON object
        '"a string"',  # JSON string, not an object
        "123",  # JSON number
        "[]",  # JSON array, not an object
    ]
    for raw in invalid_bodies:
        r_internal = client.put(
            "/api/settings/bootstrap.does_not_exist",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        r_unknown = client.put(
            "/api/settings/agent.does_not_exist",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert r_internal.status_code == r_unknown.status_code, (
            f"PUT 422-vs-404 leak for body {raw!r}: "
            f"internal={r_internal.status_code} unknown={r_unknown.status_code}"
        )
        r_internal_patch = client.patch(
            "/api/settings/bootstrap.does_not_exist",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        r_unknown_patch = client.patch(
            "/api/settings/agent.does_not_exist",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert r_internal_patch.status_code == r_unknown_patch.status_code, (
            f"PATCH 422-vs-404 leak for body {raw!r}: "
            f"internal={r_internal_patch.status_code} "
            f"unknown={r_unknown_patch.status_code}"
        )
