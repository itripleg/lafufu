"""Focused unit tests for WsBridge._fanout robustness.

The integration suite in test_ws_bridge.py runs the full uvicorn stack and
covers the happy path. These tests construct a WsBridge directly so we can
exercise the iteration-safety + error-isolation paths that are hard to
hit through a real socket.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lafufu_control.api.ws_bridge import WsBridge


class _MutatingWs:
    """Fake WS that mutates the bridge's listener set the moment send_json
    is awaited. Reproduces what happens if a concurrent disconnect cleans
    up the listener set while _fanout is iterating it."""

    def __init__(self, bridge: WsBridge, pattern: str):
        self.bridge = bridge
        self.pattern = pattern
        self.sent: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.bridge._pattern_listeners[self.pattern].discard(self)
        self.sent.append(frame)


class _RaisingWs:
    """Fake WS that raises on send_json — simulates a dead socket. The
    bridge should not let one bad socket break delivery to the others."""

    async def send_json(self, frame: dict) -> None:
        raise ConnectionResetError("simulated")

    async def close(self) -> None:
        pass


class _GoodWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)


@pytest.mark.asyncio
async def test_fanout_tolerates_listener_set_mutation_mid_iteration():
    """Iterating self._pattern_listeners[pattern] while a listener removes
    itself raises RuntimeError if the live set is iterated. Iterating a
    snapshot avoids this."""
    bridge = WsBridge(nats_client=MagicMock())
    pattern = "x.y"
    ws1 = _MutatingWs(bridge, pattern)
    ws2 = _GoodWs()
    bridge._pattern_listeners[pattern] = {ws1, ws2}  # type: ignore[dict-item]

    # Should not raise RuntimeError even though ws1.send_json mutates the set.
    await bridge._fanout(pattern, pattern, b'{"value": 1}')

    # Both WSs should have received the frame from the snapshot.
    assert ws1.sent == [{"topic": pattern, "payload": {"value": 1}}]
    assert ws2.sent == [{"topic": pattern, "payload": {"value": 1}}]


@pytest.mark.asyncio
async def test_fanout_one_bad_socket_does_not_block_others():
    """A WS that raises during send should be marked dead but not stop
    the bridge from delivering to other listeners."""
    bridge = WsBridge(nats_client=MagicMock())
    pattern = "x.y"
    bad = _RaisingWs()
    good = _GoodWs()
    bridge._pattern_listeners[pattern] = {bad, good}  # type: ignore[dict-item]

    await bridge._fanout(pattern, pattern, b'{"value": 7}')

    assert good.sent == [{"topic": pattern, "payload": {"value": 7}}]


@pytest.mark.asyncio
async def test_fanout_removes_dead_socket_from_pattern_listeners():
    """A WS that raises during send must be removed from _pattern_listeners so
    subsequent fanout calls don't iterate it again and the NATS ref-count isn't
    permanently inflated."""
    bridge = WsBridge(nats_client=MagicMock(), allowed_patterns=frozenset({"x.y"}))
    pattern = "x.y"
    bad = _RaisingWs()
    good = _GoodWs()

    # Wire up internal state as if both sockets subscribed to the pattern
    bridge._ws_patterns[bad] = {pattern}
    bridge._ws_patterns[good] = {pattern}
    bridge._pattern_listeners[pattern] = {bad, good}
    # Set ref-count to 2 (one per socket)
    mock_sub = MagicMock()
    mock_sub.unsubscribe = AsyncMock()
    bridge._subs[pattern] = (mock_sub, 2)

    await bridge._fanout(pattern, pattern, b'{"v": 1}')

    assert bad not in bridge._pattern_listeners.get(pattern, set()), (
        "dead WS must be removed from _pattern_listeners after send failure"
    )
    assert bad not in bridge._ws_patterns, (
        "dead WS must be removed from _ws_patterns after send failure"
    )
    # Ref-count must have been decremented for the dead socket
    remaining_count = bridge._subs.get(pattern, (None, 0))[1]
    assert remaining_count == 1, (
        f"NATS ref-count must drop from 2 to 1 after dead socket cleanup; got {remaining_count}"
    )
