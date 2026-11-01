"""口语练习：每日出题（个人化 + 通用）、限时口述录音、识别与评测。

练习在主窗口进程里录音和识别（与常驻进程互不干扰），结果存入 practice 表。
"""

import json
import math
import random
import threading
import time

import numpy as np

from . import coach, metrics
from .audio import Recorder
from .config import Config
from .db import DB, now_iso
from .i18n import resolve, t
from .prompts import build_practice_messages, build_topics_messages

# LLM 维度（1–5 分 × 20）+ 规则维度（0–100）
PRACTICE_WEIGHTS = {"structure": 0.25, "logic": 0.25, "wording": 0.15, "fluency": 0.20, "concision": 0.15}

FALLBACK_TOPICS = [
    {"title": "介绍你最近在做的一件事", "category": "项目介绍",
     "prompt": "向一位不了解情况的同事介绍你最近在做的事。", "hint": "背景 → 做了什么 → 下一步"},
    {"title": "用 1 分钟做自我介绍", "category": "面试",
     "prompt": "假设你在面试开场，面试官请你做自我介绍。", "hint": "我是谁 → 做过什么 → 为什么适合"},
    {"title": "向外行解释一个你熟悉的概念", "category": "概念讲解",
     "prompt": "向一位完全不懂的朋友解释一个你领域里的概念。", "hint": "一句话定义 → 打个比方 → 举个例子"},
    {"title": "说说你对远程办公的看法", "category": "观点表达",
     "prompt": "在讨论会上表达你的观点并给出理由。", "hint": "观点 → 两个理由 → 总结"},
    {"title": "汇报本周的工作进展", "category": "工作汇报",
     "prompt": "在周会上向负责人汇报本周进展。", "hint": "结论 → 完成了什么 → 风险与需要的支持"},
]

FALLBACK_TOPICS_EN = [
    {"title": "Describe something you've been working on", "category": "Project intro",
     "prompt": "Explain what you've been working on lately to a colleague who has no context.",
     "hint": "Background → What you did → Next steps"},
    {"title": "Introduce yourself in one minute", "category": "Interview",
     "prompt": "You're at the start of an interview and the interviewer asks you to introduce yourself.",
     "hint": "Who you are → What you've done → Why you fit"},
    {"title": "Explain a concept you know well to a layperson", "category": "Explain",
     "prompt": "Explain a concept from your field to a friend with no background in it.",
     "hint": "One-line definition → Analogy → Example"},
    {"title": "Share your view on remote work", "category": "Opinion",
     "prompt": "State your position in a discussion and back it up with reasons.",
     "hint": "Position → Two reasons → Wrap-up"},
    {"title": "Report this week's progress", "category": "Status update",
     "prompt": "Update your manager on this week's progress at the weekly meeting.",
     "hint": "Bottom line → What's done → Risks and support needed"},
]


# ================================================================ 麦克风


def level_of(rms: float) -> float:
    """RMS → 0–1 音量（-60dB ~ -10dB 线性映射）。"""
    if rms <= 1e-6:
        return 0.0
    db = 20 * math.log10(rms)
    return min(1.0, max(0.0, (db + 60) / 50))


class MicSession:
    """一次录音（练习或麦克风测试），提供实时音量与计时。"""

    def __init__(self) -> None:
        self._rec: Recorder | None = None
        self._started = 0.0
        self._level = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._rec and self._rec.recording:
                self._rec.stop()
            if self._rec is None:
                self._rec = Recorder(always_on=False)
            self._rec.start()
            self._started = time.perf_counter()
            self._level = 0.0

    @property
    def recording(self) -> bool:
        return bool(self._rec and self._rec.recording)

    def level(self) -> dict:
        if not self.recording:
            return {"recording": False, "elapsed": 0.0, "level": 0.0}
        recent = list(self._rec.levels)[-3:]
        target = level_of(max(recent)) if recent else 0.0
        # 上升快、下降慢，声波更自然
        self._level = target if target > self._level else self._level * 0.75 + target * 0.25
        return {"recording": True, "elapsed": round(time.perf_counter() - self._started, 1),
                "level": round(self._level, 3)}

    def stop(self) -> tuple[np.ndarray, int]:
        with self._lock:
            if not self._rec:
                return np.zeros(0, dtype=np.float32), 16000
            samples = self._rec.stop()
            return samples, self._rec.sample_rate

    def shutdown(self) -> None:
        if self._rec:
            self._rec.shutdown()
            self._rec = None


# ================================================================ 出题


def get_topics(db: DB, lang: str | None = None) -> dict | None:
    """今天的题目；传入 lang 时，界面语言变了的旧题目视为没有（需要重新出题）。"""
    data = db.get_kv("practice_topics")
    if not data or data.get("date") != coach.today():
        return None
    if lang and data.get("lang", "zh") != lang:
        return None
    return {"date": data["date"], "topics": data["topics"]}


def _recent_texts(db: DB, limit: int = 25) -> list[str]:
    rows = db.query(
        "SELECT output_text FROM utterances WHERE created_at >= ? ORDER BY id DESC LIMIT 200",
        (coach._shift(coach.today(), -14) + "T00:00:00",),
    )
    texts = [r["output_text"].strip() for r in rows if len(r["output_text"].strip()) >= 15]
    texts.sort(key=len, reverse=True)
    return [t[:120] for t in texts[:limit]]


