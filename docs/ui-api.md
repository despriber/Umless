# 主窗口 ↔ Python 接口约定

主窗口用 pywebview 加载 `desktop/umless/ui/web/index.html`，通过 `window.pywebview.api.<方法>(...)` 调用 Python。
每个方法返回 Promise；Python 端抛异常时 Promise reject（`err.message` 为当前界面语言的错误信息），前端用 toast 展示。
后端返回给界面的所有文字（错误、任务 `message`）都跟随 `app.language`；LLM 生成的点评文字也跟随界面语言，但引用的原话和改写示例保持用户说话的语言。

- 真实实现：`desktop/umless/webapi.py` 的 `Api` 类
- 模拟实现：`desktop/umless/ui/mock_api.py` 的 `MockApi` 类（方法签名与返回结构必须与本文一致）；`MockApi(platform="android")` 模拟 Android 端
- Android 主 App 在 WebView 里复用同一套界面，桥接方式与平台差异见第 7 节「Android 桥接」
- 时间统一为本地时间 ISO 字符串 `2026-10-12T10:57:44`，日期为 `2026-10-12`
- 分数：`overall/fluency/concision/logic/clarity` 统一 0–100（LLM 的 1–5 分已换算）；缺失时为 `null`

## 0. 枚举

| 名称 | 取值 |
|---|---|
| 场景 scene | `chat` 聊天 · `document` 文档 · `code` 代码 · `email` 邮件 · `default` 默认 |
| 润色强度 intensity | `faithful` 忠实 · `moderate` 适度 · `restructure` 重构 |
| 热键 hotkey | 单个键 `caps_lock` · `right_ctrl` · `f8` · `pause` …，或组合键 `ctrl+space` · `alt+q` · `ctrl+shift+f1`（修饰键 ctrl / alt / shift / win 在前，主键在后；未命名的键写作 `vk_0xNN`）。单独的字母、数字、空格等打字键必须配合修饰键。保存时规范化为小写、修饰键按 ctrl → alt → shift → win 排序 |
| ASR 引擎 | `sensevoice` · `qwen3_asr` · `openai` |
| 习惯类型 kind | `word`（某口癖词密度）· `repeat` · `correction` · `logic` · `clarity` |
| 习惯状态 | `active` · `achieved` · `archived` |
| 原文标注类型 | `keep` 保留 · `filler` 口癖 · `repeat` 重复 · `correction` 改口 · `cut` 其他被删改的内容（历史与练习结果的 `highlights` 都可能出现全部五种） |
| 任务状态 | `running` · `done` · `error` |

## 1. 通用

### `get_app_info()`
```json
{"version": "0.3.0", "onboarded": true, "tour_done": false, "theme": "system",
 "language": "zh", "language_setting": "auto", "hotkey": "caps_lock",
 "data_dir": "C:\\Users\\x\\AppData\\Roaming\\Umless", "core_running": true, "platform": "windows"}
```
- `platform`：`"windows"`（桌面，`webapi.py` 恒为此值）或 `"android"`。前端据此切换平台专属界面与文案（`js/platform.js` 的 `isAndroid()`；拿到之前退回 `window.__umlessPlatform`）。
- `core_running`：托盘常驻进程是否在运行（未运行时界面顶部提示"Umless 未在后台运行"并提供启动按钮）。
- `language`：解析后的界面语言 `zh` / `en`；`language_setting` 为配置原值 `auto` / `zh` / `en`（`auto` 跟随 Windows 显示语言，中文系统为 `zh`，其余为 `en`）。
- `tour_done`：是否看过功能导览（引导结束后自动开始导览；看完或跳过时 `save_settings({"app": {"tour_done": true}})`）。

### `core_status()`
```json
{"running": true, "ready": true, "hotkey": "caps_lock", "mode": "paste"}
```
`running` 由进程互斥量判断；`ready` 还要求识别模型和热键已就绪，且每 2 秒写入的心跳在 10 秒内更新。心跳过期只代表未就绪，不改变 `running`。引导页"试一试"用它提示状态。

