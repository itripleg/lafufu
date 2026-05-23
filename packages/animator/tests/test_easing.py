"""Tests for animator.easing."""

import pytest
from lafufu_animator.easing import ease


def test_linear_identity():
    assert ease("linear", 0.0) == 0.0
    assert ease("linear", 1.0) == 1.0
    assert ease("linear", 0.5) == 0.5


def test_ease_in_below_half_at_mid():
    """ease-in starts slow — at t=0.5, output should be < 0.5."""
    assert ease("ease-in", 0.5) < 0.5
    assert ease("ease-in", 0.0) == 0.0
    assert ease("ease-in", 1.0) == 1.0


def test_ease_out_above_half_at_mid():
    """ease-out finishes fast — at t=0.5, output should be > 0.5."""
    assert ease("ease-out", 0.5) > 0.5
    assert ease("ease-out", 0.0) == 0.0
    assert ease("ease-out", 1.0) == 1.0


def test_ease_in_out_exactly_half_at_mid():
    """Smoothstep is symmetric — exactly 0.5 at t=0.5."""
    assert ease("ease-in-out", 0.5) == pytest.approx(0.5)
    assert ease("ease-in-out", 0.0) == 0.0
    assert ease("ease-in-out", 1.0) == 1.0


def test_unknown_curve_falls_back_to_linear():
    assert ease("not-a-curve", 0.3) == pytest.approx(0.3)
    assert ease("", 0.7) == pytest.approx(0.7)


def test_input_clamping():
    """Out-of-range t is clamped before applying the curve."""
    assert ease("linear", -1.0) == 0.0
    assert ease("linear", 2.0) == 1.0
    assert ease("ease-in", -0.5) == 0.0
    assert ease("ease-in", 1.5) == 1.0
