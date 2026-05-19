"""Entry: python -m lafufu_agent

Real production path with Whisper/Ollama/Piper/PyAudio.
Pulls config from env vars.
"""

import asyncio
import os
from pathlib import Path

import pyaudio

from .audio_capture import get_pyaudio, select_input_device
from .llm import Ollama
from .service import AgentService
from .tts import Piper

SYSTEM_PROMPT = (
    "You are Lafufu, a mischievous and playful humanoid creature. "
    'Reply in no more than 20 words. Always output an "[emotion]" tag first '
    "(happy, sad, angry, surprised, neutral, agree, disagree), then the response. "
    "Never use emojis."
)


class RealMic:
    """Records from mic until silence using pre-roll + started-flag VAD, then
    transcribes via Whisper. Only commits audio AROUND detected speech — silent
    waiting time is discarded so Whisper doesn't hallucinate words from
    minutes of ambient noise.

    Port of the original monolith's `record_until_silence` pattern.
    """

    PRE_ROLL_S = 0.35  # audio kept BEFORE detected speech onset
    MAX_RECORD_S = 10.0  # hard cap on a single utterance
    MAX_WAIT_S = 30.0  # hard cap waiting for speech onset before giving up
    MIN_VOICED_CHUNKS = 5  # ignore sub-200ms blips (clicks, taps, brief sounds)

    def __init__(
        self,
        stt,
        *,
        rate: int = 44100,
        chunk_ms: int = 40,
        silence_threshold: int = 800,
        silence_tail_s: float = 1.5,
    ):
        self.stt = stt
        self.rate = rate
        self.chunk_size = int(rate * chunk_ms / 1000)
        # Live-tunable via admin UI (agent.silence_threshold). Higher = less
        # sensitive to ambient noise. Default 800 matches the original monolith.
        self.silence_threshold = silence_threshold
        # Trailing silence (seconds) that ends an utterance. Live-tunable via
        # agent.silence_seconds.
        self.silence_tail_s = silence_tail_s

    def set_stt(self, stt) -> None:
        """Hot-swap STT instance (called by AgentService on config.changed)."""
        self.stt = stt

    def listen_once(self) -> str:
        import collections

        p = get_pyaudio()
        device = select_input_device(p)
        eff_rate = self.rate
        try:
            if device is not None and not p.is_format_supported(
                float(self.rate),
                input_device=device,
                input_channels=1,
                input_format=pyaudio.paInt16,
            ):
                eff_rate = int(p.get_device_info_by_index(device).get("defaultSampleRate", 16000))
        except (ValueError, OSError):
            if device is not None:
                eff_rate = int(p.get_device_info_by_index(device).get("defaultSampleRate", 16000))

        eff_chunk = max(1, int(eff_rate * 0.04))
        chunks_per_s = eff_rate / eff_chunk
        silence_chunks_end = int(self.silence_tail_s * chunks_per_s)
        pre_roll_size = int(self.PRE_ROLL_S * chunks_per_s)
        max_chunks_recording = int(self.MAX_RECORD_S * chunks_per_s)
        max_chunks_waiting = int(self.MAX_WAIT_S * chunks_per_s)

        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=eff_rate,
            input=True,
            input_device_index=device,
            frames_per_buffer=eff_chunk,
        )

        pre_roll: collections.deque[bytes] = collections.deque(maxlen=pre_roll_size)
        frames: list[bytes] = []
        started = False
        voiced_chunks = 0
        silent_chunks = 0
        waiting_chunks = 0

        try:
            while True:
                data = stream.read(eff_chunk, exception_on_overflow=False)
                rms = audio_rms_bytes(data)
                loud = rms >= self.silence_threshold

                if not started:
                    pre_roll.append(data)
                    if loud:
                        voiced_chunks += 1
                        if voiced_chunks >= self.MIN_VOICED_CHUNKS:
                            # Real speech — flush pre-roll into frames and switch
                            # to recording mode.
                            started = True
                            frames.extend(pre_roll)
                            pre_roll.clear()
                    else:
                        voiced_chunks = 0
                        waiting_chunks += 1
                        if waiting_chunks > max_chunks_waiting:
                            # Gave up waiting — nothing said.
                            return ""
                    continue

                frames.append(data)
                silent_chunks = silent_chunks + 1 if not loud else 0
                if silent_chunks > silence_chunks_end:
                    break
                if len(frames) > max_chunks_recording:
                    break
        finally:
            stream.stop_stream()
            stream.close()

        if not started or not frames:
            return ""

        import audioop

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)

        # Convert int16 PCM bytes -> float32 numpy array normalized to [-1, 1].
        # Both STT backends accept this directly, skipping a disk write + decode.
        import numpy as np

        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.stt.transcribe(audio_np)


