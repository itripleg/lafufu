"""VoicePipeline: orchestrates one listen → think → speak cycle.

Decoupled from concrete mic/Whisper/Ollama/Piper — uses Protocol-style duck typing.
"""

import asyncio
import logging
import socket
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
        try:
            await self._run_one_cycle_inner(publish_listening=publish_listening)
        except Exception:
            log.exception("agent.cycle.failed")
            await self._publish_state("degraded")
            await self._publish_state("idle")
            raise

    async def _run_one_cycle_inner(self, publish_listening: bool = True) -> None:
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
        # The parser returns "" when the LLM omitted the emotion tag entirely.
        # Fall back to "neutral" here so AgentReply schema validation passes —
        # the DB lookup downstream (resolve_emotion_to_play_intent) handles the
        # unknown → no-pose case for non-empty unknown names.
        await self.speak(body, emotion or "neutral")

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
        chunk_dt = getattr(self.piper, "chunk_ms", 40) / 1000.0
        loop = asyncio.get_running_loop()

        # Render the WHOLE utterance up front, THEN play + pace the jaw against
        # playback start. The earlier chunk-streaming approach let a slow synth
        # fall behind realtime: when the consumer waited on the next chunk, the
        # jaw-pacing clock kept advancing, so the jaw drifted from the audio and
        # ran on after the voice stopped. Buffering removes that producer race —
        # the legacy monolith rendered each utterance fully before playing, and
        # the bench testbed confirmed buffered/file playback stays locked while
        # stdin-streaming desyncs. Tradeoff: a short beat before speech while the
        # reply synthesizes.
        def _synth_all() -> list[tuple[bytes, float]]:
            gen = (
                self.piper.synthesize_stream(text)
                if hasattr(self.piper, "synthesize_stream")
                else iter(self.piper.synthesize(text))
            )
            return list(gen)

        try:
            # Synthesis blocks; run it in an executor so the loop keeps
            # servicing NATS + callbacks during the synth beat.
            chunks = await loop.run_in_executor(None, _synth_all)

            # Feed the already-synthesized audio to the speaker in a worker
            # thread so its blocking writes never stall the loop; aplay then
            # plays at a steady realtime rate. We pace the RMS/jaw track against
            # the moment playback began (t0) — exactly like the monolith, and
            # with all audio buffered there is no producer race to drift against.
            def _play_all() -> None:
                if not play_fn:
                    return
                for audio_bytes, _rms in chunks:
                    play_fn(audio_bytes)

            play_fut = loop.run_in_executor(None, _play_all)
            t0 = time.monotonic()

            for i, (_audio_bytes, raw_rms) in enumerate(chunks):
                # Stop pacing early if playback died (speaker/aplay raised).
                if play_fut.done() and play_fut.exception() is not None:
                    break
                # Adapt raw RMS → 0..1 mouth target against recent loudness.
                mouth_target = self._lipsync_norm.update(raw_rms)
                # Publish chunk i's jaw target when it should be audible: t0 plus
                # i*chunk_dt. animator.lipsync.offset_ms trims the residual
                # buffer/prepend lead.
                target_t = t0 + i * chunk_dt
                sleep_for = target_t - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                await nats_helper.publish_model(
                    self.nats,
                    topics.AGENT_TTS_RMS,
                    schemas.AgentTtsRms(
                        ts=time.monotonic() - t0,
                        rms=mouth_target,
                        mouth_target=mouth_target,
                    ),
                )
            # Surface any playback error and wait for playback to drain.
            await play_fut
        finally:
            # Always close the speaker + return to idle, even if playback
            # raised — otherwise the aplay subprocess leaks and the agent
            # state is stuck on "speaking".
            if self.speaker_play and hasattr(self.speaker_play, "end"):
                self.speaker_play.end()
            await self._publish_state("idle")
