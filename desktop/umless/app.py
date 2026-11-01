"""常驻进程：按住热键录音 → ASR 逐字稿 → 场景识别 → LLM 润色 → 输出到光标处 → 规则指标 → 本地库。

另外负责：配置热加载、每日自动诊断、过期记录清理、托盘菜单（打开主窗口）、
提示音、写给主窗口看的心跳（kv core_state）与最近一次输出（kv last_output）、
定时检查更新与托盘通知、安装更新前按主窗口的请求（kv core_quit）退出。
"""

import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import soundfile as sf

from . import asr, audio, coach, i18n, output, sounds, system, updater
from .config import Config, config_path, data_dir, load, save_patch
from .db import DB, Utterance, now_iso
from .i18n import t
from .llm import Polisher

log = logging.getLogger(__name__)

MAINTENANCE_EVERY_S = 600  # 自动诊断、清理的检查间隔
HEARTBEAT_S = 2  # 心跳间隔；主窗口超过 10 秒没看到更新即视为未就绪


@dataclass
class Result:
    raw_text: str
    polished_text: str | None
    output_text: str
    asr_ms: int
    llm_ms: int | None
    error: str | None


class Pipeline:
    """识别 + 润色，热键模式与 `umless transcribe` 共用。"""

    def __init__(self, cfg: Config, engine: asr.ASREngine | None = None) -> None:
        self.cfg = cfg
        self.engine = engine or asr.create(cfg.asr, lambda: cfg.glossary)
        self.polisher = Polisher(cfg.llm, cfg.glossary) if cfg.llm.enabled else None

    def process(
        self,
        samples: np.ndarray,
        sample_rate: int,
        on_stage: Callable[[str], None] | None = None,
        scene: str = "default",
        intensity: str | None = None,
    ) -> Result:
        """on_stage 在进入润色阶段时以 "polishing" 回调，用于更新界面。"""
        t0 = time.perf_counter()
        raw = self.engine.transcribe(samples, sample_rate)
        asr_ms = int((time.perf_counter() - t0) * 1000)

        polished, llm_ms, error = None, None, None
        if raw and self.polisher:
            if on_stage:
                on_stage("polishing")
            t0 = time.perf_counter()
            try:
                polished = self.polisher.polish(raw, scene, intensity or self.cfg.polish.intensity) or None
            except Exception as e:  # 润色失败时退回原文，保证输入不中断
                error = f"LLM 润色失败：{e}"
                log.warning(error)
            llm_ms = int((time.perf_counter() - t0) * 1000)
        return Result(raw, polished, polished or raw, asr_ms, llm_ms, error)


def _asr_key(cfg: Config) -> tuple:
    a = cfg.asr
    return (a.engine, str(a.models_path), a.num_threads, a.openai.base_url, a.openai.api_key, a.openai.model)


