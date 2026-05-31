from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from lafufu_control.api.app import create_app
from lafufu_control.api.routers.system import _parse_nmcli_conns
from lafufu_control.db import create_engine_for_path, init_db


@pytest.fixture
def client_factory(tmp_path):
    def make():
        engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
        init_db(engine)
        published: list[tuple[str, dict]] = []
        client = TestClient(
            create_app(
                engine=engine,
                nats_publish=lambda s, p: published.append((s, p)),
            )
        )
        return client, published

    return make


def test_restart_known_service(client_factory):
    client, published = client_factory()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stderr = b""
        r = client.post("/api/system/services/agent/restart")
    assert r.status_code == 200
    called_args = run_mock.call_args[0][0]
    assert called_args == ["sudo", "-n", "systemctl", "restart", "lafufu-agent"]
    assert any(s == "system.service.restarting" for s, _ in published)


def test_restart_unknown_service_400(client_factory):
    client, _ = client_factory()
    r = client.post("/api/system/services/notreal/restart")
    assert r.status_code == 400


def test_animator_preview_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/animator/preview", json={"name": "jaw", "position": 1700})
    assert r.status_code == 202
    assert any(s == "animator.intent.preview" for s, _ in published)


def test_animator_expression_publishes(client_factory):
    client, published = client_factory()
    # Seed frame + expression so the legacy endpoint can resolve them.
    client.post(
        "/api/animator/frames",
        json={
            "name": "happy_a",
            "head_lr": 2063,
            "head_ud": 3052,
            "eye": 2045,
            "jaw": 1688,
            "brow": 2093,
        },
    )
    client.post(
        "/api/animator/expressions",
        json={"name": "happy", "playback": "loop", "steps": [{"frame": "happy_a"}]},
    )
    r = client.post("/api/animator/expression", json={"name": "happy"})
    assert r.status_code == 202
    assert any(s == "animator.intent.play_expression" for s, _ in published)


def test_agent_text_message_publishes(client_factory):
    client, published = client_factory()
    r = client.post("/api/agent/text_message", json={"text": "hello"})
    assert r.status_code == 202
    assert any(s == "agent.intent.text_message" for s, _ in published)


# ── Wi-Fi selector ───────────────────────────────────────────────────────────

_SYS = "lafufu_control.api.routers.system"


def test_parse_nmcli_conns_keeps_only_wifi_and_unescapes():
    # nmcli -t escapes ':' inside a NAME as '\:'; TYPE has no ':'.
    out = (
        "liquid:802-11-wireless\n"
        "Wired connection 1:802-3-ethernet\n"
        "spectrum\\: a3:802-11-wireless\n"
    )
    assert _parse_nmcli_conns(out) == ["liquid", "spectrum: a3"]


def _patched_linux_nmcli(fake_run):
    """Patch platform=Linux, nmcli present, and subprocess.run side_effect."""
    return (
        patch(f"{_SYS}.platform.system", return_value="Linux"),
        patch(f"{_SYS}.shutil.which", return_value="/usr/bin/nmcli"),
        patch(f"{_SYS}.subprocess.run", side_effect=fake_run),
    )


def _fake_nmcli(
    saved="liquid:802-11-wireless\nspectrum a3:802-11-wireless\n", active="liquid:802-11-wireless\n"
):
    calls: list[list[str]] = []

    def run(args, **kw):
        calls.append(args)
        m = MagicMock()
        m.returncode = 0
        m.stderr = b""
        if "--active" in args:
            m.stdout = active
        elif "show" in args:
            m.stdout = saved
        else:  # the connect call
            m.stdout = ""
        return m

    return run, calls


def test_wifi_info_lists_saved_networks_and_current(client_factory):
    client, _ = client_factory()
    run, _calls = _fake_nmcli()
    p1, p2, p3 = _patched_linux_nmcli(run)
    with p1, p2, p3:
        r = client.get("/api/system/wifi")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["current"] == "liquid"
    assert body["networks"] == ["liquid", "spectrum a3"]


def test_wifi_connect_rejects_unsaved_network(client_factory):
    """Only saved profiles may be activated — arbitrary input never hits nmcli."""
    client, _ = client_factory()
    run, calls = _fake_nmcli()
    p1, p2, p3 = _patched_linux_nmcli(run)
    with p1, p2, p3:
        r = client.post("/api/system/wifi/connect", json={"name": "evil; rm -rf /"})
    assert r.status_code == 400
    # never attempted a connect
    assert not any("up" in c for c in calls)


def test_wifi_connect_activates_saved_network(client_factory):
    client, _ = client_factory()
    run, calls = _fake_nmcli()
    p1, p2, p3 = _patched_linux_nmcli(run)
    with p1, p2, p3:
        r = client.post("/api/system/wifi/connect", json={"name": "spectrum a3"})
    assert r.status_code == 200
    assert r.json()["switching_to"] == "spectrum a3"
    # exact non-blocking, privileged command (-w 0 returns before the link flips)
    assert ["sudo", "-n", "nmcli", "-w", "0", "connection", "up", "id", "spectrum a3"] in calls


def test_wifi_info_unavailable_off_linux(client_factory):
    client, _ = client_factory()
    with patch(f"{_SYS}.platform.system", return_value="Windows"):
        r = client.get("/api/system/wifi")
    assert r.status_code == 200
    assert r.json()["available"] is False
