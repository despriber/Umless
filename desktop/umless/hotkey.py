"""按住说话（push-to-talk）热键：键名解析、监听、录制。

热键写法（config.toml 的 [hotkey] key）：单个键如 caps_lock / right_ctrl / f8 / pause，
或"修饰键 + 主键"组合如 ctrl+space / alt+q / ctrl+shift+f1；未命名的键写作 vk_0xNN。

基于 pynput 的 Windows 低层键盘钩子，在 win32_event_filter 中直接按虚拟键码处理：
- 钩子线程只做判断和投递，回调在独立线程执行。打开麦克风要 100–250ms，若在钩子线程里做，
  会卡住全系统键盘输入，且 Windows 会静默移除超时的钩子。
- 普通键（CapsLock、F 键等）和组合键的主键会被拦截（系统和输入法都收不到），
  避免按住时切换大小写或触发应用快捷键；单键轻点（短于 min_hold_ms）时再把按键补发出去，保留原有功能。
- 单独的修饰键（如右 Ctrl）不拦截，否则会影响正常的组合键；按住期间按了别的键，
  说明是在用组合键（如右 Ctrl+C），取消这次录音。
- 涉及 Alt / Win 时注入一个屏蔽键，防止松开时激活窗口菜单栏或开始菜单导致粘贴失败。
"""

import ctypes
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from pynput import keyboard

log = logging.getLogger(__name__)

DEFAULT = "caps_lock"

# 修饰键：组合键里的写法 -> 左右两侧的虚拟键码
MODIFIERS = {"ctrl": (0xA2, 0xA3), "alt": (0xA4, 0xA5), "shift": (0xA0, 0xA1), "win": (0x5B, 0x5C)}
_MOD_OF_VK = {vk: mod for mod, vks in MODIFIERS.items() for vk in vks}
# 钩子有时报告不分左右的修饰键码
_MOD_OF_VK.update({0x10: "shift", 0x11: "ctrl", 0x12: "alt"})

# 键名 -> 虚拟键码
KEYMAP = {
    "caps_lock": 0x14, "scroll_lock": 0x91, "num_lock": 0x90, "pause": 0x13, "print_screen": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "apps": 0x5D,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08, "esc": 0x1B,
    "backquote": 0xC0, "minus": 0xBD, "equal": 0xBB, "bracket_left": 0xDB, "bracket_right": 0xDD,
    "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "left_ctrl": 0xA2, "right_ctrl": 0xA3, "left_alt": 0xA4, "right_alt": 0xA5,
    "left_shift": 0xA0, "right_shift": 0xA1, "left_win": 0x5B, "right_win": 0x5C,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"num{d}": 0x60 + d for d in range(10)},
}
_NAME_OF_VK = {vk: name for name, vk in KEYMAP.items()}
_NAME_OF_VK.update({0x10: "left_shift", 0x11: "left_ctrl", 0x12: "left_alt"})
# 单独按下就是在打字的键：不能直接作为热键，必须配合修饰键
TYPING = {k for k in KEYMAP if len(k) == 1} | {
    "space", "tab", "enter", "backspace", "esc", "backquote", "minus", "equal", "bracket_left",
    "bracket_right", "backslash", "semicolon", "quote", "comma", "period", "slash",
}

_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
_LLKHF_INJECTED = 0x10
_KEYEVENTF_KEYUP = 0x0002
# 未分配的虚拟键码，用作 Alt / Win 的"菜单屏蔽键"（AutoHotkey 的做法）
_MASK_VK = 0xE8


class HotkeyError(ValueError):
    """热键写法不合法；code 对应 i18n 的 cfg.hotkey_<code>。"""

    def __init__(self, code: str, spec: str) -> None:
        super().__init__(f"{code}: {spec}")
        self.code, self.spec = code, spec


@dataclass(frozen=True)
class Hotkey:
    vk: int  # 主键
    mods: frozenset[str]  # 需要同时按住的修饰键（ctrl / alt / shift / win，不分左右）
    spec: str  # 规范写法，如 ctrl+space

    @property
    def modifier_only(self) -> bool:
        """单独一个修饰键（如 right_ctrl）。"""
        return not self.mods and self.vk in _MOD_OF_VK

    @property
    def masked(self) -> bool:
        """按下时需要注入屏蔽键：涉及 Alt / Win，松开时会激活菜单栏或开始菜单。"""
        return bool({"alt", "win"} & (self.mods | {_MOD_OF_VK.get(self.vk)}))


def _key_name(vk: int) -> str:
    return _NAME_OF_VK.get(vk) or f"vk_0x{vk:02x}"


