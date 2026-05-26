"""Seed the eight built-in expressions and their referenced frames.

Per-row idempotent: inserts missing seed rows and backfills is_builtin on
pre-existing seed-named rows. Never clobbers user edits on other fields.
15 frames + 8 expressions wired to the canonical emotions.
"""

import json

from sqlmodel import Session

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
    # idle uses random_walk — continuous sinusoidal motion around the idle
    # pose. steps_json holds the {intensity, speed, pause_chance} config
    # instead of a frame list. See KeyframePlayer._random_walk_pose.
    (
        "idle",
        "random_walk",
        1200,  # duration/delay unused by random_walk; kept for schema parity
        400,
        "ease-in-out",
        [],  # no frames
        "idle",
    ),
]

# Default config for the seeded idle random_walk row.
IDLE_RANDOM_WALK_CONFIG = {"intensity": 1.0, "speed": 1.0, "pause_chance": 0.30}


def seed_animations(engine) -> None:
    """Per-row upsert: insert missing seed rows, backfill is_builtin on
    pre-existing seed-named rows. Never clobbers user edits."""
    with Session(engine) as s:
        for name, pose in SEED_FRAMES.items():
            existing = s.get(Frame, name)
            if existing is None:
                s.add(Frame(name=name, is_builtin=True, **pose))
            elif not existing.is_builtin:
                existing.is_builtin = True
                s.add(existing)
        for (
            name,
            playback,
            dur_ms,
            delay_ms,
            easing,
            frame_names,
            emotion,
        ) in SEED_EXPRESSIONS:
            existing = s.get(Expression, name)
            if existing is None:
                if playback == "random_walk":
                    steps_json = json.dumps(IDLE_RANDOM_WALK_CONFIG)
                else:
                    steps_json = json.dumps([{"frame": n} for n in frame_names])
                s.add(
                    Expression(
                        name=name,
                        playback=playback,
                        default_duration_ms=dur_ms,
                        default_delay_ms=delay_ms,
                        default_easing=easing,
                        steps_json=steps_json,
                        emotion=emotion,
                        is_builtin=True,
                    )
                )
            elif not existing.is_builtin:
                existing.is_builtin = True
                s.add(existing)
        s.commit()


def apply_frame_seed(s: Session, name: str) -> Frame:
    """Overwrite an existing Frame row from its SEED_FRAMES entry. Caller
    holds the session open; commit is the caller's responsibility."""
    pose = SEED_FRAMES.get(name)
    if pose is None:
        raise KeyError(f"no seed for frame {name!r}")
    f = s.get(Frame, name)
    if f is None:
        f = Frame(name=name, is_builtin=True, **pose)
        s.add(f)
        return f
    for k, v in pose.items():
        setattr(f, k, v)
    f.is_builtin = True
    s.add(f)
    return f


def apply_expression_seed(s: Session, name: str) -> Expression:
    """Overwrite an existing Expression row from its SEED_EXPRESSIONS entry.
    Caller holds the session open; commit is the caller's responsibility."""
    seed = next((row for row in SEED_EXPRESSIONS if row[0] == name), None)
    if seed is None:
        raise KeyError(f"no seed for expression {name!r}")
    _, playback, dur_ms, delay_ms, easing, frame_names, emotion = seed
    if playback == "random_walk":
        steps_json = json.dumps(IDLE_RANDOM_WALK_CONFIG)
    else:
        steps_json = json.dumps([{"frame": n} for n in frame_names])
    e = s.get(Expression, name)
    if e is None:
        e = Expression(name=name, is_builtin=True)
        s.add(e)
    e.playback = playback
    e.default_duration_ms = dur_ms
    e.default_delay_ms = delay_ms
    e.default_easing = easing
    e.steps_json = steps_json
    e.emotion = emotion
    e.is_builtin = True
    s.add(e)
    return e


def is_builtin_frame_name(name: str) -> bool:
    return name in SEED_FRAMES


def is_builtin_expression_name(name: str) -> bool:
    return any(row[0] == name for row in SEED_EXPRESSIONS)
