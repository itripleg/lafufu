"""ControlService: hosts FastAPI + tracks heartbeat-derived service status + last pose."""

import asyncio
import json
import time

import uvicorn
from lafufu_shared import nats_helper, schemas, settings, topics
from lafufu_shared.base_service import BaseService
from sqlmodel import Session, select

from .api.app import create_app
from .api.ws_bridge import WsBridge
from .bootstrap import seed_default_settings
from .db import create_engine_for_path, init_db
from .models.setting import Setting


def _decode_setting_value(raw: str, value_type: str):
    """Mirror of admin payload encoding so snapshot rebroadcasts match
    config.changed publishes from PUT/PATCH /api/settings."""
    if value_type == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    if value_type == "float":
        try:
            return float(raw)
        except ValueError:
            return raw
    if value_type == "bool":
        return raw.strip().lower() in ("true", "1", "yes", "on")
    if value_type == "json":
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw
    return raw


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

        # Rebroadcast every setting as config.changed.<key> on demand. Services
        # publish CONFIG_SNAPSHOT_REQUEST on their own startup so they sync to
        # the DB instead of drifting from env defaults (e.g. printer's auto_print
        # would otherwise stay 'true' from env even when DB has it set to false).
        async def on_snapshot_request(_msg) -> None:
            await self._rebroadcast_all_settings(engine)

        await self.nats.subscribe(topics.CONFIG_SNAPSHOT_REQUEST, cb=on_snapshot_request)
        # Also rebroadcast on control's own startup, in case other services are
        # already up when control restarts.
        await self._rebroadcast_all_settings(engine)

        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

    async def _rebroadcast_all_settings(self, engine) -> None:
        with Session(engine) as s:
            rows = list(s.exec(select(Setting)).all())
        for row in rows:
            payload = {
                "key": row.key,
                "value": _decode_setting_value(row.value, row.value_type),
                "source": "snapshot",
            }
            try:
                await self.nats.publish(
                    f"{topics.CONFIG_CHANGED}.{row.key}",
                    json.dumps(payload).encode("utf-8"),
                )
            except Exception as e:
                self.log.warning("config.snapshot.publish_failed key=%s error=%s", row.key, e)

    async def main_loop(self) -> None:
        assert self._server
        serve_task = asyncio.create_task(self._server.serve())
        await self._shutdown.wait()
        self._server.should_exit = True
        await serve_task

    async def on_shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
