"""Tests for the Ollama LLM client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


async def test_chat_reuses_client_across_calls():
    """chat() must reuse the same httpx.AsyncClient across multiple calls instead
    of creating a new one per call (which would open a fresh TCP connection each time)."""
    from lafufu_agent.llm import Ollama

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

    import httpx

    with patch.object(httpx, "AsyncClient", _FakeClient):
        ollama = Ollama()
        await ollama.chat("first")
        await ollama.chat("second")

    assert len(clients_created) == 1, (
        f"only one AsyncClient should be created across multiple calls; got {len(clients_created)}"
    )


async def test_aclose_closes_the_persistent_client():
    """aclose() must close the persistent client so connections are released on shutdown."""
    from lafufu_agent.llm import Ollama

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

    import httpx

    with patch.object(httpx, "AsyncClient", _FakeClient):
        ollama = Ollama()
        await ollama.chat("hello")  # creates the client
        await ollama.aclose()

    assert closed == [True], "aclose() must call aclose() on the underlying client"
