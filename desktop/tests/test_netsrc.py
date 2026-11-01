"""下载源（spec/update.md 第 2 节）：直连 / 镜像的顺序与回退、上次成功的源、测试连接、清单 404（还没有发布版本）。

全部用本机 HTTP 假服务，不访问真实网络：把 netsrc.GITHUB_PREFIX 换成假服务上的 /gh/，
于是 /gh/… 被当作 GitHub 地址，镜像为 {假服务}/m/ + 原地址。
"""

import hashlib
import io
import json
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from umless import config, i18n, models, netsrc, updater
from umless.db import DB
from umless.webapi import Api

INSTALLER = b"MZ fake installer " * 500
SHA = hashlib.sha256(INSTALLER).hexdigest()


class Fake(BaseHTTPRequestHandler):
    """路径 → (状态码, 内容)；记录请求次数与 Range 头。路径 /slow 延迟 2 秒才应答。"""

    routes: dict = {}
    hits: dict = {}
    ranges: list = []

    def do_GET(self):
        Fake.hits[self.path] = Fake.hits.get(self.path, 0) + 1
        Fake.ranges.append(self.headers.get("Range"))
        if self.path == "/slow":
            time.sleep(2)
        status, body = Fake.routes.get(self.path, (404, b"not found"))
        rng = self.headers.get("Range")
        if status == 200 and rng and self.path != "/ignore-range":
            end = int(rng.split("-")[1])
            status, body = 206, body[: end + 1]
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:  # 客户端只读了一部分就断开
            pass

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch, server):
    monkeypatch.setenv("UMLESS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(netsrc, "GITHUB_PREFIX", f"{server}/gh/")
    monkeypatch.setattr(updater, "download_dir", lambda: tmp_path / "temp" / "umless-update")
    Fake.routes, Fake.hits, Fake.ranges = {}, {}, []
    config.data_dir()  # 创建数据目录
    i18n.set_language("zh")
    yield tmp_path / "home"
    i18n.set_language("zh")


@pytest.fixture
def db(home):
    d = DB(home / "umless.db")
    yield d
    d.close()


def gh(server, path):
    """假的 GitHub 原地址，以及它的镜像在假服务上的路径。"""
    url = f"{server}/gh/{path}"
    return url, f"/gh/{path}", f"/m/{url}"


def use(server, source="auto"):
    config.save_patch({"network": {"download_source": source, "mirror_url": f"{server}/m/"}})
    return config.load()


# ---- 配置 ----


def test_defaults_and_example():
    net = config.NetworkConfig()
    assert (net.download_source, net.mirror_url) == ("auto", "https://gh-proxy.com/")
    text = config.EXAMPLE.read_text(encoding="utf-8")
    assert "[network]" in text and 'download_source = "auto"' in text and 'mirror_url = "https://gh-proxy.com/"' in text


def test_normalize_mirror_url():
    assert config.normalize_mirror_url(" https://gh-proxy.com ") == "https://gh-proxy.com/"
    assert config.normalize_mirror_url("http://10.0.0.2:8080/gh/") == "http://10.0.0.2:8080/gh/"
    for bad in ("", "gh-proxy.com", "ftp://x/", "https://", "https://a b/", None, 3):
        with pytest.raises(ValueError):
            config.normalize_mirror_url(bad)


def test_bad_values_in_file_fall_back(home):
    config.ensure_file()
    path = config.config_path()
    path.write_text(path.read_text(encoding="utf-8").replace('download_source = "auto"', 'download_source = "ftp"')
                    .replace('mirror_url = "https://gh-proxy.com/"', 'mirror_url = "gh-proxy"'), encoding="utf-8")
    net = config.load().network
    assert (net.download_source, net.mirror_url) == ("auto", "https://gh-proxy.com/")
    config.save_patch({"network": {"mirror_url": "https://mirror.example.com"}})
    assert config.load().network.mirror_url == "https://mirror.example.com/"  # 读取时补结尾 /


def test_api_settings_validated(home):
    api = Api()
    try:
        assert api.get_settings()["config"]["network"] == {"download_source": "auto", "mirror_url": "https://gh-proxy.com/"}
        res = api.save_settings({"network": {"download_source": "github", "mirror_url": " https://ghfast.top "}})
        assert res["config"]["network"] == {"download_source": "github", "mirror_url": "https://ghfast.top/"}
        for bad in ({"download_source": "fast"}, {"mirror_url": "ghfast.top"}, {"mirror_url": 1}):
            with pytest.raises(ValueError):
                api.save_settings({"network": bad})
        with pytest.raises(ValueError):
            api.save_settings({"network": "github"})
    finally:
        api._close()


# ---- 顺序 ----


def test_order_by_mode(db):
    cfg = config.Config()
    assert netsrc.order(cfg, None) == ["mirror", "github"]  # 第一次：先镜像
    assert netsrc.order(cfg, db) == ["mirror", "github"]
    netsrc.succeeded(db, "github")
    assert db.get_kv(netsrc.LAST_KEY) == "github"
    assert netsrc.order(cfg, db) == ["github", "mirror"] and netsrc.preferred(db) == "github"
    cfg.network.download_source = "mirror"
    assert netsrc.order(cfg, db) == ["mirror"]
    cfg.network.download_source = "github"
    assert netsrc.order(cfg, db) == ["github"]
    netsrc.succeeded(db, None)  # 非 GitHub 地址：不记录
    assert db.get_kv(netsrc.LAST_KEY) == "github"


def test_custom_mirror_and_non_github(server):
    cfg = config.Config()
    cfg.network.mirror_url = "https://ghfast.top/"
    url = netsrc.GITHUB_PREFIX + "a/b/releases/download/v1/x.exe"
    assert netsrc.candidates(url, cfg, None)[0] == ("https://ghfast.top/" + url, "mirror")
    cfg.network.download_source = "mirror"
    assert netsrc.candidates("https://example.com/update.json", cfg, None) == [("https://example.com/update.json", None)]


# ---- 更新清单与安装包 ----


def manifest(asset_url):
    return {"schema": 1, "version": "9.1.0", "notes": {"zh": "修复", "en": "Fixes"},
            "assets": [{"platform": "windows-x64", "kind": "installer", "name": "Umless-Setup-9.1.0.exe",
                        "url": asset_url, "size": len(INSTALLER), "sha256": SHA}]}


def test_auto_falls_back_and_remembers(home, db, server):
    url, direct, mirror = gh(server, "u/releases/latest/download/update.json")
    Fake.routes = {mirror: (502, b"bad gateway"), direct: (200, json.dumps(manifest(url)).encode())}
    cfg = use(server)
    cfg.update.manifest_url = url
    state = updater.check(cfg, db, force=True)
    assert state["manifest"]["version"] == "9.1.0" and state["error"] is None
    assert Fake.hits == {mirror: 1, direct: 1}
    assert db.get_kv(netsrc.LAST_KEY) == "github"
    updater.check(cfg, db, force=True)  # 下次先试直连
    assert Fake.hits == {mirror: 1, direct: 2}


def test_fixed_modes_use_one_source(home, db, server):
    url, direct, mirror = gh(server, "u/update.json")
    Fake.routes = {mirror: (200, json.dumps(manifest(url)).encode()), direct: (500, b"oops")}
    cfg = use(server, "github")
    cfg.update.manifest_url = url
    assert updater.check(cfg, db, force=True)["error"]
    assert Fake.hits == {direct: 1}
    cfg = use(server, "mirror")
    cfg.update.manifest_url = url
    assert updater.check(cfg, db, force=True)["manifest"]["version"] == "9.1.0"
    assert Fake.hits == {direct: 1, mirror: 1}
    assert db.get_kv(netsrc.LAST_KEY) == "mirror"


def test_installer_download_uses_sources(home, db, server):
    url, direct, mirror = gh(server, "u/update.json")
    exe, exe_direct, exe_mirror = gh(server, "u/releases/download/v9.1.0/setup.exe")
    Fake.routes = {direct: (200, json.dumps(manifest(exe)).encode()), mirror: (200, json.dumps(manifest(exe)).encode()),
                   exe_mirror: (200, b"tampered" * 100), exe_direct: (200, INSTALLER)}
    cfg = use(server)
    cfg.update.manifest_url = url
    updater.check(cfg, db, force=True)
    assert db.get_kv(netsrc.LAST_KEY) == "mirror"
    path = updater.download(db, cfg=cfg)  # 镜像的文件校验不通过 → 换直连
    assert path.read_bytes() == INSTALLER
    assert Fake.hits[exe_mirror] == 1 and Fake.hits[exe_direct] == 1
    assert db.get_kv(netsrc.LAST_KEY) == "github"


def test_not_published_when_every_source_404(home, db, server):
    url, direct, mirror = gh(server, "u/releases/latest/download/update.json")
    cfg = use(server)
    cfg.update.manifest_url = url
    state = updater.check(cfg, db, force=True)
    assert state["error"] is None and state["not_published"] is True and state["manifest"] is None
    assert Fake.hits == {mirror: 1, direct: 1}
    info = updater.describe(cfg, db, state)
    assert info["not_published"] is True and info["error"] is None and info["available"] is False
    assert info["latest"] is None and info["checked_at"]
    assert db.get_kv(netsrc.LAST_KEY) is None  # 404 不算成功
    # 只有一个源 404、另一个出错：是检查失败，不是没有发布
    Fake.routes = {mirror: (500, b"oops")}
    state = updater.check(cfg, db, force=True)
    assert state["error"] and not state.get("not_published")
    info = updater.describe(cfg, db, state)
    assert info["not_published"] is False and info["error"]


def test_not_published_custom_url(home, db, server):
    cfg = use(server, "mirror")
    cfg.update.manifest_url = f"{server}/custom/update.json"  # 自定义地址只直连
    state = updater.check(cfg, db, force=True)
    assert state["not_published"] is True
    assert Fake.hits == {"/custom/update.json": 1}


def test_api_check_update_not_published(home, server):
    url, _, _ = gh(server, "u/update.json")
    use(server)
    config.save_patch({"update": {"manifest_url": url}})
    api = Api()
    try:
        info = api.check_update(True)
        assert info["not_published"] is True and info["error"] is None
    finally:
        api._close()


# ---- 识别模型 ----


def _archive(dirname):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        data = b"model"
        info = tarfile.TarInfo(f"{dirname}/model.onnx")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_model_download_falls_back_and_remembers(home, db, server, monkeypatch, tmp_path):
    data = _archive("tiny-model")
    spec = models.ModelSpec("tiny-model", hashlib.sha256(data).hexdigest(), 1)
    monkeypatch.setitem(models.MODELS, "tiny", spec)
    monkeypatch.setattr(models, "RELEASE", f"{server}/gh/asr-models")
    url, direct, mirror = gh(server, "asr-models/tiny-model.tar.bz2")
    assert models.url("tiny") == url
    Fake.routes = {direct: (200, data)}  # 镜像 404
    cfg = use(server)
    seen = []
    target = models.download("tiny", tmp_path / "models", lambda p, m: seen.append(m), cfg=cfg, db=db)
    assert (target / "model.onnx").read_bytes() == b"model"
    assert Fake.hits == {mirror: 1, direct: 1}
    assert db.get_kv(netsrc.LAST_KEY) == "github"
    assert any(mirror[len("/m/"):] in m for m in seen)  # 进度信息里显示实际地址
    # 只走镜像：失败后不再尝试直连
    cfg = use(server, "mirror")
    with pytest.raises(RuntimeError):
        models.download("tiny", tmp_path / "other", lambda p, m: None, cfg=cfg, db=db)
    assert Fake.hits == {mirror: 2, direct: 1}


# ---- 测试连接 ----


def test_probe_range_ok_and_errors(home, server):
    Fake.routes = {"/file": (200, b"x" * 5000), "/ignore-range": (200, b"y" * (4 << 20))}
    r = netsrc.probe(f"{server}/file")
    assert r["ok"] is True and isinstance(r["ms"], int) and r["error"] is None
    assert Fake.ranges[-1] == "bytes=0-1023"
    assert netsrc.probe(f"{server}/ignore-range")["ok"] is True  # 服务器忽略 Range 返回 200 也算通
    assert netsrc.probe(f"{server}/missing") == {"ok": False, "ms": None, "error": "HTTP 404"}
    refused = netsrc.probe("http://127.0.0.1:9/x", timeout=2)
    assert refused["ok"] is False and refused["ms"] is None and refused["error"]
    slow = netsrc.probe(f"{server}/slow", timeout=0.3)
    assert slow == {"ok": False, "ms": None, "error": "timed out"}  # 原始原因，不翻译
    Fake.routes["/empty"] = (200, b"")
    assert netsrc.probe(f"{server}/empty") == {"ok": False, "ms": None, "error": "empty response"}  # 只有响应头不算通


def test_probe_sources_parallel_and_preferred(home, db, server, monkeypatch):
    url, direct, mirror = gh(server, "asr-models/x.tar.bz2")
    Fake.routes = {direct: (200, b"z" * 2048)}
    cfg = use(server)
    res = netsrc.probe_sources(cfg, db, url)
    assert set(res) == {"github", "mirror", "preferred"}
    assert res["github"]["ok"] is True and res["mirror"] == {"ok": False, "ms": None, "error": "HTTP 404"}
    assert res["preferred"] == "github" and db.get_kv(netsrc.LAST_KEY) == "github"  # 只有一个通：记下它
    assert netsrc.order(cfg, db) == ["github", "mirror"]

    results = {}

    def fake_probe(u, timeout):
        time.sleep(0.5)
        return results["mirror" if u.startswith(f"{server}/m/") else "github"]

    monkeypatch.setattr(netsrc, "probe", fake_probe)
    ok = lambda ms: {"ok": True, "ms": ms, "error": None}  # noqa: E731
    bad = {"ok": False, "ms": None, "error": "HTTP 403"}
    # 两个都通：取更快的；两个源同时探测，总耗时约等于一次
    results.update(github=ok(400), mirror=ok(150))
    t0 = time.perf_counter()
    assert netsrc.probe_sources(cfg, db, url)["preferred"] == "mirror"
    assert time.perf_counter() - t0 < 0.9
    assert db.get_kv(netsrc.LAST_KEY) == "mirror"
    results.update(github=ok(200), mirror=ok(200))  # 一样快：保持原来的
    assert netsrc.probe_sources(cfg, db, url)["preferred"] == "mirror"
    results.update(github=bad, mirror=bad)  # 都不通：不改记录
    assert netsrc.probe_sources(cfg, db, url)["preferred"] == "mirror"
    assert db.get_kv(netsrc.LAST_KEY) == "mirror"


def test_api_test_download_sources(home, monkeypatch):
    seen = []

    def fake_probe(u, timeout):
        seen.append(u)
        return {"ok": False, "ms": None, "error": "boom"} if "gh-proxy" in u else {"ok": True, "ms": 12, "error": None}

    monkeypatch.setattr(netsrc, "probe", fake_probe)
    monkeypatch.setattr(netsrc, "GITHUB_PREFIX", "https://github.com/")
    api = Api()
    try:
        res = api.test_download_sources()
    finally:
        api._close()
    official = models.url("sensevoice")
    assert official.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/")
    assert sorted(seen) == sorted([official, "https://gh-proxy.com/" + official])
    assert res == {"github": {"ok": True, "ms": 12, "error": None},
                   "mirror": {"ok": False, "ms": None, "error": "boom"}, "preferred": "github"}
