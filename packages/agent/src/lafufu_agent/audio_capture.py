"""Process-wide PyAudio singleton + flexible mic device selection."""

import atexit
import contextlib
import logging
import os

import pyaudio

log = logging.getLogger(__name__)

_DEFAULT_PREFER = ("shure", "jabra")
_DEFAULT_AVOID = ("soundbar", "monitor of", "output", "playback")

_pa_singleton: "pyaudio.PyAudio | None" = None
_selector_logged: bool = False

# Operator-set mic device from the DB snapshot (agent.input_device). Updated
# at runtime by AgentService when config.changed.agent.input_device fires.
# The literal "auto" means "fall through to the existing PREFER -> PyAudio
# default chain" — same behavior as not having set anything.
_db_input_device: str = "auto"


def set_db_input_device(value: str) -> None:
    """Set the DB-snapshot mic device. Called by AgentService on
    config.changed.agent.input_device."""
    global _db_input_device
    _db_input_device = (value or "auto").strip() or "auto"


def get_pyaudio() -> pyaudio.PyAudio:
    """Return a process-wide PyAudio instance, suppressing ALSA stderr on first init."""
    global _pa_singleton
    if _pa_singleton is not None:
        return _pa_singleton

    devnull = None
    old_fd = None
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_fd = os.dup(2)
        os.dup2(devnull, 2)
    except OSError:
        pass
    try:
        _pa_singleton = pyaudio.PyAudio()
    finally:
        if old_fd is not None:
            os.dup2(old_fd, 2)
            os.close(old_fd)
        if devnull is not None:
            os.close(devnull)
    return _pa_singleton


@atexit.register
def _terminate_pyaudio():
    global _pa_singleton
    if _pa_singleton is not None:
        with contextlib.suppress(Exception):
            _pa_singleton.terminate()
        _pa_singleton = None


def _env_list(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def select_input_device(p: pyaudio.PyAudio) -> int | None:
    """Pick a pyaudio input device index. None → system default.

    Selection order:
      1. ``LAFUFU_INPUT_DEVICE`` env (highest — operator override)
      2. ``agent.input_device`` DB setting (set via admin UI; "auto" skips)
      3. PREFER list match (e.g. shure / jabra on the Pi)
      4. PyAudio's reported default input device — what the OS treats as
         the user's chosen mic. On Linux/Pi this is usually whatever ALSA
         picks first; on Windows it's the device the user set in Sound
         Settings. (Used to be "first non-avoided", which on Windows
         routes through the Sound Mapper virtual device and produces
         silence to wake-word detectors.)
      5. First non-AVOID device (legacy fallback if PyAudio refuses to
         report a default — very rare).
    """
    global _selector_logged
    devices: list[tuple[int, str]] = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append((i, info.get("name", "")))

    avoid = _env_list("LAFUFU_INPUT_DEVICE_AVOID", _DEFAULT_AVOID)
    prefer = _env_list("LAFUFU_INPUT_DEVICE_PREFER", _DEFAULT_PREFER)
    forced = (os.environ.get("LAFUFU_INPUT_DEVICE") or "").strip()

    chosen: int | None = None
    reason = "system default"

    if forced:
        if forced.isdigit():
            idx = int(forced)
            if any(i == idx for i, _ in devices):
                chosen = idx
                reason = f"LAFUFU_INPUT_DEVICE={forced}"
        else:
            needle = forced.lower()
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"LAFUFU_INPUT_DEVICE~={forced!r} → {name!r}"
                    break

    if chosen is None and _db_input_device != "auto":
        db_value = _db_input_device
        if db_value.isdigit():
            idx = int(db_value)
            if any(i == idx for i, _ in devices):
                chosen = idx
                reason = f"agent.input_device={db_value}"
        else:
            needle = db_value.lower()
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"agent.input_device~={db_value!r} -> {name!r}"
                    break

    if chosen is None:
        for needle in prefer:
            for i, name in devices:
                if needle in name.lower():
                    chosen, reason = i, f"prefer={needle!r} → {name!r}"
                    break
            if chosen is not None:
                break

    if chosen is None:
        try:
            default = p.get_default_input_device_info()
            default_idx = int(default.get("index", -1))
        except (OSError, ValueError):
            default_idx = -1
        if default_idx >= 0 and any(i == default_idx for i, _ in devices):
            default_name = next(name for i, name in devices if i == default_idx)
            chosen, reason = default_idx, f"PyAudio default → {default_name!r}"

    if chosen is None:
        for i, name in devices:
            if not any(s in name.lower() for s in avoid):
                chosen, reason = i, f"first non-avoided → {name!r}"
                break

    if not _selector_logged:
        log.info("mic.selected reason=%s", reason)
        _selector_logged = True
    return chosen
