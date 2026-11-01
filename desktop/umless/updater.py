"""应用更新，规范见 spec/update.md。

GitHub Releases 上的 update.json 是唯一的信息来源：读取清单 → 比较版本 → 下载安装包并校验 sha256 →
常驻进程与主窗口退出，安装程序静默安装后重新启动 Umless。不经过自建服务器，不上传任何数据。

检查结果缓存在 SQLite kv（update_state），常驻进程（定时检查、托盘通知）与主窗口进程（设置 → 关于）共用。
安装更新时主窗口进程写 kv core_quit，常驻进程在 _watch 循环里看到后退出。
"""

import hashlib
import logging
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from . import __version__, i18n, system
from .config import Config
from .db import DB, now_iso
from .i18n import t

log = logging.getLogger(__name__)

REPO = "despriber/Umless"  # 仓库改名时只改这里
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/update.json"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
MIRROR = "https://gh-proxy.com/"  # 与模型下载相同：先镜像，失败再直连
SCHEMA = 1
PLATFORM = "windows-x64"
KIND = "installer"  # 自动更新只用 Inno Setup 安装程序；portable 仅供手动下载

MANIFEST_TIMEOUT_S = 10
DOWNLOAD_TIMEOUT = httpx.Timeout(60, connect=20)  # 连接 20 秒；读取 60 秒没有数据视为失败
CHECK_EVERY_S = 24 * 3600  # 自动检查间隔（检查成功后）
RETRY_AFTER_ERROR_S = 3600  # 自动检查失败后多久再试
FIRST_CHECK_DELAY_S = 300  # 常驻进程启动 5 分钟后第一次检查

STATE_KEY = "update_state"  # 最近一次检查的结果
DOWNLOAD_KEY = "update_download"  # 已下载并校验过的安装包
NOTIFIED_KEY = "update_notified"  # 托盘已通知过的版本（每个版本只通知一次）
QUIT_KEY = "core_quit"  # 主窗口请求常驻进程退出（安装更新前）

INSTALLER_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


class UpdateError(Exception):
    """检查或下载更新失败；信息已按界面语言翻译。"""


# ---------------------------------------------------------------- 版本


def parse_version(text: str) -> tuple:
    """语义化版本 → 可比较的元组。正式版大于同号的预发布版（1.2.0 > 1.2.0-beta.1）。"""
    m = _VERSION_RE.match(str(text).strip())
    if not m:
        raise ValueError(f"不是有效的版本号：{text!r}")
    core = tuple(int(x) for x in m.group(1, 2, 3))
    if m.group(4) is None:
        return (core, 1, ())
    # 预发布标识逐段比较：数字段按数值且小于字母段
    pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split("."))
    return (core, 0, pre)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def is_valid_version(text) -> bool:
    try:
        parse_version(text)
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 清单


def manifest_url(cfg: Config) -> str:
    url = str(cfg.update.manifest_url or "").strip()  # 手改配置写成非字符串时按未设置处理
    return url if url.startswith(("http://", "https://")) else MANIFEST_URL


def sources(url: str) -> list[str]:
    """GitHub 地址先走 gh-proxy 再直连；自定义地址（测试、自建镜像）只直连。"""
    return [MIRROR + url, url] if url.startswith("https://github.com/") else [url]


