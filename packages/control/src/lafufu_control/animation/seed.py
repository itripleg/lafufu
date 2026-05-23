"""Seed the eight built-in expressions and their referenced frames.

Idempotent: if any Frame or Expression already exists, this function no-ops.
First call inserts 15 frames + 8 expressions wired to the canonical emotions.
"""

import json

from sqlmodel import Session, select

from ..models.expression import Expression
from ..models.frame import Frame

IDLE = {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075}


def _offset(**deltas: int) -> dict[str, int]:
    """Idle pose with per-servo deltas (no clamping — the keyframe player clamps)."""
    out = dict(IDLE)
    for k, v in deltas.items():
        out[k] = out[k] + v
    return out


SEED_FRAMES: dict[str, dict[str, int]] = {
    "agree_low": _offset(head_ud=40, brow=10),
    "agree_high": _offset(head_ud=-15, brow=10),
    "disagree_left": _offset(head_lr=55, brow=-5),
    "disagree_right": _offset(head_lr=-55, brow=-5),
    "happy_a": _offset(head_ud=-30, jaw=-40, brow=18),
    "happy_b": _offset(head_lr=15, head_ud=-25, jaw=-40, brow=18),
    "sad_a": _offset(head_ud=60, eye=5, brow=-18),
    "sad_b": _offset(head_ud=68, eye=5, brow=-18),
    "angry_a": _offset(head_ud=-20, jaw=-20, brow=-22),
    "angry_b": _offset(head_lr=8, head_ud=-20, jaw=-20, brow=-22),
    "surprised_held": _offset(head_ud=-40, jaw=-80, brow=20),
    "idle_calm": _offset(),
    "idle_glance_l": _offset(head_lr=12, eye=-40),
    "idle_glance_r": _offset(head_lr=-12, eye=40),
    "idle_look_up": _offset(head_ud=-20),
}

# (name, playback, default_duration_ms, default_delay_ms, easing, frame_names, emotion)
SEED_EXPRESSIONS: list[tuple[str, str, int, int, str, list[str], str]] = [
    (
        "agree",
        "once",
        220,
        60,
        "ease-in-out",
        ["agree_low", "agree_high", "agree_low", "agree_high", "agree_low"],
        "agree",
    ),
    (
        "disagree",
        "once",
        220,
        60,
        "ease-in-out",
        ["disagree_left", "disagree_right", "disagree_left", "disagree_right"],
        "disagree",
    ),
    ("happy", "loop", 800, 300, "ease-in-out", ["happy_a", "happy_b"], "happy"),
    ("sad", "loop", 1500, 600, "ease-in-out", ["sad_a", "sad_b"], "sad"),
    ("angry", "loop", 180, 50, "linear", ["angry_a", "angry_b"], "angry"),
    ("surprised", "once", 250, 1500, "ease-out", ["surprised_held"], "surprised"),
    ("neutral", "once", 300, 100, "ease-in-out", ["idle_calm"], "neutral"),
    (
        "idle",
        "shuffle",
        1200,
        400,
        "ease-in-out",
        ["idle_calm", "idle_glance_l", "idle_glance_r", "idle_look_up", "idle_calm"],
        "idle",
    ),
]


def seed_animations(engine) -> None:
    """Insert SEED_FRAMES and SEED_EXPRESSIONS if neither table has any rows."""
    with Session(engine) as s:
        has_frames = s.exec(select(Frame).limit(1)).first() is not None
        has_expressions = s.exec(select(Expression).limit(1)).first() is not None
        if has_frames or has_expressions:
            return

        for name, pose in SEED_FRAMES.items():
            s.add(Frame(name=name, **pose))
        for (
            name,
            playback,
            dur_ms,
            delay_ms,
            easing,
            frame_names,
            emotion,
        ) in SEED_EXPRESSIONS:
            s.add(
                Expression(
                    name=name,
                    playback=playback,
                    default_duration_ms=dur_ms,
                    default_delay_ms=delay_ms,
                    default_easing=easing,
                    steps_json=json.dumps([{"frame": n} for n in frame_names]),
                    emotion=emotion,
                )
            )
        s.commit()
