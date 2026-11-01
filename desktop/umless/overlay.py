"""屏幕状态提示条：Win32 分层窗口（逐像素 alpha），置顶、不抢焦点、鼠标穿透。

显示在用户正在输入的那块屏幕的底部中央。所有 Win32 调用都在专属线程里完成，
其他线程只通过 show() / hide() 修改状态。
"""

import collections
import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, replace

from .overlay_render import MARGIN, Frame, Wave, pill_width, render

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020  # 鼠标穿透
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080  # 不出现在任务栏 / Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # 不抢焦点（否则粘贴会贴错地方）
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
PM_REMOVE = 0x1
MONITOR_DEFAULTTONEAREST = 2

FPS = 40
FADE_IN, FADE_OUT = 0.14, 0.22
SLIDE = 8  # 淡入时上滑（淡出时下沉）的逻辑像素
TRANSITION = 0.2  # 状态切换时胶囊宽度过渡 + 内容交叉淡入淡出的时长
BOTTOM_OFFSET = 64  # 距工作区底部的逻辑像素
# 一次性状态的自动消失时间
AUTO_HIDE = {"done": 0.9, "info": 1.6, "error": 2.6}


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


# 64 位下句柄是指针宽度，必须声明类型，否则会被截断
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def enable_dpi_awareness() -> None:
    """按显示器 DPI 感知（v2），否则在高分屏上会被系统拉伸而模糊。需在创建任何窗口前调用。"""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _monitor_of_foreground() -> tuple[wintypes.RECT, float]:
    """返回前台窗口所在显示器的工作区和缩放比例。"""
    hmon = user32.MonitorFromWindow(user32.GetForegroundWindow(), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(hmon, ctypes.byref(info))
    dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
    try:
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    except Exception:
        pass
    return info.rcWork, dpi_x.value / 96


def _ease_out(x: float) -> float:
    return 1 - (1 - min(max(x, 0.0), 1.0)) ** 3


@dataclass
class _Shown:
    """窗口线程里正在显示的一份内容。"""

    state: str
    text: str
    since: float
    record_start: float
    snapshot: Frame | None = None  # 作为过渡中的旧内容时冻结的画面（声波、计时不再变化）


class Overlay:
    def __init__(self, level_source: Callable[[], list[float]] | None = None) -> None:
        self._level_source = level_source or (lambda: [])
        self._lock = threading.Lock()
        # ---- 由 show() / hide() 写入（加锁） ----
        self._state: str | None = None  # None 表示隐藏
        self._text = ""
        self._since = 0.0
        self._record_start = 0.0
        self._seq = 0  # 每次 show() 加一，窗口线程据此发现新内容
        # ---- 以下只在窗口线程中访问 ----
        self._seen = 0
        self._cur: _Shown | None = None
        self._prev: _Shown | None = None  # 状态切换过渡中的旧内容
        self._trans_t0 = -1.0
        self._w_from = self._w_to = 0.0  # 胶囊宽度过渡（逻辑像素）
        self._fade = 0.0  # 线性显示进度 0..1（淡入 / 淡出可中途反向而不跳变）
        self._last_tick: float | None = None
        self._visible = False  # 窗口当前是否在屏幕上（含淡出过程）
        self._anchor: tuple[wintypes.RECT, float] | None = None
        self._wave = Wave()
        self._epoch = time.perf_counter()
        self._dib: tuple | None = None  # (hbitmap, bits 指针, (w, h))，尺寸不变时复用
        self._old_bmp = None
        self.tick_ms: collections.deque[float] = collections.deque(maxlen=600)  # 每帧绘制+上屏耗时（毫秒）
        self._running = True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)

    # ---- 对外接口（线程安全） ----

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(3)

    def show(self, state: str, text: str = "") -> None:
        now = time.perf_counter()
        with self._lock:
            if state == "recording":
                self._record_start = now
            self._state, self._text, self._since = state, text, now
            self._seq += 1

    def hide(self) -> None:
        with self._lock:
            self._state = None

    def stop(self) -> None:
        self._running = False

    # ---- 窗口线程 ----

    def _run(self) -> None:
        try:
            self._create_window()
        except Exception:
            log.exception("提示条窗口创建失败")
            self._ready.set()
            return
        self._ready.set()
        try:  # 预热底图 / 字体缓存，避免第一次按下热键时首帧卡顿
            render(Frame("recording"), _monitor_of_foreground()[1])
        except Exception:
            log.exception("提示条预热失败")
        msg = wintypes.MSG()
        next_t = time.perf_counter()
        while self._running:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            try:
                self._tick()
            except Exception:
                log.exception("提示条绘制出错")
            now = time.perf_counter()
            if self._visible:  # 按固定节拍出帧（扣除绘制耗时），动画更匀
                next_t = max(next_t + 1 / FPS, now)
                time.sleep(max(0.0, next_t - now))
            else:
                time.sleep(0.03)
                next_t = time.perf_counter()
        self._free_dib()
        user32.DestroyWindow(self._hwnd)

    def _create_window(self) -> None:
        self._wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))  # 保持引用
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                         hInstance=hinst, lpszClassName="UmlessOverlay")
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(ex, "UmlessOverlay", "Umless", WS_POPUP,
                                            0, 0, 1, 1, None, None, hinst, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)

    def _enter(self, state: str, text: str, since: float, record_start: float, now: float) -> None:
        """切换到新内容：首次出现直接就位；已在屏幕上则启动宽度过渡和内容交叉淡入淡出。"""
        new = _Shown(state, text, since, record_start)
        if state == "recording":
            self._wave.reset()
        if self._cur is None or self._fade <= 0:
            self._anchor = _monitor_of_foreground()  # 每次出现时重新定位到当前屏幕
            self._cur, self._prev = new, None
            self._w_from = self._w_to = pill_width(Frame(state, text=text), self._anchor[1])
            self._trans_t0 = -1.0
            return
        if (state, text) == (self._cur.state, self._cur.text):  # 同一内容重复 show：只重置计时
            self._cur.since, self._cur.record_start = since, record_start
            return
        old = self._cur
        old.snapshot = self._frame(old, now)
        self._w_from = self._width(now)  # 从当前显示宽度出发：过渡中再次切换也不会跳
        self._w_to = pill_width(Frame(state, text=text), self._anchor[1])
        self._prev, self._cur, self._trans_t0 = old, new, now

    def _frame(self, shown: _Shown, now: float) -> Frame:
        if shown.snapshot is not None:  # 旧内容：画面冻结，只让转圈等动画继续走
            return replace(shown.snapshot, t=now - shown.since, clock=now - self._epoch)
        rec = shown.state == "recording"
        levels = list(self._level_source()) if rec else []
        return Frame(
            state=shown.state,
            t=now - shown.since,
            elapsed=now - shown.record_start,
            levels=levels,
            text=shown.text,
            bars=self._wave.update(levels, now) if rec else None,
            clock=now - self._epoch,
        )

    def _width(self, now: float) -> float:
        return self._w_from + (self._w_to - self._w_from) * _ease_out((now - self._trans_t0) / TRANSITION)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = 0.0 if self._last_tick is None else min(now - self._last_tick, 0.1)
        self._last_tick = now
        with self._lock:
            state, text, since = self._state, self._text, self._since
            record_start, seq = self._record_start, self._seq

        if state in AUTO_HIDE and now - since > AUTO_HIDE[state]:
            self.hide()
            state = None
        if state is not None and seq != self._seen:
            self._seen = seq
            self._enter(state, text, since, record_start, now)

        if state is not None:
            self._fade = min(1.0, max(self._fade, 1e-3) + dt / FADE_IN)  # 刚出现的第一帧（dt=0）也要画
        else:
            self._fade = max(0.0, self._fade - dt / FADE_OUT)
        if self._fade <= 0 or self._cur is None:
            if self._visible:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                self._visible = False
            self._cur = self._prev = None
            return

        t0 = time.perf_counter()
        mix = (now - self._trans_t0) / TRANSITION
        if mix >= 1:
            self._prev = None
        prev = self._frame(self._prev, now) if self._prev is not None else None
        frame = self._frame(self._cur, now)
        work, scale = self._anchor
        img = render(frame, scale, self._width(now), prev, min(max(mix, 0.0), 1.0))
        p = _ease_out(self._fade)
        opacity, slide = p, (1 - p) * SLIDE
        x = (work.left + work.right - img.width) // 2
        # 图像四周含阴影边距，让胶囊本体（而非阴影）距工作区底部 BOTTOM_OFFSET
        y = work.bottom - img.height - int((BOTTOM_OFFSET - slide - MARGIN) * scale)
        self._blit(img, x, y, opacity)
        if not self._visible:
            user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._visible = True
        self.tick_ms.append((time.perf_counter() - t0) * 1000)

    def _free_dib(self) -> None:
        if self._dib is not None:
            gdi32.SelectObject(self._mem_dc, self._old_bmp)
            gdi32.DeleteObject(self._dib[0])
            self._dib = None

    def _blit(self, img, x: int, y: int, opacity: float) -> None:
        w, h = img.size
        if self._dib is None or self._dib[2] != (w, h):  # 尺寸变了才重建位图（只有宽度过渡时会变）
            self._free_dib()
            bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                                   biPlanes=1, biBitCount=32, biCompression=0)
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbmp:
                raise ctypes.WinError(ctypes.get_last_error())
            self._old_bmp = gdi32.SelectObject(self._mem_dc, hbmp)
            self._dib = (hbmp, bits, (w, h))
        # UpdateLayeredWindow 要求预乘 alpha 的 BGRA；Pillow 的 RGBa 即预乘模式，打包时直接排成 BGRa
        data = img.convert("RGBa").tobytes("raw", "BGRa")
        ctypes.memmove(self._dib[1], data, len(data))
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, max(1, min(255, round(255 * opacity))), AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            self._hwnd, self._screen_dc, ctypes.byref(wintypes.POINT(x, y)),
            ctypes.byref(wintypes.SIZE(w, h)), self._mem_dc, ctypes.byref(wintypes.POINT(0, 0)),
            0, ctypes.byref(blend), ULW_ALPHA,
        )
