"""Tests for the keyframe player."""

from lafufu_animator.keyframe_player import KeyframePlayer
from lafufu_animator.pose import idle_pose
from lafufu_shared.schemas import (
    AnimatorIntentPlayExpression,
    AnimatorPlayStep,
)


def _payload(**over):
    base = {
        "name": "x",
        "playback": "once",
        "default_duration_ms": 200,
        "default_delay_ms": 0,
        "default_easing": "linear",
        "steps": [],
    }
    base.update(over)
    return AnimatorIntentPlayExpression(**base)


def test_single_step_interpolates_from_start_to_target():
    target = idle_pose().model_copy(update={"head_lr": 2200})
    start = idle_pose().model_copy(update={"head_lr": 2000})
    payload = _payload(
        steps=[AnimatorPlayStep(pose=target)],
        default_duration_ms=200,
    )
    p = KeyframePlayer(payload=payload, start_pose=start, now_ms=0)

    assert p.pose_at(0).head_lr == 2000  # start
    assert p.pose_at(200).head_lr == 2200  # arrived
    mid = p.pose_at(100).head_lr
    assert 2050 < mid < 2150  # roughly in-between (linear easing → exactly ~2100)
    assert p.is_done(200) is True


def test_loop_wraps():
    start = idle_pose()
    s1 = AnimatorPlayStep(pose=idle_pose().model_copy(update={"head_lr": 2100}))
    s2 = AnimatorPlayStep(pose=idle_pose().model_copy(update={"head_lr": 2200}))
    payload = _payload(playback="loop", steps=[s1, s2], default_duration_ms=200)
    p = KeyframePlayer(payload=payload, start_pose=start, now_ms=0)

    assert p.is_done(400) is False
    mid_second_cycle = p.pose_at(500).head_lr  # wraps to elapsed=100 in cycle
    assert 2050 < mid_second_cycle < 2200


def test_shuffle_advances_with_jitter():
    start = idle_pose()
    steps = [
        AnimatorPlayStep(pose=idle_pose().model_copy(update={"head_lr": v}))
        for v in (1900, 2000, 2100, 2200)
    ]
    payload = _payload(
        playback="shuffle", steps=steps, default_duration_ms=100, default_delay_ms=50
    )
    p = KeyframePlayer(payload=payload, start_pose=start, now_ms=0, rng_seed=1)

    samples = [p.pose_at(t).head_lr for t in (0, 200, 400, 600, 800)]
    distinct = set(samples)
    assert len(distinct) >= 2  # multiple different positions visited
