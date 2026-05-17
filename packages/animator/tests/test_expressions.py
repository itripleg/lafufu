from lafufu_animator import expressions, pose


def test_list_expressions_returns_all_emotions():
    names = expressions.list_names()
    for required in ("happy", "sad", "angry", "surprised", "neutral", "agree", "disagree"):
        assert required in names


def test_get_offsets_returns_pose_deltas():
    offsets = expressions.get_offsets("happy", intensity=1.0)
    # Returns a dict mapping servo name → offset (delta from idle, in DXL ticks)
    assert set(offsets.keys()) == {"head_lr", "head_ud", "eye", "jaw", "brow"}
    # neutral should be all zeros
    neutral = expressions.get_offsets("neutral", intensity=1.0)
    assert all(v == 0 for v in neutral.values())


def test_intensity_scales_offsets_linearly():
    half = expressions.get_offsets("happy", intensity=0.5)
    full = expressions.get_offsets("happy", intensity=1.0)
    for k in half:
        assert abs(half[k] - full[k] / 2) <= 1  # allow rounding


def test_unknown_expression_returns_neutral():
    assert expressions.get_offsets("totally_made_up") == expressions.get_offsets("neutral")


def test_apply_offsets_clamps_to_safe_range():
    base = pose.idle_pose()
    # Apply an extreme offset
    out = expressions.apply_offsets(
        base, {"jaw": 9999, "head_lr": 0, "head_ud": 0, "eye": 0, "brow": 0}
    )
    # jaw should be clamped to its safe max
    lo, hi = pose.CLAMP["jaw"]
    assert out.jaw == max(lo, hi)
