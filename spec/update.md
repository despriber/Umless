# 应用更新

桌面端（Windows）与 Android 端用同一套更新机制：GitHub Releases 发布安装包与一份更新清单 `update.json`，
客户端读取清单、比较版本、下载并校验后安装。不经过任何自建服务器，不上传任何个人数据。

## 1. 发布位置

| 应用 | 仓库 | 清单地址 |
|---|---|---|
| 桌面端 `umless-desktop` | `despriber/Umless` | `https://github.com/despriber/Umless/releases/latest/download/update.json` |
| Android `umless-android` | `despriber/Umless-Android` | `https://github.com/despriber/Umless-Android/releases/latest/download/update.json` |

- 仓库名在两端代码里各定义为一个常量（桌面 `umless/updater.py` 的 `REPO`，Android `org.umless.update` 的 `REPO`），改名时只改这一处。
- 配置项 `update.manifest_url`（默认空）可覆盖清单地址，用于测试或自建镜像；设置界面不显示。自定义地址始终直连（见第 2 节）。
- 「latest」只指向正式版（GitHub 上非预发布、非草稿的最新 Release），预发布版本不会推送给用户。

## 2. 下载源（GitHub 直连 / 镜像）

有的网络上镜像反复失败而直连正常，有的正相反，所以下载源由用户选择。两端规则完全相同，
**同时用于识别模型下载（SenseVoice 等 sherpa-onnx 模型）与应用更新（清单、安装包）**。

**配置**（`network` 一节，两端 `get_settings().config.network` 都有）：

| 键 | 取值 | 默认 |
|---|---|---|
| `download_source` | `"auto"` 自动 · `"github"` GitHub 直连 · `"mirror"` 镜像 | `"auto"` |
| `mirror_url` | 镜像前缀，必须是带主机名的 `http://` / `https://` 地址；保存时去掉首尾空白并补上结尾 `/` | `"https://gh-proxy.com/"` |

- `save_settings` 收到其他取值时报校验错误（`cfg.bad_download_source` / `cfg.bad_mirror_url`；空串、`null`、`ftp://…`、`gh-proxy.com` 都不合法）。
  手改配置文件写错时按默认值处理，不影响启动。
- 镜像地址 = `mirror_url` + GitHub 原地址，如 `https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/…`。
- **只有 `https://github.com/` 开头的地址才换源**；其他地址（如自定义的 `update.manifest_url`）始终直连。

**顺序**：

- `github`：只直连；`mirror`：只走镜像；失败即失败，不换另一个。
- `auto`：先试 kv `download_source_last`（`"github"` / `"mirror"`）记录的源，没有记录时先走镜像；失败再试另一个。
- 任何模式下**成功**后都把这次用的源写入 `download_source_last`。成功指：识别模型 sha256 校验通过；清单解析成功；
  安装包 sha256（Android 另加签名证书）校验通过。HTTP 404、校验失败都不算成功。

**超时**：清单 10 秒；安装包按连接 20 秒、读取 60 秒无数据视为失败；识别模型 60 秒。GitHub 的跳转（302）要跟随。

**还没有发布版本**：读取清单时，**所有尝试过的源**都返回 HTTP 404（仓库还没有正式 Release）不算检查失败：
`check_update` 返回 `not_published=true`、`error=null`，界面显示「暂时没有可用的更新（还没有发布版本）」。
只要有一个源是别的错误（超时、5xx、内容不是清单），就按普通的检查失败处理。

**测试连接** `test_download_sources()`：两个源同时探测，各自请求 SenseVoice 模型安装包
（`https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/<archive>`，镜像为 `mirror_url` + 该地址）的前 1 KiB：

- 请求头 `Range: bytes=0-1023`，跟随跳转，每个源超时约 8 秒；状态码 200 或 206 **且读到了正文数据**才算通（服务器忽略 Range 时读到第一块数据就断开，不下载整个文件）。
- 结果 `{"github": {"ok", "ms", "error"}, "mirror": {...}, "preferred": "github" | "mirror"}`：`ms` 为耗时毫秒（失败时 `null`）；
  `error` 为原始原因、不翻译（如 `HTTP 403`、`timed out`、`connection reset`），成功时 `null`。网络错误不抛出。
- `preferred`：两个都通时取更快的（一样快时保持原来的记录），只有一个通时取它，都不通时为原来记录的源（没有记录为 `mirror`）。
  有源通时把 `preferred` 写入 `download_source_last`，所以测试结果会影响 `auto` 下一次的顺序。

