"""WS bridge integration tests.

Strategy: spin a real uvicorn server in a daemon thread so the NATS client
and WS endpoint share *one* event loop.  Tests are synchronous and use the
websockets.sync client so there is no cross-loop sharing.
"""

import asyncio
import json
import queue
import threading
import time

import nats
import pytest
import uvicorn
from lafufu_control.api.app import create_app
from lafufu_control.api.ws_bridge import WsBridge
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_shared.testing import nats_server_fixture
from websockets.sync.client import connect as ws_connect

nats_server = nats_server_fixture("4270")

_PORT = 18790


# ---------------------------------------------------------------------------
# Server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bridge_server(nats_server):
    """Start one uvicorn server per test-module; yields (bridge_ref, publish_fn)."""
    # We share the module-scoped nats_server URL.
    app = None  # built inside the thread to avoid import issues

    bridge_ref: list[WsBridge] = []
    publish_q: queue.Queue = queue.Queue()
    ready_event = threading.Event()
    stop_event = threading.Event()
    error_ref: list[Exception] = []

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            try:
                # Build app & DB inside this loop
                import tempfile

                tmp = tempfile.mkdtemp()
                engine = create_engine_for_path(f"{tmp}/t.sqlite")
                init_db(engine)
                nonlocal app
                app = create_app(engine=engine, nats_publish=lambda s, p: None)

                nc = await nats.connect(nats_server)
                bridge = WsBridge(nc)
                bridge.mount(app)
                bridge_ref.append(bridge)

                # Publisher task: drains the queue and publishes to NATS
                async def publisher():
                    while not stop_event.is_set():
                        try:
                            subject, data = publish_q.get_nowait()
                            await nc.publish(subject, data)
                            publish_q.task_done()
                        except queue.Empty:
                            await asyncio.sleep(0.01)

                _tasks = [asyncio.create_task(publisher())]

                config = uvicorn.Config(
                    app, host="127.0.0.1", port=_PORT, log_level="error", loop="asyncio"
                )
                server = uvicorn.Server(config)
                ready_event.set()
                serve_task = asyncio.create_task(server.serve())
                while not stop_event.is_set():
                    await asyncio.sleep(0.05)
                server.should_exit = True
                await serve_task
                await nc.close()
            except Exception as exc:
                error_ref.append(exc)
                ready_event.set()  # unblock caller

        loop.run_until_complete(main())
        loop.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready_event.wait(timeout=10), "server thread never became ready"
    time.sleep(0.4)  # give uvicorn time to bind

    if error_ref:
        raise error_ref[0]

    def publish(subject: str, data: bytes) -> None:
        """Put a NATS publish request on the queue and wait for it to be sent."""
        publish_q.put((subject, data))
        publish_q.join()

    yield bridge_ref, publish

    stop_event.set()
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ws_subscribe_and_receive(bridge_server):
    _, publish = bridge_server
    with ws_connect(f"ws://127.0.0.1:{_PORT}/ws") as ws:
        ws.send(json.dumps({"op": "sub", "topics": ["test.echo"]}))
        time.sleep(0.1)  # let the subscribe register
        publish("test.echo", b'{"hi":"there"}')
        raw = ws.recv(timeout=2)
    msg = json.loads(raw)
    assert msg["topic"] == "test.echo"
    assert msg["payload"] == {"hi": "there"}


def test_ws_lazy_subscription_unsubs_when_last_client_leaves(bridge_server):
    bridge_ref, _ = bridge_server
    bridge = bridge_ref[0]
    with ws_connect(f"ws://127.0.0.1:{_PORT}/ws") as ws:
        ws.send(json.dumps({"op": "sub", "topics": ["test.lazy"]}))
        time.sleep(0.1)
        assert bridge.nats_sub_count("test.lazy") == 1
    # WS closed → bridge should unsubscribe NATS
    time.sleep(0.2)
    assert bridge.nats_sub_count("test.lazy") == 0


def test_ws_invalid_payload_dropped(bridge_server):
    _, publish = bridge_server
    with ws_connect(f"ws://127.0.0.1:{_PORT}/ws") as ws:
        ws.send(json.dumps({"op": "sub", "topics": ["test.bad"]}))
        time.sleep(0.1)
        publish("test.bad", b"not json")
        time.sleep(0.2)
        # Connection should still be alive; bridge did not crash
        ws.send(json.dumps({"op": "sub", "topics": ["test.bad"]}))
        # no message should have been delivered for the invalid payload
        try:
            ws.recv(timeout=0.2)
            raise AssertionError("should not receive any message for invalid payload")
        except TimeoutError:
            pass  # expected: nothing arrived


def test_ws_bad_client_frame_does_not_kill_session(bridge_server):
    """A malformed JSON frame from the client should not tear down the
    whole session — the bridge should drop the bad frame, optionally log,
    and continue processing subsequent frames."""
    _, publish = bridge_server
    with ws_connect(f"ws://127.0.0.1:{_PORT}/ws") as ws:
        # Send garbage as the first frame.
        ws.send("this isn't json {{{")
        time.sleep(0.1)
        # Now send a valid subscribe — if the session survived, this should
        # register and deliver a published message.
        ws.send(json.dumps({"op": "sub", "topics": ["test.resilience"]}))
        time.sleep(0.1)
        publish("test.resilience", b'{"ok": true}')
        raw = ws.recv(timeout=2)
    msg = json.loads(raw)
    assert msg["topic"] == "test.resilience"
    assert msg["payload"] == {"ok": True}
