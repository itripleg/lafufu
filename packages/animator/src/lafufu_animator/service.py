"""AnimatorService: subscribes to intents and RMS, drives the DXL bus."""

import asyncio
import contextlib
import math
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


# Per-servo first-order easing time constants (seconds).
# Smaller = snappier. Jaw must be near-instant for lipsync responsiveness.
DEFAULT_TAUS: dict[str, float] = {
    "head_lr": 0.25,
    "head_ud": 0.25,
    "eye": 0.12,
    "brow": 0.10,
    "jaw": 0.02,
}


class AnimatorService(BaseService):
    name = "animator"
    heartbeat_interval_s = 5.0

    def __init__(
        self,
        bus: DxlBusProtocol,
        nats_url: str | None = None,
        taus: dict[str, float] | None = None,
        stepper_hz: float = 30.0,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._nats_url = nats_url
        self._envelope = lipsync.LipsyncEnvelope()
        # _current_pose = what we last actually wrote to the bus.
        # _target_pose = where intent/idle-anim/etc wants us to go.
        # _stepper_loop eases current toward target at stepper_hz and writes.
        self._current_pose = pose.idle_pose()
        self._target_pose = pose.idle_pose()
        # Per-servo idle-center overrides from settings (animator.<servo>.default).
        # Empty = use the hardcoded constants in pose.idle_pose(). When the
        # operator saves a slider in the admin UI, the new value lands here
        # and any future call to _effective_idle_pose() picks it up.
        self._idle_overrides: dict[str, int] = {}
        self._taus = taus or DEFAULT_TAUS
        self._stepper_dt = 1.0 / max(1.0, stepper_hz)
        self._has_u2d2 = True  # set False on disconnect
        self._last_rms_ts = 0.0
        self._last_intent_mono = 0.0  # monotonic timestamp of last intent/preview/reply
        self.idle_animation_enabled = True  # toggleable via settings (Phase 0 default: on)
        # Active looping expression — drives _target_pose at 20 Hz via
        # _expression_animation_loop. Set by play_expression / agent reply,
        # cleared by neutral / preview / set_pose / auto-expiry.
        self._current_expression: str | None = None
        self._expression_intensity: float = 1.0
        self._expression_started_mono: float = 0.0
        self._pose_publish_task: asyncio.Task | None = None
        self._lipsync_watchdog_task: asyncio.Task | None = None
        self._idle_animation_task: asyncio.Task | None = None
        self._expression_animation_task: asyncio.Task | None = None
        self._stepper_task: asyncio.Task | None = None

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

        # Enable torque so the servos actually hold and move to commanded positions.
        # Without this, writes silently no-op physically — code thinks it commanded a
        # position but the servo is freewheeling.
        if self._has_u2d2:
            try:
                self._bus.enable_torque()
                self.log.info("dxl.torque.enabled")
            except Exception as e:
                self.log.warning("dxl.torque.enable_failed error=%s", e)
                self._has_u2d2 = False

        # Seed current+target at idle and write once so the servos snap there.
        # Overrides arrive shortly via the snapshot — handlers update target.
        self._target_pose = self._effective_idle_pose()
        try:
            self._move_to_pose(self._target_pose)
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

        # Per-servo idle defaults — operator-tunable via admin sliders.
        # When a value arrives, store it AND update target_pose so the servo
        # eases to the new center immediately.
        for servo in ("head_lr", "head_ud", "eye", "jaw", "brow"):
            await nats_helper.subscribe_model(
                self.nats,
                f"{topics.CONFIG_CHANGED}.animator.{servo}.default",
                schemas.ConfigChanged,
                self._make_idle_override_handler(servo),
            )

        # Subscribe to settings changes so idle animation can be toggled live
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.animator.idle_animation.enabled",
            schemas.ConfigChanged,
            self._on_config_idle_animation,
        )

        # Sync to DB on startup so idle_animation_enabled reflects admin's
        # current value rather than the in-code default.
        await self.request_config_snapshot()

        # Background tasks
        self._pose_publish_task = asyncio.create_task(self._pose_publish_loop())
        self._lipsync_watchdog_task = asyncio.create_task(self._lipsync_watchdog())
        self._idle_animation_task = asyncio.create_task(self._idle_animation_loop())
        self._expression_animation_task = asyncio.create_task(self._expression_animation_loop())
        self._stepper_task = asyncio.create_task(self._stepper_loop())

    async def on_shutdown(self) -> None:
        for t in (
            self._pose_publish_task,
            self._lipsync_watchdog_task,
            self._idle_animation_task,
            self._expression_animation_task,
            self._stepper_task,
        ):
            if t:
                t.cancel()
        with contextlib.suppress(Exception):
            self._bus.disable_torque()

    def _effective_idle_pose(self) -> schemas.AnimatorPose:
        """Idle pose with any operator-saved per-servo defaults applied."""
        base = pose.idle_pose()
        if not self._idle_overrides:
            return base
        return base.model_copy(update=self._idle_overrides)

    def _make_idle_override_handler(self, servo: str):
        async def _h(subject: str, msg: schemas.ConfigChanged) -> None:
            try:
                value = int(msg.value)
            except (TypeError, ValueError):
                self.log.warning("animator.%s.default.bad_value value=%r", servo, msg.value)
                return
            clamped = pose.clamp_dxl(servo, value)
            self._idle_overrides[servo] = clamped
            # Move the target toward the new idle center so the change is
            # visible right away — subsequent intents/expressions/idle anim
            # will pick it up regardless via _effective_idle_pose().
            await self._safe_apply(self._target_pose.model_copy(update={servo: clamped}))
            self.log.info("animator.%s.default.set value=%d", servo, clamped)

        return _h

    async def _on_config_idle_animation(self, subject: str, msg: schemas.ConfigChanged) -> None:
        # value may be bool, "true"/"false" str, or 0/1
        v = msg.value
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes", "on")
        self.idle_animation_enabled = bool(v)
        self.log.info("idle_animation.set enabled=%s", self.idle_animation_enabled)

    async def _publish_state(self, state_name: str, detail: str | None = None) -> None:
        await self.publish_state(
            state_name,
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
        """Set the target pose. The stepper loop eases current → target and writes."""
        self._target_pose = target_pose

    async def _safe_apply_immediate(self, target_pose: schemas.AnimatorPose) -> None:
        """Snap directly to a pose, bypassing easing. Used for startup pose only."""
        try:
            self._move_to_pose(target_pose)
            self._target_pose = target_pose
            if not self._has_u2d2:
                self._has_u2d2 = True
                await self._publish_state("idle")
        except OSError as e:
            self.log.warning("dxl.write.failed error=%s", e)
            self._has_u2d2 = False
            await self._publish_state("degraded", detail=str(e))

    async def _stepper_loop(self) -> None:
        """Ease current pose toward target pose at 30Hz and write to bus.

        Each servo uses its own time constant (DEFAULT_TAUS). Jaw is near-zero
        tau so it tracks lipsync RMS closely; head/eye/brow are smoothed for
        non-tweaky motion.
        """
        last = time.monotonic()
        while not self._shutdown.is_set():
            try:
                if self._has_u2d2:
                    now = time.monotonic()
                    dt = now - last
                    last = now

                    # First-order exponential easing per servo
                    new_vals: dict[str, int] = {}
                    for name in ("head_lr", "head_ud", "eye", "jaw", "brow"):
                        tau = self._taus.get(name, 0.15)
                        a = 1.0 - math.exp(-dt / max(1e-6, tau))
                        cv = float(getattr(self._current_pose, name))
                        tv = float(getattr(self._target_pose, name))
                        nv = cv + (tv - cv) * a
                        new_vals[name] = pose.clamp_dxl(name, nv)

                    new_pose = schemas.AnimatorPose(**new_vals)
                    try:
                        self._move_to_pose(new_pose)
                    except OSError as e:
                        self.log.warning("dxl.write.failed error=%s", e)
                        self._has_u2d2 = False
                        await self._publish_state("degraded", detail=str(e))
            except Exception as e:
                self.log.warning("stepper.error error=%s", e)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=self._stepper_dt)

    async def _on_preview(self, subject: str, msg: schemas.AnimatorIntentPreview) -> None:
        self._last_intent_mono = time.monotonic()
        # Operator is hand-driving a servo — kill any looping expression so
        # the two don't fight for _target_pose.
        self._current_expression = None
        new = self._current_pose.model_copy(
            update={msg.name: pose.clamp_dxl(msg.name, msg.position)}
        )
        await self._safe_apply(new)

    async def _on_set_pose(self, subject: str, msg: schemas.AnimatorIntentSetPose) -> None:
        self._last_intent_mono = time.monotonic()
        self._current_expression = None
        await self._safe_apply(msg.pose)

    async def _on_play_expression(
        self, subject: str, msg: schemas.AnimatorIntentPlayExpression
    ) -> None:
        self._last_intent_mono = time.monotonic()
        # "neutral" is the cancel command — clear current expression and let
        # idle take back over. Fire gesture_done so the UI clears its active
        # state pill.
        if msg.name == "neutral":
            previous = self._current_expression
            self._current_expression = None
            if previous is not None:
                await nats_helper.publish_model(
                    self.nats,
                    topics.ANIMATOR_EVENT_GESTURE_DONE,
                    schemas.AnimatorEvent(event="gesture_done", name=previous),
                )
            return
        # Set the active expression; the animation loop picks it up on its
        # next tick and starts driving _target_pose. gesture_done is emitted
        # when the expression auto-expires or is replaced.
        self._current_expression = msg.name
        self._expression_intensity = max(0.0, min(1.0, msg.intensity))
        self._expression_started_mono = time.monotonic()

    async def _on_agent_reply(self, subject: str, msg: schemas.AgentReply) -> None:
        """When agent emits a reply with an emotion, set the matching expression."""
        self._last_intent_mono = time.monotonic()
        if msg.emotion == "neutral":
            self._current_expression = None
            return
        self._current_expression = msg.emotion
        self._expression_intensity = 1.0
        self._expression_started_mono = time.monotonic()

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

    async def _idle_animation_loop(self) -> None:
        """Living-presence motion when idle — ported from the original monolith's
        _idle_loop. Multi-segment random sinusoidal motion with occasional pauses.

        Each segment lasts 2-5s with re-randomized amplitudes/frequencies/phases
        per servo; 30% of segments are pause segments holding the idle pose.
        Amplitudes are fractions of each servo's full range so motion is visible:
          head_lr ±6%, head_ud ±5%, eye ±45%, brow ±16%.

        Deferred while a user intent is recent (1.5s) or Lafufu is speaking (0.6s).
        Disabled entirely if servo bus is degraded.
        """
        import math
        import random

        HEAD_LR_RANGE = abs(pose.DXL_HEAD_LR_LEFT_POS - pose.DXL_HEAD_LR_RIGHT_POS)
        HEAD_UD_RANGE = abs(pose.DXL_HEAD_UD_DOWN_POS - pose.DXL_HEAD_UD_UP_POS)
        EYE_RANGE = abs(pose.DXL_EYE_RIGHT_POS - pose.DXL_EYE_LEFT_POS)
        BROW_RANGE = abs(pose.DXL_BROW_UP_POS - pose.DXL_BROW_DOWN_POS)

        IDLE_HEAD_LR_AMP = HEAD_LR_RANGE * 0.06
        IDLE_HEAD_UD_AMP = HEAD_UD_RANGE * 0.05
        IDLE_EYE_AMP = EYE_RANGE * 0.45
        IDLE_BROW_AMP = BROW_RANGE * 0.16

        IDLE_HEAD_FREQ = (0.08, 0.22)
        IDLE_EYE_FREQ = (0.15, 0.45)
        IDLE_SEG = (2.0, 5.0)
        IDLE_PAUSE = (1.0, 3.5)
        IDLE_PAUSE_CHANCE = 0.30
        INTENT_QUIET_S = 1.5
        RMS_QUIET_S = 0.6
        TICK_DT = 0.05  # 20 Hz

        rng = random.Random()
        idle = self._effective_idle_pose()
        seg_end = 0.0
        seg_start = 0.0
        mode = "pause"
        lr_amp = ud_amp = eye_amp = brow_amp = 0.0
        lr_freq = ud_freq = eye_freq = brow_freq = 0.0
        lr_phase = ud_phase = eye_phase = brow_phase = 0.0

        while not self._shutdown.is_set():
            try:
                # Skip when an expression is animating — the expression loop
                # owns _target_pose then, and idle's sinusoids would visibly
                # smear the gesture.
                if not (self.idle_animation_enabled and self._has_u2d2) or (
                    self._current_expression is not None
                ):
                    seg_end = 0.0
                else:
                    now = time.monotonic()
                    if (now - self._last_intent_mono) <= INTENT_QUIET_S or (
                        now - self._last_rms_ts
                    ) <= RMS_QUIET_S:
                        seg_end = 0.0
                    else:
                        if now >= seg_end:
                            seg_start = now
                            # Re-read the idle center each segment so saved
                            # animator.<servo>.default settings actually take
                            # effect without restarting the service.
                            idle = self._effective_idle_pose()
                            if rng.random() < IDLE_PAUSE_CHANCE:
                                mode = "pause"
                                seg_end = now + rng.uniform(*IDLE_PAUSE)
                            else:
                                mode = "move"
                                seg_end = now + rng.uniform(*IDLE_SEG)
                                lr_amp = IDLE_HEAD_LR_AMP * rng.uniform(0.5, 1.0)
                                ud_amp = IDLE_HEAD_UD_AMP * rng.uniform(0.4, 0.9)
                                eye_amp = IDLE_EYE_AMP * rng.uniform(0.4, 1.0)
                                brow_amp = IDLE_BROW_AMP * rng.uniform(0.4, 1.0)
                                lr_freq = rng.uniform(*IDLE_HEAD_FREQ)
                                ud_freq = rng.uniform(*IDLE_HEAD_FREQ)
                                eye_freq = rng.uniform(*IDLE_EYE_FREQ)
                                brow_freq = rng.uniform(*IDLE_EYE_FREQ)
                                lr_phase = rng.uniform(0.0, math.tau)
                                ud_phase = rng.uniform(0.0, math.tau)
                                eye_phase = rng.uniform(0.0, math.tau)
                                brow_phase = rng.uniform(0.0, math.tau)

                        if mode == "pause":
                            target = idle.model_copy(update={"jaw": self._current_pose.jaw})
                        else:
                            t = now - seg_start
                            target = schemas.AnimatorPose(
                                head_lr=pose.clamp_dxl(
                                    "head_lr",
                                    idle.head_lr
                                    + lr_amp * math.sin(math.tau * lr_freq * t + lr_phase),
                                ),
                                head_ud=pose.clamp_dxl(
                                    "head_ud",
                                    idle.head_ud
                                    + ud_amp * math.sin(math.tau * ud_freq * t + ud_phase),
                                ),
                                eye=pose.clamp_dxl(
                                    "eye",
                                    idle.eye
                                    + eye_amp * math.sin(math.tau * eye_freq * t + eye_phase),
                                ),
                                jaw=self._current_pose.jaw,
                                brow=pose.clamp_dxl(
                                    "brow",
                                    idle.brow
                                    + brow_amp * math.sin(math.tau * brow_freq * t + brow_phase),
                                ),
                            )
                        await self._safe_apply(target)
            except Exception as e:
                self.log.warning("idle_animation.error error=%s", e)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=TICK_DT)

    async def _expression_animation_loop(self) -> None:
        """Drive _target_pose at 20 Hz while an expression is active.

        Computes the expression's pose (idle + offsets + sinusoidal motion)
        per tick. Preserves whatever the jaw is currently doing so lipsync
        (TTS RMS) keeps owning the mouth without us trampling it. Auto-clears
        the expression when its `duration_s` elapses and emits a
        ``gesture_done`` event so the UI can drop its active pill.
        """
        TICK_DT = 0.05  # 20 Hz — matches idle loop
        while not self._shutdown.is_set():
            try:
                name = self._current_expression
                if name is not None and self._has_u2d2:
                    t_active = time.monotonic() - self._expression_started_mono
                    if expressions.is_expired(name, t_active):
                        self._current_expression = None
                        with contextlib.suppress(Exception):
                            await nats_helper.publish_model(
                                self.nats,
                                topics.ANIMATOR_EVENT_GESTURE_DONE,
                                schemas.AnimatorEvent(event="gesture_done", name=name),
                            )
                    else:
                        base = self._effective_idle_pose()
                        target = expressions.compute_target(
                            name, base, t_active, self._expression_intensity
                        )
                        # If lipsync is actively driving the jaw (recent RMS),
                        # leave the jaw alone — otherwise the expression
                        # offset would flap against the TTS-driven motion.
                        if time.monotonic() - self._last_rms_ts <= 0.5:
                            target = target.model_copy(update={"jaw": self._current_pose.jaw})
                        await self._safe_apply(target)
            except Exception as e:
                self.log.warning("expression_animation.error error=%s", e)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=TICK_DT)
