"""录音：以设备原生采样率录制单声道，重采样交给 ASR 引擎。

两种模式：
- 按需开麦（默认）：只在按住热键期间占用麦克风。打开约需 100–250ms，
  因此开始提示音（sounds.py）在麦克风就绪后才播放，听到提示音再开口即可。
- 常开麦克风（mic_always_on）：麦克风持续打开，但只在内存中保留最近
  PREROLL_S 秒的环形缓冲，按下热键时把这段补进录音，开口即录、不丢字。
"""

import collections
import io
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

PREROLL_S = 0.3


class Recorder:
    def __init__(self, always_on: bool = False) -> None:
        self.always_on = always_on
        self.sample_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        self._chunks: list[np.ndarray] = []
        self._preroll: collections.deque[np.ndarray] = collections.deque()
        self._preroll_len = 0
        self._recording = False
        self.levels: collections.deque[float] = collections.deque(maxlen=64)  # 最近的音量（RMS）
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        if always_on:
            self._open()

    def _open(self) -> None:
        if not self.always_on:
            # 按需开麦：每次按当前默认设备的采样率打开，插拔耳机、切换蓝牙后也能正常录音
            self.sample_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=int(self.sample_rate * 0.04),  # 40ms 一块，声波动画每块一根柱子
            callback=self._callback,
        )
        self._stream.start()

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:
        chunk = indata[:, 0].copy()
        with self._lock:
            if self._recording:
                self._chunks.append(chunk)
                self.levels.append(float(np.sqrt(np.mean(chunk * chunk))))
            elif self.always_on:
                self._preroll.append(chunk)
                self._preroll_len += len(chunk)
                limit = int(PREROLL_S * self.sample_rate)
                while self._preroll and self._preroll_len - len(self._preroll[0]) >= limit:
                    self._preroll_len -= len(self._preroll.popleft())

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._chunks = list(self._preroll)
            self.levels.clear()
            self._preroll.clear()
            self._preroll_len = 0
            self._recording = True
        if not self.always_on:
            self._open()

    def stop(self) -> np.ndarray:
        if not self.always_on:
            self._close()
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            self._recording = False
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def abort(self) -> None:
        """打开麦克风失败后复位，下一次按键可以重试。"""
        with self._lock:
            self._recording = False
            self._chunks = []
        self._stream = None

    def shutdown(self) -> None:
        self.stop()
        self._close()

    @property
    def recording(self) -> bool:
        return self._recording


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()
