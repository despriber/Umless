"""发布脚本的单元测试。运行：cd desktop && uv run pytest ../tools/release/tests -q
（或任意装了 pytest 的 Python ≥3.11：python -m pytest tools/release/tests）"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

RELEASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RELEASE_DIR))

import make_update_json as m  # noqa: E402
import prepare_release as pr  # noqa: E402

REPO_ROOT = RELEASE_DIR.parent.parent
SPEC_EXAMPLE_TIME = "2026-11-05T10:00:00Z"


def _file(tmp_path: Path, name: str, content: bytes = b"umless") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ---------------------------------------------------------------- 版本号


@pytest.mark.parametrize(
    ("version", "code"),
    [("0.2.0", 2000), ("0.1.2", 1002), ("1.2.3", 1_002_003), ("0.2.0-beta.1", 2000), ("10.999.999", 10_999_999)],
)
def test_base_version_code(version, code):
    assert m.base_version_code(version) == code


def test_abi_version_code_matches_spec_formula():
    # spec §5 公式：(0×1 000 000 + 2×1 000 + 0)×10 + 2 = 20002。
    assert m.abi_version_code("0.2.0", "android-arm64-v8a") == 20002
    assert m.abi_version_code("0.2.0", "android-armeabi-v7a") == 20001
    assert m.abi_version_code("0.2.0", "android-x86_64") == 20004
    assert m.abi_version_code("1.2.3", "android-arm64-v8a") == 10020032


def test_new_scheme_is_above_legacy_112():
    assert m.abi_version_code("0.1.2", "android-arm64-v8a") > 112


@pytest.mark.parametrize("bad", ["0.2", "v0.2.0", "0.2.0.1", "01.2.0", "0.2.0-", "0.2.0+build", "", "0.1000.0"])
def test_bad_semver(bad):
    with pytest.raises(m.ManifestError):
        m.parse_semver(bad)


def test_prerelease_detection():
    assert m.is_prerelease("0.4.0-beta.1")
    assert m.is_prerelease("0.4.0-rc.2")
    assert not m.is_prerelease("0.4.0")


# ---------------------------------------------------------------- --asset 解析


def test_parse_asset_with_windows_path_and_version_code():
    assert m.parse_asset_arg(r"android-arm64-v8a:apk:D:\x\Umless-0.2.0-arm64.apk:20002") == (
        "android-arm64-v8a", "apk", Path(r"D:\x\Umless-0.2.0-arm64.apk"), 20002)
    platform, kind, path, vc = m.parse_asset_arg(r"windows-x64:installer:C:\dist\Umless-Setup-0.4.0.exe")
    assert (platform, kind, vc) == ("windows-x64", "installer", None)
    assert path == Path(r"C:\dist\Umless-Setup-0.4.0.exe")


@pytest.mark.parametrize("bad", ["windows-x64:installer", "windows-x64::a.exe", "a"])
def test_parse_asset_bad(bad):
    with pytest.raises(m.ManifestError):
        m.parse_asset_arg(bad)


# ---------------------------------------------------------------- 生成


def test_desktop_manifest(tmp_path):
    setup = _file(tmp_path, "Umless-Setup-0.4.0.exe", b"installer-bytes")
    portable = _file(tmp_path, "Umless-0.4.0-portable.zip", b"zip")
    man = m.build_manifest(
        app="umless-desktop", version="0.4.0", notes_zh="修复", notes_en="Fixed",
        published_at=SPEC_EXAMPLE_TIME,
        assets=[f"windows-x64:installer:{setup}", f"windows-x64:portable:{portable}"],
    )
    assert list(man) == ["schema", "app", "version", "published_at", "notes", "assets"]
    assert man["schema"] == 1 and "version_code" not in man
    inst = man["assets"][0]
    assert inst == {
        "platform": "windows-x64",
        "kind": "installer",
        "name": "Umless-Setup-0.4.0.exe",
        "url": "https://github.com/despriber/Umless/releases/download/v0.4.0/Umless-Setup-0.4.0.exe",
        "size": len(b"installer-bytes"),
        "sha256": hashlib.sha256(b"installer-bytes").hexdigest(),
    }
    assert man["assets"][1]["kind"] == "portable"


def test_android_manifest_matches_spec_shape(tmp_path):
    apk = _file(tmp_path, "Umless-0.2.0-arm64.apk", b"apk")
    man = m.build_manifest(
        app="umless-android", version="0.2.0", notes_zh="修复……", notes_en="Fixed …",
        published_at=SPEC_EXAMPLE_TIME, assets=[f"android-arm64-v8a:apk:{apk}:20002"],
    )
    assert list(man) == ["schema", "app", "version", "version_code", "published_at", "notes", "assets"]
    assert man["version_code"] == 2000
    a = man["assets"][0]
    assert a["url"] == "https://github.com/despriber/Umless-Android/releases/download/v0.2.0/Umless-0.2.0-arm64.apk"
    assert a["version_code"] == 20002
    assert list(a) == ["platform", "kind", "name", "url", "size", "sha256", "version_code"]


def test_android_version_codes_computed_when_omitted(tmp_path):
    apk = _file(tmp_path, "Umless-0.3.1-arm64.apk")
    man = m.build_manifest(app="umless-android", version="0.3.1", notes_zh="x", assets=[f"android-arm64-v8a:apk:{apk}"])
    assert man["version_code"] == 3001
    assert man["assets"][0]["version_code"] == 30012


def test_android_wrong_version_code_rejected(tmp_path):
    apk = _file(tmp_path, "a.apk")
    with pytest.raises(m.ManifestError, match="20002"):
        m.build_manifest(app="umless-android", version="0.2.0", notes_zh="x",
                         assets=[f"android-arm64-v8a:apk:{apk}:112"])
    with pytest.raises(m.ManifestError, match="2000"):
        m.build_manifest(app="umless-android", version="0.2.0", version_code=112, notes_zh="x",
                         assets=[f"android-arm64-v8a:apk:{apk}"])


def test_custom_repo_and_tag(tmp_path):
    f = _file(tmp_path, "Umless-Setup-0.4.0-beta.1.exe")
    man = m.build_manifest(app="umless-desktop", version="0.4.0-beta.1", notes_en="x", repo="me/fork",
                           tag="v0.4.0-beta.1", assets=[f"windows-x64:installer:{f}"])
    assert man["assets"][0]["url"] == (
        "https://github.com/me/fork/releases/download/v0.4.0-beta.1/Umless-Setup-0.4.0-beta.1.exe")


@pytest.mark.parametrize(
    ("asset", "match"),
    [
        ("android-arm64-v8a:apk:{f}", "不属于"),
        ("windows-x64:apk:{f}", "不支持"),
        ("linux-x64:installer:{f}", "未知平台"),
        ("windows-x64:msi:{f}", "未知类型"),
        ("windows-x64:installer:{f}.missing", "不存在"),
    ],
)
def test_desktop_bad_assets(tmp_path, asset, match):
    f = _file(tmp_path, "x.exe")
    with pytest.raises(m.ManifestError, match=match):
        m.build_manifest(app="umless-desktop", version="0.4.0", notes_zh="x", assets=[asset.format(f=f)])


def test_duplicate_platform_kind_rejected(tmp_path):
    a, b = _file(tmp_path, "a.exe"), _file(tmp_path, "b.exe")
    with pytest.raises(m.ManifestError, match="重复"):
        m.build_manifest(app="umless-desktop", version="0.4.0", notes_zh="x",
                         assets=[f"windows-x64:installer:{a}", f"windows-x64:installer:{b}"])


def test_empty_notes_rejected(tmp_path):
    f = _file(tmp_path, "a.exe")
    with pytest.raises(m.ManifestError, match="notes"):
        m.build_manifest(app="umless-desktop", version="0.4.0", assets=[f"windows-x64:installer:{f}"])


def test_empty_file_rejected(tmp_path):
    f = _file(tmp_path, "a.exe", b"")
    with pytest.raises(m.ManifestError, match="为空"):
        m.build_manifest(app="umless-desktop", version="0.4.0", notes_zh="x", assets=[f"windows-x64:installer:{f}"])


# ---------------------------------------------------------------- 更新说明


def test_markdown_to_text():
    md = (
        "<!-- 写给用户看的，不要写实现细节 -->\n"
        "# Umless 0.4.0\n\n"
        "## 新增\n\n"
        "- **自动更新**：设置里可以 `检查更新`，详见 [文档](https://x)\n\n\n\n"
        "## 修复\n- 修复热键\r\n"
    )
    assert m.markdown_to_text(md) == "新增\n\n- 自动更新：设置里可以 检查更新，详见 文档\n\n修复\n- 修复热键"


def test_read_notes_file_text_and_missing(tmp_path):
    p = tmp_path / "0.4.0.zh.md"
    p.write_text("# 标题\n\n- 一\n- 二\n", encoding="utf-8")
    assert m.read_notes(str(p), "中文") == "- 一\n- 二"
    assert m.read_notes("Fixed A/B switching\\nAdded X", "英文") == "Fixed A/B switching\nAdded X"
    with pytest.raises(m.ManifestError, match="找不到"):
        m.read_notes(str(tmp_path / "nope.en.md"), "英文")
    assert m.read_notes(None, "中文") == ""


# ---------------------------------------------------------------- 校验与命令行


def test_cli_roundtrip_and_check(tmp_path, capsys):
    apk = _file(tmp_path, "Umless-0.2.0-arm64.apk", b"\x00" * 1000)
    zh = tmp_path / "0.2.0.zh.md"
    zh.write_text("# Umless 0.2.0\n\n- 修复……\n", encoding="utf-8")
    out = tmp_path / "out" / "update.json"
    rc = m.main([
        "--app", "umless-android", "--version", "0.2.0", "--version-code", "2000",
        "--notes-zh", str(zh), "--notes-en", "Fixed …",
        "--published-at", SPEC_EXAMPLE_TIME,
        "--asset", f"android-arm64-v8a:apk:{apk}:20002", "-o", str(out),
    ])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["notes"] == {"zh": "- 修复……", "en": "Fixed …"}
    assert data["assets"][0]["size"] == 1000
    assert "修复" in out.read_text(encoding="utf-8")  # ensure_ascii=False
    assert m.main(["--check", str(out)]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_spec_example_shape(tmp_path):
    doc = {
        "schema": 1, "app": "umless-android", "version": "0.2.0", "version_code": 2000,
        "published_at": SPEC_EXAMPLE_TIME,
        "notes": {"zh": "修复……\n新增……", "en": "Fixed …\nAdded …"},
        "assets": [{
            "platform": "android-arm64-v8a", "kind": "apk", "name": "Umless-0.2.0-arm64.apk",
            "url": "https://github.com/despriber/Umless-Android/releases/download/v0.2.0/Umless-0.2.0-arm64.apk",
            "size": 67196611, "sha256": "e" * 64, "version_code": 20002,
        }],
    }
    m.validate_manifest(doc)
    bad = json.loads(json.dumps(doc))
    bad["assets"][0].update(sha256="E" * 64, version_code=112, url="http://x/other.apk")
    bad["published_at"] = "2026-11-05 10:00"
    del bad["version_code"]
    with pytest.raises(m.ManifestError) as e:
        m.validate_manifest(bad)
    msg = str(e.value)
    for needle in ("sha256", "version_code", "url", "published_at"):
        assert needle in msg


def test_check_cli_reports_errors(tmp_path, capsys):
    p = tmp_path / "update.json"
    p.write_text('{"schema": 1, "app": "umless-desktop", "version": "x", "assets": []}', encoding="utf-8")
    assert m.main(["--check", str(p)]) == 1
    err = capsys.readouterr().err
    assert "版本号" in err and "assets" in err
    p.write_text("not json", encoding="utf-8")
    assert m.main(["--check", str(p)]) == 1
    assert m.main(["--check", str(tmp_path / "missing.json")]) == 1


def test_cli_requires_app_and_assets():
    with pytest.raises(SystemExit):
        m.main(["--version", "0.4.0"])
    with pytest.raises(SystemExit):
        m.main(["--app", "umless-desktop", "--version", "0.4.0"])


def test_spec_document_lists_same_platforms_and_kinds():
    """spec/update.md 是唯一事实来源：脚本里的平台和类型要与它一致。"""
    spec = (REPO_ROOT / "spec" / "update.md").read_text(encoding="utf-8")
    for platform in m.PLATFORMS:
        assert f"`{platform}`" in spec
    for kind in m.KINDS:
        assert f"`{kind}`" in spec
    for repo in m.APPS.values():
        assert f"`{repo}`" in spec


# ---------------------------------------------------------------- prepare_release


def _notes(tmp_path: Path, version: str, zh: str = "# x\n\n- 修复\n", en: str = "- Fixed\n") -> Path:
    d = tmp_path / "docs" / "release-notes"
    d.mkdir(parents=True, exist_ok=True)
    if zh is not None:
        (d / f"{version}.zh.md").write_text(zh, encoding="utf-8")
    if en is not None:
        (d / f"{version}.en.md").write_text(en, encoding="utf-8")
    return d


def test_prepare_release_desktop(tmp_path, monkeypatch, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "umless"\nversion = "0.4.0-beta.1"\n', encoding="utf-8")
    notes = _notes(tmp_path, "0.4.0-beta.1")
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    body = tmp_path / "body.md"
    rc = pr.main(["--tag", "refs/tags/v0.4.0-beta.1", "--pyproject", str(pyproject),
                  "--notes-dir", str(notes), "--body-out", str(body)])
    assert rc == 0
    outputs = dict(line.split("=", 1) for line in gh_out.read_text(encoding="utf-8").splitlines())
    assert outputs["version"] == "0.4.0-beta.1"
    assert outputs["tag"] == "v0.4.0-beta.1"
    assert outputs["prerelease"] == "true"
    text = body.read_text(encoding="utf-8")
    assert "- 修复" in text and "- Fixed" in text and "# x" not in text


def test_prepare_release_gradle_and_mismatch(tmp_path, capsys):
    props = tmp_path / "gradle.properties"
    props.write_text("#buildVersionName=\nbuildVersionName=0.1.1\nbuildVersionName = 0.2.0\n", encoding="utf-8")
    notes = _notes(tmp_path, "0.2.0")
    assert pr.version_from_gradle_properties(props) == "0.2.0"
    assert pr.main(["--tag", "v0.2.0", "--gradle-properties", str(props), "--notes-dir", str(notes)]) == 0
    assert pr.main(["--tag", "v0.2.1", "--gradle-properties", str(props), "--notes-dir", str(notes)]) == 1
    assert "不一致" in capsys.readouterr().err
    assert pr.main(["--tag", "0.2.0", "--gradle-properties", str(props), "--notes-dir", str(notes)]) == 1


def test_prepare_release_missing_or_empty_notes(tmp_path, capsys):
    props = tmp_path / "gradle.properties"
    props.write_text("buildVersionName=0.2.0\n", encoding="utf-8")
    notes = _notes(tmp_path, "0.2.0", en=None)
    assert pr.main(["--tag", "v0.2.0", "--gradle-properties", str(props), "--notes-dir", str(notes)]) == 1
    assert "0.2.0.en.md" in capsys.readouterr().err
    (notes / "0.2.0.en.md").write_text("# Umless 0.2.0\n<!-- TODO -->\n", encoding="utf-8")
    assert pr.main(["--tag", "v0.2.0", "--gradle-properties", str(props), "--notes-dir", str(notes)]) == 1
    assert "空的" in capsys.readouterr().err



def test_prepare_release_without_tag_is_dry_run(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.4.0"\n', encoding="utf-8")
    notes = _notes(tmp_path, "0.4.0")
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    assert pr.main(["--pyproject", str(pyproject), "--notes-dir", str(notes)]) == 0
    assert "tag=v0.4.0\nprerelease=false" in gh_out.read_text(encoding="utf-8")


def test_local_base_url_for_testing(tmp_path):
    f = _file(tmp_path, "Umless-Setup-0.9.0.exe")
    man = m.build_manifest(app="umless-desktop", version="0.9.0", notes_zh="x",
                           base_url="http://127.0.0.1:8000/", assets=[f"windows-x64:installer:{f}"])
    assert man["assets"][0]["url"] == "http://127.0.0.1:8000/Umless-Setup-0.9.0.exe"
    out = tmp_path / "update.json"
    m.dump_manifest(man, out)
    assert m.main(["--check", str(out)]) == 1  # 正式校验不接受 http
    assert m.main(["--check", str(out), "--check-local"]) == 0
    with pytest.raises(m.ManifestError, match="base-url"):
        m.build_manifest(app="umless-desktop", version="0.9.0", notes_zh="x", base_url="ftp://x",
                         assets=[f"windows-x64:installer:{f}"])


@pytest.mark.parametrize(
    ("version", "tags", "latest"),
    [
        ("0.4.0", [], True),
        ("0.4.0", ["v0.3.0", "v0.4.0", "v0.5.0-beta.1", "0.9.9", "junk"], True),
        ("0.3.2", ["v0.3.1", "v0.4.0"], False),  # 给旧版本发补丁不抢 latest
        ("0.5.0-beta.1", ["v0.4.0"], False),
    ],
)
def test_is_newest_stable(version, tags, latest):
    assert pr.is_newest_stable(version, tags) is latest


def test_make_latest_output(tmp_path, monkeypatch):
    props = tmp_path / "gradle.properties"
    props.write_text("buildVersionName=0.1.3\n", encoding="utf-8")
    notes = _notes(tmp_path, "0.1.3")
    tags = tmp_path / "tags.txt"
    tags.write_text("v0.2.0\nv0.1.2\n", encoding="utf-8")
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    assert pr.main(["--tag", "v0.1.3", "--gradle-properties", str(props), "--notes-dir", str(notes),
                    "--existing-tags", str(tags)]) == 0
    assert "make_latest=false" in gh_out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- collect_apk

import collect_apk as ca  # noqa: E402


def _apk_metadata(tmp_path: Path, version_code: int, output: str = "org.umless.ime-0.2.0-arm64-v8a-release.apk",
                  version_name: str = "0.2.0") -> Path:
    out = tmp_path / "apk" / "release"
    out.mkdir(parents=True)
    (out / output).write_bytes(b"PK-apk")
    meta = {
        "version": 3, "applicationId": "org.umless.ime", "variantName": "release",
        "elements": [{
            "type": "ONE_OF_MANY", "filters": [{"filterType": "ABI", "value": "arm64-v8a"}],
            "versionCode": version_code, "versionName": version_name, "outputFile": output,
        }],
    }
    path = out / "output-metadata.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_collect_apk(tmp_path, monkeypatch):
    meta = _apk_metadata(tmp_path, 20002)
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    dist = tmp_path / "dist"
    assert ca.main(["--metadata", str(meta), "--abi", "arm64-v8a", "--short", "arm64",
                    "--version", "0.2.0", "--out-dir", str(dist)]) == 0
    assert (dist / "Umless-0.2.0-arm64.apk").read_bytes() == b"PK-apk"
    asset = (dist / "Umless-0.2.0-arm64.apk.asset").read_text(encoding="utf-8").strip()
    assert asset == "android-arm64-v8a:apk:Umless-0.2.0-arm64.apk:20002"
    assert "version_code=20002" in gh_out.read_text(encoding="utf-8")
    # 生成清单时直接用 .asset 的内容（在 dist 目录里运行）
    monkeypatch.chdir(dist)
    man = m.build_manifest(app="umless-android", version="0.2.0", notes_zh="x", assets=[asset])
    assert man["assets"][0]["version_code"] == 20002


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"version_code": 112}, "versionCode 112"),  # 上游沿用的旧 versionCode
        ({"version_code": 20002, "version_name": "0.1.9"}, "versionName"),
        ({"version_code": 20002, "output": "app-arm64-v8a-release-unsigned.apk"}, "没有签名"),
    ],
)
def test_collect_apk_rejects(tmp_path, capsys, kwargs, match):
    meta = _apk_metadata(tmp_path, **kwargs)
    assert ca.main(["--metadata", str(meta), "--abi", "arm64-v8a", "--short", "arm64",
                    "--version", "0.2.0", "--out-dir", str(tmp_path / "dist")]) == 1
    assert match in capsys.readouterr().err


def test_collect_apk_wrong_abi(tmp_path, capsys):
    meta = _apk_metadata(tmp_path, 20001)
    assert ca.main(["--metadata", str(meta), "--abi", "armeabi-v7a", "--short", "armv7",
                    "--version", "0.2.0", "--out-dir", str(tmp_path / "dist")]) == 1
    assert "armeabi-v7a" in capsys.readouterr().err


def test_release_body_demotes_headings():
    assert pr.strip_for_body("<!-- c -->\n# Umless 0.4.0\n\n## 新增\n\n- 一\n### 细节\n") == "### 新增\n\n- 一\n#### 细节"
