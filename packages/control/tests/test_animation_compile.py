"""Tests for the expression compiler."""

import json

import pytest
from lafufu_control.animation.compile import compile_expression, required_frame_names
from lafufu_control.models.expression import Expression
from lafufu_control.models.frame import Frame


def _frame(name, **over):
    base = {"head_lr": 2063, "head_ud": 3082, "eye": 2045, "jaw": 1728, "brow": 2075}
    base.update(over)
    return Frame(name=name, **base)


def test_compile_resolves_frames_to_poses():
    """Steps without overrides pass through None for duration/delay/easing."""
    f1 = _frame("a", head_lr=2100)
    f2 = _frame("b", head_lr=2200)
    expr = Expression(
        name="x",
        playback="loop",
        steps_json=json.dumps(
            [
                {"frame": "a", "duration_ms": 400},  # override preserved
                {"frame": "b"},  # all step-level fields default to None
            ]
        ),
        default_duration_ms=250,
        default_delay_ms=80,
        default_easing="ease-in-out",
    )
    payload = compile_expression(expr, {"a": f1, "b": f2})

    assert payload.name == "x"
    assert payload.playback == "loop"
    assert len(payload.steps) == 2
    assert payload.steps[0].pose.head_lr == 2100
    assert payload.steps[0].duration_ms == 400  # override
    assert payload.steps[1].pose.head_lr == 2200
    assert payload.steps[1].duration_ms is None  # falls through to default at play time


def test_compile_missing_frame_raises():
    expr = Expression(
        name="x",
        steps_json=json.dumps([{"frame": "ghost"}]),
    )
    with pytest.raises(KeyError):
        compile_expression(expr, {})


def test_compile_preserves_image_from_frame():
    f = _frame("a")
    f.image = "sprites/upload/foo.png"
    expr = Expression(
        name="x",
        steps_json=json.dumps([{"frame": "a"}]),
    )
    payload = compile_expression(expr, {"a": f})
    assert payload.steps[0].image == "sprites/upload/foo.png"


def test_required_frame_names():
    expr = Expression(
        name="x", steps_json=json.dumps([{"frame": "a"}, {"frame": "b"}, {"frame": "a"}])
    )
    assert list(required_frame_names(expr)) == ["a", "b", "a"]
