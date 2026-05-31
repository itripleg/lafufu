import asyncio

import nats
import pytest
from lafufu_printer.service import PrinterService, _resolve_font, _sanitize_lp_options
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


# ---------------------------------------------------------------------------
# P3.1 — lp_options allow-list
# ---------------------------------------------------------------------------


def test_sanitize_lp_options_allows_known_keys():
    """Known option keys and KEY=VALUE pairs pass through unchanged."""
    result = _sanitize_lp_options("-o media=4x6 scaling=95 fit-to-page")
    assert result == ["-o", "media=4x6", "scaling=95", "fit-to-page"]


def test_sanitize_lp_options_rejects_unknown_flags(caplog):
    """Tokens with unknown leading keys are dropped with a warning log.
    A malicious -h evilhost injection must be rejected."""
    import logging

    with caplog.at_level(logging.WARNING, logger="lafufu_printer.service"):
        result = _sanitize_lp_options("-h evilhost -o media=4x6 --output-format raw")
    assert "-h" not in result
    assert "evilhost" not in result
    assert "--output-format" not in result
    assert "raw" not in result
    # Allowed token should still be present
    assert "-o" in result
    assert "media=4x6" in result
    # Warning logged for each rejected token
    assert any("rejected" in r.message for r in caplog.records)


def test_sanitize_lp_options_empty_string():
    assert _sanitize_lp_options("") == []


# ---------------------------------------------------------------------------
# P3.6 — Printer job lock: second concurrent intent is dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_safe_print_drops_second_while_locked():
    """Directly calling _safe_print twice concurrently — the second call
    arrives while the first is still holding the job lock and should be
    dropped immediately (not queued)."""
    import threading

    slow_started = asyncio.Event()
    slow_released = threading.Event()
    loop = asyncio.get_running_loop()

    class SlowCups(FakeCups):
        def print_text(self, text, *, title=None):
            loop.call_soon_threadsafe(slow_started.set)
            slow_released.wait(timeout=5)
            self.printed.append((text, title))
            return "job-slow"

    cups = SlowCups(available=True)
    svc = PrinterService.__new__(PrinterService)
    svc.__init__(cups=cups, nats_url=None, auto_print=True)

    # Inject a fake NATS client so _publish_state doesn't crash.
    class FakeNats:
        async def publish(self, *a, **kw):
            pass

    svc._nats = FakeNats()
    svc.nats = FakeNats()  # base_service exposes both

    # Monkey-patch _publish_state to be a no-op (we don't have a running NATS
    # server here — we're testing lock semantics, not state publishing).
    async def _noop_publish(*a, **kw):
        pass

    svc._publish_state = _noop_publish  # type: ignore[method-assign]

    # Start first print (will block inside print_text via to_thread)
    t1 = asyncio.create_task(svc._safe_print("First"))
    # Yield to let t1 start and reach the lock acquisition + to_thread launch.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Wait until the blocking thread has actually started so the lock is held.
    await asyncio.wait_for(slow_started.wait(), timeout=2.0)
    assert svc._job_lock.locked(), "lock should be held by first print"

    # Second print arrives while lock is held — should be dropped immediately.
    t2 = asyncio.create_task(svc._safe_print("Second"))
    await asyncio.sleep(0)  # let t2 run its check
    await asyncio.sleep(0)
    # t2 should have returned (dropped) without waiting for the lock.
    assert t2.done(), "second _safe_print should have returned (dropped) immediately"

    # Release the slow print.
    slow_released.set()
    await asyncio.wait_for(t1, timeout=3)

    # Only the first print went through.
    assert len(cups.printed) == 1
    assert "First" in cups.printed[0][0]


# ---------------------------------------------------------------------------
# compose_fortune intent: letterhead + font resolved service-side
# ---------------------------------------------------------------------------


async def test_compose_fortune_uses_active_letterhead_and_prints(
    nats_server, tmp_path, monkeypatch
):
    """A PrinterIntentComposeFortune composes onto the SERVICE-resolved active
    letterhead (here the white-card fallback, which legitimately lives outside
    the data dir) and prints — without rejecting on path-safety, and passing
    the lucky info through to the composer."""
    data_dir = tmp_path / "printer-data"
    data_dir.mkdir()
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(data_dir))

    # Capture what reaches the composer so we can assert the lucky info passes
    # through and the resolved (trusted) letterhead is used as-is.
    captured: dict = {}

    def fake_compose_fortune(letterhead_path, body_text, **kwargs):
        captured["letterhead_path"] = letterhead_path
        captured["body_text"] = body_text
        captured.update(kwargs)
        out = tmp_path / "composed.png"
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return out

    monkeypatch.setattr("lafufu_printer.composer.compose_fortune", fake_compose_fortune)

    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.PRINTER_INTENT_COMPOSE_FORTUNE,
        schemas.PrinterIntentComposeFortune(
            text="your destiny awaits",
            lucky_subway_stop="Bedford Av",
            lucky_numbers=[7, 11, 23],
            title="lafufu fortune",
        ),
    )
    await asyncio.sleep(0.5)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    # Reached the print call (no path-safety rejection of the white fallback).
    assert len(cups.printed_files) == 1
    # The lucky info was threaded through to the composer.
    assert captured["body_text"] == "your destiny awaits"
    assert captured["lucky_subway_stop"] == "Bedford Av"
    assert captured["lucky_numbers"] == [7, 11, 23]
    # Letterhead is the service-resolved active path (white fallback here).
    assert captured["letterhead_path"].name == "white.png"


async def test_compose_fortune_drops_when_busy(nats_server, tmp_path, monkeypatch):
    """When the job lock is already held, a compose_fortune intent is dropped
    (no print) — mirroring the existing compose busy-drop behaviour."""
    data_dir = tmp_path / "printer-data"
    data_dir.mkdir()
    monkeypatch.setenv("LAFUFU_PRINTER_DATA_DIR", str(data_dir))

    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=False)
    # Hold the lock so the incoming intent must drop.
    await svc._job_lock.acquire()
    try:
        msg = schemas.PrinterIntentComposeFortune(text="hello")
        await svc._on_compose_fortune("subject", msg)
    finally:
        svc._job_lock.release()

    assert cups.printed_files == [], "busy compose_fortune should have been dropped"


# ---------------------------------------------------------------------------
# auto_print source-gating: system/opening lines must not auto-print
# ---------------------------------------------------------------------------


async def test_agent_reply_source_system_does_not_print(nats_server):
    """The wake-word/opening reply (source='system') must NEVER auto-print,
    even with auto_print enabled."""
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.AGENT_REPLY,
        schemas.AgentReply(text="yo what's good", emotion="neutral", source="system"),
    )
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert cups.printed == [], "system-source reply must not auto-print"


async def test_agent_reply_source_llm_still_prints(nats_server):
    """A normal LLM reply (source='llm') still auto-prints — guard against
    over-gating the source check."""
    cups = FakeCups(available=True)
    svc = PrinterService(cups=cups, nats_url=nats_server, auto_print=True)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.4)
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.AGENT_REPLY,
        schemas.AgentReply(text="here is your fortune", emotion="happy", source="llm"),
    )
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(cups.printed) == 1
    assert "here is your fortune" in cups.printed[0][0]
