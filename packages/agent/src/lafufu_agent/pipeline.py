"""VoicePipeline: orchestrates one listen → think → speak cycle.

Decoupled from concrete mic/Whisper/Ollama/Piper — uses Protocol-style duck typing.
"""

import asyncio
import logging
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics

log = logging.getLogger(__name__)


class MicProtocol(Protocol):
    def listen_once(self) -> str:
        """Block until utterance ends; return transcribed text."""


class OllamaProtocol(Protocol):
    async def chat(self, user_text: str) -> str: ...


class PiperProtocol(Protocol):
    def synthesize(self, text: str) -> list[tuple[bytes, float]]: ...


class VoicePipeline:
    def __init__(self, nats_client, mic, ollama, piper, speaker_play=None) -> None:
        self.nats = nats_client
        self.mic = mic
        self.ollama = ollama
        self.piper = piper
        self.speaker_play = speaker_play  # callable(chunk_bytes) → None

    async def _publish_state(self, state_name: str) -> None:
        await nats_helper.publish_model(
            self.nats,
            f"{topics.AGENT_STATE}.{state_name}",
            schemas.AgentState(state=state_name),  # type: ignore[arg-type]
        )

    async def run_one_cycle(self) -> None:
        # ---- Listening ----
        await self._publish_state("listening")
        # Run blocking mic call in executor
        loop = asyncio.get_running_loop()
        transcript = await loop.run_in_executor(None, self.mic.listen_once)
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript(text=transcript, timestamp=time.time()),
        )

        # ---- Thinking ----
        await self._publish_state("thinking")
        reply_raw = await self.ollama.chat(transcript)

        from .emotion_parser import parse

        emotion, body = parse(reply_raw)

        # ---- Speaking (also publishes agent.reply) ----
        await self.speak(body, emotion)

    async def speak(self, text: str, emotion: str = "neutral", source: str = "llm") -> None:
        """Publish a reply event then synthesize + play TTS for the given text.

        `source` distinguishes LLM-generated replies from direct passthrough
        (puppet mode) so the admin UI can color them differently.
        """
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_REPLY,
            schemas.AgentReply(text=text, emotion=emotion, source=source),  # type: ignore[arg-type]
        )
        await self._publish_state("speaking")
        chunks = self.piper.synthesize(text)
        start_ts = time.monotonic()
        # speaker_play may be a callable (legacy) or an _AplayPlayer with
        # .play() and .end() methods. Detect and use the right interface.
        play_fn = (
            self.speaker_play.play
            if self.speaker_play and hasattr(self.speaker_play, "play")
            else self.speaker_play
        )
        # Pace chunk writes + RMS publishes to playback rate so animator's
        # jaw motion stays in sync with audio. Without this, all RMS events
        # fire in <50ms and the jaw barely twitches while audio plays for
        # several more seconds.
        chunk_dt = getattr(self.piper, "chunk_ms", 40) / 1000.0
        next_tick = time.monotonic()
        for audio_bytes, mouth_target in chunks:
            if play_fn:
                play_fn(audio_bytes)
            await nats_helper.publish_model(
                self.nats,
                topics.AGENT_TTS_RMS,
                schemas.AgentTtsRms(
                    ts=time.monotonic() - start_ts,
                    rms=mouth_target,
                    mouth_target=mouth_target,
                ),
            )
            # Sleep until the next chunk's wall-clock slot so we don't drift.
            next_tick += chunk_dt
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        # Tell the speaker the utterance is over so it can drain + close cleanly.
        if self.speaker_play and hasattr(self.speaker_play, "end"):
            self.speaker_play.end()
        await self._publish_state("idle")
