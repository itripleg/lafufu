"""Shared helpers for the lipsync debug scripts.

No NATS, no asyncio, no `lafufu_agent` / `lafufu_animator` imports — these
scripts talk DIRECTLY to the U2D2 over dynamixel_sdk and to aplay over a
subprocess pipe. The point is to bypass every layer of our own code so we
can isolate which one is causing the mouth-vs-audio desync.

Constants here are copied from `packages/animator/.../pose.py` so this dir
stands alone — if the jaw is recalibrated on the bench, update JAW_OPEN_POS
and JAW_CLOSE_POS here too.
"""

from __future__ import annotations

import atexit
import contextlib
import glob
import logging
import platform
import signal
import subprocess
import sys
import wave
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

# --- DXL constants (copied from packages/animator/.../pose.py + dxl_bus.py) ---
JAW_DXL_ID = 4
JAW_OPEN_POS = 1594  # lower DXL value = mouth open (calibrated value)
JAW_CLOSE_POS = 1811  # higher DXL value = mouth closed
ADDR_TORQUE_ENABLE = 64
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132

PROTOCOL_VERSION = 2.0
BAUD_CANDIDATES = (57600, 115200, 1_000_000, 2_000_000, 3_000_000, 4_000_000)

log = logging.getLogger("lipsync-debug")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _port_candidates() -> list[str]:
    if platform.system().lower() == "linux":
        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return [f"COM{i}" for i in range(1, 30)]