### `get_last_output()` → 对象或 `null`
```json
{"text": "我明天想请假", "raw": "嗯我明天想请假",
 "highlights": [["嗯", "filler"], ["我明天想请假", "keep"]],
 "filler_count": 1, "at": "2026-10-12T10:57:44"}
```
`text` 是实际输出（润色关闭或失败时为原文）；`raw` 是识别原文。`highlights` 标注原文，随当前口癖词表更新；`filler_count` 为命中的口癖次数。
每次允许保存的输出覆盖上一条；暂停记录或黑名单应用的输出不保存。Umless 自己窗口内的试说会更新此结果，但不写入历史。引导页用它展示原文标注及润色结果，也能在未聚焦输入框或仅复制模式下获取结果。预览后端另提供 `mock_dictate(text=null, raw=null)` 模拟输出；不传参数使用当前界面语言的示例。

### `list_sounds()`
```json
{"events": ["start", "stop", "done", "error"],
 "presets": ["rise", "fall", "pop", "drop", "chime", "tick", "soft", "classic", "low"]}
```
`events`：`start` 开始录音 · `stop` 结束录音 · `done` 文字已输出 · `error` 出错或没听清。预设名称由前端翻译。

### `preview_sound(sound, volume)` → `null`
试听。`sound` 为预设 id、已存在的 `.wav` 文件绝对路径，或空串（静音）；`volume` 为 0–100。试听不受 `audio.beep` 总开关影响；自定义文件播放最多 3 秒。

### `pick_sound_file()` → `"C:\\…\\ding.wav" | null`
弹出系统文件选择框（只允许 `.wav`），取消时返回 `null`。

### `capture_hotkey()` → `"ctrl+space" | null`
阻塞等待用户按下新热键或组合键（最长 15 秒），返回规范写法；Esc、超时或被取消时返回 `null`；按下的是不能单独使用的键时抛出错误。录制期间所有按键被拦截，常驻进程的热键不会触发。只录制，不保存——前端拿到结果后调用 `save_settings({"hotkey": {"key": ...}})`。

### `cancel_hotkey_capture()` → `null`
取消正在进行的 `capture_hotkey`（它会返回 `null`）。

### `start_core()` → `null`
启动托盘常驻进程。

### `get_nav()` → `"report" | null`
托盘菜单请求跳转的页面（前端每秒轮询一次，拿到后切换路由）。可带子路由，如托盘的新版本通知打开 `"settings/about"`。

### `open_path(kind)` → `null`
`kind`: `data_dir` / `config` / `log`。

### `copy_text(text)` → `null`

### `job_status(job_id)`
所有耗时操作（LLM 调用、模型下载、录音评测）都返回 `{"job_id": "..."}`，前端每 400ms 轮询：
```json
{"state": "running", "progress": 0.42, "message": "正在下载模型…", "result": null, "error": null}
```
`progress` 为 0–1 或 `null`（不确定进度）；`state=done` 时 `result` 为该任务的结果；`state=error` 时 `error` 为中文信息。

## 2. 概览

### `get_overview()`
```json
{
  "today": {"count": 23, "chars": 1840, "minutes_spoken": 7.5, "minutes_saved": 38.2,
            "fluency": 78, "concision": 85},
  "score": {"overall": 76, "fluency": 78, "concision": 85, "logic": 70, "clarity": 75,
            "delta": 4, "date": "2026-10-11"},
  "trend": [{"date": "2026-09-13", "overall": 68, "fluency": 70, "concision": 80,
             "logic": 60, "clarity": 65, "count": 18}],
  "habits": [<Habit>],
  "latest_diagnosis": {"date": "2026-10-11", "summary": "整体表达清楚，但「就是」…"},
  "top_fillers": [{"word": "就是", "count": 42, "per100": 2.3}],
  "recent": [<HistoryItem>]
}
```
- `today.minutes_saved`：按打字 40 字/分钟估算，相比打字节省的分钟数
- `score`：最近一次诊断日的分数；`delta` 相比上一次诊断的变化；从未诊断过时 `logic/clarity/overall` 可能为 `null`
- `trend`：最近 30 天，每天一项（没有数据的日期不出现），另含 `diagnosed`（布尔）；没有诊断的日期 `overall/logic/clarity` 为 `null`（综合分只在诊断日计算，折线只连诊断日）
- `latest_diagnosis`：从未诊断过时为 `null`
- `top_fillers`：最近 7 天口癖 Top 5，`per100` 为每百字次数
- `recent`：最近 5 条

