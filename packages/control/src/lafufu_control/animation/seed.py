"""Seed the built-in expressions and their referenced frames.

Per-row idempotent: inserts missing seed rows and backfills is_builtin on
pre-existing seed-named rows. Never clobbers user edits on other fields.

Servo frames (15): physical head-motion keyframes used by the animator.
Video frames (33): idle_01..20 + laugh_01..13, each with an image ref from
  the hand-drawn animation videos. Used by emotion expressions on the pet screen.
Expressions (8): 7 emotions + idle random_walk.
"""

import json
import logging

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from ..models.expression import Expression
from ..models.frame import Frame

IDLE = {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075}


def _offset(**deltas: int) -> dict:
    """Idle pose with per-servo deltas (no clamping — the keyframe player clamps)."""
    out = dict(IDLE)
    for k, v in deltas.items():
        out[k] = out[k] + v
    return out


def _vid(filename: str) -> dict:
    """Video-frame record: IDLE servo positions + image ref."""
    return {**IDLE, "image": f"sprites/default/{filename}"}


# Servo keyframes — physical head positions, no image refs.
SEED_FRAMES: dict[str, dict] = {
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
    # Video frames — all at IDLE servo positions; image drives the pet screen.
    **{f"vid_idle_{i:02d}": _vid(f"idle_{i:02d}.png") for i in range(1, 21)},
    **{f"vid_laugh_{i:02d}": _vid(f"laugh_{i:02d}.png") for i in range(1, 14)},
}

# Frames whose image refs must be kept up-to-date even when already in the DB
# (image path could change between releases). All vid_* frames qualify.
_ALWAYS_REFRESH_IMAGE = {n for n in SEED_FRAMES if n.startswith("vid_")}

# Default single-media clip per emotion. Built-in emotions default to
# single-media mode (one mp4 on the pet screen + servos animating frame-by-frame
# underneath) rather than the per-frame image flipbook. Only four source clips
# exist, so happy/angry are reused for agree/disagree and idle_lafufu covers the
# calmer moods. If a clip is missing on disk the pet screen falls back to the
# per-frame flipbook automatically, so these refs are safe to ship ahead of the
# files. Edit/clear per emotion in the Studio.
SEED_DISPLAY_MEDIA: dict[str, str] = {
    "neutral": "sprites/default/idle_lafufu.mp4",
    "happy": "sprites/default/happy_lafufu.mp4",
    "sad": "sprites/default/idle_lafufu.mp4",
    "angry": "sprites/default/angry_lafufu.mp4",
    "surprised": "sprites/default/idle_lafufu.mp4",
    "agree": "sprites/default/laughing_lafufu.mp4",
    "disagree": "sprites/default/angry_lafufu.mp4",
    "idle": "sprites/default/idle_lafufu.mp4",
}

# (name, playback, default_duration_ms, default_delay_ms, easing, frame_names, emotion)
# Emotion expressions now drive the pet screen through a single display_media
# clip (see SEED_DISPLAY_MEDIA); the frame list still drives the servos and is
# the fallback visual when no display_media is set / the file is missing.
# The idle expression stays random_walk (no frame list) for continuous head motion.
SEED_EXPRESSIONS: list[tuple[str, str, int, int, str, list[str], str]] = [
    (
        "neutral",
        "once",
        500,
        0,
        "ease-in-out",
        ["vid_idle_01"],
        "neutral",
    ),
    (
        "happy",
        "loop",
        150,
        0,
        "linear",
        [f"vid_laugh_{i:02d}" for i in range(1, 14)],
        "happy",
    ),
    (
        "sad",
        "loop",
        400,
        0,
        "ease-in-out",
        [f"vid_idle_{i:02d}" for i in [6, 7, 8, 9, 10, 11]],
        "sad",
    ),
    (
        "angry",
        "loop",
        160,
        0,
        "linear",
        [f"vid_idle_{i:02d}" for i in [5, 6, 7, 8, 9, 8, 7, 6]],
        "angry",
    ),
    (
        "surprised",
        "once",
        300,
        0,
        "ease-out",
        [f"vid_idle_{i:02d}" for i in [1, 2, 3]],
        "surprised",
    ),
    (
        "agree",
        "once",
        180,
        0,
        "ease-in-out",
        [f"vid_laugh_{i:02d}" for i in [1, 2, 3, 2, 1, 2, 3, 2, 1]],
        "agree",
    ),
    (
        "disagree",
        "once",
        180,
        0,
        "ease-in-out",
        [f"vid_idle_{i:02d}" for i in [5, 6, 7, 8, 7, 6, 5, 6, 7]],
        "disagree",
    ),
    # idle uses random_walk — continuous sinusoidal motion around the idle
    # pose. steps_json holds the {intensity, speed, pause_chance} config
    # instead of a frame list. See KeyframePlayer._random_walk_pose.
    (
        "idle",
        "random_walk",
        1200,
        400,
        "ease-in-out",
        [],
        "idle",
    ),
]

