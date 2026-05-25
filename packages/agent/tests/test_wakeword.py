"""Tests for wake-word gating.

Two layers:
  1. OpenWakeWordDetector — exercise feed/reset against a stubbed Model so the
     openwakeword pip dep isn't required at test time.
  2. RealMic.wait_for_onset — drive the wake-word branch with a stub detector
     and stub PyAudio stream so we never touch real audio hardware.
"""

from __future__ import annotations

import sys
import types

import pytest

# ---------- OpenWakeWordDetector unit tests ----------


def _install_fake_openwakeword(monkeypatch, score_sequence):
    """Inject a fake `openwakeword.model.Model` whose predict() returns the
    next scripted score (as `{"fake": score}`) on each call."""
    iterator = iter(score_sequence)

    class FakeModel:
        def __init__(self, wakeword_models, inference_framework):
            self.wakeword_models = wakeword_models
            self.framework = inference_framework
            self.reset_calls = 0

        def predict(self, _arr):
            try:
                return {"fake": next(iterator)}
            except StopIteration:
                return {"fake": 0.0}

        def reset(self):
            self.reset_calls += 1

    fake_pkg = types.ModuleType("openwakeword")
    fake_model_mod = types.ModuleType("openwakeword.model")
    fake_model_mod.Model = FakeModel
    fake_pkg.model = fake_model_mod
    monkeypatch.setitem(sys.modules, "openwakeword", fake_pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake_model_mod)
    return FakeModel


def test_detector_returns_max_score_from_predict(monkeypatch):
    _install_fake_openwakeword(monkeypatch, [0.1, 0.7, 0.3])
    from lafufu_agent.wakeword import OpenWakeWordDetector

    det = OpenWakeWordDetector(model_name="fake", threshold=0.5)
    # 100ms of silent int16 audio at 16kHz — actual bytes don't matter for
    # this test since the fake Model ignores them.
    silent = b"\x00\x00" * 1600
    assert det.feed(silent) == pytest.approx(0.1)
    assert det.feed(silent) == pytest.approx(0.7)
    assert det.feed(silent) == pytest.approx(0.3)


def test_detector_threshold_is_caller_decision(monkeypatch):
    """Detector exposes threshold but doesn't enforce it — caller compares."""
    _install_fake_openwakeword(monkeypatch, [0.49, 0.51])
    from lafufu_agent.wakeword import OpenWakeWordDetector

    det = OpenWakeWordDetector(model_name="fake", threshold=0.5)
    s1 = det.feed(b"\x00\x00" * 100)
    s2 = det.feed(b"\x00\x00" * 100)
    assert s1 < det.threshold
    assert s2 >= det.threshold


def test_detector_reset_calls_underlying_model(monkeypatch):
    FakeModel = _install_fake_openwakeword(monkeypatch, [0.1])
    from lafufu_agent.wakeword import OpenWakeWordDetector

    det = OpenWakeWordDetector(model_name="fake")
    det.feed(b"\x00\x00" * 100)  # forces .load()
    det.reset()
    # The reset call should propagate into the underlying model instance.
    assert isinstance(det._model, FakeModel)
    assert det._model.reset_calls == 1


def test_detector_feed_empty_returns_zero(monkeypatch):
    _install_fake_openwakeword(monkeypatch, [])
    from lafufu_agent.wakeword import OpenWakeWordDetector

    det = OpenWakeWordDetector(model_name="fake")
    assert det.feed(b"") == 0.0


def test_has_openwakeword_returns_bool():
    from lafufu_agent.wakeword import has_openwakeword

    assert isinstance(has_openwakeword(), bool)


# ---------- RealMic.wait_for_onset integration tests ----------


class _StubStream:
    """Replaces a PyAudio input stream. read() pops from a canned queue."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self._exhausted = b"\x00\x00" * 320  # ~20ms of silence as filler

    def read(self, _n, exception_on_overflow=False):
        if self._chunks:
            return self._chunks.pop(0)
        return self._exhausted

    def get_read_available(self):
        return 0

    def stop_stream(self):
        pass

    def close(self):
        pass


class _StubWakeDetector:
    """Returns scripted scores; reset() bumps a counter."""

    threshold = 0.5

    def __init__(self, scores):
        self._scores = list(scores)
        self.reset_calls = 0
        self.fed = 0

    def feed(self, _data):
        self.fed += 1
        if self._scores:
            return self._scores.pop(0)
        return 0.0

    def reset(self):
        self.reset_calls += 1


def _make_realmic_with_stub_stream(stream, *, wake_detector=None):
    """Build a RealMic that skips PyAudio init by pre-populating its private
    stream + format fields."""
    from lafufu_agent.__main__ import RealMic

    mic = RealMic(stt=object(), wake_detector=wake_detector)
    mic._stream = stream
    mic._eff_rate = 16000  # bypass resampling in the wake-word path
    mic._eff_chunk = 320  # 20ms @ 16kHz
    mic._device_index = None
    return mic


def test_wait_for_onset_fires_on_wakeword_hit():
    # First two scores below threshold, third one crosses it → return on chunk 3.
    detector = _StubWakeDetector([0.1, 0.2, 0.9])
    stream = _StubStream([b"\x11\x22" * 320] * 5)
    mic = _make_realmic_with_stub_stream(stream, wake_detector=detector)

    started, pre_roll = mic.wait_for_onset()

    assert started is True
    assert len(pre_roll) >= 1
    # Detector was fed three chunks (the threshold-crossing call is the last).
    assert detector.fed == 3
    # Reset is called so the next listen doesn't re-fire on leftover buffer.
    assert detector.reset_calls == 1


def test_wait_for_onset_no_wakeword_hit_times_out():
    # Detector never crosses threshold — RealMic should give up after MAX_WAIT_S.
    # Use a small MAX_WAIT_S override so the test stays fast.
    detector = _StubWakeDetector([0.0] * 200)
    stream = _StubStream([])  # always returns filler silence
    mic = _make_realmic_with_stub_stream(stream, wake_detector=detector)
    mic.MAX_WAIT_S = 0.2  # ~5 chunks at 40ms — but eff_chunk is 20ms here

    started, pre_roll = mic.wait_for_onset()

    assert started is False
    assert pre_roll == []


def test_wait_for_onset_falls_back_to_rms_when_no_detector():
    """No detector → original RMS-based onset path still works untouched."""
    # 5 chunks of silence then loud audio (well above silence_threshold=800).
    quiet = b"\x00\x00" * 320
    # int16 0x7FFF ≈ peak, RMS will be ~max.
    loud = b"\xff\x7f" * 320
    stream = _StubStream([quiet] * 3 + [loud] * 10)
    mic = _make_realmic_with_stub_stream(stream, wake_detector=None)

    started, pre_roll = mic.wait_for_onset()

    assert started is True
    assert len(pre_roll) >= 1
