"""下载源（spec/update.md 第 2 节）：GitHub 直连或镜像，识别模型下载与应用更新共用。

只有 github.com 上的地址才会换源，镜像地址 = network.mirror_url + 原地址；其他地址（如自定义的更新清单）始终直连。
network.download_source：github 只直连、mirror 只走镜像；auto 先试上次成功的源（SQLite kv download_source_last，
没有记录时先走镜像），失败再换另一个。任何模式下载成功（模型、安装包校验 sha256 通过，清单解析成功）
都会记下这次用的源；「测试连接」也会把结果更好的源记下来。
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import httpx

from . import __version__
from .config import DEFAULT_MIRROR_URL, NetworkConfig, normalize_mirror_url

log = logging.getLogger(__name__)

GITHUB = "github"
MIRROR = "mirror"
SOURCES = (GITHUB, MIRROR)
LAST_KEY = "download_source_last"  # 最近一次下载成功用的源
GITHUB_PREFIX = "https://github.com/"
PROBE_TIMEOUT_S = 8  # 测试连接：每个源的超时
PROBE_BYTES = 1024  # 测试连接：只请求前 1 KiB


def _net(cfg) -> NetworkConfig:
    net = getattr(cfg, "network", None)
    return net if isinstance(net, NetworkConfig) else NetworkConfig()


def mirror_url(cfg) -> str:
    try:
        return normalize_mirror_url(_net(cfg).mirror_url)
    except ValueError:
        return DEFAULT_MIRROR_URL


def preferred(db) -> str:
    """auto 模式先试的源：上次成功的源，没有记录时为镜像。"""
    try:
        last = db.get_kv(LAST_KEY) if db is not None else None
    except Exception:  # 数据库不可用时按没有记录处理
        log.exception("读取 %s 失败", LAST_KEY)
        last = None
    return last if last in SOURCES else MIRROR


def order(cfg, db) -> list[str]:
    """按顺序尝试的源。"""
    mode = _net(cfg).download_source
    if mode in SOURCES:
        return [mode]
    first = preferred(db)
    return [first, GITHUB if first == MIRROR else MIRROR]


def is_github(url: str) -> bool:
    return url.startswith(GITHUB_PREFIX)


def via(url: str, source: str, cfg) -> str:
    """原地址经指定的源访问时的实际地址。"""
    return mirror_url(cfg) + url if source == MIRROR and is_github(url) else url


def candidates(url: str, cfg, db) -> list[tuple[str, str | None]]:
    """按顺序尝试的 (实际地址, 源)。不是 GitHub 地址时只有原地址，源为 None。"""
    if not is_github(url):
        return [(url, None)]
    return [(via(url, s, cfg), s) for s in order(cfg, db)]


def succeeded(db, source: str | None) -> None:
    """下载成功后记下用的源（auto 模式下次先试它）。记录失败不影响下载结果。"""
    if db is None or source not in SOURCES:
        return
    try:
        if db.get_kv(LAST_KEY) != source:
            db.set_kv(LAST_KEY, source)
            log.info("下载源切换为 %s", source)
    except Exception:
        log.exception("记录下载源失败")


# ---------------------------------------------------------------- 测试连接


def _reason(e: Exception) -> str:
    """失败原因：原始信息（不翻译），如 HTTP 403、timed out、connection reset。"""
    if isinstance(e, httpx.TimeoutException):
        return "timed out"
    text = str(e).strip() or e.__class__.__name__
    return text if len(text) <= 160 else text[:157] + "…"


def probe(url: str, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """请求 url 的前 1 KiB（Range，接受 200 / 206，跟随跳转）→ {"ok", "ms", "error"}，不抛出网络错误。

    读到了正文数据才算通（只有响应头不算）；ms 为到读到数据为止的耗时，失败时为 None。
    """
    t0 = time.perf_counter()
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout,
                          headers={"Range": f"bytes=0-{PROBE_BYTES - 1}", "User-Agent": f"Umless/{__version__}"}) as r:
            if r.status_code not in (200, 206):
                return {"ok": False, "ms": None, "error": f"HTTP {r.status_code}"}
            got = False
            for chunk in r.iter_bytes(PROBE_BYTES):  # 拿到第一块数据就够了；服务器忽略 Range 时也不会下载整个文件
                if chunk:
                    got = True
                    break
            if not got:
                return {"ok": False, "ms": None, "error": "empty response"}
    except (httpx.HTTPError, OSError) as e:
        return {"ok": False, "ms": None, "error": _reason(e)}
    return {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "error": None}


def probe_sources(cfg, db, url: str, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """同时探测 GitHub 直连与镜像（url 为 GitHub 原地址）。

    返回 {"github": {...}, "mirror": {...}, "preferred": ...}，每项为 probe() 的结果。preferred 为 auto 模式之后先试的源：
    两个都通时取更快的（一样快时保持原来的），只有一个通时取它，都不通时为原来记录的源；有源通时写入 kv。
    """
    urls = {GITHUB: url, MIRROR: mirror_url(cfg) + url}
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="probe")
    futures = {s: pool.submit(probe, u, timeout) for s, u in urls.items()}
    result = {}
    deadline = time.monotonic() + timeout * 2 + 2  # 跳转多时 httpx 的超时按每一步计算，这里再兜一个总时限
    for s, f in futures.items():
        try:
            result[s] = f.result(timeout=max(0.1, deadline - time.monotonic()))
        except FutureTimeout:
            result[s] = {"ok": False, "ms": None, "error": "timed out"}
        except Exception as e:  # 不应发生；保证接口不抛出
            log.exception("测试下载源失败")
            result[s] = {"ok": False, "ms": None, "error": _reason(e)}
    pool.shutdown(wait=False)
    ok = [s for s in SOURCES if result[s]["ok"]]
    current = preferred(db)
    if len(ok) == 2 and result[GITHUB]["ms"] != result[MIRROR]["ms"]:
        best = min(ok, key=lambda s: result[s]["ms"])
    else:
        best = ok[0] if len(ok) == 1 else current
    succeeded(db, best if ok else None)
    result["preferred"] = best
    log.info("测试下载源：%s", result)
    return result
