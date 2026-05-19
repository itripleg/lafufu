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


def test_agree_oscillates_head_ud_so_lafufu_actually_nods():
    """`agree` is the 'yes' gesture — head_ud must visibly cycle up and down."""
    base = pose.idle_pose()
    expr = expressions.get("agree")
    # The motion table must include a head_ud sinusoid with non-trivial
    # amplitude, otherwise nothing actually nods.
    head_ud_motions = [m for m in expr.motion if m.servo == "head_ud"]
    assert head_ud_motions, "agree expression has no head_ud motion → nothing nods"
    assert head_ud_motions[0].amp_ticks >= 20

    # Sample over one full cycle and confirm we hit both extremes.
    freq = head_ud_motions[0].freq_hz
    period = 1.0 / freq
    samples = [
        expressions.compute_target("agree", base, t).head_ud
        for t in [period * i / 20 for i in range(20)]
    ]
    assert max(samples) > base.head_ud + 10
    assert min(samples) < base.head_ud  # dips below (toward UP since head_ud+ = down)


def test_disagree_oscillates_head_lr_so_lafufu_actually_shakes():
    """`disagree` is the 'no' gesture — head_lr must visibly swing."""
    base = pose.idle_pose()
    expr = expressions.get("disagree")
    head_lr_motions = [m for m in expr.motion if m.servo == "head_lr"]
    assert head_lr_motions
    freq = head_lr_motions[0].freq_hz
    period = 1.0 / freq
    samples = [
        expressions.compute_target("disagree", base, t).head_lr
        for t in [period * i / 20 for i in range(20)]
    ]
    # Swings both directions of the idle center
    assert max(samples) > base.head_lr + 10
    assert min(samples) < base.head_lr - 10


def test_compute_target_includes_static_offset_at_t_zero_for_motionless_expr():
    """surprised has no motion — at t=0, target is exactly idle + offset (clamped)."""
    base = pose.idle_pose()
    out = expressions.compute_target("surprised", base, 0.0)
    # Jaw opens (offset -80 from idle, toward MOUTH_OPEN)
    assert out.jaw < base.jaw


def test_is_expired_respects_duration():
    """Gestures with a duration auto-expire so the UI can clear their pill."""
    # agree has a duration → expires after that long
    duration = expressions.get("agree").duration_s
    assert duration is not None
    assert not expressions.is_expired("agree", duration - 0.1)
    assert expressions.is_expired("agree", duration + 0.1)

    # Sustained emotions like sad never expire on their own
    assert expressions.get("sad").duration_s is None
    assert not expressions.is_expired("sad", 100.0)


def test_compute_target_falls_back_to_neutral_for_unknown_name():
    base = pose.idle_pose()
    out = expressions.compute_target("madeup", base, 0.5)
    # Neutral has no offset + no motion → output equals base
    assert (out.head_lr, out.head_ud, out.eye, out.jaw, out.brow) == (
        base.head_lr,
        base.head_ud,
        base.eye,
        base.jaw,
        base.brow,
    )
