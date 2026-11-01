"""主窗口进程：`umless ui [--page report]`。

单实例：已打开时只把目标页面写入 kv 并把窗口提到前台（前端每秒轮询 get_nav）。
关闭窗口即退出进程，释放 WebView2 占用的内存。
"""

import ctypes
import logging
import os
import threading
import winreg
from ctypes import wintypes
from pathlib import Path

from . import config, system
from .db import DB

log = logging.getLogger(__name__)

TITLE = "Umless"
INDEX = config.PACKAGE_DIR / "ui" / "web" / "index.html"  # 打包后在 _MEIPASS 下

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.LoadImageW.restype = wintypes.HANDLE
_user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                               ctypes.c_int, wintypes.UINT]
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = ctypes.c_ssize_t


def system_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _is_dark(theme: str) -> bool:
    return theme == "dark" or (theme not in ("light", "dark") and system_dark())


def _main_window(pid: int | None = None) -> int | None:
    """标题为 Umless 的可见主窗口。托盘图标的隐藏消息窗口同名，按可见性和尺寸排除。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        buf = ctypes.create_unicode_buffer(16)
        _user32.GetWindowTextW(hwnd, buf, 16)
        if buf.value != TITLE or not _user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if rect.right - rect.left > 100 and (pid is None or owner.value == pid):
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(visit, 0)
    return found[0] if found else None


def _bring_to_front() -> None:
    hwnd = _main_window()
    if hwnd:
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _user32.SetForegroundWindow(hwnd)


def _icon_path() -> Path:
    """用托盘同款图标生成 .ico，供窗口标题栏和任务栏使用。"""
    path = config.data_dir() / "umless.ico"
    if not path.exists():
        from .tray import save_ico

        save_ico(path)
    return path


def _decorate(dark: bool, *, icons: bool = True) -> None:
    """深色标题栏（DWMWA_USE_IMMERSIVE_DARK_MODE = 20）与窗口图标。"""
    hwnd = _main_window(os.getpid())
    if not hwnd:
        return
    try:
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        value = ctypes.c_int(1 if dark else 0)
        set_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # Windows 11 caption colour is COLORREF (0x00BBGGRR); older Windows ignores this attribute.
        caption = ctypes.c_uint(0x141512 if dark else 0xF0F1EE)
        set_attribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except OSError:
        pass
    if not icons:
        return
    try:
        ico = str(_icon_path())
        for size, which in ((16, 0), (32, 1)):  # ICON_SMALL / ICON_BIG
            h = _user32.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                _user32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
    except OSError:
        log.exception("设置窗口图标失败")


def _clear_web_cache(storage: Path) -> None:
    """启动前清掉 WebView2 的 HTTP 缓存与脚本缓存。

    界面文件都在本地，缓存没有收益；反而会在升级后继续执行旧版脚本（界面停在旧样子）。
    localStorage 等页面数据在别的目录，不受影响。
    """
    import shutil

    profile = storage / "EBWebView" / "Default"
    for name in ("Cache", "Code Cache"):
        shutil.rmtree(profile / name, ignore_errors=True)


def run(page: str | None = None) -> None:
    db = DB(config.data_dir() / "umless.db")
    if page:
        db.set_kv("nav", page)
    if not system.acquire_single_instance(system.UI_MUTEX):
        _bring_to_front()
        return
    db.close()

    # 必须在导入 pywebview 之前设置 Per-Monitor V2，否则高缩放屏幕上窗口按物理像素创建，显得很小
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

    import webview

    from .webapi import Api

    api = Api()
    cfg = config.load()
    dark = _is_dark(cfg.app.theme)
    window = webview.create_window(
        TITLE, str(INDEX), js_api=api, width=1180, height=780, min_size=(960, 640),
        background_color="#121514" if dark else "#eef1f0",
    )
    api._window = window  # 下划线开头：不会被 js_api 暴露给前端；pick_sound_file 用它弹文件选择框

    closed = threading.Event()

    def on_start() -> None:
        window.events.loaded.wait(20)
        if closed.is_set():
            return
        _decorate(dark)
        last_dark = dark
        while not closed.wait(1):
            current_dark = _is_dark(api._conf().app.theme)
            if current_dark != last_dark:
                _decorate(current_dark, icons=False)
                last_dark = current_dark

    window.events.closed += closed.set
    window.events.closed += api._close
    storage = config.data_dir() / "webview"
    _clear_web_cache(storage)
    webview.start(on_start, private_mode=False, storage_path=str(storage))
