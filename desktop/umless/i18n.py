"""后端的界面文字（中 / 英）：接口错误、任务进度、托盘菜单与通知、聆听提示条。

当前语言是进程级的全局状态：常驻进程在加载 / 热加载配置时设置，主窗口进程在重新读取配置时设置。
日志（log.*）不经过这里，保持中文。两份字典的 key 和占位符必须完全一致（有测试保证）。
"""

import ctypes
import logging

log = logging.getLogger(__name__)

LANGUAGES = ("zh", "en")
SETTINGS = ("auto", "zh", "en")

_current = "zh"


def system_language() -> str:
    """Windows 显示语言：主语言 ID 为 0x04（中文，含简繁）时为 zh，其余为 en。"""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return "zh"
    return "zh" if langid & 0x3FF == 0x04 else "en"


def resolve(setting: str | None) -> str:
    """配置值（auto / zh / en）→ 实际界面语言 zh / en；未知值按 auto 处理。"""
    return setting if setting in LANGUAGES else system_language()


def set_language(setting: str | None) -> str:
    """设置当前进程的界面语言，返回解析后的 zh / en。"""
    global _current
    _current = resolve(setting)
    return _current


def current() -> str:
    return _current


def t(key: str, **kw) -> str:
    """取当前语言的文字并填入占位符；缺失时退回中文，再退回 key 本身。"""
    text = STRINGS[_current].get(key) or ZH.get(key) or key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        log.warning("界面文字占位符不匹配：%s", key)
        return text


