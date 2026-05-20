"""Optional shared-token auth — loopback bypass, cookie + bearer, login flow."""

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.api.auth import COOKIE_NAME, is_authorized
from lafufu_control.api.ws_bridge import WsBridge
from lafufu_control.db import create_engine_for_path, init_db


def _app(tmp_path, api_token=""):
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    return create_app(engine=engine, nats_publish=lambda s, p: None, api_token=api_token)


# --- is_authorized: the shared decision function ------------------------------


def test_disabled_when_no_token_configured():
    assert is_authorized(configured="", client_host="1.2.3.4", headers={}, cookies={})


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_is_always_trusted(host):
    # The on-device kiosk reaches the API over loopback — no token needed.
    assert is_authorized(configured="secret", client_host=host, headers={}, cookies={})


def test_remote_without_token_is_rejected():
    assert not is_authorized(configured="secret", client_host="10.0.0.5", headers={}, cookies={})


def test_remote_with_correct_cookie_is_allowed():
    assert is_authorized(
        configured="secret",
        client_host="10.0.0.5",
        headers={},
        cookies={COOKIE_NAME: "secret"},
    )


def test_remote_with_correct_bearer_is_allowed():
    assert is_authorized(
        configured="secret",
        client_host="10.0.0.5",
        headers={"authorization": "Bearer secret"},
        cookies={},
    )


def test_remote_with_wrong_token_is_rejected():
    assert not is_authorized(
        configured="secret",
        client_host="10.0.0.5",
        headers={"authorization": "Bearer nope"},
        cookies={COOKIE_NAME: "alsowrong"},
    )


# --- HTTP integration (TestClient host is "testclient" → treated as remote) ----


def test_no_token_configured_leaves_api_open(tmp_path):
    client = TestClient(_app(tmp_path, api_token=""))
    assert client.get("/api/auth/check").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_guarded_router_rejects_remote_without_token(tmp_path):
    client = TestClient(_app(tmp_path, api_token="secret"))
    assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_bearer_header_unlocks_guarded_router(tmp_path):
    client = TestClient(_app(tmp_path, api_token="secret"))
    r = client.get("/api/settings", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_login_with_wrong_token_is_401(tmp_path):
    client = TestClient(_app(tmp_path, api_token="secret"))
    r = client.post("/api/auth/login", json={"token": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "bad_token"


def test_login_sets_cookie_and_unlocks_subsequent_requests(tmp_path):
    client = TestClient(_app(tmp_path, api_token="secret"))
    r = client.post("/api/auth/login", json={"token": "secret"})
    assert r.status_code == 200
    assert COOKIE_NAME in r.cookies
    # The TestClient cookie jar now carries the session cookie automatically.
    assert client.get("/api/auth/check").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_loopback_client_bypasses_token(tmp_path):
    # Simulate the on-device kiosk: a request originating from loopback.
    client = TestClient(_app(tmp_path, api_token="secret"), client=("127.0.0.1", 54321))
    assert client.get("/api/auth/check").status_code == 200


# --- WebSocket bridge ---------------------------------------------------------


class _StubNats:
    async def subscribe(self, *a, **k):  # pragma: no cover - never reached in these tests
        raise AssertionError("subscribe should not run before a sub frame")


def _app_with_ws(tmp_path, api_token=""):
    app = _app(tmp_path, api_token=api_token)
    WsBridge(_StubNats()).mount(app)
    return app


def test_ws_rejects_remote_without_token(tmp_path):
    client = TestClient(_app_with_ws(tmp_path, api_token="secret"))
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws"):
        pass


def test_ws_accepts_after_login(tmp_path):
    client = TestClient(_app_with_ws(tmp_path, api_token="secret"))
    assert client.post("/api/auth/login", json={"token": "secret"}).status_code == 200
    # Cookie from login now rides the WS handshake — connection is accepted.
    with client.websocket_connect("/ws"):
        pass


def test_ws_open_when_no_token_configured(tmp_path):
    client = TestClient(_app_with_ws(tmp_path, api_token=""))
    with client.websocket_connect("/ws"):
        pass
