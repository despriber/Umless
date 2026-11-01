"""从桌面端 Python 实现生成 spec/cases 下的黄金样例（指标、评分、提示词、识别结果后处理）。

用法（在 desktop 目录下）：
    uv run python ../spec/tools/gen_metrics_cases.py           # 重新生成
    uv run python ../spec/tools/gen_metrics_cases.py --check   # 只比对，不一致时退出码为 1

样例的输入在本文件里手写（通用句子，不含任何个人数据），期望值一律由当前 Python 实现算出。
修改规则的流程：先改 spec/metrics.md → 改两端实现 → 重新生成样例 → 两端测试都通过。
"""

import argparse
import json
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPEC.parent / "desktop"))

from umless import coach, metrics, practice, prompts, textfix  # noqa: E402

CASES = SPEC / "cases"

# ================================================================ 指标样例（metrics.analyze）

LONG_RAW = (
    "嗯，大家好，那个今天主要是想跟大家同步一下这个项目的进度。然后呢，就是说上周我们其实已经把登录模块做完了，"
    "呃，测试那边也基本上过了一遍。然后就是支付这块，支付这块还有点问题，就是第三方的接口文档不对，哦不，"
    "应该是接口文档写得不太清楚，所以我们我们跟对方又确认了两次。反正现在的计划是，嗯，下周三之前把支付联调完，"
    "然后周五提测。那么风险的话，主要是对方的测试环境不稳定，经常挂，这个这个我们也没办法。另外，相当于说我们"
    "还需要一个前端同学来帮忙做一下页面，怎么说呢，现在人手确实有点紧张。最后就是，大家有什么问题的话可以随时"
    "找我，对吧，就这些，谢谢大家。"
)
LONG_MODERATE = (
    "大家好，今天主要想跟大家同步一下项目的进度。上周我们已经把登录模块做完了，测试那边也过了一遍。"
    "支付这块还有点问题，第三方的接口文档写得不太清楚，所以我们跟对方又确认了两次。现在的计划是下周三之前"
    "把支付联调完，周五提测。风险主要是对方的测试环境不稳定，经常挂，我们也没办法。另外，我们还需要一个前端"
    "同学来帮忙做页面，现在人手确实有点紧张。大家有什么问题可以随时找我，谢谢大家。"
)
LONG_RESTRUCTURE = (
    "项目进度同步：\n1. 登录模块：上周已完成，测试已过一遍。\n2. 支付模块：第三方接口文档不清楚，已与对方确认两次；"
    "计划下周三前完成联调，周五提测。\n\n风险：对方测试环境不稳定。\n需要支持：一位前端同学帮忙做页面。\n有问题随时找我。"
)
LONG_MIXED_RAW = (
    "呃，我们这个服务现在是用 Python 写的，然后部署在 Kubernetes 上面，就是每个 Pod 大概分了 2 个 CPU、4G 内存。"
    "那个最近的问题是，嗯，高峰期的时候 P99 延迟会涨到 800 毫秒左右，其实主要是数据库连接池不够用，"
    "我是说 Redis 的连接池不够用。然后我们打算先把连接池从 20 调到 50，然后再观察一下，观察一下 Grafana 上的曲线。"
    "如果还不行的话，就是说可能要考虑加一层本地缓存，或者把热点数据拆出去。反正这周五之前会给一个结论，"
    "到时候我再在群里同步，OK，大概就是这样。"
)
LONG_MIXED_POLISHED = (
    "我们的服务用 Python 编写，部署在 Kubernetes 上，每个 Pod 分配 2 个 CPU、4G 内存。最近的问题是高峰期 "
    "P99 延迟会涨到 800 毫秒左右，主要是 Redis 的连接池不够用。我们打算先把连接池从 20 调到 50，再观察 "
    "Grafana 上的曲线。如果还不行，可能要考虑加一层本地缓存，或者把热点数据拆出去。这周五之前会给出结论，"
    "到时候我在群里同步。"
)


def m(name, raw, polished=None, duration_s=None, lexicon=None):
    return {"name": name, "raw": raw, "polished": polished, "duration_s": duration_s, "lexicon": lexicon}


