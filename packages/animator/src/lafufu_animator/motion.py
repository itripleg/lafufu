"""Acceleration-limited motion model for the servo stepper.

The animator's stepper used to ease with a first-order exponential filter,
which is *ease-out only* — commanded velocity is highest the instant the
target changes, so a large move starts with a jerk. ``smooth_damp`` is a
critically-damped second-order follower: it eases IN and OUT, never has a
velocity discontinuity, caps speed at ``max_speed``, and tracks a moving
target gracefully (needed for lipsync and drag). It is the standard
game-engine "SmoothDamp" formulation.
"""

from lafufu_shared.schemas import AnimatorPose

from . import pose

_SERVOS = ("head_lr", "head_ud", "eye", "jaw", "brow")

# Per-servo motion tuning. ``smooth_time`` ≈ how long a move takes to settle;
# ``max_speed`` is a hard velocity cap in DXL ticks/second. The head is slowest
# for a calm, deliberate feel. The jaw is near-passthrough: the lipsync envelope
# (LipsyncEnvelope) already shapes its motion, so a second smoothing stage here
# only blurs syllable onsets — its smooth_time is kept tiny and its max_speed
# high enough that the max_change clamp never binds across the jaw's range.
# These shape the *commanded* motion — TUNE the feel on real hardware.
DEFAULT_SMOOTH_TIMES: dict[str, float] = {
    "head_lr": 0.26,
    "head_ud": 0.26,
    "eye": 0.16,
    "brow": 0.14,
    "jaw": 0.025,
}
DEFAULT_MAX_SPEEDS: dict[str, float] = {
    "head_lr": 1100.0,
    "head_ud": 1100.0,
    "eye": 700.0,
    "brow": 420.0,
    "jaw": 12000.0,
}


def smooth_damp(
    current: float,
    target: float,
    velocity: float,
    dt: float,
    smooth_time: float,
    max_speed: float,
) -> tuple[float, float]:
    """Advance one tick toward ``target``.

    ``velocity`` is the follower's carried velocity state — pass back the
    returned value on the next call. ``smooth_time`` is roughly how long the
    move takes; ``max_speed`` is a hard velocity cap. Returns
    ``(new_position, new_velocity)``.
    """
    if dt <= 0.0:
        return current, velocity

    smooth_time = max(1e-4, smooth_time)
    omega = 2.0 / smooth_time

    # Critically-damped exponential, rational approximation of exp(-x).
    x = omega * dt
    exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)

    change = current - target
    original_target = target

    # Clamp the considered distance so steady-state speed never exceeds max_speed.
    max_change = max_speed * smooth_time
    change = max(-max_change, min(max_change, change))
    target = current - change

    temp = (velocity + omega * change) * dt
    new_velocity = (velocity - omega * temp) * exp
    new_position = target + (change + temp) * exp

    # Kill overshoot: if we crossed the original target, snap to it.
    if (original_target - current > 0.0) == (new_position > original_target):
        new_position = original_target
        new_velocity = (new_position - original_target) / dt

    return new_position, new_velocity


class PoseSmoother:
    """Acceleration-limited follower for a whole pose.

    Holds per-servo position + velocity state. ``step`` advances every servo
    toward the target pose via ``smooth_damp`` and returns the clamped pose to
    write to the bus. This is the single place servo motion is shaped.
    """

    def __init__(self, smooth_times: dict[str, float], max_speeds: dict[str, float]) -> None:
        self._smooth_times = dict(smooth_times)
        self._max_speeds = dict(max_speeds)
        self._pos: dict[str, float] = dict.fromkeys(_SERVOS, 0.0)
        self._vel: dict[str, float] = dict.fromkeys(_SERVOS, 0.0)

    def reset_to(self, p: AnimatorPose) -> None:
        """Snap the follower's state to ``p`` with zero velocity — used to seed
        from the servos' real positions at startup so the first move eases."""
        for s in _SERVOS:
            self._pos[s] = float(getattr(p, s))
            self._vel[s] = 0.0

    def step(self, target: AnimatorPose, dt: float) -> AnimatorPose:
        """Advance one tick toward ``target``; return the clamped pose to write."""
        out: dict[str, int] = {}
        for s in _SERVOS:
            # Clamp the target so internal state can never wind up out of range.
            tv = float(pose.clamp_dxl(s, getattr(target, s)))
            new_pos, new_vel = smooth_damp(
                self._pos[s],
                tv,
                self._vel[s],
                dt,
                self._smooth_times.get(s, 0.15),
                self._max_speeds.get(s, 1e6),
            )
            self._pos[s] = new_pos
            self._vel[s] = new_vel
            out[s] = pose.clamp_dxl(s, new_pos)
        return AnimatorPose(**out)