## 3. 报告

### `list_reports()`
```json
{"today": {"date": "2026-10-12", "n": 23, "diagnosed": false},
 "items": [{"date": "2026-10-11", "overall": 76, "n": 31}]}
```
`items` 为已诊断的日期，倒序。

### `get_report(date)`
```json
{
  "date": "2026-10-11", "n": 31, "diagnosed": true, "created_at": "2026-10-12T08:01:12", "model": "deepseek-v4-flash",
  "scores": {"overall": 76, "fluency": 78, "concision": 85, "logic": 70, "clarity": 75},
  "summary": "一段话总结",
  "strengths": ["能先说结论再展开", "…"],
  "issues": [{"title": "「就是」作为填充词过多", "type": "filler",
              "evidence": ["我现在有个问题就是就是…"], "advice": "停顿时用沉默代替…"}],
  "examples": [{"original": "原话", "better": "更好的说法", "why": "为什么更好"}],
  "personal_fillers": [{"word": "对吧", "added": false}],
  "terms": [{"wrong": "CLOUD COD", "right": "Claude Code", "added": false}],
  "filler_stats": [{"word": "就是", "count": 12, "per100": 2.1}],
  "rewrite_ratio": 0.31
}
```
- `issues[].type`：`filler` / `repeat` / `correction` / `structure` / `redundancy` / `wording`
- 未诊断的日期：`diagnosed=false`，只有 `date/n/scores(fluency, concision; 其余 null)/filler_stats/rewrite_ratio`，其余字段为空数组或空串
- 没有任何数据：返回 `null`

### `run_diagnosis(date)` → `{"job_id": "..."}`
任务结果为 `get_report(date)` 的结构。样本不足（少于 3 条可评分记录）时任务以 error 结束。

### `get_trend(days)` → 与 `overview.trend` 相同结构，`days` 为 7 / 30 / 90。

### `get_habits(status)` → `[<Habit>]`
`status` 为 `"active"` / `"achieved"` / `"archived"` / `null`（全部）。
```json
{"id": 3, "key": "word:就是", "title": "减少「就是」", "kind": "word", "word": "就是",
 "advice": "想停顿时直接停顿，不用「就是」填补", "evidence": ["…"],
 "baseline": 3.1, "target": 1.5, "current": 2.2, "unit": "次/百字", "lower_is_better": true,
 "progress": 0.56, "status": "active", "created_at": "…", "achieved_at": null}
```
`progress`：0–1，从基线到目标完成了多少。`logic/clarity` 类型 `unit` 为 `"分"`，`lower_is_better=false`。

### `archive_habit(id)` / `restore_habit(id)` → `null`

### `add_term(right)` → `null`
把正确写法加入术语表。

### `add_filler(word)` → `null`
加入个人口癖词表。

## 4. 练习

### `get_practice_topics()` → `{"date": "2026-10-12", "topics": [<Topic>]} | null`
`null` 表示今天还没生成，需要调用 `generate_topics`。
```json
{"id": "t1", "title": "用 1 分钟介绍你正在做的 Umless 项目", "category": "项目介绍",
 "prompt": "假设你在组会上向导师介绍…", "hint": "背景 → 做了什么 → 下一步",
 "duration": 60, "personal": true}
```

### `generate_topics(refresh)` → `{"job_id"}`；结果为 `get_practice_topics()` 的结构。

### `practice_start(topic)` → `null`
`topic` 为 Topic 对象（自定义题目时为 `{title, duration}`，`duration` 为 60 或 90，缺省 60）。开始录音。

### `mic_level()` → `{"recording": true, "elapsed": 12.4, "level": 0.37}`
`level` 为 0–1 的平滑音量，前端约 12 次/秒轮询，用于声波和计时。练习与麦克风测试共用。

### `practice_stop()` → `{"job_id"}`
停止录音并识别、评测；结果为 `<PracticeResult>`。

### `practice_cancel()` → `null`

### `list_practice(limit)` → `[{"id", "created_at", "topic", "category", "duration_s", "overall"}]`

