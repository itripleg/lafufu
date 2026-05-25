"""Pure-logic keyframe player. Given an AnimatorIntentPlayExpression and a
start pose, return interpolated poses for each tick of monotonic time.

Playback modes:
- "once":        play steps start_pose → step1 → ... → last; freeze on last.
- "loop":        same, but wrap modulo cycle length forever.
- "shuffle":     play random-order steps with jittered durations forever.
- "random_walk": ignore steps; emit continuous sinusoidal motion around
                 start_pose using per-segment random amplitudes/frequencies.
                 Three knobs (intensity, speed, pause_chance) live on
                 payload.random_walk_config.

Per-step overrides for duration_ms, delay_ms, easing fall back to the
expression-level defaults. Each step is duration_ms of ease(curve)
interpolation FROM the previous pose TO the step's pose, followed by
delay_ms of holding the step's pose. Cursor begins at start_pose, then
becomes step_i.pose after step_i completes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from lafufu_shared.schemas import (
    AnimatorIntentPlayExpression,
    AnimatorPlayStep,
    AnimatorPose,
    RandomWalkConfig,
)

from .easing import ease
from .pose import (
    DXL_BROW_DOWN_POS,
    DXL_BROW_UP_POS,
    DXL_EYE_LEFT_POS,
    DXL_EYE_RIGHT_POS,
    DXL_HEAD_LR_LEFT_POS,
    DXL_HEAD_LR_RIGHT_POS,
    DXL_HEAD_UD_DOWN_POS,
    DXL_HEAD_UD_UP_POS,
    clamp_dxl,
)

# Per-servo amplitude at intensity=1.0, expressed as a fraction of the servo's
# full DXL range. The original calibration (head_lr=0.06, head_ud=0.05,
# eye=0.45, brow=0.16) was tuned by feel against the *original* CLAMP table.
# When pose.py recalibrated brow (48→31, -35%) and eye (170→90, -47%) to the
# real hardware limits, the absolute motion shrank with them. These percentages
# are bumped to keep idle visually energetic on the new (tighter) ranges:
#   eye:  0.45 * 170 ~= 76 units  ->  0.85 * 90 ~= 76 units (preserved)
#   brow: 0.16 * 48  ~= 7.7 units ->  0.25 * 31 ~= 7.7 units (preserved)
# Head ranges were not recalibrated; head percentages are unchanged.
_RW_AMP_PCT = {
    "head_lr": 0.06,
    "head_ud": 0.05,
    "eye": 0.85,
    "brow": 0.25,
}
_RW_FREQ_HZ = {
    "head_lr": (0.08, 0.22),
    "head_ud": (0.08, 0.22),
    "eye": (0.15, 0.45),
    "brow": (0.15, 0.45),
}
_RW_SEG_S = (2.0, 5.0)
_RW_PAUSE_S = (1.0, 3.5)


def _servo_range(name: str) -> int:
    rng = {
        "head_lr": abs(DXL_HEAD_LR_LEFT_POS - DXL_HEAD_LR_RIGHT_POS),
        "head_ud": abs(DXL_HEAD_UD_DOWN_POS - DXL_HEAD_UD_UP_POS),
        "eye": abs(DXL_EYE_RIGHT_POS - DXL_EYE_LEFT_POS),
        "brow": abs(DXL_BROW_UP_POS - DXL_BROW_DOWN_POS),
    }
    return rng[name]


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _interp(prev: AnimatorPose, target: AnimatorPose, t: float) -> AnimatorPose:
    return AnimatorPose(
        head_lr=clamp_dxl("head_lr", _lerp(prev.head_lr, target.head_lr, t)),
        head_ud=clamp_dxl("head_ud", _lerp(prev.head_ud, target.head_ud, t)),
        eye=clamp_dxl("eye", _lerp(prev.eye, target.eye, t)),
        jaw=clamp_dxl("jaw", _lerp(prev.jaw, target.jaw, t)),
        brow=clamp_dxl("brow", _lerp(prev.brow, target.brow, t)),
    )


def _dur(step: AnimatorPlayStep, p: AnimatorIntentPlayExpression) -> int:
    return step.duration_ms if step.duration_ms is not None else p.default_duration_ms


def _delay(step: AnimatorPlayStep, p: AnimatorIntentPlayExpression) -> int:
    return step.delay_ms if step.delay_ms is not None else p.default_delay_ms


def _curve(step: AnimatorPlayStep, p: AnimatorIntentPlayExpression) -> str:
    return step.easing if step.easing else p.default_easing


@dataclass
class _RwSegment:
    """One random-walk segment: either 'pause' (hold start_pose) or 'move'
    (sinusoidal per servo). Pre-rolled with rng so pose_at is deterministic
    within a segment and free of allocations."""

    start_ms: int
    dur_ms: int
    kind: str  # "pause" | "move"
    # Move-only params, per servo.
    amps: dict[str, float] = field(default_factory=dict)
    freqs: dict[str, float] = field(default_factory=dict)
    phases: dict[str, float] = field(default_factory=dict)


@dataclass
class KeyframePlayer:
    payload: AnimatorIntentPlayExpression
    start_pose: AnimatorPose
    now_ms: int  # the monotonic timestamp at construction time (ms)
    rng_seed: int | None = None
    _shuffle_plan: list[tuple[int, int, int]] = field(default_factory=list)
    _rw_segments: list[_RwSegment] = field(default_factory=list)
    _rng: random.Random = field(default_factory=random.Random)
    _cycle_len: int = 0

    def __post_init__(self) -> None:
        if self.rng_seed is not None:
            self._rng = random.Random(self.rng_seed)
        self._cycle_len = sum(
            _dur(s, self.payload) + _delay(s, self.payload) for s in self.payload.steps
        )

    # ── public API ──────────────────────────────────────────────

    def pose_at(self, now_ms: int) -> AnimatorPose:
        elapsed = now_ms - self.now_ms
        if self.payload.playback == "random_walk":
            return self._random_walk_pose(elapsed)
        if not self.payload.steps:
            return self.start_pose
        if self.payload.playback == "shuffle":
            return self._shuffle_pose(elapsed)
        if self.payload.playback == "once" and elapsed >= self._cycle_len:
            return self.payload.steps[-1].pose
        if self._cycle_len:
            elapsed = elapsed % self._cycle_len
        return self._linear_pose(elapsed)

    def is_done(self, now_ms: int) -> bool:
        if self.payload.playback != "once":
            return False
        return (now_ms - self.now_ms) >= self._cycle_len

    # ── internals ───────────────────────────────────────────────

    def _prev_pose_for(self, idx: int) -> AnimatorPose:
        return self.start_pose if idx == 0 else self.payload.steps[idx - 1].pose

    def _linear_pose(self, elapsed: int) -> AnimatorPose:
        """Walk steps in declared order. Each step is dur ms of interp + delay ms of hold."""
        cursor = 0
        for idx, step in enumerate(self.payload.steps):
            dur = _dur(step, self.payload)
            hold = _delay(step, self.payload)
            if elapsed < cursor + dur:
                t_in = (elapsed - cursor) / dur if dur > 0 else 1.0
                t = ease(_curve(step, self.payload), t_in)
                return _interp(self._prev_pose_for(idx), step.pose, t)
            if elapsed < cursor + dur + hold:
                return step.pose
            cursor += dur + hold
        return self.payload.steps[-1].pose

    def _random_walk_pose(self, elapsed: int) -> AnimatorPose:
        """Continuous sinusoidal motion around start_pose. Re-rolls per
        segment (~2-5s, scaled by speed). Some segments are pauses that just
        hold start_pose. Three knobs only: intensity, speed, pause_chance."""
        cfg: RandomWalkConfig = self.payload.random_walk_config or RandomWalkConfig()
        # Lazily extend the segment list until it covers `elapsed`.
        while (
            not self._rw_segments
            or self._rw_segments[-1].start_ms + self._rw_segments[-1].dur_ms <= elapsed
        ):
            self._rw_segments.append(self._roll_rw_segment(cfg))

        # Find the active segment (linear scan; the list grows ~once per 3s
        # of uptime — acceptable for the lifetime of a single play).
        seg = next(
            s for s in reversed(self._rw_segments) if s.start_ms <= elapsed < s.start_ms + s.dur_ms
        )
        if seg.kind == "pause":
            return self.start_pose

        t_s = (elapsed - seg.start_ms) / 1000.0
        return AnimatorPose(
            head_lr=clamp_dxl(
                "head_lr",
                self.start_pose.head_lr
                + seg.amps["head_lr"]
                * math.sin(math.tau * seg.freqs["head_lr"] * t_s + seg.phases["head_lr"]),
            ),
            head_ud=clamp_dxl(
                "head_ud",
                self.start_pose.head_ud
                + seg.amps["head_ud"]
                * math.sin(math.tau * seg.freqs["head_ud"] * t_s + seg.phases["head_ud"]),
            ),
            eye=clamp_dxl(
                "eye",
                self.start_pose.eye
                + seg.amps["eye"] * math.sin(math.tau * seg.freqs["eye"] * t_s + seg.phases["eye"]),
            ),
            # Jaw is owned by lipsync — never wiggle it here. The service
            # preserves it anyway, but emitting start_pose.jaw keeps the
            # contract clean.
            jaw=self.start_pose.jaw,
            brow=clamp_dxl(
                "brow",
                self.start_pose.brow
                + seg.amps["brow"]
                * math.sin(math.tau * seg.freqs["brow"] * t_s + seg.phases["brow"]),
            ),
        )

    def _roll_rw_segment(self, cfg: RandomWalkConfig) -> _RwSegment:
        start_ms = (
            self._rw_segments[-1].start_ms + self._rw_segments[-1].dur_ms
            if self._rw_segments
            else 0
        )
        speed = max(0.01, cfg.speed)
        if self._rng.random() < cfg.pause_chance:
            dur_s = self._rng.uniform(*_RW_PAUSE_S) / speed
            return _RwSegment(start_ms=start_ms, dur_ms=int(dur_s * 1000), kind="pause")

        dur_s = self._rng.uniform(*_RW_SEG_S) / speed
        amps: dict[str, float] = {}
        freqs: dict[str, float] = {}
        phases: dict[str, float] = {}
        for servo, pct in _RW_AMP_PCT.items():
            amps[servo] = _servo_range(servo) * pct * cfg.intensity * self._rng.uniform(0.5, 1.0)
            freqs[servo] = self._rng.uniform(*_RW_FREQ_HZ[servo])
            phases[servo] = self._rng.uniform(0.0, math.tau)
        return _RwSegment(
            start_ms=start_ms,
            dur_ms=int(dur_s * 1000),
            kind="move",
            amps=amps,
            freqs=freqs,
            phases=phases,
        )

    def _shuffle_pose(self, elapsed: int) -> AnimatorPose:
        """Lazily extend a randomised step plan until it covers `elapsed`."""
        total = sum(d + h for (_, d, h) in self._shuffle_plan)
        while total <= elapsed:
            step_idx = self._rng.randrange(len(self.payload.steps))
            step = self.payload.steps[step_idx]
            base_dur = _dur(step, self.payload)
            base_hold = _delay(step, self.payload)
            dur = max(20, int(base_dur * self._rng.uniform(0.8, 1.2)))
            hold = max(0, int(base_hold * self._rng.uniform(0.8, 1.2)))
            self._shuffle_plan.append((step_idx, dur, hold))
            total += dur + hold

        # Walk the plan, carrying prev_pose between blocks.
        cursor = 0
        prev = self.start_pose
        for step_idx, dur, hold in self._shuffle_plan:
            step = self.payload.steps[step_idx]
            if elapsed < cursor + dur:
                t_in = (elapsed - cursor) / dur if dur > 0 else 1.0
                t = ease(_curve(step, self.payload), t_in)
                return _interp(prev, step.pose, t)
            if elapsed < cursor + dur + hold:
                return step.pose
            cursor += dur + hold
            prev = step.pose
        return prev
