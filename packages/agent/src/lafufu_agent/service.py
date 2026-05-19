"""AgentService: BaseService that runs the voice loop and accepts text intents."""

import asyncio
import logging
import subprocess

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .pipeline import VoicePipeline

log = logging.getLogger(__name__)


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

    def __init__(self, mic, ollama, piper, speaker_play=None, nats_url: str | None = None) -> None:
        super().__init__()
        self._mic = mic
        self._ollama = ollama
        self._piper = piper
        self._speaker_play = speaker_play
        self._nats_url = nats_url
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
            async with self._cycle_lock:
                try:
                    await self._pipeline.run_one_cycle()
                except Exception as e:
                    self.log.exception("voice_cycle.failed error=%s", e)
                    await asyncio.sleep(1.0)
