"""Adaptive lipsync RMS normalization — the streaming equivalent of the legacy
monolith's per-utterance percentile normalization.

The old modular code used a fixed ``rms / 8000`` divisor: a quiet TTS voice
barely opened the mouth, a loud one clipped wide open. These tests pin the
adaptive behaviour — the mouth always uses its full travel and tracks
*relative* loudness, whatever the voice's absolute level.
"""

from lafufu_agent.lipsync import LipsyncNormalizer, percentile_sorted


def test_percentile_sorted_endpoints_and_midpoint():
    s = list(range(0, 100))  # 0..99
    assert percentile_sorted(s, 0.0) == 0
    assert percentile_sorted(s, 1.0) == 99
    assert percentile_sorted([], 0.5) == 0.0  # empty is safe


def test_loud_syllable_in_a_quiet_utterance_still_opens_fully():
    """A quiet line (RMS ~400-1400) must still reach near-full open on its
    loudest frames. The old fixed rms/8000 would top out around 0.17."""
    norm = LipsyncNormalizer(window=200, deadzone=0.05)
    for v in range(400, 1400, 10):  # warm the window with a quiet spread
        norm.update(v)
    assert norm.update(1390) > 0.8


def test_quiet_frame_maps_near_closed():
    norm = LipsyncNormalizer(window=200, deadzone=0.05)
    for v in range(400, 1400, 10):
        norm.update(v)
    assert norm.update(420) < 0.15


def test_loud_utterance_does_not_clip_everything_to_open():
    """The old rms/8000 pinned every frame above 8000 to a fully-open mouth.
    Adaptive normalization keeps mid-loudness frames in the middle."""
    norm = LipsyncNormalizer(window=200, deadzone=0.05)
    for v in range(5000, 15000, 100):
        norm.update(v)
    mid = norm.update(10000)
    assert 0.2 < mid < 0.8


def test_deadzone_snaps_near_silence_fully_closed():
    norm = LipsyncNormalizer(window=200, deadzone=0.05)
    for v in range(400, 1400, 10):
        norm.update(v)
    assert norm.update(400) == 0.0


def test_cold_start_is_safe_and_bounded():
    """First sample ever — no divide-by-zero on an empty/degenerate window."""
    norm = LipsyncNormalizer(window=100)
    out = norm.update(3000.0)
    assert 0.0 <= out <= 1.0


def test_output_always_in_unit_range():
    norm = LipsyncNormalizer(window=50)
    for v in (0, 100, 8000, 19999, 32000, 50):
        assert 0.0 <= norm.update(float(v)) <= 1.0
