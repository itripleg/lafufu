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
    aplay_file_popen,
    aplay_popen,
    chunk_rms,
    iter_chunks,
    open_pct_to_dxl,
    open_wav,
    percentile_sorted,
    rms_int16,
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


@dataclass
class MonolithCfg:
    """A faithful port of the legacy monolith lipsync (dynamixel.py:1838-1955).

    Differences from Envelope mode:
    - **Content-adaptive** RMS normalisation: per-WAV p_low/p_high percentile
      floor and ceiling, so a quiet WAV still opens the mouth as wide as a
      loud one.
    - **Deadzone**: ``x <= deadzone`` collapses to target=0; cleans up tiny
      RMS noise during pauses so the mouth doesn't flutter.
    - **Gamma**: ``target = target ** gamma`` (<1.0) — perceptual loudness
      curve, gives more open-mouth at lower volumes.
    - **File-mode aplay**: ``aplay file.wav`` runs to completion; the motor
      loop paces against its own wall clock relative to t0, not against an
      stdin-write cadence.
    - **FPS-driven chunking**: chunk_ms = 1000/fps (legacy default fps=20
      -> 50 ms chunks).

    Knob defaults match the legacy monolith values.
    """

    fps: int = 20
    deadzone: float = 0.05
    gamma: float = 0.70
    p_low: float = 0.10
    p_high: float = 0.95
    attack_ms: int = 30
    release_ms: int = 80
    alsa_device: str = "default"


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
    try:
        proc = aplay_popen(info.sample_rate, buffer_frames, period_frames, cfg.alsa_device)
    except BaseException:
        # If aplay can't be spawned, the bus has torque enabled with no further
        # writes — torque off and re-raise so we don't leave the jaw energised.
        bus.close()
        raise

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
        # Park briefly so the operator sees a clean stop; honour stop so the
        # park doesn't block a follow-up Run after the user clicks Stop.
        stop.wait(timeout=0.3)
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


def run_monolith(cfg: MonolithCfg, wav_path: str, stop: threading.Event | None = None) -> None:
    """Faithful port of the legacy monolith lipsync (dynamixel.py:1838-1955).

    Pre-pass:
      1. Read the whole WAV into chunks of ``framerate / fps`` frames each.
      2. Compute raw int16 RMS for every chunk.
      3. Sort the RMS values; take floor = percentile(p_low), ceil = percentile(p_high).

    Playback:
      1. Spawn ``aplay file.wav`` (file mode — no stdin streaming).
      2. Tick the motor loop by wall clock against ``t0`` (the moment aplay
         was spawned), so motor + audio drift together rather than against
         each other when the system is loaded.
      3. For each chunk:
         - x = clamp01((rms - floor) / (ceil - floor))
         - if x <= deadzone: target = 0  else: target = (x - deadzone)/(1-deadzone)
         - target = clamp01(target) ** gamma
         - asymmetric one-pole envelope: attack_ms when rising, release_ms when falling
         - jaw position from envelope in [0..1]
    """
    stop = stop or threading.Event()
    reader, info = open_wav(wav_path)
    try:
        chunk_frames = max(1, info.sample_rate // max(1, cfg.fps))
        chunks = list(iter_chunks(reader, chunk_frames))
    finally:
        reader.close()

    if not chunks:
        return

    # Pre-pass: percentile floor/ceil over RAW int16 RMS (matches legacy).
    rms_vals = [rms_int16(c) for c in chunks]
    vals_sorted = sorted(rms_vals)
    floor = percentile_sorted(vals_sorted, cfg.p_low)
    ceil = percentile_sorted(vals_sorted, cfg.p_high)
    denom = max(1e-6, ceil - floor)

    dt = 1.0 / max(1, cfg.fps)
    attack_coeff = 1.0 - math.exp(-dt / max(1e-6, cfg.attack_ms / 1000.0))
    release_coeff = 1.0 - math.exp(-dt / max(1e-6, cfg.release_ms / 1000.0))

    bus = JawBus.open()
    # t0 BEFORE Popen so the wall-clock baseline is the moment we asked aplay
    # to start (not the moment fork+exec returned ~30ms later on a loaded Pi).
    t0 = time.monotonic()
    try:
        proc = aplay_file_popen(wav_path, cfg.alsa_device)
    except BaseException:
        bus.close()
        raise
    env = 0.0
    try:
        for i, rms in enumerate(rms_vals):
            if stop.is_set():
                break
            # Wall-clock pacing: catch up to where playback should be.
            target_t = t0 + (i + 1) * dt
            now = time.monotonic()
            if target_t > now and stop.wait(timeout=target_t - now):
                break

            x = (rms - floor) / denom
            x = max(0.0, min(1.0, x))
            if x <= cfg.deadzone:
                target = 0.0
            else:
                target = (x - cfg.deadzone) / max(1e-6, 1.0 - cfg.deadzone)
            target = max(0.0, min(1.0, target))
            target = target ** max(1e-6, cfg.gamma)

            coeff = attack_coeff if target > env else release_coeff
            env = env + (target - env) * coeff
            bus.write_goal(open_pct_to_dxl(env))

        bus.write_goal(open_pct_to_dxl(0.0))
    finally:
        # Terminate FIRST so file-mode aplay (which plays the whole WAV and
        # ignores stdin) exits promptly; THEN reap. Calling wait() before
        # terminate() would block the full timeout on every Stop click.
        with contextlib.suppress(Exception):
            proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=3)
        bus.close()
