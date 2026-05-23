import asyncio

import nats
from lafufu_agent.service import AgentService
from lafufu_shared import schemas, topics
from lafufu_shared.nats_helper import publish_model
from lafufu_shared.testing import FakeOllama, FakePiper, nats_server_fixture

nats_server = nats_server_fixture("4251")


class FakeMicForService:
    def __init__(self, transcripts: list[str]):
        self.transcripts = list(transcripts)
        self.calls = 0

    def listen_once(self) -> str:
        if not self.transcripts:
            # Block forever once exhausted (simulates idle)
            import time as t

            t.sleep(60)
            return ""
        self.calls += 1
        return self.transcripts.pop(0)


async def test_text_message_intent_triggers_pipeline(nats_server):
    """When agent receives agent.intent.text_message, run pipeline as if mic heard it."""
    svc = AgentService(
        mic=FakeMicForService([]),  # mic does nothing
        ollama=FakeOllama(scripts=[("ping", "[neutral]\npong")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)  # wait for ready

    nc = await nats.connect(nats_server)
    replies: list[schemas.AgentReply] = []

    async def cb(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    await nc.subscribe(topics.AGENT_REPLY, cb=cb)

    await publish_model(
        nc, topics.AGENT_INTENT_TEXT_MESSAGE, schemas.AgentIntentTextMessage(text="ping")
    )
    await asyncio.sleep(0.5)
    await nc.drain()

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
    assert len(replies) == 1
    assert replies[0].text == "pong"


async def test_stt_backend_change_swaps_stt_instance(nats_server):
    """When config.changed.agent.stt_backend fires, agent swaps the STT instance."""
    from lafufu_shared.testing import FakeWhisper

    initial = FakeWhisper(fixed_reply="initial")
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        stt=initial,
        stt_factory=lambda backend, model: FakeWhisper(fixed_reply=f"{backend}:{model}"),
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.stt_backend",
        schemas.ConfigChanged(key="agent.stt_backend", value="faster-whisper", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert svc.stt is not initial
    assert svc.stt.fixed_reply.startswith("faster-whisper:")

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_voice_model_change_swaps_piper_instance(nats_server):
    """When config.changed.agent.voice_model fires, agent swaps the Piper instance.

    Mirrors the stt_backend swap test — confirms _rebuild_tts is wired in and
    the factory result replaces self._piper.
    """
    from pathlib import Path

    def make_fake_piper(name: str) -> FakePiper:
        p = FakePiper()
        p.model_path = Path(f"/fake/{name}.onnx")
        p.voice_name = name
        return p

    initial = make_fake_piper("lafufu_voice")
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=initial,
        nats_url=nats_server,
        piper_factory=make_fake_piper,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.voice_model",
        schemas.ConfigChanged(
            key="agent.voice_model", value="lafufu_voice_kristian", source="test"
        ),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert svc._piper is not initial
    assert svc._piper.voice_name == "lafufu_voice_kristian"
    assert svc._voice_model == "lafufu_voice_kristian"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_voice_model_change_keeps_prev_on_missing_file(nats_server):
    """If piper_factory raises FileNotFoundError, agent keeps the previous voice."""
    from pathlib import Path

    initial = FakePiper()
    initial.model_path = Path("/fake/lafufu_voice.onnx")

    def bad_factory(name: str):
        raise FileNotFoundError(f"no such voice: {name}")

    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=initial,
        nats_url=nats_server,
        piper_factory=bad_factory,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.voice_model",
        schemas.ConfigChanged(key="agent.voice_model", value="missing_voice", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    # Previous piper kept; voice_model reverted to the previous voice name so
    # a subsequent valid change is detected (not silently equal to the prior).
    assert svc._piper is initial
    assert svc._voice_model == "lafufu_voice"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_on_startup_warms_stt(nats_server):
    """AgentService.on_startup() should call stt.warmup() so first utterance is fast."""
    from lafufu_shared.testing import FakeWhisper

    fake_stt = FakeWhisper()
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        stt=fake_stt,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    assert fake_stt.warmup_count == 1, "stt.warmup() should be called once during on_startup"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_text_intent_processes_while_mic_is_waiting_for_onset(nats_server):
    """Text intent should NOT wait for the mic to give up on silence.

    Before this change, the mic loop holds _cycle_lock for up to 30s of silence
    listening, blocking text intents. After: mic only takes the lock once it
    detects speech onset.
    """

    class _SilentMic:
        """Pretends to wait for speech onset but always returns empty after a slow pause."""

        def __init__(self):
            self.set_stt_calls = 0

        def wait_for_onset(self):
            import time as _t

            _t.sleep(2.0)  # simulate 2s of silence-listening
            return False, []

        def record_until_silence(self, pre_roll):
            return ""

        def listen_once(self):
            self.wait_for_onset()
            return ""

    svc = AgentService(
        mic=_SilentMic(),
        ollama=FakeOllama(scripts=[("ping", "[neutral]\npong")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(0.3)  # let mic loop enter wait_for_onset

    nc = await nats.connect(nats_server)
    replies: list[schemas.AgentReply] = []

    async def cb(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    await nc.subscribe(topics.AGENT_REPLY, cb=cb)

    import time as _t

    t0 = _t.monotonic()
    await publish_model(
        nc, topics.AGENT_INTENT_TEXT_MESSAGE, schemas.AgentIntentTextMessage(text="ping")
    )
    # Wait for the reply
    for _ in range(50):
        await asyncio.sleep(0.1)
        if replies:
            break
    elapsed = _t.monotonic() - t0
    await nc.drain()

    assert len(replies) == 1, "text intent should still be processed"
    assert elapsed < 1.0, f"text intent took {elapsed:.2f}s — mic loop is blocking the lock"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
