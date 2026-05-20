"""AgentService: BaseService that runs the voice loop and accepts text intents."""

import asyncio
import logging
import subprocess

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .pipeline import VoicePipeline

log = logging.getLogger(__name__)


def _voice_name_of(piper) -> str:
    """Bare voice name (stem) from a Piper instance — what the setting takes."""
    path = getattr(piper, "model_path", None)
    return path.stem if path is not None else "lafufu_voice"


def _set_alsa_volume(card: str, control: str, pct: int) -> tuple[bool, str]:
    """Set ALSA mixer volume. Returns (ok, message)."""
    pct = max(0, min(100, int(pct)))
    try:
        result = subprocess.run(
            ["amixer", "-q", "-c", card, "sset", control, f"{pct}%"],
            capture_output=True,
            timeout=3,
        )
    except FileNotFoundError:
        return False, "amixer not installed"
    except subprocess.SubprocessError as e:
        return False, str(e)
    if result.returncode != 0:
        return False, result.stderr.decode(errors="replace").strip() or f"exit {result.returncode}"
    return True, f"set {card}/{control} to {pct}%"


class AgentService(BaseService):
    name = "agent"

    def __init__(
        self,
        mic,
        ollama,
        piper,
        speaker_play=None,
        nats_url: str | None = None,
        stt=None,
        stt_factory=None,
        piper_factory=None,
        player_factory=None,
    ) -> None:
        super().__init__()
        self._mic = mic
        self._ollama = ollama
        self._piper = piper
        self._speaker_play = speaker_play
        self._nats_url = nats_url
        self.stt = stt
        self._stt_factory = stt_factory
        # Seed from the injected stt so a config snapshot that matches the
        # env-configured backend doesn't trigger a redundant rebuild — which
        # would discard the already-warmed instance for a cold one.
        self._stt_backend = getattr(stt, "backend_id", "openai-whisper")
        self._stt_model = getattr(stt, "model_name", "tiny.en")
        # TTS voice swap (mirrors STT factory pattern). Seed from the injected
        # piper so a snapshot matching the env-configured voice is a no-op.
        self._piper_factory = piper_factory
        self._player_factory = player_factory
        self._voice_model = _voice_name_of(piper)
        # TTS length scale lives in settings (tts.length_scale, default 0.95).
        # Seeded from the config snapshot on startup; mirrored into self._piper
        # and into any new piper produced by _rebuild_tts.
        self._tts_length_scale: float | None = None
        self._pipeline: VoicePipeline | None = None
        self._cycle_lock = asyncio.Lock()
        self._mic_loop_task: asyncio.Task | None = None
        # Speaker mixer settings; updated live by config.changed.speaker.* subscribers.
        self._speaker_card = "USB"
        self._speaker_control = "PCM"

    @property
    def nats_url(self) -> str:
        return self._nats_url or super().nats_url

    async def on_startup(self) -> None:
        await self._publish_state("warming")
        # Hot-warm Ollama if it has a warmup method
        if hasattr(self._ollama, "warmup"):
            try:
                elapsed = await self._ollama.warmup()
                self.log.info("ollama.warmed_up elapsed_s=%.1f", elapsed)
            except Exception as e:
                self.log.warning("ollama.warmup.failed error=%s", e)

        # Hot-warm STT in an executor — same idea as Ollama warmup. Done off
        # the loop because whisper.load_model + a 0.5s dummy decode is blocking
        # C code that would freeze NATS subscribers otherwise.
        if self.stt is not None and hasattr(self.stt, "warmup"):
            try:
                loop = asyncio.get_running_loop()
                elapsed = await loop.run_in_executor(None, self.stt.warmup)
                self.log.info(
                    "stt.warmed_up backend=%s elapsed_s=%.1f",
                    getattr(self.stt, "backend_id", "?"),
                    elapsed,
                )
            except Exception as e:
                self.log.warning("stt.warmup.failed error=%s", e)

        self._pipeline = VoicePipeline(
            self.nats, self._mic, self._ollama, self._piper, self._speaker_play
        )
        await self._publish_state("idle")

        # Subscribe to text-message intent (headless input path — text → LLM → TTS)
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_INTENT_TEXT_MESSAGE,
            schemas.AgentIntentTextMessage,
            self._on_text_message,
        )

        # Subscribe to speak-text intent (direct passthrough — text → TTS, skip LLM)
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_INTENT_SPEAK_TEXT,
            schemas.AgentIntentSpeakText,
            self._on_speak_text,
        )

        # Live-switch LLM model when admin changes agent.llm_model setting.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.llm_model",
            schemas.ConfigChanged,
            self._on_config_llm_model,
        )

        # Live-switch STT backend + whisper model when admin updates settings.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.stt_backend",
            schemas.ConfigChanged,
            self._on_config_stt_backend,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.whisper_model",
            schemas.ConfigChanged,
            self._on_config_whisper_model,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.voice_model",
            schemas.ConfigChanged,
            self._on_config_voice_model,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.tts.length_scale",
            schemas.ConfigChanged,
            self._on_config_tts_length_scale,
        )

        # Live-update system prompt when admin changes it.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.system_prompt",
            schemas.ConfigChanged,
            self._on_config_system_prompt,
        )

        # Speaker volume + ALSA routing — wired to settings so a slider in admin
        # can adjust playback volume live without restart.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.speaker.volume",
            schemas.ConfigChanged,
            self._on_config_volume,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.speaker.alsa_card",
            schemas.ConfigChanged,
            self._on_config_card,
        )
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.speaker.alsa_control",
            schemas.ConfigChanged,
            self._on_config_control_name,
        )

        # Mic auto-listen toggle — live-driven by the agent.auto_listen setting,
        # so the admin UI can start/stop the mic loop without restarting agent.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.auto_listen",
            schemas.ConfigChanged,
            self._on_config_auto_listen,
        )

        # Mic RMS threshold — admin slider tunes how loud incoming audio must
        # be to count as speech (vs ambient noise).
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.silence_threshold",
            schemas.ConfigChanged,
            self._on_config_silence_threshold,
        )
        # Silence tail — how long of a quiet stretch (seconds) ends an utterance.
        await nats_helper.subscribe_model(
            self.nats,
            f"{topics.CONFIG_CHANGED}.agent.silence_seconds",
            schemas.ConfigChanged,
            self._on_config_silence_seconds,
        )

        # Sync to DB on startup so all the *.changed.* subscribers above receive
        # the current admin-set values immediately, instead of waiting for the
        # operator to toggle each one.
        await self.request_config_snapshot()

        # Note: we do NOT auto-start the mic loop in tests (FakeMicForService blocks).
        # Real `main.py` calls start_mic_loop() explicitly after construction.

    async def _on_config_volume(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            pct = int(msg.value)
        except (TypeError, ValueError):
            self.log.warning("speaker.volume.bad_value value=%r", msg.value)
            return
        ok, detail = _set_alsa_volume(self._speaker_card, self._speaker_control, pct)
        if ok:
            self.log.info("speaker.volume.set pct=%d", pct)
        else:
            self.log.warning("speaker.volume.failed detail=%s", detail)

    async def _on_config_card(self, subject: str, msg: schemas.ConfigChanged) -> None:
        self._speaker_card = str(msg.value)
        self.log.info("speaker.card.set value=%s", self._speaker_card)

    async def _on_config_control_name(self, subject: str, msg: schemas.ConfigChanged) -> None:
        self._speaker_control = str(msg.value)
        self.log.info("speaker.control.set value=%s", self._speaker_control)

    async def _on_config_llm_model(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_model = str(msg.value).strip()
        if not new_model:
            return
        prev = getattr(self._ollama, "model", None)
        if prev == new_model:
            return
        self._ollama.model = new_model
        self.log.info("llm.model.switched from=%s to=%s", prev, new_model)
        # Warm the new model so the next request doesn't pay cold-load.
        if hasattr(self._ollama, "warmup"):
            try:
                elapsed = await self._ollama.warmup()
                self.log.info("llm.model.warmed model=%s elapsed_s=%.1f", new_model, elapsed)
            except Exception as e:
                self.log.warning("llm.model.warmup_failed model=%s error=%s", new_model, e)

    async def _on_config_stt_backend(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_backend = str(msg.value).strip()
        if not new_backend or new_backend == self._stt_backend:
            return
        self._stt_backend = new_backend
        self._rebuild_stt(reason="backend")

    async def _on_config_whisper_model(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_model = str(msg.value).strip()
        if not new_model or new_model == self._stt_model:
            return
        self._stt_model = new_model
        self._rebuild_stt(reason="model")

    def _rebuild_stt(self, reason: str) -> None:
        if self._stt_factory is None:
            self.log.warning("stt.rebuild.skipped reason=%s factory_missing", reason)
            return
        prev = self.stt
        self.stt = self._stt_factory(self._stt_backend, self._stt_model)
        actual_backend = getattr(self.stt, "backend_id", self._stt_backend)
        if actual_backend != self._stt_backend:
            self.log.warning(
                "stt.rebuilt.fallback requested=%s actual=%s model=%s — backend not installed?",
                self._stt_backend,
                actual_backend,
                self._stt_model,
            )
        else:
            self.log.info(
                "stt.rebuilt reason=%s backend=%s model=%s prev=%r",
                reason,
                self._stt_backend,
                self._stt_model,
                type(prev).__name__,
            )
        if hasattr(self._mic, "set_stt"):
            self._mic.set_stt(self.stt)

    async def _on_config_tts_length_scale(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            value = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("tts.length_scale.bad_value value=%r", msg.value)
            return
        self._tts_length_scale = value
        if self._piper is not None:
            self._piper.length_scale = value
        self.log.info("tts.length_scale.set value=%.3f", value)

    async def _on_config_voice_model(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_name = str(msg.value).strip()
        if not new_name or new_name == self._voice_model:
            return
        self._voice_model = new_name
        self._rebuild_tts(reason="voice_model")

    def _rebuild_tts(self, reason: str) -> None:
        """Swap the Piper voice. Updates self._piper, optionally rebuilds the
        speaker player when the sample rate changes, and propagates both into
        the persistent self._pipeline so the mic loop picks up the new voice
        on its next cycle. Per-call pipelines (constructed inside
        _on_text_message / _on_speak_text) capture the live self._piper at
        build time and don't need extra wiring. Mid-utterance audio finishes
        on whichever Piper iterator is already in flight.
        """
        if self._piper_factory is None:
            self.log.warning("tts.rebuild.skipped reason=%s factory_missing", reason)
            return
        prev = self._piper
        try:
            new_piper = self._piper_factory(self._voice_model)
        except FileNotFoundError as e:
            self.log.warning(
                "tts.rebuild.failed voice=%s error=%s — keeping previous voice",
                self._voice_model,
                e,
            )
            # Restore the previous voice name so the next change is detected.
            self._voice_model = _voice_name_of(prev)
            return
        old_rate = getattr(prev, "sample_rate", None)
        new_rate = getattr(new_piper, "sample_rate", None)
        self._piper = new_piper
        # Carry the operator's current length scale onto the new voice so a
        # swap doesn't silently revert speed to the .onnx.json default.
        if self._tts_length_scale is not None:
            self._piper.length_scale = self._tts_length_scale
        if self._player_factory is not None and new_rate is not None and new_rate != old_rate:
            self._speaker_play = self._player_factory(new_rate)
            self.log.info("tts.player.rebuilt sample_rate=%s prev_rate=%s", new_rate, old_rate)
        # Propagate the swap to the persistent pipeline so the mic loop picks up
        # the new voice. Per-call pipelines (built inside _on_text_message /
        # _on_speak_text) already capture self._piper at construction time and
        # don't need this. Mirrors _rebuild_stt's self._mic.set_stt(self.stt).
        if self._pipeline is not None:
            self._pipeline.piper = self._piper
            self._pipeline.speaker_play = self._speaker_play
        self.log.info(
            "tts.rebuilt reason=%s voice=%s prev=%s sample_rate=%s",
            reason,
            self._voice_model,
            _voice_name_of(prev),
            new_rate,
        )

    async def _on_config_silence_threshold(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            value = int(msg.value)
        except (TypeError, ValueError):
            self.log.warning("silence_threshold.bad_value value=%r", msg.value)
            return
        if hasattr(self._mic, "silence_threshold"):
            self._mic.silence_threshold = value
            self.log.info("mic.silence_threshold.set value=%d", value)

    async def _on_config_silence_seconds(self, subject: str, msg: schemas.ConfigChanged) -> None:
        try:
            value = float(msg.value)
        except (TypeError, ValueError):
            self.log.warning("silence_seconds.bad_value value=%r", msg.value)
            return
        if hasattr(self._mic, "silence_tail_s"):
            self._mic.silence_tail_s = value
            self.log.info("mic.silence_tail_s.set value=%.2f", value)

    async def _on_config_auto_listen(self, subject: str, msg: schemas.ConfigChanged) -> None:
        v = msg.value
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes", "on")
        want = bool(v)
        running = self._mic_loop_task is not None and not self._mic_loop_task.done()
        if want and not running:
            self.start_mic_loop()
            self.log.info("mic_loop.started reason=config")
        elif not want and running:
            self._mic_loop_task.cancel()
            self._mic_loop_task = None
            self.log.info("mic_loop.stopped reason=config")

    async def _on_config_system_prompt(self, subject: str, msg: schemas.ConfigChanged) -> None:
        new_prompt = str(msg.value)
        self._ollama.system_prompt = new_prompt
        self.log.info("llm.system_prompt.updated chars=%d", len(new_prompt))

    async def on_shutdown(self) -> None:
        await self._publish_state("shutdown")
        if self._mic_loop_task:
            self._mic_loop_task.cancel()
        if hasattr(self._mic, "close"):
            try:
                self._mic.close()
            except Exception as e:
                self.log.warning("mic.close.failed error=%s", e)

    async def _publish_state(self, name: str) -> None:
        await self.publish_state(name, schemas.AgentState(state=name))  # type: ignore[arg-type]

    async def _on_text_message(self, subject: str, msg: schemas.AgentIntentTextMessage) -> None:
        async with self._cycle_lock:
            # Override the mic's next call to return this text
            class _OnceMic:
                def __init__(self, text):
                    self.text = text

                def listen_once(self):
                    return self.text

            tmp = VoicePipeline(
                self.nats, _OnceMic(msg.text), self._ollama, self._piper, self._speaker_play
            )
            await tmp.run_one_cycle()

    async def _on_speak_text(self, subject: str, msg: schemas.AgentIntentSpeakText) -> None:
        """Direct text-to-speech: skip LLM, play exactly what was sent."""
        async with self._cycle_lock:
            tmp = VoicePipeline(self.nats, None, self._ollama, self._piper, self._speaker_play)
            await tmp.speak(msg.text, msg.emotion, source="puppet")

    def start_mic_loop(self) -> None:
        """Call from real main() after on_startup to begin listening continuously."""
        self._mic_loop_task = asyncio.create_task(self._mic_loop())

    async def _mic_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._voice_cycle_with_split_lock()
            except Exception as e:
                self.log.exception("voice_cycle.failed error=%s", e)
                await asyncio.sleep(1.0)

    async def _voice_cycle_with_split_lock(self) -> None:
        """Wait for onset WITHOUT holding the lock. Once speech starts, grab the
        lock and finish the cycle. This lets text intents jump in during silence.
        """
        if self._pipeline is None:
            await asyncio.sleep(0.5)
            return

        # Fast-path: if the mic doesn't expose the split interface, just do the
        # old thing (used by tests with FakeMic).
        if not hasattr(self._mic, "wait_for_onset"):
            async with self._cycle_lock:
                await self._pipeline.run_one_cycle()
            return

        loop = asyncio.get_running_loop()
        await self._publish_state("listening")
        started, pre_roll = await loop.run_in_executor(None, self._mic.wait_for_onset)
        if not started:
            await self._publish_state("idle")
            return

        async with self._cycle_lock:
            audio = await loop.run_in_executor(None, self._mic.record_until_silence, pre_roll)
            if getattr(audio, "size", 0) == 0:
                await self._publish_state("idle")
                return

            if self.stt is None:
                await self._publish_state("idle")
                return

            await self._publish_state("transcribing")
            transcript = await loop.run_in_executor(None, self.stt.transcribe, audio)
            clean = (transcript or "").strip()
            if len(clean) < 2:
                await self._publish_state("idle")
                return

            # Reuse the rest of the pipeline (publish + LLM + speak) by
            # constructing a one-shot mic that returns this transcript.
            class _OnceMic:
                def listen_once(self):
                    return clean

            tmp = VoicePipeline(
                self.nats, _OnceMic(), self._ollama, self._piper, self._speaker_play
            )
            await tmp.run_one_cycle(publish_listening=False)
