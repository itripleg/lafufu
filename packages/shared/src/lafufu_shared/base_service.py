"""BaseService: lifecycle, signal handling, heartbeat, error reporting."""

import asyncio
import contextlib
import logging
import signal
import time
from typing import ClassVar

from . import logging_setup, nats_helper, settings, topics
from .schemas import ServiceName, SystemError, SystemHeartbeat, SystemServiceEvent

log = logging.getLogger(__name__)


class BaseService:
    """Subclass and override on_startup, on_shutdown, main_loop."""

    name: ClassVar[ServiceName] = "agent"  # subclasses set this
    heartbeat_interval_s: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self.nats = None  # set in run()
        self._shutdown = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None
        self._start_ts: float = 0.0
        self.log = logging.getLogger(f"lafufu.{self.name}")

    @property
    def nats_url(self) -> str:
        """Override in tests or services that take a custom URL."""
        return settings.nats_url()

    # --- Overridables ---

    async def on_startup(self) -> None:
        """Connect to hardware, load models, etc. Runs after NATS connect."""

    async def on_shutdown(self) -> None:
        """Release hardware, save state. Runs after main_loop exits."""

    async def main_loop(self) -> None:
        """The service's main work. Default: wait for shutdown."""
        await self._shutdown.wait()

    # --- Internals ---

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await nats_helper.publish_model(
                    self.nats,
                    f"{topics.SYSTEM_HEARTBEAT}.{self.name}",
                    SystemHeartbeat(
                        service=self.name,
                        ts=time.time(),
                        uptime_s=time.monotonic() - self._start_ts,
                    ),
                )
            except Exception as e:
                self.log.warning("heartbeat.failed error=%s", e)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=self.heartbeat_interval_s)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                # Windows lacks add_signal_handler for some signals; skip silently
                loop.add_signal_handler(sig, self._shutdown.set)

    async def request_config_snapshot(self) -> None:
        """Ask control to re-publish every setting as config.changed.<key>.

        Call this at the END of on_startup, AFTER subscribing to every
        config.changed.<key> topic this service cares about. The re-broadcast
        flows through the same subscribers used for live admin toggles, so the
        service ends up synced to the DB on every restart without drift.

        If control isn't up yet, the request is silently dropped — the service
        falls back to whatever env defaults it was constructed with until
        someone toggles the setting in the admin UI.
        """
        try:
            await self.nats.publish(topics.CONFIG_SNAPSHOT_REQUEST, b"")
        except Exception as e:
            self.log.warning("config.snapshot_request.failed error=%s", e)

    async def _publish_service_event(self, event_subject: str) -> None:
        try:
            await nats_helper.publish_model(
                self.nats,
                event_subject,
                SystemServiceEvent(service=self.name, event=event_subject.rsplit(".", 1)[-1]),  # type: ignore[arg-type]
            )
        except Exception as e:
            self.log.warning("service_event.publish_failed event=%s error=%s", event_subject, e)

    async def run(self) -> None:
        logging_setup.configure(self.name)
        self._install_signal_handlers()
        self._start_ts = time.monotonic()

        self.nats = await nats_helper.connect_with_retry(self.nats_url, name=f"lafufu-{self.name}")
        await self._publish_service_event(topics.SYSTEM_SERVICE_STARTING)

        try:
            await self.on_startup()
            await self._publish_service_event(topics.SYSTEM_SERVICE_READY)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            await self.main_loop()
        except Exception as e:
            self.log.exception("service.main.crashed")
            with contextlib.suppress(Exception):
                await nats_helper.publish_model(
                    self.nats,
                    f"{topics.SYSTEM_ERROR}.{self.name}.unhandled",
                    SystemError(service=self.name, error_kind="unhandled", message=str(e)),
                )
            raise
        finally:
            self._shutdown.set()
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._heartbeat_task
            try:
                await self.on_shutdown()
            except Exception:
                self.log.exception("on_shutdown.failed")
            with contextlib.suppress(Exception):
                await self._publish_service_event(topics.SYSTEM_SERVICE_STOPPED)
                await self.nats.drain()
