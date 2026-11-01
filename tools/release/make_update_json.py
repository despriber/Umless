#!/usr/bin/env python3
"""生成 / 校验 Umless 的更新清单 update.json（格式见 spec/update.md 第 3 节）。只用标准库。

生成（桌面端）：
    python tools/release/make_update_json.py --app umless-desktop --version 0.4.0 \
        --notes-zh docs/release-notes/0.4.0.zh.md --notes-en docs/release-notes/0.4.0.en.md \
        --asset windows-x64:installer:desktop/dist/Umless-Setup-0.4.0.exe \
        --asset windows-x64:portable:desktop/dist/Umless-0.4.0-portable.zip \
        --output update.json

生成（Android）：
    python tools/release/make_update_json.py --app umless-android --version 0.2.0 --version-code 2000 \
        --notes-zh ... --notes-en ... \
        --asset android-arm64-v8a:apk:dist/Umless-0.2.0-arm64.apk:20002

校验已有清单：
    python tools/release/make_update_json.py --check update.json

--asset 的格式为 platform:kind:path[:version_code]（path 可以是 Windows 路径，如 D:\\x\\a.apk）。
下载地址按 https://github.com/{repo}/releases/download/{tag}/{name} 生成；repo 默认按 app 取
spec/update.md 第 1 节的仓库，tag 默认 v{version}。本地测试时可用 --base-url http://127.0.0.1:8000
让下载地址指向本机（配合 python -m http.server 与配置项 update.manifest_url）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
APPS = {
    "umless-desktop": "despriber/Umless",
    "umless-android": "despriber/Umless-Android",
}
# 每个平台允许的安装包类型；平台必须属于对应的 app
PLATFORMS = {
    "windows-x64": ("umless-desktop", {"installer", "portable"}),
    "android-arm64-v8a": ("umless-android", {"apk"}),
    "android-armeabi-v7a": ("umless-android", {"apk"}),
    "android-x86_64": ("umless-android", {"apk"}),
}
KINDS = {"installer", "portable", "apk"}
# spec/update.md 第 5 节：versionCode = 基础号 × 10 + ABI
ABI_DIGIT = {"android-armeabi-v7a": 1, "android-arm64-v8a": 2, "android-x86_64": 4}
URL_TEMPLATE = "https://github.com/{repo}/releases/download/{tag}/{name}"

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?$"
)
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ManifestError(ValueError):
    """清单内容或参数不合法。"""


# ---------------------------------------------------------------- 版本号


def parse_semver(version: str) -> tuple[int, int, int, str | None]:
    m = SEMVER_RE.match(version or "")
    if not m:
        raise ManifestError(f"版本号不是 MAJOR.MINOR.PATCH[-后缀] 格式：{version!r}")
    major, minor, patch, pre = m.groups()
    major, minor, patch = int(major), int(minor), int(patch)
    if minor > 999 or patch > 999:
        raise ManifestError(f"MINOR / PATCH 不能超过 999（versionCode 规则所限）：{version}")
    return major, minor, patch, pre


def is_prerelease(version: str) -> bool:
    return parse_semver(version)[3] is not None


def base_version_code(version: str) -> int:
    """spec §5：MAJOR × 1 000 000 + MINOR × 1 000 + PATCH（不含 ABI 位）。"""
    major, minor, patch, _ = parse_semver(version)
    return major * 1_000_000 + minor * 1_000 + patch


def abi_version_code(version: str, platform: str) -> int:
    return base_version_code(version) * 10 + ABI_DIGIT[platform]


# ---------------------------------------------------------------- 更新说明


def markdown_to_text(md: str) -> str:
    """把 docs/release-notes/*.md 转成清单里用的纯文本（换行分段）。

    去掉 HTML 注释、一级标题（Release 页面已经有标题），二级及以下标题、粗体 / 斜体 / 行内代码标记，
    链接只留文字；列表与空行原样保留。
    """
    text = re.sub(r"<!--.*?-->", "", md.replace("\r\n", "\n"), flags=re.S)
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r"^#\s", stripped):
            continue
        line = re.sub(r"^\s*#{2,6}\s+", "", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        lines.append(line.rstrip())
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def read_notes(value: str | None, lang: str) -> str:
    """value 是已存在的文件就读文件（.md 会转纯文本），否则当作正文。看起来像文件但不存在时报错。"""
    if value is None:
        return ""
    path = Path(value)
    try:
        is_file = "\n" not in value and path.is_file()
    except OSError:  # 正文太长等情况不是合法路径
        is_file = False
    if is_file:
        raw = path.read_text(encoding="utf-8-sig")
        return markdown_to_text(raw) if path.suffix.lower() == ".md" else raw.strip()
    if "\n" not in value and path.suffix.lower() in {".md", ".txt"}:
        raise ManifestError(f"找不到 {lang} 更新说明文件：{value}")
    return value.replace("\\n", "\n").strip()


# ---------------------------------------------------------------- 资产


def parse_asset_arg(spec: str) -> tuple[str, str, Path, int | None]:
    """platform:kind:path[:version_code]；path 本身可以含冒号（Windows 盘符）。"""
    parts = spec.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ManifestError(f"--asset 格式应为 platform:kind:path[:version_code]：{spec!r}")
    platform, kind, rest = parts
    version_code = None
    m = re.match(r"^(.+):(\d+)$", rest)
    if m and len(m.group(1)) > 1:  # 排除 "C:123" 这种只有盘符的情况
        rest, version_code = m.group(1), int(m.group(2))
    return platform, kind, Path(rest), version_code


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_asset(app: str, version: str, repo: str, tag: str, spec: str, base_url: str | None = None) -> dict:
    platform, kind, path, version_code = parse_asset_arg(spec)
    check_platform_kind(app, platform, kind)
    if not path.is_file():
        raise ManifestError(f"安装包文件不存在：{path}")
    size = path.stat().st_size
    if size <= 0:
        raise ManifestError(f"安装包文件为空：{path}")
    asset = {
        "platform": platform,
        "kind": kind,
        "name": path.name,
        "url": (base_url.rstrip("/") + "/" + path.name) if base_url
        else URL_TEMPLATE.format(repo=repo, tag=tag, name=path.name),
        "size": size,
        "sha256": sha256_of(path),
    }
    if kind == "apk":
        expected = abi_version_code(version, platform)
        if version_code is None:
            version_code = expected
        elif version_code != expected:
            raise ManifestError(
                f"{path.name} 的 versionCode {version_code} 与版本 {version} 按 spec §5 算出的 {expected} 不一致"
            )
        asset["version_code"] = version_code
    elif version_code is not None:
        raise ManifestError(f"只有 apk 才能带 version_code：{spec!r}")
    return asset


def check_platform_kind(app: str, platform: str, kind: str) -> None:
    if platform not in PLATFORMS:
        raise ManifestError(f"未知平台 {platform!r}，可选：{', '.join(PLATFORMS)}")
    if kind not in KINDS:
        raise ManifestError(f"未知类型 {kind!r}，可选：{', '.join(sorted(KINDS))}")
    owner, kinds = PLATFORMS[platform]
    if owner != app:
        raise ManifestError(f"平台 {platform} 不属于 {app}")
    if kind not in kinds:
        raise ManifestError(f"平台 {platform} 不支持类型 {kind}（可选：{', '.join(sorted(kinds))}）")


# ---------------------------------------------------------------- 生成与校验


def build_manifest(
    *,
    app: str,
    version: str,
    assets: list[str],
    notes_zh: str = "",
    notes_en: str = "",
    version_code: int | None = None,
    published_at: str | None = None,
    repo: str | None = None,
    tag: str | None = None,
    base_url: str | None = None,
) -> dict:
    if app not in APPS:
        raise ManifestError(f"未知 app {app!r}，可选：{', '.join(APPS)}")
    parse_semver(version)
    repo = repo or APPS[app]
    if not REPO_RE.match(repo):
        raise ManifestError(f"仓库应为 owner/name：{repo!r}")
    tag = tag or f"v{version}"
    if base_url is not None and not re.match(r"^https?://[^/\s]+", base_url):
        raise ManifestError(f"--base-url 应为 http(s) 地址：{base_url!r}")
    published_at = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest: dict = {"schema": SCHEMA, "app": app, "version": version}
    if app == "umless-android":
        expected = base_version_code(version)
        if version_code is None:
            version_code = expected
        elif version_code != expected:
            raise ManifestError(f"--version-code {version_code} 与版本 {version} 按 spec §5 算出的 {expected} 不一致")
        manifest["version_code"] = version_code
    elif version_code is not None:
        raise ManifestError("桌面端不使用 --version-code")
    manifest["published_at"] = published_at
    manifest["notes"] = {"zh": notes_zh, "en": notes_en}
    manifest["assets"] = [build_asset(app, version, repo, tag, a, base_url) for a in assets]
    validate_manifest(manifest, allow_http=base_url is not None)
    return manifest


def validate_manifest(m: object, allow_http: bool = False) -> list[str]:
    """返回所有问题；有问题时抛 ManifestError（消息里列出全部问题）。"""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    if not isinstance(m, dict):
        raise ManifestError("清单顶层必须是 JSON 对象")

    schema = m.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        err(f"schema 必须是 ≥1 的整数，当前 {schema!r}")

    app = m.get("app")
    if app not in APPS:
        err(f"app 必须是 {' / '.join(APPS)}，当前 {app!r}")

    version = m.get("version")
    try:
        parse_semver(version if isinstance(version, str) else "")
    except ManifestError as e:
        err(str(e))
        version = None

    vc = m.get("version_code")
    if app == "umless-android":
        if not isinstance(vc, int) or isinstance(vc, bool) or vc <= 0:
            err(f"Android 清单必须有正整数 version_code，当前 {vc!r}")
        elif version and vc != base_version_code(version):
            err(f"version_code {vc} 与 version {version} 不符（应为 {base_version_code(version)}）")
    elif vc is not None and (not isinstance(vc, int) or isinstance(vc, bool)):
        err(f"version_code 必须是整数，当前 {vc!r}")

    published = m.get("published_at")
    if not isinstance(published, str) or not TIME_RE.match(published):
        err(f"published_at 必须是 UTC 时间 YYYY-MM-DDTHH:MM:SSZ，当前 {published!r}")
    else:
        try:
            datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            err(f"published_at 不是有效日期：{published}")

    notes = m.get("notes")
    if not isinstance(notes, dict):
        err("notes 必须是对象 {\"zh\": …, \"en\": …}")
    else:
        for lang in ("zh", "en"):
            if lang in notes and not isinstance(notes[lang], str):
                err(f"notes.{lang} 必须是字符串")
        if not any(isinstance(notes.get(k), str) and notes[k].strip() for k in ("zh", "en")):
            err("notes 至少要有一种语言（zh / en）的非空说明")

    assets = m.get("assets")
    if not isinstance(assets, list) or not assets:
        err("assets 必须是非空数组")
        assets = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_names: set[str] = set()
    for i, a in enumerate(assets):
        where = f"assets[{i}]"
        if not isinstance(a, dict):
            err(f"{where} 必须是对象")
            continue
        platform, kind, name = a.get("platform"), a.get("kind"), a.get("name")
        try:
            if isinstance(app, str) and app in APPS:
                check_platform_kind(app, platform, kind)
            elif platform not in PLATFORMS or kind not in KINDS:
                raise ManifestError(f"平台 / 类型不合法：{platform!r} / {kind!r}")
        except ManifestError as e:
            err(f"{where}：{e}")
        if (platform, kind) in seen_pairs:
            err(f"{where}：platform={platform} kind={kind} 重复")
        seen_pairs.add((platform, kind))
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            err(f"{where}：name 必须是文件名，当前 {name!r}")
        elif name in seen_names:
            err(f"{where}：name {name} 重复")
        else:
            seen_names.add(name)
        url = a.get("url")
        schemes = ("https://", "http://") if allow_http else ("https://",)
        if not isinstance(url, str) or not url.startswith(schemes):
            err(f"{where}：url 必须是 https 地址（本地测试清单用 --check-local），当前 {url!r}")
        elif isinstance(name, str) and not url.endswith("/" + name):
            err(f"{where}：url 应以 /{name} 结尾，当前 {url}")
        size = a.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            err(f"{where}：size 必须是正整数，当前 {size!r}")
        sha = a.get("sha256")
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            err(f"{where}：sha256 必须是 64 位小写十六进制，当前 {sha!r}")
        avc = a.get("version_code")
        if kind == "apk":
            if not isinstance(avc, int) or isinstance(avc, bool) or avc <= 0:
                err(f"{where}：apk 必须有正整数 version_code，当前 {avc!r}")
            elif version and platform in ABI_DIGIT and avc != abi_version_code(version, platform):
                err(f"{where}：version_code {avc} 应为 {abi_version_code(version, platform)}（spec §5）")
        elif avc is not None:
            err(f"{where}：只有 apk 才带 version_code")

    if errors:
        raise ManifestError("清单不合法：\n  - " + "\n  - ".join(errors))
    return errors


def dump_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def check_file(path: Path, allow_http: bool = False) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ManifestError(f"找不到清单文件：{path}") from None
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path} 不是合法 JSON：{e}") from None
    validate_manifest(data, allow_http=allow_http)
    return data


# ---------------------------------------------------------------- 命令行


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="生成或校验 Umless 更新清单 update.json（spec/update.md §3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--check", metavar="UPDATE_JSON", help="只校验已有清单，不生成")
    p.add_argument("--check-local", action="store_true", help="配合 --check：允许 http:// 地址（本地测试清单）")
    p.add_argument("--app", choices=sorted(APPS))
    p.add_argument("--version", help="语义化版本，如 0.4.0 或 0.4.0-beta.1（不带 v）")
    p.add_argument("--version-code", type=int, help="Android 基础 versionCode（不含 ABI 位）；省略时按 spec §5 计算")
    p.add_argument("--notes-zh", help="中文更新说明：文件路径（.md 会转纯文本）或正文")
    p.add_argument("--notes-en", help="英文更新说明：文件路径或正文")
    p.add_argument("--published-at", help="发布时间 UTC，YYYY-MM-DDTHH:MM:SSZ，默认现在")
    p.add_argument("--asset", action="append", default=[], metavar="PLATFORM:KIND:PATH[:VERSION_CODE]")
    p.add_argument("--repo", help="GitHub 仓库 owner/name，默认按 app 取")
    p.add_argument("--tag", help="Release 标签，默认 v{version}")
    p.add_argument("--base-url", help="仅本地测试：下载地址改为 {base-url}/{name}，如 http://127.0.0.1:8000")
    p.add_argument("-o", "--output", default="update.json", help="输出路径，默认 ./update.json")
    args = p.parse_args(argv)

    try:
        if args.check:
            data = check_file(Path(args.check), allow_http=args.check_local)
            print(f"OK：{args.check}（{data['app']} {data['version']}，{len(data['assets'])} 个安装包）")
            return 0
        missing = [n for n in ("app", "version") if not getattr(args, n)]
        if missing:
            p.error("生成清单需要 " + "、".join("--" + n for n in missing))
        if not args.asset:
            p.error("至少需要一个 --asset")
        manifest = build_manifest(
            app=args.app,
            version=args.version,
            assets=args.asset,
            notes_zh=read_notes(args.notes_zh, "中文"),
            notes_en=read_notes(args.notes_en, "英文"),
            version_code=args.version_code,
            published_at=args.published_at,
            repo=args.repo,
            tag=args.tag,
            base_url=args.base_url,
        )
    except ManifestError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    out = Path(args.output)
    dump_manifest(manifest, out)
    print(f"已写入 {out}：{manifest['app']} {manifest['version']}")
    for a in manifest["assets"]:
        print(f"  {a['platform']:<20} {a['kind']:<9} {a['name']}  {a['size']} B  sha256={a['sha256']}")
    return 0


if __name__ == "__main__":
    # Windows 控制台默认编码可能不是 UTF-8，避免中文输出报错
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
