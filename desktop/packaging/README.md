# Windows 打包与发布

桌面端用 PyInstaller 打成目录版（one-dir），再用 Inno Setup 做成安装程序。更新机制见 `spec/update.md`。

## 构建

```powershell
cd desktop
uv run --with pyinstaller python packaging/build.py
```

可选参数：`--no-installer` 不编译安装程序；`--require-installer` 找不到 Inno Setup 时报错退出（CI 用）。

版本号只在 `desktop/pyproject.toml` 改，`umless/__init__.py` 的 `__version__` 必须一致（不一致时构建直接报错）。

产物都在 `desktop/dist/`：

| 文件 | 内容 |
|---|---|
| `Umless/` | PyInstaller 目录：`Umless.exe`（无控制台，托盘常驻与主窗口）、`umless-cli.exe`（控制台版）、`_internal/` |
| `Umless-<ver>-portable.zip` | 便携版：解压后双击 `Umless.exe`。不支持自动安装更新，需手动下载新版本 |
| `Umless-Setup-<ver>.exe` | 安装程序（需要 Inno Setup 6） |
| `build-info.json` | 产物清单：`kind`（`installer` / `portable`）、文件名、大小、sha256，供发布脚本生成 `update.json` |

构建脚本最后会打印每个文件的路径、大小与 sha256。

### Inno Setup

`build.py` 按以下顺序查找 `ISCC.exe`：环境变量 `ISCC` → `PATH` → `C:\Program Files (x86)\Inno Setup 6\`、
`C:\Program Files\Inno Setup 6\`、`%LOCALAPPDATA%\Programs\Inno Setup 6\`。找不到就只生成目录版与便携 zip。

简体中文界面使用 Inno Setup 自带的 `Languages\ChineseSimplified.isl`（6.3 起为官方翻译）；更早的版本可把该文件放在
`packaging/` 下，都没有时安装程序只有英文。

## 文件说明

| 文件 | 作用 |
|---|---|
| `build.py` | 构建入口：生成图标与版本信息 → PyInstaller → zip →（可选）ISCC |
| `umless.spec` | PyInstaller 配置：两个 exe 共用一份 `_internal`；收集界面文件、示例配置、sherpa-onnx（含 `onnxruntime.dll`）、PortAudio、libsndfile、pywebview（WinForms + WebView2，经 pythonnet） |
| `launcher.py` | 两个 exe 的入口脚本，等同 `python -m umless` |
| `umless.iss` | Inno Setup 脚本 |

图标由 `umless/tray.py` 的托盘图标生成（`build/packaging/umless.ico`），与主窗口标题栏图标一致。

## 安装程序的行为

- 按用户安装，不需要管理员权限：`%LOCALAPPDATA%\Programs\Umless`；开始菜单快捷方式，桌面快捷方式可选。
- 安装 / 卸载前关闭正在运行的 Umless（常驻进程与主窗口，通过互斥量 `Local\Umless.Core` / `Local\Umless.UI` 判断）：
  交互安装先询问；静默安装先等 10 秒让 Umless 自己退出，再强制结束。
- 升级时先删除旧的 `_internal/`，避免新旧运行库混用。
- 交互安装完成页有「启动 Umless」勾选框；静默安装（自动更新）完成后直接重新启动 Umless。
- **卸载不会删除用户数据**：`%APPDATA%\Umless`（配置、记录、识别模型、日志）保留，卸载完成时会提示该路径；
  开机自启项若指向本安装目录会一并删除。
- 应用内自动更新以 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /LOG=%TEMP%\umless-update\install.log` 运行安装程序。

## 打包版与源码版的区别

`system.install_mode()` 区分三种运行方式：`installed`（安装目录里有 `unins000.exe`）、`portable`、`source`。

- 启动子进程（主窗口、开机自启）：打包后用 `Umless.exe`，源码运行时用 `pythonw -m umless`。
  打包版启动时若开机自启项指向别处（以前从源码运行、便携版换了目录），会改成当前程序。
- 包内资源（`config.example.toml`、`ui/web/`）打包后在 `_internal/umless/` 下（`config.PACKAGE_DIR`）。
- 只有安装版能在应用内下载并安装更新；便携版与源码版只检查、提示手动更新。

## 本地验证

```powershell
$env:UMLESS_HOME = "$env:TEMP\umless-pkg-test"   # 不碰个人数据
dist\Umless\umless-cli.exe doctor
dist\Umless\umless-cli.exe transcribe test.wav --no-llm
```
