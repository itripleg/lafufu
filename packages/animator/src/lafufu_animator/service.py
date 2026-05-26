"""AnimatorService: subscribes to intents and RMS, drives the DXL bus."""

import asyncio
import contextlib
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from . import lipsync, motion, pose
from .keyframe_player import KeyframePlayer


class DxlBusProtocol(Protocol):
    def write(self, name: str, position: int) -> None: ...
    def read(self, name: str) -> int: ...
    def enable_torque(self) -> None: ...
    def disable_torque(self) -> None: ...
    def configure_limits(self) -> None: ...
    def open(self) -> None: ...


# RMS events arrive at the agent's fixed TTS chunk cadence (~40 ms). The lipsync
# envelope is fed this fixed dt rather than one derived from message timestamps
# (msg.ts resets to 0 each utterance, which corrupted the attack/release rate).
_LIPSYNC_DT = 0.04


class AnimatorService(BaseService):
    name = "animator"
    heartbeat_interval_s = 5.0

    def __init__(
        self,
        bus: DxlBusProtocol,
        nats_url: str | None = None,
        smooth_times: dict[str, float] | None = None,
        max_speeds: dict[str, float] | None = None,
        stepper_hz: float = 30.0,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._nats_url = nats_url
        self._envelope = lipsync.LipsyncEnvelope()
        # RMS arrival → jaw apply delay, in seconds. Live-tunable via
        # animator.lipsync.offset_ms. Bump positive if the mouth still leads
        # audio after the agent-side aplay period shrink.
        self._lipsync_offset_s = 0.0
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
        # Acceleration-limited follower: eases current → target with ease-IN
        # *and* ease-out (no jerky start) and a hard per-servo speed cap.
        self._smoother = motion.PoseSmoother(
            smooth_times=smooth_times or motion.DEFAULT_SMOOTH_TIMES,
            max_speeds=max_speeds or motion.DEFAULT_MAX_SPEEDS,
        )
        self._stepper_dt = 1.0 / max(1.0, stepper_hz)
        self._has_u2d2 = True  # set False on disconnect
        self._last_rms_ts = 0.0
        self._last_intent_mono = 0.0  # monotonic timestamp of last intent/preview/reply
        self.idle_animation_enabled = True  # toggleable via settings (Phase 0 default: on)

        # Single active KeyframePlayer (None when no expression is playing).
        # Idle fallback: when _active_player is done/None and _idle_payload is
        # cached, we instantiate a fresh idle player.
        self._active_player: KeyframePlayer | None = None
        self._active_expression_name: str | None = None
        self._idle_payload: schemas.AnimatorIntentPlayExpression | None = None

        self._pose_publish_task: asyncio.Task | None = None
        self._lipsync_watchdog_task: asyncio.Task | None = None
        self._keyframe_player_task: asyncio.Task | None = None
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

        if self._has_u2d2:
            # Write hardware safety limits (position bounds + Profile
            # Velocity/Acceleration) while torque is still off — the
            # position-limit registers are EEPROM. Non-fatal: the software
            # smoother still limits motion if this fails.
            try:
                self._bus.configure_limits()
                self.log.info("dxl.limits.configured")
            except Exception as e:
                self.log.warning("dxl.limits.config_failed error=%s", e)

            # Enable torque so the servos actually hold and move to commanded
            # positions. Without this, writes silently no-op physically — code
            # thinks it commanded a position but the servo is freewheeling.
            try:
                self._bus.enable_torque()
                self.log.info("dxl.torque.enabled")
            except Exception as e:
                self.log.warning("dxl.torque.enable_failed error=%s", e)
                self._has_u2d2 = False

        # Seed the smoother from the servos' ACTUAL positions so the first move
        # is an eased glide to idle — not a full-speed snap from an unknown
        # power-up position. Falls back to idle if the read is unavailable.
        # Overrides arrive shortly via the snapshot — handlers update target.
        self._target_pose = self._effective_idle_pose()
        start_pose = self._read_present_pose() if self._has_u2d2 else None
        seed = start_pose or self._target_pose
        self._smoother.reset_to(seed)
        self._current_pose = seed

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

        # Lipsync tuning — attack/release shape jaw responsiveness, offset
        # shifts the whole jaw track in time relative to audio playback.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.animator.lipsync.attack_ms",
            schemas.ConfigChanged,
            self._on_config_lipsync_attack,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.animator.lipsync.release_ms",
            schemas.ConfigChanged,
            self._on_config_lipsync_release,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.animator.lipsync.offset_ms",
            schemas.ConfigChanged,
            self._on_config_lipsync_offset,
        )

        # Sync to DB on startup so idle_animation_enabled reflects admin's
        # current value rather than the in-code default.
        await self.request_config_snapshot()

        # Background tasks
        self._pose_publish_task = asyncio.create_task(self._pose_publish_loop())
        self._lipsync_watchdog_task = asyncio.create_task(self._lipsync_watchdog())
        self._keyframe_player_task = asyncio.create_task(self._keyframe_player_loop())
        self._stepper_task = asyncio.create_task(self._stepper_loop())

    async def on_shutdown(self) -> None:
        for t in (
            self._pose_publish_task,
            self._lipsync_watchdog_task,
            self._keyframe_player_task,
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

    async def _on_config_lipsync_attack(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            ms = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("animator.lipsync.attack_ms.bad_value value=%r", msg.value)
            return
        # Floor at 1 ms so the envelope's exp(-dt/tau) never divides by zero.
        self._envelope.attack_s = max(0.001, ms / 1000.0)
        self.log.info("animator.lipsync.attack_ms.set value=%.0f", ms)

    async def _on_config_lipsync_release(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            ms = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("animator.lipsync.release_ms.bad_value value=%r", msg.value)
            return
        self._envelope.release_s = max(0.001, ms / 1000.0)
        self.log.info("animator.lipsync.release_ms.set value=%.0f", ms)

    async def _on_config_lipsync_offset(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            ms = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("animator.lipsync.offset_ms.bad_value value=%r", msg.value)
            return
        # Clamp negative — only forward (delay) offsets work with this approach.
        # Backward "advance" would need look-ahead in the agent pipeline.
        self._lipsync_offset_s = max(0.0, ms / 1000.0)
        self.log.info("animator.lipsync.offset_ms.set value=%.0f", ms)

    async def _on_config_idle_animation(self, subject: str, msg: schemas.ConfigChanged) -> None:
        # value may be bool, "true"/"false" str, or 0/1
        v = msg.value
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes", "on")
        self.idle_animation_enabled = bool(v)
        # If idle is currently the active player and we just turned it off,
        # stop it so Lafufu freezes at the current pose instead of finishing
        # the loop. Non-idle expressions are unaffected.
        if not self.idle_animation_enabled and self._active_expression_name == "idle":
            self._active_player = None
            self._active_expression_name = None
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

    def _read_present_pose(self) -> schemas.AnimatorPose | None:
        """Read the servos' actual positions to seed the smoother at startup.

        Returns None if any read fails or yields an implausible value (e.g. 0
        from an unseeded fake, or garbage from a flaky bus) — the caller then
        falls back to seeding at the idle pose.
        """
        try:
            vals: dict[str, int] = {}
            for name in ("head_lr", "head_ud", "eye", "jaw", "brow"):
                raw = self._bus.read(name)
                lo, hi = min(pose.CLAMP[name]), max(pose.CLAMP[name])
                if not (lo - 200 <= raw <= hi + 200):
                    self.log.warning("dxl.read.implausible servo=%s value=%s", name, raw)
                    return None
                vals[name] = pose.clamp_dxl(name, raw)
            return schemas.AnimatorPose(**vals)
        except OSError as e:
            self.log.warning("dxl.read.failed error=%s", e)
            return None

    async def _stepper_loop(self) -> None:
        """Ease the current pose toward the target pose and write to the bus.

        Uses the acceleration-limited PoseSmoother (motion.py): every move
        eases IN and out, so a large target change no longer starts with a
        jerk. Per-servo smooth-times keep the jaw fast for lipsync while the
        head stays calm and deliberate.
        """
        last = time.monotonic()
        while not self._shutdown.is_set():
            now = time.monotonic()
            dt = now - last
            last = now
            try:
                if self._has_u2d2:
                    new_pose = self._smoother.step(self._target_pose, dt)
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
        self._active_player = None
        self._active_expression_name = None
        new = self._current_pose.model_copy(
            update={msg.name: pose.clamp_dxl(msg.name, msg.position)}
        )
        await self._safe_apply(new)

    async def _on_set_pose(self, subject: str, msg: schemas.AnimatorIntentSetPose) -> None:
        self._last_intent_mono = time.monotonic()
        self._active_player = None
        self._active_expression_name = None
        await self._safe_apply(msg.pose)

    async def _on_play_expression(
        self, subject: str, msg: schemas.AnimatorIntentPlayExpression
    ) -> None:
        self._last_intent_mono = time.monotonic()
        # If this is the idle expression, cache its payload as the fallback that
        # plays whenever no other expression is active.
        if msg.name == "idle":
            self._idle_payload = msg
        # Idle wandering should orbit the canonical idle pose, not wherever
        # Lafufu happens to be right now. Other expressions still interpolate
        # from current_pose so the transition stays smooth.
        start = self._effective_idle_pose() if msg.playback == "random_walk" else self._current_pose
        self._active_player = KeyframePlayer(
            payload=msg,
            start_pose=start,
            now_ms=int(time.monotonic() * 1000),
        )
        self._active_expression_name = msg.name

    async def _on_tts_rms(self, subject: str, msg: schemas.AgentTtsRms) -> None:
        # Drive the jaw via the attack/release envelope. mouth_target is already
        # the adaptively-normalized 0..1 value from the agent's LipsyncNormalizer.
        # Optional delay so the operator can shift the whole jaw track in time
        # relative to audio. The NATS callback runs in its own task per message,
        # so sleeping here delays *this* apply without stalling later messages —
        # they queue behind in arrival order and stay correctly ordered.
        if self._lipsync_offset_s > 0:
            await asyncio.sleep(self._lipsync_offset_s)
        smoothed = self._envelope.step(target=msg.mouth_target, dt=_LIPSYNC_DT)
        jaw_pos = lipsync.rms_to_jaw_dxl(smoothed)
        new = self._current_pose.model_copy(update={"jaw": jaw_pos})
        await self._safe_apply(new)
        # Wall-clock stamp so the idle/expression loops' "is speaking" checks
        # (which compare against time.monotonic()) actually work.
        self._last_rms_ts = time.monotonic()

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

    async def _keyframe_player_loop(self) -> None:
        """Drive _target_pose at 20Hz from the active KeyframePlayer.

        Idle fallback: when no expression is active, no recent operator intent,
        and an idle payload is cached, spin up a fresh idle player. The
        intent-quiet deferral matters because _on_preview/_on_set_pose clear
        _active_player and set a slider-driven target — without the deferral
        this loop would respawn idle on the very next tick and stomp the
        slider value back to current pose.

        Lipsync ownership of the jaw is preserved when a recent RMS event is
        present.
        """
        TICK_DT = 0.05  # 20Hz
        INTENT_QUIET_S = 1.5
        while not self._shutdown.is_set():
            try:
                if self._has_u2d2:
                    now_mono = time.monotonic()
                    now_ms = int(now_mono * 1000)
                    operator_active = (now_mono - self._last_intent_mono) <= INTENT_QUIET_S

                    # Idle fallback: only when nothing is playing, idle anim
                    # is enabled, AND no recent operator activity. Otherwise
                    # the slider-driven target would be overridden on the
                    # next tick.
                    #
                    # Center idle wandering on the canonical idle pose, NOT
                    # _current_pose — otherwise idle drifts around wherever
                    # the operator last clicked (a snapshot frame, a slider
                    # tweak, etc.), which looks broken.
                    if (
                        self._active_player is None
                        and self._idle_payload is not None
                        and self.idle_animation_enabled
                        and not operator_active
                    ):
                        self._active_player = KeyframePlayer(
                            payload=self._idle_payload,
                            start_pose=self._effective_idle_pose(),
                            now_ms=now_ms,
                        )
                        self._active_expression_name = "idle"

                    if self._active_player is not None:
                        target = self._active_player.pose_at(now_ms)
                        # If lipsync is currently driving the jaw, preserve it.
                        if now_mono - self._last_rms_ts <= 0.5:
                            target = target.model_copy(update={"jaw": self._current_pose.jaw})
                        await self._safe_apply(target)

                        if self._active_player.is_done(now_ms):
                            finished_name = self._active_expression_name
                            self._active_player = None
                            self._active_expression_name = None
                            if finished_name is not None and finished_name != "idle":
                                with contextlib.suppress(Exception):
                                    await nats_helper.publish_model(
                                        self.nats,
                                        topics.ANIMATOR_EVENT_GESTURE_DONE,
                                        schemas.AnimatorEvent(
                                            event="gesture_done", name=finished_name
                                        ),
                                    )
            except Exception as e:
                self.log.warning("keyframe_player.error error=%s", e)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown.wait(), timeout=TICK_DT)
