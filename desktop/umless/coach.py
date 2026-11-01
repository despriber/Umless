"""表达教练：逐条指标落库、每日诊断、习惯卡，以及概览 / 报告 / 趋势的数据组装。

分数统一为 0–100：流畅度、简洁度来自规则；逻辑性、清晰度来自 LLM 的 1–5 分 × 20。
综合分只在诊断过的日期计算（缺某个维度时按剩余维度重新归一）。
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import date as Date
from datetime import datetime, timedelta

from . import metrics
from .config import Config
from .db import DB, now_iso
from .i18n import resolve, t
from .llm import LLMClient
from .prompts import build_diagnosis_messages

log = logging.getLogger(__name__)

WEIGHTS = {"fluency": 0.30, "concision": 0.20, "logic": 0.25, "clarity": 0.25}
ISSUE_TYPES = {"filler", "repeat", "correction", "structure", "redundancy", "wording"}
HABIT_KINDS = {"word", "repeat", "correction", "logic", "clarity"}
MAX_ACTIVE_HABITS = 3
MIN_DIAGNOSIS_SAMPLES = 3
TYPING_CPM = 40  # 估算"节省时间"用的打字速度（字 / 分钟）
DIAGNOSIS_BUDGET = 8000  # 送给 LLM 的逐字稿总字数上限

Progress = Callable[[float | None, str], None]


class CoachError(RuntimeError):
    pass


# ================================================================ 工具


def _day_range(d: str) -> tuple[str, str]:
    nxt = (Date.fromisoformat(d) + timedelta(days=1)).isoformat()
    return f"{d}T00:00:00", f"{nxt}T00:00:00"


def _shift(d: str, days: int) -> str:
    return (Date.fromisoformat(d) + timedelta(days=days)).isoformat()


def today() -> str:
    return Date.today().isoformat()


def _r(x):
    return None if x is None else int(round(x))


def overall_score(fluency=None, concision=None, logic=None, clarity=None) -> float | None:
    vals = {"fluency": fluency, "concision": concision, "logic": logic, "clarity": clarity}
    avail = {k: v for k, v in vals.items() if v is not None}
    if not avail:
        return None
    total = sum(WEIGHTS[k] for k in avail)
    return round(sum(WEIGHTS[k] * v for k, v in avail.items()) / total, 1)


def _content_len(text: str) -> int:
    return len(re.sub(r"[\s\W_]", "", text or ""))


def _metric_dict(row) -> dict | None:
    """数据库行 → metrics.aggregate 需要的字典；没有指标时返回 None。"""
    if row["chars"] is None:
        return None
    return {
        "chars": row["chars"],
        "fillers": json.loads(row["fillers"] or "{}"),
        "repeat_count": row["repeat_count"],
        "correction_count": row["correction_count"],
        "rewrite_ratio": row["rewrite_ratio"],
        "scoreable": bool(row["scoreable"]),
    }


def stats_of(rows) -> dict:
    ms = [m for r in rows if (m := _metric_dict(r))]
    agg = metrics.aggregate(ms)
    scoreable = [m for m in ms if m["scoreable"]]
    agg["repeat_count"] = sum(m["repeat_count"] for m in scoreable)
    agg["correction_count"] = sum(m["correction_count"] for m in scoreable)
    agg["fillers_per100"] = round(agg["filler_count"] / agg["chars"] * 100, 2) if agg["chars"] else 0
    return agg


def rows_between(db: DB, start_day: str, end_day: str):
    """[start_day, end_day] 闭区间内的记录（含指标）。"""
    return db.utterances_between(_day_range(start_day)[0], _day_range(end_day)[1])


# ================================================================ 指标


def lexicon_words(db: DB) -> list[str]:
    if not db.query("SELECT 1 FROM lexicon LIMIT 1"):
        ensure_lexicon(db)  # 新库：先放入内置口癖词
    return [r["word"] for r in db.lexicon(enabled_only=True)]


def ensure_lexicon(db: DB) -> None:
    db.add_words(metrics.BUILTIN_FILLERS, "builtin")


def compute_metrics(db: DB, uid: int, raw_text: str, polished_text: str | None,
                    duration_s: float | None, lexicon: list[str] | None = None) -> dict:
    lex = lexicon if lexicon is not None else lexicon_words(db)
    m = metrics.analyze(raw_text, polished_text, duration_s, lex)
    db.save_metrics(uid, m)
    return m


def _lexicon_hash(words: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(words)).encode()).hexdigest()[:12]


def backfill_metrics(db: DB) -> int:
    """补算缺失或过期的指标；口癖词表变化时全部重算。返回处理条数。"""
    ensure_lexicon(db)
    lex = lexicon_words(db)
    h = _lexicon_hash(lex) + f"-v{metrics.METRICS_VERSION}"
    if db.get_kv("metrics_hash") != h:
        rows = db.query("SELECT * FROM utterances")
    else:
        rows = db.utterances_missing_metrics(metrics.METRICS_VERSION)
    for r in rows:
        compute_metrics(db, r["id"], r["raw_text"], r["polished_text"], r["duration_s"], lex)
    db.set_kv("metrics_hash", h)
    if rows:
        log.info("重算指标 %d 条", len(rows))
    return len(rows)


# ================================================================ 历史


def history_item(row) -> dict:
    keys = row.keys()
    get = lambda k: row[k] if k in keys else None  # noqa: E731
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "duration_s": row["duration_s"],
        "app": row["app"],
        "scene": get("scene") or "default",
        "intensity": get("intensity"),
        "output_text": row["output_text"],
        "raw_text": row["raw_text"],
        "highlights": json.loads(get("highlights")) if get("highlights") else [[row["raw_text"], "keep"]],
        "filler_count": get("filler_count") or 0,
        "repeat_count": get("repeat_count") or 0,
        "correction_count": get("correction_count") or 0,
        "fluency": _r(get("fluency")),
        "rewrite_ratio": get("rewrite_ratio"),
        "asr_ms": row["asr_ms"],
        "llm_ms": row["llm_ms"],
    }


_HISTORY_SELECT = (
    "SELECT u.*, m.chars, m.fillers, m.filler_count, m.repeat_count, m.correction_count, "
    "m.rewrite_ratio, m.cpm, m.fluency, m.concision, m.scoreable, m.highlights "
    "FROM utterances u LEFT JOIN metrics m ON m.utterance_id = u.id "
)


def list_history(db: DB, query: str = "", offset: int = 0, limit: int = 30) -> dict:
    where, params = "", ()
    if query:
        like = f"%{query}%"
        where = "WHERE u.raw_text LIKE ? OR u.output_text LIKE ? OR u.app LIKE ? "
        params = (like, like, like)
    total = db.query(f"SELECT COUNT(*) AS n FROM utterances u {where}", params)[0]["n"]
    rows = db.query(_HISTORY_SELECT + where + "ORDER BY u.id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"total": total, "items": [history_item(r) for r in rows]}


# ================================================================ 诊断


def latest_diagnosis(db: DB, d: str):
    rows = db.query("SELECT * FROM diagnoses WHERE date = ? ORDER BY id DESC LIMIT 1", (d,))
    return rows[0] if rows else None


def _sample_for_llm(rows) -> list[dict]:
    """挑选送给 LLM 的逐字稿：总字数受限时优先保留较长的句子，再按时间排序。"""
    items = [r for r in rows if r["scoreable"]]
    chosen, used = [], 0
    for r in sorted(items, key=lambda r: -r["chars"]):
        text = r["raw_text"][:300]
        if used + len(text) > DIAGNOSIS_BUDGET:
            continue
        chosen.append((r["id"], {"app": r["app"], "raw_text": text}))
        used += len(text)
    return [u for _, u in sorted(chosen)]


def _str_list(v, limit: int, maxlen: int = 200) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip()[:maxlen] for x in v if str(x).strip()][:limit]


def _score_1to5(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return min(5.0, max(1.0, x))


def _sanitize_diagnosis(raw: dict, lexicon: list[str], glossary: list[str]) -> dict:
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    issues = []
    for it in raw.get("issues") or []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        issues.append({
            "title": str(it["title"])[:60],
            "type": it.get("type") if it.get("type") in ISSUE_TYPES else "wording",
            "evidence": _str_list(it.get("evidence"), 3),
            "advice": str(it.get("advice") or "")[:300],
        })
    examples = [
        {"original": str(e["original"]), "better": str(e["better"]), "why": str(e.get("why") or "")}
        for e in raw.get("examples") or []
        if isinstance(e, dict) and e.get("original") and e.get("better")
    ][:4]
    lex = set(lexicon)
    fillers = [w for w in _str_list(raw.get("personal_fillers"), 5, 8) if w not in lex]
    gl = {g.lower() for g in glossary}
    terms = [
        {"wrong": str(t["wrong"])[:40], "right": str(t["right"])[:40]}
        for t in raw.get("terms") or []
        if isinstance(t, dict) and t.get("wrong") and t.get("right") and str(t["right"]).lower() not in gl
    ][:5]
    habits = []
    for h in raw.get("habits") or []:
        if not isinstance(h, dict) or h.get("kind") not in HABIT_KINDS:
            continue
        word = str(h.get("word") or "").strip() if h["kind"] == "word" else None
        if h["kind"] == "word" and not word:
            continue
        habits.append({
            "kind": h["kind"], "word": word,
            "title": str(h.get("title") or "")[:30],
            "advice": str(h.get("advice") or "")[:200],
            "evidence": _str_list(h.get("evidence"), 3),
        })
    return {
        "summary": str(raw.get("summary") or "")[:600],
        "logic": _score_1to5(scores.get("logic")),
        "clarity": _score_1to5(scores.get("clarity")),
        "strengths": _str_list(raw.get("strengths"), 3),
        "issues": issues[:4],
        "examples": examples,
        "personal_fillers": fillers,
        "terms": terms,
        "habits": habits[:2],
    }


def coach_client(cfg: Config) -> LLMClient:
    if not cfg.llm.base_url:
        raise CoachError(t("coach.no_llm"))
    return LLMClient(cfg.llm, model=cfg.coach.model or cfg.llm.model, timeout_s=max(cfg.llm.timeout_s, 120))


def diagnose(db: DB, cfg: Config, d: str, progress: Progress | None = None) -> dict:
    """诊断某一天，结果写库并更新习惯卡。返回 get_report(d)。"""
    progress = progress or (lambda p, m: None)
    progress(0.05, t("coach.collecting"))
    backfill_metrics(db)
    rows = rows_between(db, d, d)
    stats = stats_of(rows)
    if stats["n"] < MIN_DIAGNOSIS_SAMPLES:
        raise CoachError(t("coach.too_few", n=stats["n"], min=MIN_DIAGNOSIS_SAMPLES))

    lexicon = lexicon_words(db)
    active = [h["title"] for h in get_habits(db, "active")]
    # 点评文字跟随界面语言，引用的原话保持用户说话的语言
    lang = resolve(cfg.app.language)
    messages = build_diagnosis_messages(d, _sample_for_llm(rows), stats, lexicon, cfg.glossary, active, lang)
    client = coach_client(cfg)
    progress(None, t("coach.analyzing"))
    data = _sanitize_diagnosis(client.chat_json(messages), lexicon, cfg.glossary)

    logic = data["logic"] * 20 if data["logic"] else None
    clarity = data["clarity"] * 20 if data["clarity"] else None
    overall = overall_score(stats["fluency"], stats["concision"], logic, clarity)
    progress(0.9, t("coach.making_habits"))
    db.execute("DELETE FROM diagnoses WHERE date = ?", (d,))
    db.execute(
        "INSERT INTO diagnoses (date, created_at, model, n, logic, clarity, fluency, concision, overall, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (d, now_iso(), client.model, stats["n"], logic, clarity, stats["fluency"], stats["concision"],
         overall, json.dumps(data, ensure_ascii=False)),
    )
    _create_habits(db, data["habits"], d)
    refresh_habits(db)
    log.info("诊断 %s 完成：综合 %s，逻辑 %s，清晰 %s", d, overall, logic, clarity)
    return get_report(db, cfg, d)


def get_report(db: DB, cfg: Config, d: str) -> dict | None:
    rows = rows_between(db, d, d)
    diag = latest_diagnosis(db, d)
    if not rows and not diag:
        return None
    stats = stats_of(rows)
    report = {
        "date": d, "n": stats["n"], "diagnosed": False, "created_at": None, "model": None,
        "scores": {"overall": None, "fluency": _r(stats["fluency"]), "concision": _r(stats["concision"]),
                   "logic": None, "clarity": None},
        "summary": "", "strengths": [], "issues": [], "examples": [],
        "personal_fillers": [], "terms": [],
        "filler_stats": stats["filler_stats"],
        "rewrite_ratio": stats["rewrite_ratio"],
    }
    if diag:
        data = json.loads(diag["data"])
        lex = {r["word"] for r in db.lexicon(enabled_only=False)}
        gl = {g.lower() for g in cfg.glossary}
        report.update({
            "n": diag["n"], "diagnosed": True, "created_at": diag["created_at"], "model": diag["model"],
            "scores": {"overall": _r(diag["overall"]), "fluency": _r(diag["fluency"]),
                       "concision": _r(diag["concision"]), "logic": _r(diag["logic"]),
                       "clarity": _r(diag["clarity"])},
            "summary": data.get("summary", ""),
            "strengths": data.get("strengths", []),
            "issues": data.get("issues", []),
            "examples": data.get("examples", []),
            "personal_fillers": [{"word": w, "added": w in lex} for w in data.get("personal_fillers", [])],
            "terms": [{**t, "added": t["right"].lower() in gl} for t in data.get("terms", [])],
        })
    return report


def list_reports(db: DB) -> dict:
    t = today()
    rows = db.query(
        "SELECT date, overall, n FROM diagnoses WHERE id IN (SELECT MAX(id) FROM diagnoses GROUP BY date) "
        "ORDER BY date DESC"
    )
    today_stats = stats_of(rows_between(db, t, t))
    return {
        "today": {"date": t, "n": today_stats["n"], "diagnosed": latest_diagnosis(db, t) is not None},
        "items": [{"date": r["date"], "overall": _r(r["overall"]), "n": r["n"]} for r in rows],
    }


def get_trend(db: DB, days: int = 30) -> list[dict]:
    end = today()
    start = _shift(end, -(days - 1))
    by_day: dict[str, list] = {}
    for r in rows_between(db, start, end):
        by_day.setdefault(r["created_at"][:10], []).append(r)
    diags = {
        r["date"]: r for r in db.query(
            "SELECT * FROM diagnoses WHERE id IN (SELECT MAX(id) FROM diagnoses GROUP BY date) "
            "AND date >= ? AND date <= ?", (start, end),
        )
    }
    out = []
    for d in sorted(set(by_day) | set(diags)):
        rows = by_day.get(d, [])
        s = stats_of(rows)
        g = diags.get(d)
        out.append({
            "date": d,
            "overall": _r(g["overall"]) if g else None,
            "fluency": _r(g["fluency"] if g else s["fluency"]),
            "concision": _r(g["concision"] if g else s["concision"]),
            "logic": _r(g["logic"]) if g else None,
            "clarity": _r(g["clarity"]) if g else None,
            "count": len(rows),
            "diagnosed": g is not None,
        })
    return out


def get_overview(db: DB, cfg: Config) -> dict:
    t = today()
    today_rows = rows_between(db, t, t)
    ts = stats_of(today_rows)
    spoken = sum(r["duration_s"] for r in today_rows) / 60
    typed = sum(_content_len(r["output_text"]) for r in today_rows) / TYPING_CPM

    diag_rows = db.query(
        "SELECT * FROM diagnoses WHERE id IN (SELECT MAX(id) FROM diagnoses GROUP BY date) "
        "ORDER BY date DESC LIMIT 2"
    )
    if diag_rows:
        g = diag_rows[0]
        delta = None
        if len(diag_rows) > 1 and g["overall"] is not None and diag_rows[1]["overall"] is not None:
            delta = _r(g["overall"] - diag_rows[1]["overall"])
        score = {"overall": _r(g["overall"]), "fluency": _r(g["fluency"]), "concision": _r(g["concision"]),
                 "logic": _r(g["logic"]), "clarity": _r(g["clarity"]), "delta": delta, "date": g["date"]}
        latest = {"date": g["date"], "summary": json.loads(g["data"]).get("summary", "")}
    else:
        # 从未诊断：用最近 7 天的规则指标
        week = stats_of(rows_between(db, _shift(t, -6), t))
        score = {"overall": None, "fluency": _r(week["fluency"]), "concision": _r(week["concision"]),
                 "logic": None, "clarity": None, "delta": None, "date": t}
        latest = None

    week = stats_of(rows_between(db, _shift(t, -6), t))
    recent = db.query(_HISTORY_SELECT + "ORDER BY u.id DESC LIMIT 5")
    return {
        "today": {
            "count": len(today_rows), "chars": ts["chars"],
            "minutes_spoken": round(spoken, 1), "minutes_saved": round(max(0.0, typed - spoken), 1),
            "fluency": _r(ts["fluency"]), "concision": _r(ts["concision"]),
        },
        "score": score,
        "trend": get_trend(db, 30),
        "habits": get_habits(db, "active"),
        "latest_diagnosis": latest,
        "top_fillers": week["filler_stats"][:5],
        "recent": [history_item(r) for r in recent],
    }


# ================================================================ 习惯卡


def _habit_key(kind: str, word: str | None) -> str:
    return f"word:{word}" if kind == "word" else kind


def _lower_is_better(kind: str) -> bool:
    return kind in ("word", "repeat", "correction")


def habit_value(db: DB, kind: str, word: str | None, end_day: str) -> tuple[float | None, int]:
    """截至 end_day 的最近 7 天指标值及样本数。规则类为每百字次数，LLM 类为平均分。"""
    start = _shift(end_day, -6)
    if kind in ("logic", "clarity"):
        rows = db.query(
            f"SELECT {kind} AS v FROM diagnoses WHERE id IN (SELECT MAX(id) FROM diagnoses GROUP BY date) "
            f"AND date >= ? AND date <= ? AND {kind} IS NOT NULL", (start, end_day),
        )
        vals = [r["v"] for r in rows]
        return (round(sum(vals) / len(vals), 1) if vals else None), len(vals)
    ms = [m for r in rows_between(db, start, end_day) if (m := _metric_dict(r)) and m["scoreable"]]
    chars = sum(m["chars"] for m in ms)
    if not chars:
        return None, 0
    if kind == "word":
        count = sum(m["fillers"].get(word, 0) for m in ms)
    elif kind == "repeat":
        count = sum(m["repeat_count"] for m in ms)
    else:
        count = sum(m["correction_count"] for m in ms)
    return round(count / chars * 100, 2), len(ms)


def habit_target(kind: str, baseline: float | None) -> float | None:
    """由基线算目标；不值得练（或没有基线）时返回 None。"""
    if baseline is None:
        return None
    if _lower_is_better(kind):
        if baseline < 0.3:  # 本来就很少，没必要练
            return None
        return round(baseline / 2, 2)
    if baseline >= 90:
        return None
    return min(100.0, baseline + 20)


def habit_reached(kind: str, current: float, target: float, n: int, created_day: str, today_day: str) -> bool:
    """是否达成：达到目标、样本足够（规则类 ≥10 条，LLM 类 ≥2 次诊断）、创建满 3 天。"""
    lower = _lower_is_better(kind)
    reached = current <= target if lower else current >= target
    enough = n >= (10 if lower else 2)
    old_enough = created_day <= _shift(today_day, -3)
    return reached and enough and old_enough


def _create_habits(db: DB, suggestions: list[dict], d: str) -> None:
    active = db.query("SELECT COUNT(*) AS n FROM habits WHERE status = 'active'")[0]["n"]
    for s in suggestions:
        if active >= MAX_ACTIVE_HABITS:
            return
        kind, word = s["kind"], s["word"]
        key = _habit_key(kind, word)
        if db.query("SELECT 1 FROM habits WHERE key = ?", (key,)):
            continue
        if kind == "word" and word not in lexicon_words(db):
            db.add_words([word], "llm")  # 要练的口头禅必须能被统计到
            backfill_metrics(db)
        baseline, _ = habit_value(db, kind, word, d)
        target = habit_target(kind, baseline)
        if target is None:
            continue
        title = s["title"] or (t("habit.word", word=word) if kind == "word" else t(f"habit.{kind}"))
        now = now_iso()
        db.execute(
            "INSERT INTO habits (key, title, kind, word, advice, evidence, baseline, target, current, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (key, title, kind, word, s["advice"], json.dumps(s["evidence"], ensure_ascii=False),
             baseline, target, baseline, now, now),
        )
        active += 1


def refresh_habits(db: DB) -> None:
    """更新进行中习惯的当前值，达标则标记为已达成。"""
    t = today()
    for h in db.query("SELECT * FROM habits WHERE status = 'active'"):
        current, n = habit_value(db, h["kind"], h["word"], t)
        if current is None:
            continue
        status, achieved_at = "active", None
        if habit_reached(h["kind"], current, h["target"], n, h["created_at"][:10], t):
            status, achieved_at = "achieved", now_iso()
        db.execute(
            "UPDATE habits SET current = ?, status = ?, achieved_at = ?, updated_at = ? WHERE id = ?",
            (current, status, achieved_at, now_iso(), h["id"]),
        )


def habit_dict(h) -> dict:
    lower = _lower_is_better(h["kind"])
    base, target, cur = h["baseline"], h["target"], h["current"]
    progress = 0.0
    if base is not None and target is not None and cur is not None and base != target:
        progress = (base - cur) / (base - target) if lower else (cur - base) / (target - base)
    if h["status"] == "achieved":
        progress = 1.0
    return {
        "id": h["id"], "key": h["key"], "title": h["title"], "kind": h["kind"], "word": h["word"],
        "advice": h["advice"] or "", "evidence": json.loads(h["evidence"] or "[]"),
        "baseline": base, "target": target, "current": cur,
        "unit": "次/百字" if lower else "分", "lower_is_better": lower,
        "progress": round(min(1.0, max(0.0, progress)), 2),
        "status": h["status"], "created_at": h["created_at"], "achieved_at": h["achieved_at"],
    }


def get_habits(db: DB, status: str | None = None) -> list[dict]:
    if status:
        rows = db.query("SELECT * FROM habits WHERE status = ? ORDER BY id DESC", (status,))
    else:
        rows = db.query("SELECT * FROM habits ORDER BY status = 'active' DESC, id DESC")
    return [habit_dict(h) for h in rows]


def set_habit_status(db: DB, hid: int, status: str) -> None:
    db.execute("UPDATE habits SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), hid))


# ================================================================ 自动诊断


def due_auto_diagnosis(db: DB, cfg: Config) -> str | None:
    """需要自动诊断的日期（前一天），没有则返回 None。失败后间隔 1 小时重试，最多 3 次。"""
    if not (cfg.coach.enabled and cfg.coach.auto_daily and cfg.llm.base_url):
        return None
    d = _shift(today(), -1)
    if latest_diagnosis(db, d):
        return None
    attempt = db.get_kv("auto_diagnosis", {})
    if attempt.get("date") == d:
        if attempt.get("count", 0) >= 3:
            return None
        last = datetime.fromisoformat(attempt["at"])
        if datetime.now() - last < timedelta(hours=1):
            return None
    n = db.query(
        "SELECT COUNT(*) AS n FROM utterances u JOIN metrics m ON m.utterance_id = u.id "
        "WHERE m.scoreable = 1 AND u.created_at >= ? AND u.created_at < ?", _day_range(d),
    )[0]["n"]
    return d if n >= max(cfg.coach.min_utterances, MIN_DIAGNOSIS_SAMPLES) else None


def run_auto_diagnosis(db: DB, cfg: Config) -> None:
    d = due_auto_diagnosis(db, cfg)
    if not d:
        return
    attempt = db.get_kv("auto_diagnosis", {})
    count = attempt.get("count", 0) + 1 if attempt.get("date") == d else 1
    db.set_kv("auto_diagnosis", {"date": d, "at": now_iso(), "count": count})
    try:
        diagnose(db, cfg, d)
    except Exception:
        log.exception("自动诊断 %s 失败（第 %d 次）", d, count)