class JawBus:
    """Open the U2D2, torque-on the jaw, write goal positions. That's it.

    write_goal() is a SYNCHRONOUS blocking serial round-trip — same as our
    real DxlBus, but with zero asyncio / threading / smoothing on top.
    close() is registered with atexit and SIGINT/SIGTERM so Ctrl-C cannot
    leave torque ON (which would let the head go limp into something).
    """

    def __init__(self, port_handler, packet) -> None:
        self.port_handler = port_handler
        self.packet = packet
        self._closed = False

    @classmethod
    def open(cls) -> JawBus:
        from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler  # lazy

        last_err: Exception | None = None
        for p in _port_candidates():
            try:
                ph = PortHandler(p)
                if not ph.openPort():
                    continue
                for b in BAUD_CANDIDATES:
                    if not ph.setBaudRate(b):
                        continue
                    pkt = PacketHandler(PROTOCOL_VERSION)
                    # Probe motor 1 to confirm the bus is alive.
                    _, rc, _ = pkt.read4ByteTxRx(ph, 1, ADDR_PRESENT_POSITION)
                    if rc == COMM_SUCCESS:
                        log.info("dxl.open port=%s baud=%d", p, b)
                        bus = cls(ph, pkt)
                        bus._torque_on()
                        bus._install_safety_hooks()
                        return bus
                ph.closePort()
            except Exception as e:
                last_err = e
        raise ConnectionError(f"U2D2 not found on any port; last error: {last_err}")

    def _torque_on(self) -> None:
        from dynamixel_sdk import COMM_SUCCESS

        rc, err = self.packet.write1ByteTxRx(self.port_handler, JAW_DXL_ID, ADDR_TORQUE_ENABLE, 1)
        if rc != COMM_SUCCESS or err != 0:
            raise OSError(f"torque_on failed rc={rc} err={err}")

    def _install_safety_hooks(self) -> None:
        atexit.register(self.close)

        def _sig(_signum, _frame):
            self.close()
            sys.exit(0)

        for s in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(Exception):
                signal.signal(s, _sig)

    def write_goal(self, position: int) -> None:
        """Clamp + write goal position. Synchronous serial round-trip."""
        if self._closed:
            return
        lo, hi = sorted((JAW_OPEN_POS, JAW_CLOSE_POS))
        pos = max(lo, min(hi, int(position)))
        from dynamixel_sdk import COMM_SUCCESS

        rc, err = self.packet.write4ByteTxRx(self.port_handler, JAW_DXL_ID, ADDR_GOAL_POSITION, pos)
        if rc != COMM_SUCCESS or err != 0:
            raise OSError(f"write_goal failed rc={rc} err={err} pos={pos}")

    def close(self) -> None:
        """Disable torque and close the port. Idempotent."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self.packet.write1ByteTxRx(self.port_handler, JAW_DXL_ID, ADDR_TORQUE_ENABLE, 0)
        with contextlib.suppress(Exception):
            self.port_handler.closePort()


def open_pct_to_dxl(pct: float) -> int:
    """0.0 = mouth closed, 1.0 = fully open. Maps to DXL position.

    JAW_OPEN_POS < JAW_CLOSE_POS, so we interpolate in DXL units accordingly.
    """
    pct = max(0.0, min(1.0, pct))
    return int(JAW_CLOSE_POS + (JAW_OPEN_POS - JAW_CLOSE_POS) * pct)


@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int  # bytes per sample
    frames: int


def open_wav(path: str) -> tuple[wave.Wave_read, WavInfo]:
    """Open a 16-bit PCM mono WAV. Returns (reader, info).

    Piper outputs match this format. Resample externally (sox / ffmpeg) if
    you have something exotic.
    """
    w = wave.open(path, "rb")  # noqa: SIM115 — caller closes after iter_chunks
    info = WavInfo(
        sample_rate=w.getframerate(),
        channels=w.getnchannels(),
        sample_width=w.getsampwidth(),
        frames=w.getnframes(),
    )
    if info.channels != 1 or info.sample_width != 2:
        raise ValueError(
            f"expected 16-bit mono WAV, got channels={info.channels} width={info.sample_width}"
        )
    return w, info


def aplay_popen(
    sample_rate: int, buffer_frames: int, period_frames: int, device: str = "default"
) -> subprocess.Popen:
    """Spawn aplay with the same flags as our production speaker path.

    buffer_frames + period_frames are in frames (samples for mono).
    """
    return subprocess.Popen(
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
            str(sample_rate),
            f"--buffer-size={buffer_frames}",
            f"--period-size={period_frames}",
        ],
        stdin=subprocess.PIPE,
    )


def aplay_file_popen(wav_path: str, device: str = "default") -> subprocess.Popen:
    """Spawn ``aplay file.wav`` — file mode, the way the legacy monolith does it.

    aplay reads the WAV header itself (format, rate, channels) and manages
    its own buffering. The motor loop paces against its own wall clock, not
    against an stdin write cadence, so motor + audio drift together when
    the system is loaded rather than against each other.
    """
    return subprocess.Popen(
        ["aplay", "-q", "-D", device, wav_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def rms_int16(chunk: bytes) -> float:
    """RAW int16 RMS (NOT normalised), matching the legacy monolith.

    Used by Monolith mode so the p10/p95 percentile floor/ceil are computed
    in the same units the legacy used. ``chunk_rms`` above normalises to
    ~[0, 1] which loses the per-utterance percentile semantics.
    """
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def percentile_sorted(vals_sorted: list[float], p: float) -> float:
    """Linear-interp percentile from a pre-sorted list — matches legacy."""
    if not vals_sorted:
        return 0.0
    p = max(0.0, min(1.0, p))
    if len(vals_sorted) == 1:
        return vals_sorted[0]
    idx = p * (len(vals_sorted) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(vals_sorted) - 1)
    frac = idx - lo
    return vals_sorted[lo] * (1.0 - frac) + vals_sorted[hi] * frac


def chunk_rms(chunk: bytes) -> float:
    """RMS of a 16-bit PCM chunk, normalised to ~[0, 1]."""
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    return rms / 32768.0


def iter_chunks(reader: wave.Wave_read, chunk_frames: int) -> Iterator[bytes]:
    """Yield raw byte chunks of chunk_frames each (last may be short)."""
    while True:
        b = reader.readframes(chunk_frames)
        if not b:
            return
        yield b
