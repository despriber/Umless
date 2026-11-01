"""热键过滤逻辑测试：直接喂低层钩子消息，不安装真实钩子。"""

import threading
import time
from types import SimpleNamespace

import pytest

from umless import hotkey
from umless.hotkey import KEYMAP, PushToTalk

DOWN, UP = 0x100, 0x101
CAPS = KEYMAP["caps_lock"]


class Suppressed(Exception):
    pass


@pytest.fixture
def ptt(monkeypatch):
    taps: list[int] = []
    calls: list[str] = []
    monkeypatch.setattr(hotkey, "_tap", taps.append)

    def make(key: str, min_hold_ms: int = 300) -> PushToTalk:
        p = PushToTalk(key, lambda: calls.append("down"), lambda: calls.append("up"), min_hold_ms,
                       lambda: calls.append("cancel"))
        p._listener.suppress_event = lambda: (_ for _ in ()).throw(Suppressed())
        threading.Thread(target=p._dispatch, daemon=True).start()
        p.taps, p.calls = taps, calls
        return p

    return make


def feed(p: PushToTalk, msg: int, vk: int, injected: bool = False) -> str:
    """返回 'suppressed' / 'blocked'（不交给 pynput 回调但放行给系统）/ 'pass'。"""
    data = SimpleNamespace(vkCode=vk, flags=0x10 if injected else 0)
    try:
        return "pass" if p._filter(msg, data) else "blocked"
    except Suppressed:
        return "suppressed"


def settle():
    time.sleep(0.1)


def test_caps_hold_records_and_is_suppressed(ptt):
    p = ptt("caps_lock", min_hold_ms=50)
    assert feed(p, DOWN, CAPS) == "suppressed"
    assert feed(p, DOWN, CAPS) == "suppressed"  # 自动重复不重复触发
    time.sleep(0.08)
    assert feed(p, UP, CAPS) == "suppressed"
    settle()
    assert p.calls == ["down", "up"]
    assert p.taps == []  # 长按不补发，不会切换大小写


def test_caps_short_tap_is_reinjected(ptt):
    p = ptt("caps_lock", min_hold_ms=300)
    feed(p, DOWN, CAPS)
    feed(p, UP, CAPS)
    settle()
    assert p.calls == ["down", "up"]
    assert p.taps == [CAPS]  # 轻点：补发 CapsLock，保留切换大小写


def test_injected_and_other_keys_pass_through(ptt):
    p = ptt("caps_lock")
    assert feed(p, DOWN, CAPS, injected=True) == "pass"  # 我们自己补发的按键
    assert feed(p, DOWN, 0x41) == "pass"  # 字母 A
    settle()
    assert p.calls == []


def test_right_alt_not_suppressed_but_masked(ptt):
    p = ptt("right_alt", min_hold_ms=0)
    assert feed(p, 0x104, KEYMAP["right_alt"]) == "blocked"  # WM_SYSKEYDOWN，系统照常收到
    assert feed(p, 0x105, KEYMAP["right_alt"]) == "blocked"
    settle()
    assert p.calls == ["down", "up"]
    assert p.taps == [0xE8]  # 只注入菜单屏蔽键，不补发 Alt


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        PushToTalk("space", lambda: None, lambda: None)  # 单独的空格会影响打字
    with pytest.raises(ValueError):
        PushToTalk("hyper+q", lambda: None, lambda: None)


# ---- 键名解析 ----


@pytest.mark.parametrize("spec, norm", [
    ("caps_lock", "caps_lock"), ("F8", "f8"), ("Ctrl + Space", "ctrl+space"), ("shift+ctrl+f1", "ctrl+shift+f1"),
    ("alt+q", "alt+q"), ("win+num5", "win+num5"), ("vk_0xE9", "vk_0xe9"), ("pause", "pause"), ("right_ctrl", "right_ctrl"),
])
def test_parse_normalizes(spec, norm):
    assert hotkey.normalize(spec) == norm


