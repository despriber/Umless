"""0.3.0 新增：配置新字段、主窗口新接口、常驻进程心跳与最近一次输出、模拟后端与真实接口结构一致。"""

import time
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from umless import app as app_mod
from umless import config, i18n, output, sounds, system
from umless.db import DB
from umless.ui.mock_api import MockApi
from umless.webapi import Api


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UMLESS_HOME", str(tmp_path))
    monkeypatch.delenv("UMLESS_LLM_API_KEY", raising=False)
    monkeypatch.setattr(sounds, "play", lambda *a: False)  # 不真的出声
    yield tmp_path
    i18n.set_language("zh")


@pytest.fixture
def api(home):
    a = Api()
    yield a
    a._close()  # 数据库不关：后台补算指标的线程可能还在用


# ---- 配置 ----


def test_new_config_defaults(home):
    cfg = config.load()
    assert cfg.app.language == "auto" and cfg.app.tour_done is False
    assert cfg.audio.volume == 60
    assert cfg.audio.sounds == {"start": "rise", "stop": "fall", "done": "", "error": "low"}
    assert cfg.output.restore_clipboard is False


def test_sounds_merge_with_defaults_and_volume_clamped(home):
    (home / "config.toml").write_text(
        '[audio]\nvolume = 150\nsounds = { start = "pop", bogus = "x" }\n', encoding="utf-8")
    cfg = config.load()
    assert cfg.audio.sounds == {"start": "pop", "stop": "fall", "done": "", "error": "low"}
    assert cfg.audio.volume == 100


def test_old_restore_clipboard_migrated_once(home):
    old = "[output]\nmode = \"paste\"\n# 自动粘贴后恢复原剪贴板内容（仅限文本）\nrestore_clipboard = true\n"
    (home / "config.toml").write_text(old, encoding="utf-8")
    assert config.load().output.restore_clipboard is False
    assert "restore_clipboard = false" in (home / "config.toml").read_text(encoding="utf-8")
    # 迁移后用户自己再打开，不会被改回
    assert config.save_patch({"output": {"restore_clipboard": True}}).output.restore_clipboard is True
    assert config.load().output.restore_clipboard is True


def test_deliver_keeps_result_in_clipboard(monkeypatch):
    clip = {"v": "旧内容"}
    monkeypatch.setattr(output.pyperclip, "paste", lambda: clip["v"])
    monkeypatch.setattr(output.pyperclip, "copy", lambda v: clip.update(v=v))
    monkeypatch.setattr(output, "_keyboard", SimpleNamespace(
        pressed=lambda *_: __import__("contextlib").nullcontext(), tap=lambda *_: None))
    output.deliver("新结果", "paste", restore_clipboard=False)
    time.sleep(0.8)  # 恢复线程（如果有）的延迟是 0.6 秒
    assert clip["v"] == "新结果"


# ---- 设置校验 ----


def test_custom_hotkey_saved_normalized(api, home):
    api.save_settings({"hotkey": {"key": "Shift + Ctrl + F1", "min_hold_ms": 400}})
    cfg = config.load()
    assert cfg.hotkey.key == "ctrl+shift+f1" and cfg.hotkey.min_hold_ms == 400
    for bad in ({"hotkey": {"key": "a"}}, {"hotkey": {"key": "ctrl+shift"}}, {"hotkey": {"min_hold_ms": 5}}):
        with pytest.raises(ValueError):
            api.save_settings(bad)
    api.save_settings({"app": {"language": "zh"}})  # 不依赖运行环境的系统语言（CI 是英文）
    with pytest.raises(ValueError, match="影响正常打字"):
        api.save_settings({"hotkey": {"key": "space"}})


