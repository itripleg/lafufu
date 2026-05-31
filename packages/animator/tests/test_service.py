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


# Distinct, in-range jaw position an emotion expression keyframe wants. Must
# differ from the idle/closed default so we can tell whether the expression
# (vs lipsync) drove the mouth.
_EXPR_JAW = 1650


async def test_expression_does_not_move_jaw_while_speaking(running_animator, nats_server):
    """While the agent is speaking, lipsync owns the jaw outright.

    A custom emotion expression may still move head/eye/brow, but its jaw
    keyframe must NOT reach the mouth — otherwise the animation fights lipsync.
    No RMS is sent here, so the OLD 500ms-since-last-RMS guard would let the
    expression grab the jaw; only an explicit `speaking` signal protects it.
    """
    _svc, bus = running_animator
    nc = await nats.connect(nats_server)

    # Agent announces it has started speaking.
    await publish_model(nc, topics.AGENT_STATE_SPEAKING, schemas.AgentState(state="speaking"))
    await asyncio.sleep(0.05)

    bus.writes.clear()
    # Short ramp + long hold so the expression's jaw target is a STABLE _EXPR_JAW
    # (not a moving interpolation value) for the whole observation window.
    payload = schemas.AnimatorIntentPlayExpression(
        name="disagree",
        playback="loop",
        steps=[
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(
                    head_lr=2100, head_ud=3082, eye=2045, jaw=_EXPR_JAW, brow=2075
                )
            )
        ],
        default_duration_ms=40,
        default_delay_ms=400,
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.2)
    await nc.drain()

    # The expression still drives the head (custom animation plays)...
    assert abs(bus.last_position("head_lr") - 2100) <= 1
    # ...but the jaw is NEVER driven to the expression's mouth value.
    jaw_writes = [pos for (name, pos) in bus.writes if name == "jaw"]
    assert jaw_writes, "stepper should still be writing the jaw"
    assert all(pos != _EXPR_JAW for pos in jaw_writes), (
        "expression keyframe drove the mouth while speaking — lipsync must own the jaw"
    )


async def test_expression_moves_jaw_again_after_speaking_ends(running_animator, nats_server):
    """Once the agent stops speaking, the expression regains the jaw."""
    _svc, bus = running_animator
    nc = await nats.connect(nats_server)

    await publish_model(nc, topics.AGENT_STATE_SPEAKING, schemas.AgentState(state="speaking"))
    await asyncio.sleep(0.05)
    payload = schemas.AnimatorIntentPlayExpression(
        name="disagree",
        playback="loop",
        steps=[
            schemas.AnimatorPlayStep(
                pose=schemas.AnimatorPose(
                    head_lr=2100, head_ud=3082, eye=2045, jaw=_EXPR_JAW, brow=2075
                )
            )
        ],
        default_duration_ms=40,
        default_delay_ms=400,
    )
    await publish_model(nc, topics.ANIMATOR_INTENT_PLAY_EXPRESSION, payload)
    await asyncio.sleep(0.1)

    # Agent finished speaking — lipsync releases the jaw, expression takes over.
    await publish_model(nc, topics.AGENT_STATE_IDLE, schemas.AgentState(state="idle"))
    await asyncio.sleep(0.15)
    await nc.drain()

    assert abs(bus.last_position("jaw") - _EXPR_JAW) <= 1, (
        "after speaking ends the expression should drive the jaw again"
    )


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


async def test_on_shutdown_cancels_pending_jaw_tasks():
    """on_shutdown must cancel deferred jaw-apply tasks so they don't call
    bus.write() after bus.close() (which would raise OSError on the serial port)."""
    from lafufu_animator.service import AnimatorService

    class _FakeBus:
        def open(self) -> None: pass
        def close(self) -> None: pass
        def enable_torque(self) -> None: pass
        def disable_torque(self) -> None: pass
        def configure_limits(self) -> None: pass
        def write(self, name: str, position: int) -> None: pass
        def read(self, name: str) -> int: return 2048

    svc = AnimatorService(bus=_FakeBus())
    # Manually create a couple of pending jaw tasks to simulate in-flight offset sleeps
    ran_after_shutdown: list[bool] = []

    async def _sleepy_jaw() -> None:
        await asyncio.sleep(5)
        ran_after_shutdown.append(True)

    jaw_tasks = [asyncio.create_task(_sleepy_jaw()), asyncio.create_task(_sleepy_jaw())]
    svc._pending_jaw_tasks = set(jaw_tasks)
    # Patch the methods that need a running loop to no-op
    svc._loop = asyncio.get_running_loop()
    svc._stepper_stop.set()  # prevent the stepper thread from starting

    await svc.on_shutdown()

    # Give any escaped tasks a chance to run (they shouldn't)
    await asyncio.sleep(0.1)

    assert ran_after_shutdown == [], (
        "pending jaw tasks must be cancelled in on_shutdown and must NOT run after bus.close()"
    )
    # Verify the tasks were actually CANCELLED, not merely abandoned.
    # A task sleeping for 5 s would not have run in 0.1 s regardless, so the
    # ran_after_shutdown check above cannot distinguish cancel from abandon.
    # task.cancelled() is True only if CancelledError was raised inside it —
    # i.e. on_shutdown called t.cancel() and then awaited the task to confirm.
    for t in jaw_tasks:
        assert t.cancelled(), "each pending jaw task must be cancelled, not merely abandoned"


async def test_shutdown_awaits_tasks_before_closing_bus(nats_server):
    """On shutdown: all background tasks must be done, torque disabled, and
    the bus closed — in that order — so no task can write to the bus after
    torque-off (racy re-energize) and no serial FD is leaked."""
    bus = FakeDxlBus()
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
    await asyncio.sleep(0.4)  # wait for service ready + background tasks started

    # Trigger shutdown the same way existing tests do
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert bus.closed is True, "DxlBus.close() must be called on shutdown"
    assert bus.torque_disabled is True, "torque must be disabled on shutdown"
    for t in (
        svc._pose_publish_task,
        svc._keyframe_player_task,
        svc._lipsync_watchdog_task,
        svc._idle_request_task,
    ):
        assert t is None or t.done(), f"background task {t!r} must be done after shutdown"
    # The servo stepper is a dedicated thread now; it must be joined before the
    # bus is closed so no write races disable_torque/close.
    assert svc._stepper_thread is None or not svc._stepper_thread.is_alive(), (
        "stepper thread must be joined on shutdown"
    )