# Default config for the seeded idle random_walk row.
IDLE_RANDOM_WALK_CONFIG = {"intensity": 1.0, "speed": 1.0, "pause_chance": 0.30}


def seed_animations(engine) -> None:
    """Per-row upsert: insert missing seed rows, backfill is_builtin on
    pre-existing seed-named rows. Never clobbers user edits.

    Video frames (_ALWAYS_REFRESH_IMAGE) are always updated so image paths
    stay current across releases.

    Expressions are committed one-at-a-time because `Expression.emotion`
    carries a UNIQUE constraint: if a user-created row already claims
    `emotion="idle"` (etc.), inserting the built-in with the same emotion
    raises IntegrityError. A single batch commit would roll back ALL the
    other seed inserts, leaving the registry empty and the animator
    without an idle payload — silent and very hard to diagnose remotely.
    Per-row commit isolates the failure: log + retry without the emotion
    field so the built-in still gets seeded."""
    log = logging.getLogger(__name__)
    with Session(engine) as s:
        for name, pose in SEED_FRAMES.items():
            existing = s.get(Frame, name)
            if existing is None:
                s.add(Frame(name=name, is_builtin=True, **pose))
            elif name in _ALWAYS_REFRESH_IMAGE:
                # Keep image ref current; don't touch servo positions or description.
                existing.image = pose.get("image")
                existing.is_builtin = True
                s.add(existing)
            elif not existing.is_builtin:
                existing.is_builtin = True
                s.add(existing)
        s.commit()

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
                row = Expression(
                    name=name,
                    playback=playback,
                    default_duration_ms=dur_ms,
                    default_delay_ms=delay_ms,
                    default_easing=easing,
                    steps_json=steps_json,
                    emotion=emotion,
                    display_media=SEED_DISPLAY_MEDIA.get(name),
                    is_builtin=True,
                )
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    log.warning(
                        "seed.expression.emotion_taken name=%r emotion=%r — "
                        "another expression already claims this emotion; "
                        "inserting seed row without the emotion binding",
                        name,
                        emotion,
                    )
                    row = Expression(
                        name=name,
                        playback=playback,
                        default_duration_ms=dur_ms,
                        default_delay_ms=delay_ms,
                        default_easing=easing,
                        steps_json=steps_json,
                        emotion=None,
                        display_media=SEED_DISPLAY_MEDIA.get(name),
                        is_builtin=True,
                    )
                    s.add(row)
                    s.commit()
            else:
                # Existing built-in row: backfill display_media when the operator
                # hasn't set one (None) so already-seeded DBs pick up the new
                # single-media defaults without clobbering a user's own choice.
                dirty = False
                if not existing.is_builtin:
                    existing.is_builtin = True
                    dirty = True
                seed_media = SEED_DISPLAY_MEDIA.get(name)
                if existing.display_media is None and seed_media is not None:
                    existing.display_media = seed_media
                    dirty = True
                if dirty:
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
    # Explicitly clear image when the seed doesn't define one — prevents stale
    # image refs from persisting across resets on pure servo keyframes.
    if "image" not in pose:
        f.image = None
    f.is_builtin = True
    s.add(f)
    return f


def apply_all_video_frame_seeds(s: Session) -> list[Frame]:
    """Upsert every vid_* frame from the current seed. Used by the apply-seeds
    script to push updated image refs to a running server's DB."""
    out = []
    for name in sorted(_ALWAYS_REFRESH_IMAGE):
        out.append(apply_frame_seed(s, name))
    return out


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
    e.display_media = SEED_DISPLAY_MEDIA.get(name)
    e.is_builtin = True
    s.add(e)
    return e


def is_builtin_frame_name(name: str) -> bool:
    return name in SEED_FRAMES


def is_builtin_expression_name(name: str) -> bool:
    return any(row[0] == name for row in SEED_EXPRESSIONS)
