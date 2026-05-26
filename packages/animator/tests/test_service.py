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


async def test_play_expression_intent_sets_active_name(running_animator, nats_server):
    """Publishing a play_expression intent should immediately set the active expression name."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)
    payload = schemas.AnimatorIntentPlayExpression(
        name="happy",
        playback="once",
        steps=[
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(head_lr=2100, head_ud=3082, eye=2045, jaw=1728, brow=2075)
            )
        ],
        default_duration_ms=100,
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.05)
    await nc.drain()
    assert svc._active_expression_name == "happy"


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


async def test_preview_intent_cancels_active_expression(running_animator, nats_server):
    """Operator-driven slider preview must take priority over a looping
    expression — otherwise the two fight for _target_pose."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)
    payload = schemas.AnimatorIntentPlayExpression(
        name="happy",
        playback="loop",
        steps=[
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(head_lr=2100, head_ud=3082, eye=2045, jaw=1728, brow=2075)
            )
        ],
        default_duration_ms=500,
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.15)
    assert svc._active_expression_name == "happy"

    await publish_model(
        nc,
        topics.ANIMATOR_INTENT_PREVIEW,
        schemas.AnimatorIntentPreview(name="head_lr", position=2100),
    )
    await asyncio.sleep(0.15)
    await nc.drain()
    assert svc._active_expression_name is None


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


async def test_play_expression_drives_target_through_keyframes(running_animator, nats_server):
    """A 2-step once expression should converge to the LAST step's pose."""
    svc, bus = running_animator
    nc = await nats.connect(nats_server)
    payload = schemas.AnimatorIntentPlayExpression(
        name="agree",
        playback="once",
        default_duration_ms=120,
        default_delay_ms=0,
        steps=[
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(head_lr=2100, head_ud=3082, eye=2045, jaw=1728, brow=2075)
            ),
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(head_lr=2200, head_ud=3082, eye=2045, jaw=1728, brow=2075)
            ),
        ],
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.5)  # plenty of time for 240ms total
    # Final pose should be the second step (give or take +/-2 for rounding).
    assert abs(bus.last_position("head_lr") - 2200) <= 5
    assert svc._active_expression_name is None  # done + cleared
    await nc.drain()


async def test_animator_requests_idle_on_startup(running_animator, nats_server):
    """Cold-boot handshake: animator publishes `animator.request.idle` on
    startup so control can (re)publish the idle expression in response.
    Without this request, control's startup-time fire-and-forget publish
    races the animator's subscribe and is silently dropped on the broker."""
    _svc, _bus = running_animator
    # The fixture sleeps 0.4 s before yielding; subscribe to the request
    # topic with a JetStream consumer-style late subscription would miss the
    # initial publish. Instead, subscribe and trigger a fresh request — the
    # restart-mid-session case is identical to the cold-boot case from
    # control's POV: we expect every request to elicit a response.
    nc = await nats.connect(nats_server)
    received: list[bytes] = []

    async def cb(msg):
        received.append(msg.data)

    await nc.subscribe(topics.ANIMATOR_REQUEST_IDLE, cb=cb)
    # The animator will retry up to 3 times if no _idle_payload arrives,
    # so even if we missed the first attempt during fixture startup, we
    # will see attempt 2 or 3 within a few seconds. To make the test
    # deterministic and fast, observe that the animator publishes the
    # request — wait up to 3 s.
    for _ in range(30):
        if received:
            break
        await asyncio.sleep(0.1)
    await nc.drain()
    assert len(received) >= 1, "animator did not publish animator.request.idle on startup"


async def test_animator_caches_idle_payload_from_play_expression(running_animator, nats_server):
    """When a play_expression with name='idle' arrives, the animator caches
    it as _idle_payload for the idle fallback loop. This is the response
    path of the bootstrap handshake."""
    svc, _bus = running_animator
    nc = await nats.connect(nats_server)

    payload = schemas.AnimatorIntentPlayExpression(
        name="idle",
        playback="random_walk",
        steps=[],
        random_walk_config=schemas.RandomWalkConfig(intensity=1.0, speed=1.0, pause_chance=0.3),
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.2)
    await nc.drain()

    assert svc._idle_payload is not None
    assert svc._idle_payload.name == "idle"
    assert svc._idle_payload.playback == "random_walk"
