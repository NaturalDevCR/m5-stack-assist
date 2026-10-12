import struct
import wave

import pytest

import audio_utils as au


def pcm_const(value: int, n: int) -> bytes:
    return struct.pack(f"<{n}h", *([value] * n))


def test_chunk_size_is_sample_aligned():
    assert au.chunk_size_bytes(16000, 20) == 640
    assert au.chunk_size_bytes(16000, 100) == 3200
    assert au.chunk_size_bytes(22050, 10) == 220 * 2
    with pytest.raises(ValueError):
        au.chunk_size_bytes(16000, 0)


def test_iter_chunks_pads_last_chunk():
    pcm = pcm_const(1000, 500)  # 500 muestras
    chunks = list(au.iter_chunks(pcm, 16000, 20))  # 320 muestras por trama
    assert [len(c) for c in chunks] == [640, 640]
    assert chunks[1][360:] == bytes(280)


def test_wav_roundtrip(tmp_path):
    pcm = pcm_const(-1234, 1600)
    path = tmp_path / "x.wav"
    au.write_wav_pcm16_mono(path, pcm, 16000)
    assert au.read_wav_pcm16_mono(path, 16000) == pcm


def test_wav_rejects_wrong_rate_and_stereo(tmp_path):
    path = tmp_path / "bad.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(bytes(400))
    with pytest.raises(au.AudioFormatError) as exc:
        au.read_wav_pcm16_mono(path, 16000)
    msg = str(exc.value)
    assert "2 canales" in msg and "44100 Hz" in msg and "afconvert" in msg


def test_rms_dbfs():
    assert au.rms_dbfs(bytes(640)) == float("-inf")
    assert au.rms_dbfs(pcm_const(32767, 320)) == pytest.approx(0.0, abs=0.01)
    assert au.rms_dbfs(pcm_const(3277, 320)) == pytest.approx(-20.0, abs=0.05)


def test_speech_end_ignores_trailing_silence():
    voice = pcm_const(8000, 16000 // 2)  # 0,5 s
    pcm = voice + bytes(16000)  # + 0,5 s de silencio
    assert au.speech_end_s(pcm, 16000) == pytest.approx(0.5, abs=0.02)
    assert au.duration_s(pcm, 16000) == pytest.approx(1.0)


def test_parse_pcm_format():
    assert au.parse_pcm_format("pcm_16000") == 16000
    assert au.parse_pcm_format("pcm_24000") == 24000
    for bad in ("ulaw_8000", "pcm_x", None):
        with pytest.raises(au.AudioFormatError):
            au.parse_pcm_format(bad)
