import asyncio

import nats
import numpy as np
import pytest
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


async def test_text_message_intent_does_not_publish_listening(nats_server):
    """A text intent has no mic phase — it must not emit a 'listening' state."""
    svc = AgentService(
        mic=FakeMicForService([]),  # mic does nothing
        ollama=FakeOllama(scripts=[("ping", "[neutral]\npong")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
    )

    nc = await nats.connect(nats_server)
    states: list[str] = []

    async def on_state(msg):
        states.append(msg.subject.split(".")[-1])

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=on_state)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)  # wait for ready

    await publish_model(
        nc, topics.AGENT_INTENT_TEXT_MESSAGE, schemas.AgentIntentTextMessage(text="ping")
    )
    await asyncio.sleep(0.5)
    await nc.drain()

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert "listening" not in states, f"text intent must not emit 'listening'; got {states}"
    assert "thinking" in states, f"text intent should still emit 'thinking'; got {states}"


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
    # The persistent pipeline must also pick up the new voice — otherwise the
    # mic loop (which uses self._pipeline.run_one_cycle) keeps speaking with
    # the old Piper instance even though self._piper has been swapped.
    assert svc._pipeline is not None
    assert svc._pipeline.piper is svc._piper

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_tts_length_scale_change_updates_piper(nats_server):
    """When config.changed.tts.length_scale fires, the agent mirrors the
    new float onto self._piper.length_scale so the next synthesize call
    uses it. Also stores the value on the service so a subsequent voice
    swap propagates it onto the new Piper instance.
    """
    initial = FakePiper()
    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=initial,
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.tts.length_scale",
        schemas.ConfigChanged(key="tts.length_scale", value="0.85", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert initial.length_scale == 0.85
    assert svc._tts_length_scale == 0.85

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_voice_swap_carries_current_length_scale(nats_server):
    """A voice swap must apply the operator's current length_scale to the new
    Piper — otherwise the swap would silently revert speed to the .onnx.json
    default.
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
    # First set the length scale, then swap the voice.
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.tts.length_scale",
        schemas.ConfigChanged(key="tts.length_scale", value="0.85", source="test"),
    )
    await asyncio.sleep(0.2)
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
    assert svc._piper.length_scale == 0.85, "new voice must inherit current length_scale"

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
            return np.zeros(0, dtype=np.float32)

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


async def test_voice_cycle_publishes_transcribing_state(nats_server):
    """Voice cycle should publish 'transcribing' state after speech onset, before LLM."""
    from lafufu_shared.testing import FakeWhisper

    class _OnsetMic:
        def wait_for_onset(self):
            return (True, [])

        def record_until_silence(self, pre_roll):
            return np.zeros(1600, dtype=np.float32)

        def listen_once(self):
            return ""

    svc = AgentService(
        mic=_OnsetMic(),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
        stt=FakeWhisper(fixed_reply="hello there"),
    )

    nc = await nats.connect(nats_server)
    collected_states: list[str] = []

    async def on_state(msg):
        # subject is e.g. "agent.state.transcribing" — grab the trailing token
        token = msg.subject.split(".")[-1]
        collected_states.append(token)

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=on_state)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(0.6)
    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert "transcribing" in collected_states


# ---------------------------------------------------------------------------
# Trigger-mode interaction loop
# ---------------------------------------------------------------------------


class _TriggerMic:
    """Mic that mimics RealMic's wake-then-RMS split.

    First wait_for_onset() call simulates the wake-word firing; subsequent
    calls (with force_rms=True) simulate in-session user input. After
    `num_user_inputs` such calls, it blocks (idle) so the agent shuts down
    without re-firing.
    """

    def __init__(self, num_user_inputs: int = 1):
        self.wake_detector = object()  # truthy sentinel — passes trigger-mode validation
        self._max_inputs = num_user_inputs
        self._wake_fired = False
        self._inputs_served = 0

    def wait_for_onset(self, force_rms: bool = False):
        if not self._wake_fired:
            if force_rms:
                # Caller is asking for an in-session listen without ever firing
                # the wake — shouldn't happen in trigger mode.
                import time

                time.sleep(60)
                return False, []
            self._wake_fired = True
            return True, [b"\x00" * 100]
        if self._inputs_served < self._max_inputs:
            self._inputs_served += 1
            return True, [b"\x00" * 100]
        # Out of scripted inputs — block (simulates the agent settling back
        # into wake-listen until shutdown).
        import time

        time.sleep(60)
        return False, []

    def record_until_silence(self, pre_roll):
        return np.zeros(1280, dtype=np.float32)

    def listen_once(self):
        return ""


async def test_trigger_mode_speaks_opening_runs_round_and_auto_prints(nats_server):
    """Happy path: wake → opening phrase → 1 round → auto-print of LLM reply."""
    from lafufu_agent.trigger import InteractionMode, TriggerConfig
    from lafufu_shared.testing import FakeWhisper

    svc = AgentService(
        mic=_TriggerMic(num_user_inputs=1),
        ollama=FakeOllama(scripts=[("tell me my fortune", "[happy]\nGreat fortune awaits.")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
        stt=FakeWhisper(fixed_reply="tell me my fortune"),
        interaction_mode=InteractionMode.TRIGGER,
        trigger_config=TriggerConfig(
            phrase="Ask, traveler.",
            emotion="neutral",
            rounds=1,
            print_mode="auto",
            print_prompt="Want a slip?",
        ),
    )

    nc = await nats.connect(nats_server)
    replies: list[schemas.AgentReply] = []
    prints: list[schemas.PrinterIntentPrintText] = []

    async def cb_reply(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    async def cb_print(msg):
        prints.append(schemas.PrinterIntentPrintText.model_validate_json(msg.data))

    await nc.subscribe(topics.AGENT_REPLY, cb=cb_reply)
    await nc.subscribe(topics.PRINTER_INTENT_PRINT_TEXT, cb=cb_print)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(1.2)  # one full session
    await nc.drain()

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert len(replies) >= 2, f"expected opening + round reply, got {[r.text for r in replies]}"
    opening = next(r for r in replies if r.source == "system")
    assert opening.text == "Ask, traveler."
    assert opening.emotion == "neutral"

    llm_reply = next(r for r in replies if r.source == "llm")
    assert llm_reply.text == "Great fortune awaits."

    assert len(prints) == 1, f"auto-print should emit exactly one print job, got {len(prints)}"
    assert prints[0].text == "Great fortune awaits."


async def test_trigger_mode_without_wake_detector_fails_startup(nats_server):
    """Trigger mode requires a wake-gated mic — bare mics must fail loudly."""
    from lafufu_agent.trigger import InteractionMode, TriggerConfig

    svc = AgentService(
        mic=FakeMicForService([]),  # no wake_detector attribute, no wait_for_onset
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
        interaction_mode=InteractionMode.TRIGGER,
        trigger_config=TriggerConfig.from_env({}),
    )
    with pytest.raises(RuntimeError, match="wake"):
        await asyncio.wait_for(svc.run(), timeout=3)


class _ScriptedSTT:
    """STT that returns a different transcript per call (list order)."""

    backend_id = "fake"
    model_name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def transcribe(self, audio) -> str:
        idx = self.calls
        self.calls += 1
        return self._replies[idx] if idx < len(self._replies) else ""

    def warmup(self) -> float:
        return 0.0


async def test_trigger_mode_passes_in_session_history_to_llm(nats_server):
    """Each round's LLM call should receive the opening phrase + every prior
    round's (user, assistant) turn as history, so multi-round trigger sessions
    can produce context-aware ("personalized") fortunes.
    """
    from lafufu_agent.trigger import InteractionMode, TriggerConfig

    ollama = FakeOllama(
        scripts=[
            ("first question", "[neutral]\nfirst reply"),
            ("second question", "[neutral]\nsecond reply"),
        ]
    )

    svc = AgentService(
        mic=_TriggerMic(num_user_inputs=2),
        ollama=ollama,
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
        stt=_ScriptedSTT(["first question", "second question"]),
        interaction_mode=InteractionMode.TRIGGER,
        trigger_config=TriggerConfig(
            phrase="Ask away.",
            emotion="neutral",
            rounds=2,
            print_mode="none",
            print_prompt="",
        ),
    )

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(2.0)  # both rounds

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert len(ollama.calls) == 2, f"expected two LLM calls, got {ollama.calls}"

    # Round 1: history is just the opening phrase (the assistant turn).
    assert ollama.history_calls[0] == [("assistant", "Ask away.")]

    # Round 2: history grows with round 1's user transcript + emotion-stripped reply.
    assert ollama.history_calls[1] == [
        ("assistant", "Ask away."),
        ("user", "first question"),
        ("assistant", "first reply"),
    ]


async def test_trigger_session_publishes_wake_listening_state(nats_server):
    """The wake-listen slot of a trigger session should publish 'wake_listening',
    not the generic 'listening' — admin UI uses this to label "waiting for
    trigger word" distinctly from in-session listening for the user's answer.
    """
    from lafufu_agent.trigger import InteractionMode, TriggerConfig
    from lafufu_shared.testing import FakeWhisper

    svc = AgentService(
        mic=_TriggerMic(num_user_inputs=1),
        ollama=FakeOllama(scripts=[("hello", "[neutral]\nhi")]),
        piper=FakePiper(chunks=[(b"\x00" * 100, 0.3)]),
        nats_url=nats_server,
        stt=FakeWhisper(fixed_reply="hello"),
        interaction_mode=InteractionMode.TRIGGER,
        trigger_config=TriggerConfig(
            phrase="Ask.",
            emotion="neutral",
            rounds=1,
            print_mode="none",
            print_prompt="",
        ),
    )

    nc = await nats.connect(nats_server)
    states: list[str] = []

    async def on_state(msg):
        states.append(msg.subject.split(".")[-1])

    await nc.subscribe(f"{topics.AGENT_STATE}.*", cb=on_state)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.start_mic_loop()
    await asyncio.sleep(1.2)
    await nc.drain()
    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert "wake_listening" in states, f"expected wake_listening in {states}"
    # The in-session round listen should publish plain 'listening', not
    # 'wake_listening' again — they're semantically different slots.
    wake_idx = states.index("wake_listening")
    later = states[wake_idx + 1 :]
    assert "listening" in later, (
        f"expected an in-session 'listening' after wake_listening; later={later}"
    )


async def test_input_device_setting_resets_mic(nats_server):
    """A change to agent.input_device should both update the audio_capture
    module-level snapshot AND close the existing mic stream so the next
    listen rebinds to the new device."""
    from lafufu_agent import audio_capture

    closed = {"count": 0}

    class _Mic:
        def close(self):
            closed["count"] += 1

        def listen_once(self):
            return ""

    svc = AgentService(
        mic=_Mic(),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
    )
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.input_device",
        schemas.ConfigChanged(key="agent.input_device", value="usb", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()

    assert audio_capture._db_input_device == "usb"
    assert closed["count"] >= 1, "mic should be closed so next listen picks the new device"

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
    audio_capture.set_db_input_device("auto")  # reset for other tests


async def test_interaction_mode_setting_swaps_field(nats_server):
    """Flipping agent.interaction_mode at runtime should update the field
    so the next _mic_loop iteration uses the new branch."""
    from lafufu_agent.trigger import InteractionMode

    svc = AgentService(
        mic=FakeMicForService([]),
        ollama=FakeOllama(),
        piper=FakePiper(),
        nats_url=nats_server,
    )
    assert svc._interaction_mode == InteractionMode.CONTINUOUS

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.5)

    nc = await nats.connect(nats_server)
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.interaction_mode",
        schemas.ConfigChanged(key="agent.interaction_mode", value="trigger", source="test"),
    )
    await asyncio.sleep(0.3)

    assert svc._interaction_mode == InteractionMode.TRIGGER

    # Invalid values should be rejected (logged + ignored), not crash
    await publish_model(
        nc,
        f"{topics.CONFIG_CHANGED}.agent.interaction_mode",
        schemas.ConfigChanged(key="agent.interaction_mode", value="bogus", source="test"),
    )
    await asyncio.sleep(0.3)
    await nc.drain()
    assert svc._interaction_mode == InteractionMode.TRIGGER  # unchanged

    svc._shutdown.set()
    await asyncio.wait_for(task, timeout=3)