def audio_rms_bytes(pcm16_bytes: bytes) -> float:
    """Local copy so we don't pull in the whole VAD module just for this."""
    try:
        import audioop
    except ModuleNotFoundError:
        import audioop_lts as audioop  # type: ignore[no-redef]
    if not pcm16_bytes:
        return 0.0
    return float(audioop.rms(pcm16_bytes, 2))


class _AplayPlayer:
    """Per-utterance aplay subprocess.

    Opens a fresh aplay on the first `play()` of an utterance, streams chunks
    to its stdin, then `end()` closes the pipe so aplay drains and exits.
    Avoids inter-utterance underruns (which were clipping the beginning of
    each new reply because the previous aplay was in the middle of a stall).

    Sample rate is set at construction so the aplay invocation matches the
    Piper voice's native rate (no resample, no pitch shift).

    The pipeline calls play() per chunk and end() after the last chunk.
    """

    def __init__(self, sample_rate: int = 22050) -> None:
        import subprocess

        self._subprocess = subprocess
        self._proc: subprocess.Popen | None = None
        self._sample_rate = int(sample_rate)
        # Buffer ~2s, period ~0.2s, scaled to sample rate.
        self._buffer_size = self._sample_rate * 2
        self._period_size = self._sample_rate // 5

    def play(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._proc is None or self._proc.poll() is not None:
            device = os.environ.get("LAFUFU_APLAY_DEVICE", "default")
            self._proc = self._subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(self._sample_rate),
                    f"--buffer-size={self._buffer_size}",
                    f"--period-size={self._period_size}",
                ],
                stdin=self._subprocess.PIPE,
            )
        try:
            self._proc.stdin.write(chunk)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            # aplay died mid-utterance — reset for next call
            self._proc = None

    def end(self) -> None:
        """Close stdin so aplay drains its buffer + a final ~0.1s silence pad."""
        if self._proc is None:
            return
        try:
            # Append ~100ms of silence so aplay's last buffer flush carries the
            # full utterance through the speaker without losing the tail samples.
            silence_frames = self._sample_rate // 10
            self._proc.stdin.write(b"\x00\x00" * silence_frames)
            self._proc.stdin.flush()
            self._proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        # Don't wait() — let the process finish in the background. Next play()
        # call opens a fresh proc anyway.
        self._proc = None


def main() -> None:
    from .stt import make_stt

    whisper_model = os.environ.get("LAFUFU_WHISPER_MODEL", "tiny.en")
    stt_backend = os.environ.get("LAFUFU_STT_BACKEND", "openai-whisper")
    qwen_model = os.environ.get("LAFUFU_LLM_MODEL", "qwen2.5:7b")
    piper_model_path = Path(os.environ.get("LAFUFU_PIPER_MODEL", "models/lafufu_voice.onnx"))
    ollama_url = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

    stt = make_stt(stt_backend, model_name=whisper_model)
    ollama = Ollama(base_url=ollama_url, model=qwen_model, system_prompt=SYSTEM_PROMPT)
    piper = Piper(model_path=piper_model_path)
    piper.load()  # populate sample_rate from the .onnx config
    mic = RealMic(stt=stt)
    player = _AplayPlayer(sample_rate=piper.sample_rate)

    # Mic loop is started/stopped by the config.changed.agent.auto_listen
    # subscriber inside AgentService — driven by the DB setting via the
    # snapshot mechanism. Env vars no longer toggle it.
    svc = AgentService(
        mic=mic,
        ollama=ollama,
        piper=piper,
        speaker_play=player,
        stt=stt,
        stt_factory=lambda backend, model: make_stt(backend, model_name=model),
    )

    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