### `get_practice(id)` → `<PracticeResult>`
```json
{"id": 7, "created_at": "…", "topic": "…", "category": "项目介绍", "duration_s": 58.2,
 "raw_text": "逐字稿", "overall": 72,
 "scores": {"structure": 70, "logic": 75, "fluency": 68, "concision": 80, "wording": 70},
 "strengths": ["…"], "improvements": ["…"],
 "outline": ["背景：…", "做了什么：…", "下一步：…"],
 "better_version": "示范表达（保留你的内容，换成更好的表达）",
 "highlights": [["嗯", "filler"], ["我们做了一个", "keep"]],
 "filler_count": 6, "cpm": 210}
```

## 5. 历史

### `list_history(query, offset, limit)`
```json
{"total": 128, "items": [<HistoryItem>]}
```
```json
{"id": 16, "created_at": "…", "duration_s": 20.4, "app": "Typora.exe", "scene": "document",
 "intensity": "moderate", "output_text": "润色后", "raw_text": "原文",
 "highlights": [["嗯", "filler"], ["下午三点开会", "keep"]],
 "filler_count": 5, "repeat_count": 2, "correction_count": 0, "fluency": 64, "rewrite_ratio": 0.35,
 "asr_ms": 1081, "llm_ms": 3392}
```
`query` 为空串时返回全部；按时间倒序。

### `delete_history(id)` → `null`

## 6. 设置

### `get_settings()`
```json
{
  "config": {
    "app": {"onboarded": true, "tour_done": true, "theme": "system", "language": "auto"},
    "hotkey": {"key": "caps_lock", "min_hold_ms": 300},
    "audio": {"beep": true, "volume": 60,
              "sounds": {"start": "rise", "stop": "fall", "done": "", "error": "low"},
              "mic_always_on": false, "keep_audio": false},
    "asr": {"engine": "sensevoice", "num_threads": 4,
            "openai": {"base_url": "…", "api_key": "…", "model": "…"}},
    "llm": {"enabled": true, "base_url": "…", "api_key": "…", "model": "…", "temperature": 0.2, "timeout_s": 20},
    "polish": {"intensity": "moderate"},
    "scenes": {"enabled": true,
               "intensity": {"chat": "", "document": "restructure", "code": "", "email": "restructure", "default": ""},
               "rules": [{"app": "WeChat.exe", "scene": "chat"}]},
    "output": {"mode": "paste", "restore_clipboard": false},
    "ui": {"overlay": true},
    "coach": {"enabled": true, "auto_daily": true, "min_utterances": 5, "model": ""},
    "privacy": {"record_history": true, "exclude_apps": ["KeePass.exe"], "retention_days": 0},
    "glossary": {"terms": ["Claude Code"]},
    "update": {"auto_check": true, "skipped_version": "", "manifest_url": ""},
    "network": {"download_source": "auto", "mirror_url": "https://gh-proxy.com/"}
  },
  "models": {"sensevoice": {"downloaded": true, "size_mb": 166, "name": "SenseVoice-Small"},
             "qwen3_asr": {"downloaded": false, "size_mb": 879, "name": "Qwen3-ASR-0.6B"}},
  "providers": [{"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "models": ["deepseek-v4-flash"]}],
  "autostart": false
}
```
- `audio.beep` 为提示音总开关；`audio.sounds` 每个事件的值为预设 id、`.wav` 绝对路径，或空串（该事件不播放）。
- `output.restore_clipboard`：粘贴后是否把剪贴板恢复成原内容。默认 `false`，即无论有没有点中输入框，识别结果都留在剪贴板里，可以随时 Ctrl+V。界面上显示为反向的开关"结果保留在剪贴板"。
- `scenes.intensity` 中的空串表示"跟随全局"；`retention_days=0` 表示永久保留；`coach.model` 为空表示与润色相同。
- `update`：见第 8 节「更新」。界面只显示 `auto_check`（关于页「自动检查更新」开关）；`skipped_version`（由 `skip_update` 写入）与 `manifest_url`（覆盖清单地址，测试 / 镜像用）不在界面上显示。两端都有这一节。
- `network`：下载源，识别模型下载与应用更新共用，只影响 `https://github.com/` 上的地址（规则见 `spec/update.md` 第 2 节）。两端都有这一节。
  - `download_source`：`auto`（默认，先试上次成功的源，没有记录时先走镜像，失败换另一个）· `github`（只直连）· `mirror`（只走镜像）。
  - `mirror_url`：镜像前缀，默认 `https://gh-proxy.com/`；实际请求为 `mirror_url` + GitHub 原地址。必须是带主机名的 `http(s)://` 地址，`save_settings` 去掉首尾空白并补结尾 `/` 后保存（返回值里是规范化后的值），否则报错。

