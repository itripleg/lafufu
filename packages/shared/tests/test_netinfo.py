import socket

from lafufu_shared.netinfo import primary_lan_ip


def test_returns_routable_ipv4(monkeypatch):
    class _FakeSock:
        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.42", 54321)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSock())
    assert primary_lan_ip() == "192.168.1.42"


def test_returns_none_when_offline(monkeypatch):
    class _DeadSock:
        def connect(self, addr):
            raise OSError("Network is unreachable")

        def getsockname(self):
            raise AssertionError("must not be reached when offline")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _DeadSock())
    assert primary_lan_ip() is None
