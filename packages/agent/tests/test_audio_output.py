"""Tests for ALSA output-device resolution.

The agent must auto-pick the USB (non-HDMI) playback card so audio "just works"
when the headset or the Lafufu's built-in speaker is connected — without a
hardcoded card name (which broke when the card was named "Device" not "USB").
HDMI is never auto-selected (Pi HDMI adds lag and the user's monitor has no
speakers), but the operator CAN select it explicitly.
"""

from lafufu_agent.audio_output import (
    _parse_aplay_l,
    resolve_output_device,
)

# Representative `aplay -l` output: two HDMI cards + one USB audio device.
APLAY_L = """**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 1: vc4hdmi1 [vc4-hdmi-1], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""


def test_parse_aplay_l_extracts_card_short_names():
    assert _parse_aplay_l(APLAY_L) == ["vc4hdmi0", "vc4hdmi1", "Device"]


def test_auto_picks_first_non_hdmi_card():
    # Skips both vc4hdmi cards, lands on the USB "Device" card.
    assert resolve_output_device("auto", cards=["vc4hdmi0", "vc4hdmi1", "Device"]) == (
        "plughw:CARD=Device,DEV=0"
    )


def test_auto_is_case_insensitive_and_empty_means_auto():
    for v in ("auto", "AUTO", "", None):
        assert resolve_output_device(v, cards=["vc4hdmi0", "USB"]) == "plughw:CARD=USB,DEV=0"


def test_auto_falls_back_to_default_when_only_hdmi_present():
    # Never auto-select HDMI even if it's the only thing there.
    assert resolve_output_device("auto", cards=["vc4hdmi0", "vc4hdmi1"]) == "default"


def test_explicit_bare_card_name_becomes_plughw():
    assert resolve_output_device("USB", cards=[]) == "plughw:CARD=USB,DEV=0"


def test_operator_can_explicitly_select_hdmi():
    # Manual selection of an HDMI card is honored (auto never would).
    assert resolve_output_device("vc4hdmi0", cards=["vc4hdmi0"]) == "plughw:CARD=vc4hdmi0,DEV=0"


def test_full_device_string_passes_through():
    for dev in ("plughw:CARD=USB,DEV=0", "default", "sysdefault:CARD=Device", "hw:2,0"):
        assert resolve_output_device(dev, cards=[]) == dev
