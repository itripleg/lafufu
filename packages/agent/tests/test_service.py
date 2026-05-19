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
