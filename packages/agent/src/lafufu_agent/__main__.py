"""Entry: python -m lafufu_agent

Real production path with Whisper/Ollama/Piper/PyAudio.
Pulls config from env vars.
"""

import asyncio
import os
import wave
from pathlib import Path

import pyaudio

from .audio_capture import get_pyaudio, select_input_device
from .llm import Ollama
from .service import AgentService
from .stt import Whisper
from .tts import Piper
from .vad import SilenceDetector

SYSTEM_PROMPT = (
    "You are Lafufu, a mischievous and playful humanoid creature. "
    'Reply in no more than 20 words. Always output an "[emotion]" tag first '
    "(happy, sad, angry, surprised, neutral, agree, disagree), then the response. "
    "Never use emojis."
)


class RealMic:
    """Records from mic until silence, returns transcribed text via Whisper."""

    def __init__(self, whisper: Whisper, *, rate: int = 44100, chunk_ms: int = 40):
        self.whisper = whisper
        self.rate = rate
        self.chunk_size = int(rate * chunk_ms / 1000)
        self.tmp_wav = Path("/tmp/lafufu_capture.wav")

    def listen_once(self) -> str:
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
        det = SilenceDetector(
            silence_threshold=800, silent_chunks_required=int(1.5 * eff_rate / eff_chunk)
        )
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=eff_rate,
            input=True,
            input_device_index=device,
            frames_per_buffer=eff_chunk,
        )
        frames: list[bytes] = []
        try:
            while True:
                data = stream.read(eff_chunk, exception_on_overflow=False)
                det.observe(data)
                frames.append(data)
                if det.is_done(0):
                    break
                if len(frames) * eff_chunk / eff_rate > 10:  # max 10s
                    break
        finally:
            stream.stop_stream()
            stream.close()

        import audioop

        raw = b"".join(frames)
        if eff_rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, eff_rate, 16000, None)
        with wave.open(str(self.tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(raw)
        return self.whisper.transcribe(self.tmp_wav)


def _aplay_player():
    """Returns a callable(chunk_bytes) that streams to aplay (Pi only)."""
    import subprocess

    proc = None

    def play(chunk: bytes) -> None:
        nonlocal proc
        if proc is None:
            # Open aplay on first chunk; close in atexit
            device = os.environ.get("LAFUFU_APLAY_DEVICE", "default")
            proc = subprocess.Popen(
                ["aplay", "-q", "-D", device, "-f", "S16_LE", "-c", "1", "-r", "22050"],
                stdin=subprocess.PIPE,
            )
        proc.stdin.write(chunk)

    return play


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def main() -> None:
    whisper_model = os.environ.get("LAFUFU_WHISPER_MODEL", "tiny")
    qwen_model = os.environ.get("LAFUFU_LLM_MODEL", "qwen2.5:7b")
    piper_model_path = Path(os.environ.get("LAFUFU_PIPER_MODEL", "models/lafufu_voice.onnx"))
    ollama_url = os.environ.get("LAFUFU_OLLAMA_URL", "http://localhost:11434")
    # Default false: mic loop OFF unless explicitly enabled. Prevents the
    # ambient-noise → garbage transcript → reply → print runaway feedback.
    # Set LAFUFU_AGENT_AUTO_LISTEN=true (or toggle via admin in a later phase)
    # to enable continuous listening.
    auto_listen = _env_bool("LAFUFU_AGENT_AUTO_LISTEN", False)

    whisper = Whisper(model_name=whisper_model)
    ollama = Ollama(base_url=ollama_url, model=qwen_model, system_prompt=SYSTEM_PROMPT)
    piper = Piper(model_path=piper_model_path)
    mic = RealMic(whisper=whisper)
    player = _aplay_player()

    svc = AgentService(mic=mic, ollama=ollama, piper=piper, speaker_play=player)

    async def run():
        # Run base lifecycle. Mic loop only started if auto_listen is on.
        run_task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.5)  # let startup complete
        if auto_listen:
            svc.start_mic_loop()
        await run_task

    asyncio.run(run())


if __name__ == "__main__":
    main()
