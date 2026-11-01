"""提示音：用 numpy 合成的预设音效，或用户自选的 .wav 文件。

四个事件：start 开始录音 · stop 结束录音 · done 文字已输出 · error 出错或没听清。
每个事件的配置值为预设 id、.wav 绝对路径，或空串（不播放）。

合成要点（听感柔和、不刺耳）：
- 正弦为主，只叠加少量 2、3 次泛音，避免方波 / 锯齿那种"电子嘀"的尖锐感；
- 几毫秒的升余弦起音，按指数衰减，结尾再做一段淡出，首尾都落在 0，不会爆音；
- 开始音不超过 150ms，不拖慢开口。

播放走 winsound.PlaySound(SND_MEMORY)，经系统混音器输出，与 sounddevice 的录音输入流互不影响。
winsound 不支持 SND_MEMORY | SND_ASYNC，所以在后台线程里同步播放；同一进程内新的 PlaySound
会打断上一个，正好让"开始"音在快速松开时被"结束"音接上。
"""

import io
import logging
import threading
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import SOUND_EVENTS

log = logging.getLogger(__name__)

SR = 44100
EVENTS = SOUND_EVENTS
PRESETS = ("rise", "fall", "pop", "drop", "chime", "tick", "soft", "classic", "low")
MAX_FILE_S = 3.0  # 自定义音效最多播放 3 秒
EDGE_S = 0.004  # 首尾淡入淡出


# ================================================================ 合成


def _env(n: int, attack: float, tau: float) -> np.ndarray:
    """升余弦起音 + 指数衰减。"""
    t = np.arange(n) / SR
    env = np.exp(-t / tau)
    a = max(1, int(attack * SR))
    if a < n:
        env[:a] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a))
    return env


def _tone(freq: float, dur: float, tau: float, attack: float = 0.004,
          partials: tuple = ((2, 0.18), (3, 0.05)), glide_to: float | None = None,
          release: float = 0.015) -> np.ndarray:
    """单个音：正弦 + 少量泛音；glide_to 时音高按指数从 freq 滑到 glide_to。

    每个音结尾都有 release 秒的淡出：两音连奏时前一个音不会被硬切出"咔"声。
    """
    n = int(dur * SR)
    if glide_to:
        f = freq * (glide_to / freq) ** (np.arange(n) / n)
    else:
        f = np.full(n, float(freq))
    phase = 2 * np.pi * np.cumsum(f) / SR
    wave_ = np.sin(phase)
    for k, amp in partials:
        wave_ += amp * np.sin(k * phase)
    out = wave_ * _env(n, attack, tau)
    r = min(int(release * SR), n // 2)
    if r > 1:
        out[-r:] *= 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, r))
    return out


