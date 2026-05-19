"""Expression definitions: emotion → animated motion overlay on top of idle.

Each expression has a static pose offset (held throughout) plus zero or more
sinusoidal motion overlays per servo. The animator runs the active expression
as a continuous loop at the stepper rate — so "agree" actually nods, "disagree"
shakes, and sustained emotions like "happy"/"sad" get subtle living-presence
motion rather than freezing at a single pose.

Expressions with a non-None ``duration_s`` auto-clear back to neutral after
that many seconds (used for discrete gestures: agree/disagree/surprised).
``duration_s = None`` means the expression holds indefinitely until another
expression is played or a user intent overrides it.
"""

import math
from dataclasses import dataclass

from lafufu_shared.schemas import AnimatorPose

from . import pose

ServoOffsets = dict[str, int]


@dataclass(frozen=True)
class ServoMotion:
    """Sinusoidal motion overlay on a single servo, in DXL ticks.

    target(t) = base[servo] + offset[servo] + amp_ticks * sin(2π·freq_hz·t + phase_rad)
    """

    servo: str
    amp_ticks: float
    freq_hz: float
    phase_rad: float = 0.0


@dataclass(frozen=True)
class Expression:
    offsets: ServoOffsets
    motion: tuple[ServoMotion, ...] = ()
    duration_s: float | None = None


def _zeros() -> ServoOffsets:
    return {"head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0}


# Per-servo DXL conventions (see pose.py):
#   head_ud — positive = look down (DOWN_POS > UP_POS).
#   head_lr — positive = toward LEFT_POS.
#   jaw     — positive = closed; negative offset opens the mouth.
#   brow    — positive = raised.
_EXPRESSIONS: dict[str, Expression] = {
    "neutral": Expression(
        offsets=_zeros(),
        motion=(),
        duration_s=0.3,  # quickly settles, then idle takes back over
    ),
    "happy": Expression(
        offsets={"head_lr": 0, "head_ud": -30, "eye": 0, "jaw": -40, "brow": +18},
        motion=(
            # Gentle bobbing — contented bouncing
            ServoMotion("head_ud", amp_ticks=10, freq_hz=0.6),
            ServoMotion("head_lr", amp_ticks=14, freq_hz=0.4, phase_rad=math.pi / 2),
        ),
    ),
    "sad": Expression(
        offsets={"head_lr": 0, "head_ud": +60, "eye": +5, "jaw": 0, "brow": -18},
        motion=(
            # Slow, heavy breathing motion
            ServoMotion("head_ud", amp_ticks=14, freq_hz=0.2),
        ),
    ),
    "angry": Expression(
        offsets={"head_lr": 0, "head_ud": -20, "eye": 0, "jaw": -20, "brow": -22},
        motion=(
            # Tense, fast tremor of head and brow
            ServoMotion("head_lr", amp_ticks=8, freq_hz=2.4),
            ServoMotion("brow", amp_ticks=5, freq_hz=2.4, phase_rad=math.pi),
        ),
    ),
    "surprised": Expression(
        offsets={"head_lr": 0, "head_ud": -40, "eye": 0, "jaw": -80, "brow": +20},
        motion=(),  # frozen startle
        duration_s=2.0,  # snap-and-recover
    ),
    "agree": Expression(
        # Slight chin-down baseline so the nod reads clearly as a "yes" motion.
        offsets={"head_lr": 0, "head_ud": +5, "eye": 0, "jaw": 0, "brow": +10},
        motion=(
            # Three crisp nods — head_ud dips down on each cycle.
            ServoMotion("head_ud", amp_ticks=40, freq_hz=1.2),
        ),
        duration_s=2.6,  # ~3 nods at 1.2 Hz
    ),
    "disagree": Expression(
        offsets={"head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": -10},
        motion=(
            # Side-to-side shake — head_lr sinusoid
            ServoMotion("head_lr", amp_ticks=55, freq_hz=1.7),
        ),
        duration_s=2.4,  # ~4 shakes at 1.7 Hz
    ),
}


def list_names() -> list[str]:
    return list(_EXPRESSIONS.keys())


def get(name: str) -> Expression:
    """Full Expression for `name`, falling back to neutral."""
    return _EXPRESSIONS.get(name, _EXPRESSIONS["neutral"])


def get_offsets(name: str, intensity: float = 1.0) -> ServoOffsets:
    """Scaled static offsets only (no motion). Kept for one-shot callers
    that don't go through the expression animation loop."""
    base = get(name).offsets
    intensity = max(0.0, min(1.0, intensity))
    return {k: round(v * intensity) for k, v in base.items()}


def apply_offsets(base_pose: AnimatorPose, offsets: ServoOffsets) -> AnimatorPose:
    """Apply DXL-tick offsets to a pose, clamping each servo to its safe range."""
    return AnimatorPose(
        head_lr=pose.clamp_dxl("head_lr", base_pose.head_lr + offsets.get("head_lr", 0)),
        head_ud=pose.clamp_dxl("head_ud", base_pose.head_ud + offsets.get("head_ud", 0)),
        eye=pose.clamp_dxl("eye", base_pose.eye + offsets.get("eye", 0)),
        jaw=pose.clamp_dxl("jaw", base_pose.jaw + offsets.get("jaw", 0)),
        brow=pose.clamp_dxl("brow", base_pose.brow + offsets.get("brow", 0)),
    )


def compute_target(
    name: str,
    base_pose: AnimatorPose,
    t_active: float,
    intensity: float = 1.0,
) -> AnimatorPose:
    """Pose at `t_active` seconds into the expression: base + scaled static
    offset + sum of per-servo sinusoidal overlays, clamped per servo."""
    expr = get(name)
    intensity = max(0.0, min(1.0, intensity))

    per_servo: dict[str, float] = {k: v * intensity for k, v in expr.offsets.items()}
    for m in expr.motion:
        per_servo.setdefault(m.servo, 0.0)
        per_servo[m.servo] += (
            m.amp_ticks * intensity * math.sin(math.tau * m.freq_hz * t_active + m.phase_rad)
        )

    return AnimatorPose(
        head_lr=pose.clamp_dxl("head_lr", base_pose.head_lr + per_servo.get("head_lr", 0.0)),
        head_ud=pose.clamp_dxl("head_ud", base_pose.head_ud + per_servo.get("head_ud", 0.0)),
        eye=pose.clamp_dxl("eye", base_pose.eye + per_servo.get("eye", 0.0)),
        jaw=pose.clamp_dxl("jaw", base_pose.jaw + per_servo.get("jaw", 0.0)),
        brow=pose.clamp_dxl("brow", base_pose.brow + per_servo.get("brow", 0.0)),
    )


def is_expired(name: str, t_active: float) -> bool:
    """True if `name`'s `duration_s` is set and `t_active` exceeds it."""
    d = get(name).duration_s
    return d is not None and t_active >= d
