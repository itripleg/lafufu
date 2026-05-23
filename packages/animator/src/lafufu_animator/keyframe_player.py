"""Pure-logic keyframe player. Given an AnimatorIntentPlayExpression and a
start pose, return interpolated poses for each tick of monotonic time.

Playback modes:
- "once":    play steps from start_pose → step1 → step2 → ... → last; freeze on last.
- "loop":    same, but wrap modulo cycle length forever.
- "shuffle": play random-order steps with jittered durations forever.

Per-step overrides for duration_ms, delay_ms, easing fall back to the
expression-level defaults. Each step is duration_ms of ease(curve)
interpolation FROM the previous pose TO the step's pose, followed by
delay_ms of holding the step's pose. Cursor begins at start_pose, then
becomes step_i.pose after step_i completes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from lafufu_shared.schemas import (
    AnimatorIntentPlayExpression,
    AnimatorPlayStep,
    AnimatorPose,
)

from .easing import ease
from .pose import clamp_dxl


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
class KeyframePlayer:
    payload: AnimatorIntentPlayExpression
    start_pose: AnimatorPose
    now_ms: int  # the monotonic timestamp at construction time (ms)
    rng_seed: int | None = None
    _shuffle_plan: list[tuple[int, int, int]] = field(default_factory=list)
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
