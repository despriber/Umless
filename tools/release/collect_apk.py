#!/usr/bin/env python3
"""从 Android Gradle 的 output-metadata.json 找到某个 ABI 的正式包，复制成发布用的文件名并核对 versionCode。

    python tools/release/collect_apk.py \
        --metadata app/build/outputs/apk/release/output-metadata.json \
        --abi arm64-v8a --short arm64 --version 0.2.0 --out-dir "$RUNNER_TEMP/dist"

输出 <out-dir>/Umless-<version>-<short>.apk，以及同名 .asset 文件（内容是 make_update_json.py 的
--asset 参数：android-<abi>:apk:<文件名>:<versionCode>）。versionCode 必须等于 spec/update.md §5 的公式结果，
签名缺失（-unsigned.apk）时报错。只用标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_update_json import ABI_DIGIT, ManifestError, abi_version_code  # noqa: E402


def find_output(metadata_path: Path, abi: str) -> tuple[Path, int, str]:
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"找不到 {metadata_path}（assembleRelease 没有成功？）") from None
    elements = meta.get("elements") or []
    matches = []
    for el in elements:
        filters = {f.get("filterType"): f.get("value") for f in el.get("filters") or []}
        el_abi = filters.get("ABI")
        if el_abi == abi or (el_abi is None and len(elements) == 1):
            matches.append(el)
    if len(matches) != 1:
        found = [{f.get("value") for f in el.get("filters") or []} for el in elements]
        raise ManifestError(f"{metadata_path} 里没有唯一的 {abi} 安装包（找到 {found}）")
    el = matches[0]
    apk = metadata_path.parent / el["outputFile"]
    if not apk.is_file():
        raise ManifestError(f"安装包不存在：{apk}")
    return apk, int(el["versionCode"]), str(el.get("versionName", ""))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--metadata", type=Path, required=True, help="app/build/outputs/apk/release/output-metadata.json")
    p.add_argument("--abi", required=True, choices=sorted(a.removeprefix("android-") for a in ABI_DIGIT))
    p.add_argument("--short", required=True, help="文件名里的 ABI 简称，如 arm64")
    p.add_argument("--version", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args(argv)

    platform = f"android-{args.abi}"
    try:
        apk, version_code, version_name = find_output(args.metadata, args.abi)
        if apk.name.endswith("-unsigned.apk"):
            raise ManifestError(f"{apk.name} 没有签名：检查 SIGN_KEY_BASE64 / SIGN_KEY_PWD / SIGN_KEY_ALIAS")
        if version_name and version_name != args.version:
            raise ManifestError(f"安装包 versionName {version_name} 与发布版本 {args.version} 不一致")
        expected = abi_version_code(args.version, platform)
        if version_code != expected:
            raise ManifestError(
                f"安装包 versionCode {version_code} 与 spec §5 公式结果 {expected} 不一致（版本 {args.version}，{args.abi}）"
            )
    except ManifestError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = f"Umless-{args.version}-{args.short}.apk"
    dest = args.out_dir / name
    shutil.copyfile(apk, dest)
    asset = f"{platform}:apk:{name}:{version_code}"
    (args.out_dir / f"{name}.asset").write_text(asset + "\n", encoding="utf-8")
    print(f"{apk} -> {dest}（versionCode {version_code}）")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"apk={dest.as_posix()}\nname={name}\nversion_code={version_code}\nasset={asset}\n")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