### `save_settings(patch)` → 新的 `get_settings()` 结果
`patch` 为部分配置（与 `config` 同结构，深度合并；列表整体替换）。常驻进程会自动热加载。

### `test_llm(llm)` → `{"ok": true, "latency_ms": 820, "input": "…", "output": "…", "error": null}`
`llm` 为 `{base_url, api_key, model}`，用示例句子实际跑一次润色。

### `list_llm_models(base_url, api_key)` → `["deepseek-v4-flash", …]`

### `download_model(name)` → `{"job_id"}`；进度在 `progress`，结果 `{"name": "sensevoice"}`。

### `test_download_sources()`
```json
{"github": {"ok": true, "ms": 412, "error": null},
 "mirror": {"ok": false, "ms": null, "error": "HTTP 502"},
 "preferred": "github"}
```
同时探测 GitHub 直连与镜像（按已保存的 `network.mirror_url`）：各请求 SenseVoice 模型安装包的前 1 KiB（`Range: bytes=0-1023`，跟随跳转，200 / 206 且读到正文才算通），每个源超时约 8 秒。
- `ms`：耗时毫秒，失败时 `null`；`error`：原始失败原因（不翻译，如 `HTTP 403`、`timed out`），成功时 `null`。网络错误不抛异常。
- `preferred`：`auto` 模式接下来先试的源——两个都通取更快的，只有一个通取它，都不通时为原来记录的源。有源通时同时写入记录，所以测试会影响 `auto` 的顺序。
- 界面：「设置 → 通用 → 下载源」的「测试连接」，每个源一行（✓ 延迟 / ✗ 原因），`preferred` 标「自动优先」；固定了一种方式却连不上、另一种能连上时提示换一下。测试前先保存未写入的修改。

### `set_autostart(enabled)` → `null`

### `get_lexicon()` → `[{"word": "嗯", "source": "builtin", "enabled": true}]`
`source`：`builtin` 内置 · `llm` 诊断发现（习惯卡要练的词）· `user` 用户添加。内置词只能停用，`remove_filler` 对内置词等同于停用。
### `remove_filler(word)` / `toggle_filler(word, enabled)` → `null`

### `mic_test()` → `{"job_id"}`
录 4 秒并识别，录音期间 `mic_level().recording=true`；任务 `progress` 录音阶段为 0–0.8，识别阶段 ≥ 0.85（前端按 progress 区分阶段，不解析 `message` 文本）。结果 `{"text": "…", "peak": 0.6}`。Android 另有 `model_ready`：识别模型还没下载时为 `false`，此时只测了音量、`text` 为空。

### `export_data()` → `{"path": "C:\\Users\\x\\Downloads\\umless-export-20261012.json"}`
### `clear_data()` → `null`

### `finish_onboarding()` → `null`

## 7. Android 桥接

Android 端（fcitx5-android 的分支）的主 App（概览 / 报告 / 练习 / 历史 / 设置 / 新手引导）用 WebView 加载**同一套** `desktop/umless/ui/web/`，两端界面一致。宽度 < 720px 时界面自动切换为手机布局（底部标签栏、设置页「列表 → 详情」），与平台无关；Android 平板等宽屏仍是侧栏布局。

### 7.1 加载方式

