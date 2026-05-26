"""Verify the cold-boot idle bootstrap handshake works end-to-end.

The animator publishes `animator.request.idle` after its subscriptions are
active; control's subscriber responds by (re)publishing the idle expression
to `animator.intent.play_expression`. Closes the race where control's
startup-time fire-and-forget publish landed before the animator was ready.

See docs/superpowers/specs/2026-05-26-idle-bootstrap-readiness-design.md.
"""

import asyncio
import json

import nats
import pytest
from lafufu_control.animation.seed import seed_animations
from lafufu_control.db import create_engine_for_path, init_db
from lafufu_control.service import _publish_idle_expression
from lafufu_shared import topics
from lafufu_shared.testing import nats_server_fixture

nats_server = nats_server_fixture("4271")


@pytest.mark.asyncio
async def test_publish_idle_expression_emits_play_expression(tmp_path, nats_server):
    """`_publish_idle_expression` against a seeded DB emits exactly one
    play_expression with name='idle'."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)

    nc = await nats.connect(nats_server)
    received: list[dict] = []

    async def cb(msg):
        received.append(json.loads(msg.data.decode()))

    sub = await nc.subscribe(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, cb=cb)
    await asyncio.sleep(0.1)

    await _publish_idle_expression(engine, nc)
    await asyncio.sleep(0.2)

    await sub.unsubscribe()
    await nc.drain()

    assert len(received) == 1
    assert received[0]["name"] == "idle"
    assert received[0]["playback"] == "random_walk"


@pytest.mark.asyncio
async def test_publish_idle_silent_on_unseeded_db(tmp_path, nats_server):
    """If no idle expression is seeded yet (very first boot before seed has
    run), `_publish_idle_expression` returns silently — it must not crash."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    # Deliberately do NOT call seed_animations.

    nc = await nats.connect(nats_server)
    received: list[dict] = []

    async def cb(msg):
        received.append(json.loads(msg.data.decode()))

    sub = await nc.subscribe(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, cb=cb)
    await asyncio.sleep(0.1)

    await _publish_idle_expression(engine, nc)  # no exception
    await asyncio.sleep(0.2)

    await sub.unsubscribe()
    await nc.drain()

    assert received == []


@pytest.mark.asyncio
async def test_request_idle_triggers_play_expression(tmp_path, nats_server):
    """End-to-end handshake: a fake animator publishes
    `animator.request.idle`; the control-side subscriber (mirroring the
    one in `ControlService.on_startup`) responds with the idle
    play_expression."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)

    nc = await nats.connect(nats_server)

    # Mirror the on_request_idle subscriber wired up in service.py.
    async def on_request_idle(_msg):
        await _publish_idle_expression(engine, nc)

    await nc.subscribe(topics.ANIMATOR_REQUEST_IDLE, cb=on_request_idle)

    # Fake animator side: subscribe to play_expression so we can observe
    # the control's response.
    received: list[dict] = []

    async def play_cb(msg):
        received.append(json.loads(msg.data.decode()))

    await nc.subscribe(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, cb=play_cb)
    await asyncio.sleep(0.1)  # settle subscriptions

    await nc.publish(topics.ANIMATOR_REQUEST_IDLE, b"{}")
    await asyncio.sleep(0.3)  # round-trip + response

    await nc.drain()

    assert len(received) == 1
    assert received[0]["name"] == "idle"


@pytest.mark.asyncio
async def test_multiple_request_idles_each_get_response(tmp_path, nats_server):
    """Animator restart-mid-session case: the subscriber must respond to
    every request, not just the first one."""
    engine = create_engine_for_path(str(tmp_path / "t.sqlite"))
    init_db(engine)
    seed_animations(engine)

    nc = await nats.connect(nats_server)

    async def on_request_idle(_msg):
        await _publish_idle_expression(engine, nc)

    await nc.subscribe(topics.ANIMATOR_REQUEST_IDLE, cb=on_request_idle)

    received: list[dict] = []

    async def play_cb(msg):
        received.append(json.loads(msg.data.decode()))

    await nc.subscribe(topics.ANIMATOR_INTENT_PLAY_EXPRESSION, cb=play_cb)
    await asyncio.sleep(0.1)

    for _ in range(3):
        await nc.publish(topics.ANIMATOR_REQUEST_IDLE, b"{}")
        await asyncio.sleep(0.15)

    await nc.drain()

    assert len(received) == 3
    assert all(r["name"] == "idle" for r in received)