def test_validate_new_fields(api, home):
    for bad in ({"app": {"language": "fr"}}, {"audio": {"volume": 101}}, {"audio": {"volume": 2.5}},
                {"audio": {"volume": True}}, {"audio": {"sounds": {"beep": "rise"}}},
                {"audio": {"sounds": {"start": "boom"}}},
                {"audio": {"sounds": {"start": str(home / "missing.wav")}}},
                {"audio": {"sounds": {"start": "relative.wav"}}}):
        with pytest.raises(ValueError):
            api.save_settings(bad)
    wav = home / "ding.wav"
    sf.write(wav, np.zeros(441, dtype=np.float32), 44100)
    s = api.save_settings({"audio": {"volume": 40.0, "sounds": {"done": "chime", "error": "", "start": str(wav)}}})
    a = s["config"]["audio"]
    assert a["volume"] == 40 and isinstance(a["volume"], int)
    assert a["sounds"] == {"start": str(wav), "stop": "fall", "done": "chime", "error": ""}
    assert config.load().audio.sounds["start"] == str(wav)  # 含反斜杠的路径正确写进 TOML


def test_language_switch_changes_app_info_and_errors(api):
    info = api.get_app_info()
    assert set(info) == {"version", "onboarded", "tour_done", "theme", "language", "language_setting",
                         "hotkey", "data_dir", "core_running", "platform"}
    assert info["platform"] == "windows"
    assert info["version"] == "0.3.0" and info["language_setting"] == "auto"
    assert info["language"] in ("zh", "en")
    api.save_settings({"app": {"language": "en", "tour_done": True}})
    info = api.get_app_info()
    assert info["language"] == "en" and info["language_setting"] == "en" and info["tour_done"] is True
    with pytest.raises(ValueError, match="Term can't be empty"):
        api.add_term("")
    names = {p["id"]: p["name"] for p in api.get_settings()["providers"]}
    assert names["siliconflow"] == "SiliconFlow"
    api.save_settings({"app": {"language": "zh"}})
    with pytest.raises(ValueError, match="术语不能为空"):
        api.add_term("")


# ---- 常驻进程状态与最近一次输出 ----


def test_core_status(api, monkeypatch):
    monkeypatch.setattr(system, "is_running", lambda name: False)
    assert api.core_status() == {"running": False, "ready": False, "hotkey": "caps_lock", "mode": "paste"}
    monkeypatch.setattr(system, "is_running", lambda name: True)
    assert api.core_status()["running"] is True and api.core_status()["ready"] is False  # 还没有心跳
    api._db.set_kv("core_state", {"ready": True, "pid": 1, "ts": time.time(), "hotkey": "f8", "mode": "clipboard"})
    assert api.core_status() == {"running": True, "ready": True, "hotkey": "f8", "mode": "clipboard"}
    api._db.set_kv("core_state", {"ready": True, "pid": 1, "ts": time.time() - 30, "hotkey": "f8", "mode": "paste"})
    assert api.core_status() == {"running": True, "ready": False, "hotkey": "caps_lock", "mode": "paste"}


def test_get_last_output_with_highlights(api):
    assert api.get_last_output() is None
    api._db.set_kv("last_output", {"text": "我明天想约你聊一下那个方案", "raw": "嗯我明天就是想约你聊一下那个方案",
                                   "polished": "我明天想约你聊一下那个方案", "at": "2026-10-12T10:57:44"})
    out = api.get_last_output()
    assert set(out) == {"text", "raw", "highlights", "filler_count", "at"}
    assert out["filler_count"] == 2 and ["嗯", "filler"] in out["highlights"] and ["就是", "filler"] in out["highlights"]
    assert api.get_last_output() == out  # 轮询时走缓存


def test_last_output_rechecks_changed_lexicon(api):
    api._db.set_kv("last_output", {"text": "好", "raw": "嗯好", "polished": "好", "at": "now"})
    assert api.get_last_output()["filler_count"] == 1
    # 词表可能在常驻进程或其他窗口中修改，不能只靠本对象的修改方法失效缓存。
    api._db.set_word_enabled("嗯", False)
    assert api.get_last_output()["filler_count"] == 0


