"""本地 ASR 模型下载：优先走 gh-proxy 加速，下载后用官方 sha256 校验。"""

import hashlib
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from .i18n import t

RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
MIRRORS = ["https://gh-proxy.com/" + RELEASE, RELEASE]


@dataclass(frozen=True)
class ModelSpec:
    dirname: str
    sha256: str
    size_mb: int

    @property
    def archive(self) -> str:
        return self.dirname + ".tar.bz2"


MODELS = {
    "sensevoice": ModelSpec(
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63",
        166,
    ),
    "qwen3_asr": ModelSpec(
        "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        "393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96",
        879,
    ),
}


def model_path(models_dir: Path, name: str) -> Path:
    return models_dir / MODELS[name].dirname


def is_downloaded(models_dir: Path, name: str) -> bool:
    return (models_dir / MODELS[name].dirname).is_dir()


def download(
    name: str, models_dir: Path, progress: Callable[[float | None, str], None] | None = None
) -> Path:
    """下载并解压模型。progress(比例或 None, 说明) 用于界面进度条；不传时打印到终端。"""
    def report(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)
        else:
            print(msg, flush=True)

    spec = MODELS[name]
    target = models_dir / spec.dirname
    if target.exists():
        report(1.0, t("models.exists", path=target))
        return target
    models_dir.mkdir(parents=True, exist_ok=True)
    archive = models_dir / spec.archive

    for base in MIRRORS:
        url = f"{base}/{spec.archive}"
        report(0.0, t("models.downloading_from", url=url, size=spec.size_mb))
        sha = hashlib.sha256()
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done = 0
                with archive.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        sha.update(chunk)
                        done += len(chunk)
                        if total and progress:
                            progress(done / total * 0.95, t("models.progress", done=done >> 20, total=total >> 20))
                        elif total:
                            print(f"\r  {done / total:6.1%}", end="", flush=True)
            if not progress:
                print()
        except httpx.HTTPError as e:
            report(None, t("models.source_failed", error=e))
            continue
        if sha.hexdigest() != spec.sha256:
            report(None, t("models.checksum_failed"))
            archive.unlink(missing_ok=True)
            continue
        report(0.96, t("models.extracting"))
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(models_dir, filter="data")
        archive.unlink()
        report(1.0, t("models.done", path=target))
        return target

    raise RuntimeError(t("models.failed", name=name))
