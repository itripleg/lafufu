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

        async def on_hb(subject: str, msg: schemas.SystemHeartbeat) -> None:
            self._app.state.service_status[msg.service] = {
                "service": msg.service,
                "last_seen": time.time(),
                "uptime_s": msg.uptime_s,
            }

        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.SYSTEM_HEARTBEAT}.>",
            schemas.SystemHeartbeat,
            on_hb,
        )

        async def on_pose(subject: str, msg: schemas.AnimatorPose) -> None:
            self._app.state.last_pose = msg.model_dump()

        await nats_helper.subscribe_model(
            self.nats,
            topics.ANIMATOR_POSE,
            schemas.AnimatorPose,
            on_pose,
        )

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