- 用 `WebViewAssetLoader` 以 `https://appassets.androidplatform.net/assets/web/index.html` 之类的地址加载（ES 模块不能从 `file://` 加载）；不要用 `loadDataWithBaseURL`。
- 在页面脚本执行前注入 `desktop/umless/ui/web/js/android-shim.js` 的内容（`WebViewCompat.addDocumentStartJavaScript(webView, shim, setOf("*"))`）。桌面端的 `index.html` 不加载这个文件，两端共用同一份垫片。
- 系统返回键：`webView.canGoBack()` 时调用 `webView.goBack()`（设置详情、报告日期等都是 hash 路由，返回即回到上一页）。
- 沉浸式 / 刘海屏：页面已设置 `viewport-fit=cover` 并用 `env(safe-area-inset-*)` 避让，WebView 可以铺满到系统栏下面；软键盘弹出用 `adjustResize`（输入框聚焦时底部标签栏自动收起）。
- 主题：界面跟随 `app.theme`（`system` 时跟随 `prefers-color-scheme`，WebView 应关闭算法深色 `setAlgorithmicDarkeningAllowed(false)`，由页面自己适配深色）。

### 7.2 协议

垫片定义 `window.pywebview = { api: { <方法>: (...args) => Promise } }`、设置 `window.__umlessPlatform = "android"`，然后派发 `pywebviewready` 事件，因此 `js/api.js` 与桌面完全相同。`api` 是 Proxy：任意方法名都会转发给原生端。

| 方向 | 调用 | 说明 |
|---|---|---|
| JS → 原生 | `UmlessBridge.call(id, method, argsJson)` | `addJavascriptInterface(bridge, "UmlessBridge")` 暴露；`id` 为递增整数，`method` 为本文的方法名，`argsJson` 为参数数组的 JSON（如 `"[\"2026-10-12\"]"`）。原生端应立即返回，在后台线程执行 |
| 原生 → JS | `window.__umlessResolve(id, ok, resultJson)` | 在主线程 `evaluateJavascript` 调用。`ok=true` 时 `resultJson` 为返回值的 JSON（无返回值传 `"null"`）；`ok=false` 时为 `{"message": "当前界面语言的错误信息"}`，前端以 toast 显示 |

- 返回结构与桌面完全相同（本文第 1–6 节）；耗时任务同样返回 `{"job_id"}`，由前端轮询 `job_status`。
- 原生端没有的方法以 `ok=false` 回调，`message` 写明方法名。
- 只有本 App 自己的页面可以调用桥接：`WebViewClient` 拦截所有非 `appassets.androidplatform.net` 的导航，外部链接交给浏览器打开。

### 7.3 平台差异

`get_app_info()` 在 Android 上：`platform="android"`，`hotkey=""`，`core_running` 恒为 `true`（键盘由系统管理，界面改用 `get_ime_status()`），`data_dir` 为应用私有目录。

**Android 不需要实现**（界面不会调用）：`capture_hotkey`、`cancel_hotkey_capture`、`start_core`、`core_status`、`open_path`、`set_autostart`。`get_settings().autostart` 恒为 `false`。

**行为不同的方法**：
- `pick_sound_file()`：打开系统文件选择器（只允许 WAV），把文件复制到应用私有目录后返回该路径；取消时返回 `null`。
- `export_data()`：保存到「下载」目录（MediaStore），返回显示给用户的路径，如 `/storage/emulated/0/Download/umless-export-20261012.json`。
- `get_nav()`：键盘里的「设置」等入口请求打开的页面（如 `"settings/keyboard"`），前端每秒轮询。
- `list_history()` 等返回的 `app`、`scenes.rules[].app`、`privacy.exclude_apps` 都是**包名**（如 `com.tencent.mm`）。

**配置差异**（`get_settings().config` / `save_settings`）：
- 没有 `hotkey`、`output`、`ui` 三节；`audio` 没有 `mic_always_on`、`keep_audio`（Android 不保存录音）。
- `asr.engine` 只有 `sensevoice` / `openai`；`models` 只有 `sensevoice`。
- 新增 `voice`：
  ```json
  {"space_long_press": true, "toolbar_button": true, "haptic": true}
  ```
  `space_long_press` 长按空格说话；`toolbar_button` 工具栏麦克风按钮（打开语音面板）；`haptic` 开始 / 结束录音时振动。默认都为 `true`。
