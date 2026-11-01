"""命令行入口。

    umless                      启动托盘常驻（默认；已在运行时打开主窗口）
    umless ui [--page report]   打开主窗口（概览 / 报告 / 练习 / 历史 / 设置）
    umless transcribe a.wav     对音频文件跑一遍识别 + 润色（不写入数据库）
    umless download [模型名]    下载本地 ASR 模型（sensevoice / qwen3_asr）
    umless doctor               检查配置、模型、麦克风与 LLM 连通性
    umless history [-n 10]      查看最近的记录
"""

import argparse
import logging
import os
import sys

from . import config


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(config.data_dir() / "umless.log", encoding="utf-8")
    ]
    if sys.stdout:  # pythonw 启动时没有控制台
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # 设环境变量 UMLESS_DEBUG=1 时 Umless 自身输出 debug 日志（含识别原文与润色结果，仅供本地排查）
    if os.environ.get("UMLESS_DEBUG"):
        logging.getLogger("umless").setLevel(logging.DEBUG)


def cmd_run(cfg: config.Config, _args) -> None:
    from . import system

    if not system.acquire_single_instance(system.CORE_MUTEX):
        system.spawn("ui")  # 已在后台运行：再次启动等于打开主窗口
        return
    from .app import App

    App(cfg).run()


def cmd_ui(_cfg: config.Config, args) -> None:
    from .window import run

    run(args.page)


def _load_audio(path: str):
    """读取音频为单声道 float32；soundfile 读不了的（如尾部损坏的 mp3）交给 ffmpeg。"""
    import shutil
    import subprocess

    import numpy as np
    import soundfile as sf

    try:
        samples, sr = sf.read(path, dtype="float32", always_2d=True)
        return samples.mean(axis=1), sr
    except Exception:
        if not shutil.which("ffmpeg"):
            raise
    pcm = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.float32), 16000


def cmd_transcribe(cfg: config.Config, args) -> None:
    from .app import Pipeline

    if args.no_llm:
        cfg.llm.enabled = False
    pipeline = Pipeline(cfg)
    for path in args.files:
        samples, sr = _load_audio(path)
        res = pipeline.process(samples, sr)
        print(f"=== {path}（{len(samples) / sr:.1f}s）")
        print(f"[逐字稿 {res.asr_ms}ms] {res.raw_text}")
        if res.polished_text or res.error:
            print(f"[润色 {res.llm_ms}ms] {res.polished_text or res.error}")
        print()


def cmd_download(cfg: config.Config, args) -> None:
    from .models import MODELS, download

    for name in args.models or [cfg.asr.engine if cfg.asr.engine in MODELS else "sensevoice"]:
        download(name, cfg.asr.models_path)


def cmd_doctor(cfg: config.Config, _args) -> None:
    from . import __version__, system
    from .models import MODELS, model_path

    mode = {"installed": "安装版", "portable": "便携版", "source": "源码运行"}[system.install_mode()]
    print(f"Umless {__version__}（{mode}）：{system.app_dir()}")
    print(f"配置文件：{config.config_path()}")
    print(f"数据目录：{config.data_dir()}")
    print(f"ASR 引擎：{cfg.asr.engine}")
    if cfg.asr.engine in MODELS:
        try:
            import sherpa_onnx

            print(f"  sherpa-onnx：{getattr(sherpa_onnx, '__version__', '?')} ✓")
        except Exception as e:  # 缺少 onnxruntime.dll 等
            print(f"  sherpa-onnx：✗ {e}")
        p = model_path(cfg.asr.models_path, cfg.asr.engine)
        print(f"  模型：{p} {'✓' if p.exists() else '✗ 未下载，运行 umless download'}")

    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        print(f"麦克风：{dev['name']}（{int(dev['default_samplerate'])} Hz）✓")
    except Exception as e:
        print(f"麦克风：✗ {e}")

    try:  # 只加载主窗口组件（pythonnet → .NET WinForms → WebView2），不创建窗口
        from webview.platforms import winforms

        ok = winforms.renderer == "edgechromium"
        print(f"主窗口：{winforms.renderer} {'✓' if ok else '✗ 未检测到 WebView2 运行时'}")
    except Exception as e:
        print(f"主窗口：✗ {e}")

    if not cfg.llm.enabled:
        print("LLM：已关闭")
        return
    print(f"LLM：{cfg.llm.base_url}  模型 {cfg.llm.model}")
    from .llm import Polisher

    try:
        sample = "嗯那个就是我明天哦不对后天想去一下那个GIT HUB上看看"
        print(f"  测试输入：{sample}")
        print(f"  润色结果：{Polisher(cfg.llm, cfg.glossary).polish(sample)} ✓")
    except Exception as e:
        print(f"  ✗ {e}")


def cmd_history(_cfg: config.Config, args) -> None:
    from .db import DB

    for r in reversed(DB(config.data_dir() / "umless.db").recent(args.n)):
        print(f"#{r['id']} {r['created_at']} {r['duration_s']}s [{r['app']} · {r['scene']}/{r['intensity']}]")
        print(f"  原文：{r['raw_text']}")
        print(f"  输出：{r['output_text']}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream:  # pythonw 启动时为 None
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="umless", description="会帮你改掉口癖的语音输入工具")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="启动托盘常驻（默认）")
    p = sub.add_parser("ui", help="打开主窗口")
    p.add_argument("--page", help="打开后跳转的页面，如 report / practice / settings / onboarding")
    p = sub.add_parser("transcribe", help="对音频文件跑识别 + 润色")
    p.add_argument("files", nargs="+")
    p.add_argument("--no-llm", action="store_true", help="只做识别，不润色")
    p = sub.add_parser("download", help="下载本地 ASR 模型")
    p.add_argument("models", nargs="*", choices=["sensevoice", "qwen3_asr", []])
    sub.add_parser("doctor", help="检查环境")
    p = sub.add_parser("history", help="查看最近记录")
    p.add_argument("-n", type=int, default=10)
    args = parser.parse_args()

    _setup_logging()
    cfg = config.load()
    from . import i18n

    i18n.set_language(cfg.app.language)  # 下载进度等后端文字跟随界面语言
    handler = {
        None: cmd_run,
        "run": cmd_run,
        "ui": cmd_ui,
        "transcribe": cmd_transcribe,
        "download": cmd_download,
        "doctor": cmd_doctor,
        "history": cmd_history,
    }[args.cmd]
    handler(cfg, args)


if __name__ == "__main__":
    main()
