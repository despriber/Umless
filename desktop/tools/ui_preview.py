"""主窗口 UI 预览：用 pywebview 加载前端 + MockApi，用于独立开发与截图自检。

在 desktop 目录下运行：
    uv run python tools/ui_preview.py                                  # 打开预览窗口
    uv run python tools/ui_preview.py --page report --theme dark       # 直接打开某页，强制深色
    uv run python tools/ui_preview.py --page history --shot .testhome/ui-shots/history.png
    uv run python tools/ui_preview.py --page practice --eval "document.querySelector('[data-start]').click()" \
        --wait 2 --shot .testhome/ui-shots/rec.png                     # 执行 JS 切换状态后截图
    uv run python tools/ui_preview.py --page report --full --shot .testhome/ui-shots/report.png
                                                                       # 逐屏滚动截图 report-1.png、report-2.png…
    uv run python tools/ui_preview.py --suite                          # 所有页面 × 浅/深色批量截图

其他选项：--empty（无数据，检查空状态）、--fresh（未完成引导）、--core-off（常驻进程未运行）、
--size 1180x780、--clip x,y,w,h（只截取某区域，CSS 像素）。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

# 必须在创建任何窗口之前设置 DPI 感知（Per-Monitor V2），否则坐标与截图按逻辑像素缩放
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:  # noqa: BLE001
    pass

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))

import webview  # noqa: E402
from PIL import Image, ImageGrab  # noqa: E402

from umless.ui.mock_api import MockApi  # noqa: E402

INDEX = DESKTOP / "umless" / "ui" / "web" / "index.html"
SHOTS = Path(os.environ.get("UMLESS_SHOTS", DESKTOP / ".testhome" / "ui-shots"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------------ 截图（PrintWindow，不受遮挡影响）

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindowDC.restype = wintypes.HDC
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _client_box(hwnd) -> tuple[int, int, int, int]:
    """客户区在屏幕上的物理像素坐标 (left, top, right, bottom)。"""
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    return pt.x, pt.y, pt.x + cr.right, pt.y + cr.bottom


def capture(hwnd) -> Image.Image:
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    ok = user32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT：可截到 WebView2 的合成内容
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    left, top, right, bottom = _client_box(hwnd)
    img = img.crop((left - wr.left, top - wr.top, right - wr.left, bottom - wr.top))
    if not ok or img.getextrema() == ((0, 0), (0, 0), (0, 0)):
        img = ImageGrab.grab(bbox=_client_box(hwnd), all_screens=True)  # 兜底：直接截屏
    return img


# ------------------------------------------------------------------ 批量截图清单

def _click(sel: str) -> str:
    return f"(document.querySelector({sel!r}) || {{click(){{}}}}).click()"


# (名称, 路由, [JS], 等待秒数, 是否逐屏滚动)
SUITE: list[tuple[str, str, list[str], float, bool]] = [
    ("overview", "overview", [], 1.2, True),
    ("report", "report", [], 1.2, True),
    ("report-today", "report", [_click(".date-item.is-today")], 1.0, False),
    ("report-habits", "report/habits", [], 1.0, False),
    ("practice", "practice", [], 1.0, True),
    ("practice-recording", "practice", [_click("[data-start]")], 2.6, False),
    ("practice-result", "practice/result/3", [], 1.0, True),
    ("history", "history", [], 1.0, False),
    ("history-expanded", "history", [_click(".hist-item .hist-head")], 0.8, False),
    ("settings-general", "settings/general", [], 0.8, False),
    ("settings-asr", "settings/asr", [], 0.8, False),
    ("settings-polish", "settings/polish", [], 0.8, True),
    ("settings-scenes", "settings/scenes", [], 0.8, True),
    ("settings-lexicon", "settings/lexicon", [], 0.8, False),
    ("settings-coach", "settings/coach", [], 0.8, False),
    ("settings-privacy", "settings/privacy", [], 0.8, False),
    ("settings-about", "settings/about", [], 0.8, False),
    ("onboarding-welcome", "onboarding/welcome", [], 0.8, False),
    ("onboarding-model", "onboarding/model", [], 0.8, False),
    ("onboarding-llm", "onboarding/llm", [], 0.8, False),
    ("onboarding-mic", "onboarding/mic", [_click("[data-mic-test]")], 2.0, False),
    ("onboarding-hotkey", "onboarding/hotkey", [], 0.8, False),
    ("onboarding-done", "onboarding/done", [], 0.8, False),
]


def _eval(window, js: str):
    return window.evaluate_js(js)


def wait_ready(window, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if _eval(window, "!!(window.__umless && window.__umless.ready)"):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    raise RuntimeError("前端未在限定时间内就绪")


def shoot(window, hwnd, path: Path, full: bool, clip: tuple[int, int, int, int] | None) -> list[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    scale = user32.GetDpiForWindow(hwnd) / 96
    out: list[Path] = []
    if not full:
        img = capture(hwnd)
        if clip:
            x, y, w, h = (round(v * scale) for v in clip)
            img = img.crop((x, y, x + w, y + h))
        img.save(path)
        return [path]
    i = 0
    while True:
        i += 1
        _eval(window, f"window.__umless.scrollPage({i - 1})")
        time.sleep(0.7)
        capture(hwnd)  # 第一次 PrintWindow 可能拿到滚动前的旧帧，丢弃
        time.sleep(0.15)
        p = path.with_name(f"{path.stem}-{i}{path.suffix}")
        capture(hwnd).save(p)
        out.append(p)
        if _eval(window, "window.__umless.atBottom()") or i >= 6:
            break
    _eval(window, "window.__umless.scrollPage(0)")
    return out


def report_errors(window) -> None:
    """打印前端运行时错误（window.__umless.errors）。"""
    try:
        errs = _eval(window, "JSON.stringify(window.__umless.errors || [])")
        import json
        errs = json.loads(errs or "[]")
    except Exception:  # noqa: BLE001
        return
    if errs:
        print("前端错误：", *errs, sep="\n  ", file=sys.stderr)


def run(window, args) -> None:
    window.events.loaded.wait(20)
    wait_ready(window)
    hwnd = user32.FindWindowW(None, window.title)

    def goto(route: str, theme: str | None) -> None:
        if theme:
            _eval(window, f"window.__umless.setTheme({theme!r})")
        if route:
            _eval(window, f"window.__umless.go({route!r})")

    try:
        if args.suite:
            themes = [args.theme] if args.theme else ["light", "dark"]
            only = set(args.only.split(",")) if args.only else None
            for theme in themes:
                for name, route, js, wait, full in SUITE:
                    if only and not any(name.startswith(o) for o in only):
                        continue
                    _eval(window, "window.__umless.reset && window.__umless.reset()")
                    goto(route, theme)
                    time.sleep(0.6)
                    for code in js:
                        _eval(window, code)
                    time.sleep(wait)
                    files = shoot(window, hwnd, SHOTS / f"{name}-{theme}.png", full and not args.no_full, None)
                    print("截图：", ", ".join(str(f) for f in files))
                    _eval(window, "window.__umless.reset && window.__umless.reset()")
            report_errors(window)
            window.destroy()
            return

        goto(args.page, args.theme)
        if args.eval:
            time.sleep(0.6)
            for code in args.eval:
                _eval(window, code)
        if args.shot:
            time.sleep(args.wait)
            clip = tuple(int(v) for v in args.clip.split(",")) if args.clip else None
            files = shoot(window, hwnd, Path(args.shot), args.full, clip)
            print("截图：", ", ".join(str(f) for f in files))
            report_errors(window)
            window.destroy()
    except Exception as e:  # noqa: BLE001
        print("预览出错：", e, file=sys.stderr)
        if args.shot or args.suite:
            window.destroy()


def main() -> None:
    ap = argparse.ArgumentParser(description="Umless 主窗口 UI 预览（MockApi）")
    ap.add_argument("--page", default="", help="启动后打开的路由，如 report、settings/polish、onboarding/llm")
    ap.add_argument("--theme", choices=["light", "dark", "system"], help="强制主题")
    ap.add_argument("--shot", help="截图输出路径（png），截完自动关闭")
    ap.add_argument("--full", action="store_true", help="逐屏滚动截图，输出 name-1.png、name-2.png…")
    ap.add_argument("--clip", help="只截取区域 x,y,w,h（CSS 像素）")
    ap.add_argument("--eval", action="append", help="导航后执行的 JS，可重复")
    ap.add_argument("--wait", type=float, default=1.2, help="截图前等待秒数")
    ap.add_argument("--suite", action="store_true", help="批量截图所有页面到 .testhome/ui-shots/")
    ap.add_argument("--only", help="配合 --suite，只截名称以这些前缀开头的（逗号分隔）")
    ap.add_argument("--no-full", action="store_true", help="配合 --suite，不做逐屏滚动")
    ap.add_argument("--empty", action="store_true", help="无数据模式")
    ap.add_argument("--fresh", action="store_true", help="未完成新手引导")
    ap.add_argument("--core-off", action="store_true", help="模拟常驻进程未运行")
    ap.add_argument("--size", default="1180x780", help="窗口大小（逻辑像素）")
    ap.add_argument("--debug", action="store_true", help="打开开发者工具")
    ap.add_argument("--real", action="store_true", help="使用真实后端 umless.webapi.Api（数据来自 UMLESS_HOME）")
    args = ap.parse_args()

    if args.real:  # 真实后端：数据来自 UMLESS_HOME（建议指向测试目录）
        from umless.webapi import Api

        api = Api()
    else:
        api = MockApi(onboarded=not args.fresh, core_running=not args.core_off, empty=args.empty,
                      theme=args.theme if args.theme in ("light", "dark") else "system")
    width, height = (int(v) for v in args.size.lower().split("x"))
    dark = args.theme == "dark"
    window = webview.create_window(
        f"Umless 预览 {os.getpid()}", str(INDEX), js_api=api, width=width, height=height,
        min_size=(960, 640), background_color="#141618" if dark else "#f7f8f9",
        focus=not (args.shot or args.suite),
    )
    webview.start(run, (window, args), debug=args.debug, private_mode=True)


if __name__ == "__main__":
    main()