ZH: dict[str, str] = {
    # ---- 聆听提示条 ----
    "overlay.recording": "正在聆听",
    "overlay.transcribing": "识别中",
    "overlay.polishing": "润色中",
    "overlay.pasted": "已输入",
    "overlay.copied": "已复制",
    "overlay.loading": "模型加载中，请稍候",
    "overlay.no_speech": "未识别到内容",
    "overlay.polish_failed": "润色失败，已输出原文",
    "overlay.failed": "处理失败，详见日志",
    "overlay.mic_failed": "无法打开麦克风",
    "overlay.output_failed": "输出失败，可在历史里复制",
    # ---- 托盘 ----
    "tray.title.loading": "Umless - 加载模型中…",
    "tray.title.idle": "Umless - 就绪（按住热键说话）",
    "tray.title.recording": "Umless - 录音中…",
    "tray.title.processing": "Umless - 处理中…",
    "tray.title.error": "Umless - 出错，查看日志",
    "tray.menu.open": "打开 Umless",
    "tray.menu.report": "今日报告",
    "tray.menu.practice": "口语练习",
    "tray.menu.settings": "设置",
    "tray.menu.pause": "暂停记录（照常输入，不保存）",
    "tray.menu.more": "更多",
    "tray.menu.data_dir": "打开数据目录",
    "tray.menu.log": "打开日志",
    "tray.menu.quit": "退出",
    "tray.paused": "已暂停记录：照常输入，但不保存",
    "tray.resumed": "已恢复记录",
    "tray.ready": "已就绪，按住 {hotkey} 说话",
    "tray.engine_switched": "识别引擎已切换为 {engine}",
    "tray.load_failed": "识别模型加载失败，请在设置里检查识别引擎",
    "tray.process_failed": "处理失败：{error}",
    "tray.report_ready": "昨天的表达报告已生成：综合 {score} 分，点击托盘「今日报告」查看",
    # ---- 后台任务 ----
    "job.processing": "处理中…",
    "job.not_found": "任务不存在",
    # ---- 主窗口接口 ----
    "api.unknown_path": "未知的路径类型：{kind}",
    "api.habit_not_found": "习惯卡不存在",
    "api.term_empty": "术语不能为空",
    "api.filler_empty": "口癖词不能为空",
    "api.filler_too_long": "口癖词最多 8 个字",
    "api.topic_empty": "题目不能为空",
    "api.not_recording": "当前没有在录音",
    "api.practice_not_found": "练习记录不存在",
    "api.unknown_model": "未知的模型：{name}",
    "api.loading_asr": "正在加载识别模型…",
    "api.organizing": "正在整理记录…",
    "api.making_topics": "正在出题…",
    "api.recognizing": "正在识别…",
    "api.preparing_download": "准备下载…",
    "api.mic_opening": "正在打开麦克风…",
    "api.mic_listening": "正在聆听…请随便说一句话",
    "api.url_scheme": "接口地址需要以 http:// 或 https:// 开头",
    "api.empty_output": "模型返回了空内容",
    "api.test_sentence": "嗯那个我明天就是想请个假然后去医院复查一下",
    "api.provider.ollama": "Ollama（本地）",
    "api.provider.deepseek": "DeepSeek",
    "api.provider.siliconflow": "硅基流动",
    "api.provider.dashscope": "阿里百炼",
    "api.provider.zhipu": "智谱",
    # ---- 设置校验 ----
    "cfg.bad_format": "配置格式不正确",
    "cfg.bad_hotkey": "不支持的热键：{name}",
    "cfg.hotkey_empty": "热键不能为空",
    "cfg.bad_hold_ms": "按住时长需在 {min}–{max} 毫秒之间",
    "cfg.hotkey_bad": "无法识别的热键：{name}",
    "cfg.hotkey_mod_main": "组合键需要一个非修饰键，例如 Ctrl + Space：{name}",
    "cfg.hotkey_typing": "{name} 单独使用会影响正常打字，请配合 Ctrl / Alt / Shift / Win 使用",
    "tray.bad_hotkey": "热键 {name} 无效，已改用 CapsLock。请在设置里重新设置",
    "cfg.bad_intensity": "未知的润色强度：{value}",
    "cfg.bad_scene_intensity": "场景强度设置有误：{scene}={value}",
    "cfg.bad_rule": "场景规则需要填写应用名并选择场景",
    "cfg.bad_engine": "未知的识别引擎：{engine}",
    "cfg.bad_retention": "保留天数必须是非负整数",
    "cfg.bad_language": "未知的界面语言：{value}",
    "cfg.bad_volume": "音量必须是 0–100 的整数",
    "cfg.bad_sound_event": "未知的提示音事件：{event}",
    "cfg.bad_sound": "提示音需要是预设名称或 .wav 文件的绝对路径：{value}",
    "cfg.sound_missing": "提示音文件不存在：{path}",
    # ---- 识别引擎 ----
    "asr.unknown_engine": "未知的识别引擎：{engine}",
    "asr.model_missing": "识别模型未下载，请先在设置里下载（或运行 `umless download {engine}`）：{path}",
    # ---- 大模型 ----
    "llm.timeout": "请求超时",
    "llm.connect_failed": "无法连接：{error}",
    "llm.http_error": "接口返回 {status}：{body}",
    "llm.bad_response": "无法解析返回：{body}",
    "llm.list_failed": "获取模型列表失败：{error}",
    "llm.no_json": "模型没有返回 JSON",
    "llm.bad_json": "模型返回的 JSON 格式有误",
    "llm.not_object": "模型返回的不是 JSON 对象",
    # ---- 表达教练 ----
    "coach.no_llm": "还没有配置大模型，请先在设置里填写",
    "coach.collecting": "整理当天的记录…",
    "coach.too_few": "这一天可评分的记录只有 {n} 条，至少需要 {min} 条",
    "coach.analyzing": "大模型正在分析你的表达…",
    "coach.making_habits": "生成习惯卡…",
    "habit.word": "减少「{word}」",
    "habit.repeat": "减少重复",
    "habit.correction": "减少改口",
    "habit.logic": "说话更有条理",
    "habit.clarity": "表达更清楚",
    # ---- 口语练习 ----
    "practice.making_topics": "正在根据你最近说的话出题…",
    "practice.category_general": "综合",
    "practice.category_custom": "自定义",
    "practice.free_topic": "自由练习",
    "practice.too_short": "录音太短，至少说 3 秒",
    "practice.recognizing": "正在识别…",
    "practice.no_speech": "没有识别到内容，请检查麦克风",
    "practice.evaluating": "正在评估你的表达…",
    "practice.llm_failed": "大模型评测失败（{error}），只给出了流畅度和简洁度",
    # ---- 模型下载 ----
    "models.exists": "[已存在] {path}",
    "models.downloading_from": "[下载] {url}（约 {size} MB）",
    "models.progress": "正在下载 {done} / {total} MB",
    "models.source_failed": "[失败] {error}",
    "models.checksum_failed": "[校验失败] sha256 不匹配，换下一个源",
    "models.extracting": "校验通过，正在解压…",
    "models.done": "[完成] {path}",
    "models.failed": "模型 {name} 下载失败",
    # ---- 应用更新 ----
    "tray.update_available": "Umless {version} 可用，点击查看",
    "update.check_failed": "检查更新失败：{error}",
    "update.bad_manifest": "更新清单格式不正确（{error}）",
    "update.none": "没有可安装的新版本",
    "update.downloading_from": "正在下载更新：{url}",
    "update.progress": "正在下载更新 {done} / {total} MB",
    "update.download_failed": "下载更新失败：{error}",
    "update.checksum_failed": "安装包校验失败（sha256 不一致），已删除，请重新下载",
    "update.downloaded": "更新已下载并通过校验",
    "update.not_downloaded": "请先下载更新",
    "update.launch_failed": "无法启动安装程序：{error}",
    "update.hint_source": "当前从源码运行，不能自动安装。请用 git pull 与 uv sync 更新",
    "update.hint_portable": "便携版不能自动安装，请到 {url} 下载新版本",
    "cfg.bad_manifest_url": "更新清单地址需要以 http:// 或 https:// 开头",
    "cfg.bad_version": "版本号格式不正确：{value}",
}

