"""表达教练测试：规则指标、诊断（模拟 LLM）、习惯卡、练习评测、主窗口接口。"""

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from umless import coach, config, metrics, practice
from umless.db import DB, Utterance
from umless.llm import parse_json

DIAGNOSIS = {
    "summary": "整体清楚，「就是」偏多。",
    "scores": {"logic": 4, "clarity": 3.5},
    "strengths": ["先说结论"],
    "issues": [{"title": "「就是」过多", "type": "filler", "evidence": ["就是就是"], "advice": "停顿代替"},
               {"title": "无效类型", "type": "???", "evidence": "不是列表", "advice": ""}],
    "examples": [{"original": "就是那个我想", "better": "我想", "why": "更直接"}],
    "personal_fillers": ["我觉得吧", "就是"],
    "terms": [{"wrong": "CLOUD COD", "right": "Claude Code"}, {"wrong": "get hub", "right": "GitHub X"}],
    "habits": [{"kind": "word", "word": "就是", "title": "减少「就是」", "advice": "停顿", "evidence": []},
               {"kind": "bogus", "word": None, "title": "x", "advice": "", "evidence": []}],
}


class MockLLM(BaseHTTPRequestHandler):
    reply = ""
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockLLM.requests.append(body)
        data = json.dumps({"choices": [{"message": {"content": MockLLM.reply}}]}, ensure_ascii=False).encode()
        self.send_response(200)
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


@pytest.fixture
def env(tmp_path, monkeypatch, llm_url):
    monkeypatch.setenv("UMLESS_HOME", str(tmp_path))
    monkeypatch.delenv("UMLESS_LLM_API_KEY", raising=False)
    cfg = config.load()
    cfg = config.save_patch({"llm": {"base_url": llm_url, "model": "m"}, "glossary": {"terms": ["Claude Code"]}})
    db = DB(tmp_path / "umless.db")
    MockLLM.requests = []
    yield cfg, db
    db.close()


def _add(db: DB, raw: str, polished: str, when: datetime | None = None) -> int:
    uid = db.add_utterance(Utterance(duration_s=5, app="Typora.exe", asr_engine="fake", raw_text=raw,
                                     asr_ms=100, output_text=polished, polished_text=polished))
    if when:
        db.execute("UPDATE utterances SET created_at = ? WHERE id = ?", (when.isoformat(timespec="seconds"), uid))
    coach.compute_metrics(db, uid, raw, polished, 5)
    return uid


SAMPLES = [
    ("我现在就是就是想测试一下这个功能有没有问题", "我现在想测试一下这个功能有没有问题。"),
    ("嗯然后我们明天就是要把那个报告交上去", "我们明天要把那个报告交上去。"),
    ("这个项目就是一个语音输入工具它可以帮你去掉口癖", "这个项目是一个语音输入工具，它可以帮你去掉口癖。"),
    ("我觉得我觉得这个设计还是挺好的就是有点慢", "我觉得这个设计挺好的，就是有点慢。"),
]


# ---- 规则指标 ----


def test_metrics_only_count_fillers_removed_by_polish():
    m = metrics.analyze("那个文件就是就是在这个目录下", "那个文件在这个目录下", 4, metrics.BUILTIN_FILLERS)
    assert m["fillers"] == {"就是": 2}  # 被保留的「那个」「这个」不算口癖
    assert m["chars"] == 14 and m["cpm"] == 210
    assert ["就是就是", "filler"] in m["highlights"]


def test_metrics_repeat_and_correction():
    m = metrics.analyze("我们我们明天不对后天开会", "我们后天开会", None, [])
    assert m["repeat_count"] == 1
    assert m["correction_count"] == 1


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('好的，结果如下：{"a": 2} 希望有帮助') == {"a": 2}


# ---- 诊断与习惯卡 ----


def test_diagnosis_flow(env):
    cfg, db = env
    coach.ensure_lexicon(db)
    for raw, polished in SAMPLES:
        _add(db, raw, polished)
    MockLLM.reply = json.dumps(DIAGNOSIS, ensure_ascii=False)
    day = coach.today()

    report = coach.diagnose(db, cfg, day)

    assert report["diagnosed"] and report["n"] == 4
    assert report["scores"]["logic"] == 80 and report["scores"]["clarity"] == 70
    assert report["scores"]["overall"] is not None
    assert [i["type"] for i in report["issues"]] == ["filler", "wording"]  # 未知类型归为 wording
    assert report["issues"][1]["evidence"] == []
    # 已在词表中的「就是」被过滤；术语表已有的 Claude Code 被过滤
    assert [f["word"] for f in report["personal_fillers"]] == ["我觉得吧"]
    assert [t["right"] for t in report["terms"]] == ["GitHub X"]
    assert report["filler_stats"][0]["word"] == "就是"
    # 诊断请求带上了 JSON 输出要求与逐字稿
    req = MockLLM.requests[-1]
    assert req["response_format"] == {"type": "json_object"}
    assert "就是就是想测试" in req["messages"][1]["content"]

    habits = coach.get_habits(db, "active")
    assert len(habits) == 1 and habits[0]["key"] == "word:就是"
    assert habits[0]["baseline"] > 0 and habits[0]["target"] == round(habits[0]["baseline"] / 2, 2)

    reports = coach.list_reports(db)
    assert reports["today"]["diagnosed"] and reports["items"][0]["date"] == day