def select_asset(assets, platform: str = PLATFORM, kind: str = KIND) -> dict | None:
    """取与本机平台、种类匹配的第一项；字段不全的项忽略。"""
    for a in assets if isinstance(assets, list) else []:
        if not isinstance(a, dict) or a.get("platform") != platform or a.get("kind") != kind:
            continue
        sha = str(a.get("sha256") or "").strip().lower()
        url = str(a.get("url") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not url.startswith(("http://", "https://")):
            continue
        name = Path(str(a.get("name") or url.rsplit("/", 1)[-1])).name
        size = a.get("size")
        return {"name": name, "url": url, "sha256": sha,
                "size": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None}
    return None


def parse_manifest(data) -> dict:
    """校验并精简 update.json，只保留本机需要的字段。schema 更大时仍按已知字段解析。"""
    if not isinstance(data, dict):
        raise UpdateError(t("update.bad_manifest", error="not an object"))
    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise UpdateError(t("update.bad_manifest", error=f"schema={schema!r}"))
    version = data.get("version")
    if not isinstance(version, str) or not is_valid_version(version):
        raise UpdateError(t("update.bad_manifest", error=f"version={version!r}"))
    notes = data.get("notes")
    notes = {k: v for k, v in notes.items() if isinstance(v, str)} if isinstance(notes, dict) else {}
    published = data.get("published_at")
    return {"schema": schema, "version": version.lstrip("v"), "notes": notes,
            "published_at": published if isinstance(published, str) else None,
            "asset": select_asset(data.get("assets"))}


def pick_notes(notes: dict, lang: str) -> str:
    """按界面语言选 zh / en，缺失时用另一种。"""
    other = "en" if lang == "zh" else "zh"
    return notes.get(lang) or notes.get(other) or ""


def fetch_manifest(url: str) -> dict:
    errors = []
    for src in sources(url):
        try:
            r = httpx.get(src, follow_redirects=True, timeout=MANIFEST_TIMEOUT_S,
                          headers={"User-Agent": f"Umless/{__version__}"})
            r.raise_for_status()
            return parse_manifest(r.json())
        except (httpx.HTTPError, ValueError) as e:  # 网络错误、非 JSON
            log.info("读取更新清单失败（%s）：%s", src, e)
            errors.append(str(e) or e.__class__.__name__)
        except UpdateError as e:  # 镜像返回了错误页面等，换下一个源
            log.info("更新清单格式有误（%s）：%s", src, e)
            errors.append(str(e))
    raise UpdateError(errors[-1] if errors else "no source")


# ---------------------------------------------------------------- 检查（带缓存）


def _fresh(state, url: str, now: float) -> bool:
    if not isinstance(state, dict) or state.get("url") != url or state.get("current") != __version__:
        return False
    ttl = RETRY_AFTER_ERROR_S if state.get("error") else CHECK_EVERY_S
    try:
        return 0 <= now - float(state.get("checked_ts") or 0) < ttl
    except (TypeError, ValueError):
        return False


def check(cfg: Config, db: DB, force: bool = False) -> dict:
    """返回检查状态（kv update_state 的内容），网络错误记在 error 里、不抛出。

    force=False 时：关闭了自动检查就只返回缓存；缓存未过期（成功 24 小时、失败 1 小时）也直接返回缓存。
    """
    state = db.get_kv(STATE_KEY)
    url = manifest_url(cfg)
    now = time.time()
    if not force and (not cfg.update.auto_check or _fresh(state, url, now)):
        return state if isinstance(state, dict) else {}
    try:
        new = {"manifest": fetch_manifest(url), "error": None}
        log.info("检查更新：最新版本 %s，当前 %s", new["manifest"]["version"], __version__)
    except UpdateError as e:
        log.warning("检查更新失败：%s", e)
        # 保留之前查到的清单：网络暂时不通时，已知的新版本仍然可以显示
        same = isinstance(state, dict) and state.get("url") == url
        new = {"manifest": state.get("manifest") if same else None, "error": str(e)}
    new.update(url=url, current=__version__, checked_at=now_iso(), checked_ts=now)
    db.set_kv(STATE_KEY, new)
    return new


def install_hint(mode: str | None = None) -> str | None:
    mode = mode or system.install_mode()
    if mode == "source":
        return t("update.hint_source")
    if mode == "portable":
        return t("update.hint_portable", url=RELEASES_URL)
    return None


def describe(cfg: Config, db: DB, state: dict | None) -> dict:
    """检查状态 → 界面接口 check_update 的返回结构（docs/ui-api.md「更新」）。"""
    state = state if isinstance(state, dict) else {}
    m = state.get("manifest") if isinstance(state.get("manifest"), dict) else {}
    latest = m.get("version")
    asset = m.get("asset") if isinstance(m.get("asset"), dict) else None
    try:
        available = bool(latest and asset and is_newer(latest, __version__))
    except ValueError:
        available = False
    mode = system.install_mode()
    error = state.get("error")
    return {
        "current": __version__,
        "latest": latest,
        "available": available,
        "skipped": bool(available and latest == str(cfg.update.skipped_version or "").strip().lstrip("v")),
        "notes": pick_notes(m.get("notes") or {}, i18n.current()) if latest else "",
        "published_at": m.get("published_at"),
        "size": asset.get("size") if asset else None,
        "checked_at": state.get("checked_at"),
        "error": t("update.check_failed", error=error) if error else None,
        "can_install": mode == "installed",
        "install_hint": install_hint(mode),
        "downloaded": bool(available and downloaded_installer(db, asset, latest)),
    }


def latest_asset(db: DB) -> tuple[str, dict]:
    """缓存里比当前版本新、且有本机安装包的更新；没有时抛出 UpdateError。"""
    state = db.get_kv(STATE_KEY)
    m = state.get("manifest") if isinstance(state, dict) else None
    try:
        ok = isinstance(m, dict) and isinstance(m.get("asset"), dict) and is_newer(m["version"], __version__)
    except (KeyError, ValueError, TypeError):
        ok = False
    if not ok:
        raise UpdateError(t("update.none"))
    return m["version"], m["asset"]


# ---------------------------------------------------------------- 下载


def download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "umless-update"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def downloaded_installer(db: DB, asset: dict | None, version: str | None) -> Path | None:
    """已下载的安装包（版本、sha256 与清单一致且文件还在）。按记录比对，不重新计算哈希。"""
    rec = db.get_kv(DOWNLOAD_KEY)
    if not asset or not isinstance(rec, dict) or rec.get("version") != version or rec.get("sha256") != asset["sha256"]:
        return None
    path = Path(rec.get("path") or "")
    if not path.is_file() or (asset.get("size") is not None and path.stat().st_size != asset["size"]):
        return None
    return path


def download(db: DB, progress: Callable[[float | None, str], None] | None = None) -> Path:
    """下载缓存里的新版本安装包到 %TEMP%\\umless-update\\，校验 sha256；不一致则删除并换下一个源。"""
    def report(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    version, asset = latest_asset(db)
    if path := downloaded_installer(db, asset, version):
        report(1.0, t("update.downloaded"))
        return path
    folder = download_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / asset["name"]
    for old in folder.glob("*"):  # 旧版本的安装包、上次没下完的临时文件
        if old.is_file() and old != target and old.suffix.lower() in (".exe", ".part"):
            old.unlink(missing_ok=True)
    part = target.with_name(target.name + ".part")
    error = t("update.download_failed", error="no source")
    for src in sources(asset["url"]):
        report(0.0, t("update.downloading_from", url=src))
        sha = hashlib.sha256()
        try:
            with httpx.stream("GET", src, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT,
                              headers={"User-Agent": f"Umless/{__version__}"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0) or asset.get("size") or 0
                done = 0
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 18):
                        f.write(chunk)
                        sha.update(chunk)
                        done += len(chunk)
                        if total:
                            report(min(done / total, 1.0) * 0.98,
                                   t("update.progress", done=round(done / 1048576, 1), total=round(total / 1048576, 1)))
        except (httpx.HTTPError, OSError) as e:
            log.warning("下载更新失败（%s）：%s", src, e)
            part.unlink(missing_ok=True)
            error = t("update.download_failed", error=str(e) or e.__class__.__name__)
            continue
        if sha.hexdigest() != asset["sha256"]:
            log.warning("更新安装包 sha256 不一致（%s）：%s", src, sha.hexdigest())
            part.unlink(missing_ok=True)
            error = t("update.checksum_failed")
            continue
        target.unlink(missing_ok=True)
        part.replace(target)
        db.set_kv(DOWNLOAD_KEY, {"version": version, "path": str(target), "sha256": asset["sha256"],
                                 "at": now_iso()})
        log.info("更新 %s 已下载：%s", version, target)
        report(1.0, t("update.downloaded"))
        return target
    raise UpdateError(error)


# ---------------------------------------------------------------- 安装


def verified_installer(db: DB) -> Path:
    """安装前重新计算一次 sha256，防止下载后被替换。"""
    version, asset = latest_asset(db)
    path = downloaded_installer(db, asset, version)
    if path is None:
        raise UpdateError(t("update.not_downloaded"))
    if sha256_of(path) != asset["sha256"]:
        path.unlink(missing_ok=True)
        db.set_kv(DOWNLOAD_KEY, None)
        raise UpdateError(t("update.checksum_failed"))
    return path


def install_command(installer: Path) -> list[str]:
    log_file = download_dir() / "install.log"
    return [str(installer), *INSTALLER_ARGS, f"/LOG={log_file}"]


DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def launch_installer(cmd: list[str]) -> None:
    """独立启动安装程序：Umless 退出后它继续运行。所在作业对象不允许脱离时退回普通的分离启动。"""
    kwargs = {"close_fds": True, "env": system.child_env(), "cwd": str(download_dir())}
    try:
        subprocess.Popen(cmd, creationflags=DETACHED | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        try:
            subprocess.Popen(cmd, creationflags=DETACHED, **kwargs)
        except OSError as e:
            raise UpdateError(t("update.launch_failed", error=e)) from None
    log.info("已启动安装程序：%s", cmd)


# ---------------------------------------------------------------- 常驻进程：退出请求、托盘通知


def request_core_quit(db: DB, reason: str = "update") -> None:
    db.set_kv(QUIT_KEY, {"reason": reason, "ts": time.time()})


def quit_requested(db: DB, since: float) -> bool:
    """常驻进程轮询：有在本进程启动之后写入的退出请求时返回 True 并清除；更早的请求是残留，直接清除。"""
    req = db.get_kv(QUIT_KEY)
    if not isinstance(req, dict):
        return False
    db.set_kv(QUIT_KEY, None)
    try:
        return float(req.get("ts") or 0) >= since
    except (TypeError, ValueError):
        return False


def should_notify(db: DB, info: dict) -> bool:
    """有可用更新、没被跳过、这个版本还没通知过时返回 True，并记下已通知。"""
    if not info.get("available") or info.get("skipped"):
        return False
    if db.get_kv(NOTIFIED_KEY) == info["latest"]:
        return False
    db.set_kv(NOTIFIED_KEY, info["latest"])
    return True
