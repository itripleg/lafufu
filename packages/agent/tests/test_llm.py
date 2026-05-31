"""Tests for the Ollama LLM client.

Two independent concerns:
- Connection reuse: chat() reuses a single httpx.AsyncClient across calls, and
  aclose() releases it on shutdown (avoids a fresh TCP connection per call).
- keep_alive normalization: numeric strings ("-1", "300") must go on the wire
  as ints (Ollama won't parse "-1" as the int -1), while duration strings
  ("10m") pass through untouched — keeps the model resident so warmup stays hot
  across agent restarts instead of paying a 30-60s cold load.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from lafufu_agent.llm import Ollama


async def test_chat_reuses_client_across_calls():
    """chat() must reuse the same httpx.AsyncClient across multiple calls instead
    of creating a new one per call (which would open a fresh TCP connection each time)."""
    clients_created: list[object] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"message": {"content": "hello"}}

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            clients_created.append(self)
            self._closed = False

        async def post(self, url: str, json: dict) -> _FakeResponse:
            return _FakeResponse()

        async def aclose(self) -> None:
            self._closed = True

        @property
        def is_closed(self) -> bool:
            return self._closed

    with patch.object(httpx, "AsyncClient", _FakeClient):
        ollama = Ollama()
        await ollama.chat("first")
        await ollama.chat("second")

    assert len(clients_created) == 1, (
        f"only one AsyncClient should be created across multiple calls; got {len(clients_created)}"
    )


async def test_aclose_closes_the_persistent_client():
    """aclose() must close the persistent client so connections are released on shutdown."""
    closed: list[bool] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"message": {"content": "hi"}}

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self._closed = False

        async def post(self, url: str, json: dict) -> _FakeResponse:
            return _FakeResponse()

        async def aclose(self) -> None:
            self._closed = True
            closed.append(True)

        @property
        def is_closed(self) -> bool:
            return self._closed

    with patch.object(httpx, "AsyncClient", _FakeClient):
        ollama = Ollama()
        await ollama.chat("hello")  # creates the client
        await ollama.aclose()

    assert closed == [True], "aclose() must call aclose() on the underlying client"


class _CaptureClient:
    """Stand-in for httpx.AsyncClient that records the JSON body of the POST."""

    last_json: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> _CaptureClient:
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url, json=None):
        _CaptureClient.last_json = json

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"message": {"content": "ok"}}

        return _Resp()


@pytest.mark.parametrize(
    "configured,expected",
    [
        ("-1", -1),  # indefinite — must be the int, not the string
        ("0", 0),
        ("300", 300),
        ("10m", "10m"),  # duration strings pass through untouched
        ("24h", "24h"),
    ],
)
async def test_chat_normalizes_keep_alive(monkeypatch, configured, expected):
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
    ollama = Ollama(keep_alive=configured)
    await ollama.chat("hi")
    assert _CaptureClient.last_json is not None
    assert _CaptureClient.last_json["keep_alive"] == expected
    assert type(_CaptureClient.last_json["keep_alive"]) is type(expected)


async def test_warmup_normalizes_keep_alive(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
    ollama = Ollama(keep_alive="-1")
    await ollama.warmup()
    assert _CaptureClient.last_json is not None
    assert _CaptureClient.last_json["keep_alive"] == -1
    assert type(_CaptureClient.last_json["keep_alive"]) is int