桌面实现见 `desktop/umless/netsrc.py`（`models.py`、`updater.py` 共用）。

## 3. `update.json`

```json
{
  "schema": 1,
  "app": "umless-android",
  "version": "0.2.0",
  "version_code": 2000,
  "published_at": "2026-11-05T10:00:00Z",
  "notes": { "zh": "修复……\n新增……", "en": "Fixed …\nAdded …" },
  "assets": [
    {
      "platform": "android-arm64-v8a",
      "kind": "apk",
      "name": "Umless-0.2.0-arm64.apk",
      "url": "https://github.com/despriber/Umless-Android/releases/download/v0.2.0/Umless-0.2.0-arm64.apk",
      "size": 67196611,
      "sha256": "e7f0…",
      "version_code": 20002
    }
  ]
}
```

- `schema`：目前为 1。客户端遇到更大的 schema 时仍按已知字段解析，未知字段忽略。
- `version`：语义化版本 `MAJOR.MINOR.PATCH`（可带 `-beta.1` 之类的后缀，但正式渠道不发布带后缀的版本）。
- `version_code`：Android 的基础版本号（不含 ABI 位，见第 5 节）；桌面端可省略。
- `notes`：更新说明，纯文本（换行分段），按界面语言选 `zh` / `en`，缺失时用另一种。
- `assets[].platform`：`windows-x64`、`android-arm64-v8a`、`android-armeabi-v7a`、`android-x86_64`。
  客户端只取与自己平台匹配的那一项；没有匹配项视为「没有适合本机的更新」。
- `assets[].kind`：`installer`（桌面安装程序，Inno Setup）、`portable`（桌面免安装 zip，仅供手动下载）、`apk`。
  桌面端自动更新只用 `installer`。
- `assets[].sha256`：小写十六进制。下载完成后必须校验，不一致则删除文件并报错。
- `assets[].version_code`：Android 该 ABI 安装包的完整 versionCode。

清单由发布脚本 `tools/release/make_update_json.py` 生成，作为 Release 的附件上传。

## 4. 版本比较

- 桌面端：按 `version` 比较（数字逐段比较；`1.2.0` > `1.2.0-beta.1`）。
- Android：按 `version_code` 比较（与本机 `PackageInfo.longVersionCode` 去掉 ABI 位后的基础号比较）。
- 只有清单版本**大于**当前版本才算有更新；用户点过「跳过此版本」的版本不再自动提示（手动检查仍显示）。

## 5. Android 版本号

`versionCode = (MAJOR × 1 000 000 + MINOR × 1 000 + PATCH) × 10 + ABI`，
ABI：armeabi-v7a=1、arm64-v8a=2、x86=3、x86_64=4。例如 0.2.0 arm64 → 20002，0.1.2 arm64 → 10022。
版本名只在 `gradle.properties` 的 `buildVersionName` 里改一处，versionCode 由它算出。
（0.1.0 / 0.1.1 测试包的 versionCode 是上游沿用的 112，新方案下任何 0.1.2 及以上版本都大于它，可直接覆盖安装。）

## 6. 校验与安装

- **桌面端**：下载 `installer` 到 `%TEMP%\umless-update\`，校验 sha256 后，常驻进程与主窗口都退出，
  以 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` 运行安装程序；安装程序结束后自动重新启动 Umless。
  从源码运行（未打包）时只检查、不安装，提示用 `git pull` 与 `uv sync` 更新。
- **Android**：下载到应用缓存目录，校验 sha256；再读取安装包的签名证书，必须与当前应用的签名一致
  （不一致说明安装包被替换或不是官方版本，拒绝安装）；然后通过系统 PackageInstaller 覆盖安装，数据保留。
  首次安装需要用户允许「安装未知应用」。

## 7. 自动检查

- 配置 `update.auto_check`，默认开启。开启时：桌面端常驻进程每 24 小时检查一次（启动 5 分钟后第一次）；
  Android 每次打开主 App 时若距上次检查超过 24 小时则检查一次（不做后台定时任务）。
- 检查只请求清单文件；失败静默（记日志），不打扰用户。
- 发现新版本时：主界面显示提示条（可「稍后」或「跳过此版本」）；桌面端托盘另弹一次通知。

## 8. 界面接口

见 `docs/ui-api.md` 的「更新」一节：`check_update(force)`、`download_update()`、`install_update()`、`skip_update(version)`；
下载源见「设置」一节的 `network` 与 `test_download_sources()`。
