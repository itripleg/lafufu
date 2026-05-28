"""ControlService: hosts FastAPI + tracks heartbeat-derived service status + last pose."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import uvicorn
from lafufu_shared import nats_helper, schemas, settings, topics
from lafufu_shared.base_service import BaseService
from sqlmodel import Session, select

from .animation.compile import compile_expression, required_frame_names
from .animation.seed import seed_animations
from .api.app import create_app
from .api.ws_bridge import WsBridge
from .bootstrap import seed_default_settings
from .db import create_engine_for_path, init_db
from .models.chat import ChatMessage
from .models.expression import Expression
from .models.frame import Frame
from .models.setting import Setting

_log = logging.getLogger(__name__)


async def _publish_idle_expression(engine, nats_client) -> None:
    """Look up the seeded idle expression, compile it, publish to animator.

    Called once at control startup so the animator can cache the idle payload
    as its fallback and begin looping immediately without waiting for an
    external play_expression publish.
    """
    with Session(engine) as s:
        e = s.get(Expression, "idle")
        if e is None:
            return  # No idle seeded yet (fresh, unseeded DB)
        need = list(required_frame_names(e))
        frames = {f.name: f for f in s.exec(select(Frame).where(Frame.name.in_(need))).all()}
        if any(n not in frames for n in need):
            return  # Broken seed — don't crash, just skip
        payload = compile_expression(e, frames)
    await nats_helper.publish_model(nats_client, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)


def resolve_emotion_to_play_intent(engine, emotion: str | None) -> dict | None:
    """Return the AnimatorIntentPlayExpression payload (as a dict) for the
    expression named `emotion`, or None if the name is empty/unknown/broken.
    Pure: caller is responsible for publishing the result on NATS.

    "Broken" includes: missing referenced frame rows, malformed steps_json,
    or any pydantic validation error during compile. Returning None for all
    broken cases lets the caller log a single 'unknown' warning instead of
    surfacing a stack trace from the NATS wrapper for a corrupted row."""
    name = (emotion or "").strip()
    if not name:
        return None
    with Session(engine) as s:
        e = s.get(Expression, name)
        if e is None:
            return None
        need = list(required_frame_names(e))
        frames = {f.name: f for f in s.exec(select(Frame).where(Frame.name.in_(need))).all()}
        missing = [n for n in need if n not in frames]
        if missing:
            _log.warning(
                "expression.broken.missing_frames name=%r missing=%s",
                name,
                missing,
            )
            return None
        try:
            return compile_expression(e, frames).model_dump()
        except Exception:
            _log.warning(
                "expression.broken.compile_error name=%r — DB row exists but compile failed",
                name,
                exc_info=True,
            )
            return None


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
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        nats_url: str | None = None,
        api_token: str = "",
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._nats_url = nats_url
        self._api_token = api_token
        self._server: uvicorn.Server | None = None
        self._app = None
        self._engine = None
        # Arrival time of the most recent agent.transcript; cleared by the reply that consumes it.
        self._last_transcript_at: datetime | None = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        engine = create_engine_for_path(str(settings.db_path()))
        self._engine = engine
        init_db(engine)
        seed_default_settings(engine)
        seed_animations(engine)
        # Publish the idle expression so the animator can cache it as its fallback —
        # without this, the animator sits frozen at startup pose until something else
        # publishes a play_expression. NOTE: this is a fire-and-forget publish that
        # races the animator's own subscription startup; the animator is responsible
        # for re-requesting via `animator.request.idle` if it missed this one (see
        # the on_request_idle subscriber below).
        await _publish_idle_expression(engine, self.nats)

        # Request-reply handshake: animator publishes `animator.request.idle` after
        # its own subscriptions complete; we re-publish the idle expression. This
        # closes the cold-boot race where the startup publish above lands before
        # the animator has subscribed. Also handles animator-restart-mid-session
        # (animator re-requests on its new subscribe). See
        # docs/superpowers/specs/2026-05-26-idle-bootstrap-readiness-design.md.
        async def on_request_idle(_msg) -> None:
            _log.info("animator.request.idle received — publishing idle expression")
            await _publish_idle_expression(engine, self.nats)

        await self.nats.subscribe(topics.ANIMATOR_REQUEST_IDLE, cb=on_request_idle)

        loop = asyncio.get_running_loop()

        def publish_sync(subject: str, payload: dict) -> None:
            """Schedule a publish from the synchronous FastAPI handler thread.

            NATS publish is fire-and-forget; if the broker is down the coroutine
            raises and the Future stores the exception. Without an explicit
            done-callback the failure would garbage-collect silently and the
            HTTP handler still returns 2xx. Log on failure so a dropped intent
            is visible in the journal."""
            data = json.dumps(payload).encode("utf-8")
            fut = asyncio.run_coroutine_threadsafe(self.nats.publish(subject, data), loop)

            def _on_done(f):
                exc = f.exception()
                if exc is not None:
                    _log.warning(
                        "nats.publish_sync.failed subject=%s error=%s",
                        subject,
                        exc,
                    )

            fut.add_done_callback(_on_done)

        self._app = create_app(engine=engine, nats_publish=publish_sync, api_token=self._api_token)
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

        async def on_transcript(subject: str, msg: schemas.AgentTranscript) -> None:
            self._last_transcript_at = datetime.now(UTC)
            await self._persist_chat(
                engine,
                role="user",
                text=msg.text,
                emotion=None,
                source=None,
                reply_delay_ms=None,
            )

        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript,
            on_transcript,
        )

        async def on_reply(subject: str, msg: schemas.AgentReply) -> None:
            role = "puppet" if msg.source == "puppet" else "lafufu"
            delay_ms = self._compute_reply_delay_ms(msg.source)
            self._last_transcript_at = None
            await self._persist_chat(
                engine,
                role=role,
                text=msg.text,
                emotion=msg.emotion,
                source=msg.source,
                reply_delay_ms=delay_ms,
            )
            payload = resolve_emotion_to_play_intent(engine, msg.emotion)
            if payload is None:
                if (msg.emotion or "").strip():
                    self.log.warning(
                        "agent.reply emotion=%r not found in expression registry; skipping pose",
                        msg.emotion,
                    )
                return
            data = json.dumps(payload).encode("utf-8")
            await self.nats.publish(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, data)

        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_REPLY,
            schemas.AgentReply,
            on_reply,
        )

        # Also track server's notion of "now" so the snapshot can return a clock
        # the client can trust without skew worries.
        self._app.state.server_now = lambda: time.time()

        # Rebroadcast every setting as config.changed.<key> on demand. Services
        # publish CONFIG_SNAPSHOT_REQUEST on their own startup so they sync to
        # the DB instead of drifting from env defaults.
        #
        # Coalesce: if 4 services boot within ~100ms of each other (cold-boot
        # case), we'd otherwise fire 4 full rebroadcasts back-to-back. The
        # coalesce window debounces them into a single broadcast — every
        # subscribed service still receives the result.
        self._snapshot_pending = False
        SNAPSHOT_COALESCE_S = 0.15

        async def on_snapshot_request(_msg) -> None:
            if self._snapshot_pending:
                return
            self._snapshot_pending = True
            try:
                await asyncio.sleep(SNAPSHOT_COALESCE_S)
                await self._rebroadcast_all_settings(engine)
            finally:
                self._snapshot_pending = False

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

    def _compute_reply_delay_ms(self, source: str) -> int | None:
        """Transcript-to-reply delay in ms; puppet replies and stale (>=120 s) gaps yield None."""
        if source == "puppet" or self._last_transcript_at is None:
            return None
        gap = (datetime.now(UTC) - self._last_transcript_at).total_seconds()
        if gap < 0 or gap >= 120:
            return None
        return int(gap * 1000)

    async def _persist_chat(
        self,
        engine,
        *,
        role: str,
        text: str,
        emotion: str | None,
        source: str | None,
        reply_delay_ms: int | None,
    ) -> None:
        def _insert() -> None:
            with Session(engine) as s:
                s.add(
                    ChatMessage(
                        role=role,
                        text=text,
                        emotion=emotion,
                        source=source,
                        reply_delay_ms=reply_delay_ms,
                    )
                )
                s.commit()

        try:
            await asyncio.to_thread(_insert)
        except Exception as e:
            self.log.warning("chat.persist.failed role=%s error=%s", role, e)

    async def main_loop(self) -> None:
        assert self._server
        serve_task = asyncio.create_task(self._server.serve())
        await self._shutdown.wait()
        self._server.should_exit = True
        await serve_task

    async def on_shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._engine is not None:
            await asyncio.to_thread(self._engine.dispose)
