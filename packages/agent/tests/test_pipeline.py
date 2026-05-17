import asyncio

from lafufu_agent.pipeline import VoicePipeline
from lafufu_shared import schemas, topics
from lafufu_shared.testing import FakeOllama, FakePiper, nats_server_fixture

nats_server = nats_server_fixture("4250")


class FakeMic:
    """Yields a fixed list of canned (chunk_bytes, eventual_transcript) once."""

    def __init__(self):
        self.played = False

    def listen_once(self) -> str:
        """Return the transcribed text. Synchronous for simplicity in tests."""
        self.played = True
        return "hello lafufu"


async def test_pipeline_one_cycle_publishes_all_state_transitions(nats_server):
    import nats

    nc = await nats.connect(nats_server)
    states: list[str] = []
    replies: list[schemas.AgentReply] = []
    rms_events: list[schemas.AgentTtsRms] = []

    async def cb_state(msg):
        states.append(msg.subject)

    async def cb_reply(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    async def cb_rms(msg):
        rms_events.append(schemas.AgentTtsRms.model_validate_json(msg.data))

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=cb_state)
    await nc.subscribe(topics.AGENT_REPLY, cb=cb_reply)
    await nc.subscribe(topics.AGENT_TTS_RMS, cb=cb_rms)

    fake_ollama = FakeOllama(scripts=[("hello", "[happy]\nHi there!")])
    fake_piper = FakePiper(chunks=[(b"\x00" * 1024, 0.5)] * 4)

    pipeline = VoicePipeline(
        nats_client=await nats.connect(nats_server, name="pipeline"),
        mic=FakeMic(),
        ollama=fake_ollama,
        piper=fake_piper,
    )
    await pipeline.run_one_cycle()
    await asyncio.sleep(0.2)
    await nc.drain()

    # Expected state transitions
    state_tails = [s.rsplit(".", 1)[-1] for s in states]
    for required in ("listening", "thinking", "speaking", "idle"):
        assert required in state_tails, f"missing {required} in {state_tails}"

    # Expected reply
    assert len(replies) == 1
    assert replies[0].text == "Hi there!"
    assert replies[0].emotion == "happy"

    # Expected RMS chunks
    assert len(rms_events) == 4
