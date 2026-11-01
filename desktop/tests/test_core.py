"""核心逻辑测试：配置、润色请求、失败回退、存储。不依赖麦克风与真实 LLM。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from umless import asr, config
from umless.app import Pipeline
from umless.config import LLMConfig
from umless.llm import Polisher
from umless.db import DB, Utterance


class MockLLM(BaseHTTPRequestHandler):
    """模拟 OpenAI 兼容 /chat/completions，记录最近一次请求。"""

    last_request: dict = {}
    last_headers: dict = {}
    reply = "<think>思考过程</think>\n整理后的文字"
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockLLM.last_request = body
        MockLLM.last_headers = dict(self.headers)
        payload = {"choices": [{"message": {"role": "assistant", "content": MockLLM.reply}}]}
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(MockLLM.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def llm_url():
    server = HTTPServer(("127.0.0.1", 0), MockLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("UMLESS_HOME", str(tmp_path))
    monkeypatch.delenv("UMLESS_LLM_API_KEY", raising=False)
    MockLLM.status = 200
    MockLLM.reply = "<think>思考过程</think>\n整理后的文字"


class FakeASR:
    name = "fake"

    def __init__(self, text: str):
        self.text = text

    def transcribe(self, samples, sample_rate):
        return self.text


# ---- 配置 ----


def test_first_load_copies_example_config(tmp_path):
    cfg = config.load()
    assert (tmp_path / "config.toml").exists()
    assert cfg.asr.engine == "sensevoice"
    assert cfg.hotkey.key == "caps_lock"
    assert cfg.glossary == []
    assert cfg == config.Config()  # 示例配置与 dataclass 默认值保持一致


def test_config_ignores_unknown_keys_and_reads_env_key(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        '[llm]\nmodel = "deepseek-flash"\nfoo = 1\n[asr.openai]\nmodel = "x"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("UMLESS_LLM_API_KEY", "sk-env")
    cfg = config.load()
    assert cfg.llm.model == "deepseek-flash"
    assert cfg.llm.api_key == "sk-env"
    assert cfg.asr.openai.model == "x"
    assert cfg.asr.engine == "sensevoice"  # 未配置的段落使用默认值


# ---- 润色 ----


def test_polisher_request_and_think_stripping(llm_url):
    cfg = LLMConfig(base_url=llm_url, api_key="sk-test", model="m", extra_body={"enable_thinking": False})
    out = Polisher(cfg, ["Claude Code"]).polish("嗯然后就是用 CLOUD COD 写代码")

    assert out == "整理后的文字"
    req = MockLLM.last_request
    assert req["model"] == "m"
    assert req["enable_thinking"] is False
    assert "Claude Code" in req["messages"][0]["content"]  # 术语表进入 system prompt
    assert "<transcript>" in req["messages"][1]["content"]  # 逐字稿被包裹为数据
    assert MockLLM.last_headers["Authorization"] == "Bearer sk-test"


def test_pipeline_falls_back_to_raw_text_when_llm_fails(llm_url):
    MockLLM.status = 500
    cfg = config.load()
    cfg.llm.base_url = llm_url
    res = Pipeline(cfg, engine=FakeASR("嗯原始文字")).process(np.zeros(16000, np.float32), 16000)

    assert res.output_text == "嗯原始文字"
    assert res.polished_text is None
    assert "LLM 润色失败" in res.error


def test_pipeline_skips_llm_when_disabled_or_empty(llm_url):
    cfg = config.load()
    cfg.llm.enabled = False
    res = Pipeline(cfg, engine=FakeASR("原文")).process(np.zeros(10, np.float32), 16000)
    assert res.output_text == "原文" and res.llm_ms is None

    cfg.llm.enabled = True
    cfg.llm.base_url = llm_url
    res = Pipeline(cfg, engine=FakeASR("")).process(np.zeros(10, np.float32), 16000)
    assert res.output_text == "" and res.llm_ms is None  # 没识别到内容就不调用 LLM


# ---- 识别结果后处理 ----


class FakeRecognizer:
    """模拟 sherpa-onnx OfflineRecognizer：返回固定文字。"""

    def __init__(self, text: str):
        self.text = text

    def create_stream(self):
        rec = self

        class Stream:
            result = type("R", (), {"text": rec.text})()

            def accept_waveform(self, sr, samples):
                pass

        return Stream()

    def decode_stream(self, stream):
        pass


def test_sensevoice_output_case_fixed_with_live_glossary():
    terms: list[str] = []
    eng = asr.SherpaEngine("sensevoice", FakeRecognizer(" 我在 GITHUB 上用 GPU 跑 PYTHON "), lambda: terms)
    assert eng.transcribe(np.zeros(10, np.float32), 16000) == "我在 github 上用 GPU 跑 python"
    terms.append("GitHub")  # 术语表在识别时读取，改了立即生效
    assert eng.transcribe(np.zeros(10, np.float32), 16000) == "我在 GitHub 上用 GPU 跑 python"
    assert asr.SherpaEngine("sensevoice", FakeRecognizer("HELLO")).transcribe(np.zeros(1, np.float32), 16000) == "Hello"


def test_other_engines_keep_case():
    eng = asr.SherpaEngine("qwen3_asr", FakeRecognizer("我们用 PYTHON"), lambda: ["Python"])
    assert eng.transcribe(np.zeros(10, np.float32), 16000) == "我们用 PYTHON"


def test_cloud_sensevoice_case_fixed(monkeypatch):
    cfg = config.ASRConfig(engine="openai")
    cfg.openai.base_url = "http://x"
    resp = type("Resp", (), {"raise_for_status": lambda self: None, "json": lambda self: {"text": "THIS IS A TEST"}})()
    monkeypatch.setattr(asr.httpx, "post", lambda *a, **k: resp)
    cfg.openai.model = "FunAudioLLM/SenseVoiceSmall"
    assert asr.create(cfg).transcribe(np.zeros(10, np.float32), 16000) == "This is a test"
    cfg.openai.model = "whisper-1"
    assert asr.create(cfg).transcribe(np.zeros(10, np.float32), 16000) == "THIS IS A TEST"


# ---- 场景与强度 ----


def test_scene_and_intensity_resolution():
    cfg = config.load()
    assert cfg.scene_of("wechat.exe") == "chat"  # 进程名不区分大小写
    assert cfg.scene_of("Unknown.exe") == "default"
    assert cfg.intensity_of("chat") == "moderate"  # 空串跟随全局
    assert cfg.intensity_of("document") == "restructure"
    cfg.scenes.enabled = False
    assert cfg.scene_of("WeChat.exe") == "default"
    assert cfg.intensity_of("document") == "moderate"


def test_polish_prompt_carries_scene_and_intensity(llm_url):
    cfg = LLMConfig(base_url=llm_url, model="m")
    Polisher(cfg, []).polish("原文", scene="code", intensity="faithful")
    system = MockLLM.last_request["messages"][0]["content"]
    assert "整理强度：忠实" in system and "代码标识符" in system


def test_save_patch_keeps_comments_and_replaces_lists(tmp_path):
    config.load()
    cfg = config.save_patch({
        "polish": {"intensity": "restructure"},
        "privacy": {"exclude_apps": ["A.exe"]},
        "scenes": {"intensity": {"chat": "faithful"}, "rules": [{"app": "X.exe", "scene": "email"}]},
    })
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "# 润色强度（全局默认）" in text  # 注释保留
    assert cfg.polish.intensity == "restructure"
    assert cfg.privacy.exclude_apps == ["A.exe"]
    assert cfg.scenes.intensity["chat"] == "faithful" and cfg.scenes.intensity["document"] == "restructure"
    assert cfg.scene_of("x.exe") == "email"
    assert cfg.is_excluded("a.EXE")


# ---- 存储 ----


def test_db_roundtrip_and_migration(tmp_path):
    db = DB(tmp_path / "t.db")
    uid = db.add_utterance(
        Utterance(
            duration_s=3.21,
            app="WeChat.exe",
            asr_engine="sensevoice",
            raw_text="嗯就是测试",
            asr_ms=120,
            output_text="测试",
            llm_model="m",
            polished_text="测试",
            llm_ms=800,
            scene="chat",
            intensity="moderate",
        )
    )
    row = db.recent(1)[0]
    assert row["id"] == uid
    assert row["raw_text"] == "嗯就是测试"
    assert row["scene"] == "chat"
    db.close()
    assert DB(tmp_path / "t.db").query("PRAGMA user_version")[0][0] == 2  # 重复打开不重复迁移