@pytest.mark.parametrize("volume", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_sound_volume_is_rejected(api, volume):
    with pytest.raises(ValueError):
        api.save_settings({"audio": {"volume": volume}})
    with pytest.raises(ValueError):
        api.preview_sound("rise", volume)


@pytest.mark.parametrize("patch", [{"audio": None}, {"app": "en"}, {"audio": []}])
def test_invalid_settings_sections_have_validation_error(api, patch):
    with pytest.raises(ValueError):
        api.save_settings(patch)


def test_nonfinite_volume_in_config_falls_back(home):
    (home / "config.toml").write_text("[audio]\nvolume = inf\n", encoding="utf-8")
    assert config.load().audio.volume == 60


def test_sound_methods(api, home, monkeypatch):
    assert api.list_sounds() == {"events": ["start", "stop", "done", "error"], "presets": list(sounds.PRESETS)}
    calls = []
    monkeypatch.setattr(sounds, "play", lambda s, v: calls.append((s, v)))
    assert api.preview_sound("chime", 70) is None and calls == [("chime", 70.0)]
    with pytest.raises(ValueError):
        api.preview_sound(str(home / "nope.wav"), 50)
    assert api.pick_sound_file() is None  # 没有窗口（测试环境）
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: ("C:/x/ding.wav",))
    assert api.pick_sound_file() == "C:/x/ding.wav"
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: None)
    assert api.pick_sound_file() is None
    assert not [k for k in vars(api) if not k.startswith("_")]


def test_mic_test_progress_phases(api, monkeypatch):
    import umless.webapi as webapi

    monkeypatch.setattr(webapi, "MIC_TEST_S", 0.3)
    seen: list[float] = []

    class FakeMic:
        def start(self): pass
        def level(self): return {"recording": True, "elapsed": 0, "level": 0.5}
        def stop(self): return np.zeros(1600, np.float32), 16000
        def shutdown(self): pass

    class FakeJobs:
        def start(self, fn, *args, message=None):
            self.result = fn(lambda p, msg=None: seen.append(p), *args)
            return {"job_id": "x"}

    api._mic, api._jobs = FakeMic(), FakeJobs()
    api._engine, api._engine_key = None, None
    monkeypatch.setattr(api, "_asr", lambda progress=None: (progress(None, "加载"), SimpleNamespace(
        transcribe=lambda s, sr: "你好"))[1])
    api.mic_test()
    assert api._jobs.result == {"text": "你好", "peak": 0.5}
    first_rec = [p for p in seen if p is not None and p < 0.85]
    assert first_rec and max(first_rec) <= 0.8
    after = seen[len(first_rec):]
    assert after and all(p is not None and p >= 0.85 for p in after)


# ---- 常驻进程：最近一次输出、不记录自己窗口里的输入、心跳 ----


class FakeASR:
    name = "fake"

    def transcribe(self, samples, sr):
        return "嗯我明天就是想约你聊一下"


def _core(home, monkeypatch) -> app_mod.App:
    """不创建托盘 / 提示条 / 麦克风，只组装 _handle 需要的部分。"""
    monkeypatch.setattr(output, "deliver", lambda *a: None)
    cfg = config.load()
    cfg.llm.enabled = False
    a = app_mod.App.__new__(app_mod.App)
    a.cfg, a.home, a.overlay, a.hotkey = cfg, home, None, None
    a.db = DB(home / "umless.db")
    a.pipeline = app_mod.Pipeline(cfg, engine=FakeASR())
    a._stop = __import__("threading").Event()
    return a


def test_handle_writes_last_output_and_skips_own_window(home, monkeypatch):
    core = _core(home, monkeypatch)
    samples = np.zeros(16000, np.float32)
    core._handle(samples, 16000, "Typora.exe")
    assert core.db.query("SELECT COUNT(*) AS n FROM utterances")[0]["n"] == 1
    last = core.db.get_kv("last_output")
    assert last["text"] == "嗯我明天就是想约你聊一下" and last["raw"] and last["at"]

    core.db.set_kv("last_output", None)
    core._handle(samples, 16000, "python.exe", own=True)  # Umless 主窗口里的"试一试"
    assert core.db.query("SELECT COUNT(*) AS n FROM utterances")[0]["n"] == 1  # 不写历史
    assert core.db.get_kv("last_output")["text"]  # 但写最近一次输出

    core.db.set_kv("last_output", None)
    core._handle(samples, 16000, "KeePass.exe")  # 黑名单应用：都不写
    assert core.db.get_kv("last_output") is None
    assert core.db.query("SELECT COUNT(*) AS n FROM utterances")[0]["n"] == 1
    core.db.close()


