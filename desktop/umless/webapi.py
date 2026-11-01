"""主窗口的 Python 接口（pywebview js_api），约定见 docs/ui-api.md。

运行在主窗口进程里；与常驻进程通过 config.toml 和 SQLite 共享状态。
pywebview 在独立线程中调用这些方法，耗时操作一律走 Jobs 返回 job_id。
公开方法即前端接口；内部状态一律用下划线开头，避免被 pywebview 暴露给前端。
返回给界面的文字（异常信息、任务 message）跟随 app.language，见 i18n.py。
"""

import json
import math
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pyperclip

from . import __version__, coach, config, hotkey, i18n, metrics, models, practice, sounds, system, updater
from .config import INTENSITIES, LANGUAGES, SCENES, SOUND_EVENTS
from .db import DB
from .i18n import t
from .jobs import Jobs
from .llm import LLMClient, Polisher

log = logging.getLogger(__name__)

# name 为中文名，返回给界面时按界面语言替换（i18n 的 api.provider.<id>）
PROVIDERS = [
    {"id": "ollama", "name": "Ollama（本地）", "base_url": "http://localhost:11434/v1",
     "models": ["qwen3.5:2b", "qwen3.5:4b", "gemma3:4b"]},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-v4-flash", "deepseek-v4"]},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "models": ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3.2"]},
    {"id": "dashscope", "name": "阿里百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-flash", "qwen-plus"]},
    {"id": "zhipu", "name": "智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4.5-flash", "glm-4.5-air"]},
]
MODEL_NAMES = {"sensevoice": "SenseVoice-Small", "qwen3_asr": "Qwen3-ASR-0.6B"}
HOLD_MS_RANGE = (100, 1500)  # 轻点与按住的分界
CAPTURE_TIMEOUT_S = 15
MIC_TEST_S = 4.0
MIC_REC_PROGRESS = 0.8  # mic_test：录音阶段 progress 为 0–0.8，识别阶段 ≥ 0.85（前端据此区分阶段）
CORE_STALE_S = 10  # 常驻进程心跳超过这么久没更新，视为没有就绪


