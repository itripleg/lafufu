"""Expression → resolved AnimatorIntentPlayExpression compiler.

The Expression DB row carries a JSON list of steps that reference Frames by
name. The animator wants a fully-resolved payload (each step has a concrete
pose + optional image), so this module looks up the named frames and packs
them into AnimatorPlayStep instances.

For playback='random_walk', steps_json holds a config dict (intensity, speed,
pause_chance) instead of a step list; the compiler parses it into
random_walk_config and emits an empty steps list.
"""

import json
from collections.abc import Iterable

from lafufu_shared.schemas import (
    AnimatorIntentPlayExpression,
    AnimatorPlayStep,
    AnimatorPose,
    RandomWalkConfig,
)

from ..models.expression import Expression
from ..models.frame import Frame


def required_frame_names(expr: Expression) -> Iterable[str]:
    """Yield the frame name referenced by each step (one per step, ordered).

    random_walk expressions reference no frames — yields nothing."""
    if expr.playback == "random_walk":
        return
    raw = json.loads(expr.steps_json or "[]")
    if not isinstance(raw, list):
        return
    for step in raw:
        yield step["frame"]


def compile_expression(
    expr: Expression, frames_by_name: dict[str, Frame]
) -> AnimatorIntentPlayExpression:
    """Resolve each step's frame reference into a concrete pose + image.

    For random_walk, parse the config dict and pass it through; steps stays
    empty (the player generates motion from the config + start_pose)."""
    if expr.playback == "random_walk":
        cfg_raw = json.loads(expr.steps_json or "{}")
        cfg = (
            RandomWalkConfig.model_validate(cfg_raw)
            if isinstance(cfg_raw, dict)
            else RandomWalkConfig()
        )
        return AnimatorIntentPlayExpression(
            name=expr.name,
            playback="random_walk",
            steps=[],
            default_duration_ms=expr.default_duration_ms,
            default_delay_ms=expr.default_delay_ms,
            default_easing=expr.default_easing,
            random_walk_config=cfg,
        )

    raw_steps = json.loads(expr.steps_json or "[]")
    resolved: list[AnimatorPlayStep] = []
    for step in raw_steps:
        frame_name = step["frame"]
        frame = frames_by_name[frame_name]  # KeyError if caller missed pre-fetch
        resolved.append(
            AnimatorPlayStep(
                pose=AnimatorPose(
                    head_lr=frame.head_lr,
                    head_ud=frame.head_ud,
                    eye=frame.eye,
                    jaw=frame.jaw,
                    brow=frame.brow,
                ),
                image=frame.image,
                duration_ms=step.get("duration_ms"),
                delay_ms=step.get("delay_ms"),
                easing=step.get("easing"),
            )
        )
    return AnimatorIntentPlayExpression(
        name=expr.name,
        playback=expr.playback,  # type: ignore[arg-type]
        steps=resolved,
        default_duration_ms=expr.default_duration_ms,
        default_delay_ms=expr.default_delay_ms,
        default_easing=expr.default_easing,
    )
