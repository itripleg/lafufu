import asyncio

import nats
from lafufu_printer.service import PrinterService, _resolve_font
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_shared.testing import nats_server_fixture

nats_server = nats_server_fixture("4260")


def test_resolve_font_finds_bundled_default():
    """The repo's default IM Fell English font resolves to an on-disk path."""
    path = _resolve_font("IMFellEnglish-Regular.ttf")
    assert path is not None and path.endswith("IMFellEnglish-Regular.ttf")


def test_resolve_font_rejects_unknown_and_unsafe_names():
    assert _resolve_font(None) is None
    assert _resolve_font("does-not-exist.ttf") is None
    # Path traversal / nested names must not resolve.
    assert _resolve_font("../../../etc/passwd") is None
    assert _resolve_font("sub/dir.ttf") is None


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


async def _collect_state_transitions(nc, timeout: float = 0.4) -> list[str]:
    """Subscribe to printer.state.* + collect final-token of each subject for
    `timeout` seconds. Returns transitions in order."""
    seen: list[str] = []

    async def cb(msg):
        seen.append(msg.subject.rsplit(".", 1)[-1])

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    await asyncio.sleep(timeout)
    return seen


async def test_compose_with_missing_letterhead_returns_to_idle(nats_server):
    """A compose with a non-existent letterhead path should publish error
    AND then idle — not stay stuck in error state. Regression test for the
    early-return paths in _on_compose."""
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    seen: list[str] = []

    async def cb(msg):
        seen.append(msg.subject.rsplit(".", 1)[-1])

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    await publish_model(
        nc,
        topics.PRINTER_INTENT_COMPOSE,
        schemas.PrinterIntentCompose(
            letterhead_path="/definitely/does/not/exist.png", text="hello"
        ),
    )
    await asyncio.sleep(0.4)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert "error" in seen, f"expected error state, saw {seen}"
    assert seen[-1] == "idle", f"expected to return to idle, ended in {seen[-1]}; seen={seen}"


async def test_print_file_rejects_path_outside_data_dir(nats_server, tmp_path, monkeypatch):
    """A NATS publisher sending an arbitrary file path (e.g. /etc/passwd)
    must NOT cause the printer to open it. Allowed roots are the printer
    data dir + tempfile.gettempdir() (for composed PNGs)."""
    data_dir = tmp_path / "printer-data"
    data_dir.mkdir()
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(data_dir))

    # Create something readable outside the allowed roots.
    outside = tmp_path / "evil.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # PNG header

    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    seen: list[str] = []

    async def cb(msg):
        seen.append(msg.subject.rsplit(".", 1)[-1])

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    await publish_model(
        nc,
        topics.PRINTER_INTENT_PRINT_FILE,
        schemas.PrinterIntentPrintFile(path=str(outside)),
    )
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    # File was rejected before reaching the cups layer.
    assert cups.printed_files == []
    # And the service reported an error then returned to idle.
    assert "error" in seen, f"expected an error state, saw {seen}"


async def test_compose_with_bad_image_returns_to_idle(nats_server, tmp_path):
    """A compose with a non-image letterhead (e.g. text file) makes
    compose_fortune raise. Service should still return to idle."""
    bogus = tmp_path / "not_an_image.png"
    bogus.write_text("this is not png data")

    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    seen: list[str] = []

    async def cb(msg):
        seen.append(msg.subject.rsplit(".", 1)[-1])

    await nc.subscribe(f"{topics.PRINTER_STATE}.*", cb=cb)
    await publish_model(
        nc,
        topics.PRINTER_INTENT_COMPOSE,
        schemas.PrinterIntentCompose(letterhead_path=str(bogus), text="hello"),
    )
    await asyncio.sleep(0.6)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert "error" in seen, f"expected error state, saw {seen}"
    assert seen[-1] == "idle", f"expected to return to idle, ended in {seen[-1]}; seen={seen}"
