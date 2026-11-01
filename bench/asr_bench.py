"""Umless ASR 选型对比脚本。

对 data/wav/*.wav 逐条转写，记录：转写文本、耗时、RTF、进程内存峰值。
每个模型在独立子进程中运行，避免内存统计互相干扰。

用法：
    uv run python asr_bench.py               # 跑所有已下载的模型
    uv run python asr_bench.py sensevoice    # 只跑指定模型
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import sherpa_onnx
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
MODELS = ROOT / "models"
WAV_DIR = ROOT / "data" / "wav"
OUT_DIR = ROOT / "results" / "raw"
NUM_THREADS = 4


def _sensevoice(use_itn: bool):
    d = MODELS / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
    return d, lambda: sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(d / "model.int8.onnx"),
        tokens=str(d / "tokens.txt"),
        num_threads=NUM_THREADS,
        language="zh",
        use_itn=use_itn,
    )


def _paraformer():
    d = MODELS / "sherpa-onnx-paraformer-zh-int8-2025-10-07"
    return d, lambda: sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(d / "model.int8.onnx"),
        tokens=str(d / "tokens.txt"),
        num_threads=NUM_THREADS,
    )


def _qwen3_asr():
    d = MODELS / "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
    return d, lambda: sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
        conv_frontend=str(d / "conv_frontend.onnx"),
        encoder=str(d / "encoder.int8.onnx"),
        decoder=str(d / "decoder.int8.onnx"),
        tokenizer=str(d / "tokenizer"),
        num_threads=NUM_THREADS,
        max_total_len=1536,
        max_new_tokens=768,
    )


def _funasr_nano(itn: bool):
    d = MODELS / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    return d, lambda: sherpa_onnx.OfflineRecognizer.from_funasr_nano(
        encoder_adaptor=str(d / "encoder_adaptor.int8.onnx"),
        llm=str(d / "llm.int8.onnx"),
        embedding=str(d / "embedding.int8.onnx"),
        tokenizer=str(d / "Qwen3-0.6B"),
        num_threads=NUM_THREADS,
        language="中文",
        itn=itn,
        max_new_tokens=768,
    )


# 名称 -> 构造函数；带 itn 的变体用于观察"规整化"是否会吃掉语气词
REGISTRY = {
    "sensevoice": lambda: _sensevoice(use_itn=False),
    "sensevoice_itn": lambda: _sensevoice(use_itn=True),
    "paraformer": _paraformer,
    "qwen3_asr": _qwen3_asr,
    "funasr_nano": lambda: _funasr_nano(itn=False),
    "funasr_nano_itn": lambda: _funasr_nano(itn=True),
}


def run_one(name: str) -> None:
    model_dir, build = REGISTRY[name]()
    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    t0 = time.perf_counter()
    recognizer = build()
    load_s = time.perf_counter() - t0
    rss_loaded = proc.memory_info().rss

    rows = []
    for wav in sorted(WAV_DIR.glob("*.wav")):
        samples, sr = sf.read(wav, dtype="float32")
        duration = len(samples) / sr
        stream = recognizer.create_stream()
        stream.accept_waveform(sr, samples)
        t0 = time.perf_counter()
        recognizer.decode_stream(stream)
        elapsed = time.perf_counter() - t0
        text = stream.result.text.strip()
        rows.append(
            {
                "id": wav.stem,
                "duration": round(duration, 2),
                "elapsed": round(elapsed, 3),
                "rtf": round(elapsed / duration, 4),
                "text": text,
            }
        )
        print(f"[{name}] {wav.stem} {elapsed:.2f}s  {text[:40]}", flush=True)

    mem = proc.memory_info()
    summary = {
        "model": name,
        "model_dir": model_dir.name,
        "num_threads": NUM_THREADS,
        "load_s": round(load_s, 2),
        "model_mem_mb": round((rss_loaded - rss_before) / 2**20),
        # peak_wset 仅 Windows 提供，即进程工作集峰值
        "peak_mem_mb": round(getattr(mem, "peak_wset", mem.rss) / 2**20),
        "total_audio_s": round(sum(r["duration"] for r in rows), 1),
        "total_elapsed_s": round(sum(r["elapsed"] for r in rows), 2),
        "rows": rows,
    }
    summary["avg_rtf"] = round(summary["total_elapsed_s"] / summary["total_audio_s"], 4)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{name}] done: load {summary['load_s']}s, RTF {summary['avg_rtf']}, "
        f"peak mem {summary['peak_mem_mb']} MB",
        flush=True,
    )


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        run_one(sys.argv[2])
        return

    names = sys.argv[1:] or list(REGISTRY)
    for name in names:
        model_dir, _ = REGISTRY[name]()
        if not model_dir.exists():
            print(f"[skip] {name}: 模型未下载 ({model_dir.name})")
            continue
        subprocess.run([sys.executable, __file__, "--child", name], check=False)


if __name__ == "__main__":
    main()
