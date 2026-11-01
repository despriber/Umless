"""构建 Windows 发布包：PyInstaller 目录版 → 便携 zip →（找到 Inno Setup 时）安装程序。

    cd desktop
    uv run --with pyinstaller python packaging/build.py [--no-installer | --require-installer]

产物（版本号取自 pyproject.toml）：
    dist/Umless/                         PyInstaller 目录（Umless.exe、umless-cli.exe、_internal/）
    dist/Umless-<ver>-portable.zip       便携版
    dist/Umless-Setup-<ver>.exe          安装程序（需要 Inno Setup 6 的 ISCC.exe）
    dist/build-info.json                 产物清单：文件名、大小、sha256（发布脚本生成 update.json 用）

ISCC.exe 的查找顺序：环境变量 ISCC → PATH → Inno Setup 6 的默认安装目录。找不到时跳过安装程序
（--require-installer 时报错退出，CI 用）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
PACKAGING = DESKTOP / "packaging"
GEN = DESKTOP / "build" / "packaging"  # 图标、版本信息（umless.spec 读取）
DIST = DESKTOP / "dist"
APP_DIR = DIST / "Umless"

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Umless'),
      StringStruct('FileDescription', 'Umless'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Umless'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'Umless.exe'),
      StringStruct('ProductName', 'Umless'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""


def read_version() -> str:
    """版本号以 pyproject.toml 为准，并要求与 umless/__init__.py 的 __version__ 一致。"""
    version = tomllib.loads((DESKTOP / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    init = (DESKTOP / "umless" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not m or m.group(1) != version:
        sys.exit(f"版本号不一致：pyproject.toml 为 {version}，umless/__init__.py 为 {m and m.group(1)}")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", version):
        sys.exit(f"版本号不是语义化版本：{version}")
    return version


def numeric(version: str) -> tuple[int, int, int, int]:
    """Windows 文件版本只能是 4 段数字：去掉预发布后缀。"""
    major, minor, patch = (int(x) for x in version.split("-")[0].split("."))
    return major, minor, patch, 0


def generate(version: str) -> Path:
    GEN.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(DESKTOP))
    from umless.tray import save_ico

    icon = GEN / "umless.ico"
    save_ico(icon)
    (GEN / "version_info.txt").write_text(
        VERSION_INFO.format(nums=numeric(version), version=version), encoding="utf-8")
    return icon


def run_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("没有 PyInstaller：请用 uv run --with pyinstaller python packaging/build.py")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
           "--distpath", str(DIST), "--workpath", str(DESKTOP / "build" / "pyinstaller"),
           str(PACKAGING / "umless.spec")]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=DESKTOP)
    for exe in ("Umless.exe", "umless-cli.exe"):
        if not (APP_DIR / exe).is_file():
            sys.exit(f"PyInstaller 没有生成 {exe}")


def check_bundle() -> None:
    """打包结果里必须有全部界面文件：缺任何一个被 import 的 js，整个主窗口都会是空白。"""
    src = DESKTOP / "umless" / "ui" / "web"
    dst = DIST / "Umless" / "_internal" / "umless" / "ui" / "web"
    missing = [str(f.relative_to(src)) for f in src.rglob("*") if f.is_file() and not (dst / f.relative_to(src)).is_file()]
    if missing:
        sys.exit("打包结果缺少界面文件：" + "、".join(sorted(missing)))
    print(f"界面文件齐全：{sum(1 for f in src.rglob('*') if f.is_file())} 个", flush=True)


def make_zip(version: str) -> Path:
    base = DIST / f"Umless-{version}-portable"
    Path(str(base) + ".zip").unlink(missing_ok=True)
    return Path(shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir="Umless"))


def find_iscc() -> Path | None:
    candidates = [os.environ.get("ISCC"), shutil.which("ISCC"), shutil.which("iscc")]
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            sub = "Programs/Inno Setup 6" if env == "LOCALAPPDATA" else "Inno Setup 6"
            candidates.append(str(Path(root) / sub / "ISCC.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def make_installer(iscc: Path, version: str, icon: Path) -> Path:
    out = DIST / f"Umless-Setup-{version}.exe"
    out.unlink(missing_ok=True)
    cmd = [str(iscc), "/Qp", f"/DAppVersion={version}", f"/DAppVersionNumeric={'.'.join(map(str, numeric(version)))}",
           f"/DSourceDir={APP_DIR}", f"/DOutputDir={DIST}", f"/DIconFile={icon}", str(PACKAGING / "umless.iss")]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PACKAGING)
    if not out.is_file():
        sys.exit(f"ISCC 没有生成 {out.name}")
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # 输出重定向到文件或 CI 日志时也用 UTF-8
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="构建 Umless Windows 发布包")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-installer", action="store_true", help="不编译安装程序")
    group.add_argument("--require-installer", action="store_true", help="找不到 ISCC.exe 时报错（CI 用）")
    args = parser.parse_args()

    version = read_version()
    print(f"Umless {version}", flush=True)
    icon = generate(version)
    run_pyinstaller()
    check_bundle()
    files = [("portable", make_zip(version))]

    if not args.no_installer:
        iscc = find_iscc()
        if iscc:
            files.insert(0, ("installer", make_installer(iscc, version, icon)))
        elif args.require_installer:
            sys.exit("找不到 ISCC.exe（Inno Setup 6）：安装后重试，或用环境变量 ISCC 指定路径")
        else:
            print("未找到 Inno Setup 的 ISCC.exe，跳过安装程序（可用环境变量 ISCC 指定）", flush=True)

    info = {"version": version, "platform": "windows-x64", "files": []}
    print(f"\n目录版：{APP_DIR}")
    for kind, path in files:
        digest = sha256(path)
        info["files"].append({"kind": kind, "name": path.name, "path": str(path),
                              "size": path.stat().st_size, "sha256": digest})
        print(f"{path}\n  {path.stat().st_size} bytes  sha256 {digest}")
    (DIST / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"产物清单：{DIST / 'build-info.json'}")


if __name__ == "__main__":
    main()
