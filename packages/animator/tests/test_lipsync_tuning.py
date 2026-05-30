"""Lipsync tuning fixes (bench-tuning follow-ups).

1. The lipsync offset must be applied WITHOUT blocking the RMS subscription.
   NATS delivers a subscription's messages serially — it awaits the callback
   before handling the next message — so an inline ``await asyncio.sleep(offset)``
   in ``_on_tts_rms`` throttled the jaw to one update per offset while the agent
   publishes RMS every ~40ms. The backlog grew through the utterance (the jaw
   drifted behind the audio) and drained after the audio stopped (the mouth kept
   moving). The delay must run in its own task so the handler returns promptly.

2. ``gamma`` (the perceptual loudness curve that opens the mouth more on soft
   syllables) must be live-tunable like attack/release/offset, so soft-word
   articulation can be dialed in by eye on the hardware.
"""

import asyncio
import time

from lafufu_animator.lipsync import LipsyncEnvelope
from lafufu_animator.service import AnimatorService
from lafufu_shared import schemas
from lafufu_shared.testing import FakeDxlBus


def _svc() -> AnimatorService:
    return AnimatorService(bus=FakeDxlBus(), nats_url="nats://unused:4222")


async def test_lipsync_offset_does_not_block_the_handler():
    """A positive offset must NOT be an inline await — that serializes NATS
    delivery and backs the jaw up. The handler returns promptly; the jaw is
    applied ~offset later via a separate task."""
    svc = _svc()
    svc._lipsync_offset_s = 0.15
    jaw_initial = svc._target_pose.jaw

    msg = schemas.AgentTtsRms(ts=0.0, rms=1.0, mouth_target=1.0)
    t0 = time.monotonic()
    await svc._on_tts_rms("agent.tts.rms", msg)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.05, (
        f"handler blocked {elapsed * 1000:.0f}ms — the offset must not be an inline "
        f"await (it serializes NATS message delivery and backs the jaw up)"
    )
    # The apply is deferred by the offset, so the jaw hasn't moved yet.
    assert svc._target_pose.jaw == jaw_initial
    # ...but once the offset elapses, the deferred task drives the jaw.
    await asyncio.sleep(0.25)
    assert svc._target_pose.jaw != jaw_initial, "jaw must be applied ~offset later"


async def test_lipsync_offset_zero_applies_immediately():
    """With no offset the jaw applies inline (no needless task scheduling)."""
    svc = _svc()
    svc._lipsync_offset_s = 0.0
    jaw_initial = svc._target_pose.jaw

    msg = schemas.AgentTtsRms(ts=0.0, rms=1.0, mouth_target=1.0)
    await svc._on_tts_rms("agent.tts.rms", msg)

    assert svc._target_pose.jaw != jaw_initial, "with no offset the jaw applies immediately"


async def test_lipsync_gamma_is_live_tunable():
    """gamma must be settable live via animator.lipsync.gamma, like attack/release."""
    svc = _svc()
    assert svc._envelope.gamma == 0.7  # in-code default

    await svc._on_config_lipsync_gamma(
        "config.changed.animator.lipsync.gamma",
        schemas.ConfigChanged(key="animator.lipsync.gamma", value=0.5, source="admin"),
    )
    assert svc._envelope.gamma == 0.5


def test_lower_gamma_opens_more_for_soft_syllables():
    """Rationale anchor: a lower gamma opens the mouth more on a soft target —
    that's the knob for the 'soft words don't open enough' symptom."""
    soft = 0.3
    e_high = LipsyncEnvelope(gamma=0.7)
    e_low = LipsyncEnvelope(gamma=0.5)
    v_high = v_low = 0.0
    for _ in range(50):  # converge both to the soft target
        v_high = e_high.step(soft, 0.04)
        v_low = e_low.step(soft, 0.04)
    assert v_low > v_high, "lower gamma must open the mouth more on soft syllables"