def test_output_failure_still_recorded(home, monkeypatch):
    core = _core(home, monkeypatch)
    shown = []
    core._show = lambda state, text="": shown.append((state, text))
    core._sound = lambda event: None

    def broken(*a):
        raise RuntimeError("clipboard locked")

    monkeypatch.setattr(output, "deliver", broken)
    core._handle(np.zeros(16000, np.float32), 16000, "Typora.exe")
    assert core.db.query("SELECT COUNT(*) AS n FROM utterances")[0]["n"] == 1  # 能在历史里找回
    assert shown[-1] == ("error", i18n.t("overlay.output_failed"))
    core.db.close()


def test_mic_failure_shows_error_instead_of_hanging(home, monkeypatch):
    core = _core(home, monkeypatch)
    shown, sounds_ = [], []
    core._show = lambda state, text="": shown.append(state)
    core._sound = sounds_.append
    core.tray = SimpleNamespace(set_state=lambda s: shown.append(f"tray:{s}"))
    monkeypatch.setattr(output, "foreground_app", lambda: "x.exe")
    monkeypatch.setattr(output, "foreground_is_umless", lambda: False)
    aborted = []

    def fail():
        raise OSError("no device")

    core.recorder = SimpleNamespace(start=fail, abort=lambda: aborted.append(1), recording=False)
    core._on_down()
    assert shown == ["recording", "error"] and sounds_ == ["error"] and aborted == [1]
    assert "tray:recording" not in shown
    core.db.close()


def test_jobs_with_same_key_are_not_duplicated():
    from umless.jobs import Jobs

    gate = __import__("threading").Event()
    jobs, runs = Jobs(), []

    def work(progress):
        runs.append(1)
        gate.wait(2)
        return "ok"

    a = jobs.start(work, key="diagnose:2026-10-30")
    b = jobs.start(work, key="diagnose:2026-10-30")
    c = jobs.start(work, key="diagnose:2026-10-29")
    assert a == b and a != c
    gate.set()
    for _ in range(50):
        if jobs.status(a["job_id"])["state"] == "done":
            break
        time.sleep(0.02)
    assert len(runs) == 2
    assert "key" not in jobs.status(c["job_id"])


def test_heartbeat_reports_ready(home, monkeypatch):
    core = _core(home, monkeypatch)
    core.hotkey = SimpleNamespace(alive=True)
    core._stop.set()  # 只写一次就退出
    core._heartbeat()
    state = core.db.get_kv("core_state")
    assert state["ready"] is True and state["hotkey"] == "caps_lock" and abs(state["ts"] - time.time()) < 5
    core.pipeline = None  # 模型还在加载：在运行但未就绪
    core._heartbeat()
    assert core.db.get_kv("core_state")["ready"] is False
    core.db.close()


# ---- 模拟后端与真实接口结构一致 ----


def test_mock_api_matches_real_shapes(api):
    mock = MockApi()
    assert set(mock.get_app_info()) == set(api.get_app_info())
    assert set(mock.core_status()) == set(api.core_status())
    assert mock.list_sounds() == api.list_sounds()
    assert mock.get_last_output() is None
    out = mock.mock_dictate()
    api._db.set_kv("last_output", {"text": "好", "raw": "嗯好", "polished": "好", "at": "2026-10-12T10:00:00"})
    assert set(out) == set(api.get_last_output())
    assert ["就是", "filler"] in out["highlights"] or out["highlights"][0][1] == "filler"
    assert mock.get_last_output() == out
    assert set(mock.get_settings()["config"]["audio"]) == set(api.get_settings()["config"]["audio"])
    assert set(mock.get_settings()["config"]["app"]) == set(api.get_settings()["config"]["app"])
    mock.save_settings({"app": {"language": "en"}})
    assert mock.get_app_info()["language"] == "en"
    assert mock.mock_dictate()["text"].startswith("I'd")
    assert mock.pick_sound_file().endswith(".wav") and mock.preview_sound("rise", 50) is None
    mock.clear_data()
    assert mock.get_last_output() is None
    with pytest.raises(ValueError):
        mock.save_settings({"audio": {"volume": 1.5}})


