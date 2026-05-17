"""AgentService: BaseService that runs the voice loop and accepts text intents."""

import asyncio
import logging

from lafufu_shared import nats_helper, schemas, topics
from lafufu_shared.base_service import BaseService

from .pipeline import VoicePipeline

log = logging.getLogger(__name__)


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

        # Subscribe to text-message intent (headless input path)
        await nats_helper.subscribe_model(
            self.nats,
            topics.AGENT_INTENT_TEXT_MESSAGE,
            schemas.AgentIntentTextMessage,
            self._on_text_message,
        )

        # Note: we do NOT auto-start the mic loop in tests (FakeMicForService blocks).
        # Real `main.py` calls start_mic_loop() explicitly after construction.

    async def on_shutdown(self) -> None:
        await self._publish_state("shutdown")
        if self._mic_loop_task:
            self._mic_loop_task.cancel()

    async def _publish_state(self, name: str) -> None:
        await nats_helper.publish_model(
            self.nats,
            f"{topics.AGENT_STATE}.{name}",
            schemas.AgentState(state=name),  # type: ignore[arg-type]
        )

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