class App:
    def __init__(self, cfg: Config) -> None:
        from .overlay import Overlay, enable_dpi_awareness
        from .tray import Tray

        enable_dpi_awareness()  # 必须在创建任何窗口（托盘、提示条）之前
        self.cfg = cfg
        i18n.set_language(cfg.app.language)  # 托盘菜单、提示条文字跟随界面语言
        self.home = data_dir()
        self.db = DB(self.home / "umless.db")
        self.recorder = audio.Recorder(always_on=cfg.audio.mic_always_on)
        self.pipeline: Pipeline | None = None
        self._loading = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._down_at = 0.0
        self._down_app: str | None = None
        self._down_own = False  # 按下热键时前台是不是 Umless 自己的主窗口
        self._cfg_mtime = self._mtime()
        self._started = time.time()  # 只响应本进程启动之后写入的退出请求

        self.tray = Tray(
            open_page=self.open_ui,
            is_paused=lambda: not self.cfg.privacy.record_history,
            toggle_pause=self._toggle_pause,
            data_dir=str(self.home),
            log_file=str(self.home / "umless.log"),
            on_quit=self.shutdown,
        )
        self.hotkey = None
        self._overlay_cls = Overlay
        self.overlay = Overlay(lambda: list(self.recorder.levels)) if cfg.ui.overlay else None

    def _show(self, state: str, text: str = "") -> None:
        if self.overlay:
            self.overlay.show(state, text)

    def _sound(self, event: str) -> None:
        """播放某个事件的提示音：受 audio.beep 总开关控制，事件值为空串则不播放。"""
        a = self.cfg.audio
        if a.beep:
            sounds.play(a.sounds.get(event, ""), a.volume)

    def _ready_text(self) -> str:
        from .hotkey import label

        key = self.hotkey.key.spec if self.hotkey else self.cfg.hotkey.key
        return t("tray.ready", hotkey=label(key, i18n.current()))

    # ---- 主窗口 ----

    def open_ui(self, page: str | None = None) -> None:
        system.spawn("ui", *(["--page", page] if page else []))

    def _toggle_pause(self) -> None:
        self.cfg = save_patch({"privacy": {"record_history": not self.cfg.privacy.record_history}})
        self._cfg_mtime = self._mtime()
        paused = not self.cfg.privacy.record_history
        self.tray.notify(t("tray.paused") if paused else t("tray.resumed"))
        self.tray.refresh_menu()

    # ---- 热键回调（在热键分发线程中执行） ----

    def _on_down(self) -> None:
        if self.pipeline is None:
            self._show("info", t("overlay.loading"))
            self._sound("error")
            return
        self._show("recording")  # 先给出界面反馈，再打开麦克风
        self._down_at = time.perf_counter()
        self._down_app = output.foreground_app()
        self._down_own = output.foreground_is_umless()
        try:
            self.recorder.start()
        except Exception:  # 麦克风被拔掉、被占用或没有权限：提示出来，而不是一直停在"正在聆听"
            log.exception("打开麦克风失败")
            self.recorder.abort()
            self._show("error", t("overlay.mic_failed"))
            self._sound("error")
            return
        self._sound("start")  # 麦克风就绪后才播放：听到提示音再开口，不会丢开头
        self.tray.set_state("recording")

    def _on_up(self) -> None:
        if not self.recorder.recording:
            return
        held_ms = (time.perf_counter() - self._down_at) * 1000
        samples = self.recorder.stop()
        self._sound("stop")
        if held_ms < self.cfg.hotkey.min_hold_ms or samples.size == 0:
            self.tray.set_state("idle")
            if self.overlay:
                self.overlay.hide()
            return
        self.tray.set_state("processing")
        self._show("transcribing")
        self._jobs.put((samples, self.recorder.sample_rate, self._down_app, self._down_own))

    def _on_cancel(self) -> None:
        """按住单独的修饰键作热键时又按了别的键（在用组合键）：丢弃这段录音。"""
        if not self.recorder.recording:
            return
        self.recorder.stop()
        self.tray.set_state("idle")
        if self.overlay:
            self.overlay.hide()

    # ---- 后台工作线程：串行处理每段录音 ----

    def _worker(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._handle(*job)
                self.tray.set_state("idle")
            except Exception as e:
                log.exception("处理录音失败")
                self.tray.set_state("error")
                self._show("error", t("overlay.failed"))
                self._sound("error")
                self.tray.notify(t("tray.process_failed", error=e))

    def _handle(self, samples: np.ndarray, sr: int, app: str | None, own: bool = False) -> None:
        """own=True：在 Umless 自己的主窗口里说的（引导页"试一试"），照常输出，但不写历史、不算场景。"""
        cfg, pipeline = self.cfg, self.pipeline
        duration = samples.size / sr
        scene = "default" if own else cfg.scene_of(app)
        intensity = cfg.intensity_of(scene)
        res = pipeline.process(samples, sr, on_stage=self._show, scene=scene, intensity=intensity)
        if not res.raw_text:
            log.info("未识别到内容（%.1fs）", duration)
            self._show("info", t("overlay.no_speech"))
            self._sound("error")
            return
        try:
            output.deliver(res.output_text, cfg.output.mode, cfg.output.restore_clipboard)
            delivered = True
        except Exception:  # 剪贴板被别的程序占用等：这段话照常记录，可以在历史里复制
            log.exception("输出文字失败")
            delivered = False
        if not delivered:
            self._show("error", t("overlay.output_failed"))
            self._sound("error")
        elif res.error:
            self._show("error", t("overlay.polish_failed"))
            self._sound("error")
        else:
            self._show("done", t("overlay.pasted") if cfg.output.mode == "paste" else t("overlay.copied"))
            self._sound("done")

        # 最近一次输出（引导页"试一试"读取）：黑名单应用、暂停记录时不写；自己窗口里的照写
        if own or (cfg.privacy.record_history and not cfg.is_excluded(app)):
            self._save_last_output(res)
        if own:
            log.info("%.1fs Umless 主窗口内的输入，不记录", duration)
            return
        if not cfg.privacy.record_history or cfg.is_excluded(app):
            log.info("%.1fs [%s] 未记录（暂停记录或应用在黑名单中）", duration, app)
            return
        uid = self.db.add_utterance(
            Utterance(
                duration_s=duration,
                app=app,
                asr_engine=pipeline.engine.name,
                raw_text=res.raw_text,
                asr_ms=res.asr_ms,
                output_text=res.output_text,
                llm_model=pipeline.polisher.model if pipeline.polisher else None,
                polished_text=res.polished_text,
                llm_ms=res.llm_ms,
                error=res.error,
                scene=scene,
                intensity=intensity if pipeline.polisher else None,
            )
        )
        try:
            coach.compute_metrics(self.db, uid, res.raw_text, res.polished_text, duration)
        except Exception:
            log.exception("计算指标失败")
        if cfg.audio.keep_audio:
            audio_dir = self.home / "audio"
            audio_dir.mkdir(exist_ok=True)
            sf.write(audio_dir / f"{uid}.wav", samples, sr)
        # 日志默认只记字数与耗时；原文 / 输出属于个人语音内容，仅在 debug 级别（UMLESS_DEBUG=1）写入
        log.info(
            "#%d %.1fs [%s/%s/%s] ASR %dms, LLM %sms, 原文 %d 字 → 输出 %d 字",
            uid, duration, app, scene, intensity, res.asr_ms, res.llm_ms, len(res.raw_text), len(res.output_text),
        )
        log.debug("#%d\n  原文：%s\n  输出：%s", uid, res.raw_text, res.output_text)

    def _save_last_output(self, res: Result) -> None:
        """只存原文与润色稿；原文标注（口癖高亮）由主窗口读取时按当前词表计算。"""
        try:
            self.db.set_kv("last_output", {"text": res.output_text, "raw": res.raw_text,
                                           "polished": res.polished_text, "at": now_iso()})
        except Exception:
            log.exception("写入最近一次输出失败")

    # ---- 心跳：主窗口据此判断常驻进程是否就绪 ----

    def _core_state(self) -> dict:
        ready = self.pipeline is not None and self.hotkey is not None and self.hotkey.alive
        return {"ready": ready, "pid": os.getpid(), "ts": time.time(),
                "hotkey": self.cfg.hotkey.key, "mode": self.cfg.output.mode}

    def _heartbeat(self) -> None:
        """独立线程每 2 秒写一次 kv core_state：模型加载、配置热加载阻塞 _watch 时心跳也不中断。"""
        while True:
            try:
                self.db.set_kv("core_state", self._core_state())
            except Exception:
                log.exception("写入心跳失败")
            if self._stop.wait(HEARTBEAT_S):
                return

    # ---- 模型加载与配置热加载 ----

    def _load_pipeline(self, cfg: Config, engine: asr.ASREngine | None = None) -> bool:
        with self._loading:
            try:
                # 术语表从 self.cfg 读取：只改术语表时复用已加载的引擎，也能用上新术语
                self.pipeline = Pipeline(cfg, engine or asr.create(cfg.asr, lambda: self.cfg.glossary))
                return True
            except Exception as e:
                log.warning("ASR 加载失败：%s", e)
                self.pipeline = None
                return False

    @staticmethod
    def _mtime() -> float:
        try:
            return config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _start_hotkey(self) -> None:
        """配置里的热键写错时（可能是手改了 config.toml）退回默认热键，而不是让热键失效。"""
        from .hotkey import DEFAULT, HotkeyError, PushToTalk

        if self.hotkey:
            self.hotkey.stop()
        args = (self._on_down, self._on_up, self.cfg.hotkey.min_hold_ms, self._on_cancel)
        try:
            self.hotkey = PushToTalk(self.cfg.hotkey.key, *args)
        except HotkeyError:
            log.warning("热键 %r 无效，改用默认热键 %s", self.cfg.hotkey.key, DEFAULT)
            self.hotkey = PushToTalk(DEFAULT, *args)
            self.tray.notify(t("tray.bad_hotkey", name=self.cfg.hotkey.key))
        self.hotkey.start()

    def _reload(self) -> None:
        old = self.cfg
        try:
            new = load()
        except Exception:
            log.exception("配置文件有误，保持原配置")
            return
        self.cfg = new
        log.info("配置已重新加载")
        i18n.set_language(new.app.language)  # 菜单文字在下面 refresh_menu 时按新语言重新取
        if (new.hotkey.key, new.hotkey.min_hold_ms) != (old.hotkey.key, old.hotkey.min_hold_ms):
            self._start_hotkey()
        if new.audio.mic_always_on != old.audio.mic_always_on and not self.recorder.recording:
            self.recorder.shutdown()
            self.recorder = audio.Recorder(always_on=new.audio.mic_always_on)
        if new.ui.overlay != old.ui.overlay:
            if new.ui.overlay:
                self.overlay = self._overlay_cls(lambda: list(self.recorder.levels))
                self.overlay.start()
            elif self.overlay:
                self.overlay.stop()
                self.overlay = None
        if _asr_key(new) != _asr_key(old) or self.pipeline is None:
            self.tray.set_state("loading")
            if self._load_pipeline(new):
                self.tray.set_state("idle")
                self.tray.notify(t("tray.engine_switched", engine=new.asr.engine))
            else:
                self.tray.set_state("error")
        else:
            self._load_pipeline(new, self.pipeline.engine)  # 只更新润色设置，复用已加载的 ASR
        self.tray.refresh_menu()

    def _watch(self) -> None:
        """配置热加载 + 定期维护（自动诊断、过期清理）+ 检查更新 + 响应退出请求。"""
        last_maintenance = time.monotonic() - MAINTENANCE_EVERY_S + 60  # 启动 1 分钟后做第一次
        last_retry = time.monotonic()
        next_update = time.monotonic() + updater.FIRST_CHECK_DELAY_S
        while not self._stop.wait(2):
            if self._quit_requested():
                log.info("主窗口请求退出（安装更新）")
                self.tray.stop()
                return
            m = self._mtime()
            if m != self._cfg_mtime:
                self._cfg_mtime = m
                try:
                    self._reload()
                except Exception:  # 热加载出错不能让这个线程退出，否则之后的热加载和每日诊断都会停掉
                    log.exception("应用新配置失败")
            elif self.pipeline is None and time.monotonic() - last_retry > 10:
                # 首次启动时模型可能还在主窗口里下载，下载完成后自动加载
                last_retry = time.monotonic()
                if self._load_pipeline(self.cfg):
                    self.tray.set_state("idle")
                    self.tray.notify(self._ready_text())
            if time.monotonic() - last_maintenance > MAINTENANCE_EVERY_S:
                last_maintenance = time.monotonic()
                threading.Thread(target=self._maintenance, daemon=True).start()
            if time.monotonic() >= next_update:
                # 每次都只是看缓存是否过期：成功后 24 小时、失败后 1 小时才真正请求清单（与主窗口共用缓存）
                next_update = time.monotonic() + MAINTENANCE_EVERY_S
                if self.cfg.update.auto_check:
                    threading.Thread(target=self._check_update, daemon=True).start()

    def _quit_requested(self) -> bool:
        try:
            return updater.quit_requested(self.db, self._started)
        except Exception:
            log.exception("读取退出请求失败")
            return False

    def _check_update(self) -> None:
        """自动检查更新：失败只记日志；每个新版本只在托盘通知一次，点击通知打开设置的关于页。"""
        try:
            cfg = self.cfg
            info = updater.describe(cfg, self.db, updater.check(cfg, self.db))
            if updater.should_notify(self.db, info):
                self.tray.notify(t("tray.update_available", version=info["latest"]),
                                 on_click=lambda: self.open_ui("settings/about"))
        except Exception:
            log.exception("检查更新失败")

    def _maintenance(self) -> None:
        try:
            coach.backfill_metrics(self.db)
            days = self.cfg.privacy.retention_days
            if days > 0 and self.db.get_kv("purged_on") != date.today().isoformat():
                n = self.db.purge_older_than(days)
                self.db.set_kv("purged_on", date.today().isoformat())
                if n:
                    log.info("已删除 %d 条超过 %d 天的记录", n, days)
            before = coach.latest_diagnosis(self.db, coach._shift(coach.today(), -1))
            coach.run_auto_diagnosis(self.db, self.cfg)
            after = coach.latest_diagnosis(self.db, coach._shift(coach.today(), -1))
            if after and not before:
                self.tray.notify(t("tray.report_ready", score=coach._r(after["overall"])))
            coach.refresh_habits(self.db)
        except Exception:
            log.exception("定期维护失败")

    # ---- 生命周期 ----

    def _setup(self) -> None:
        """托盘就绪后在后台加载模型，避免启动时界面无响应。"""
        if not self.cfg.app.onboarded:
            self.open_ui("onboarding")
        try:  # 打包版：自启项指向旧位置（源码版、移动过的便携版）时改成当前程序
            system.sync_autostart()
        except OSError:
            log.exception("更新开机自启项失败")
        # 心跳最先启动：加载模型期间主窗口也能看到"在运行、未就绪"
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        if self.overlay:
            self.overlay.start()
        self._start_hotkey()
        if self._load_pipeline(self.cfg):
            self.tray.set_state("idle")
            self.tray.notify(self._ready_text())
        else:
            self.tray.set_state("error")
            if self.cfg.app.onboarded:
                self.tray.notify(t("tray.load_failed"))

    def run(self) -> None:
        self.tray.run(self._setup)

    def shutdown(self) -> None:
        self._stop.set()
        if self.hotkey:
            self.hotkey.stop()
        self.recorder.shutdown()
        if self.overlay:
            self.overlay.stop()
        self._jobs.put(None)
        try:  # 退出时立即标记为未就绪，不必等心跳过期
            self.db.set_kv("core_state", {**self._core_state(), "ready": False})
        except Exception:
            pass
