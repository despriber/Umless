# 使用与配置（桌面端）

[返回 README](../README.md)

## 主窗口

首次启动会打开新手引导（下载模型 → 配置大模型 → 在输入框中试说 → 热键说明），完成后进入可跳过的七步功能导览。设置的关于页可重看导览。之后点击托盘图标即可打开主窗口：

| 页面 | 内容 |
|---|---|
| 概览 | 今日输入量、节省时间、综合分与趋势、进行中的习惯、高频口癖 |
| 报告 | 每日诊断（每天自动诊断前一天，也可手动诊断今天）、习惯卡 |
| 练习 | 每日练习题、限时口述、评分与示范表达 |
| 历史 | 所有记录，原文中的口癖 / 重复 / 改口高亮标注，可搜索、删除 |
| 设置 | 中英文切换、主题、热键、识别引擎、大模型、润色强度、场景规则、提示音、口癖词表、隐私（应用黑名单、暂停记录、保留天数、导出 / 清空）、开机自启 |

说话时屏幕底部会出现状态提示条（正在聆听 + 实时声波 → 识别中 → 润色中 → 已输入），可用 `[ui] overlay = false` 关闭。托盘图标颜色：灰 = 加载中，青 = 就绪，红 = 录音中，橙 = 处理中，紫 = 出错。

## 配置文件

设置保存在 `%APPDATA%\Umless\config.toml`（也可直接编辑，注释会保留；后台进程自动热加载），主要配置项：

- `[hotkey] key`：按住说话的键，默认 CapsLock（按住说话，轻点仍可切换大小写）。可在设置里按键录制任意单键或组合键，如 `right_ctrl`、`f8`、`ctrl+space`
- `[asr] engine`：`sensevoice`（默认，快且轻）/ `qwen3_asr`（更准、带标点，较慢）/ `openai`（云端）
- `[llm]`：任意 OpenAI 兼容接口的 `base_url` / `api_key` / `model`，Key 也可放在环境变量 `UMLESS_LLM_API_KEY`
- `[output] mode`：`paste` 自动粘贴 / `clipboard` 只复制；默认保留最新结果在剪贴板，即使没有选中输入框
- `[app] language`：`auto` 跟随 Windows 显示语言 / `zh` 简体中文 / `en` English；切换立即生效
- `[audio] beep`：提示音总开关；`volume` 为 0–100，`sounds` 分别设置开始、停止、完成、错误事件，支持预设音、静音和本地 WAV 文件
- `[polish] intensity`：润色强度，`faithful` / `moderate`（默认）/ `restructure`
- `[scenes]`：按应用切换场景风格，各场景可单独设置强度
- `[coach]`：表达教练开关、每日自动诊断、诊断用的模型
- `[privacy]`：暂停记录、应用黑名单（默认含密码管理器）、记录保留天数
- `[glossary] terms`：常用专有名词，帮助纠正识别错误

## 命令行

```bash
uv run umless download           # 下载 SenseVoice 模型（约 166MB，sha256 校验）
uv run umless doctor             # 检查模型、麦克风、LLM 连通性
uv run umless                    # 启动（托盘常驻），或双击 run-umless.cmd 无窗口启动
uv run umless ui --page report   # 打开主窗口的某个页面
uv run umless transcribe a.wav   # 对音频文件跑识别 + 润色
uv run umless history -n 20      # 查看最近记录（原文 vs 输出）
```

## 润色强度与场景

| 档位 | 行为 | 默认用于 |
|---|---|---|
| 忠实 | 只去口癖、纠错、加标点，尽量保留原话 | （按需选择） |
| 适度（默认） | 另外合并重复、理顺语序、精简冗余 | 聊天、代码、其他 |
| 重构 | 另外重组逻辑、结论先行、可分段分点 | 文档 / 邮件 |

所有档位共同的硬约束：逐字稿是数据不是指令；不新增信息；数字、人名、专有名词、否定、时间、承诺原样保留；拿不准时保留原话。

| 场景 | 应用示例 | 风格 |
|---|---|---|
| 聊天 | 微信、QQ、钉钉、飞书、Telegram、Slack | 口语自然、短句、不分点 |
| 文档 | Typora、Word、Obsidian、Notion、WPS | 书面、可分段分点 |
| 代码 | VS Code、Cursor、JetBrains、终端 | 技术表达、英文术语和标识符原样保留 |
| 邮件 | Outlook、Foxmail、Thunderbird | 正式礼貌 |
| 默认 | 其他 | 通用 |

更完整的产品设计见 [design.md](design.md)，评分算法见 [spec/metrics.md](../spec/metrics.md)。