class Api:
    def __init__(self) -> None:
        self._cfg = config.load()
        self._cfg_mtime = self._mtime()
        i18n.set_language(self._cfg.app.language)
        self._db = DB(config.data_dir() / "umless.db")
        self._jobs = Jobs()
        self._mic = practice.MicSession()
        self._topic: dict | None = None
        self._engine = None
        self._engine_key = None
        self._engine_lock = threading.Lock()
        self._window = None  # pywebview 窗口，由 window.run 赋值（文件选择框用）
        self._last_output: tuple | None = None  # (kv 原值与当前词表, 结果)
        self._capture: hotkey.Capture | None = None  # 正在录制的热键
        threading.Thread(target=self._warmup, daemon=True).start()

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _mtime() -> float:
        try:
            return config.config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _conf(self) -> config.Config:
        """按修改时间重新读取配置，同时更新界面语言。"""
        m = self._mtime()
        if m != self._cfg_mtime:
            self._cfg, self._cfg_mtime = config.load(), m
            i18n.set_language(self._cfg.app.language)
        return self._cfg

    def _warmup(self) -> None:
        try:
            coach.backfill_metrics(self._db)
            coach.refresh_habits(self._db)
        except Exception:
            log.exception("补算指标失败")

    def _recompute_later(self) -> None:
        threading.Thread(target=self._warmup, daemon=True).start()

    def _asr(self, progress=None):
        """主窗口进程内的 ASR 引擎（练习、麦克风测试用），首次使用时加载。"""
        a = self._conf().asr
        key = (a.engine, str(a.models_path), a.num_threads, a.openai.base_url, a.openai.model)
        with self._engine_lock:
            if self._engine is None or self._engine_key != key:
                if progress:
                    progress(None, t("api.loading_asr"))
                from . import asr

                self._engine = asr.create(a, lambda: self._conf().glossary)
                self._engine_key = key
            return self._engine

    # ------------------------------------------------------------ 1. 通用

    def get_app_info(self):
        cfg = self._conf()
        return {"version": __version__, "onboarded": cfg.app.onboarded, "tour_done": cfg.app.tour_done,
                "theme": cfg.app.theme, "language": i18n.current(),
                "language_setting": cfg.app.language if cfg.app.language in LANGUAGES else "auto",
                "hotkey": cfg.hotkey.key, "data_dir": str(config.data_dir()),
                "core_running": system.is_running(system.CORE_MUTEX), "platform": "windows"}

    def core_status(self):
        """running 以互斥量为准（与 core_running 一致）；ready 还要求心跳在 CORE_STALE_S 秒内且已就绪。

        常驻进程刚启动（还没写第一次心跳）、正在加载模型或卡住时：running=true、ready=false。
        """
        cfg = self._conf()
        running = system.is_running(system.CORE_MUTEX)
        state = self._db.get_kv("core_state")
        state = state if isinstance(state, dict) else {}
        try:
            fresh = running and abs(time.time() - float(state.get("ts") or 0)) <= CORE_STALE_S
        except (TypeError, ValueError):
            fresh = False
        return {"running": running, "ready": bool(fresh and state.get("ready")),
                "hotkey": (fresh and state.get("hotkey")) or cfg.hotkey.key,
                "mode": (fresh and state.get("mode")) or cfg.output.mode}

    def get_last_output(self):
        """常驻进程最近一次输出。kv 里存原文与润色稿，原文标注在这里按当前口癖词表计算。"""
        data = self._db.get_kv("last_output")
        if not isinstance(data, dict) or not data.get("text"):
            return None
        words = coach.lexicon_words(self._db)
        cache_key = (data, tuple(words))
        if self._last_output and self._last_output[0] == cache_key:
            return dict(self._last_output[1])
        raw = data.get("raw") or data["text"]
        m = metrics.analyze(raw, data.get("polished"), None, words)
        result = {"text": data["text"], "raw": raw, "highlights": m["highlights"],
                  "filler_count": m["filler_count"], "at": data.get("at")}
        self._last_output = (cache_key, result)
        return dict(result)

    def list_sounds(self):
        return {"events": list(SOUND_EVENTS), "presets": list(sounds.PRESETS)}

    @staticmethod
    def _check_sound(sound) -> str:
        """提示音取值：预设 id、空串，或存在的 .wav 绝对路径。"""
        value = sound.strip() if isinstance(sound, str) else sound
        if isinstance(value, str) and sounds.is_valid(value):
            return value
        if isinstance(value, str) and value.lower().endswith(".wav") and Path(value).is_absolute():
            raise ValueError(t("cfg.sound_missing", path=value))
        raise ValueError(t("cfg.bad_sound", value=value))

    def preview_sound(self, sound, volume):
        self._conf()
        sound = self._check_sound(sound)
        try:
            volume = float(volume)
            if not math.isfinite(volume):
                raise ValueError
            volume = min(100.0, max(0.0, volume))
        except (TypeError, ValueError):
            raise ValueError(t("cfg.bad_volume")) from None
        sounds.play(sound, volume)
        return None

    def pick_sound_file(self):
        if self._window is None:
            return None
        import webview

        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("WAV (*.wav)",))
        if not picked:  # 取消
            return None
        return str(picked[0] if isinstance(picked, (list, tuple)) else picked)

    @staticmethod
    def _check_hotkey(key) -> str:
        """热键写法校验并规范化（如 "Ctrl + Space" → ctrl+space），错误信息跟随界面语言。"""
        try:
            return hotkey.normalize(key if isinstance(key, str) else "")
        except hotkey.HotkeyError as e:
            raise ValueError(t(f"cfg.hotkey_{e.code}", name=hotkey.label(e.spec, i18n.current()))) from None

    def capture_hotkey(self):
        """等待用户按下新热键（或组合键），返回规范写法；Esc、超时或被取消时返回 None。

        录制期间临时安装的键盘钩子会拦截所有按键，常驻进程的热键因此不会被触发。
        """
        self._conf()
        self.cancel_hotkey_capture()
        cap = self._capture = hotkey.Capture()
        try:
            spec = cap.capture(CAPTURE_TIMEOUT_S)
        except hotkey.HotkeyError as e:
            raise ValueError(t(f"cfg.hotkey_{e.code}", name=hotkey.label(e.spec, i18n.current()))) from None
        finally:
            if self._capture is cap:
                self._capture = None
        return spec

    def cancel_hotkey_capture(self):
        if self._capture:
            self._capture.cancel()
        return None

    def start_core(self):
        if not system.is_running(system.CORE_MUTEX):
            system.spawn()
            for _ in range(30):  # 最多等 3 秒，让界面能立刻刷新状态
                time.sleep(0.1)
                if system.is_running(system.CORE_MUTEX):
                    break
        return None

    def get_nav(self):
        page = self._db.get_kv("nav")
        if page:
            self._db.set_kv("nav", None)
        return page

    def open_path(self, kind):
        paths = {"data_dir": config.data_dir(), "config": config.config_path(),
                 "log": config.data_dir() / "umless.log"}
        if kind not in paths:
            raise ValueError(t("api.unknown_path", kind=kind))
        system.open_path(paths[kind])
        return None

    def copy_text(self, text):
        pyperclip.copy(text or "")
        return None

    def job_status(self, job_id):
        return self._jobs.status(job_id)

    # ------------------------------------------------------------ 2. 概览

    def get_overview(self):
        coach.refresh_habits(self._db)
        return coach.get_overview(self._db, self._conf())

    # ------------------------------------------------------------ 3. 报告

    def list_reports(self):
        data = coach.list_reports(self._db)
        data["items"] = [i for i in data["items"] if i["date"] != data["today"]["date"]]
        return data

    def get_report(self, date):  # noqa: A002 参数名与约定一致
        return coach.get_report(self._db, self._conf(), date)

    def run_diagnosis(self, date):  # noqa: A002
        self._conf()
        return self._jobs.start(lambda p, d: coach.diagnose(self._db, self._conf(), d, p), date,
                               message=t("api.organizing"), key=f"diagnose:{date}")

    def get_trend(self, days):
        return coach.get_trend(self._db, int(days or 30))

    def get_habits(self, status):
        return coach.get_habits(self._db, status)

    def archive_habit(self, id):  # noqa: A002
        coach.set_habit_status(self._db, int(id), "archived")
        return None

    def restore_habit(self, id):  # noqa: A002
        rows = self._db.query("SELECT achieved_at FROM habits WHERE id = ?", (int(id),))
        if not rows:
            raise ValueError(t("api.habit_not_found"))
        coach.set_habit_status(self._db, int(id), "achieved" if rows[0]["achieved_at"] else "active")
        return None

    def add_term(self, right):
        right = (right or "").strip()
        if not right:
            raise ValueError(t("api.term_empty"))
        terms = self._conf().glossary
        if right.lower() not in {x.lower() for x in terms}:
            config.save_patch({"glossary": {"terms": [*terms, right]}})
        return None

    def add_filler(self, word):
        word = (word or "").strip()
        if not word:
            raise ValueError(t("api.filler_empty"))
        if len(word) > 8:
            raise ValueError(t("api.filler_too_long"))
        if not self._db.add_words([word], "user"):
            self._db.set_word_enabled(word, True)
        self._recompute_later()
        return None

    # ------------------------------------------------------------ 4. 练习

    def get_practice_topics(self):
        # 界面语言变了的旧题目视为还没生成，前端会调用 generate_topics 重新出题
        return practice.get_topics(self._db, i18n.resolve(self._conf().app.language))

    def generate_topics(self, refresh):
        if not refresh and (topics := self.get_practice_topics()):
            return self._jobs.start(lambda p: topics)
        return self._jobs.start(lambda p: practice.generate_topics(self._db, self._conf(), p),
                               message=t("api.making_topics"), key="topics")

    def practice_start(self, topic):
        self._conf()
        if not isinstance(topic, dict) or not (topic.get("title") or "").strip():
            raise ValueError(t("api.topic_empty"))
        self._topic = {"duration": 60, "category": t("practice.category_custom"), **topic}
        self._mic.start()
        # 录音期间预先加载识别模型，结束后少等一会儿
        threading.Thread(target=lambda: self._try(self._asr), daemon=True).start()
        return None

    @staticmethod
    def _try(fn):
        try:
            fn()
        except Exception:
            log.exception("预加载失败")

    def mic_level(self):
        return self._mic.level()

    def practice_stop(self):
        if not self._mic.recording or self._topic is None:
            raise ValueError(t("api.not_recording"))
        samples, sr = self._mic.stop()
        topic, self._topic = self._topic, None

        def work(progress):
            engine = self._asr(progress)
            return practice.evaluate(self._db, self._conf(), engine, topic, samples, sr, progress)

        return self._jobs.start(work, message=t("api.recognizing"))

    def practice_cancel(self):
        if self._mic.recording:
            self._mic.stop()
        self._topic = None
        return None

    def list_practice(self, limit):
        return practice.list_practice(self._db, int(limit or 20))

    def get_practice(self, id):  # noqa: A002
        result = practice.get_practice(self._db, int(id))
        if result is None:
            raise ValueError(t("api.practice_not_found"))
        return result

    # ------------------------------------------------------------ 5. 历史

    def list_history(self, query, offset, limit):
        return coach.list_history(self._db, (query or "").strip(), int(offset or 0), int(limit or 30))

    def delete_history(self, id):  # noqa: A002
        self._db.delete_utterance(int(id))
        (config.data_dir() / "audio" / f"{int(id)}.wav").unlink(missing_ok=True)
        return None

    # ------------------------------------------------------------ 6. 设置

    def get_settings(self):
        cfg = self._conf()
        return {
            "config": cfg.to_dict(),
            "models": {
                name: {"downloaded": models.is_downloaded(cfg.asr.models_path, name),
                       "size_mb": spec.size_mb, "name": MODEL_NAMES[name]}
                for name, spec in models.MODELS.items()
            },
            "providers": [{**p, "name": t(f"api.provider.{p['id']}")} for p in PROVIDERS],
            "autostart": system.get_autostart(),
        }

    @classmethod
    def _validate(cls, patch: dict) -> None:
        """校验并就地规范化（音量转为整数、提示音路径去空白）。"""
        if not isinstance(patch, dict):
            raise ValueError(t("cfg.bad_format"))
        for section in ("app", "hotkey", "audio", "polish", "scenes", "asr", "privacy", "update"):
            if section in patch and not isinstance(patch[section], dict):
                raise ValueError(t("cfg.bad_format"))
        lang = patch.get("app", {}).get("language")
        if lang is not None and lang not in LANGUAGES:
            raise ValueError(t("cfg.bad_language", value=lang))
        hk = patch.get("hotkey", {})
        if "key" in hk:
            hk["key"] = cls._check_hotkey(hk["key"])
        if "min_hold_ms" in hk:
            ms = hk["min_hold_ms"]
            lo, hi = HOLD_MS_RANGE
            if isinstance(ms, bool) or not isinstance(ms, int) or not lo <= ms <= hi:
                raise ValueError(t("cfg.bad_hold_ms", min=lo, max=hi))
        audio = patch.get("audio", {})
        if "volume" in audio:
            vol = audio["volume"]
            if (isinstance(vol, bool) or not isinstance(vol, (int, float))
                    or not 0 <= vol <= 100 or vol != int(vol)):
                raise ValueError(t("cfg.bad_volume"))
            audio["volume"] = int(vol)
        if "sounds" in audio:
            if not isinstance(audio["sounds"], dict):
                raise ValueError(t("cfg.bad_format"))
            for event, value in audio["sounds"].items():
                if event not in SOUND_EVENTS:
                    raise ValueError(t("cfg.bad_sound_event", event=event))
                audio["sounds"][event] = cls._check_sound(value)
        intensity = patch.get("polish", {}).get("intensity")
        if intensity is not None and intensity not in INTENSITIES:
            raise ValueError(t("cfg.bad_intensity", value=intensity))
        scenes = patch.get("scenes", {})
        for scene, v in (scenes.get("intensity") or {}).items():
            if scene not in SCENES or (v and v not in INTENSITIES):
                raise ValueError(t("cfg.bad_scene_intensity", scene=scene, value=v))
        for rule in scenes.get("rules") or []:
            if not isinstance(rule, dict) or not rule.get("app") or rule.get("scene") not in SCENES:
                raise ValueError(t("cfg.bad_rule"))
        engine = patch.get("asr", {}).get("engine")
        if engine is not None and engine not in ("sensevoice", "qwen3_asr", "openai"):
            raise ValueError(t("cfg.bad_engine", engine=engine))
        days = patch.get("privacy", {}).get("retention_days")
        if days is not None and (not isinstance(days, int) or days < 0):
            raise ValueError(t("cfg.bad_retention"))
        upd = patch.get("update", {})
        if "auto_check" in upd and not isinstance(upd["auto_check"], bool):
            raise ValueError(t("cfg.bad_format"))
        if "skipped_version" in upd:
            ver = upd["skipped_version"]
            if not isinstance(ver, str):
                raise ValueError(t("cfg.bad_format"))
            ver = upd["skipped_version"] = ver.strip().lstrip("v")
            if ver and not updater.is_valid_version(ver):
                raise ValueError(t("cfg.bad_version", value=ver))
        if "manifest_url" in upd:
            url = upd["manifest_url"]
            if not isinstance(url, str):
                raise ValueError(t("cfg.bad_format"))
            url = upd["manifest_url"] = url.strip()
            if url and not url.startswith(("http://", "https://")):
                raise ValueError(t("cfg.bad_manifest_url"))

    def save_settings(self, patch):
        self._conf()  # 先同步界面语言，校验错误按当前语言提示
        self._validate(patch)
        config.save_patch(patch)
        return self.get_settings()

    def _llm_cfg(self, llm: dict | None):
        base = self._conf().llm
        llm = llm or {}
        return replace(
            base,
            base_url=(llm.get("base_url") or base.base_url).strip(),
            api_key=(llm.get("api_key") if llm.get("api_key") is not None else base.api_key).strip(),
            model=(llm.get("model") or base.model).strip(),
        )

    def test_llm(self, llm):
        cfg = self._llm_cfg(llm)
        sentence = t("api.test_sentence")  # 示例句子跟随界面语言
        if not cfg.base_url.startswith(("http://", "https://")):
            return {"ok": False, "latency_ms": None, "input": sentence, "output": "",
                    "error": t("api.url_scheme")}
        t0 = time.perf_counter()
        try:
            out = Polisher(cfg, self._conf().glossary).polish(sentence)
        except Exception as e:
            return {"ok": False, "latency_ms": None, "input": sentence, "output": "", "error": str(e)}
        return {"ok": bool(out), "latency_ms": int((time.perf_counter() - t0) * 1000),
                "input": sentence, "output": out, "error": None if out else t("api.empty_output")}

    def list_llm_models(self, base_url, api_key):
        cfg = self._llm_cfg({"base_url": base_url, "api_key": api_key})
        return LLMClient(cfg, timeout_s=10).list_models()

    def download_model(self, name):
        self._conf()
        if name not in models.MODELS:
            raise ValueError(t("api.unknown_model", name=name))

        def work(progress):
            models.download(name, self._conf().asr.models_path, progress)
            return {"name": name}

        return self._jobs.start(work, message=t("api.preparing_download"), key=f"download:{name}")

    def set_autostart(self, enabled):
        system.set_autostart(bool(enabled))
        return None

    def get_lexicon(self):
        coach.ensure_lexicon(self._db)
        return [{"word": r["word"], "source": r["source"], "enabled": bool(r["enabled"])}
                for r in self._db.lexicon(enabled_only=False)]

    def remove_filler(self, word):
        rows = self._db.query("SELECT source FROM lexicon WHERE word = ?", (word,))
        if rows and rows[0]["source"] == "builtin":
            self._db.set_word_enabled(word, False)  # 内置词只能停用，否则下次启动会被补回
        else:
            self._db.remove_word(word)
        self._recompute_later()
        return None

    def toggle_filler(self, word, enabled):
        self._db.set_word_enabled(word, bool(enabled))
        self._recompute_later()
        return None

    def mic_test(self):
        self._conf()

        def work(progress):
            self._mic.start()
            peak = 0.0
            try:
                t0 = time.perf_counter()
                while (el := time.perf_counter() - t0) < MIC_TEST_S:
                    peak = max(peak, self._mic.level()["level"])
                    progress(el / MIC_TEST_S * MIC_REC_PROGRESS, t("api.mic_listening"))
                    time.sleep(0.08)
            finally:
                samples, sr = self._mic.stop()
            progress(0.85, t("api.recognizing"))
            # 首次加载模型时保持 0.85，不能退回 None（前端按 progress 判断阶段）
            engine = self._asr(lambda p, msg=None: progress(0.85, msg))
            progress(0.9, t("api.recognizing"))
            text = engine.transcribe(samples, sr) if samples.size else ""
            return {"text": text, "peak": round(peak, 2)}

        return self._jobs.start(work, message=t("api.mic_opening"))

    def export_data(self):
        data = {"app": "Umless", "version": __version__,
                "exported_at": datetime.now().isoformat(timespec="seconds"), **self._db.export_all()}
        path = system.downloads_dir() / f"umless-export-{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"path": str(path)}

    def clear_data(self):
        self._db.clear_all()
        audio_dir = config.data_dir() / "audio"
        if audio_dir.is_dir():
            for f in audio_dir.glob("*.wav"):
                f.unlink(missing_ok=True)
        return None

    def finish_onboarding(self):
        config.save_patch({"app": {"onboarded": True}})
        return None

    # ------------------------------------------------------------ 7. 更新（spec/update.md）

    def check_update(self, force=False):
        """读取更新清单（force=False 时优先用缓存），网络错误放在 error 字段里，不抛出。"""
        cfg = self._conf()
        try:
            state = updater.check(cfg, self._db, bool(force))
        except Exception as e:  # 数据库等意外错误也不打断界面
            log.exception("检查更新失败")
            state = {"error": str(e) or e.__class__.__name__}
        try:
            return updater.describe(cfg, self._db, state)
        except Exception as e:
            log.exception("读取更新状态失败")
            return updater.describe(cfg, None, {"error": str(e) or e.__class__.__name__})

    def download_update(self):
        self._conf()

        def work(progress):
            try:
                updater.download(self._db, progress)
            except updater.UpdateError as e:
                raise RuntimeError(str(e)) from None
            return {"downloaded": True}

        return self._jobs.start(work, message=t("api.preparing_download"), key="update:download")

    def install_update(self):
        """启动已下载的安装程序（静默安装，结束后自动重新启动 Umless），然后常驻进程与主窗口都退出。"""
        self._conf()
        mode = system.install_mode()
        if mode != "installed":
            return {"status": "unsupported", "install_hint": updater.install_hint(mode)}
        try:
            installer = updater.verified_installer(self._db)
            updater.launch_installer(updater.install_command(installer))
        except updater.UpdateError as e:
            raise ValueError(str(e)) from None
        updater.request_core_quit(self._db)
        threading.Thread(target=self._exit_for_update, daemon=True).start()
        return {"status": "installing"}

    def _exit_for_update(self) -> None:
        time.sleep(0.5)  # 先让 install_update 的返回值送到前端
        self._close()
        if self._window is not None:
            try:
                self._window.destroy()  # 关闭窗口即退出主窗口进程
            except Exception:
                log.exception("关闭主窗口失败")

    def skip_update(self, version):
        self._conf()
        if version is None:
            version = ""
        version = version.strip().lstrip("v") if isinstance(version, str) else version
        # 空串 / null：清除已跳过的版本（docs/ui-api.md，与 Android 一致）
        if not isinstance(version, str) or (version and not updater.is_valid_version(version)):
            raise ValueError(t("cfg.bad_version", value=version))
        config.save_patch({"update": {"skipped_version": version}})
        return None

    # ------------------------------------------------------------ 生命周期

    def _close(self) -> None:
        self.cancel_hotkey_capture()
        self._mic.shutdown()