def parse(spec: str) -> Hotkey:
    """解析热键写法并规范化；不合法时抛出 HotkeyError。"""
    text = str(spec or "").strip().lower().replace(" ", "")
    parts = text.split("+")
    if not text or text.endswith("+"):
        raise HotkeyError("empty", str(spec))  # 如 "ctrl+"：还没选主键
    *mods, main = parts
    if any(m not in MODIFIERS for m in mods) or len(set(mods)) != len(mods):
        raise HotkeyError("bad", str(spec))
    if main in MODIFIERS:
        raise HotkeyError("mod_main", str(spec))  # 只由修饰键组成，如 ctrl+shift
    if m := re.fullmatch(r"vk_(?:0x)?([0-9a-f]{1,2})", main):
        vk = int(m.group(1), 16)
        if not 0 < vk < 0xFF:
            raise HotkeyError("bad", str(spec))
    elif main in KEYMAP:
        vk = KEYMAP[main]
    else:
        raise HotkeyError("bad", str(spec))
    if mods and vk in _MOD_OF_VK:
        raise HotkeyError("mod_main", str(spec))  # 只由修饰键组成的组合，如 ctrl+shift
    name = _key_name(vk)
    if not mods and name in TYPING:
        raise HotkeyError("typing", str(spec))  # 单独的字母、空格等，按住说话会影响打字
    ordered = [m for m in MODIFIERS if m in mods]
    return Hotkey(vk, frozenset(mods), "+".join([*ordered, name]))


def normalize(spec: str) -> str:
    return parse(spec).spec