- `privacy` 新增 `skip_incognito`（默认 `true`）：输入框带 `IME_FLAG_NO_PERSONALIZED_LEARNING`（如浏览器无痕标签页）时照常输入，但不记录。密码框始终不触发语音、不记录，不提供开关。

### 7.4 Android 专属方法

桌面端 `webapi.py` 没有这些方法。

#### `get_ime_status()` → `{"enabled": true, "selected": false}`
`enabled`：Umless 是否已在系统设置里启用（`InputMethodManager.getEnabledInputMethodList()`）；`selected`：是否为当前默认输入法（`Settings.Secure.DEFAULT_INPUT_METHOD`）。界面在设置「键盘」分组每 1.5 秒、引导「启用键盘」步骤每秒、以及 App 回到前台时查询。

#### `open_ime_settings()` → `null`
打开系统输入法设置（`Settings.ACTION_INPUT_METHOD_SETTINGS`）。

#### `show_ime_picker()` → `null`
弹出系统输入法选择框（`InputMethodManager.showInputMethodPicker()`）。

#### `request_mic_permission()` → `{"granted": true}`
已授权时直接返回 `true`；否则弹出 `RECORD_AUDIO` 权限对话框，等用户选择后返回。用户勾选过「不再询问」时跳转到应用权限设置，返回 `false`。

#### `export_logs()` → `{"path": "/storage/emulated/0/Download/umless-logs-20261012.zip"}`
把最近的运行日志（不含输入内容）打包保存到「下载」目录。

预览后端另提供 `mock_ime(enabled, selected)` 设置模拟的键盘状态（不属于接口约定）。

### 7.5 界面差异（前端已实现）

- 文案：`t(key)` 在 Android 上优先取 `key + ".android"`（见 `js/locales/android-*.js`），如「按住热键说话」→「长按空格键说话（或点工具栏麦克风）」。
- 隐藏：热键与按住时长、常开麦克风、聆听提示条、输出方式 / 保留剪贴板、开机自启、打开数据目录 / 配置 / 日志、Qwen3-ASR、侧栏「后台运行中」（换成键盘状态）、保留录音。
- 新增：设置「键盘」分组（状态、`voice.*` 开关、上滑取消说明）；隐私里的「无痕模式不记录」和密码框说明；关于里的「导出诊断日志」；键盘未启用时顶部横幅。
- 新手引导：欢迎 → 启用键盘 → 麦克风权限与测试 → 识别模型 → 润色 → 试一试（用 Umless 键盘在试写区说一句，对比 `get_last_output()` 的原文与输出）→ 完成。

## 8. 更新

两端（桌面、Android）接口相同，机制见 `spec/update.md`。界面：「设置 → 关于」的「更新」分组，以及发现新版本时的顶部提示条（`js/update.js` 保存两处共用的状态）。

### `check_update(force)`
```json
{"current": "0.3.0", "latest": "0.4.0", "available": true, "skipped": false,
 "notes": "新增：……\n修复：……", "published_at": "2026-11-05T10:00:00Z", "size": 58720256,
 "checked_at": "2026-11-01T09:12:30", "error": null, "not_published": false,
 "can_install": true, "install_hint": null, "downloaded": false}
```
- `force=false`：返回缓存的检查结果，不等网络（开启了自动检查且距上次检查已超过间隔时，后端自己在后台刷新；`update.auto_check` 关闭时完全不联网）。从未检查过时 `latest`、`notes`、`published_at`、`size`、`checked_at` 等都可能为 `null` / 空，界面按「还没有检查过」显示。界面在启动、回到前台、打开关于页时调用。
- `force=true`：立即联网读取清单（「检查更新」按钮），返回新结果。
- **网络错误不抛异常**：`error` 为当前界面语言的错误信息，此时 `available=false`，`checked_at` 仍是上次成功检查的时间（可能为 `null`）。
- `latest`：清单里的版本（即使没有适合本机的安装包）；从未成功检查过时为 `null`。已是最新时 `latest` 等于 `current`。
- `available`：清单版本大于当前版本**且**有适合本机的安装包，只有它表示可以更新。`skipped`：`latest` 等于 `update.skipped_version`，与 `available` 相互独立（提示条只在 `available && !skipped` 时显示；关于页仍显示新版本并标注「已跳过」）。
- `notes`：更新说明纯文本（`\n` 分段），已按界面语言选好；界面按纯文本显示（转义，保留换行）。
- `published_at`：清单的发布时间（UTC ISO 字符串，可带 `Z`）；`size`：安装包字节数；没有更新时两者可为 `null`。
- `can_install=false` 时界面不提供下载，显示 `install_hint`（如桌面从源码运行：「从源码运行：用 git pull 和 uv sync 更新」）。
- `downloaded`：`latest` 的安装包已下载并校验，可以直接安装。
- `not_published`：所有尝试过的下载源对清单都返回 HTTP 404（仓库还没有发布正式版）。此时 `error=null`、`latest=null`、`available=false`、`checked_at` 为这次检查的时间；界面显示「暂时没有可用的更新（还没有发布版本）」而不是错误。有任何一个源是别的错误时按检查失败处理（`error` 非空，`not_published=false`）。

