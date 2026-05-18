"""ControlService: hosts FastAPI + tracks heartbeat-derived service status + last pose."""

import asyncio
import json
import time

import uvicorn
from lafufu_shared import nats_helper, schemas, settings, topics
from lafufu_shared.base_service import BaseService

from .api.app import create_app
from .api.ws_bridge import WsBridge
from .bootstrap import seed_default_settings
from .db import create_engine_for_path, init_db


class ControlService(BaseService):
    name = "control"

    def __init__(
        self, host: str = "0.0.0.0", port: int = 8080, nats_url: str | None = None
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._nats_url = nats_url
        self._server: uvicorn.Server | None = None
        self._app = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        engine = create_engine_for_path(str(settings.db_path()))
        init_db(engine)
        seed_default_settings(engine)
        loop = asyncio.get_running_loop()

        def publish_sync(subject: str, payload: dict) -> None:
            """Schedule a publish from the synchronous FastAPI handler thread."""
            data = json.dumps(payload).encode("utf-8")
            asyncio.run_coroutine_threadsafe(self.nats.publish(subject, data), loop)

        self._app = create_app(engine=engine, nats_publish=publish_sync)
        self._app.state.service_status = {}
        self._app.state.last_pose = None

        bridge = WsBridge(self.nats)
        bridge.mount(self._app)

        def _ensure_row(name: str) -> dict:
            row = self._app.state.service_status.get(name)
            if row is None:
                row = {"service": name, "last_seen": None, "uptime_s": 0}
                self._app.state.service_status[name] = row
            return row

        async def on_hb(subject: str, msg: schemas.SystemHeartbeat) -> None:
            row = _ensure_row(msg.service)
            row["last_seen"] = time.time()
            row["uptime_s"] = msg.uptime_s

        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.SYSTEM_HEARTBEAT}.>",
            schemas.SystemHeartbeat,
            on_hb,
        )

        # Track operational state per service (animator.state.idle, agent.state.warming, ...)
        # We subscribe with a raw NATS callback so we don't have to know each service's
        # schema; the topic itself encodes service + state.
        async def on_state_raw(msg) -> None:
            parts = msg.subject.split(".")
            if len(parts) < 3 or parts[1] != "state":
                return
            name, state = parts[0], ".".join(parts[2:])
            row = _ensure_row(name)
            row["state"] = state

        await self.nats.subscribe("*.state.>", cb=on_state_raw)

        # Lifecycle events (BaseService publishes system.service.{starting|ready|...} for every service)
        async def on_lifecycle(subject: str, msg: schemas.SystemServiceEvent) -> None:
            row = _ensure_row(msg.service)
            row["lifecycle"] = msg.event

        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.SYSTEM_SERVICE}.>",
            schemas.SystemServiceEvent,
            on_lifecycle,
        )

        async def on_pose(subject: str, msg: schemas.AnimatorPose) -> None:
            self._app.state.last_pose = msg.model_dump()

        await nats_helper.subscribe_model(
            self.nats,
            topics.ANIMATOR_POSE,
            schemas.AnimatorPose,
            on_pose,
        )

        # Also track server's notion of "now" so the snapshot can return a clock
        # the client can trust without skew worries.
        self._app.state.server_now = lambda: time.time()

        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

    async def main_loop(self) -> None:
        assert self._server
        serve_task = asyncio.create_task(self._server.serve())
        await self._shutdown.wait()
        self._server.should_exit = True
        await serve_task

    async def on_shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
