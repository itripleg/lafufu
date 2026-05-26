import asyncio
import subprocess
import time

import pytest
from lafufu_shared import nats_helper, schemas, topics


@pytest.fixture(scope="module")
def nats_server(tmp_path_factory):
    """Spawn a real nats-server for this module's tests."""
    storedir = tmp_path_factory.mktemp("js")
    proc = subprocess.Popen(
        ["nats-server", "--port", "4233", "--jetstream", "--store_dir", str(storedir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield "nats://localhost:4233"
    proc.terminate()
    proc.wait(timeout=5)


async def test_connect_with_retry_succeeds(nats_server):
    nc = await nats_helper.connect_with_retry(nats_server, name="test")
    assert nc.is_connected
    await nc.drain()


async def test_publish_and_subscribe_model_round_trip(nats_server):
    nc = await nats_helper.connect_with_retry(nats_server, name="t")
    got: list[schemas.AgentReply] = []

    async def handler(subject: str, msg: schemas.AgentReply):
        got.append(msg)

    sub = await nats_helper.subscribe_model(nc, topics.AGENT_REPLY, schemas.AgentReply, handler)
    await nats_helper.publish_model(
        nc, topics.AGENT_REPLY, schemas.AgentReply(text="hi", emotion="happy")
    )
    await asyncio.sleep(0.1)
    await sub.unsubscribe()
    await nc.drain()

    assert len(got) == 1
    assert got[0].text == "hi"
    assert got[0].emotion == "happy"


async def test_subscribe_drops_invalid_payload(nats_server, caplog):
    nc = await nats_helper.connect_with_retry(nats_server, name="t")
    got: list = []

    async def handler(subject, msg):
        got.append(msg)

    await nats_helper.subscribe_model(nc, "test.bad", schemas.AgentReply, handler)
    await nc.publish("test.bad", b"not json")
    # `emotion` is now `str` (PR #20 — registry is the validity check), so a
    # nonsense emotion name no longer fails schema. Test schema-level dropping
    # with a missing required field instead.
    await nc.publish("test.bad", b'{"emotion":"happy"}')  # missing required "text"
    await asyncio.sleep(0.1)
    await nc.drain()
    assert got == []
