import asyncio
import subprocess
import time

import nats
import pytest
from lafufu_shared import base_service, schemas, topics


@pytest.fixture(scope="module")
def nats_server(tmp_path_factory):
    storedir = tmp_path_factory.mktemp("js")
    proc = subprocess.Popen(
        ["nats-server", "--port", "4234", "--jetstream", "--store_dir", str(storedir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield "nats://localhost:4234"
    proc.terminate()
    proc.wait(timeout=5)


class _TinyService(base_service.BaseService):
    name = "agent"  # reuse known ServiceName; doesn't matter for test
    nats_url_override: str = ""

    def __init__(self, nats_url: str):
        super().__init__()
        self.nats_url_override = nats_url
        self.startup_called = False
        self.shutdown_called = False

    @property
    def nats_url(self) -> str:
        return self.nats_url_override

    async def on_startup(self) -> None:
        self.startup_called = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True

    async def main_loop(self) -> None:
        # Wait until external shutdown signal
        await self._shutdown.wait()


async def test_lifecycle_calls_startup_and_shutdown(nats_server):
    svc = _TinyService(nats_server)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.2)
    assert svc.startup_called
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert svc.shutdown_called


async def test_heartbeat_published(nats_server):
    svc = _TinyService(nats_server)
    svc.heartbeat_interval_s = 0.1  # speed up
    received: list[schemas.SystemHeartbeat] = []

    # Subscribe before starting
    nc = await nats.connect(nats_server)

    async def cb(msg):
        received.append(schemas.SystemHeartbeat.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.SYSTEM_HEARTBEAT}.>", cb=cb)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.35)  # ~3 heartbeats
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    await nc.drain()

    assert len(received) >= 2
    assert all(h.service == "agent" for h in received)