@pytest.mark.parametrize("spec, code", [
    ("", "empty"), ("ctrl+", "empty"), ("ctrl+shift", "mod_main"), ("ctrl+ctrl+a", "bad"), ("a", "typing"),
    ("space", "typing"), ("foo", "bad"), ("vk_0x00", "bad"),
])
def test_parse_rejects(spec, code):
    with pytest.raises(hotkey.HotkeyError) as e:
        hotkey.parse(spec)
    assert e.value.code == code


def test_label():
    assert hotkey.label("ctrl+space") == "Ctrl + Space"
    assert hotkey.label("right_ctrl") == "右 Ctrl"
    assert hotkey.label("right_ctrl", "en") == "Right Ctrl"
    assert hotkey.label("caps_lock") == "CapsLock"


# ---- 组合键 ----

LCTRL, LSHIFT, SPACE = 0xA2, 0xA0, 0x20


def test_combo_requires_exact_modifiers(ptt):
    p = ptt("ctrl+space", min_hold_ms=0)
    assert feed(p, DOWN, SPACE) == "pass"  # 只按空格：正常打字
    feed(p, UP, SPACE)
    assert feed(p, DOWN, LCTRL) == "pass"  # 修饰键本身不拦截
    assert feed(p, DOWN, SPACE) == "suppressed"  # Ctrl+Space：开始录音，空格不传给应用
    assert feed(p, DOWN, SPACE) == "suppressed"  # 自动重复
    assert feed(p, UP, LCTRL) == "pass"  # 先松开 Ctrl 也不影响，松开空格才结束
    assert feed(p, UP, SPACE) == "suppressed"
    settle()
    assert p.calls == ["down", "up"]
    assert p.taps == []  # 组合键不补发
    feed(p, DOWN, LCTRL)
    feed(p, DOWN, LSHIFT)
    assert feed(p, DOWN, SPACE) == "pass"  # Ctrl+Shift+Space 是别的快捷键，放行
    settle()
    assert p.calls == ["down", "up"]


def test_alt_combo_masks_menu(ptt):
    p = ptt("alt+q", min_hold_ms=0)
    feed(p, 0x104, 0xA4)
    feed(p, 0x104, ord("Q"))
    feed(p, 0x105, ord("Q"))
    settle()
    assert p.calls == ["down", "up"] and p.taps == [0xE8]


def test_modifier_alone_cancels_when_used_as_shortcut(ptt):
    p = ptt("right_ctrl", min_hold_ms=0)
    rctrl = KEYMAP["right_ctrl"]
    assert feed(p, DOWN, rctrl) == "blocked"  # 不拦截，系统照常知道 Ctrl 被按下
    assert feed(p, DOWN, ord("C")) == "pass"  # 右 Ctrl+C：照常复制
    feed(p, UP, ord("C"))
    feed(p, UP, rctrl)
    settle()
    assert p.calls == ["down", "cancel"]  # 不会把这段当成语音输入


# ---- 录制 ----


def _capture_with(monkeypatch, events):
    cap = hotkey.Capture()
    cap._listener = SimpleNamespace(suppress_event=lambda: (_ for _ in ()).throw(Suppressed()))
    for msg, vk in events:
        try:
            cap._filter(msg, SimpleNamespace(vkCode=vk, flags=0))
        except Suppressed:
            pass
    return cap


def test_capture_combo_and_single_modifier(monkeypatch):
    cap = _capture_with(monkeypatch, [(DOWN, LCTRL), (DOWN, LSHIFT), (DOWN, KEYMAP["f1"])])
    assert cap._result == "ctrl+shift+f1"
    cap = _capture_with(monkeypatch, [(DOWN, 0xA3), (UP, 0xA3)])
    assert cap._result == "right_ctrl"
    cap = _capture_with(monkeypatch, [(DOWN, 0x1B)])
    assert cap._done.is_set() and cap._result is None  # Esc：取消
    cap = _capture_with(monkeypatch, [(DOWN, ord("A"))])
    assert cap._error is not None and cap._error.code == "typing"
    cap = _capture_with(monkeypatch, [(DOWN, LCTRL), (DOWN, LSHIFT), (UP, LSHIFT), (UP, LCTRL)])
    assert not cap._done.is_set()  # 只按了几个修饰键又松开：继续等
