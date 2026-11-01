"""提示音测试：合成结果是合法 WAV、时长与首尾符合要求、音量曲线、自定义文件。不真的播放声音。"""

import io
import os
import time
import wave

import numpy as np
import pytest
import soundfile as sf

from umless import sounds

# 每个预设的时长上限（秒）：开始音要短，不拖慢开口
LIMITS = {"rise": 0.15, "chime": 0.45}


@pytest.fixture
def played(monkeypatch):
    """替换掉 winsound.PlaySound，记录调用而不发声。"""
    import winsound

    calls: list = []
    monkeypatch.setattr(winsound, "PlaySound", lambda data, flags: calls.append((data, flags)))
    return calls


def _read(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767
        return x, w.getframerate()


@pytest.mark.parametrize("name", sounds.PRESETS)
def test_presets_are_valid_short_wavs(name):
    data = sounds.render(name, 100)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    x, sr = _read(data)
    assert sr == 44100
    assert 0.02 < len(x) / sr <= LIMITS.get(name, 0.30)
    assert 0.3 < np.max(np.abs(x)) <= 1.0  # 不静音、不削波
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01  # 首尾淡入淡出，不爆音


def test_volume_curve_and_mute(played):
    loud, _ = _read(sounds.render("soft", 100))
    half, _ = _read(sounds.render("soft", 50))
    assert np.max(np.abs(half)) == pytest.approx(np.max(np.abs(loud)) * 0.25, rel=0.02)  # (v/100)^2
    assert sounds.render("soft", 0) is None
    assert sounds.play("soft", 0) is False
    assert sounds.play("", 80) is False  # 空串：该事件不播放
    time.sleep(0.05)
    assert played == []


def test_play_runs_in_background_with_memory_flag(played):
    import winsound

    assert sounds.play("rise", 60) is True
    for _ in range(50):
        if played:
            break
        time.sleep(0.01)
    data, flags = played[0]
    assert data[:4] == b"RIFF" and flags & winsound.SND_MEMORY and not flags & winsound.SND_ASYNC


def test_custom_wav_mono_truncated_and_reloaded(tmp_path, played):
    path = tmp_path / "ding.wav"
    stereo = np.full((22050 * 5, 2), 0.5, dtype=np.float32)  # 5 秒双声道
    sf.write(path, stereo, 22050)
    x, sr = sounds.samples(str(path))
    assert sr == 22050 and x.ndim == 1 and len(x) == 22050 * 3  # 截到 3 秒
    assert sounds.is_valid(str(path))

    sf.write(path, np.zeros(22050, dtype=np.float32), 22050)  # 替换文件：按修改时间重新读取
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert len(sounds.samples(str(path))[0]) == 22050


def test_missing_or_bad_file_is_skipped(tmp_path, played):
    missing = str(tmp_path / "nope.wav")
    assert sounds.samples(missing) is None and sounds.play(missing, 80) is False
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert sounds.play(str(bad), 80) is False
    assert not sounds.is_valid(missing) and not sounds.is_valid("ding.wav")  # 相对路径不行
    assert sounds.is_valid("") and sounds.is_valid("chime")
    time.sleep(0.05)
    assert played == []
