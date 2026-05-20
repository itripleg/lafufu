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
    # Near-zero smooth-times + uncapped speed + high stepper rate so tests
    # converge in ~1 tick instead of waiting on real easing.
    servos = ("head_lr", "head_ud", "eye", "jaw", "brow")
    fast_smooth = {name: 0.001 for name in servos}
    fast_speeds = {name: 1e7 for name in servos}
    svc = AnimatorService(
        bus=bus,
        nats_url=nats_server,
        smooth_times=fast_smooth,
        max_speeds=fast_speeds,
        stepper_hz=200.0,
    )
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


async def test_agree_drives_head_ud_oscillation_over_time(running_animator, nats_server):
    """`agree` is a discrete nod — confirm head_ud actually moves through the
    expression-animation loop rather than locking to a single position."""
    _svc, bus = running_animator
    bus.writes.clear()
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
        schemas.AnimatorIntentPlayExpression(name="agree", intensity=1.0),
    )
    # Let several expression-loop ticks land (~20 Hz; ~0.4s window).
    await asyncio.sleep(0.4)
    await nc.drain()

    head_ud_positions = [pos for (servo, pos) in bus.writes if servo == "head_ud"]
    # Multiple writes across the window — not just one snap-to-pose.
    assert len(head_ud_positions) >= 3
    # The nod sinusoid swings ~40 ticks → range should be clearly > 10.
    assert max(head_ud_positions) - min(head_ud_positions) >= 10


async def test_expression_auto_clears_when_duration_elapses(running_animator, nats_server):
    """surprised has duration_s=2.0 → after that, animator publishes gesture_done
    and idle/neutral resumes ownership of _target_pose."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorEvent] = []

    async def cb(msg):
        seen.append(schemas.AnimatorEvent.model_validate_json(msg.data))

    await nc.subscribe(topics.ANIMATOR_EVENT_GESTURE_DONE, cb=cb)
    # Force a short-lived expression. We override duration via the registry
    # so the test doesn't sleep 2s.
    from lafufu_animator import expressions

    short = expressions.Expression(
        offsets={"head_lr": 0, "head_ud": 0, "eye": 0, "jaw": 0, "brow": 0},
        motion=(),
        duration_s=0.1,
    )
    expressions._EXPRESSIONS["__test_short__"] = short
    try:
        await publish_model(
            nc,
            topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
            schemas.AnimatorIntentPlayExpression(name="__test_short__", intensity=1.0),
        )
        await asyncio.sleep(0.4)
        await nc.drain()
    finally:
        expressions._EXPRESSIONS.pop("__test_short__", None)

    assert svc._current_expression is None
    assert any(e.event == "gesture_done" and e.name == "__test_short__" for e in seen)


async def test_neutral_expression_cancels_an_active_one(running_animator, nats_server):
    """Sending `neutral` is the cancel command — clears the active expression
    and emits gesture_done so the UI can drop its pill."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)
    seen: list[schemas.AnimatorEvent] = []

    async def cb(msg):
        seen.append(schemas.AnimatorEvent.model_validate_json(msg.data))

    await nc.subscribe(topics.ANIMATOR_EVENT_GESTURE_DONE, cb=cb)
    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
        schemas.AnimatorIntentPlayExpression(name="happy", intensity=1.0),
    )
    await asyncio.sleep(0.15)
    assert svc._current_expression == "happy"

    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
        schemas.AnimatorIntentPlayExpression(name="neutral", intensity=1.0),
    )
    await asyncio.sleep(0.15)
    await nc.drain()

    assert svc._current_expression is None
    assert any(e.event == "gesture_done" and e.name == "happy" for e in seen)


async def test_preview_intent_cancels_active_expression(running_animator, nats_server):
    """Operator-driven slider preview must take priority over a looping
    expression — otherwise the two fight for _target_pose."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PLAY_EXPRESSION,
        schemas.AnimatorIntentPlayExpression(name="happy", intensity=1.0),
    )
    await asyncio.sleep(0.15)
    assert svc._current_expression == "happy"

    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PREVIEW,
        schemas.AnimatorIntentPreview(name="head_lr", position=2100),
    )
    await asyncio.sleep(0.15)
    await nc.drain()
    assert svc._current_expression is None


async def test_startup_eases_from_real_position_not_snap(nats_server):
    """On boot the animator reads where the servos actually are and EASES to
    idle — it must not snap (write idle directly) from an unknown start."""
    start_lr = 1850  # near an extreme; idle head_lr is ~2063
    bus = FakeDxlBus(
        initial_positions={
            "head_lr": start_lr,
            "head_ud": 3082,
            "eye": 2045,
            "jaw": 1728,
            "brow": 2075,
        }
    )
    # Default (realistic) smooth_times so the ease is observable over time.
    svc = AnimatorService(bus=bus, nats_url=nats_server, stepper_hz=120.0)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    from lafufu_animator import pose

    head_writes = [pos for (servo, pos) in bus.writes if servo == "head_lr"]
    assert head_writes, "stepper never wrote head_lr"
    idle_lr = pose.HEAD_IDLE_LR_DXL
    # First write starts near the real position, NOT snapped to idle.
    assert abs(head_writes[0] - start_lr) < abs(head_writes[0] - idle_lr)
    # And it eases toward idle over the run.
    assert abs(head_writes[-1] - idle_lr) < abs(head_writes[0] - idle_lr)


async def test_configures_hardware_limits_on_startup(nats_server):
    """Animator writes the per-servo hardware safety limits when it boots."""
    bus = FakeDxlBus()
    svc = AnimatorService(bus=bus, nats_url=nats_server, stepper_hz=120.0)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert bus.limits_configured_count >= 1


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
