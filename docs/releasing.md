# 发布流程

Umless 的两个客户端各自在 GitHub Releases 发布：

| 应用 | 仓库 | 工作流 | 产物 |
|---|---|---|---|
| Windows 桌面端 | `despriber/Umless`（本仓库） | `.github/workflows/desktop-release.yml` | `Umless-Setup-<版本>.exe`、`Umless-<版本>-portable.zip`、`update.json` |
| Android | `despriber/Umless-Android` | `.github/workflows/umless-release.yml` | `Umless-<版本>-arm64.apk`、`update.json` |

两边都是**推送 `v*` 标签 → 工作流打包 → 生成 `update.json` → 发布 Release**，客户端从
`releases/latest/download/update.json` 读取更新清单（格式见 `spec/update.md`）。本文写桌面端与两边共用的部分；
Android 的签名、构建环境与 Obtainium 见 Umless-Android 仓库的 `RELEASING.md`。

发布脚本都在 `tools/release/`（只用 Python 标准库，Android 的工作流也从这里调用）：

| 脚本 | 作用 |
|---|---|
| `prepare_release.py` | 核对标签与源码里的版本号、检查更新说明是否存在，生成 Release 正文，判断是否预发布、是否标为 Latest |
| `make_update_json.py` | 生成 `update.json`（计算大小与 sha256、校验平台 / 类型 / versionCode）；`--check` 校验已有清单 |
| `collect_apk.py` | Android：从 Gradle 的 `output-metadata.json` 取 APK，改名为 `Umless-<版本>-<abi>.apk` 并核对 versionCode |

测试：`cd desktop && uv run pytest ../tools/release/tests -q`（`desktop-ci.yml` 里也会跑）。

## 一次性准备

1. 在 GitHub 创建公开仓库 `despriber/Umless`，推送主分支。仓库名改了的话要同时改
   `desktop/umless/updater.py` 的 `REPO` 和 `tools/release/make_update_json.py` 的 `APPS`（见 `spec/update.md` 第 1 节）。
2. Settings → Actions → General：允许运行 Actions。工作流自己声明了 `contents: write` 权限，用自带的
   `GITHUB_TOKEN` 建 Release，**桌面端不需要配置任何机密**。
3. 目前安装程序没有代码签名，首次运行时 Windows SmartScreen 会提示「已保护你的电脑」，用户要点「更多信息 → 仍要运行」。
   以后有了代码签名证书，再在 `desktop/packaging/build.py` 之后加签名步骤。
4. Android 仓库的准备（签名密钥机密等）见 Umless-Android 的 `RELEASING.md`。

## 版本号规则

- 语义化版本 `MAJOR.MINOR.PATCH`。正式版不带后缀；`-beta.1`、`-rc.1` 之类的后缀表示预发布。
- 标签就是 `v` + 版本号，如 `v0.4.0`、`v0.4.0-beta.1`。标签和源码里的版本不一致时工作流直接失败。
- 桌面端版本号写在 `desktop/pyproject.toml` 的 `version`，`desktop/umless/__init__.py` 的 `__version__`
  必须相同（打包脚本会检查）。客户端用 `__version__` 和清单比较：数字逐段比较，`1.2.0` > `1.2.0-beta.1`。
- Android 版本号只在 `gradle.properties` 的 `buildVersionName` 改，versionCode 由它算出：
  `(MAJOR × 1 000 000 + MINOR × 1 000 + PATCH) × 10 + ABI`（`spec/update.md` 第 5 节）。所以 MINOR、PATCH 不能超过 999。
- 两端版本号各自独立，不要求同步。
- **预发布**：工作流把它发布成 GitHub 的 Pre-release，不会成为 `releases/latest`，因此不会推送给用户。
- **Latest**：正式版只有不低于已发布的所有正式版时才标为 Latest。给旧版本线发补丁（比如已有 0.4.0 时发 0.3.2）
  不会抢走 Latest，用户仍然看到 0.4.0。

## 发布步骤（桌面端）

1. **改版本号**：`desktop/pyproject.toml` 的 `version` 和 `desktop/umless/__init__.py` 的 `__version__`
   （例如都改成 `0.4.0`），然后 `cd desktop && uv lock` 让 `uv.lock` 里的版本跟着变。
2. **写更新说明**：`docs/release-notes/<版本>.zh.md` 和 `docs/release-notes/<版本>.en.md`，两个都要有，
   缺一个或者内容为空（只有标题和注释）时工作流会失败并给出文件名。
   - 写给用户看：每条一句话，说「能做什么 / 修好了什么」，不写实现细节。
   - 一级标题和 HTML 注释会被去掉；GitHub Release 正文保留 Markdown（中文在前、英文在后），
     `update.json` 里转成纯文本，客户端按界面语言显示。
   - 可以复制上一版的文件改。
3. **本地检查**：
   ```bash
   cd desktop
   uv run pytest tests -q && uv run pytest ../tools/release/tests -q
   cd ..
   python tools/release/prepare_release.py --tag v0.4.0 --pyproject desktop/pyproject.toml
   ```
   想在 CI 上先试打包，可以在 Actions 页面手动运行「Desktop release」（workflow_dispatch）：
   它会测试、打包、生成 `update.json` 并作为构建产物（Artifacts）上传，但不发布 Release。
4. **提交、打标签、推送**：
   ```bash
   git commit -am "release: 0.4.0"
   git tag -a v0.4.0 -m "Umless 0.4.0"
   git push origin main
   git push origin v0.4.0
   ```
