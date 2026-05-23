"""Cubic-style easing curves for keyframe interpolation.

`ease(curve, t)` takes a normalised t ∈ [0,1] (clamped if outside) and the
curve name. Unknown curves fall through to `linear` so we don't crash on a
typo or a curve we haven't implemented yet.
"""


def _clamp01(t: float) -> float:
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


def ease(curve: str, t: float) -> float:
    t = _clamp01(t)
    if curve == "ease-in":
        return t * t
    if curve == "ease-out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if curve == "ease-in-out":
        return t * t * (3.0 - 2.0 * t)
    # "linear" or any unknown name → identity
    return t
