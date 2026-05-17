"""Expression definitions: emotion → pose offsets from idle.

Each expression is a dict mapping servo name → DXL-tick delta from idle.
Hand-tuned values; tweak via web admin in later phases.
"""

from lafufu_shared.schemas import AnimatorPose

from . import pose

ServoOffsets = dict[str, int]

# Deltas relative to idle pose (idle_pose() values).
# Positive head_ud = look down; positive eye = look right; positive jaw = open (toward MOUTH_OPEN).
_EXPRESSIONS: dict[str, ServoOffsets] = {
    "neutral": {"head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0},
    "happy": {"head_lr": 0, "head_ud": -30, "eye": 0, "jaw": -40, "brow": +18},
    "sad": {"head_lr": 0, "head_ud": +60, "eye": +5, "jaw": 0, "brow": -18},
    "angry": {"head_lr": 0, "head_ud": -20, "eye": 0, "jaw": -20, "brow": -22},
    "surprised": {"head_lr": 0, "head_ud": -40, "eye": 0, "jaw": -80, "brow": +20},
    "agree": {"head_lr": 0, "head_ud": +20, "eye": 0, "jaw": 0, "brow": +10},
    "disagree": {"head_lr": +30, "head_ud": 0, "eye": 0, "jaw": 0, "brow": -10},
}


def list_names() -> list[str]:
    return list(_EXPRESSIONS.keys())


def get_offsets(name: str, intensity: float = 1.0) -> ServoOffsets:
    """Return scaled offsets for the named expression. Unknown → neutral."""
    base = _EXPRESSIONS.get(name, _EXPRESSIONS["neutral"])
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