def _tap(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


class PushToTalk:
    def __init__(
        self,
        key_spec: str,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        min_hold_ms: int = 300,
        on_cancel: Callable[[], None] | None = None,
    ):
        self.key = parse(key_spec)
        self._vk = self.key.vk
        self._combo = bool(self.key.mods)
        # 单独的修饰键不拦截；其余（普通键、组合键的主键）拦截
        self._suppress = not self.key.modifier_only
        self._on_down = on_down
        self._on_up = on_up
        self._on_cancel = on_cancel or on_up
        self._min_hold_s = min_hold_ms / 1000
        self._held = False
        self._canceled = False
        self._down_at = 0.0
        self._mods_down: set[int] = set()  # 当前按住的修饰键（物理按键，不含注入的）
        self._events: queue.Queue = queue.Queue()
        self._listener = keyboard.Listener(win32_event_filter=self._filter)

    def start(self) -> None:
        threading.Thread(target=self._dispatch, daemon=True).start()
        self._listener.start()
        log.info("热键监听已启动：%s", self.key.spec)

    def stop(self) -> None:
        self._listener.stop()
        self._events.put(None)

    @property
    def alive(self) -> bool:
        """键盘钩子线程是否在运行（写入常驻进程心跳用）。"""
        return self._listener.is_alive()

    def _mods_match(self) -> bool:
        held = {_MOD_OF_VK[vk] for vk in self._mods_down}
        return held == self.key.mods

    # 在钩子线程中执行：必须极快返回
    def _filter(self, msg: int, data) -> bool:
        if data.flags & _LLKHF_INJECTED:
            return True  # 我们自己补发的按键、粘贴用的 Ctrl+V，一律放行
        vk = data.vkCode
        down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
        if vk in _MOD_OF_VK:
            if down:
                self._mods_down.add(vk)
            else:
                self._mods_down.discard(vk)
        if vk != self._vk:
            # 单独修饰键作热键时，按住期间按了别的键：是在用组合键，取消录音
            if down and self._held and not self._suppress and not self._canceled and vk not in _MOD_OF_VK:
                self._canceled = True
                self._events.put("cancel")
            return True
        if down:
            if self._held:
                pass  # 按住时的自动重复
            elif self._combo and not self._mods_match():
                return True  # 修饰键不对（如热键是 ctrl+space，却按了空格或 ctrl+shift+space）：放行
            else:
                self._held = True
                self._canceled = False
                self._down_at = time.perf_counter()
                self._events.put("down")
        elif self._held:
            self._held = False
            if not self._canceled:
                self._events.put(("up", time.perf_counter() - self._down_at))
        else:
            return True  # 没有被我们接管的按下，对应的松开也放行
        if self._suppress:
            self._listener.suppress_event()  # 抛出异常，阻止事件继续传递
        return False  # 不再交给 pynput 的回调

    def _dispatch(self) -> None:
        while (event := self._events.get()) is not None:
            try:
                if event == "down":
                    if self.key.masked:
                        _tap(_MASK_VK)
                    self._on_down()
                elif event == "cancel":
                    self._on_cancel()
                else:
                    _, held_s = event
                    self._on_up()
                    if self._suppress and not self._combo and held_s < self._min_hold_s:
                        _tap(self._vk)  # 单键轻点：补发按键，保留原功能（如切换大小写）
            except Exception:
                log.exception("热键回调出错")


# ---------------------------------------------------------------- 录制（设置界面"按下新热键"）


class Capture:
    """临时安装一个键盘钩子，捕获用户按下的下一个键或组合键。

    录制期间拦截所有按键：后安装的低层钩子先收到事件，常驻进程的热键因此不会被触发，
    按下的键也不会传给其他程序。Esc 取消；超时自动结束。
    """

    def __init__(self) -> None:
        self._mods: list[int] = []  # 按下顺序
        self._seen_mods: set[int] = set()
        self._result: str | None = None
        self._error: HotkeyError | None = None
        self._done = threading.Event()
        self._listener: keyboard.Listener | None = None

    def capture(self, timeout_s: float = 15) -> str | None:
        """阻塞直到录到一个热键（返回规范写法）、取消或超时（返回 None）；不合法时抛出 HotkeyError。"""
        self._listener = keyboard.Listener(win32_event_filter=self._filter)
        self._listener.start()
        try:
            self._done.wait(timeout_s)
        finally:
            self._listener.stop()
        if self._error:
            raise self._error
        return self._result

    def cancel(self) -> None:
        self._done.set()

    def _finish(self, spec: str | None) -> None:
        if self._done.is_set():
            return
        if spec is not None:
            try:
                self._result = normalize(spec)
            except HotkeyError as e:
                self._error = e
        self._done.set()

    def _filter(self, msg: int, data) -> bool:
        if data.flags & _LLKHF_INJECTED:
            return True
        if not self._done.is_set():
            vk = data.vkCode
            down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
            if vk in _MOD_OF_VK:
                if down and vk not in self._mods:
                    self._mods.append(vk)
                    self._seen_mods.add(vk)
                elif not down:
                    if vk in self._mods:
                        self._mods.remove(vk)
                    # 只按了一个修饰键就松开：单独修饰键作热键（如右 Ctrl）
                    if not self._mods and len(self._seen_mods) == 1:
                        self._finish(_key_name(vk))
                    elif not self._mods:
                        self._seen_mods.clear()  # 只按了几个修饰键又全部松开：重新等待
            elif down:
                if vk == 0x1B and not self._mods:
                    self._finish(None)  # Esc：取消
                else:
                    mods = []
                    for m in self._mods:
                        if (name := _MOD_OF_VK[m]) not in mods:
                            mods.append(name)
                    self._finish("+".join([*mods, _key_name(vk)]))
        self._listener.suppress_event()
        return False


# ---------------------------------------------------------------- 显示名


_LABELS = {
    "caps_lock": "CapsLock", "scroll_lock": "ScrollLock", "num_lock": "NumLock", "pause": "Pause",
    "print_screen": "PrtSc", "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End",
    "page_up": "PgUp", "page_down": "PgDn", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "apps": "Menu", "space": "Space", "tab": "Tab", "enter": "Enter", "backspace": "Backspace", "esc": "Esc",
    "backquote": "`", "minus": "-", "equal": "=", "bracket_left": "[", "bracket_right": "]",
    "backslash": "\\", "semicolon": ";", "quote": "'", "comma": ",", "period": ".", "slash": "/",
    "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
}


def label(spec: str, lang: str = "zh") -> str:
    """给人看的名字，如 Ctrl + Space、右 Ctrl；与前端 ui.js 的 hotkeyName 一致。"""
    try:
        parts = parse(spec).spec.split("+")
    except HotkeyError:
        return str(spec)
    out = []
    for p in parts:
        if m := re.fullmatch(r"(left|right)_(ctrl|alt|shift|win)", p):
            side, key = m.groups()
            side_text = {"zh": {"left": "左 ", "right": "右 "}, "en": {"left": "Left ", "right": "Right "}}
            out.append(side_text.get(lang, side_text["en"])[side] + _LABELS[key])
        elif m := re.fullmatch(r"num(\d)", p):
            out.append(f"Num {m.group(1)}")
        elif p.startswith("vk_"):
            out.append(p.upper().replace("VK_0X", "VK 0x"))
        else:
            out.append(_LABELS.get(p, p.upper()))
    return " + ".join(out)