5. **工作流会做的事**（windows-latest，约 15 分钟）：
   1. 核对标签与 `pyproject.toml` 的版本，检查更新说明；
   2. `uv sync --locked`，跑 `desktop/tests` 与发布脚本的测试；
   3. `choco install innosetup`，运行 `desktop/packaging/build.py --require-installer`，
      得到 `desktop/dist/Umless-Setup-<版本>.exe` 和 `desktop/dist/Umless-<版本>-portable.zip`；
   4. 生成 `update.json`（平台 `windows-x64`：`installer` + `portable` 两项）并用 `--check` 校验；
   5. 先建**草稿** Release，上传安装程序、zip、`update.json`，全部传完再公开
      （避免 `releases/latest` 指向一个还没有 `update.json` 的 Release）。带后缀的版本发布成 Pre-release。
6. **核对**：打开 Release 页面看三个附件都在；
   `curl -L https://github.com/despriber/Umless/releases/latest/download/update.json` 应返回新版本。
   在一台装着旧版的电脑上「设置 → 关于 → 检查更新」，应提示新版本并能下载安装。

### 客户端怎么拿到更新

- 安装版每 24 小时（启动 5 分钟后第一次）读一次 `releases/latest/download/update.json`，按下载源设置直连或走镜像（默认自动，见 `spec/update.md` 第 2 节）；
  版本更高就在主界面和托盘提示。用户确认后下载 `installer`，校验 sha256，退出 Umless，静默运行安装程序，装完自动重启。
- 免安装版和源码运行只提示，不自动安装（`portable` 一项只给手动下载用）。
- 用户点过「跳过此版本」的版本不再自动提示，手动检查仍会显示。

## 发布前测试更新流程

配置项 `update.manifest_url`（默认空，设置界面不显示）可以把清单地址换成别的。桌面端在
`%APPDATA%\Umless\config.toml` 里改：

```toml
[update]
manifest_url = "https://github.com/despriber/Umless/releases/download/v0.4.0-beta.1/update.json"
```

测完把它改回 `""`。注意：**草稿 Release 的附件要登录才能下载，客户端读不到**，所以测试用下面两种办法之一。

**办法一：预发布。** 把版本改成 `0.4.0-beta.1`，写好 `docs/release-notes/0.4.0-beta.1.*.md`，打标签
`v0.4.0-beta.1` 推送。工作流发布成 Pre-release（不推送给用户），清单地址就是
`https://github.com/despriber/Umless/releases/download/v0.4.0-beta.1/update.json`。
在装着旧正式版的电脑上设置上面的 `manifest_url`，手动检查更新，走完下载、校验、安装、重启。

**办法二：本机文件。** 自定义地址只直连、不走镜像，`http://` 也可以：

```bash
cd desktop
# 改成一个比本机已安装版本更高的版本号（不提交）后打包
uv run --with pyinstaller python packaging/build.py
cd ..
python tools/release/make_update_json.py --app umless-desktop --version 0.4.0 \
  --notes-zh docs/release-notes/0.4.0.zh.md --notes-en docs/release-notes/0.4.0.en.md \
  --asset windows-x64:installer:desktop/dist/Umless-Setup-0.4.0.exe \
  --asset windows-x64:portable:desktop/dist/Umless-0.4.0-portable.zip \
  --base-url http://127.0.0.1:8000 --output desktop/dist/update.json
python -m http.server 8000 -d desktop/dist
```

然后设 `manifest_url = "http://127.0.0.1:8000/update.json"`。`--base-url` 生成的清单只能用于测试，
正式校验（`--check`）会因为 `http://` 报错，需要时用 `--check --check-local`。

也可以用 workflow_dispatch 的构建产物代替本地打包：下载 Artifacts 解压到一个目录，再按上面生成清单、起本地服务。

## 出问题时

- **工作流中途失败**：Release 停在草稿状态（或者根本没建），用户看不到。修好原因后在 Actions 页面
  「Re-run jobs」即可，同一标签重跑会覆盖已有附件。
- **标签打错了提交、Release 还没公开**：删掉草稿 Release 和标签，重新打：
  `git push origin :refs/tags/v0.4.0 && git tag -d v0.4.0`，改好后重新 `git tag` / `git push`。
- **已经公开的版本有严重问题**：
  1. 先止损：`gh release edit v0.4.0 --prerelease`（或在网页上改成 Pre-release / 删除这个 Release，**保留标签**）。
     `releases/latest` 会回到上一个正式版，它的清单版本不高于已更新用户的版本，所以不会再有人收到 0.4.0。
  2. 修复后发补丁版 `0.4.1`（版本号只增不减，不要复用已发布过的版本号或标签）。已经装了 0.4.0 的用户会收到 0.4.1。
  3. 桌面端用户如果急需退回，可以手动下载旧版安装程序覆盖安装；数据目录 `%APPDATA%\Umless` 不受安装 / 卸载影响，
     但要确认旧版能读新版写过的数据库。Android 无法降级安装（versionCode 只能增大），只能发补丁版。
- **给旧版本线发补丁**：从对应标签拉分支（`git switch -c release/0.3 v0.3.0`），改好后版本号改成 `0.3.1`，
  写更新说明、打 `v0.3.1` 标签推送。它不会成为 Latest（见「版本号规则」）。
- **更新说明写错了**：Release 正文可以直接在网页上改；`update.json` 里的说明要改就重跑工作流，或者用
  `make_update_json.py` 重新生成后 `gh release upload v0.4.0 update.json --clobber`（安装包不变，sha256 不变）。

## 参考

- 更新机制与清单格式：`spec/update.md`
- 打包脚本与安装程序行为：`desktop/packaging/README.md`
- Android 发布：Umless-Android 仓库 `RELEASING.md`
