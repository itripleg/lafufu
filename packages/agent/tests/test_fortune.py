"""Tests for the lucky-number generator (fortune.py)."""

import random

from lafufu_agent.fortune import generate_lucky_numbers


def test_count_range_uniqueness_and_sorted() -> None:
    nums = generate_lucky_numbers(4, 99)
    assert len(nums) == 4
    assert len(set(nums)) == 4  # unique
    assert nums == sorted(nums)  # ascending
    assert all(1 <= n <= 99 for n in nums)


def test_count_zero_returns_empty() -> None:
    assert generate_lucky_numbers(0, 99) == []


def test_count_negative_returns_empty() -> None:
    assert generate_lucky_numbers(-3, 99) == []


def test_count_clamped_to_max_n() -> None:
    # Can't draw more unique numbers than the range allows.
    nums = generate_lucky_numbers(10, 5)
    assert nums == [1, 2, 3, 4, 5]


def test_max_n_floored_to_one() -> None:
    # max_n below 1 is clamped to a sane floor of 1.
    nums = generate_lucky_numbers(3, 0)
    assert nums == [1]


def test_determinism_with_seeded_rng() -> None:
    a = generate_lucky_numbers(5, 99, rng=random.Random(1234))
    b = generate_lucky_numbers(5, 99, rng=random.Random(1234))
    assert a == b
    assert len(a) == 5


def test_different_seeds_differ() -> None:
    a = generate_lucky_numbers(6, 99, rng=random.Random(1))
    b = generate_lucky_numbers(6, 99, rng=random.Random(2))
    # Overwhelmingly likely to differ; guards against ignoring rng.
    assert a != b
