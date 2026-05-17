import struct

from lafufu_agent.vad import SilenceDetector, audio_rms


def _pcm16_buffer(samples: list[int]) -> bytes:
    """Build int16 little-endian PCM bytes."""
    return b"".join(struct.pack("<h", s) for s in samples)


def test_audio_rms_silence_is_zero():
    buf = _pcm16_buffer([0] * 100)
    assert audio_rms(buf) == 0.0


def test_audio_rms_loud_is_nonzero():
    buf = _pcm16_buffer([5000] * 100)
    assert audio_rms(buf) > 1000.0


def test_silence_detector_triggers_after_threshold_silence():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    loud = _pcm16_buffer([5000] * 100)
    silent = _pcm16_buffer([0] * 100)
    # Speak first
    assert not det.is_done(det.observe(loud))
    # Now silence
    assert not det.is_done(det.observe(silent))
    assert not det.is_done(det.observe(silent))
    assert det.is_done(det.observe(silent))


def test_silence_detector_resets_on_speech():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    silent = _pcm16_buffer([0] * 100)
    loud = _pcm16_buffer([5000] * 100)
    det.observe(silent)
    det.observe(silent)
    rms = det.observe(loud)
    # Speech should reset the silence counter
    assert det.silent_count == 0
    assert rms > 500


def test_silence_detector_started_flag():
    det = SilenceDetector(silence_threshold=500, silent_chunks_required=3)
    silent = _pcm16_buffer([0] * 100)
    det.observe(silent)
    assert not det.started
    loud = _pcm16_buffer([5000] * 100)
    det.observe(loud)
    assert det.started
