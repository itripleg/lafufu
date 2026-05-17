"""AnimatorService: subscribes to intents and RMS, drives the DXL bus."""

import asyncio
import contextlib
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from . import expressions, lipsync, pose


class DxlBusProtocol(Protocol):
    def write(self, name: str, position: int) -> None: ...
    def read(self, name: str) -> int: ...
    def disable_torque(self) -> None: ...
    def open(self) -> None: ...


class AnimatorService(BaseService):
    name = "animator"
    heartbeat_interval_s = 5.0

    def __init__(self, bus: DxlBusProtocol, nats_url: str | None = None) -> None:
        super().__init__()
        self._bus = bus
        self._nats_url = nats_url
        self._envelope = lipsync.LipsyncEnvelope()
        self._current_pose = pose.idle_pose()
        self._has_u2d2 = True  # set False on disconnect
        self._last_rms_ts = 0.0
        self._pose_publish_task: asyncio.Task | None = None
        self._lipsync_watchdog_task: asyncio.Task | None = None

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        # Try to open the real bus; ignore if it's already opened (e.g. fake)
        try:
            self._bus.open()  # type: ignore[call-arg]
        except (AttributeError, TypeError):
            # Fakes don't need open()
            pass
        except ConnectionError as e:
            self.log.warning("dxl.open.failed error=%s", e)
            self._has_u2d2 = False

        try:
            self._move_to_pose(self._current_pose)
        except OSError:
            self._has_u2d2 = False

        await self._publish_state("idle" if self._has_u2d2 else "degraded")

        # Subscribe to intents
        await nats_helper.subscribe_model(
            self.nats,
            topics.ANIMATOR_INTENT_PREVIEW,
            schemas.AnimatorIntentPreview,
            self._on_preview,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.ANIMATOR_INTENT_SET_POSE,
            schemas.AnimatorIntentSetPose,
            self._on_set_pose,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
            schemas.AnimatorIntentPlayExpression,
            self._on_play_expression,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_TTS_RMS,
            schemas.AgentTtsRms,
            self._on_tts_rms,
        )
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_REPLY,
            schemas.AgentReply,
            self._on_agent_reply,
        )

        # Background tasks
        self._pose_publish_task = asyncio.create_task(self._pose_publish_loop())
        self._lipsync_watchdog_task = asyncio.create_task(self._lipsync_watchdog())

    async def on_shutdown(self) -> None:
        if self._pose_publish_task:
            self._pose_publish_task.cancel()
        if self._lipsync_watchdog_task:
            self._lipsync_watchdog_task.cancel()
        with contextlib.suppress(Exception):
            self._bus.disable_torque()

    async def _publish_state(self, state_name: str, detail: str | None = None) -> None:
        topic = f"{topics.ANIMATOR_STATE}.{state_name}"
        await nats_helper.publish_model(
            self.nats,
            topic,
            schemas.AnimatorState(state=state_name, detail=detail, has_u2d2=self._has_u2d2),  # type: ignore[arg-type]
        )

    def _move_to_pose(self, p: schemas.AnimatorPose) -> None:
        for name, value in (
            ("head_lr", p.head_lr),
            ("head_ud", p.head_ud),
            ("eye", p.eye),
            ("jaw", p.jaw),
            ("brow", p.brow),
        ):
            try:
                self._bus.write(name, value)
            except OSError:
                self._has_u2d2 = False
                raise
        self._current_pose = p

    async def _safe_apply(self, target_pose: schemas.AnimatorPose) -> None:
        try:
            self._move_to_pose(target_pose)
            if not self._has_u2d2:
                # Recovered
                self._has_u2d2 = True
                await self._publish_state("idle")
        except OSError as e:
            self.log.warning("dxl.write.failed error=%s", e)
            self._has_u2d2 = False
            await self._publish_state("degraded", detail=str(e))

    async def _on_preview(self, subject: str, msg: schemas.AnimatorIntentPreview) -> None:
        new = self._current_pose.model_copy(
            update={msg.name: pose.clamp_dxl(msg.name, msg.position)}
        )
        await self._safe_apply(new)

    async def _on_set_pose(self, subject: str, msg: schemas.AnimatorIntentSetPose) -> None:
        await self._safe_apply(msg.pose)

    async def _on_play_expression(
        self, subject: str, msg: schemas.AnimatorIntentPlayExpression
    ) -> None:
        offsets = expressions.get_offsets(msg.name, msg.intensity)
        target = expressions.apply_offsets(pose.idle_pose(), offsets)
        await self._safe_apply(target)
        await nats_helper.publish_model(
            self.nats,
            topics.ANIMATOR_EVENT_GESTURE_DONE,
            schemas.AnimatorEvent(event="gesture_done", name=msg.name),
        )

    async def _on_agent_reply(self, subject: str, msg: schemas.AgentReply) -> None:
        """When agent emits a reply with an emotion, set the matching expression."""
        offsets = expressions.get_offsets(msg.emotion, intensity=1.0)
        target = expressions.apply_offsets(pose.idle_pose(), offsets)
        await self._safe_apply(target)

    async def _on_tts_rms(self, subject: str, msg: schemas.AgentTtsRms) -> None:
        # Drive jaw via envelope
        dt = msg.ts - self._last_rms_ts if msg.ts > self._last_rms_ts else 0.04
        self._last_rms_ts = msg.ts
        smoothed = self._envelope.step(target=msg.mouth_target, dt=dt)
        jaw_pos = lipsync.rms_to_jaw_dxl(smoothed)
        new = self._current_pose.model_copy(update={"jaw": jaw_pos})
        await self._safe_apply(new)

    async def _pose_publish_loop(self) -> None:
        """Publish current pose at 20 Hz for live UI."""
        while not self._shutdown.is_set():
            try:
                await nats_helper.publish_model(self.nats, topics.ANIMATOR_POSE, self._current_pose)
            except Exception as e:
                self.log.warning("pose.publish.failed error=%s", e)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=0.05)

    async def _lipsync_watchdog(self) -> None:
        """If no RMS for 500ms, close the jaw."""
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(0.2)
                if self._envelope.value > 0.01 and time.monotonic() - self._last_rms_ts > 0.5:
                    self._envelope.reset()
                    new = self._current_pose.model_copy(update={"jaw": pose.MOUTH_CLOSE_DXL})
                    await self._safe_apply(new)
            except Exception as e:
                self.log.warning("lipsync_watchdog.error error=%s", e)
