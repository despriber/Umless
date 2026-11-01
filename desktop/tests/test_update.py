"""应用更新（spec/update.md）：版本比较、清单解析、缓存、下载校验、安装命令、常驻进程退出请求。

全部用本机 HTTP 假服务，不访问真实网络。
"""

import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from umless import __version__, config, i18n, system, updater
from umless import app as app_mod
from umless.db import DB
from umless.webapi import Api

INSTALLER = b"MZ fake installer " * 1000
SHA = hashlib.sha256(INSTALLER).hexdigest()


class Fake(BaseHTTPRequestHandler):
    """路径 → (状态码, 内容)；记录每个路径被请求的次数。"""

    routes: dict = {}
    hits: dict = {}

    def do_GET(self):
        Fake.hits[self.path] = Fake.hits.get(self.path, 0) + 1
        status, body = Fake.routes.get(self.path, (404, b"not found"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def manifest(base: str, version="9.1.0", sha=SHA, **extra) -> dict:
    return {
        "schema": 1,
        "app": "umless-desktop",
        "version": version,
        "published_at": "2026-11-05T10:00:00Z",
        "notes": {"zh": "修复若干问题", "en": "Bug fixes"},
        "assets": [
            {"platform": "windows-x64", "kind": "portable", "name": "Umless-portable.zip",
             "url": f"{base}/portable.zip", "size": 1, "sha256": "0" * 64},
            {"platform": "android-arm64-v8a", "kind": "apk", "name": "a.apk",
             "url": f"{base}/a.apk", "size": 1, "sha256": "1" * 64},
            {"platform": "windows-x64", "kind": "installer", "name": f"Umless-Setup-{version}.exe",
             "url": f"{base}/setup.exe", "size": len(INSTALLER), "sha256": sha},
        ],
        **extra,
    }


@pytest.fixture
def home(tmp_path, monkeypatch, server):
    monkeypatch.setenv("UMLESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(updater, "download_dir", lambda: tmp_path / "temp" / "umless-update")
    Fake.routes = {"/update.json": (200, json.dumps(manifest(server)).encode()),
                   "/setup.exe": (200, INSTALLER)}
    Fake.hits = {}
    config.save_patch({"update": {"manifest_url": f"{server}/update.json"}})
    i18n.set_language("zh")
    yield tmp_path / "home"
    i18n.set_language("zh")


@pytest.fixture
def db(home):
    d = DB(home / "umless.db")
    yield d
    d.close()


# ---- 版本比较 ----


@pytest.mark.parametrize("newer, older", [
    ("0.3.1", "0.3.0"), ("0.10.0", "0.9.9"), ("1.0.0", "0.99.99"), ("1.2.0", "1.2.0-beta.1"),
    ("1.2.0-beta.2", "1.2.0-beta.1"), ("1.2.0-beta.10", "1.2.0-beta.2"), ("1.2.0-rc.1", "1.2.0-beta.9"),
    ("1.2.0-beta", "1.2.0-1"), ("v2.0.0", "1.9.0"),
])
def test_version_order(newer, older):
    assert updater.is_newer(newer, older)
    assert not updater.is_newer(older, newer)


def test_version_equal_and_invalid():
    assert not updater.is_newer("0.3.0", "0.3.0")
    assert not updater.is_newer("0.3.0+build.5", "0.3.0")  # 构建元数据不参与比较
    for bad in ("1.2", "1.2.x", "", "latest", "1.2.3.4"):
        assert not updater.is_valid_version(bad)
        with pytest.raises(ValueError):
            updater.parse_version(bad)


# ---- 清单 ----


def test_manifest_selects_windows_installer_only(server):
    m = updater.parse_manifest(manifest(server))
    assert m["version"] == "9.1.0" and m["published_at"] == "2026-11-05T10:00:00Z"
    assert m["asset"] == {"name": "Umless-Setup-9.1.0.exe", "url": f"{server}/setup.exe",
                          "sha256": SHA, "size": len(INSTALLER)}


def test_manifest_without_matching_asset(server):
    data = manifest(server)
    data["assets"] = [a for a in data["assets"] if a["kind"] != "installer"]
    assert updater.parse_manifest(data)["asset"] is None
    data["assets"].append({"platform": "windows-x64", "kind": "installer", "url": f"{server}/x.exe",
                           "sha256": "not-a-hash"})  # 字段不全的项忽略
    assert updater.parse_manifest(data)["asset"] is None


def test_manifest_future_schema_and_unknown_fields(server):
    data = manifest(server, schema=2, channel="stable")
    data["assets"][2]["sha256"] = SHA.upper()  # 统一转成小写
    m = updater.parse_manifest(data)
    assert m["schema"] == 2 and m["asset"]["sha256"] == SHA


@pytest.mark.parametrize("patch", [{"schema": 0}, {"schema": "1"}, {"version": "next"}, {"version": None}])
def test_manifest_invalid(server, patch):
    with pytest.raises(updater.UpdateError):
        updater.parse_manifest({**manifest(server), **patch})
    with pytest.raises(updater.UpdateError):
        updater.parse_manifest([1, 2])


def test_notes_language_fallback():
    assert updater.pick_notes({"zh": "中", "en": "en"}, "en") == "en"
    assert updater.pick_notes({"zh": "中"}, "en") == "中"
    assert updater.pick_notes({"en": "en"}, "zh") == "en"
    assert updater.pick_notes({}, "zh") == ""


def test_sources_mirror_only_for_github():
    url = updater.MANIFEST_URL
    assert url == "https://github.com/despriber/Umless/releases/latest/download/update.json"
    assert updater.sources(url) == ["https://gh-proxy.com/" + url, url]
    assert updater.sources("http://127.0.0.1:1/update.json") == ["http://127.0.0.1:1/update.json"]


# ---- 检查与缓存 ----


def test_check_caches_until_forced(home, db, server):
    cfg = config.load()
    s1 = updater.check(cfg, db)
    assert s1["manifest"]["version"] == "9.1.0" and s1["error"] is None
    assert Fake.hits["/update.json"] == 1
    updater.check(cfg, db)  # 24 小时内：用缓存
    assert Fake.hits["/update.json"] == 1
    updater.check(cfg, db, force=True)
    assert Fake.hits["/update.json"] == 2
    # 缓存在 kv 里：另一个进程（另一个 DB 连接）也能读到
    other = DB(home / "umless.db")
    assert other.get_kv(updater.STATE_KEY)["manifest"]["version"] == "9.1.0"
    other.close()


def test_check_cache_expires_and_depends_on_url_and_version(home, db, server, monkeypatch):
    cfg = config.load()
    updater.check(cfg, db)
    state = db.get_kv(updater.STATE_KEY)
    db.set_kv(updater.STATE_KEY, {**state, "checked_ts": time.time() - updater.CHECK_EVERY_S - 1})
    updater.check(cfg, db)
    assert Fake.hits["/update.json"] == 2
    db.set_kv(updater.STATE_KEY, {**db.get_kv(updater.STATE_KEY), "current": "0.0.1"})  # 本机升级过
    updater.check(cfg, db)
    assert Fake.hits["/update.json"] == 3
    Fake.routes["/other.json"] = Fake.routes["/update.json"]
    cfg.update.manifest_url = f"{server}/other.json"
    updater.check(cfg, db)
    assert Fake.hits["/other.json"] == 1


def test_auto_check_off_only_returns_cache(home, db):
    cfg = config.load()
    cfg.update.auto_check = False
    assert updater.check(cfg, db) == {}
    assert "/update.json" not in Fake.hits
    assert updater.check(cfg, db, force=True)["manifest"]  # 手动检查照常
    assert Fake.hits["/update.json"] == 1


def test_check_failure_is_recorded_not_raised(home, db, server):
    cfg = config.load()
    updater.check(cfg, db)
    Fake.routes["/update.json"] = (500, b"oops")
    state = updater.check(cfg, db, force=True)
    assert state["error"] and state["manifest"]["version"] == "9.1.0"  # 之前查到的版本保留
    # 失败后 1 小时内不自动重试
    updater.check(cfg, db)
    assert Fake.hits["/update.json"] == 2
    Fake.routes["/update.json"] = (200, b"<html>not json</html>")
    assert updater.check(cfg, db, force=True)["error"]


def test_describe_shape_and_flags(home, db, monkeypatch):
    cfg = config.load()
    info = updater.describe(cfg, db, updater.check(cfg, db))
    assert set(info) == {"current", "latest", "available", "skipped", "notes", "published_at", "size",
                         "checked_at", "error", "can_install", "install_hint", "downloaded"}
    assert info["current"] == __version__ and info["latest"] == "9.1.0"
    assert info["available"] and not info["skipped"] and not info["downloaded"]
    assert info["notes"] == "修复若干问题" and info["size"] == len(INSTALLER) and info["checked_at"]
    assert info["can_install"] is False and "git pull" in info["install_hint"]  # 源码运行
    i18n.set_language("en")
    assert updater.describe(cfg, db, db.get_kv(updater.STATE_KEY))["notes"] == "Bug fixes"
    cfg.update.skipped_version = "9.1.0"
    assert updater.describe(cfg, db, db.get_kv(updater.STATE_KEY))["skipped"] is True
    # 清单里的版本不比当前新：没有更新
    assert not updater.describe(cfg, db, {"manifest": {"version": __version__, "asset": {"sha256": SHA}}})["available"]
    assert updater.describe(cfg, db, None)["latest"] is None


def test_can_install_frozen_vs_source(tmp_path, monkeypatch):
    assert system.install_mode() == "source"
    app = tmp_path / "Umless"
    app.mkdir()
    (app / "Umless.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "umless-cli.exe"))
    assert system.install_mode() == "portable"
    assert "releases" in updater.install_hint()
    (app / "unins000.exe").write_bytes(b"")
    assert system.install_mode() == "installed" and updater.install_hint() is None


# ---- 下载与校验 ----


def test_download_verifies_and_marks_downloaded(home, db):
    cfg = config.load()
    updater.check(cfg, db)
    seen = []
    path = updater.download(db, lambda p, msg: seen.append(p))
    assert path.read_bytes() == INSTALLER and path.name == "Umless-Setup-9.1.0.exe"
    assert seen[-1] == 1.0
    assert updater.describe(cfg, db, db.get_kv(updater.STATE_KEY))["downloaded"] is True
    assert updater.verified_installer(db) == path
    updater.download(db)  # 已下载：不再请求
    assert Fake.hits["/setup.exe"] == 1


def test_download_sha256_mismatch_deletes_file(home, db, server):
    Fake.routes["/update.json"] = (200, json.dumps(manifest(server, sha="ab" * 32)).encode())
    cfg = config.load()
    updater.check(cfg, db)
    with pytest.raises(updater.UpdateError, match="sha256"):
        updater.download(db)
    assert not any(updater.download_dir().iterdir())
    assert updater.describe(cfg, db, db.get_kv(updater.STATE_KEY))["downloaded"] is False


def test_installer_tampered_after_download(home, db):
    updater.check(config.load(), db)
    path = updater.download(db)
    path.write_bytes(INSTALLER[:-1] + b"X")  # 大小不变、内容被改
    with pytest.raises(updater.UpdateError):
        updater.verified_installer(db)
    assert not path.exists()


def test_download_without_update(home, db, server):
    Fake.routes["/update.json"] = (200, json.dumps(manifest(server, version="0.0.1")).encode())
    updater.check(config.load(), db)
    with pytest.raises(updater.UpdateError):
        updater.download(db)


# ---- 界面接口 ----


@pytest.fixture
def api(home, monkeypatch):
    a = Api()
    yield a
    a._close()


def _wait(api, job):
    for _ in range(200):
        st = api.job_status(job["job_id"])
        if st["state"] != "running":
            return st
        time.sleep(0.02)
    raise AssertionError("任务超时")


def test_api_check_never_raises(api, monkeypatch):
    config.save_patch({"update": {"manifest_url": "http://127.0.0.1:9/update.json"}})  # 没有服务
    info = api.check_update(True)
    assert info["error"] and info["available"] is False and info["latest"] is None
    monkeypatch.setattr(updater, "check", lambda *a: 1 / 0)
    assert api.check_update(False)["error"]


def test_api_download_install_flow(api, monkeypatch):
    info = api.check_update(False)
    assert info["available"] and not info["downloaded"]
    st = _wait(api, api.download_update())
    assert st["state"] == "done" and st["result"] == {"downloaded": True}
    assert api.check_update(False)["downloaded"] is True

    # 源码运行：不安装，只给提示
    res = api.install_update()
    assert res["status"] == "unsupported" and "git pull" in res["install_hint"]

    launched, destroyed = [], []
    monkeypatch.setattr(system, "install_mode", lambda: "installed")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    api._window = SimpleNamespace(destroy=lambda: destroyed.append(True))
    assert api.install_update() == {"status": "installing"}
    cmd, kw = launched[0]
    assert Path(cmd[0]).name == "Umless-Setup-9.1.0.exe"
    assert cmd[1:5] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    assert cmd[5].startswith("/LOG=") and cmd[5].endswith("install.log")
    assert kw["creationflags"] & updater.subprocess.DETACHED_PROCESS
    # 请求常驻进程退出，并关闭主窗口
    assert api._db.get_kv(updater.QUIT_KEY)["reason"] == "update"
    for _ in range(100):
        if destroyed:
            break
        time.sleep(0.02)
    assert destroyed


def test_api_install_falls_back_without_breakaway(api, monkeypatch):
    api.check_update(False)
    _wait(api, api.download_update())
    calls = []

    def popen(cmd, creationflags, **kw):
        calls.append(creationflags)
        if creationflags & updater.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("job does not allow breakaway")

    monkeypatch.setattr(system, "install_mode", lambda: "installed")
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    assert api.install_update()["status"] == "installing"
    assert len(calls) == 2 and not calls[1] & updater.CREATE_BREAKAWAY_FROM_JOB


def test_api_install_requires_download(api, monkeypatch):
    api.check_update(False)
    monkeypatch.setattr(system, "install_mode", lambda: "installed")
    with pytest.raises(ValueError):
        api.install_update()
    assert api._db.get_kv(updater.QUIT_KEY) is None


def test_api_download_job_error_on_bad_checksum(api, server):
    Fake.routes["/update.json"] = (200, json.dumps(manifest(server, sha="cd" * 32)).encode())
    api.check_update(True)
    st = _wait(api, api.download_update())
    assert st["state"] == "error" and "sha256" in st["error"]


def test_api_skip_update(api):
    api.check_update(False)
    assert api.skip_update("v9.1.0") is None
    assert config.load().update.skipped_version == "9.1.0"
    assert api.check_update(False)["skipped"] is True
    with pytest.raises(ValueError):
        api.skip_update("latest")
    # 空串 / null 清除已跳过的版本（与 Android、docs/ui-api.md 一致）
    for clear in ("", None):
        api.skip_update("9.1.0")
        assert api.skip_update(clear) is None
        assert config.load().update.skipped_version == ""


def test_update_settings_validated(api):
    cfg = api.get_settings()["config"]["update"]
    assert cfg["auto_check"] is True and cfg["skipped_version"] == ""
    api.save_settings({"update": {"auto_check": False, "skipped_version": " v1.2.3 "}})
    assert config.load().update.auto_check is False and config.load().update.skipped_version == "1.2.3"
    for bad in ({"auto_check": "yes"}, {"skipped_version": "abc"}, {"manifest_url": "ftp://x"},
                {"manifest_url": 3}):
        with pytest.raises(ValueError):
            api.save_settings({"update": bad})
    with pytest.raises(ValueError):
        api.save_settings({"update": "on"})


def test_update_defaults_and_example():
    cfg = config.UpdateConfig()
    assert (cfg.auto_check, cfg.skipped_version, cfg.manifest_url) == (True, "", "")
    text = config.EXAMPLE.read_text(encoding="utf-8")
    assert "[update]" in text and "auto_check = true" in text and 'manifest_url = ""' in text


# ---- 常驻进程 ----


def _core(home, db):
    core = app_mod.App.__new__(app_mod.App)
    core.cfg, core.home, core.db = config.load(), home, db
    core._started = time.time()
    notes = []
    core.tray = SimpleNamespace(notify=lambda msg, title="Umless", on_click=None: notes.append((msg, on_click)),
                                stop=lambda: notes.append(("stop", None)))
    core.open_ui = lambda page=None: notes.append(("open", page))
    return core, notes


def test_core_quit_flag(home, db):
    core, _ = _core(home, db)
    assert core._quit_requested() is False
    db.set_kv(updater.QUIT_KEY, {"reason": "update", "ts": core._started - 60})  # 上次残留的请求
    assert core._quit_requested() is False and db.get_kv(updater.QUIT_KEY) is None
    updater.request_core_quit(db)
    assert core._quit_requested() is True
    assert db.get_kv(updater.QUIT_KEY) is None  # 只响应一次


def test_core_watch_exits_on_quit_request(home, db, monkeypatch):
    core, notes = _core(home, db)
    core._stop = threading.Event()
    core._cfg_mtime = core._mtime()
    core.pipeline = object()
    monkeypatch.setattr(core._stop, "wait", lambda s: False)  # 不真的等 2 秒
    updater.request_core_quit(db)
    core._watch()  # 看到退出请求后返回
    assert ("stop", None) in notes


def test_core_notifies_once_per_version(home, db, server):
    core, notes = _core(home, db)
    core._check_update()
    assert len(notes) == 1 and "9.1.0" in notes[0][0]
    notes[0][1]()  # 点击通知：打开设置的关于页
    assert notes[-1] == ("open", "settings/about")
    core._check_update()
    assert len([n for n in notes if "9.1.0" in str(n[0])]) == 1  # 同一版本只通知一次
    Fake.routes["/update.json"] = (200, json.dumps(manifest(server, version="9.2.0")).encode())
    updater.check(core.cfg, db, force=True)
    core.cfg.update.skipped_version = "9.2.0"
    core._check_update()  # 跳过的版本不通知
    assert not any("9.2.0" in str(n[0]) for n in notes)
    core.cfg.update.skipped_version = ""
    core._check_update()
    assert any("9.2.0" in str(n[0]) for n in notes)


def test_core_check_failure_is_silent(home, db, server):
    Fake.routes["/update.json"] = (503, b"")
    core, notes = _core(home, db)
    core._check_update()
    assert notes == [] and db.get_kv(updater.STATE_KEY)["error"]


# ---- 打包后的启动命令 ----


def test_launch_command_source_vs_frozen(tmp_path, monkeypatch):
    cmd = system.launch_command("ui", "--page", "report")
    assert cmd[1:] == ["-m", "umless", "ui", "--page", "report"]
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in system.child_env()

    app = tmp_path / "Umless"
    app.mkdir()
    (app / "Umless.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "umless-cli.exe"))  # 从控制台版调用也启动 Umless.exe
    assert system.launch_command("ui") == [str(app / "Umless.exe"), "ui"]
    assert system.launch_command() == [str(app / "Umless.exe")]
    assert system.child_env()["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert system._autostart_command() == str(app / "Umless.exe")


def test_package_dir_frozen(tmp_path, monkeypatch):
    assert config._package_dir() == Path(config.__file__).resolve().parent
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert config._package_dir() == tmp_path / "umless"


def test_tray_balloon_click_runs_callback():
    """点击托盘通知气泡（NIN_BALLOONUSERCLICK）时执行 notify 传入的回调，其余消息照常交给 pystray。"""
    from pystray._util import win32

    from umless import tray as tray_mod

    tr = tray_mod.Tray(lambda page: None, lambda: False, lambda: None, ".", "x.log", lambda: None)
    tr.icon.notify = lambda *a: None  # 不真的弹通知
    tr.icon._menu_handle = None  # 托盘没有真的运行：交给 pystray 的消息照常处理（忽略）
    clicked = []
    tr.notify("Umless 9.1.0 可用，点击查看", on_click=lambda: clicked.append(1))
    handler = tr.icon._message_handlers[win32.WM_NOTIFY]
    handler(0, tray_mod.NIN_BALLOONUSERCLICK)
    handler(0, tray_mod.NIN_BALLOONUSERCLICK)  # 回调只执行一次
    assert clicked == [1]
    tr.notify("已就绪")  # 新通知没有回调：替换掉旧的
    handler(0, tray_mod.NIN_BALLOONUSERCLICK)
    assert clicked == [1]
