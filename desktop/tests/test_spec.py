"""共享规范一致性测试：spec/ 下的提示词、常量与黄金样例必须与 Python 实现完全一致。

spec/ 是两端（桌面 Python、Android Kotlin）共同的事实来源；桌面端的常量与提示词仍写在代码里
（wheel 不依赖仓库根目录的文件），由本测试保证两边同步。
"""

import json
from pathlib import Path

import pytest

from umless import coach, metrics, practice, prompts, textfix

SPEC = Path(__file__).resolve().parents[2] / "spec"
pytestmark = pytest.mark.skipif(not SPEC.is_dir(), reason="没有 spec/ 目录（不在仓库中运行）")

PROMPT_FILES = {
    "polish_base.txt": prompts.POLISH_BASE,
    **{f"intensity_{k}.txt": v for k, v in prompts.INTENSITY_RULES.items()},
    **{f"scene_{k}.txt": v for k, v in prompts.SCENE_STYLES.items()},
    "diagnosis_system.txt": prompts.DIAGNOSIS_SYSTEM,
    "diagnosis_en.txt": prompts.DIAGNOSIS_EN,
    "topics_system.txt": prompts.TOPICS_SYSTEM,
    "topics_en.txt": prompts.TOPICS_EN,
    "practice_system.txt": prompts.PRACTICE_SYSTEM,
    "practice_en.txt": prompts.PRACTICE_EN,
}
ANDROID_ONLY_PROMPTS = {"scene_search.txt"}  # 桌面端没有搜索框场景


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cases(sub: str) -> list[Path]:
    return sorted((SPEC / "cases" / sub).glob("*.json")) if SPEC.is_dir() else []


# ================================================================ 提示词


@pytest.mark.parametrize("name", sorted(PROMPT_FILES))
def test_prompt_file_equals_python(name):
    # 按字节比对：不允许 CRLF、BOM 或末尾多余换行
    assert (SPEC / "prompts" / name).read_bytes() == PROMPT_FILES[name].encode("utf-8")


def test_prompt_dir_has_no_unknown_files():
    files = {p.name for p in (SPEC / "prompts").glob("*.txt")}
    assert files == set(PROMPT_FILES) | ANDROID_ONLY_PROMPTS
    for name in ANDROID_ONLY_PROMPTS:
        text = (SPEC / "prompts" / name).read_bytes().decode("utf-8")
        assert text and not text.endswith("\n") and "\r" not in text


def test_fallback_topics():
    data = _json(SPEC / "practice_fallback_topics.json")
    assert data == {"zh": practice.FALLBACK_TOPICS, "en": practice.FALLBACK_TOPICS_EN}


# ================================================================ 常量


def test_constants_match_python():
    c = _json(SPEC / "constants.json")
    assert c["metrics_version"] == metrics.METRICS_VERSION
    assert c["min_scoreable_chars"] == metrics.MIN_SCOREABLE_CHARS
    assert c["builtin_fillers"] == metrics.BUILTIN_FILLERS
    assert set(c["interjections"]) == metrics.INTERJECTIONS
    assert len(c["interjections"]) == len(metrics.INTERJECTIONS)
    assert c["correction_markers"] == metrics.CORRECTION_MARKERS
    assert c["repeat"]["max_len"] == metrics.REPEAT_MAX_LEN
    assert c["score_formula"] == {"fluency_k": metrics.FLUENCY_K, "concision_k": metrics.CONCISION_K}
    assert c["overall_weights"] == coach.WEIGHTS
    assert c["practice_weights"] == practice.PRACTICE_WEIGHTS
    assert c["coach"]["max_active_habits"] == coach.MAX_ACTIVE_HABITS
    assert c["coach"]["min_diagnosis_samples"] == coach.MIN_DIAGNOSIS_SAMPLES
    assert c["coach"]["typing_cpm"] == coach.TYPING_CPM
    assert c["coach"]["diagnosis_budget"] == coach.DIAGNOSIS_BUDGET
    assert c["asr"]["common_words"] == textfix.COMMON_WORDS
    assert c["asr"]["short_max"] == textfix.SHORT_MAX
    assert c["asr"]["cjk_min_codepoint"] == textfix.CJK_MIN