METRICS_CASES = [
    # ---- 口癖基础 ----
    m("filler_basic", "嗯，我觉得这个方案还可以，然后我们明天再讨论一下。", "我觉得这个方案还可以，我们明天再讨论一下。", 6.2),
    m("filler_kept_in_polish", "那个文件我已经发给你了，你看一下这个表格。", "那个文件我已经发给你了，你看一下这个表格。", 4.0),
    m("filler_partially_kept", "就是说这个方案不行，成本太高。", "说这个方案不行，成本太高。"),
    m("filler_overlap_longest_first", "就是说这个方案不行，就是成本太高了。", "这个方案不行，成本太高了。"),
    m("filler_double_with_repeat", "嗯嗯，我知道了，马上就改。", "我知道了，马上就改。"),
    m("filler_many_builtin", "然后呢其实反正基本上，怎么说呢，相当于说所以说那么就这样了的话对吧", "就这样了"),
    # ---- 没有润色稿 ----
    m("no_polish_interjections", "嗯，然后呃我们就先这样吧，啊对，唉。", None, 3.0),
    m("no_polish_repeats", "我觉得我觉得这个这个可以，看看再说。"),
    m("no_polish_repeat_second_labeled", "我们我们先吃饭，然后再开会讨论这个问题。"),
    m("no_polish_reduplication_false_positive", "这个问题我们研究研究，回头再商量商量。"),
    m("polished_empty_string_means_no_polish", "嗯，那个我们先这样吧，然后再说。", ""),
    # ---- 空串与边界 ----
    m("empty_no_polish", "", None),
    m("empty_both", "", ""),
    m("empty_raw_with_polish", "", "好的。"),
    m("polish_only_punct", "嗯嗯，好的", "。"),
    m("raw_only_punct", "……，。！？  \n", "。"),
    m("whitespace_punct_mix", "  嗯……  好 ， 我 知道 了 \n\t 明天 见 ！ ", "好，我知道了，明天见！", 2.5),
    m("scoreable_14_chars", "嗯我们明天下午三点开个会吧好", "我们明天下午三点开个会吧好"),
    m("scoreable_15_chars", "嗯我们明天下午三点开个会吧好吗", "我们明天下午三点开个会吧好吗"),
    m("cpm_zero_duration", "嗯，好的，我知道了。", "好的，我知道了。", 0.0),
    m("cpm_fraction", "那个我们下午三点在三楼会议室开会，记得带上电脑。", "我们下午三点在三楼会议室开会，记得带上电脑。", 7.3),
    # ---- 英文、大小写、全角、特殊字符 ----
    m("english_case_fold", "我在用 GitHub Copilot 写代码，嗯，然后 PR 就提了。", "我在用 github copilot 写代码，PR 就提了。", 5.0),
    m("english_lexicon_substring", "Um so basically the API is down, like, totally down.", "The API is totally down.",
      3.4, ["um", "like", "so", "basically", "you know"]),
    m("english_lexicon_uppercase_word", "LIKE I said, like, it works.", "As I said, it works.", None, ["Like"]),
    m("fullwidth_vs_halfwidth", "ＡＩ助手１２点开会，嗯。", "AI助手12点开会。"),
    m("fullwidth_both", "ＡＩ助手，嗯，１２点开会。", "ＡＩ助手１２点开会。"),
    m("emoji", "好的👍嗯，收到🎉，然后我马上处理😀", "好的👍收到🎉，我马上处理", 2.0),
    m("cjk_ext_b_supplementary", "𠮷野家的那个牛肉饭，嗯，挺好吃的。", "𠮷野家的牛肉饭挺好吃的。"),
    m("number_variants", "第①点，嗯，预算是½，Ⅻ月份交付，3点开会，²。", "第①点，预算是½，Ⅻ月份交付，3点开会，²。"),
    m("combining_marks", "caf\u00e9 和 cafe\u0301，嗯，是一样的。", "caf\u00e9 和 cafe\u0301 是一样的。"),
    m("greek_final_sigma", "ΟΔΟΣ 和 Москва，嗯。", "οδος 和 москва。"),
    m("turkish_dotted_i", "İstanbul 的 Ankara，嗯，都去过。", "istanbul 的 Ankara 都去过。"),
    # ---- 改口 ----
    m("correction_budui", "明天，哦不对，后天开会。", "后天开会。"),
    m("correction_woshishuo", "我们周三，我是说周四交初稿。", "我们周四交初稿。"),
    m("correction_kept_in_polish", "这个说法不对，应该是三点。", "这个说法不对，应该是三点。"),
    m("correction_bushibushi_difflib", "不是不是，是这样的", "不是，是这样的"),
    m("correction_multiple", "我的意思是，准确地说，下周一上线，说错了，下周二上线。", "下周二上线。"),
    m("correction_oubu_eaten_by_filler", "哦不，是周五交。", "是周五交。"),
    m("correction_oubu_custom_lexicon", "哦不，是周五交。", "是周五交。", None, []),
    # ---- 相邻重复 ----
    m("repeat_len1_triple", "这这这个功能我们先不做。", "这个功能我们先不做。"),
    m("repeat_reduplication_kept", "谢谢你，我们再看看吧。", "谢谢你，我们再看看吧。"),
    m("repeat_len3", "我觉得我觉得这样不太好。", "我觉得这样不太好。"),
    m("repeat_len8", "我们明天下午开会我们明天下午开会，记得带电脑。", "我们明天下午开会，记得带电脑。"),
    m("repeat_len10_not_detected", "这个项目的进度有点慢这个项目的进度有点慢", "这个项目的进度有点慢"),
    m("repeat_separated_by_filler", "我觉得，嗯，我觉得可以。", "我觉得可以。"),
    m("repeat_first_copy_cut", "我们我们去吃饭吧。", "我们去吃饭吧。"),
    m("repeat_tie_second_copy_cut", "好的好的，没问题。", "好的，没问题。"),
    m("repeat_triple_dui", "对对对，就是这样。", "对，就是这样。"),
    m("repeat_numbers_in_text", "一二三一二三一二三", "一二三"),
    # ---- difflib 细节 ----
    m("difflib_tie_earliest_j", "开会", "开会，开会吧"),
    m("difflib_greedy_not_lcs", "下午三点开会，三点半结束", "三点开会，下午三点半结束"),
    m("difflib_repeated_substrings", "我们先看数据然后我们先看结论", "我们先看结论"),
    m("difflib_reordered", "原因是预算不够，所以项目暂停", "项目暂停，原因是预算不够"),
    m("difflib_ab_ba", "甲乙甲乙乙甲", "乙甲甲乙"),
    # ---- 自定义词表 ----
    m("lexicon_english_mixed", "OK，you know，就是这个意思，OK吗", "就是这个意思，好吗", None, ["OK", "you know", "就是"]),
    m("lexicon_empty", "嗯，然后我们走吧。", "我们走吧。", None, []),
    m("lexicon_dup_and_punct", "嗯，我觉得吧，嗯。这样可以。", "这样可以。", None, ["嗯", "嗯", "，", "嗯。", "我觉得吧"]),
    m("lexicon_same_length_overlap_order", "就是说好了，就是说定了。", "好了，定了。", None, ["就是", "是说"]),
    m("lexicon_same_length_overlap_order_rev", "就是说好了，就是说定了。", "好了，定了。", None, ["是说", "就是"]),
    # ---- 长文本 ----
    m("long_meeting_moderate", LONG_RAW, LONG_MODERATE, 75.0),
    m("long_meeting_restructure", LONG_RAW, LONG_RESTRUCTURE, 75.0),
    m("long_meeting_no_polish", LONG_RAW, None, 75.0),
    m("long_mixed_tech", LONG_MIXED_RAW, LONG_MIXED_POLISHED, 58.6),
]


