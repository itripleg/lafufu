"""VoicePipeline: orchestrates one listen → think → speak cycle.

Decoupled from concrete mic/Whisper/Ollama/Piper — uses Protocol-style duck typing.
"""

import asyncio
import logging
import socket
import threading
import time
from datetime import datetime
from typing import Protocol

from lafufu_shared import nats_helper, netinfo, schemas, topics

from .intents import build_ip_slip, match_ip_intent, spoken_ip_answer

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
        # Skip the rest if mic returned empty (no speech detected) or if Whisper
        # gave us a near-empty / trivial transcript — saves an LLM round-trip
        # and prevents Lafufu from "replying to silence".
        clean = (transcript or "").strip()
        if len(clean) < 2:
            await self._publish_state("idle")
            return
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript(text=clean, timestamp=time.time()),
        )

        # ---- System intents (answered directly, no LLM) ----
        if match_ip_intent(clean):
            await self._answer_ip_query()
            return

        # ---- Thinking ----
        await self._publish_state("thinking")
        reply_raw = await self.ollama.chat(clean)

        from .emotion_parser import parse

        emotion, body = parse(reply_raw)

        # ---- Speaking (also publishes agent.reply) ----
        await self.speak(body, emotion)

    async def _answer_ip_query(self) -> None:
        """Answer the 'what's your IP' voice intent directly: print a slip
        on the receipt printer and speak the address. Bypasses the LLM."""
        ip = netinfo.primary_lan_ip()
        if ip is not None:
            slip = build_ip_slip(ip, socket.gethostname(), datetime.now())
            await nats_helper.publish_model(
                self.nats,
                topics.PRINTER_INTENT_PRINT_TEXT,
                schemas.PrinterIntentPrintText(text=slip),
            )
        await self.speak(spoken_ip_answer(ip), emotion="neutral", source="system")

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
        start_ts = time.monotonic()
        next_tick = time.monotonic()

        # Stream synth in an executor — chunks arrive via a bounded queue so
        # blocking generator iteration doesn't freeze the loop. `cancelled`
        # lets us stop the producer if the consumer aborts early.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(maxsize=8)
        cancelled = threading.Event()

        def _produce():
            try:
                gen = (
                    self.piper.synthesize_stream(text)
                    if hasattr(self.piper, "synthesize_stream")
                    else iter(self.piper.synthesize(text))
                )
                for item in gen:
                    if cancelled.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        try:
            producer_fut = loop.run_in_executor(None, _produce)
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    audio_bytes, mouth_target = item
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
            finally:
                # Signal the producer to stop, then drain the queue so any
                # pending put() coroutine can complete — without this, a
                # producer thread blocked on a full queue would hang the
                # `await producer_fut` below forever.
                cancelled.set()
                while True:
                    try:
                        drained = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if drained is None:
                        break
                await producer_fut
        finally:
            # Always close the speaker + return to idle, even if playback
            # raised — otherwise the aplay subprocess leaks and the agent
            # state is stuck on "speaking".
            if self.speaker_play and hasattr(self.speaker_play, "end"):
                self.speaker_play.end()
            await self._publish_state("idle")
