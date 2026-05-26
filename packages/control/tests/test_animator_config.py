import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client(tmp_path):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    app = create_app(engine=engine, nats_publish=lambda s, p: None)
    return TestClient(app)


def test_get_config_returns_ranges_and_defaults(client):
    r = client.get("/api/animator/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"ranges", "idle_defaults", "idle_overrides"}
    for k in ("head_lr", "head_ud", "eye", "jaw", "brow"):
        assert k in body["ranges"]
        lo, hi = body["ranges"][k]
        assert lo < hi
    from lafufu_animator.pose import CLAMP

    assert body["ranges"]["head_lr"] == list(CLAMP["head_lr"])


def test_get_config_idle_overrides_reflect_settings(client):
    client.put(
        "/api/settings/animator.head_lr.default",
        json={
            "value": 2077,
            "value_type": "int",
        },
    )
    r = client.get("/api/animator/config")
    assert r.status_code == 200
    assert r.json()["idle_overrides"].get("head_lr") == 2077