def test_mock_api_android_platform():
    """Android 模式：配置去掉桌面专属项，提供键盘状态等 Android 专属方法，桌面专属方法不可用。"""
    mock = MockApi(platform="android", onboarded=False)
    assert mock.get_app_info()["platform"] == "android"
    config = mock.get_settings()["config"]
    assert not {"hotkey", "output", "ui"} & set(config)
    assert config["voice"] == {"space_long_press": True, "toolbar_button": True, "haptic": True}
    assert config["privacy"]["skip_incognito"] is True
    assert set(mock.get_settings()["models"]) == {"sensevoice"}
    assert mock.get_ime_status() == {"enabled": False, "selected": False}
    mock.show_ime_picker()  # 未启用时选不到
    assert mock.get_ime_status()["selected"] is False
    mock.open_ime_settings()
    mock.show_ime_picker()
    assert mock.get_ime_status() == {"enabled": True, "selected": True}
    assert mock.request_mic_permission() == {"granted": True}
    assert mock.export_logs()["path"].endswith(".zip")
    for name in ("core_status", "start_core", "capture_hotkey"):
        with pytest.raises(ValueError):
            getattr(mock, name)()
    with pytest.raises(ValueError):
        MockApi().get_ime_status()


def _wait_job(mock, job_id):
    for _ in range(100):
        st = mock.job_status(job_id)
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("任务超时")


UPDATE_KEYS = {"current", "latest", "available", "skipped", "notes", "published_at", "size", "checked_at", "error",
               "can_install", "install_hint", "downloaded"}


@pytest.mark.parametrize("platform", ["windows", "android"])
def test_mock_api_update(platform):
    """模拟后端的更新接口（docs/ui-api.md「更新」）：检查 → 下载 → 安装、跳过、已是最新、检查失败。"""
    mock = MockApi(platform=platform)
    latest = "0.2.0" if platform == "android" else "0.4.0"
    info = mock.check_update(False)
    assert set(info) == UPDATE_KEYS
    assert info["available"] and info["latest"] == latest and info["current"] == mock.get_app_info()["version"]
    assert "\n" in info["notes"] and info["size"] > 0 and info["error"] is None and not info["downloaded"]
    assert mock.get_settings()["config"]["update"] == {"auto_check": True, "skipped_version": "", "manifest_url": ""}
    with pytest.raises(ValueError):
        mock.install_update()  # 还没下载
    job = mock.download_update()["job_id"]
    assert mock.download_update()["job_id"] == job  # 同一时间只有一个下载任务
    st = _wait_job(mock, job)
    assert st["state"] == "done" and st["result"] == {"downloaded": True}
    assert mock.check_update(False)["downloaded"]
    if platform == "android":
        assert mock.install_update() == {"status": "needs_permission"}
    assert mock.install_update() == {"status": "installing"}
    assert mock.skip_update(latest) is None
    assert mock.check_update(False)["skipped"] and mock.get_settings()["config"]["update"]["skipped_version"] == latest
    mock.mock_update("latest")
    info = mock.check_update(True)
    assert not info["available"] and info["latest"] == info["current"] and info["checked_at"]
    mock.mock_update("error")
    info = mock.check_update(True)
    assert info["error"] and not info["available"]
    with pytest.raises(ValueError):
        mock.download_update()
    mock.mock_update("available", fail_download=True)
    assert _wait_job(mock, mock.download_update()["job_id"])["state"] == "error"


def test_mock_api_update_from_source():
    mock = MockApi(update="source")
    info = mock.check_update(False)
    assert info["available"] and not info["can_install"] and "git pull" in info["install_hint"]
    res = mock.install_update()
    assert res["status"] == "unsupported" and "git pull" in res["install_hint"]
    with pytest.raises(ValueError):
        MockApi(update="nope")
