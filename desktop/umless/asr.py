"""ASR 引擎：本地 sherpa-onnx（SenseVoice / Qwen3-ASR）与云端 OpenAI 兼容接口。

所有引擎都输出"逐字稿"：保留嗯、啊、然后等口癖，供后续表达分析使用。
SenseVoice 输出的英文是全大写，识别后立即按 spec/asr.md 修正大小写（textfix.fix_case），历史里的原文也是修正后的。
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

import httpx
import numpy as np

from .audio import to_wav_bytes
from .config import ASRConfig
from .i18n import t
from .models import model_path
from .textfix import fix_case

log = logging.getLogger(__name__)


class ASREngine(Protocol):
    name: str

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str: ...


Glossary = Callable[[], list[str]]  # 每次识别时取当前术语表（配置可热加载）


def _no_glossary() -> list[str]:
    return []


def _post(name: str, text: str, glossary: Glossary) -> str:
    """SenseVoice（本地或云端同名模型）输出的英文全大写，修正大小写；其他引擎原样返回。"""
    return fix_case(text, glossary()) if "sensevoice" in name.lower() else text


class SherpaEngine:
    """sherpa-onnx 离线识别；输入任意采样率，由 sherpa-onnx 内部重采样。"""

    def __init__(self, name: str, recognizer, glossary: Glossary = _no_glossary) -> None:
        self.name = name
        self._recognizer = recognizer
        self._glossary = glossary

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._recognizer.decode_stream(stream)
        return _post(self.name, stream.result.text.strip(), self._glossary)


class OpenAIEngine:
    """OpenAI 兼容 /audio/transcriptions（OpenAI、Groq、硅基流动、自部署 FunASR 等）。"""

    def __init__(self, cfg: ASRConfig, glossary: Glossary = _no_glossary) -> None:
        self.name = f"openai:{cfg.openai.model}"
        self._cfg = cfg.openai
        self._glossary = glossary

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        r = httpx.post(
            self._cfg.base_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._cfg.api_key}"},
            files={"file": ("audio.wav", to_wav_bytes(samples, sample_rate), "audio/wav")},
            data={"model": self._cfg.model},
            timeout=60,
        )
        r.raise_for_status()
        return _post(self.name, r.json().get("text", "").strip(), self._glossary)


def create(cfg: ASRConfig, glossary: Glossary = _no_glossary) -> ASREngine:
    """glossary：返回当前术语表的函数，用于恢复术语的大小写（只对 SenseVoice 生效）。"""
    if cfg.engine == "openai":
        return OpenAIEngine(cfg, glossary)

    import sherpa_onnx  # 延迟导入：只用云端时不必加载 onnxruntime

    if cfg.engine not in ("sensevoice", "qwen3_asr"):
        raise ValueError(t("asr.unknown_engine", engine=cfg.engine))
    d = model_path(cfg.models_path, cfg.engine)
    if not d.exists():
        raise FileNotFoundError(t("asr.model_missing", engine=cfg.engine, path=d))

    t0 = time.perf_counter()
    if cfg.engine == "sensevoice":
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=cfg.num_threads,
            language="zh",
            use_itn=True,
        )
    else:
        recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(d / "conv_frontend.onnx"),
            encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.int8.onnx"),
            tokenizer=str(d / "tokenizer"),
            num_threads=cfg.num_threads,
            max_total_len=1536,
            max_new_tokens=768,
        )
    log.info("ASR %s 加载完成，用时 %.1fs", cfg.engine, time.perf_counter() - t0)
    return SherpaEngine(cfg.engine, recognizer, glossary)
