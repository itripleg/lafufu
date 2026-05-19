"""PrinterService: auto-prints replies (when enabled) and handles on-demand intents."""

import asyncio
import contextlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .formatter import format_reply, format_transcript

log = logging.getLogger(__name__)


def _printer_data_dir() -> Path:
    """Directory where uploaded letterheads + composed images live. Matches
    the control router's _data_dir() so the two sides agree."""
    return Path(os.environ.get("LAFUFU_PRINTER_DATA_DIR", "/srv/lafufu/data/printer"))


def _path_within_allowed_roots(path: Path) -> bool:
    """True if `path` resolves to a location inside the printer data dir.
    Anything else (e.g. /etc/shadow, ~/.ssh) is rejected — direct NATS
    publishers must not be able to read arbitrary files via this service.
    (Composed temp files are printed via direct method call, not a NATS
    intent, so they don't need to be in the allow list.)"""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    root = _printer_data_dir().resolve()
    return _is_inside(resolved, root)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class CupsProtocol(Protocol):
    @property
    def available(self) -> bool: ...
    def list_printers(self) -> list[str]: ...
    def default_printer(self) -> str | None: ...
    def print_text(self, text: str, *, title: str | None = None) -> str: ...


# Known thermal-label media sizes — ACTUAL PRINTABLE AREA (width_in, height_in),
# NOT the nominal label size. The Phomemo 4x6 driver reports media[288x432pts]
# but the real printable region is 274.46x432pts = 3.81x6" — there's a small
# horizontal dead zone where the print head can't reach. Their own sample PDF
# (https://doc.phomemo.com/Labels-Sample.pdf) renders at 3.81x6 for this
# reason. Using the nominal size causes the right edge to clip.
_MEDIA_INCHES: dict[str, tuple[float, float]] = {
    "4x6": (3.81, 6.0),
    "4x8": (3.81, 8.0),
    "2x1": (2.0, 1.0),
    "Round108": (1.5, 1.5),
    "Round144": (2.0, 2.0),
    "Letter": (8.27, 10.69),  # ~0.25" margin all around
    "A4": (8.05, 11.47),
    "A6": (3.93, 5.63),
}
_PRINTER_DPI = 203  # Phomemo standard; close enough for other thermals too.