EN: dict[str, str] = {
    # ---- 热键名称 ----
    # ---- 聆听提示条 ----
    "overlay.recording": "Listening",
    "overlay.transcribing": "Transcribing",
    "overlay.polishing": "Polishing",
    "overlay.pasted": "Inserted",
    "overlay.copied": "Copied",
    "overlay.loading": "Loading model, please wait",
    "overlay.no_speech": "Didn't catch that",
    "overlay.polish_failed": "Polish failed, raw text inserted",
    "overlay.failed": "Failed, see log",
    "overlay.mic_failed": "Can't open the microphone",
    "overlay.output_failed": "Couldn't insert. Copy it from History",
    # ---- 托盘 ----
    "tray.title.loading": "Umless - Loading model…",
    "tray.title.idle": "Umless - Ready (hold the hotkey to talk)",
    "tray.title.recording": "Umless - Recording…",
    "tray.title.processing": "Umless - Processing…",
    "tray.title.error": "Umless - Error, see log",
    "tray.menu.open": "Open Umless",
    "tray.menu.report": "Today's report",
    "tray.menu.practice": "Speaking practice",
    "tray.menu.settings": "Settings",
    "tray.menu.pause": "Pause history (type as usual, don't save)",
    "tray.menu.more": "More",
    "tray.menu.data_dir": "Open data folder",
    "tray.menu.log": "Open log",
    "tray.menu.quit": "Quit",
    "tray.paused": "History paused: dictation still works, nothing is saved",
    "tray.resumed": "History resumed",
    "tray.ready": "Ready. Hold {hotkey} to talk",
    "tray.engine_switched": "Speech engine switched to {engine}",
    "tray.load_failed": "Failed to load the speech model. Check the speech engine in Settings",
    "tray.process_failed": "Processing failed: {error}",
    "tray.report_ready": "Yesterday's report is ready: overall {score}. Open \"Today's report\" from the tray",
    # ---- 后台任务 ----
    "job.processing": "Working…",
    "job.not_found": "Task not found",
    # ---- 主窗口接口 ----
    "api.unknown_path": "Unknown path type: {kind}",
    "api.habit_not_found": "Habit not found",
    "api.term_empty": "Term can't be empty",
    "api.filler_empty": "Filler word can't be empty",
    "api.filler_too_long": "Filler words can be at most 8 characters",
    "api.topic_empty": "Topic can't be empty",
    "api.not_recording": "Not recording",
    "api.practice_not_found": "Practice record not found",
    "api.unknown_model": "Unknown model: {name}",
    "api.loading_asr": "Loading speech model…",
    "api.organizing": "Gathering records…",
    "api.making_topics": "Writing topics…",
    "api.recognizing": "Transcribing…",
    "api.preparing_download": "Preparing download…",
    "api.mic_opening": "Opening microphone…",
    "api.mic_listening": "Listening… say anything",
    "api.url_scheme": "The API URL must start with http:// or https://",
    "api.empty_output": "The model returned an empty response",
    "api.test_sentence": "um so I was uh thinking we could like move the meeting to Friday, no wait, Thursday",
    "api.provider.ollama": "Ollama (local)",
    "api.provider.deepseek": "DeepSeek",
    "api.provider.siliconflow": "SiliconFlow",
    "api.provider.dashscope": "Alibaba Model Studio",
    "api.provider.zhipu": "Zhipu AI",
    # ---- 设置校验 ----
    "cfg.bad_format": "Invalid settings format",
    "cfg.bad_hotkey": "Unsupported hotkey: {name}",
    "cfg.hotkey_empty": "The hotkey can't be empty",
    "cfg.bad_hold_ms": "Hold time must be between {min} and {max} ms",
    "cfg.hotkey_bad": "Unrecognized hotkey: {name}",
    "cfg.hotkey_mod_main": "A shortcut needs a non-modifier key, such as Ctrl + Space: {name}",
    "cfg.hotkey_typing": "{name} on its own would get in the way of typing. Combine it with Ctrl, Alt, Shift or Win",
    "tray.bad_hotkey": "Hotkey {name} is invalid, so CapsLock is used instead. Set a new one in Settings",
    "cfg.bad_intensity": "Unknown polish level: {value}",
    "cfg.bad_scene_intensity": "Invalid scene polish level: {scene}={value}",
    "cfg.bad_rule": "Each scene rule needs an app name and a scene",
    "cfg.bad_engine": "Unknown speech engine: {engine}",
    "cfg.bad_retention": "Retention days must be a non-negative integer",
    "cfg.bad_language": "Unknown interface language: {value}",
    "cfg.bad_volume": "Volume must be an integer from 0 to 100",
    "cfg.bad_sound_event": "Unknown sound event: {event}",
    "cfg.bad_sound": "A sound must be a preset name or an absolute path to a .wav file: {value}",
    "cfg.sound_missing": "Sound file not found: {path}",
    # ---- 识别引擎 ----
    "asr.unknown_engine": "Unknown speech engine: {engine}",
    "asr.model_missing": "The speech model isn't downloaded yet. Download it in Settings (or run `umless download {engine}`): {path}",
    # ---- 大模型 ----
    "llm.timeout": "Request timed out",
    "llm.connect_failed": "Can't connect: {error}",
    "llm.http_error": "API returned {status}: {body}",
    "llm.bad_response": "Can't parse the response: {body}",
    "llm.list_failed": "Failed to list models: {error}",
    "llm.no_json": "The model didn't return JSON",
    "llm.bad_json": "The model returned malformed JSON",
    "llm.not_object": "The model didn't return a JSON object",
    # ---- 表达教练 ----
    "coach.no_llm": "No language model configured yet. Set one up in Settings",
    "coach.collecting": "Gathering the day's records…",
    "coach.too_few": "Only {n} scoreable records that day; at least {min} are needed",
    "coach.analyzing": "The model is analyzing how you speak…",
    "coach.making_habits": "Creating habit cards…",
    "habit.word": "Say \"{word}\" less",
    "habit.repeat": "Fewer repetitions",
    "habit.correction": "Fewer self-corrections",
    "habit.logic": "Speak with more structure",
    "habit.clarity": "Speak more clearly",
    # ---- 口语练习 ----
    "practice.making_topics": "Writing topics based on what you've said recently…",
    "practice.category_general": "General",
    "practice.category_custom": "Custom",
    "practice.free_topic": "Free practice",
    "practice.too_short": "Recording too short: speak for at least 3 seconds",
    "practice.recognizing": "Transcribing…",
    "practice.no_speech": "No speech detected. Check your microphone",
    "practice.evaluating": "Evaluating your answer…",
    "practice.llm_failed": "Model evaluation failed ({error}); only fluency and concision were scored",
    # ---- 模型下载 ----
    "models.exists": "[exists] {path}",
    "models.downloading_from": "[download] {url} (about {size} MB)",
    "models.progress": "Downloading {done} / {total} MB",
    "models.source_failed": "[failed] {error}",
    "models.checksum_failed": "[checksum mismatch] sha256 doesn't match, trying the next source",
    "models.extracting": "Checksum OK, extracting…",
    "models.done": "[done] {path}",
    "models.failed": "Failed to download model {name}",
    # ---- 应用更新 ----
    "tray.update_available": "Umless {version} is available. Click to view",
    "update.check_failed": "Couldn't check for updates: {error}",
    "update.bad_manifest": "Invalid update manifest ({error})",
    "update.none": "No newer version to install",
    "update.downloading_from": "Downloading update: {url}",
    "update.progress": "Downloading update {done} / {total} MB",
    "update.download_failed": "Failed to download the update: {error}",
    "update.checksum_failed": "The installer failed verification (sha256 mismatch) and was deleted. Please download again",
    "update.downloaded": "Update downloaded and verified",
    "update.not_downloaded": "Download the update first",
    "update.launch_failed": "Couldn't start the installer: {error}",
    "update.hint_source": "Running from source, so updates can't be installed automatically. Update with git pull and uv sync",
    "update.hint_portable": "The portable version can't install updates automatically. Download the new version from {url}",
    "cfg.bad_manifest_url": "The update manifest URL must start with http:// or https://",
    "cfg.bad_version": "Invalid version number: {value}",
}

STRINGS = {"zh": ZH, "en": EN}
