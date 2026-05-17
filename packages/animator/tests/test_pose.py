from lafufu_animator import pose


def test_clamp_within_range():
    assert pose.clamp(1500, 1000, 2000) == 1500


def test_clamp_below_returns_lo():
    assert pose.clamp(500, 1000, 2000) == 1000


def test_clamp_above_returns_hi():
    assert pose.clamp(2500, 1000, 2000) == 2000


def test_clamp_handles_reversed_bounds():
    """min/max should be inferred, not assumed ordered."""
    assert pose.clamp(1500, 2000, 1000) == 1500
    assert pose.clamp(500, 2000, 1000) == 1000


def test_dxl_from_deg_endpoints():
    # 0 degrees → midpoint
    assert pose.dxl_from_deg(0.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 1500
    # max → pos_max
    assert pose.dxl_from_deg(10.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 2000
    # min → pos_min
    assert pose.dxl_from_deg(-10.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 1000


def test_dxl_from_deg_clamps_out_of_range():
    assert pose.dxl_from_deg(100.0, deg_min=-10.0, deg_max=10.0, pos_min=1000, pos_max=2000) == 2000


def test_deg_from_dxl_round_trip():
    deg = pose.deg_from_dxl(1500, pos_min=1000, pos_max=2000, deg_min=-10.0, deg_max=10.0)
    assert abs(deg - 0.0) < 1e-6


def test_lerp_int_midpoint():
    assert pose.lerp_int(1000, 2000, 0.5) == 1500


def test_lerp_int_endpoints():
    assert pose.lerp_int(1000, 2000, 0.0) == 1000
    assert pose.lerp_int(1000, 2000, 1.0) == 2000


def test_idle_pose_constants_match_spec():
    p = pose.idle_pose()
    assert p.head_lr == pose.HEAD_IDLE_LR_DXL
    assert p.head_ud == pose.HEAD_IDLE_UD_DXL
    assert p.jaw == pose.MOUTH_CLOSE_DXL
    assert p.eye == pose.EYE_IDLE_DXL
    assert p.brow == pose.BROW_IDLE_DXL
