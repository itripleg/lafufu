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


def test_aplay_player_uses_dynamic_sample_rate(monkeypatch):
    """_AplayPlayer should invoke aplay with the rate it was constructed with."""
    from lafufu_agent.__main__ import _AplayPlayer

    invocations: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            invocations.append(argv)
            self.stdin = type(
                "S",
                (),
                {
                    "write": lambda self, b: None,
                    "flush": lambda self: None,
                    "close": lambda self: None,
                },
            )()

        def poll(self):
            return None

    import subprocess as _sp

    monkeypatch.setattr(_sp, "Popen", _FakePopen)

    player = _AplayPlayer(sample_rate=16000)
    player.play(b"\x00\x00" * 100)
    assert any("16000" in argv for argv in invocations), (
        f"aplay must use the passed sample rate; got {invocations}"
    )


async def test_pipeline_does_not_block_event_loop_during_synth(nats_server):
    """While Piper is synthesizing, the event loop must still process callbacks."""
    import time as _time

    import nats

    class _SlowPiper:
        sample_rate = 22050
        chunk_ms = 40

        def synthesize(self, text):
            # 200ms blocking call simulating a slow synth.
            _time.sleep(0.2)
            return [(b"\x00" * 1764, 0.0)]

    nc = await nats.connect(nats_server)
    pipeline = VoicePipeline(
        nats_client=nc,
        mic=FakeMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=_SlowPiper(),
    )

    # Schedule a heartbeat that should fire DURING synth.
    ticks: list[float] = []

    async def heartbeat():
        for _ in range(8):
            ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.03)

    hb = asyncio.create_task(heartbeat())
    await pipeline.run_one_cycle()
    await hb
    await nc.drain()

    # All 8 ticks should be there even though synth blocked for 200ms.
    assert len(ticks) == 8, f"event loop starved during synth; got {len(ticks)} ticks"
    # Ticks should be ~30ms apart, not all bunched after the 200ms block.
    spans = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    assert max(spans) < 0.18, f"event loop blocked for >180ms during synth: {spans}"
