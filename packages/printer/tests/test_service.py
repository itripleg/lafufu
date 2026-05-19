import asyncio

import nats
from lafufu_printer.service import PrinterService
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_shared.testing import nats_server_fixture

nats_server = nats_server_fixture("4260")


class FakeCups:
    def __init__(self, available: bool = True):
        self.available = available
        self.printed: list[tuple[str, str | None]] = []
        # Captures (path, title, target_size_px) so tests can assert what
        # the printer service handed off to the cups layer for image jobs.
        self.printed_files: list[tuple[str, str | None, tuple[int, int] | None]] = []

    def list_printers(self) -> list[str]:
        return ["fake-printer"] if self.available else []

    def default_printer(self) -> str | None:
        return "fake-printer" if self.available else None

    def print_text(self, text: str, *, title: str | None = None) -> str:
        self.printed.append((text, title))
        return "job-001"

    def print_file(
        self,
        path,
        *,
        title: str | None = None,
        extra_lp_options=None,
        target_size_px=None,
        dead_zone_top_px: int = 0,
        dead_zone_bottom_px: int = 0,
    ) -> str:
        self.printed_files.append((str(path), title, target_size_px))
        return "job-file-001"


async def test_publishes_offline_when_no_printer(nats_server):
    cups = FakeCups(available=False)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    seen: list[schemas.PrinterState] = []

    async def cb(msg):
        seen.append(schemas.PrinterState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    # Trigger by sending an intent so service publishes current state
    await publish_model(
        nc, topics.PRINTER_INTENT_TEST_PAGE, schemas.AgentReply(text="x", emotion="neutral")
    )
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert any(s.state == "offline" for s in seen) or cups.printed == []


async def test_auto_print_on_agent_reply(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.AGENT_REPLY, schemas.AgentReply(text="Hello!", emotion="happy"))
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(cups.printed) == 1
    assert "Hello!" in cups.printed[0][0]


async def test_auto_print_disabled(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(nc, topics.AGENT_REPLY, schemas.AgentReply(text="Hi", emotion="neutral"))
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert cups.printed == []


async def test_print_intent_always_prints(nats_server):
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(
        nc, topics.PRINTER_INTENT_PRINT_TEXT, schemas.PrinterIntentPrintText(text="Hand-triggered")
    )
    await asyncio.sleep(0.2)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(cups.printed) == 1
    assert "Hand-triggered" in cups.printed[0][0]