def metrics_case(c: dict) -> dict:
    return {**c, "expected": metrics.analyze(c["raw"], c["polished"], c["duration_s"], c["lexicon"])}


# ================================================================ 评分样例


def _exact(v) -> Fraction:
    """输入值按十进制字面量理解的精确值（用于找出"恰好是 .5"的边界样例）。"""
    return Fraction(Decimal(repr(v)))


def _is_tie(x: Fraction, digits: int) -> bool:
    y = x * 10**digits * 2
    return y.denominator == 1 and y.numerator % 2 == 1


def _overall_ties() -> list[dict]:
    """综合分恰好落在 x.x5 的组合：Python 的结果取决于二进制浮点值和 HALF_EVEN，而不是十进制的四舍五入。"""
    w = {k: Fraction(Decimal(str(v))) for k, v in coach.WEIGHTS.items()}
    down, up = [], []
    for f10 in range(600, 1001, 3):
        for c10 in range(500, 1001, 7):
            for logic in (60.0, 70.0, 3.7 * 20):
                vals = {"fluency": f10 / 10, "concision": c10 / 10, "logic": logic, "clarity": 70.0}
                exact = sum(w[k] * _exact(v) for k, v in vals.items()) / sum(w.values())
                if not _is_tie(exact, 1):
                    continue
                py = coach.overall_score(**vals)
                (down if _exact(py) < exact else up).append(vals)
    return down[:3] + up[:3]


def _practice_ties() -> list[dict]:
    """练习综合分（取整）恰好落在 .5 的组合。"""
    w = {k: Fraction(Decimal(str(v))) for k, v in practice.PRACTICE_WEIGHTS.items()}
    down, up = [], []
    for s in (3.0, 3.5, 4.0):
        for wd in (3.0, 3.5, 4.5):
            for f10 in range(600, 1000, 3):
                for c10 in range(600, 1000, 11):
                    llm = {"structure": s, "logic": 4.0, "wording": wd}
                    scores, overall = practice.practice_scores(llm, f10 / 10, c10 / 10)
                    exact = sum(w[k] * v for k, v in scores.items()) / sum(w.values())
                    if not _is_tie(exact, 0):
                        continue
                    item = {"llm_scores": llm, "fluency": f10 / 10, "concision": c10 / 10}
                    (down if overall < exact else up).append(item)
    return down[:3] + up[:3]


