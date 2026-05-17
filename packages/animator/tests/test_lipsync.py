from lafufu_animator import pose
from lafufu_animator.lipsync import LipsyncEnvelope, rms_to_jaw_dxl


def test_jaw_dxl_at_zero_rms_is_closed():
    assert rms_to_jaw_dxl(0.0) == pose.MOUTH_CLOSE_DXL


def test_jaw_dxl_at_max_rms_approaches_open():
    val = rms_to_jaw_dxl(1.0)
    # MOUTH_OPEN_DXL < MOUTH_CLOSE_DXL (open is a lower tick value), so opening goes DOWN
    assert val <= pose.MOUTH_OPEN_DXL + 5


def test_envelope_attack_smooths_jumps_upward():
    env = LipsyncEnvelope(attack_s=0.05, release_s=0.10, gamma=0.7)
    # Feed RMS jumping from 0 to 1 in one tick (dt=20ms)
    out1 = env.step(target=1.0, dt=0.02)
    # Should not jump straight to 1
    assert 0.0 < out1 < 1.0


def test_envelope_release_smooths_drops():
    env = LipsyncEnvelope(attack_s=0.05, release_s=0.10, gamma=0.7)
    env.step(target=1.0, dt=0.1)  # ramp up
    high = env.step(target=1.0, dt=0.1)
    low = env.step(target=0.0, dt=0.02)  # immediate target drop
    # Release is slower than attack, so should still be above the gap
    assert low > 0.0
    assert low < high


def test_envelope_gamma_compresses_low_end():
    env_linear = LipsyncEnvelope(attack_s=0.0, release_s=0.0, gamma=1.0)
    env_curve = LipsyncEnvelope(attack_s=0.0, release_s=0.0, gamma=0.5)
    out_linear = env_linear.step(target=0.5, dt=0.1)
    out_curve = env_curve.step(target=0.5, dt=0.1)
    # gamma < 1 boosts low-end values
    assert out_curve > out_linear
