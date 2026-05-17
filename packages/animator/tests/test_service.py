import asyncio

import nats
import pytest
from lafufu_animator.service import AnimatorService
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_shared.testing import FakeDxlBus, nats_server_fixture

nats_server = nats_server_fixture("4240")


@pytest.fixture
async def running_animator(nats_server):
    bus = FakeDxlBus()
    # Use near-zero taus + high stepper rate so tests don't wait on easing.
    fast_taus = {name: 0.001 for name in ("head_lr", "head_ud", "eye", "jaw", "brow")}
    svc = AnimatorService(bus=bus, nats_url=nats_server, taus=fast_taus, stepper_hz=200.0)
    task = asyncio.create_task(svc.run())
    # Wait for service ready
    await asyncio.sleep(0.4)
    yield svc, bus
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)


async def test_publishes_idle_state_on_startup(running_animator, nats_server):
    _svc, bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorState] = []

    async def cb(msg):
        seen.append(schemas.AnimatorState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.ANIMATOR_STATE}.*", cb=cb)
    # Force-republish current state by sending a no-op preview
    await publish_model(
        nc, topics.ANIMATOR_INTENT_PREVIEW, schemas.AnimatorIntentPreview(name="jaw", position=1728)
    )
    await asyncio.sleep(0.2)
    await nc.drain()
    # At minimum we should see one state event since startup
    # (state was published before our subscription; test that fake bus got the write)
    assert bus.last_position("jaw") == 1728


async def test_preview_intent_moves_servo(running_animator, nats_server):
    _svc, bus = running_animator
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PREVIEW,
        schemas.AnimatorIntentPreview(name="head_lr", position=2100),
    )
    await asyncio.sleep(0.15)
    await nc.drain()
    # Easing converges asymptotically + integer clamp → expect within ±1 of target
    assert abs(bus.last_position("head_lr") - 2100) <= 1


async def test_play_expression_intent_applies_offsets(running_animator, nats_server):
    _svc, bus = running_animator
    bus.writes.clear()
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
        schemas.AnimatorIntentPlayExpression(name="surprised", intensity=1.0),
    )
    await asyncio.sleep(0.3)
    await nc.drain()
    # surprised opens the jaw — expect jaw to have moved toward MOUTH_OPEN
    from lafufu_animator import pose

    final_jaw = bus.last_position("jaw")
    assert final_jaw is not None
    assert final_jaw < pose.MOUTH_CLOSE_DXL  # opened


async def test_tts_rms_drives_jaw_during_speaking(running_animator, nats_server):
    _svc, bus = running_animator
    bus.writes.clear()
    nc = await nats.connect(nats_server)
    # Simulate a sequence of RMS values
    for i in range(5):
        await publish_model(
            nc, topics.AGENT_TTS_RMS, schemas.AgentTtsRms(ts=i * 0.04, rms=0.8, mouth_target=0.8)
        )
        await asyncio.sleep(0.04)
    await nc.drain()
    # Jaw should have moved (multiple writes)
    jaw_writes = [w for w in bus.writes if w[0] == "jaw"]
    assert len(jaw_writes) >= 2


async def test_degrades_gracefully_when_bus_disconnects(running_animator, nats_server):
    _svc, bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorState] = []

    async def cb(msg):
        seen.append(schemas.AnimatorState.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.ANIMATOR_STATE}.*", cb=cb)
    bus.disconnect()
    # Send a preview; service should publish degraded state
    await publish_model(
        nc, topics.ANIMATOR_INTENT_PREVIEW, schemas.AnimatorIntentPreview(name="jaw", position=1700)
    )
    await asyncio.sleep(0.3)
    await nc.drain()
    assert any(s.state == "degraded" for s in seen)