def _weak_points(db: DB) -> list[str]:
    points = [h["title"] for h in coach.get_habits(db, "active")]
    rows = db.query("SELECT data FROM diagnoses ORDER BY id DESC LIMIT 1")
    if rows:
        points += [i["title"] for i in json.loads(rows[0]["data"]).get("issues", [])[:3]]
    return points


def _clean_topic(tp: dict, i: int, lang: str = "zh") -> dict | None:
    if not isinstance(tp, dict) or not tp.get("title"):
        return None
    duration = tp.get("duration")
    k = 3 if lang == "en" else 1  # 英文按字母计，长度上限放宽
    return {
        "id": f"t{i}",
        "title": str(tp["title"])[:40 * k],
        "category": str(tp.get("category") or t("practice.category_general"))[:6 * k],
        "prompt": str(tp.get("prompt") or "")[:200 * k],
        "hint": str(tp.get("hint") or "")[:60 * k],
        "duration": 90 if duration == 90 else 60,
        "personal": bool(tp.get("personal")),
    }


def generate_topics(db: DB, cfg: Config, progress=None) -> dict:
    progress = progress or (lambda p, m: None)
    progress(None, t("practice.making_topics"))
    lang = resolve(cfg.app.language)
    try:
        client = coach.coach_client(cfg)
        messages = build_topics_messages(_recent_texts(db), _weak_points(db), lang)
        raw = client.chat_json(messages, temperature=0.8)
        topics = [c for i, tp in enumerate(raw.get("topics") or [], 1) if (c := _clean_topic(tp, i, lang))][:5]
    except Exception:
        topics = []
    if not topics:  # 没有配置 LLM 或调用失败时使用内置题目
        pool = FALLBACK_TOPICS_EN if lang == "en" else FALLBACK_TOPICS
        picks = random.sample(pool, k=len(pool))
        topics = [_clean_topic({**tp, "duration": 60}, i, lang) for i, tp in enumerate(picks, 1)]
    data = {"date": coach.today(), "topics": topics}
    db.set_kv("practice_topics", {**data, "lang": lang})  # 记下出题语言，切换界面语言后重新出题
    return data


# ================================================================ 评测


def practice_scores(llm_scores: dict, fluency: float, concision: float) -> tuple[dict, int]:
    """五个维度的分数（整数）与综合分：LLM 1–5 分 × 20，规则分取整，缺失维度按剩余权重归一。"""
    scores = {}
    for k in ("structure", "logic", "wording"):
        v = coach._score_1to5(llm_scores.get(k))
        scores[k] = round(v * 20) if v else None
    scores["fluency"] = round(fluency)
    scores["concision"] = round(concision)
    avail = {k: v for k, v in scores.items() if v is not None}
    overall = round(sum(PRACTICE_WEIGHTS[k] * v for k, v in avail.items()) / sum(PRACTICE_WEIGHTS[k] for k in avail))
    return scores, overall


def evaluate(db: DB, cfg: Config, engine, topic: dict, samples: np.ndarray, sr: int, progress=None) -> dict:
    progress = progress or (lambda p, m: None)
    duration = samples.size / sr if sr else 0
    if duration < 3:
        raise RuntimeError(t("practice.too_short"))
    progress(0.1, t("practice.recognizing"))
    raw = engine.transcribe(samples, sr)
    if not raw.strip():
        raise RuntimeError(t("practice.no_speech"))

    progress(0.35, t("practice.evaluating"))
    feedback: dict = {}
    error = None
    try:
        client = coach.coach_client(cfg)
        feedback = client.chat_json(build_practice_messages(topic, raw, duration, resolve(cfg.app.language)))
    except Exception as e:
        error = str(e)
    clean = str(feedback.get("clean_text") or "") or None
    m = metrics.analyze(raw, clean, duration, coach.lexicon_words(db))

    llm_scores = feedback.get("scores") if isinstance(feedback.get("scores"), dict) else {}
    scores, overall = practice_scores(llm_scores, m["fluency"], m["concision"])

    improvements = coach._str_list(feedback.get("improvements"), 3, 300)
    if error:
        improvements = [t("practice.llm_failed", error=error)]
    data = {
        "scores": scores,
        "strengths": coach._str_list(feedback.get("strengths"), 3, 300),
        "improvements": improvements,
        "outline": coach._str_list(feedback.get("outline"), 5, 200),
        "better_version": str(feedback.get("better_version") or ""),
        "highlights": m["highlights"],
        "filler_count": m["filler_count"],
        "cpm": round(m["cpm"]) if m["cpm"] else None,
        "topic_detail": topic,
    }
    pid = db.execute(
        "INSERT INTO practice (created_at, topic, category, duration_s, raw_text, overall, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), topic.get("title") or t("practice.free_topic"), topic.get("category") or t("practice.category_custom"),
         round(duration, 1), raw, overall, json.dumps(data, ensure_ascii=False)),
    )
    return get_practice(db, pid)


def get_practice(db: DB, pid: int) -> dict | None:
    rows = db.query("SELECT * FROM practice WHERE id = ?", (pid,))
    if not rows:
        return None
    r = rows[0]
    data = json.loads(r["data"])
    data.pop("topic_detail", None)
    return {"id": r["id"], "created_at": r["created_at"], "topic": r["topic"], "category": r["category"],
            "duration_s": r["duration_s"], "raw_text": r["raw_text"], "overall": coach._r(r["overall"]), **data}


def list_practice(db: DB, limit: int = 20) -> list[dict]:
    rows = db.query(
        "SELECT id, created_at, topic, category, duration_s, overall FROM practice ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [{**dict(r), "overall": coach._r(r["overall"])} for r in rows]
