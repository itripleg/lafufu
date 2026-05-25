"""Pure pose math: clamping, dxl <-> degrees conversion, interpolation, idle pose."""

from lafufu_shared.schemas import AnimatorPose

# Calibrated constants (preserve from existing build — measured by hand)
DXL_IDS = {"head_lr": 1, "head_ud": 2, "brow": 3, "jaw": 4, "eye": 5}

DXL_HEAD_LR_LEFT_POS = 2298
DXL_HEAD_LR_RIGHT_POS = 1828
DXL_HEAD_UD_UP_POS = 2885
DXL_HEAD_UD_DOWN_POS = 3278
DXL_BROW_UP_POS = 2087
DXL_BROW_DOWN_POS = 2056
DXL_JAW_OPEN_POS = 1594
DXL_JAW_CLOSE_POS = 1811
DXL_EYE_LEFT_POS = 1995
DXL_EYE_RIGHT_POS = 2085

EYE_IDLE_DXL = 2045
BROW_IDLE_DXL = 2075
HEAD_IDLE_LR_DXL = 2063
HEAD_IDLE_UD_DXL = 3082
MOUTH_CLOSE_DXL = 1811
MOUTH_OPEN_DXL = 1594

# Per-servo clamp ranges (min, max); bounds order-agnostic
CLAMP = {
    "head_lr": (DXL_HEAD_LR_RIGHT_POS, DXL_HEAD_LR_LEFT_POS),
    "head_ud": (DXL_HEAD_UD_UP_POS, DXL_HEAD_UD_DOWN_POS),
    "brow": (DXL_BROW_DOWN_POS, DXL_BROW_UP_POS),
    "jaw": (DXL_JAW_OPEN_POS, DXL_JAW_CLOSE_POS),
    "eye": (DXL_EYE_LEFT_POS, DXL_EYE_RIGHT_POS),
}

# Hardware motion backstop, written to every servo by DxlBus.configure_limits().
# The software PoseSmoother (see motion.py) is the PRIMARY motion shaper; these
# are a safety cap *beneath* it so a bypassed or runaway goal can't slew at the
# servo's absolute maximum. Chosen generous enough that the servo always tracks
# the 30 Hz eased command stream without lag.
#
# Units are DXL-model-specific (X-series: Profile Velocity ≈ 0.229 rev/min,
# Profile Acceleration ≈ 214.577 rev/min² per unit). VERIFY against your servo
# model and tune the feel on real hardware.
PROFILE_VELOCITY = 300
PROFILE_ACCELERATION = 80


def clamp(value: float, lo: float, hi: float) -> int:
    """Clamp value to [min(lo,hi), max(lo,hi)] and return as int."""
    a, b = (lo, hi) if lo <= hi else (hi, lo)
    return int(max(a, min(b, value)))


def clamp_dxl(name: str, value: float) -> int:
    lo, hi = CLAMP[name]
    return clamp(value, lo, hi)


def dxl_from_deg(deg: float, *, deg_min: float, deg_max: float, pos_min: int, pos_max: int) -> int:
    """Map a degree value to a DXL position, clamped."""
    deg = max(deg_min, min(deg_max, deg))
    frac = (deg - deg_min) / (deg_max - deg_min)
    return round(pos_min + frac * (pos_max - pos_min))


def deg_from_dxl(
    pos: float, *, pos_min: int, pos_max: int, deg_min: float, deg_max: float
) -> float:
    frac = (pos - pos_min) / (pos_max - pos_min)
    return deg_min + frac * (deg_max - deg_min)


def lerp_int(a: int, b: int, t: float) -> int:
    """Linear interpolation, clamped to [0, 1], returned as int."""
    t = max(0.0, min(1.0, t))
    return round(a + (b - a) * t)


def idle_pose() -> AnimatorPose:
    return AnimatorPose(
        head_lr=HEAD_IDLE_LR_DXL,
        head_ud=HEAD_IDLE_UD_DXL,
        eye=EYE_IDLE_DXL,
        jaw=MOUTH_CLOSE_DXL,
        brow=BROW_IDLE_DXL,
    )
