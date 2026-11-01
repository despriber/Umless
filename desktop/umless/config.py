"""配置加载与保存：%APPDATA%\\Umless\\config.toml，首次运行从 config.example.toml 复制。

读取用 tomllib；设置界面修改时用 tomlkit 深度合并写回，保留文件里的注释。
常驻进程按文件修改时间热加载。
"""

import copy
import os
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomlkit



def _package_dir() -> Path:
    """包内资源（示例配置、界面文件）所在目录。

    源码运行时为本文件所在目录；PyInstaller 打包后资源解压 / 复制在 sys._MEIPASS/umless 下。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        return Path(bundle) / "umless"
    return Path(__file__).resolve().parent


PACKAGE_DIR = _package_dir()
EXAMPLE = PACKAGE_DIR / "config.example.toml"

SCENES = ("chat", "document", "code", "email", "default")
INTENSITIES = ("faithful", "moderate", "restructure")
LANGUAGES = ("auto", "zh", "en")
SOUND_EVENTS = ("start", "stop", "done", "error")  # 开始录音 / 结束录音 / 文字已输出 / 出错或没听清

DEFAULT_SCENE_RULES = [
    *({"app": a, "scene": "chat"} for a in (
        "WeChat.exe", "Weixin.exe", "WXWork.exe", "QQ.exe", "DingTalk.exe", "Feishu.exe",
        "Lark.exe", "Telegram.exe", "Slack.exe", "Discord.exe", "WhatsApp.exe",
    )),
    *({"app": a, "scene": "document"} for a in (
        "Typora.exe", "WINWORD.EXE", "wps.exe", "Obsidian.exe", "Notion.exe", "ONENOTE.EXE",
        "Joplin.exe", "Logseq.exe",
    )),
    *({"app": a, "scene": "code"} for a in (
        "Code.exe", "Cursor.exe", "Windsurf.exe", "idea64.exe", "pycharm64.exe", "devenv.exe",
        "WindowsTerminal.exe", "wezterm-gui.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    )),
    *({"app": a, "scene": "email"} for a in (
        "OUTLOOK.EXE", "olk.exe", "Foxmail.exe", "thunderbird.exe",
    )),
]


def data_dir() -> Path:
    """用户数据目录，可用环境变量 UMLESS_HOME 覆盖（便于测试）。"""
    override = os.environ.get("UMLESS_HOME")
    base = Path(override) if override else Path(os.environ.get("APPDATA", Path.home())) / "Umless"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.toml"


@dataclass
class AppConfig:
    onboarded: bool = False
    tour_done: bool = False  # 是否看过功能导览
    theme: str = "system"  # system / light / dark
    language: str = "auto"  # auto（跟随 Windows 显示语言）/ zh / en


@dataclass
class HotkeyConfig:
    key: str = "caps_lock"
    min_hold_ms: int = 300


@dataclass
class AudioConfig:
    beep: bool = True  # 提示音总开关
    volume: int = 60  # 提示音音量 0–100
    # 各事件的提示音：预设 id、.wav 绝对路径，或空串（不播放）
    sounds: dict = field(default_factory=lambda: {"start": "rise", "stop": "fall", "done": "", "error": "low"})
    mic_always_on: bool = False
    keep_audio: bool = False


@dataclass
class OpenAIASRConfig:
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = "FunAudioLLM/SenseVoiceSmall"


@dataclass
class ASRConfig:
    engine: str = "sensevoice"
    model_dir: str = ""
    num_threads: int = 4
    openai: OpenAIASRConfig = field(default_factory=OpenAIASRConfig)

    @property
    def models_path(self) -> Path:
        return Path(self.model_dir) if self.model_dir else data_dir() / "models"


@dataclass
class LLMConfig:
    enabled: bool = True
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    model: str = "qwen3.5:2b"
    temperature: float = 0.2
    timeout_s: float = 20
    extra_body: dict = field(default_factory=dict)


@dataclass
class PolishConfig:
    intensity: str = "moderate"


@dataclass
class ScenesConfig:
    enabled: bool = True
    # 空串表示跟随全局强度
    intensity: dict = field(default_factory=lambda: {
        "chat": "", "document": "restructure", "code": "", "email": "restructure", "default": "",
    })
    rules: list = field(default_factory=lambda: copy.deepcopy(DEFAULT_SCENE_RULES))


@dataclass
class UIConfig:
    overlay: bool = True


@dataclass
class OutputConfig:
    mode: str = "paste"
    # 粘贴后是否恢复原剪贴板。默认不恢复：没点中输入框时，结果仍留在剪贴板里可以手动粘贴
    restore_clipboard: bool = False


@dataclass
class CoachConfig:
    enabled: bool = True
    auto_daily: bool = True
    min_utterances: int = 5
    model: str = ""  # 空表示与润色相同


@dataclass
class PrivacyConfig:
    record_history: bool = True
    # 常见密码管理器与 Windows 凭据输入框
    exclude_apps: list = field(default_factory=lambda: [
        "KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe",
        "Enpass.exe", "KeeWeb.exe", "NordPass.exe", "CredentialUIBroker.exe",
    ])
    retention_days: int = 0  # 0 表示永久保留


@dataclass
class GlossaryConfig:
    terms: list = field(default_factory=list)


@dataclass
class UpdateConfig:
    auto_check: bool = True  # 常驻进程每 24 小时检查一次新版本（启动 5 分钟后第一次）
    skipped_version: str = ""  # 用户点过「跳过此版本」的版本，不再自动提示
    manifest_url: str = ""  # 覆盖更新清单地址（测试或自建镜像），空表示 GitHub Releases；设置界面不显示


@dataclass
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    polish: PolishConfig = field(default_factory=PolishConfig)
    scenes: ScenesConfig = field(default_factory=ScenesConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    coach: CoachConfig = field(default_factory=CoachConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    glossary_cfg: GlossaryConfig = field(default_factory=GlossaryConfig)

    @property
    def glossary(self) -> list[str]:
        return list(self.glossary_cfg.terms)

    def scene_of(self, app: str | None) -> str:
        if not self.scenes.enabled or not app:
            return "default"
        name = app.lower()
        for rule in self.scenes.rules:
            if str(rule.get("app", "")).lower() == name and rule.get("scene") in SCENES:
                return rule["scene"]
        return "default"

    def intensity_of(self, scene: str) -> str:
        if self.scenes.enabled:
            override = self.scenes.intensity.get(scene, "")
            if override in INTENSITIES:
                return override
        return self.polish.intensity if self.polish.intensity in INTENSITIES else "moderate"

    def is_excluded(self, app: str | None) -> bool:
        if not app:
            return False
        name = app.lower()
        return any(name == a.lower() for a in self.privacy.exclude_apps)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["glossary"] = d.pop("glossary_cfg")
        # 来自环境变量的 Key 不回显，避免设置界面保存时写进文件
        for env, path in (("UMLESS_LLM_API_KEY", ("llm",)), ("UMLESS_ASR_API_KEY", ("asr", "openai"))):
            sec = d
            for p in path:
                sec = sec[p]
            if sec["api_key"] and sec["api_key"] == os.environ.get(env):
                sec["api_key"] = ""
        return d


def _pick(cls, raw: dict):
    """只取 dataclass 中声明过的字段，忽略配置里的未知键。"""
    names = cls.__dataclass_fields__.keys()
    return cls(**{k: v for k, v in raw.items() if k in names})


def ensure_file() -> Path:
    path = config_path()
    if not path.exists() and EXAMPLE.exists():
        shutil.copyfile(EXAMPLE, path)
    return path


# 0.3.0 之前的示例配置显式写了 restore_clipboard = true（原样复制给了每个用户）。新默认为 false：
# 识别结果始终留在剪贴板里。只迁移这段原封未动的旧示例文字，并换成新注释，所以只会发生一次；
# 之后用户在设置里重新打开也不会再被改回。
_OLD_RESTORE = "# 自动粘贴后恢复原剪贴板内容（仅限文本）\nrestore_clipboard = true\n"
_NEW_RESTORE = (
    "# 自动粘贴后恢复原剪贴板内容（仅限文本）。默认 false：识别结果始终留在剪贴板里，\n"
    "# 没点中输入框时也可以随时 Ctrl+V\nrestore_clipboard = false\n"
)


def _migrate(path: Path, text: str) -> str:
    if _OLD_RESTORE not in text:
        return text
    text = text.replace(_OLD_RESTORE, _NEW_RESTORE)
    try:
        tmp = path.with_suffix(".toml.migrate.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # 写不进去就只在内存里生效，下次再试
    return text


def load() -> Config:
    path = ensure_file()
    text = _migrate(path, path.read_text(encoding="utf-8")) if path.exists() else ""
    raw = tomllib.loads(text)

    asr_raw = dict(raw.get("asr", {}))
    asr_openai = _pick(OpenAIASRConfig, asr_raw.pop("openai", {}))
    scenes = _pick(ScenesConfig, raw.get("scenes", {}))
    # 用户只写了部分场景强度时，其余场景补默认值
    scenes.intensity = {**ScenesConfig().intensity, **scenes.intensity}
    audio = _pick(AudioConfig, raw.get("audio", {}))
    # 同上：只写了部分事件的提示音时，其余事件补默认值；忽略未知事件
    sounds = {**AudioConfig().sounds, **(audio.sounds if isinstance(audio.sounds, dict) else {})}
    audio.sounds = {k: str(sounds[k]) for k in SOUND_EVENTS}
    try:
        audio.volume = min(100, max(0, int(audio.volume)))
    except (TypeError, ValueError, OverflowError):
        audio.volume = AudioConfig.volume
    cfg = Config(
        app=_pick(AppConfig, raw.get("app", {})),
        hotkey=_pick(HotkeyConfig, raw.get("hotkey", {})),
        audio=audio,
        asr=_pick(ASRConfig, asr_raw),
        llm=_pick(LLMConfig, raw.get("llm", {})),
        polish=_pick(PolishConfig, raw.get("polish", {})),
        scenes=scenes,
        output=_pick(OutputConfig, raw.get("output", {})),
        ui=_pick(UIConfig, raw.get("ui", {})),
        coach=_pick(CoachConfig, raw.get("coach", {})),
        privacy=_pick(PrivacyConfig, raw.get("privacy", {})),
        update=_pick(UpdateConfig, raw.get("update", {})),
        glossary_cfg=_pick(GlossaryConfig, raw.get("glossary", {})),
    )
    cfg.asr.openai = asr_openai
    # Key 允许放在环境变量里，避免明文写进配置文件
    cfg.llm.api_key = cfg.llm.api_key or os.environ.get("UMLESS_LLM_API_KEY", "")
    cfg.asr.openai.api_key = cfg.asr.openai.api_key or os.environ.get("UMLESS_ASR_API_KEY", "")
    return cfg


def _to_toml(value):
    """字典列表（如场景规则）写成多行的内联表数组，便于阅读。"""
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        arr = tomlkit.array()
        for v in value:
            t = tomlkit.inline_table()
            t.update(v)
            arr.append(t)
        arr.multiline(True)
        return arr
    if isinstance(value, list):
        arr = tomlkit.array()
        arr.extend(value)
        if len(value) > 4:
            arr.multiline(True)
        return arr
    return value


def _merge(doc, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and key in doc and isinstance(doc[key], dict):
            _merge(doc[key], value)
        elif isinstance(value, dict):
            table = tomlkit.table() if not isinstance(doc, tomlkit.items.InlineTable) else tomlkit.inline_table()
            _merge(table, value)
            doc[key] = table
        else:
            doc[key] = _to_toml(value)


def save_patch(patch: dict) -> Config:
    """把部分配置深度合并进 config.toml（列表整体替换），保留注释。"""
    path = ensure_file()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)
    _merge(doc, patch)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)
    return load()