def test_diagnosis_requires_enough_samples(env):
    cfg, db = env
    _add(db, *SAMPLES[0])
    with pytest.raises(coach.CoachError):
        coach.diagnose(db, cfg, coach.today())


def test_habit_achieved_when_target_reached(env):
    cfg, db = env
    coach.ensure_lexicon(db)
    old = datetime.now() - timedelta(days=10)
    for _ in range(3):
        _add(db, *SAMPLES[0], when=old)
    db.execute(
        "INSERT INTO habits (key, title, kind, word, advice, evidence, baseline, target, current, status, "
        "created_at, updated_at) VALUES ('word:就是', 't', 'word', '就是', '', '[]', 5, 2.5, 5, 'active', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    for _ in range(10):  # 最近 7 天说得很干净
        _add(db, "我们明天要把报告交上去然后再讨论下一步", "我们明天要把报告交上去然后再讨论下一步")
    coach.refresh_habits(db)
    h = coach.get_habits(db)[0]
    assert h["status"] == "achieved" and h["progress"] == 1.0 and h["current"] == 0


def test_auto_diagnosis_due_for_yesterday(env):
    cfg, db = env
    yesterday = datetime.now() - timedelta(days=1)
    for raw, polished in SAMPLES + SAMPLES:
        _add(db, raw, polished, when=yesterday)
    assert coach.due_auto_diagnosis(db, cfg) == yesterday.date().isoformat()
    MockLLM.reply = json.dumps(DIAGNOSIS, ensure_ascii=False)
    coach.run_auto_diagnosis(db, cfg)
    assert coach.due_auto_diagnosis(db, cfg) is None


def test_lexicon_change_triggers_recompute(env):
    cfg, db = env
    coach.ensure_lexicon(db)
    uid = _add(db, "我觉得吧这个方案可以", "这个方案可以")
    coach.backfill_metrics(db)
    db.add_words(["我觉得吧"], "user")
    assert coach.backfill_metrics(db) == 1
    row = db.query("SELECT fillers FROM metrics WHERE utterance_id = ?", (uid,))[0]
    assert json.loads(row["fillers"]) == {"我觉得吧": 1}


# ---- 练习 ----


class FakeASR:
    name = "fake"

    def transcribe(self, samples, sr):
        return "嗯我们这个项目就是做一个语音输入工具然后它可以帮你改掉口癖"


def test_practice_evaluate(env):
    cfg, db = env
    MockLLM.reply = json.dumps({
        "clean_text": "我们这个项目做一个语音输入工具，它可以帮你改掉口癖。",
        "scores": {"structure": 3, "logic": 4, "wording": 3.5},
        "strengths": ["主题明确"], "improvements": ["补充下一步"], "outline": ["背景：…"],
        "better_version": "我们在做一个语音输入工具。",
    }, ensure_ascii=False)
    topic = {"title": "介绍你的项目", "duration": 60}
    result = practice.evaluate(db, cfg, FakeASR(), topic, np.zeros(16000 * 10, np.float32), 16000)
    assert result["scores"]["structure"] == 60 and result["scores"]["logic"] == 80
    assert result["filler_count"] >= 3  # 嗯、就是、然后
    assert result["overall"] > 0 and result["cpm"] > 0
    assert practice.list_practice(db)[0]["id"] == result["id"]


def test_practice_topics_fall_back_without_llm(env, monkeypatch):
    cfg, db = env
    MockLLM.reply = "不是 JSON"
    data = practice.generate_topics(db, cfg)
    assert len(data["topics"]) == 5 and data["topics"][0]["id"] == "t1"
    assert practice.get_topics(db)["date"] == coach.today()


# ---- 主窗口接口 ----


def test_api_contract_shapes(env):
    from umless.webapi import Api

    cfg, db = env
    for raw, polished in SAMPLES:
        _add(db, raw, polished)
    api = Api()
    ov = api.get_overview()
    assert set(ov) == {"today", "score", "trend", "habits", "latest_diagnosis", "top_fillers", "recent"}
    assert ov["today"]["count"] == 4 and ov["score"]["overall"] is None
    hist = api.list_history("报告", 0, 10)
    assert hist["total"] == 1 and hist["items"][0]["highlights"]
    s = api.get_settings()
    assert s["config"]["scenes"]["intensity"]["document"] == "restructure"
    assert "glossary" in s["config"] and "glossary_cfg" not in s["config"]
    with pytest.raises(ValueError):
        api.save_settings({"polish": {"intensity": "wild"}})
    s = api.save_settings({"polish": {"intensity": "faithful"}})
    assert s["config"]["polish"]["intensity"] == "faithful"
    api.add_term("Umless")
    assert "Umless" in config.load().glossary
    # 公开属性只有接口方法（pywebview 会把公开属性暴露给前端）
    assert not [k for k, v in vars(api).items() if not k.startswith("_")]
    api._close()