def _ratio_ties() -> list[list[float]]:
    """两条记录改写幅度的平均值恰好落在第 4 位小数为 5。"""
    down, up = [], []
    for a in range(1, 400, 3):
        for b in range(a + 1, a + 40, 2):
            ra, rb = a / 1000, b / 1000
            exact = (_exact(ra) + _exact(rb)) / 2
            if not _is_tie(exact, 3):
                continue
            py = round((ra + rb) / 2, 3)
            (down if _exact(py) < exact else up).append([ra, rb])
    return down[:3] + up[:3]


def _naive_sum(xs) -> float:
    """朴素逐项累加（错误的移植方式），只用来挑出能区分 Neumaier 求和的样例。"""
    f = 0.0
    for x in xs:
        f += x
    return f


def _naive_sum_overall() -> list[dict]:
    """用朴素累加算综合分会得到不同结果的组合（Python 3.12+ 的 sum() 是 Neumaier 补偿求和）。"""
    out = []
    for f10 in range(400, 1001):
        for c10 in range(400, 1001, 7):
            for logic, clarity in ((3.7 * 20, 66.0), (60.0, 90.0)):
                v = {"fluency": f10 / 10, "concision": c10 / 10, "logic": logic, "clarity": clarity}
                naive = round(_naive_sum(coach.WEIGHTS[k] * x for k, x in v.items()) / _naive_sum(coach.WEIGHTS.values()), 1)
                if naive != coach.overall_score(**v):
                    out.append(v)
                    if len(out) == 3:
                        return out
    return out


def _naive_sum_ratios() -> list[list[float]]:
    out = []
    for n in (4, 6):
        for a in range(1, 1000):
            rs = [((a * (i + 7) * 37) % 1000) / 1000 for i in range(n)]
            if round(sum(rs) / n, 3) != round(_naive_sum(rs) / n, 3):
                out.append(rs)
                break
    return out


def _row(chars, fillers=None, repeat=0, correction=0, ratio=None, scoreable=True):
    return {"chars": chars, "fillers": fillers or {}, "repeat_count": repeat, "correction_count": correction,
            "rewrite_ratio": ratio, "scoreable": scoreable}


AGG_INPUTS = [
    ("empty", []),
    ("all_unscoreable", [_row(8, {"嗯": 1}, scoreable=False), _row(10, scoreable=False)]),
    ("single", [_row(40, {"然后": 2, "嗯": 1}, 1, 0, 0.215)]),
    ("weighted_by_chars", [_row(20, {"嗯": 2}, 0, 1, 0.1), _row(180, {"就是": 3}, 2, 0, 0.3),
                           _row(9, {"嗯": 5}, scoreable=False)]),
    ("filler_stats_tie_order", [_row(50, {"然后": 2, "就是": 3}), _row(60, {"嗯": 3, "然后": 1})]),
    ("ratio_none_mixed", [_row(30, {}, 0, 0, None), _row(30, {"那个": 1}, 0, 0, 0.42), _row(30, {}, 1, 1, 0.0)]),
    ("ratio_neumaier_sum", [_row(30, {}, 0, 0, 0.1) for _ in range(10)]),
    ("per100_rounding", [_row(7, {"嗯": 1}), _row(14, {"嗯": 1, "呃": 2})]),
    ("zero_chars_scoreable", [_row(0)]),
] + [(f"ratio_half_{i}", [_row(30, {}, 0, 0, a), _row(30, {}, 0, 0, b)]) for i, (a, b) in enumerate(_ratio_ties())] + [
    (f"ratio_neumaier_{i}", [_row(30, {}, 0, 0, r) for r in rs]) for i, rs in enumerate(_naive_sum_ratios())]


def _stats(rows: list[dict]) -> dict:
    db_rows = [{**r, "fillers": json.dumps(r["fillers"], ensure_ascii=False) if r["chars"] is not None else None}
               for r in rows]
    return coach.stats_of(db_rows)


STATS_INPUTS = [
    ("empty", []),
    ("missing_metrics_skipped", [_row(None), _row(40, {"嗯": 2}, 1, 1, 0.2)]),
    ("mixed", [_row(20, {"嗯": 2}, 0, 1, 0.1), _row(180, {"就是": 3}, 2, 0, 0.3), _row(9, {"嗯": 5}, 3, 3, 0.5, False)]),
    ("fillers_per100_rounding", [_row(30, {"然后": 1}), _row(37, {"嗯": 1})]),
]


