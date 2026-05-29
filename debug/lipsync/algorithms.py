"""Lipsync algorithm functions, parameterized by dataclasses.

The 4 CLI scripts (00..03) and the web server (server.py) both call into
these functions — they are the single source of truth for the algorithms.
Each function takes a Cfg dataclass and an optional `threading.Event` so
the server can interrupt a run mid-flight (Ctrl-C handles the CLI case).
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from dataclasses import dataclass

from common import (
    JawBus,
    aplay_popen,
    chunk_rms,
    iter_chunks,
    open_pct_to_dxl,
    open_wav,
)

# --- configs ---


@dataclass
class ServoOnlyCfg:
    freq_hz: float = 4.0
    duration_s: float = 5.0
    tick_hz: int = 30


@dataclass
class DirectCfg:
    chunk_ms: int = 40
    alsa_buffer_ms: int = 1000
    alsa_period_ms: int = 40
    offset_ms: int = 0
    rms_min: float = 0.005
    rms_max: float = 0.30
    alsa_device: str = "default"


@dataclass
class EnvelopeCfg(DirectCfg):
    attack_ms: int = 20
    release_ms: int = 80


@dataclass
class GateCfg(DirectCfg):
    gate_threshold: float = 0.02
    open_pct: float = 1.0


# --- shared chunked-playback loop ---


def _run_chunked(cfg: DirectCfg, wav_path: str, target_fn, stop: threading.Event) -> None:
    """Drive jaw + aplay one chunk at a time.

    target_fn(prev_pos, rms, cfg) -> new_pos in [0..1]. Modes plug in
    their own target_fn; the loop, timing, audio, and bus handling
    are identical across modes — so any timing difference between
    modes is purely the target function, never the runner.
    """
    reader, info = open_wav(wav_path)
    try:
        chunk_frames = max(1, info.sample_rate * cfg.chunk_ms // 1000)
        buffer_frames = info.sample_rate * cfg.alsa_buffer_ms // 1000
        period_frames = info.sample_rate * cfg.alsa_period_ms // 1000
        chunks = list(iter_chunks(reader, chunk_frames))
    finally:
        reader.close()

    bus = JawBus.open()
    proc = aplay_popen(info.sample_rate, buffer_frames, period_frames, cfg.alsa_device)

    try:
        dt = cfg.chunk_ms / 1000.0
        offset_chunks = cfg.offset_ms // cfg.chunk_ms
        next_tick = time.monotonic()
        prev = 0.0
        for i, chunk in enumerate(chunks):
            if stop.is_set():
                break
            j = i + offset_chunks
            rms = chunk_rms(chunks[j]) if 0 <= j < len(chunks) else 0.0
            prev = target_fn(prev, rms, cfg)
            bus.write_goal(open_pct_to_dxl(prev))
            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                break
            next_tick += dt
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0 and stop.wait(timeout=sleep_for):
                break

        bus.write_goal(open_pct_to_dxl(0.0))
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.wait(timeout=3)
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
        bus.close()


# --- modes ---


def run_servo_only(cfg: ServoOnlyCfg, stop: threading.Event | None = None) -> None:
    """No audio: sine-sweep the jaw open<->closed at freq_hz for duration_s."""
    stop = stop or threading.Event()
    bus = JawBus.open()
    try:
        dt = 1.0 / cfg.tick_hz
        t0 = time.monotonic()
        next_tick = t0
        while not stop.is_set():
            t = time.monotonic() - t0
            if t >= cfg.duration_s:
                break
            pct = 0.5 * (1.0 + math.sin(2 * math.pi * cfg.freq_hz * t))
            bus.write_goal(open_pct_to_dxl(pct))
            next_tick += dt
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0 and stop.wait(timeout=sleep_for):
                break
        bus.write_goal(open_pct_to_dxl(0.0))
        time.sleep(0.3)
    finally:
        bus.close()


def _direct_target(_prev: float, rms: float, cfg: DirectCfg) -> float:
    pct = (rms - cfg.rms_min) / max(1e-6, cfg.rms_max - cfg.rms_min)
    return max(0.0, min(1.0, pct))


def run_direct(cfg: DirectCfg, wav_path: str, stop: threading.Event | None = None) -> None:
    """RMS -> jaw, no envelope, no smoothing."""
    _run_chunked(cfg, wav_path, _direct_target, stop or threading.Event())


def _alpha(time_constant_ms: float, dt_ms: float) -> float:
    if time_constant_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_ms / time_constant_ms)


def _envelope_target(prev: float, rms: float, cfg: EnvelopeCfg) -> float:
    target = (rms - cfg.rms_min) / max(1e-6, cfg.rms_max - cfg.rms_min)
    target = max(0.0, min(1.0, target))
    a_up = _alpha(cfg.attack_ms, cfg.chunk_ms)
    a_down = _alpha(cfg.release_ms, cfg.chunk_ms)
    alpha = a_up if target > prev else a_down
    return prev + alpha * (target - prev)


def run_envelope(cfg: EnvelopeCfg, wav_path: str, stop: threading.Event | None = None) -> None:
    """RMS -> attack/release envelope -> jaw."""
    _run_chunked(cfg, wav_path, _envelope_target, stop or threading.Event())


def _gate_target(_prev: float, rms: float, cfg: GateCfg) -> float:
    return cfg.open_pct if rms > cfg.gate_threshold else 0.0


def run_gate(cfg: GateCfg, wav_path: str, stop: threading.Event | None = None) -> None:
    """RMS threshold -> binary open/close."""
    _run_chunked(cfg, wav_path, _gate_target, stop or threading.Event())
