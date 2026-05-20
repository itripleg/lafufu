"""Acceleration-limited motion model — the fix for jarring, un-eased servo moves.

The old stepper used first-order exponential easing (ease-OUT only): commanded
velocity is maximum at the instant the target changes, so a large move *starts*
with a jerk. These tests pin the properties that fix is missing — chiefly that
a move from rest must ease IN (accelerate), not start at full speed.
"""

import math

from lafufu_animator import pose
from lafufu_animator.motion import PoseSmoother, smooth_damp
from lafufu_shared.schemas import AnimatorPose

_SERVOS = ("head_lr", "head_ud", "eye", "jaw", "brow")


def _run(current, target, *, smooth_time, max_speed, dt=1 / 30, steps=400):
    """Step the smoother repeatedly toward a fixed target; return the path."""
    vel = 0.0
    positions = [current]
    for _ in range(steps):
        current, vel = smooth_damp(current, target, vel, dt, smooth_time, max_speed)
        positions.append(current)
    return positions


def _displacements(positions):
    return [abs(positions[i + 1] - positions[i]) for i in range(len(positions) - 1)]


def test_move_from_rest_eases_in():
    """A move from rest must accelerate — the first tick covers far less
    ground than the peak tick. First-order ease-out fails this: its first
    tick is the largest. This is the core anti-jerk property."""
    disp = _displacements(_run(0.0, 1000.0, smooth_time=0.3, max_speed=1e6))
    assert disp[0] < max(disp) * 0.5


def test_converges_to_target():
    pos = _run(0.0, 1000.0, smooth_time=0.3, max_speed=1e6)
    assert abs(pos[-1] - 1000.0) < 0.5


def test_does_not_overshoot():
    pos = _run(0.0, 1000.0, smooth_time=0.3, max_speed=1e6)
    assert max(pos) <= 1000.0 + 0.5


def test_velocity_capped_by_max_speed():
    """max_speed is a hard safety cap — no tick may move faster than it."""
    dt = 1 / 30
    max_speed = 200.0
    disp = _displacements(_run(0.0, 5000.0, smooth_time=0.3, max_speed=max_speed, dt=dt, steps=80))
    assert max(disp) <= max_speed * dt * 1.15


def test_decelerates_into_target():
    """After the peak, motion must slow to a soft stop (ease-out)."""
    disp = _displacements(_run(0.0, 1000.0, smooth_time=0.3, max_speed=1e6))
    peak = disp.index(max(disp))
    tail = disp[peak:]
    assert tail[-1] < tail[0]
    assert tail[-1] < 1.0


def test_holds_when_already_at_target():
    p, v = smooth_damp(500.0, 500.0, 0.0, 1 / 30, 0.3, 1e6)
    assert abs(p - 500.0) < 1e-6
    assert abs(v) < 1e-6


def test_tracks_moving_target_without_diverging():
    """Lipsync moves the jaw target every tick — the follower must keep up,
    not diverge."""
    dt = 1 / 30
    vel = 0.0
    current = 0.0
    worst_lag = 0.0
    for i in range(300):
        target = 100.0 * math.sin(i * dt * 2 * math.pi * 0.5)
        current, vel = smooth_damp(current, target, vel, dt, 0.08, 1e6)
        if i > 60:
            worst_lag = max(worst_lag, abs(current - target))
    assert worst_lag < 100.0


# --- PoseSmoother: per-servo wrapper used by the stepper ----------------------


def _smoother(smooth_time=0.2, max_speed=1e6):
    return PoseSmoother(
        smooth_times={s: smooth_time for s in _SERVOS},
        max_speeds={s: max_speed for s in _SERVOS},
    )


def test_reset_to_then_step_at_same_target_holds():
    """Seeded at a pose and asked to hold it, the smoother does not drift."""
    sm = _smoother()
    start = pose.idle_pose()
    sm.reset_to(start)
    out = sm.step(start, 1 / 30)
    for s in _SERVOS:
        assert abs(getattr(out, s) - getattr(start, s)) <= 1


def test_step_eases_toward_target_pose():
    """Repeated steps converge every servo onto the target pose."""
    sm = _smoother()
    sm.reset_to(pose.idle_pose())
    target = AnimatorPose(head_lr=2200, head_ud=3200, eye=2100, jaw=1560, brow=2090)
    out = pose.idle_pose()
    for _ in range(400):
        out = sm.step(target, 1 / 30)
    for s in _SERVOS:
        assert abs(getattr(out, s) - getattr(target, s)) <= 1


def test_step_clamps_each_servo_to_safe_range():
    """A target past a servo's calibrated range is clamped — the smoother is
    a hard safety net even if a caller forgets to clamp."""
    sm = _smoother()
    sm.reset_to(pose.idle_pose())
    # head_lr schema range is 0..4095 but its safe range is pose.CLAMP.
    target = AnimatorPose(head_lr=4095, head_ud=3082, eye=2045, jaw=1728, brow=2075)
    out = pose.idle_pose()
    for _ in range(400):
        out = sm.step(target, 1 / 30)
    lo, hi = pose.CLAMP["head_lr"]
    assert lo <= out.head_lr <= hi


def test_pose_step_eases_in():
    """The pose-level move also accelerates from rest — no first-tick jerk."""
    sm = _smoother(smooth_time=0.3)
    sm.reset_to(AnimatorPose(head_lr=1850, head_ud=3082, eye=2045, jaw=1728, brow=2075))
    target = AnimatorPose(head_lr=2290, head_ud=3082, eye=2045, jaw=1728, brow=2075)
    prev = 1850
    disp = []
    for _ in range(120):
        out = sm.step(target, 1 / 30)
        disp.append(abs(out.head_lr - prev))
        prev = out.head_lr
    assert disp[0] < max(disp) * 0.5
