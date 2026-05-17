"""Lipsync envelope: smooths a fast-changing RMS into a jaw setpoint.

Inputs come from `agent.tts.rms` (already normalized 0..1 by agent).
Output drives the jaw servo.
"""

import math

from . import pose


def rms_to_jaw_dxl(target: float) -> int:
    """Map a normalized [0,1] mouth-target value to DXL jaw position.

    target=0 → closed (MOUTH_CLOSE_DXL)
    target=1 → open  (MOUTH_OPEN_DXL)
    Note that MOUTH_OPEN < MOUTH_CLOSE in DXL ticks.
    """
    target = max(0.0, min(1.0, target))
    return pose.lerp_int(pose.MOUTH_CLOSE_DXL, pose.MOUTH_OPEN_DXL, target)


class LipsyncEnvelope:
    """First-order envelope with separate attack/release time constants.

    `gamma` is a post-shaping exponent applied to the target BEFORE the envelope:
    gamma < 1 boosts low values (more responsive to soft speech).
    """

    def __init__(self, attack_s: float = 0.03, release_s: float = 0.08, gamma: float = 0.7) -> None:
        self.attack_s = attack_s
        self.release_s = release_s
        self.gamma = gamma
        self._env = 0.0

    def step(self, target: float, dt: float) -> float:
        """Advance one timestep. Returns smoothed value in [0,1]."""
        shaped = math.pow(max(0.0, min(1.0, target)), max(1e-6, self.gamma))
        tau = max(1e-6, self.attack_s) if shaped > self._env else max(1e-6, self.release_s)
        alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 1.0
        self._env = self._env + (shaped - self._env) * alpha
        return self._env

    def reset(self) -> None:
        self._env = 0.0

    @property
    def value(self) -> float:
        return self._env
