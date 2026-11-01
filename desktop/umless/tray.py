"""系统托盘图标：用颜色表示状态，菜单提供常用入口。

菜单文字和悬停提示跟随界面语言：菜单项的文字是函数，refresh_menu() 时重新取值。
"""

import logging
import os
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from .i18n import t

log = logging.getLogger(__name__)

NIN_BALLOONUSERCLICK = 0x0405  # WM_USER + 5：用户点击了托盘通知气泡

COLORS = {
    "loading": "#9e9e9e",  # 灰：模型加载中
    "idle": "#14b8a6",  # 青绿：就绪
    "recording": "#e53935",  # 红：录音中
    "processing": "#fb8c00",  # 橙：识别 / 润色中
    "error": "#8e24aa",  # 紫：出错
}


def _title(state: str) -> str:
    """悬停提示，如"Umless - 就绪（按住热键说话）"。"""
    return t(f"tray.title.{state}")


def _text(key: str) -> Callable:
    """菜单项文字：每次刷新菜单时按当前语言取值。"""
    return lambda _item: t(key)


def _icon(color: str, size: int = 64) -> Image.Image:
    """按 64×64 的坐标作图，size 更大时等比放大（打包用的 .ico 需要 256×256 仍然清晰）。"""
    k = size / 64
    s = lambda *v: tuple(round(x * k) for x in v)  # noqa: E731
    w = max(1, round(3 * k))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse(s(4, 4, 60, 60), fill=color)
    # 简化的麦克风图形
    d.rounded_rectangle(s(25, 14, 39, 38), radius=round(7 * k), fill="white")
    d.arc(s(19, 24, 45, 46), start=0, end=180, fill="white", width=w)
    d.line(s(32, 46, 32, 52), fill="white", width=w)
    return img


def save_ico(path, color: str = COLORS["idle"]) -> None:
    """生成多尺寸 .ico（窗口标题栏、任务栏、exe 与安装程序图标）。"""
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    big = _icon(color, 256)
    big.save(path, format="ICO", sizes=[(n, n) for n in sizes],
             append_images=[_icon(color, n) for n in sizes if n != 256])


class Tray:
    def __init__(
        self,
        open_page: Callable[[str | None], None],
        is_paused: Callable[[], bool],
        toggle_pause: Callable[[], None],
        data_dir: str,
        log_file: str,
        on_quit: Callable[[], None],
    ):
        self._images = {k: _icon(v) for k, v in COLORS.items()}
        self._on_quit = on_quit
        self._state = "loading"
        menu = pystray.Menu(
            pystray.MenuItem(_text("tray.menu.open"), lambda: open_page(None), default=True),
            pystray.MenuItem(_text("tray.menu.report"), lambda: open_page("report")),
            pystray.MenuItem(_text("tray.menu.practice"), lambda: open_page("practice")),
            pystray.MenuItem(_text("tray.menu.settings"), lambda: open_page("settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_text("tray.menu.pause"), lambda: toggle_pause(),
                             checked=lambda _: is_paused()),
            pystray.MenuItem(_text("tray.menu.more"), pystray.Menu(
                pystray.MenuItem(_text("tray.menu.data_dir"), lambda: os.startfile(data_dir)),
                pystray.MenuItem(_text("tray.menu.log"), lambda: os.startfile(log_file)),
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_text("tray.menu.quit"), self._quit),
        )
        self.icon = pystray.Icon("Umless", self._images["loading"], _title("loading"), menu)
        self._on_click: Callable[[], None] | None = None  # 当前通知被点击时执行
        self._hook_balloon_click()

    def _hook_balloon_click(self) -> None:
        """pystray 不处理通知气泡的点击：包一层它的 Win32 消息处理，点击时执行 notify 传入的回调。"""
        handlers = getattr(self.icon, "_message_handlers", None)
        try:
            from pystray._util import win32

            original = handlers[win32.WM_NOTIFY]
        except (ImportError, AttributeError, KeyError, TypeError):
            return

        def on_notify(wparam, lparam):
            if lparam == NIN_BALLOONUSERCLICK and self._on_click:
                callback, self._on_click = self._on_click, None
                try:
                    callback()
                except Exception:
                    log.exception("处理通知点击失败")
                return 0
            return original(wparam, lparam)

        handlers[win32.WM_NOTIFY] = on_notify

    def set_state(self, state: str) -> None:
        self._state = state
        self.icon.icon = self._images[state]
        self.icon.title = _title(state)

    def notify(self, message: str, title: str = "Umless", on_click: Callable[[], None] | None = None) -> None:
        """弹出托盘通知；on_click 为点击通知时的操作（下一条通知会替换它）。"""
        self._on_click = on_click
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def refresh_menu(self) -> None:
        """重新取菜单文字和勾选状态（界面语言、暂停记录变化后调用），悬停提示一并刷新。"""
        try:
            self.icon.title = _title(self._state)
            self.icon.update_menu()
        except Exception:
            pass

    def _quit(self) -> None:
        self._on_quit()
        self.icon.stop()

    def stop(self) -> None:
        """与菜单「退出」相同（安装更新前由常驻进程调用），可在任意线程调用。"""
        self._quit()

    def run(self, setup: Callable[[], None]) -> None:
        """阻塞运行托盘（必须在主线程）；setup 在托盘就绪后于后台线程执行。"""
        def _setup(icon: pystray.Icon) -> None:
            icon.visible = True
            setup()

        self.icon.run(setup=_setup)
