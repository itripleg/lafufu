"""Entry: python -m lafufu_agent

Real production path with Whisper/Ollama/Piper/PyAudio.
Pulls config from env vars.
"""

import asyncio
import logging
import os
from pathlib import Path

import pyaudio
from lafufu_shared.prompts import DEFAULT_SYSTEM_PROMPT as SYSTEM_PROMPT

from .audio_capture import get_pyaudio, select_input_device
from .llm import Ollama
from .service import AgentService
from .tts import Piper


class RealMic:
    """Records from mic until silence using pre-roll + started-flag VAD, then
    transcribes via STT. Only commits audio AROUND detected speech — silent
    waiting time is discarded so STT doesn't hallucinate words from
    minutes of ambient noise.

    Holds a single PyAudio stream open across listen_once calls —
    opening/closing it every utterance was costing ~50-200ms per cycle and
    fragmenting the first buffer (clipping the leading 20-40ms of speech).

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
        wake_detector=None,
    ):
        self.stt = stt
        self.rate = rate
        self.chunk_ms = chunk_ms
        # Live-tunable via admin UI (agent.silence_threshold). Higher = less
        # sensitive to ambient noise. Default 800 matches the original monolith.
        self.silence_threshold = silence_threshold
        # Trailing silence (seconds) that ends an utterance. Live-tunable via
        # agent.silence_seconds.
        self.silence_tail_s = silence_tail_s
        # When set, wait_for_onset gates on a wake word instead of RMS onset.
        self.wake_detector = wake_detector

        # Lazily populated on first listen_once — needs PyAudio init.
        self._stream = None
        self._eff_rate: int | None = None
        self._eff_chunk: int | None = None
        self._device_index: int | None = None
        # Persistent audioop.ratecv state for the wake-word resample path —
        # needed across chunks so the resampler doesn't restart its phase on
        # every call (which would produce audible artifacts in the 16k stream).
        self._wake_rcv_state = None

    def set_stt(self, stt) -> None:
        """Hot-swap STT instance (called by AgentService on config.changed)."""
        self.stt = stt

    def _ensure_stream(self) -> None:
        """Open the input stream once, with format probing. Subsequent calls are no-ops."""
        if self._stream is not None:
            return

        p = get_pyaudio()
        self._device_index = select_input_device(p)
        eff_rate = self.rate
        try:
            if self._device_index is not None and not p.is_format_supported(
                float(self.rate),
                input_device=self._device_index,
                input_channels=1,
                input_format=pyaudio.paInt16,
            ):
                eff_rate = int(
                    p.get_device_info_by_index(self._device_index).get("defaultSampleRate", 16000)
                )
        except (ValueError, OSError):
            if self._device_index is not None:
                eff_rate = int(
                    p.get_device_info_by_index(self._device_index).get("defaultSampleRate", 16000)
                )

        self._eff_rate = eff_rate
        self._eff_chunk = max(1, int(eff_rate * self.chunk_ms / 1000))
        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._eff_rate,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=self._eff_chunk,
        )

    def close(self) -> None:
        """Stop and close the cached input stream (called on service shutdown)."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        self._wake_rcv_state = None

    def _resample_for_wakeword(self, data: bytes) -> bytes:
        """Convert a chunk from the input stream's native rate to 16kHz mono
        int16, which is what openwakeword expects. Pass-through when the
        input is already at 16kHz."""
        if self._eff_rate == 16000:
            return data
        try:
            import audioop
        except ModuleNotFoundError:
            import audioop_lts as audioop  # type: ignore[no-redef]
        out, self._wake_rcv_state = audioop.ratecv(
            data, 2, 1, self._eff_rate, 16000, self._wake_rcv_state
        )
        return out

    def wait_for_onset(self) -> tuple[bool, list[bytes]]:
        """Listen until speech starts or MAX_WAIT_S elapses.

        Returns (started, pre_roll_frames). Does NOT hold any external lock —
        safe to run while other coroutines need the agent.
        """
        import collections

        self._ensure_stream()
        stream = self._stream
        eff_rate = self._eff_rate
        eff_chunk = self._eff_chunk
        chunks_per_s = eff_rate / eff_chunk
        pre_roll_size = int(self.PRE_ROLL_S * chunks_per_s)
        max_chunks_waiting = int(self.MAX_WAIT_S * chunks_per_s)

        # Drain anything that piled up while we were synthesizing/speaking.
        # PyAudio's get_read_available counts samples, not chunks.
        try:
            stale = stream.get_read_available()
            while stale >= eff_chunk:
                stream.read(eff_chunk, exception_on_overflow=False)
                stale -= eff_chunk
        except OSError:
            pass

        pre_roll: collections.deque[bytes] = collections.deque(maxlen=pre_roll_size)
        voiced_chunks = 0
        waiting_chunks = 0

        while True:
            data = stream.read(eff_chunk, exception_on_overflow=False)
            pre_roll.append(data)

            if self.wake_detector is not None:
                # Gate on wake-word: skip RMS heuristics, only fire when the
                # detector's score crosses its threshold. Whisper stays idle
                # the rest of the time.
                chunk_16k = self._resample_for_wakeword(data)
                score = self.wake_detector.feed(chunk_16k)
                if score >= self.wake_detector.threshold:
                    # Drop the detector's internal buffer so the same wake
                    # audio doesn't immediately re-fire on the next listen.
                    self.wake_detector.reset()
                    return True, list(pre_roll)
                waiting_chunks += 1
                if waiting_chunks > max_chunks_waiting:
                    return False, []
                continue

            rms = audio_rms_bytes(data)
            loud = rms >= self.silence_threshold
            if loud:
                voiced_chunks += 1
                if voiced_chunks >= self.MIN_VOICED_CHUNKS:
                    # Real speech — hand the pre-roll buffer to the recorder.
                    return True, list(pre_roll)
            else:
                voiced_chunks = 0
                waiting_chunks += 1
                if waiting_chunks > max_chunks_waiting:
                    # Gave up waiting — nothing said.
                    return False, []

    def record_until_silence(self, pre_roll: list[bytes]) -> str:
        """Continue reading after onset, transcribe when silence ends."""
        import audioop
        import numpy as np

        stream = self._stream
        eff_rate = self._eff_rate
        eff_chunk = self._eff_chunk
        chunks_per_s = eff_rate / eff_chunk
        silence_chunks_end = int(self.silence_tail_s * chunks_per_s)
        max_chunks_recording = int(self.MAX_RECORD_S * chunks_per_s)

        frames: list[bytes] = list(pre_roll)
        silent_chunks = 0

        while True:
            data = stream.read(eff_chunk, exception_on_overflow=False)
            rms = audio_rms_bytes(data)
            loud = rms >= self.silence_threshold
            frames.append(data)
            silent_chunks = silent_chunks + 1 if not loud else 0
            if silent_chunks > silence_chunks_end:
                break
            if len(frames) > max_chunks_recording:
                break

        if not frames:
            return ""

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)

        # Convert int16 PCM bytes -> float32 numpy array normalized to [-1, 1].
        # Both STT backends accept this directly, skipping a disk write + decode.
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.stt.transcribe(audio_np)

    def listen_once(self) -> str:
        """Backward-compat single-call interface (used by text intent paths)."""
        started, pre_roll = self.wait_for_onset()
        if not started:
            return ""
        return self.record_until_silence(pre_roll)


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
    models_dir = Path(os.environ.get("LAFUFU_MODELS_DIR", "/srv/lafufu/models"))
    voice_model = os.environ.get("LAFUFU_VOICE_MODEL", "lafufu_voice")
    # LAFUFU_PIPER_MODEL (full path) wins over LAFUFU_VOICE_MODEL (bare name)
    # for backwards compat with existing systemd units.
    if "LAFUFU_PIPER_MODEL" in os.environ:
        piper_model_path = Path(os.environ["LAFUFU_PIPER_MODEL"])
    else:
        piper_model_path = models_dir / f"{voice_model}.onnx"
    ollama_url = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")

    def make_piper(name: str) -> Piper:
        """Build + load a Piper for a voice name (resolved against models_dir)."""
        p = Piper(model_path=models_dir / f"{name}.onnx")
        p.load()  # raises FileNotFoundError if the .onnx is missing
        return p

    def make_player(sample_rate: int) -> _AplayPlayer:
        return _AplayPlayer(sample_rate=sample_rate)

    stt = make_stt(stt_backend, model_name=whisper_model)
    ollama = Ollama(base_url=ollama_url, model=qwen_model, system_prompt=SYSTEM_PROMPT)
    piper = Piper(model_path=piper_model_path)
    piper.load()  # populate sample_rate from the .onnx config

    wake_detector = None
    if os.environ.get("LAFUFU_WAKEWORD_ENABLED", "").lower() in ("1", "true", "yes"):
        from .wakeword import OpenWakeWordDetector, has_openwakeword

        if not has_openwakeword():
            logging.getLogger(__name__).warning(
                "wakeword.enabled_but_missing — install with `uv sync --extra wakeword`; "
                "falling back to RMS-based onset"
            )
        else:
            wake_detector = OpenWakeWordDetector(
                model_name=os.environ.get("LAFUFU_WAKEWORD_MODEL", "hey_jarvis_v0.1"),
                threshold=float(os.environ.get("LAFUFU_WAKEWORD_THRESHOLD", "0.5")),
            )

    mic = RealMic(stt=stt, wake_detector=wake_detector)
    player = make_player(piper.sample_rate)

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
        piper_factory=make_piper,
        player_factory=make_player,
    )

    asyncio.run(svc.run())


if __name__ == "__main__":
    main()