def _bell(freq: float, dur: float) -> np.ndarray:
    """钟声：非整数倍泛音，高泛音衰减得更快，余音由基音撑着。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for ratio, amp, tau in ((1.0, 1.0, 0.16), (2.0, 0.32, 0.10), (2.76, 0.22, 0.06), (4.07, 0.10, 0.035)):
        out += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t / tau)
    return out * _env(n, 0.002, 1e9)  # 只要起音，衰减已按泛音分别处理


def _seq(*notes: tuple[float, np.ndarray]) -> np.ndarray:
    """按起始时间（秒）把若干音叠到一条音轨上，允许重叠形成连奏。"""
    total = max(int(start * SR) + len(x) for start, x in notes)
    out = np.zeros(total)
    for start, x in notes:
        i = int(start * SR)
        out[i:i + len(x)] += x
    return out


def _finish(x: np.ndarray, peak: float) -> np.ndarray:
    """首尾淡入淡出到 0（防爆音），再归一到指定峰值。"""
    x = x.astype(np.float64)
    e = min(int(EDGE_S * SR), len(x) // 4)
    if e > 1:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, e))
        x[:e] *= ramp
        x[-e:] *= ramp[::-1]
    m = np.max(np.abs(x)) or 1.0
    return (x / m * peak).astype(np.float32)


def _synth(name: str) -> np.ndarray:
    # 峰值按听感微调：短促的高音听起来更响，给低一点；低沉的音给高一点
    if name == "rise":  # 开始：E5 → A5 两音上扬，像"准备好了"，总长约 130ms
        return _finish(_seq((0.0, _tone(659.3, 0.07, 0.05)), (0.05, _tone(880.0, 0.08, 0.045))), 0.80)
    if name == "fall":  # 结束：A5 → E5 两音下行，与开始音呼应，约 140ms
        return _finish(_seq((0.0, _tone(880.0, 0.07, 0.05)), (0.055, _tone(659.3, 0.085, 0.05))), 0.80)
    if name == "pop":  # 气泡：音高从 900Hz 快速下滑，短而圆润
        return _finish(_tone(900, 0.09, 0.03, attack=0.002, partials=((2, 0.08),), glide_to=320), 0.85)
    if name == "drop":  # 水滴：音高快速上扬的"啵"，像水滴落进水面
        return _finish(_tone(520, 0.12, 0.04, attack=0.002, partials=((2, 0.06),), glide_to=1500), 0.80)
    if name == "chime":  # 铃声：C6 钟声带泛音，余音约 450ms
        return _finish(_bell(1046.5, 0.45), 0.70)
    if name == "tick":  # 轻点：很短的木质"嗒"
        return _finish(_tone(1600, 0.035, 0.007, attack=0.0008, partials=((2.4, 0.12),)), 0.60)
    if name == "soft":  # 柔和单音：C5 纯净正弦，起音较慢、衰减平缓
        return _finish(_tone(523.3, 0.22, 0.07, attack=0.012, partials=((2, 0.06),)), 0.75)
    if name == "classic":  # 经典嘀声：接近原来 winsound.Beep(880, 60)，纯正弦
        n = int(0.06 * SR)
        return _finish(np.sin(2 * np.pi * 880 * np.arange(n) / SR), 0.45)  # 不衰减，听感偏响，峰值给低
    if name == "low":  # 出错：G4 → D4 两个低沉的音，泛音稍多，笔记本扬声器上也听得清
        low = ((2, 0.35), (3, 0.15))
        return _finish(_seq((0.0, _tone(392.0, 0.11, 0.06, partials=low)),
                            (0.12, _tone(293.7, 0.15, 0.07, partials=low))), 0.90)
    raise KeyError(name)


@lru_cache(maxsize=len(PRESETS))
def preset(name: str) -> np.ndarray:
    """预设音效的波形（float32，单声道，44.1kHz），合成一次后缓存。"""
    return _synth(name)


# ================================================================ 自定义文件


@lru_cache(maxsize=8)
def _load_file(path: str, mtime: float) -> tuple[np.ndarray, int]:
    """读取 .wav 为单声道 float32，截到 3 秒（缓存键含修改时间，文件被替换后自动重读）。"""
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    x = data.mean(axis=1)[: int(MAX_FILE_S * sr)]
    e = min(int(0.01 * sr), len(x) // 4)
    if e > 1:  # 截断处淡出，避免"咔"一声
        x[-e:] *= np.linspace(1, 0, e, dtype=np.float32)
    return x, sr


def is_valid(sound: str) -> bool:
    """配置值是否可用：空串、预设 id，或存在的 .wav 绝对路径。"""
    if sound == "" or sound in PRESETS:
        return True
    p = Path(sound)
    return p.is_absolute() and p.suffix.lower() == ".wav" and p.is_file()


def samples(sound: str) -> tuple[np.ndarray, int] | None:
    """取得音效波形；空串、文件不存在或读取失败时返回 None（记录日志）。"""
    if not sound:
        return None
    if sound in PRESETS:
        return preset(sound), SR
    p = Path(sound)
    if p.suffix.lower() != ".wav":
        log.warning("未知的提示音：%s", sound)
        return None
    try:
        return _load_file(str(p), p.stat().st_mtime)
    except FileNotFoundError:
        log.warning("提示音文件不存在：%s", sound)
    except Exception as e:  # soundfile 读取失败（格式不支持、文件损坏等）
        log.warning("读取提示音失败：%s（%s）", sound, e)
    return None


# ================================================================ 播放


def gain(volume: float) -> float:
    """0–100 → 振幅倍数。按平方曲线，滑块中段的响度变化更均匀（接近感知音量）。"""
    v = min(max(float(volume), 0.0), 100.0) / 100
    return v * v


def to_wav(x: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def render(sound: str, volume: float) -> bytes | None:
    """生成可直接播放的 WAV 字节；不需要播放（空串、音量为 0、读取失败）时返回 None。"""
    g = gain(volume)
    if g <= 0:
        return None
    got = samples(sound)
    if got is None:
        return None
    x, sr = got
    return to_wav(x * g, sr)


def _play_bytes(data: bytes) -> None:
    import winsound

    try:
        winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except Exception:
        log.exception("播放提示音失败")


def play(sound: str, volume: float) -> bool:
    """异步播放，不阻塞调用方（热键线程 / 工作线程）。返回是否真的开始播放。"""
    try:
        data = render(sound, volume)
    except Exception:
        log.exception("生成提示音失败：%s", sound)
        return False
    if data is None:
        return False
    threading.Thread(target=_play_bytes, args=(data,), name="sound", daemon=True).start()
    return True