### `download_update()` → `{"job_id"}`
任务键 `update:download`：正在下载时再次调用返回同一个任务。`progress` 0–1，`message` 如「正在下载 Umless-0.4.0-setup.exe… 12.3 / 56.0 MB」「正在校验文件…」；结果 `{"downloaded": true}`。sha256（Android 另有签名证书）校验不一致时删除文件，任务以 error 结束（界面显示「重新下载」）。没有可下载的更新或 `can_install=false` 时抛出错误。

### `install_update()` → `{"status": "installing" | "needs_permission" | "unsupported"}`
- `installing`：桌面端随后退出常驻进程与主窗口，静默运行安装程序，安装结束后自动重新启动 Umless——界面显示全屏提示「正在安装更新，Umless 会自动重新启动」；Android 已把安装包交给系统 PackageInstaller 并立即返回（随后弹出系统确认窗口），界面提示在系统窗口中点「安装」。
- `needs_permission`（Android）：本 App 还没有「安装未知应用」权限，已打开对应的系统设置页；界面提示用户允许后回来再点「安装」。安装包保留（`downloaded=true`），用户回到 App（回到前台，或某些系统在切换权限时杀掉进程后重新打开）时关于页直接显示「安装」按钮，不需要重新下载。
- `unsupported`：当前环境不能自动安装（如桌面从源码运行），可另带 `install_hint`；界面显示它（没有时用 `check_update` 的 `install_hint`）。
- 还没下载、安装包已不存在或校验不一致时抛出错误；界面显示错误并提供「重新下载」。

### `skip_update(version)` → `null`
把 `version` 写入 `update.skipped_version`：之后自动检查不再为这个版本显示提示条，手动检查仍会显示。传空串或 `null` 清除。

### 界面行为
- 提示条：`check_update(false)` 返回 `available && !skipped` 时显示「Umless 0.4.0 可用」+「查看」（→ `#settings/about`）、「稍后」（本次运行内不再显示）、「跳过此版本」（`skip_update`）。在引导页与关于页不显示。
- 关于页：状态行（检查中 / 已是最新 + 上次检查时间 / 检查失败 + 重试 / 还没有发布版本 + 上次检查时间 / 发现新版本 + 发布日期与大小），有新版本时显示更新说明与「下载更新」→ 进度条 →「安装」（失败时「重新下载」）以及「跳过此版本」；下载在切换页面后继续。Android 返回 `needs_permission` 后回到前台时，提示「已允许后再点一次安装」，安装包不必重新下载。下面是「自动检查更新」开关（`update.auto_check`）及说明「只访问 GitHub 上的更新清单，不上传任何数据」。

预览后端另提供 `mock_update(state, fail_download=False)`：`state` 为 `available`（默认，桌面 0.3.0 → 0.4.0，Android 0.1.1 → 0.2.0）/ `latest` / `error` / `not_published`（还没有发布版本）/ `unchecked`（从未检查过）/ `source`（桌面从源码运行）；`fail_download=True` 时下载到一半失败。另有 `mock_network(fail=None, source_last="mirror")` 设置 `test_download_sources` 的结果：`fail` 为失败的源 `github` / `mirror` / `both`，`source_last` 为原来记录的源。也可用 `MockApi(update=...)` 指定初始状态（不属于接口约定）。
