"""PrinterService: auto-prints replies (when enabled) and handles on-demand intents."""

import logging
from datetime import datetime
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .formatter import format_reply, format_transcript

log = logging.getLogger(__name__)


class CupsProtocol(Protocol):
    @property
    def available(self) -> bool: ...
    def list_printers(self) -> list[str]: ...
    def default_printer(self) -> str | None: ...
    def print_text(self, text: str, *, title: str | None = None) -> str: ...


class PrinterService(BaseService):
    name = "printer"

    def __init__(
        self, cups: CupsProtocol, nats_url: str | None = None, auto_print: bool = True
    ) -> None:
        super().__init__()
        self._cups = cups
        self._nats_url = nats_url
        self.auto_print = auto_print

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        await self._publish_state()
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_REPLY,
            schemas.AgentReply,
            self._on_agent_reply,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.PRINTER_INTENT_PRINT_TEXT,
            schemas.PrinterIntentPrintText,
            self._on_print_text,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.PRINTER_INTENT_PRINT_TRANSCRIPT,
            schemas.PrinterIntentPrintTranscript,
            self._on_print_transcript,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.PRINTER_INTENT_TEST_PAGE,
            schemas.AgentReply,
            self._on_test_page,
        )
        # Subscribe to live config changes so auto_print toggle takes effect without restart.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.printer.auto_print",
            schemas.ConfigChanged,
            self._on_config_auto_print,
        )

        # Sync to DB on startup: control rebroadcasts every setting via
        # config.changed.<key>, hitting the same subscriber above.
        await self.request_config_snapshot()

    async def _on_config_auto_print(self, subject: str, msg: schemas.ConfigChanged) -> None:
        v = msg.value
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes", "on")
        self.auto_print = bool(v)
        self.log.info("printer.auto_print.set value=%s", self.auto_print)

    async def _publish_state(
        self, state_name: str | None = None, detail: str | None = None
    ) -> None:
        if state_name is None:
            state_name = "idle" if self._cups.default_printer() else "offline"
        await nats_helper.publish_model(
            self.nats,
            f"{topics.PRINTER_STATE}.{state_name}",
            schemas.PrinterState(
                state=state_name,  # type: ignore[arg-type]
                detail=detail,
                printer_name=self._cups.default_printer(),
            ),
        )

    async def _safe_print(self, text: str, title: str | None = None) -> None:
        if not self._cups.default_printer():
            await self._publish_state("offline")
            return
        await self._publish_state("printing")
        try:
            job_id = self._cups.print_text(text, title=title)
            await nats_helper.publish_model(
                self.nats,
                topics.PRINTER_EVENT_JOB_DONE,
                schemas.PrinterEvent(event="job_done", job_id=job_id),
            )
        except Exception as e:
            self.log.warning("print.failed error=%s", e)
            await self._publish_state("error", detail=str(e))
            return
        await self._publish_state("idle")

    async def _on_agent_reply(self, subject: str, msg: schemas.AgentReply) -> None:
        if not self.auto_print:
            return
        text = format_reply(text=msg.text, emotion=msg.emotion, ts=datetime.now())
        await self._safe_print(text, title="lafufu reply")

    async def _on_print_text(self, subject: str, msg: schemas.PrinterIntentPrintText) -> None:
        text = format_reply(text=msg.text, emotion="neutral", ts=datetime.now())
        await self._safe_print(text, title=msg.title or "lafufu print")

    async def _on_print_transcript(
        self, subject: str, msg: schemas.PrinterIntentPrintTranscript
    ) -> None:
        await self._safe_print(format_transcript(msg.transcript), title="lafufu transcript")

    async def _on_test_page(self, subject: str, msg: schemas.AgentReply) -> None:
        await self._publish_state()
