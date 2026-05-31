"""Pure helpers for the fortune-card print path (lucky numbers, etc.)."""

import random

# Module-level RNG so tests can inject a seeded ``random.Random`` while the
# default stays injectable (NOT the global ``random`` module — that would make
# the draw entangled with unrelated callers seeding the global).
_RNG = random.Random()


def generate_lucky_numbers(
    count: int, max_n: int, *, rng: random.Random | None = None
) -> list[int]:
    """Draw ``count`` unique integers in ``1..max_n``, sorted ascending.

    - ``count <= 0`` → ``[]``.
    - ``count`` is clamped to at most ``max_n`` (can't draw more unique numbers
      than the range allows).
    - ``max_n`` is clamped to a sane floor of 1.
    - ``rng`` is an optional ``random.Random`` for deterministic tests; defaults
      to a module-level instance.
    """
    if count <= 0:
        return []
    max_n = max(1, int(max_n))
    k = min(int(count), max_n)
    r = rng if rng is not None else _RNG
    return sorted(r.sample(range(1, max_n + 1), k))