def _sample_rows() -> list[dict]:
    base = "今天我们讨论了项目的进度和下一步的计划，大家对接口设计提了一些意见，需要再确认一下时间安排。"
    rows = []
    lengths = [40, 420, 120, 310, 300, 299, 60, 500, 15, 300, 280, 350, 90, 300, 260, 300, 330, 100, 300,
               305, 200, 300, 301, 150, 300, 290, 300, 300, 20, 300, 300, 310]
    for i, n in enumerate(lengths, 1):
        text = (base * (n // len(base) + 1))[:n]
        rows.append({"id": i * 3, "chars": metrics.analyze(text, None)["chars"], "scoreable": i % 9 != 0,
                     "app": "Notepad" if i % 2 else "", "raw_text": text})
    return rows


def _habit_progress(kind, baseline, target, current, status):
    row = {"id": 1, "key": kind, "title": "", "kind": kind, "word": None, "advice": "", "evidence": "[]",
           "baseline": baseline, "target": target, "current": current, "status": status,
           "created_at": "2026-03-01T00:00:00", "achieved_at": None}
    return coach.habit_dict(row)["progress"]


def _practice(llm_scores, fluency, concision):
    scores, overall = practice.practice_scores(llm_scores, fluency, concision)
    return {"scores": scores, "overall": overall}


def score_files() -> dict[str, dict]:
    def kind(name, fn, inputs):
        return {"kind": name, "cases": [{"name": n, "input": i, "expected": fn(**i)} for n, i in inputs]}

    per100 = [0, 0.5, 3, 5, 10, 100 / 3, 100 / 14, 1 / 7 * 100, 200]
    return {
        "fluency_score": kind("fluency_score", metrics.fluency_score,
                              [(f"p{i}", {"fillers_per100": p}) for i, p in enumerate(per100)]),
        "concision_score": kind("concision_score", metrics.concision_score,
                                [(f"p{i}", {"issues_per100": p}) for i, p in enumerate(per100)]),
        "overall_score": kind("overall_score", coach.overall_score, [
            ("all_none", {"fluency": None, "concision": None, "logic": None, "clarity": None}),
            ("full", {"fluency": 76.3, "concision": 88.6, "logic": 70.0, "clarity": 80.0}),
            ("rules_only", {"fluency": 76.3, "concision": 88.6, "logic": None, "clarity": None}),
            ("missing_clarity", {"fluency": 64.0, "concision": 100.0, "logic": 90.0, "clarity": None}),
            ("only_fluency", {"fluency": 41.1, "concision": None, "logic": None, "clarity": None}),
            ("only_logic", {"fluency": None, "concision": None, "logic": 3.7 * 20, "clarity": None}),
            ("llm_float_artifact", {"fluency": 80.0, "concision": 80.0, "logic": 3.7 * 20, "clarity": 3.3 * 20}),
        ] + [(f"half_{i}", v) for i, v in enumerate(_overall_ties())]
          + [(f"neumaier_{i}", v) for i, v in enumerate(_naive_sum_overall())]),
        "round_int": kind("round_int", coach._r, [
            (f"x{i}", {"x": x}) for i, x in enumerate(
                [None, 0.5, 1.5, 2.5, 76.5, 77.5, 76.49999999999999, 64.0, 99.5, 100.0, 0.0, 3.7 * 20, 65.45])
        ]),
        "practice_scores": kind("practice_scores", _practice, [
            ("full", {"llm_scores": {"structure": 3.5, "logic": 4, "wording": 3}, "fluency": 76.3, "concision": 88.6}),
            ("llm_failed", {"llm_scores": {}, "fluency": 64.0, "concision": 55.1}),
            ("fluency_half_even", {"llm_scores": {"structure": 4, "logic": 4, "wording": 4},
                                   "fluency": 76.5, "concision": 77.5}),
            ("clamp_and_strings", {"llm_scores": {"structure": 7, "logic": "3.5", "wording": 0},
                                   "fluency": 100.0, "concision": 100.0}),
            ("invalid_values", {"llm_scores": {"structure": "abc", "logic": None, "wording": [1]},
                                "fluency": 41.1, "concision": 0.5}),
            ("non_half_llm", {"llm_scores": {"structure": 3.3, "logic": 3.7, "wording": 4.25},
                              "fluency": 90.0, "concision": 85.0}),
        ] + [(f"half_{i}", v) for i, v in enumerate(_practice_ties())]),
        "aggregate": kind("aggregate", metrics.aggregate, [(n, {"rows": rows}) for n, rows in AGG_INPUTS]),
        "stats_of": kind("stats_of", _stats, [(n, {"rows": rows}) for n, rows in STATS_INPUTS]),
        "habit_target": kind("habit_target", coach.habit_target, [
            (f"{k}_{b}", {"kind": k, "baseline": b}) for k, b in [
                ("word", None), ("word", 0.29), ("word", 0.3), ("word", 0.125), ("correction", 0.85),
                ("repeat", 2.5), ("word", 1.234), ("logic", 60.0), ("logic", 89.9), ("logic", 90.0),
                ("clarity", 85.5), ("clarity", 70.0)]
        ]),
        "habit_reached": kind("habit_reached", coach.habit_reached, [
            ("lower_ok", {"kind": "word", "current": 0.5, "target": 0.5, "n": 10,
                          "created_day": "2026-03-07", "today_day": "2026-03-10"}),
            ("lower_not_reached", {"kind": "repeat", "current": 0.51, "target": 0.5, "n": 30,
                                   "created_day": "2026-03-01", "today_day": "2026-03-10"}),
            ("lower_few_samples", {"kind": "correction", "current": 0.1, "target": 0.5, "n": 9,
                                   "created_day": "2026-03-01", "today_day": "2026-03-10"}),
            ("too_new", {"kind": "word", "current": 0.1, "target": 0.5, "n": 20,
                         "created_day": "2026-03-08", "today_day": "2026-03-10"}),
            ("higher_ok", {"kind": "logic", "current": 80.0, "target": 80.0, "n": 2,
                           "created_day": "2026-02-27", "today_day": "2026-03-02"}),
            ("higher_few_samples", {"kind": "clarity", "current": 90.0, "target": 80.0, "n": 1,
                                    "created_day": "2026-02-01", "today_day": "2026-03-02"}),
            ("higher_not_reached", {"kind": "clarity", "current": 79.9, "target": 80.0, "n": 5,
                                    "created_day": "2026-02-01", "today_day": "2026-03-02"}),
            ("leap_day_too_new", {"kind": "logic", "current": 90.0, "target": 80.0, "n": 3,
                                  "created_day": "2028-02-29", "today_day": "2028-03-02"}),
            ("leap_day_ok", {"kind": "logic", "current": 90.0, "target": 80.0, "n": 3,
                             "created_day": "2028-02-28", "today_day": "2028-03-02"}),
        ]),
        "habit_progress": kind("habit_progress", _habit_progress, [
            (n, {"kind": k, "baseline": b, "target": t, "current": c, "status": s}) for n, k, b, t, c, s in [
                ("lower_half", "word", 2.0, 1.0, 1.5, "active"),
                ("lower_third", "repeat", 3.0, 1.5, 2.5, "active"),
                ("lower_worse_clamped", "word", 2.0, 1.0, 2.6, "active"),
                ("lower_beyond_clamped", "correction", 2.0, 1.0, 0.2, "active"),
                ("higher", "logic", 60.0, 80.0, 75.0, "active"),
                ("base_equals_target", "logic", 80.0, 80.0, 90.0, "active"),
                ("current_none", "word", 2.0, 1.0, None, "active"),
                ("achieved_forced_one", "word", 2.0, 1.0, 1.9, "achieved"),
                ("rounding", "clarity", 63.0, 83.0, 70.0, "active"),
            ]
        ]),
        "sample_for_llm": kind("sample_for_llm", coach._sample_for_llm, [
            ("small", {"rows": [
                {"id": 1, "chars": 20, "scoreable": True, "app": "微信", "raw_text": "我们明天下午三点开会，记得带电脑和资料。"},
                {"id": 2, "chars": 4, "scoreable": False, "app": "", "raw_text": "好的收到"},
                {"id": 3, "chars": 17, "scoreable": True, "app": "", "raw_text": "这个需求下周再评估一下排期吧好吗。"},
            ]}),
            ("budget_8000", {"rows": _sample_rows()}),
        ]),
        "content_len": kind("content_len", coach._content_len, [
            (f"t{i}", {"text": s}) for i, s in enumerate(
                ["", "嗯，好的。", "Hello_world 123", "  \n\t", "𠮷野家👍", "cafe\u0301", "²Ⅻ½①", "ＡＩ，１２。"])
        ]),
    }


# ================================================================ 提示词样例

DIAG_STATS = {
    "n": 12, "fluency": 76.3, "concision": 88.6, "fillers_per100": 2.95, "repeat_count": 3, "correction_count": 1,
    "filler_stats": [{"word": w, "count": c, "per100": 0.1} for w, c in
                     [("然后", 9), ("就是", 7), ("嗯", 5), ("那个", 4), ("其实", 3), ("对吧", 2), ("反正", 2),
                      ("的话", 1), ("那么", 1)]],
}
DIAG_UTTS = [
    {"app": "微信", "raw_text": "嗯，那个我们明天下午三点开会吧，然后记得带电脑。"},
    {"app": "", "raw_text": "就是说这个方案其实还可以，但是成本有点高。"},
    {"app": "Visual Studio Code", "raw_text": "帮我把这个函数改成异步的，然后加上错误处理。"},
]

PROMPT_CASES = [
    ("polish_default_no_glossary", "build_messages",
     {"raw_text": "嗯，帮我写一首关于秋天的诗。", "glossary": [], "scene": "default", "intensity": "moderate"}),
    ("polish_chat_faithful_glossary", "build_messages",
     {"raw_text": "那个 cloud code 今天又更新了，你看一下", "glossary": ["Claude Code", "GitHub", "Kubernetes"],
      "scene": "chat", "intensity": "faithful"}),
    ("polish_code_restructure", "build_messages",
     {"raw_text": "就是把 main 函数里的那个循环，嗯，拆成两个函数", "glossary": ["main"],
      "scene": "code", "intensity": "restructure"}),
    ("polish_email_moderate", "build_messages",
     {"raw_text": "王老师您好，就是想问一下周五的组会能不能改到下午", "glossary": [], "scene": "email",
      "intensity": "moderate"}),
    ("polish_document_multiline", "build_messages",
     {"raw_text": "第一点是预算\n第二点是人手", "glossary": [], "scene": "document", "intensity": "restructure"}),
    ("polish_unknown_scene_intensity_fallback", "build_messages",
     {"raw_text": "好的", "glossary": [], "scene": "unknown_scene", "intensity": "xyz"}),
    ("diagnosis_zh_full", "build_diagnosis_messages",
     {"date": "2026-03-10", "utterances": DIAG_UTTS, "stats": DIAG_STATS, "lexicon": ["嗯", "然后", "就是"],
      "glossary": ["Claude Code", "GitHub"], "active_habits": ["减少「就是」"], "lang": "zh"}),
    ("diagnosis_en_empty_lists", "build_diagnosis_messages",
     {"date": "2026-03-11", "utterances": DIAG_UTTS[:2],
      "stats": {"fluency": None, "concision": None, "fillers_per100": 0, "repeat_count": 0,
                "correction_count": 0, "filler_stats": []},
      "lexicon": [], "glossary": [], "active_habits": [], "lang": "en"}),
    ("topics_zh", "build_topics_messages",
     {"recent_texts": ["我们这周把登录模块做完了，下周开始做支付。", "这个实验的结果和预期不太一样，需要再跑一次。"],
      "weak_points": ["减少「就是」", "先说结论"], "lang": "zh"}),
    ("topics_en_empty", "build_topics_messages", {"recent_texts": [], "weak_points": [], "lang": "en"}),
    ("practice_zh_half_even_duration", "build_practice_messages",
     {"topic": {"id": "t1", "title": "汇报本周的工作进展", "category": "工作汇报", "prompt": "在周会上向负责人汇报本周进展。",
                "hint": "结论 → 完成了什么 → 风险与需要的支持", "duration": 60, "personal": False},
      "raw_text": "嗯，这周主要是把登录模块做完了。", "duration_s": 62.5, "lang": "zh"}),
    ("practice_en_escape_and_90s", "build_practice_messages",
     {"topic": {"title": "Explain \"RAG\"\nto a friend", "prompt": "", "hint": "Definition → Example", "duration": 90},
      "raw_text": "So RAG means retrieval augmented generation.", "duration_s": 63.5, "lang": "en"}),
    ("practice_custom_topic_no_duration", "build_practice_messages",
     {"topic": {"title": "自由发挥"}, "raw_text": "我想说一下我对远程办公的看法。", "duration_s": 59.99, "lang": "zh"}),
]


def prompt_case(name, builder, inp) -> dict:
    return {"name": name, "builder": builder, "input": inp, "expected": getattr(prompts, builder)(**inp)}


# ================================================================ 识别结果后处理样例（textfix.fix_case）

GLOSSARY = ["Python", "GitHub", "Claude Code"]

ASR_CASES = [
    # ---- 基本：普通词小写、句首大写、缩写保留 ----
    ("english_sentence", "HELLO EVERYONE THIS IS A TEST", []),
    ("mixed_zh_acronym_and_word", "我们用 GPU 跑 PYTHON 脚本", []),
    ("contraction_and_acronyms", "I THINK IT'S OK. THE API IS FINE", []),
    ("sentence_start_after_punct", "YES, I DO. I'M FINE! WHY NOT? 好的。HELLO AGAIN", []),
    ("sentence_start_cjk_punct", "我说完了！THANKS A LOT？SURE", []),
    ("i_contractions", "I'M HERE, I'LL GO, I'D SAY I'VE SEEN IT", []),
    ("curly_apostrophe", "I’M SURE IT’S FINE", []),
    ("short_acronyms_kept", "PDF SQL AI UI OK APP CEO", []),
    ("common_short_words", "WE CAN DO IT AND YOU CAN TOO", []),
    ("long_words_lowercased", "我在 KUBERNETES 上部署了 REDIS 和 NGINX", []),
    ("newline_not_sentence_end", "FIRST LINE\nSECOND LINE", []),
    # ---- 夹在中文里的短词 ----
    ("short_common_isolated_in_zh", "我买了 A 股，IT 部门说 NO", []),
    ("short_common_next_to_english", "这个 IT DEPARTMENT 很忙", []),
    ("short_common_no_space_zh", "用A方案还是B方案", []),
    ("isolated_without_zh", "A", []),
    ("single_common_word_english", "YES.", []),
    # ---- 紧贴数字、下划线、拉丁扩展字母：不改 ----
    ("glued_digits", "GPT4 和 PYTHON3，A4 纸，4K 屏幕", []),
    ("glued_underscore", "MY_VAR 和 HELLO_WORLD", []),
    ("glued_latin_extended", "CAFÉ AND NAÏVE", []),
    ("hyphenated", "E-MAIL 和 WI-FI，COVID-19", []),
    # ---- 已有小写字母的词不动 ----
    ("mixed_case_untouched", "iPhone 和 macOS 都很好 USE IT", []),
    ("proper_case_untouched", "Hello World, this is OpenAI.", []),
    # ---- 术语表 ----
    ("glossary_restore", "PYTHON 很好用，我在 GITHUB 上看到 CLAUDE CODE", GLOSSARY),
    ("glossary_multiword_whitespace", "CLAUDE   CODE IS GREAT", GLOSSARY),
    ("glossary_any_case", "python 和 Github 还有 claude code", GLOSSARY),
    ("glossary_longer_first", "CLAUDE CODE 写的 CODE", ["code", "Claude Code"]),
    ("glossary_word_boundary", "PYTHONIC 和 PYTHON3 和 MYPYTHON", ["Python"]),
    ("glossary_not_sentence_capitalized", "PYTHON IS GOOD. GITHUB TOO", ["python", "github"]),
    ("glossary_acronym_lowercased_term", "我们用 GPU", ["gpu"]),
    ("glossary_symbols", "我会 C++ 和 NODE.JS，还有 .NET", ["C++", "Node.js", ".NET"]),
    ("glossary_chinese_only_ignored", "嗯，我们用 SENSEVOICE 识别", ["识别", "  ", " SenseVoice\t"]),
    # 全角空格不算空白：术语里的全角空格按字面匹配
    ("glossary_fullwidth_space_literal", "CLAUDE\u3000CODE 和 CLAUDE CODE", ["Claude\u3000Code"]),
    ("glossary_same_length_first_wins", "GITHUB", ["Github", "GitHub"]),
    # 只做 ASCII 大小写不敏感：开尔文符号 U+212A 不等于 K
    ("glossary_ascii_case_only", "KOTLIN 和 \u212aOTLIN", ["Kotlin"]),
    # ---- 边界 ----
    ("empty", "", []),
    ("punctuation_only", "。，！？...", []),
    ("chinese_only", "嗯，今天天气不错。", GLOSSARY),
    ("non_ascii_letters_untouched", "ΑΒΓ ПРИВЕТ ÉCOLE", []),
    ("already_fixed_idempotent", "Hello everyone. I think it's OK", []),
]


def asr_case(name, text, glossary) -> dict:
    out = textfix.fix_case(text, glossary)
    assert textfix.fix_case(out, glossary) == out, f"{name}：fix_case 不幂等"
    return {"name": name, "input": text, "glossary": glossary, "expected": out}


# ================================================================ 输出


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_all() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    names = [c["name"] for c in METRICS_CASES]
    assert len(names) == len(set(names)), "样例重名"
    for c in METRICS_CASES:
        out[f"metrics/{c['name']}.json"] = _dump(metrics_case(c))
    for name, data in score_files().items():
        out[f"scores/{name}.json"] = _dump(data)
    for name, builder, inp in PROMPT_CASES:
        out[f"prompts/{name}.json"] = _dump(prompt_case(name, builder, inp))
    assert len({c[0] for c in ASR_CASES}) == len(ASR_CASES), "样例重名"
    for c in ASR_CASES:
        out[f"asr/{c[0]}.json"] = _dump(asr_case(*c))
    return out


SUBDIRS = ("metrics", "scores", "prompts", "asr")


def existing() -> dict[str, bytes]:
    return {p.relative_to(CASES).as_posix(): p.read_bytes()
            for sub in SUBDIRS for p in sorted((CASES / sub).glob("*.json"))}


def main() -> int:
    ap = argparse.ArgumentParser(description="从 Python 实现生成 spec/cases 黄金样例")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件；不一致时退出码为 1")
    args = ap.parse_args()
    new, old = build_all(), existing()
    if args.check:
        bad = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
        for k in bad:
            state = "缺少" if k not in old else "多余" if k not in new else "不一致"
            print(f"{state}：spec/cases/{k}")
        print("样例与 Python 实现一致" if not bad else f"共 {len(bad)} 个文件需要重新生成")
        return 1 if bad else 0
    for k in set(old) - set(new):
        (CASES / k).unlink()
    for k, data in new.items():
        path = CASES / k
        path.parent.mkdir(parents=True, exist_ok=True)
        if old.get(k) != data:
            path.write_bytes(data)
    counts = {sub: sum(1 for k in new if k.startswith(sub + "/")) for sub in SUBDIRS}
    n_scores = sum(len(d["cases"]) for d in score_files().values())
    print(f"已生成：指标 {counts['metrics']} 个，评分 {counts['scores']} 个文件 / {n_scores} 条，提示词 {counts['prompts']} 个，"
          f"识别后处理 {counts['asr']} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
