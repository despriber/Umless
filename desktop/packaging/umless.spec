# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置：一个目录（one-dir）里两个 exe，共用 _internal：
#   Umless.exe      无控制台，托盘常驻与主窗口（开始菜单、开机自启、安装程序都启动它）
#   umless-cli.exe  控制台版，用于 doctor / transcribe / history / download
# 不要直接运行，用 packaging/build.py（会先生成图标与版本信息）。

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

DESKTOP = Path(SPECPATH).resolve().parent
GEN = DESKTOP / "build" / "packaging"  # build.py 生成的图标与版本信息
ICON = GEN / "umless.ico"
VERSION_FILE = GEN / "version_info.txt"
if not ICON.exists() or not VERSION_FILE.exists():
    raise SystemExit("缺少图标或版本信息，请用 packaging/build.py 构建")


def _keep(dest: str) -> bool:
    """去掉用不到的平台文件：Android 的 jar、32 位 / ARM64 / ASIO 版 PortAudio、32 位 ClrLoader 与 WebBrowserInterop。

    pywebview 的 webview/lib/runtimes/win-{arm64,x64,x86} 三个目录都要保留：导入 edgechromium 时
    会逐个查找并加入 PATH，缺一个就报 Cannot find win-arm64（文件都很小）。
    """
    d = "/" + dest.replace("\\", "/").lower()
    if d.startswith("/umless/"):
        return True  # 本项目的文件一律保留（界面会在所有平台加载 locales/android-*.js，缺了整个界面起不来）
    if "/_sounddevice_data/" in d:
        return not any(x in d for x in ("32bit", "arm64", "-asio", ".dylib"))
    return not any(x in d for x in ("android", "/clr_loader/ffi/dlls/x86/", "webbrowserinterop.x86.dll", ".dylib"))


datas, binaries, hiddenimports = [], [], []

# 本项目：界面文件 ui/web/**、示例配置；模块多为函数内延迟导入，全部列为隐藏导入（模拟后端除外）
datas += collect_data_files("umless", excludes=["**/__pycache__/**"])
hiddenimports += collect_submodules("umless", filter=lambda name: name != "umless.ui.mock_api")

# sherpa-onnx：_sherpa_onnx.pyd 与同目录的 onnxruntime.dll、sherpa-onnx-*.dll（来自 sherpa-onnx-core）。
# 必须保持在 sherpa_onnx/lib 下，加载时优先用同目录的 onnxruntime.dll，不会回落到 System32 里的旧版。
binaries += collect_dynamic_libs("sherpa_onnx")
hiddenimports += collect_submodules("sherpa_onnx", filter=lambda name: "cli" not in name)

# PortAudio（sounddevice）与 libsndfile（soundfile）：DLL 放在 *_data 目录里，按原路径收集
for pkg in ("_sounddevice_data", "_soundfile_data"):
    binaries += [(src, dest) for src, dest in collect_dynamic_libs(pkg)]
    datas += collect_data_files(pkg)

# pywebview 6（Windows 用 WinForms + EdgeChromium/WebView2）：经 pythonnet / clr_loader 调 .NET
for pkg in ("webview", "pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["clr", "webview.platforms.winforms", "webview.platforms.edgechromium"]

# 托盘、全局热键：按平台动态导入的后端
hiddenimports += ["pystray._win32", "pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32"]

hiddenimports = sorted({h for h in hiddenimports
                        if not any(x in h for x in (".gtk", ".qt", ".cocoa", ".android", ".cef", "mock_api"))})

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(DESKTOP)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "_tkinter", "pytest", "_pytest", "playwright", "greenlet", "pyee", "IPython",
              "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3"],
    noarchive=False,
)
# 第三方钩子（sounddevice、pywebview 等）也会收集其他平台的文件，在分析结果里统一过滤
a.binaries = [e for e in a.binaries if _keep(e[0])]
# 依赖分析会把 onnxruntime.dll 等再复制一份到 _internal 根目录（约 22MB）。去掉：_sherpa_onnx.pyd 加载时
# 先在自己所在目录（sherpa_onnx/lib）找依赖，用的就是那里的同版本 DLL，而不是 System32 里可能存在的旧版
SHERPA_DLLS = {"onnxruntime.dll", "sherpa-onnx-c-api.dll", "sherpa-onnx-cxx-api.dll"}
a.binaries = [e for e in a.binaries if e[0].lower() not in SHERPA_DLLS]
a.datas = [e for e in a.datas if _keep(e[0])]
pyz = PYZ(a.pure)

common = dict(exclude_binaries=True, debug=False, strip=False, upx=False,
              icon=str(ICON), version=str(VERSION_FILE))
gui = EXE(pyz, a.scripts, [], name="Umless", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="umless-cli", console=True, **common)

coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="Umless")
