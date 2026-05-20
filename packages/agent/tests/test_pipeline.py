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


def test_fake_piper_supports_streaming_iteration():
    """FakePiper.synthesize_stream yields chunks one at a time."""
    from lafufu_shared.testing import FakePiper

    fp = FakePiper(chunks=[(b"\x00" * 100, 0.1), (b"\x00" * 100, 0.2)])
    streamed = list(fp.synthesize_stream("hello"))
    assert streamed == [(b"\x00" * 100, 0.1), (b"\x00" * 100, 0.2)]


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


async def test_pipeline_streams_first_chunk_before_synth_finishes(nats_server):
    """TTFS test: first chunk should hit the speaker before the last chunk is synthesized."""
    import time as _time

    import nats

    play_times: list[float] = []

    class _StreamingSlowPiper:
        sample_rate = 22050
        chunk_ms = 40

        def synthesize_stream(self, text):
            for _i in range(5):
                _time.sleep(0.1)  # 100ms per chunk
                yield (b"\x00" * 1764, 0.5)

        def synthesize(self, text):
            return list(self.synthesize_stream(text))

    def _record_play(chunk):
        play_times.append(_time.monotonic())

    nc = await nats.connect(nats_server)
    pipeline = VoicePipeline(
        nats_client=nc,
        mic=FakeMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=_StreamingSlowPiper(),
        speaker_play=_record_play,
    )

    # NOTE: t_start includes mic + LLM stub time (both near-instant with fakes).
    # The 250ms budget below has ~150ms slack over the ~100ms first-synth-chunk.
    t_start = _time.monotonic()
    await pipeline.run_one_cycle()
    await nc.drain()

    assert len(play_times) == 5
    # First chunk should arrive within ~250ms of synth start (first synth chunk
    # is ~100ms, plus loop overhead). NOT 500ms (which would mean buffering).
    first_chunk_latency = play_times[0] - t_start
    assert first_chunk_latency < 0.25, (
        f"first chunk latency {first_chunk_latency:.3f}s exceeds budget — synth was buffered"
    )


async def test_speak_recovers_when_playback_raises(nats_server):
    """If play_fn raises mid-stream, speak() must not deadlock and must still
    end the speaker + return to idle state."""
    import nats

    class _ManyChunkPiper:
        sample_rate = 22050
        chunk_ms = 40

        def synthesize_stream(self, text):
            # More chunks than the queue maxsize (8) so the producer thread
            # will be blocked on a full queue when the consumer aborts.
            for _i in range(40):
                yield (b"\x00" * 1764, 0.5)

        def synthesize(self, text):
            return list(self.synthesize_stream(text))

    class _ExplodingSpeaker:
        def __init__(self):
            self.ended = False

        def play(self, chunk):
            raise RuntimeError("speaker exploded")

        def end(self):
            self.ended = True

    nc = await nats.connect(nats_server)
    states: list[str] = []

    async def cb_state(msg):
        states.append(msg.subject.rsplit(".", 1)[-1])

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=cb_state)

    speaker = _ExplodingSpeaker()
    pipeline = VoicePipeline(
        nats_client=nc,
        mic=FakeMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=_ManyChunkPiper(),
        speaker_play=speaker,
    )

    # speak() should raise the RuntimeError (not hang), and cleanup must run.
    import pytest

    with pytest.raises(RuntimeError, match="speaker exploded"):
        await asyncio.wait_for(pipeline.speak("hi"), timeout=5.0)

    await asyncio.sleep(0.1)
    await nc.drain()

    assert speaker.ended, "speaker.end() must run even when playback raises"
    assert "idle" in states, "state must return to idle even when playback raises"


async def test_ip_intent_prints_and_speaks_without_llm(nats_server, monkeypatch):
    """A 'what's your IP' utterance must print a slip and speak the IP,
    and must NOT call the LLM."""
    import nats
    from lafufu_shared.testing import FakePiper

    monkeypatch.setattr("lafufu_shared.netinfo.primary_lan_ip", lambda: "192.168.1.42")

    nc = await nats.connect(nats_server)
    prints: list[schemas.PrinterIntentPrintText] = []
    replies: list[schemas.AgentReply] = []

    async def cb_print(msg):
        prints.append(schemas.PrinterIntentPrintText.model_validate_json(msg.data))

    async def cb_reply(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    await nc.subscribe(topics.PRINTER_INTENT_PRINT_TEXT, cb=cb_print)
    await nc.subscribe(topics.AGENT_REPLY, cb=cb_reply)

    class _IpQueryMic:
        def listen_once(self) -> str:
            return "hey lafufu what's your ip address"

    class _ExplodingOllama:
        async def chat(self, user_text: str) -> str:
            raise AssertionError("LLM must not be called for the IP intent")

    pipeline = VoicePipeline(
        nats_client=await nats.connect(nats_server, name="pipeline"),
        mic=_IpQueryMic(),
        ollama=_ExplodingOllama(),
        piper=FakePiper(chunks=[(b"\x00" * 1024, 0.5)]),
    )
    await pipeline.run_one_cycle()
    await asyncio.sleep(0.2)
    await nc.drain()

    assert len(prints) == 1, f"expected 1 print job, got {len(prints)}"
    assert "192.168.1.42" in prints[0].text
    assert "http://192.168.1.42:8080/admin" in prints[0].text
    assert len(replies) == 1, f"expected 1 reply, got {len(replies)}"
    assert replies[0].source == "system"
    assert "192.168.1.42" in replies[0].text
