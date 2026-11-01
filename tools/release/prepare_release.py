#!/usr/bin/env python3
"""发布前检查：标签与源码里的版本号一致、更新说明存在，并生成 GitHub Release 正文。只用标准库。

    # 桌面端（版本号在 desktop/pyproject.toml）
    python tools/release/prepare_release.py --tag v0.4.0 --pyproject desktop/pyproject.toml \
        --notes-dir docs/release-notes --body-out build/release-body.md

    # Android（版本号在 gradle.properties 的 buildVersionName）
    python tools/release/prepare_release.py --tag v0.2.0 --gradle-properties gradle.properties \
        --notes-dir docs/release-notes --body-out build/release-body.md

在 GitHub Actions 里运行时把 version / tag / prerelease / notes_zh / notes_en / body 写进 $GITHUB_OUTPUT。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_update_json import ManifestError, markdown_to_text, parse_semver  # noqa: E402


def version_from_pyproject(path: Path) -> str:
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    try:
        return str(data["project"]["version"])
    except KeyError:
        raise ManifestError(f"{path} 里没有 [project] version") from None


def version_from_gradle_properties(path: Path) -> str:
    """取最后一个未注释的 buildVersionName=…（与 Gradle 读取 properties 的行为一致）。"""
    version = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*buildVersionName\s*[=:]\s*(.*?)\s*$", line)
        if m:
            version = m.group(1)
    if not version:
        raise ManifestError(f"{path} 里没有设置 buildVersionName")
    return version


def tag_to_version(tag: str) -> str:
    tag = tag.removeprefix("refs/tags/")
    if not tag.startswith("v"):
        raise ManifestError(f"发布标签必须是 v 开头，如 v0.4.0：{tag!r}")
    return tag[1:]


def notes_paths(notes_dir: Path, version: str) -> tuple[Path, Path]:
    return notes_dir / f"{version}.zh.md", notes_dir / f"{version}.en.md"


def check_notes(notes_dir: Path, version: str) -> tuple[Path, Path]:
    zh, en = notes_paths(notes_dir, version)
    problems = []
    for path, lang in ((zh, "中文"), (en, "英文")):
        if not path.is_file():
            problems.append(f"缺少{lang}更新说明 {path.as_posix()}")
        elif not markdown_to_text(path.read_text(encoding="utf-8-sig")):
            problems.append(f"{lang}更新说明 {path.as_posix()} 是空的（只有标题或注释）")
    if problems:
        raise ManifestError(
            "；".join(problems)
            + f"。请按 {notes_dir.as_posix()}/<版本>.zh.md / .en.md 的约定写好更新说明后重新打标签。"
        )
    return zh, en


def strip_for_body(md: str) -> str:
    """Release 正文：去掉 HTML 注释和一级标题，其余标题降一级（外层已有「## 更新内容」），其他 Markdown 原样保留。"""
    text = re.sub(r"<!--.*?-->", "", md.replace("\r\n", "\n"), flags=re.S)
    lines = [ln for ln in text.split("\n") if not re.match(r"^\s*#\s", ln)]
    lines = [re.sub(r"^(\s*)(#{2,5})\s", r"\1\2# ", ln) for ln in lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def release_body(zh: Path, en: Path, manifest_hint: str | None = None) -> str:
    parts = [
        "## 更新内容",
        "",
        strip_for_body(zh.read_text(encoding="utf-8-sig")),
        "",
        "## What's new",
        "",
        strip_for_body(en.read_text(encoding="utf-8-sig")),
    ]
    if manifest_hint:
        parts += ["", "---", "", manifest_hint]
    return "\n".join(parts).strip() + "\n"


def is_newest_stable(version: str, existing_tags: list[str]) -> bool:
    """正式版且不低于所有已发布的正式版时才标为 Latest（给旧版本发补丁时不抢 latest）。"""
    major, minor, patch, pre = parse_semver(version)
    if pre is not None:
        return False
    mine = (major, minor, patch)
    for tag in existing_tags:
        tag = tag.strip()
        if not tag.startswith("v"):
            continue
        try:
            o_major, o_minor, o_patch, o_pre = parse_semver(tag[1:])
        except ManifestError:
            continue
        if o_pre is None and (o_major, o_minor, o_patch) > mine:
            return False
    return True


def write_github_output(values: dict[str, str]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8", newline="\n") as f:  # Windows 上也写 LF
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="发布前检查版本号与更新说明，生成 Release 正文")
    p.add_argument("--tag", help="Git 标签，如 v0.4.0（也接受 refs/tags/v0.4.0）；省略时取源码版本（试运行，不比对标签）")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pyproject", type=Path, help="桌面端 pyproject.toml")
    src.add_argument("--gradle-properties", type=Path, help="Android gradle.properties")
    p.add_argument("--notes-dir", type=Path, default=Path("docs/release-notes"))
    p.add_argument("--body-out", type=Path, help="把 Release 正文写到这个文件")
    p.add_argument("--manifest-hint", help="附在 Release 正文末尾的一段说明")
    p.add_argument("--existing-tags", type=Path,
                   help="已发布 Release 的标签列表（每行一个，如 gh release list 的输出），用来决定是否标为 Latest")
    args = p.parse_args(argv)

    try:
        if args.pyproject:
            source, src_version = args.pyproject, version_from_pyproject(args.pyproject)
        else:
            source, src_version = args.gradle_properties, version_from_gradle_properties(args.gradle_properties)
        tag_version = tag_to_version(args.tag) if args.tag else src_version
        parse_semver(tag_version)
        if src_version != tag_version:
            raise ManifestError(
                f"标签 {args.tag} 与 {source.as_posix()} 里的版本 {src_version} 不一致。"
                "请先改版本号并提交，再给那个提交打标签。"
            )
        prerelease = parse_semver(tag_version)[3] is not None
        zh, en = check_notes(args.notes_dir, tag_version)
    except ManifestError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    existing = []
    if args.existing_tags and args.existing_tags.is_file():
        existing = args.existing_tags.read_text(encoding="utf-8").split()
    body = ""
    if args.body_out:
        args.body_out.parent.mkdir(parents=True, exist_ok=True)
        args.body_out.write_text(release_body(zh, en, args.manifest_hint), encoding="utf-8", newline="\n")
        body = args.body_out.as_posix()
    write_github_output({
        "version": tag_version,
        "tag": f"v{tag_version}",
        "prerelease": "true" if prerelease else "false",
        "notes_zh": zh.as_posix(),
        "notes_en": en.as_posix(),
        "body": body,
        "make_latest": "true" if is_newest_stable(tag_version, existing) else "false",
    })
    kind = "预发布" if prerelease else "正式版"
    print(f"OK：v{tag_version}（{kind}），更新说明 {zh.as_posix()} / {en.as_posix()}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
