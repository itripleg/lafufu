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
from .lipsync import LipsyncNormalizer

log = logging.getLogger(__name__)


class MicProtocol(Protocol):
    def listen_once(self) -> str:
        """Block until utterance ends; return transcribed text."""


class OllamaProtocol(Protocol):
    async def chat(self, user_text: str, history: list[tuple[str, str]] | None = None) -> str: ...


class PiperProtocol(Protocol):
    def synthesize(self, text: str) -> list[tuple[bytes, float]]: ...


class VoicePipeline:
    def __init__(self, nats_client, mic, ollama, piper, speaker_play=None) -> None:
        self.nats = nats_client
        self.mic = mic
        self.ollama = ollama
        self.piper = piper
        self.speaker_play = speaker_play  # callable(chunk_bytes) → None
        # Adaptive lipsync normalization. Persists across utterances so its
        # rolling window stays warm — only the first utterance sees a cold start.
        self._lipsync_norm = LipsyncNormalizer()

    async def _publish_state(self, state_name: str) -> None:
        await nats_helper.publish_model(
            self.nats,
            f"{topics.AGENT_STATE}.{state_name}",
            schemas.AgentState(state=state_name),  # type: ignore[arg-type]
        )

    async def run_one_cycle(self, publish_listening: bool = True) -> None:
        # ---- Listening ----
        if publish_listening:
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
        # jaw motion stays in sync with audio. Anchored to the FIRST play_fn
        # call (set inside the loop) — anchoring before queue.get() instead
        # would leave next_tick in the past by however long Piper took to
        # synthesize the first chunk (~100ms+), so every sleep_for would
        # compute negative and the loop would race through publishing
        # back-to-back. With first-write anchoring, chunk N publishes at
        # audio_start + (N+1)*chunk_dt — i.e. exactly when ALSA's first
        # period flips chunk N to audible (period_size = chunk_dt).
        chunk_dt = getattr(self.piper, "chunk_ms", 40) / 1000.0
        start_ts: float | None = None
        next_tick = 0.0

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
                    audio_bytes, raw_rms = item
                    if play_fn:
                        play_fn(audio_bytes)
                    # Anchor the pacing clock to the FIRST write into aplay —
                    # that's the moment audio begins buffering toward the
                    # speaker. Anchoring before queue.get() would leave the
                    # pacing math in the past by the synth latency and every
                    # subsequent sleep_for would compute negative.
                    if start_ts is None:
                        start_ts = time.monotonic()
                        next_tick = start_ts
                    # Adapt raw RMS → 0..1 mouth target against recent loudness.
                    mouth_target = self._lipsync_norm.update(raw_rms)
                    # Sleep until next_tick (one chunk_dt after the previous
                    # publish, i.e. when ALSA flips the freshly-buffered chunk
                    # to audible), THEN publish so the jaw moves with the
                    # audio rather than ahead of it.
                    next_tick += chunk_dt
                    sleep_for = next_tick - time.monotonic()
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                    await nats_helper.publish_model(
                        self.nats,
                        topics.AGENT_TTS_RMS,
                        schemas.AgentTtsRms(
                            ts=time.monotonic() - start_ts,
                            rms=mouth_target,
                            mouth_target=mouth_target,
                        ),
                    )
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
