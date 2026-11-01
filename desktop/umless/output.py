"""输出：写入剪贴板，可选自动粘贴（Ctrl+V）并恢复原剪贴板；获取前台应用名。

默认不恢复剪贴板（restore_clipboard=False）：不管有没有点中输入框，识别结果都留在剪贴板里。
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import psutil
import pyperclip
from pynput.keyboard import Controller, Key

log = logging.getLogger(__name__)
_keyboard = Controller()
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

UMLESS_TITLE = "Umless"  # 与 window.TITLE 一致
_PYTHON_EXES = {"python.exe", "pythonw.exe"}


def foreground_app() -> str | None:
    """返回前台窗口的进程名（如 WeChat.exe），用于后续按场景统计。"""
    try:
        hwnd = _user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return None


def foreground_is_umless() -> bool:
    """前台是不是 Umless 自己的主窗口（标题 Umless 且属于 python / pythonw 进程，判定同 window._main_window）。

    引导页会让用户在主窗口的输入框里按住热键试说，这类输入照常粘贴，但不写历史、不算场景。
    """
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(16)
        _user32.GetWindowTextW(hwnd, buf, 16)
        if buf.value != UMLESS_TITLE:
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower() in _PYTHON_EXES
    except Exception:
        return False


def deliver(text: str, mode: str, restore_clipboard: bool) -> None:
    previous = None  # 只有 restore_clipboard=True 时才读取旧内容；为 None 时结果一直留在剪贴板
    if mode == "paste" and restore_clipboard:
        try:
            previous = pyperclip.paste()  # 非文本内容（如图片）读到的是空串，不做恢复
        except pyperclip.PyperclipException:
            previous = None

    pyperclip.copy(text)
    if mode != "paste":
        return

    time.sleep(0.05)  # 等剪贴板写入生效
    with _keyboard.pressed(Key.ctrl):
        _keyboard.tap("v")

    if previous:
        # 目标应用可能异步读取剪贴板，延迟恢复，避免把旧内容粘出去
        def _restore() -> None:
            time.sleep(0.6)
            try:
                if pyperclip.paste() == text:
                    pyperclip.copy(previous)
            except pyperclip.PyperclipException:
                log.warning("恢复剪贴板失败")

        threading.Thread(target=_restore, daemon=True).start()
