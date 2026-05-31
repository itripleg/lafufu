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
    # tts.length_scale is a real key in BOOTSTRAP_DEFAULTS (float, default 0.95)
    r = client.put("/api/settings/tts.length_scale", json={"value": 0.85, "value_type": "float"})
    assert r.status_code == 200
    assert r.json()["key"] == "tts.length_scale"
    assert r.json()["value"] == "0.85"


def test_patch_publishes_config_changed(tmp_path):
    published: list[tuple[str, dict]] = []
    engine = create_engine_for_path(str(tmp_path / "t2.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: published.append((s, p)))
    c = TestClient(app)
    # Use a real key: speaker.volume (int, default 80)
    c.put("/api/settings/speaker.volume", json={"value": 80, "value_type": "int"})
    published.clear()
    r = c.patch("/api/settings/speaker.volume", json={"value": 75, "value_type": "int"})
    assert r.status_code == 200
    assert r.json()["value"] == "75"
    assert len(published) == 1
    assert published[0][0].startswith("config.changed.speaker.volume")
    assert published[0][1]["value"] == 75


def test_delete_unknown_key_returns_404(client_with_engine):
    """DELETE with a key that exists in the DB but is not in BOOTSTRAP_DEFAULTS must return 404.
    Without the fix, the row is silently deleted even though PUT/PATCH both reject it."""
    c, engine = client_with_engine
    _insert(engine, "totally.unknown.key", "value", "str")
    r = c.delete("/api/settings/totally.unknown.key")
    assert r.status_code == 404
    with Session(engine) as s:
        assert s.get(Setting, "totally.unknown.key") is not None, "row must be untouched"


def test_patch_unknown_key_returns_404(client_with_engine):
    """PATCH with a key that exists in the DB but is not in BOOTSTRAP_DEFAULTS must return 404.
    Without the fix, the row is updated and broadcast over NATS."""
    c, engine = client_with_engine
    _insert(engine, "totally.unknown.key", "old", "str")
    r = c.patch(
        "/api/settings/totally.unknown.key",
        json={"value": "new", "value_type": "str"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "not_found"
    with Session(engine) as s:
        row = s.get(Setting, "totally.unknown.key")
        assert row is not None
        assert row.value == "old"


def test_put_unknown_key_returns_404(client):
    """PUT with an unknown key (not in BOOTSTRAP_DEFAULTS) must return 404.
    Without the fix, arbitrary keys are silently persisted and broadcast over NATS."""
    r = client.put(
        "/api/settings/totally.unknown.key",
        json={"value": "oops", "value_type": "str"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "not_found"


def test_get_missing_404(client):
    r = client.get("/api/settings/missing")
    assert r.status_code == 404


def test_delete_setting(client):
    client.put("/api/settings/speaker.output_device", json={"value": "auto", "value_type": "str"})
    r = client.delete("/api/settings/speaker.output_device")
    assert r.status_code == 204
    assert client.get("/api/settings/speaker.output_device").status_code == 404


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


def test_put_rejects_unknown_value_type(client):
    r = client.put("/api/settings/tts.length_scale", json={"value": 0.85, "value_type": "jsonn"})
    assert r.status_code == 422, f"unknown value_type must be 422, got {r.status_code}"


def test_put_accepts_known_value_types(client):
    # Use one real key (tts.length_scale) and cycle through all value_types.
    # PUT is idempotent so each call overwrites the previous one — we just need
    # to confirm every Literal value_type is accepted (not 422).
    for vt in ("str", "int", "float", "bool", "json"):
        r = client.put("/api/settings/tts.length_scale", json={"value": "1", "value_type": vt})
        assert r.status_code == 200, f"{vt} should be accepted, got {r.status_code}"


def test_snapshot_excludes_bootstrap_internal_keys(client_with_engine):
    """The /api/state/snapshot payload must hide internal bookkeeping rows
    (bootstrap.*) just like the settings CRUD API — otherwise the migration
    flag row leaks to the browser via the seed snapshot even though
    GET /api/settings hides it."""
    c, engine = client_with_engine
    _insert(engine, "agent.silence_threshold", "1500", "int", "silence threshold ms")
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "migration bookkeeping")
    r = c.get("/api/state/snapshot")
    assert r.status_code == 200
    keys = [row["key"] for row in r.json()["settings"]]
    assert "agent.silence_threshold" in keys
    assert "bootstrap.migrations.wakeword_lafufu_v1" not in keys


async def test_rebroadcast_skips_bootstrap_internal_keys(tmp_path):
    """control._rebroadcast_all_settings must not publish config.changed for
    internal bootstrap.* rows — they'd otherwise cross the WS bridge into the
    browser config firehose even though the settings API + snapshot hide them."""
    from lafufu_control.service import ControlService

    engine = create_engine_for_path(str(tmp_path / "rb.sqlite"))
    init_db(engine)
    _insert(engine, "agent.silence_threshold", "1500", "int", "x")
    _insert(engine, "bootstrap.migrations.wakeword_lafufu_v1", "1", "str", "x")

    published: list[str] = []

    class _FakeNats:
        async def publish(self, subject, data):
            published.append(subject)

    svc = ControlService()
    svc.nats = _FakeNats()
    await svc._rebroadcast_all_settings(engine)

    assert "config.changed.agent.silence_threshold" in published
    assert not any("bootstrap.migrations" in s for s in published)
