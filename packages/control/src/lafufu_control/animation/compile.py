"""Expression → resolved AnimatorIntentPlayExpression compiler.

The Expression DB row carries a JSON list of steps that reference Frames by
name. The animator wants a fully-resolved payload (each step has a concrete
pose + optional image), so this module looks up the named frames and packs
them into AnimatorPlayStep instances.
"""

import json
from collections.abc import Iterable

from lafufu_shared.schemas import (
    AnimatorIntentPlayExpression,
    AnimatorPlayStep,
    AnimatorPose,
)

from ..models.expression import Expression
from ..models.frame import Frame


def required_frame_names(expr: Expression) -> Iterable[str]:
    """Yield the frame name referenced by each step (one per step, ordered)."""
    raw = json.loads(expr.steps_json or "[]")
    for step in raw:
        yield step["frame"]


def compile_expression(
    expr: Expression, frames_by_name: dict[str, Frame]
) -> AnimatorIntentPlayExpression:
    """Resolve each step's frame reference into a concrete pose + image."""
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