class PrinterService(BaseService):
    name = "printer"

    def __init__(
        self, cups: CupsProtocol, nats_url: str | None = None, auto_print: bool = True
    ) -> None:
        super().__init__()
        self._cups = cups
        self._nats_url = nats_url
        self.auto_print = auto_print
        # Print positioning. Composed into lp options at print time; tunable
        # live via admin UI. Driver-native options (roAdjust*, roFeedOffset,
        # roRotate) are Phomemo-specific but the same shape applies to many
        # thermal label printer drivers.
        self.media: str = "4x6"
        self.adjust_vertical: int = 0  # -20..20, neg shifts UP
        self.adjust_horizontal: int = 0  # -20..20, neg shifts LEFT
        self.feed_offset: int = 0  # -20..20, label feed position
        self.rotate: int = 0  # 0..3, 90deg steps
        self.scale_pct: int = 100
        self.lp_options: str = ""  # raw escape-hatch options
        # Physical dead zones where the print head can't mark, in mm.
        # Converted to pixels at print time based on DPI.
        self.dead_zone_top_mm: int = 3
        self.dead_zone_bottom_mm: int = 0

    def _build_lp_options(self) -> list[str]:
        """Compose structured positioning settings into a single lp arg list."""
        opts: list[str] = []
        if self.media:
            opts += ["-o", f"media={self.media}"]
        if self.scale_pct and self.scale_pct != 100:
            opts += ["-o", f"scaling={self.scale_pct}"]
        if self.adjust_vertical:
            opts += ["-o", f"roAdjustVertical={self.adjust_vertical}"]
        if self.adjust_horizontal:
            opts += ["-o", f"roAdjustHorizontal={self.adjust_horizontal}"]
        if self.feed_offset:
            opts += ["-o", f"roFeedOffset={self.feed_offset}"]
        if self.rotate:
            opts += ["-o", f"roRotate={self.rotate}"]
        if self.lp_options:
            opts += self.lp_options.split()
        return opts

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

        # PRINTER_INTENT_TEST_PAGE accepts an empty body — use a raw NATS
        # callback so we don't drop messages that don't happen to match an
        # unrelated schema like AgentReply.
        async def _on_test_page_raw(msg) -> None:
            await self._publish_state()

        await self.nats.subscribe(topics.PRINTER_INTENT_TEST_PAGE, cb=_on_test_page_raw)
        await nats_helper.subscribe_model(
            self.nats,
            topics.PRINTER_INTENT_PRINT_FILE,
            schemas.PrinterIntentPrintFile,
            self._on_print_file,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.PRINTER_INTENT_COMPOSE,
            schemas.PrinterIntentCompose,
            self._on_compose,
        )
        # Subscribe to live config changes so auto_print toggle takes effect without restart.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.printer.auto_print",
            schemas.ConfigChanged,
            self._on_config_auto_print,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.printer.lp_options",
            schemas.ConfigChanged,
            self._on_config_lp_options,
        )
        for key, attr, caster in (
            ("printer.media", "media", str),
            ("printer.adjust_vertical", "adjust_vertical", int),
            ("printer.adjust_horizontal", "adjust_horizontal", int),
            ("printer.feed_offset", "feed_offset", int),
            ("printer.rotate", "rotate", int),
            ("printer.scale_pct", "scale_pct", int),
            ("printer.dead_zone_top_mm", "dead_zone_top_mm", int),
            ("printer.dead_zone_bottom_mm", "dead_zone_bottom_mm", int),
        ):
            await nats_helper.subscribe_model(
                self.nats,
                f"{topics.CONFIG_CHANGED}.{key}",
                schemas.ConfigChanged,
                self._make_setattr_handler(key, attr, caster),
            )

        # Sync to DB on startup: control rebroadcasts every setting via
        # config.changed.<key>, hitting the same subscriber above.
        await self.request_config_snapshot()

    def _target_pixels(self) -> tuple[int, int] | None:
        """Return (width_px, height_px) for the configured media, or None
        if we don't recognize the media name (caller falls back to lp scaling)."""
        dims = _MEDIA_INCHES.get(self.media)
        if not dims:
            return None
        w_in, h_in = dims
        # Apply user scale_pct so e.g. 95 leaves a small white border.
        scale = (self.scale_pct or 100) / 100.0
        return (int(w_in * _PRINTER_DPI * scale), int(h_in * _PRINTER_DPI * scale))

    def _make_setattr_handler(self, key: str, attr: str, caster):
        async def _handler(subject: str, msg: schemas.ConfigChanged) -> None:
            try:
                value = caster(msg.value)
            except (TypeError, ValueError):
                self.log.warning("%s.bad_value value=%r", key, msg.value)
                return
            setattr(self, attr, value)
            self.log.info("%s.set value=%r", key, value)

        return _handler

    async def _on_config_auto_print(self, subject: str, msg: schemas.ConfigChanged) -> None:
        v = msg.value
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes", "on")
        self.auto_print = bool(v)
        self.log.info("printer.auto_print.set value=%s", self.auto_print)

    async def _on_config_lp_options(self, subject: str, msg: schemas.ConfigChanged) -> None:
        self.lp_options = str(msg.value or "").strip()
        self.log.info("printer.lp_options.set value=%r", self.lp_options)

    async def _publish_state(
        self, state_name: str | None = None, detail: str | None = None
    ) -> None:
        if state_name is None:
            state_name = "idle" if self._cups.default_printer() else "offline"
        await self.publish_state(
            state_name,
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
            # lp subprocess is blocking — off-thread so we don't stall the
            # event loop for the duration of the print.
            job_id = await asyncio.to_thread(self._cups.print_text, text, title=title)
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

    async def _on_compose(self, subject: str, msg: schemas.PrinterIntentCompose) -> None:
        """Composite text onto the letterhead, then print the result."""
        from .composer import compose_fortune

        lh = Path(msg.letterhead_path)
        if not _path_within_allowed_roots(lh):
            await self._publish_state(
                "error", detail=f"letterhead path not allowed: {msg.letterhead_path}"
            )
            await self._publish_state("idle")
            return
        if not lh.exists():
            await self._publish_state(
                "error", detail=f"letterhead not found: {msg.letterhead_path}"
            )
            await self._publish_state("idle")
            return
        if not self._cups.default_printer():
            await self._publish_state("offline")
            return
        try:
            # PIL composition is CPU-bound (~200ms-2s on a Pi for full card).
            # Run in a worker thread so heartbeats + other NATS handlers
            # don't stall on the event loop while we draw.
            composed = await asyncio.to_thread(
                compose_fortune,
                lh,
                body_text=msg.text,
                lucky_subway_stop=msg.lucky_subway_stop,
                lucky_numbers=msg.lucky_numbers,
            )
        except Exception as e:
            self.log.warning("compose.failed error=%s", e)
            await self._publish_state("error", detail=str(e))
            await self._publish_state("idle")
            return
        await self._publish_state("printing")
        try:
            target_px = self._target_pixels()
            dz_top_px = int(self.dead_zone_top_mm * _PRINTER_DPI / 25.4)
            dz_bot_px = int(self.dead_zone_bottom_mm * _PRINTER_DPI / 25.4)
            # PIL resize + lp subprocess (potentially 30s timeout) — also
            # off-thread so the event loop stays responsive.
            job_id = await asyncio.to_thread(
                self._cups.print_file,
                composed,
                title=msg.title or "lafufu fortune",
                extra_lp_options=self._build_lp_options(),
                target_size_px=target_px,
                dead_zone_top_px=dz_top_px,
                dead_zone_bottom_px=dz_bot_px,
            )
            await nats_helper.publish_model(
                self.nats,
                topics.PRINTER_EVENT_JOB_DONE,
                schemas.PrinterEvent(event="job_done", job_id=job_id),
            )
        except Exception as e:
            self.log.warning("compose.print_failed error=%s", e)
            await self._publish_state("error", detail=str(e))
            await self._publish_state("idle")
            return
        finally:
            # Clean up the composed temp file regardless of outcome so /tmp
            # doesn't fill with stale fortune PNGs over the printer's lifetime.
            if composed is not None:
                with contextlib.suppress(OSError):
                    composed.unlink(missing_ok=True)
        await self._publish_state("idle")

    async def _on_print_file(self, subject: str, msg: schemas.PrinterIntentPrintFile) -> None:
        """Send an image file (e.g. the uploaded letterhead) directly to lp.
        The path must resolve inside the printer data dir or temp dir — a
        NATS publisher must not be able to print arbitrary files."""
        path = Path(msg.path)
        if not _path_within_allowed_roots(path):
            await self._publish_state("error", detail=f"file path not allowed: {msg.path}")
            await self._publish_state("idle")
            return
        if not path.exists():
            await self._publish_state("error", detail=f"file not found: {msg.path}")
            await self._publish_state("idle")
            return
        if not self._cups.default_printer():
            await self._publish_state("offline")
            return
        await self._publish_state("printing")
        try:
            # Pre-resize the image to the printer's exact pixel dimensions so
            # we don't depend on CUPS fit-to-page. Falls back to driver scaling
            # if we don't know the media size.
            target_px = self._target_pixels()
            dz_top_px = int(self.dead_zone_top_mm * _PRINTER_DPI / 25.4)
            dz_bot_px = int(self.dead_zone_bottom_mm * _PRINTER_DPI / 25.4)
            # Off-thread: PIL resize + lp subprocess (up to 30s) would stall
            # heartbeats and config events otherwise.
            job_id = await asyncio.to_thread(
                self._cups.print_file,
                path,
                title=msg.title or path.name,
                extra_lp_options=self._build_lp_options(),
                target_size_px=target_px,
                dead_zone_top_px=dz_top_px,
                dead_zone_bottom_px=dz_bot_px,
            )
            await nats_helper.publish_model(
                self.nats,
                topics.PRINTER_EVENT_JOB_DONE,
                schemas.PrinterEvent(event="job_done", job_id=job_id),
            )
        except Exception as e:
            self.log.warning("print_file.failed error=%s", e)
            await self._publish_state("error", detail=str(e))
            await self._publish_state("idle")
            return
        await self._publish_state("idle")