def test_constants_by_behavior():
    """写在函数体里的阈值，通过行为核对。"""
    c = _json(SPEC / "constants.json")
    # 没有润色稿时只有 ≥2 字的重复才算
    lo = c["repeat"]["min_len_without_polish"]
    assert metrics.analyze("好" * (2 * lo), None)["repeat_count"] == 1
    assert metrics.analyze("好好", None)["repeat_count"] == (1 if lo <= 1 else 0)
    # LLM 分 1–5，× 20
    s = c["llm_score"]
    assert coach._score_1to5(-3) == s["min"] and coach._score_1to5(99) == s["max"]
    scores, _ = practice.practice_scores({"structure": 1, "logic": 5, "wording": 3}, 80.0, 80.0)
    assert (scores["structure"], scores["logic"], scores["wording"]) == (s["scale"], 5 * s["scale"], 3 * s["scale"])
    # 逐字稿截断
    rows = [{"id": 1, "chars": 999, "scoreable": True, "app": "", "raw_text": "字" * 999}]
    assert len(coach._sample_for_llm(rows)[0]["raw_text"]) == c["coach"]["diagnosis_text_max"]
    # 习惯卡
    h = c["habit"]
    assert {k for k in coach.HABIT_KINDS if coach._lower_is_better(k)} == set(h["lower_is_better_kinds"])
    assert coach.HABIT_KINDS == set(h["lower_is_better_kinds"]) | set(h["higher_is_better_kinds"])
    assert coach.habit_target("word", h["lower_min_baseline"]) == round(h["lower_min_baseline"] * h["lower_target_ratio"], 2)
    assert coach.habit_target("word", h["lower_min_baseline"] - 0.01) is None
    assert coach.habit_target("logic", h["higher_max_baseline"]) is None
    assert coach.habit_target("logic", 50.0) == 50.0 + h["higher_target_delta"]
    assert coach.habit_target("logic", h["higher_max_baseline"] - 1) == h["higher_target_cap"]
    today = "2026-03-10"
    old = coach._shift(today, -h["min_age_days"])
    assert coach.habit_reached("word", 0.1, 0.5, h["lower_min_samples"], old, today)
    assert not coach.habit_reached("word", 0.1, 0.5, h["lower_min_samples"] - 1, old, today)
    assert coach.habit_reached("logic", 90.0, 80.0, h["higher_min_samples"], old, today)
    assert not coach.habit_reached("logic", 90.0, 80.0, h["higher_min_samples"] - 1, old, today)
    assert not coach.habit_reached("word", 0.1, 0.5, 99, coach._shift(old, 1), today)


# ================================================================ 指标样例


def test_enough_metrics_cases():
    assert len(_cases("metrics")) >= 40


@pytest.mark.parametrize("path", _cases("metrics"), ids=lambda p: p.stem)
def test_metrics_case(path):
    c = _json(path)
    assert c["name"] == path.stem
    assert metrics.analyze(c["raw"], c["polished"], c["duration_s"], c["lexicon"]) == c["expected"]


# ================================================================ 评分样例


def _stats_of(rows):
    db_rows = [{**r, "fillers": json.dumps(r["fillers"], ensure_ascii=False) if r["chars"] is not None else None}
               for r in rows]
    return coach.stats_of(db_rows)


def _habit_progress(kind, baseline, target, current, status):
    row = {"id": 1, "key": kind, "title": "", "kind": kind, "word": None, "advice": "", "evidence": "[]",
           "baseline": baseline, "target": target, "current": current, "status": status,
           "created_at": "2026-03-01T00:00:00", "achieved_at": None}
    return coach.habit_dict(row)["progress"]


def _practice(llm_scores, fluency, concision):
    scores, overall = practice.practice_scores(llm_scores, fluency, concision)
    return {"scores": scores, "overall": overall}


SCORE_FUNCS = {
    "fluency_score": metrics.fluency_score,
    "concision_score": metrics.concision_score,
    "overall_score": coach.overall_score,
    "round_int": coach._r,
    "practice_scores": _practice,
    "aggregate": metrics.aggregate,
    "stats_of": _stats_of,
    "habit_target": coach.habit_target,
    "habit_reached": coach.habit_reached,
    "habit_progress": _habit_progress,
    "sample_for_llm": coach._sample_for_llm,
    "content_len": coach._content_len,
}


def _score_cases():
    out = []
    for path in _cases("scores"):
        data = _json(path)
        assert data["kind"] == path.stem
        out += [pytest.param(data["kind"], c, id=f"{data['kind']}-{c['name']}") for c in data["cases"]]
    return out


def test_score_kinds_complete():
    assert {p.stem for p in _cases("scores")} == set(SCORE_FUNCS)


@pytest.mark.parametrize(("kind", "case"), _score_cases())
def test_score_case(kind, case):
    got = SCORE_FUNCS[kind](**case["input"])
    assert got == case["expected"]
    assert type(got) is type(case["expected"]) or isinstance(got, float) and isinstance(case["expected"], float)


# ================================================================ 提示词样例


@pytest.mark.parametrize("path", _cases("prompts"), ids=lambda p: p.stem)
def test_prompt_case(path):
    c = _json(path)
    assert getattr(prompts, c["builder"])(**c["input"]) == c["expected"]


# ================================================================ 识别结果后处理样例


def test_asr_common_words_well_formed():
    words = textfix.COMMON_WORDS
    assert words == sorted(set(words))
    assert all(w.isascii() and w.isalpha() and w.islower() and len(w) <= textfix.SHORT_MAX for w in words)


def test_enough_asr_cases():
    assert len(_cases("asr")) >= 30


@pytest.mark.parametrize("path", _cases("asr"), ids=lambda p: p.stem)
def test_asr_case(path):
    c = _json(path)
    assert c["name"] == path.stem
    got = textfix.fix_case(c["input"], c["glossary"])
    assert got == c["expected"]
    assert textfix.fix_case(got, c["glossary"]) == got  # 幂等
