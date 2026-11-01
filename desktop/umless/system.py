"""与 Windows 系统打交道：开机自启、启动常驻进程、单实例检测、打开文件。"""

import ctypes
import os
import subprocess
import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Umless"

# 常驻进程与主窗口各自持有一个命名互斥量，用于判断是否已在运行（安装程序也据此关闭 Umless）。
# 开发调试用：设环境变量 UMLESS_INSTANCE（配合 UMLESS_HOME）可与正在运行的 Umless 并存，互斥量名加上后缀
_INSTANCE = "".join(c for c in os.environ.get("UMLESS_INSTANCE", "") if c.isalnum() or c in "-_")
CORE_MUTEX = "Local\\Umless.Core" + (f".{_INSTANCE}" if _INSTANCE else "")
UI_MUTEX = "Local\\Umless.UI" + (f".{_INSTANCE}" if _INSTANCE else "")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.OpenMutexW.restype = ctypes.c_void_p
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183
_held: list = []


def acquire_single_instance(name: str) -> bool:
    """创建命名互斥量；已存在则返回 False（说明另一个实例在运行）。"""
    handle = _kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _held.append(handle)  # 进程退出时由系统释放
    return True


def is_running(name: str) -> bool:
    handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        _kernel32.CloseHandle(ctypes.c_void_p(handle))
        return True
    return False


GUI_EXE = "Umless.exe"  # 打包后的无控制台主程序；umless-cli.exe 是同一份代码的控制台版本
UNINSTALLER = "unins000.exe"  # Inno Setup 安装版才有，用来区分安装版与便携版


def is_frozen() -> bool:
    """是否为 PyInstaller 打包后的程序（安装版或便携版）。"""
    return bool(getattr(sys, "frozen", False))


def install_mode() -> str:
    """installed：安装程序装的；portable：解压即用的 zip；source：从源码运行。"""
    if not is_frozen():
        return "source"
    return "installed" if (app_dir() / UNINSTALLER).exists() else "portable"


def app_dir() -> Path:
    """打包后为 exe 所在目录；源码运行时为 umless 包所在目录（desktop/）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def launch_command(*args: str) -> list[str]:
    """启动 umless 子命令且不弹控制台：打包后用 Umless.exe（从 umless-cli.exe 调用时也一样），
    源码运行时用 pythonw -m umless。"""
    if is_frozen():
        gui = app_dir() / GUI_EXE
        return [str(gui if gui.exists() else Path(sys.executable)), *args]
    return [_pythonw(), "-m", "umless", *args]


def child_env() -> dict[str, str]:
    """子进程环境变量。打包后再启动自己（或安装程序结束后启动新版本）时，
    PYINSTALLER_RESET_ENVIRONMENT 让子进程按全新实例初始化，不沿用本进程的解压目录等状态。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    if is_frozen():
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def spawn(*args: str) -> None:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(launch_command(*args), creationflags=flags, close_fds=True, env=child_env(),
                     cwd=str(Path.home()))


def _autostart_command() -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in launch_command())


def get_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def sync_autostart() -> None:
    """打包版启动时：已开启自启但登记的命令不是当前程序（从源码版换成安装版、便携版换了目录），改成当前程序。"""
    if not is_frozen():
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current = winreg.QueryValueEx(key, RUN_NAME)[0]
    except OSError:
        return
    if current != _autostart_command():
        set_autostart(True)


def open_path(path: Path) -> None:
    os.startfile(str(path))  # noqa: S606 - 只打开本地数据目录 / 配置 / 日志


def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()
