"""Unit tests for the mic device selector.

The selector previously fell back to "first non-AVOID" when no PREFER pattern
matched. On Windows that often picked the Sound Mapper virtual device, which
fed silence into the wake-word detector and made trigger mode appear dead.

Selector now prefers PyAudio's own ``get_default_input_device_info()`` index
when no PREFER hits — that's whatever the OS reports as the user's chosen
mic (Sound Settings on Windows, ALSA default on Linux/Pi).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from lafufu_agent import audio_capture


@pytest.fixture(autouse=True)
def reset_selector_log_flag():
    """Clear the once-only log flag so every test sees a fresh log line."""
    audio_capture._selector_logged = False
    yield
    audio_capture._selector_logged = False


def _fake_pyaudio(devices: list[dict], default_idx: int | None = None) -> MagicMock:
    """Build a MagicMock that quacks like ``pyaudio.PyAudio`` for the selector.

    ``devices`` is a list of ``get_device_info_by_index`` dicts. ``default_idx``
    is what ``get_default_input_device_info()`` returns (None → raises).
    """
    p = MagicMock()
    p.get_device_count.return_value = len(devices)
    p.get_device_info_by_index.side_effect = lambda i: devices[i]
    if default_idx is None:
        p.get_default_input_device_info.side_effect = OSError("no default")
    else:
        p.get_default_input_device_info.return_value = devices[default_idx]
    return p


def test_falls_back_to_pyaudio_default_when_no_prefer_match(monkeypatch):
    """When no PREFER pattern matches, picks PyAudio's reported default."""
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_PREFER", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_AVOID", raising=False)

    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "Microsoft Sound Mapper - Input"},
        {"index": 1, "maxInputChannels": 4, "name": "Microphone Array (Realtek)"},
        {"index": 2, "maxInputChannels": 2, "name": "BT LE Headphones"},
    ]
    p = _fake_pyaudio(devices, default_idx=1)

    chosen = audio_capture.select_input_device(p)

    assert chosen == 1, (
        f"expected PyAudio default (idx 1) — not 'first non-avoided' (idx 0). got {chosen}"
    )


def test_prefer_match_still_wins_over_default(monkeypatch):
    """A PREFER hit (e.g. Pi's Shure mic) outranks the PyAudio default."""
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    monkeypatch.setenv("LAFUFU_INPUT_DEVICE_PREFER", "shure")
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_AVOID", raising=False)

    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "default ALSA device"},
        {"index": 1, "maxInputChannels": 1, "name": "Shure SM7B"},
    ]
    p = _fake_pyaudio(devices, default_idx=0)

    chosen = audio_capture.select_input_device(p)
    assert chosen == 1


def test_forced_env_var_still_wins(monkeypatch):
    """LAFUFU_INPUT_DEVICE remains the highest-priority override."""
    monkeypatch.setenv("LAFUFU_INPUT_DEVICE", "2")
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_PREFER", raising=False)

    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "Mapper"},
        {"index": 1, "maxInputChannels": 1, "name": "Realtek"},
        {"index": 2, "maxInputChannels": 1, "name": "USB Mic"},
    ]
    p = _fake_pyaudio(devices, default_idx=1)
    assert audio_capture.select_input_device(p) == 2


def test_falls_back_to_first_non_avoided_when_pyaudio_has_no_default(monkeypatch):
    """If PyAudio can't report a default, fall back to legacy 'first non-avoided'."""
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_PREFER", raising=False)
    monkeypatch.delenv("LAFUFU_INPUT_DEVICE_AVOID", raising=False)

    devices = [
        {"index": 0, "maxInputChannels": 2, "name": "monitor of speaker"},  # AVOID
        {"index": 1, "maxInputChannels": 1, "name": "real mic"},
    ]
    p = _fake_pyaudio(devices, default_idx=None)  # raises OSError

    assert audio_capture.select_input_device(p) == 1
