"""逐条规则指标：口癖、重复、改口、语速、改写幅度，以及原文分段标注。

核心思路：用字符级 diff 对比原文与润色稿，判断原文每个字是否被保留。
口癖词表里的词只有"被润色删掉"才算口癖——避免把"那个文件"的"那个"误判；
重复、改口同理。没有润色稿时退化为只统计纯语气词。
"""

import math
import unicodedata
from difflib import SequenceMatcher

METRICS_VERSION = 1
MIN_SCOREABLE_CHARS = 15  # 太短的句子只统计口癖，不参与评分

# 内置口癖词表：语气词 + 常见口头禅（口头禅在正常语义下也会出现，因此依赖"是否被删"判断）
BUILTIN_FILLERS = [
    "嗯", "呃", "额", "啊", "哦", "噢", "诶", "欸", "唉",
    "然后", "就是", "就是说", "那个", "这个", "其实", "反正", "对吧", "的话",
    "怎么说呢", "所以说", "然后呢", "那么", "基本上", "相当于说",
]
# 纯语气词：没有润色稿时仍可直接计数
INTERJECTIONS = {"嗯", "呃", "额", "哦", "噢", "诶", "欸", "唉"}
CORRECTION_MARKERS = ["不对", "哦不", "我是说", "我的意思是", "应该是", "准确地说", "说错了", "不是不是"]
REPEAT_MAX_LEN = 8  # 相邻重复检测的最长片段
FLUENCY_K = 0.09  # 流畅度衰减系数
CONCISION_K = 0.12  # 简洁度衰减系数


def _is_content(ch: str) -> bool:
    """有效字符：汉字、字母、数字（不含标点、空白、符号）。"""
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N")


def _normalize(text: str) -> tuple[str, list[int]]:
    """去掉标点空白并小写，返回规范化文本及其每个字符在原文中的下标。

    逐字小写且保证一对一：唯一会变长的 "İ"（U+0130 → "i̇"）只取首字符 "i"，与 Kotlin 的 Character.toLowerCase 一致。
    """
    chars, index = [], []
    for i, ch in enumerate(text):
        if _is_content(ch):
            chars.append(ch.lower()[:1])
            index.append(i)
    return "".join(chars), index


def _kept_mask(raw: str, polished: str) -> tuple[list[bool], float]:
    """原文每个字是否在润色稿中保留，以及相似度（0~1）。"""
    sm = SequenceMatcher(None, raw, polished, autojunk=False)
    kept = [False] * len(raw)
    for tag, i1, i2, _, _ in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                kept[i] = True
    return kept, sm.ratio()


def _find_all(text: str, word: str) -> list[int]:
    out, start = [], 0
    while (i := text.find(word, start)) != -1:
        out.append(i)
        start = i + 1
    return out


def analyze(
    raw_text: str,
    polished_text: str | None,
    duration_s: float | None = None,
    lexicon: list[str] | None = None,
) -> dict:
    raw, index = _normalize(raw_text)
    n = len(raw)
    has_polish = bool(polished_text)
    if has_polish:
        kept, similarity = _kept_mask(raw, _normalize(polished_text)[0])
    else:
        kept, similarity = [True] * n, None

    labels = ["keep" if k else "cut" for k in kept]  # 每个规范化字符的标注
    removed = lambda a, b: all(not kept[i] for i in range(a, b))  # noqa: E731

    # ---- 口癖：长词优先匹配，已标注的位置不重复计数 ----
    # 去重保留首次出现的顺序（不用 set：set 的遍历顺序每次运行都可能不同），再按长度稳定排序
    words = sorted(dict.fromkeys(lexicon if lexicon is not None else BUILTIN_FILLERS), key=len, reverse=True)
    fillers: dict[str, int] = {}
    taken = [False] * n
    for w in words:
        wn = _normalize(w)[0]
        if not wn:
            continue
        for i in _find_all(raw, wn):
            j = i + len(wn)
            if any(taken[i:j]):
                continue
            ok = removed(i, j) if has_polish else wn in INTERJECTIONS
            if ok:
                fillers[w] = fillers.get(w, 0) + 1
                for k in range(i, j):
                    taken[k] = True
                    labels[k] = "filler"

    # ---- 改口标记 ----
    corrections = 0
    if has_polish:
        for marker in CORRECTION_MARKERS:
            for i in _find_all(raw, marker):
                j = i + len(marker)
                if not any(taken[i:j]) and removed(i, j):
                    corrections += 1
                    for k in range(i, j):
                        taken[k] = True
                        labels[k] = "correction"

    # ---- 相邻重复：如"我觉得我觉得""这这"；其中一份被删才算 ----
    repeats = 0
    i = 0
    while i < n:
        found = 0
        for length in range(min(REPEAT_MAX_LEN, (n - i) // 2), 0, -1):
            a, b = raw[i : i + length], raw[i + length : i + 2 * length]
            if a != b:
                continue
            first_cut, second_cut = removed(i, i + length), removed(i + length, i + 2 * length)
            if has_polish and (first_cut or second_cut):
                found = length
            elif not has_polish and length >= 2:
                found = length
            if found:
                break
        if found:
            repeats += 1
            # 标注被删掉的那一份（没有润色稿时标注后一份）
            start = i if has_polish and removed(i, i + found) else i + found
            for k in range(start, start + found):
                if labels[k] in ("cut", "keep"):
                    labels[k] = "repeat"
            i += 2 * found
        else:
            i += 1

    filler_count = sum(fillers.values())
    per100 = lambda c: c / n * 100 if n else 0.0  # noqa: E731
    return {
        "chars": n,
        "fillers": fillers,
        "filler_count": filler_count,
        "repeat_count": repeats,
        "correction_count": corrections,
        "rewrite_ratio": round(1 - similarity, 3) if similarity is not None else None,
        "cpm": round(n / duration_s * 60, 1) if duration_s else None,
        "fluency": fluency_score(per100(filler_count)),
        "concision": concision_score(per100(repeats + corrections)),
        "scoreable": n >= MIN_SCOREABLE_CHARS,
        "highlights": _segments(raw_text, index, labels),
        "version": METRICS_VERSION,
    }


def fluency_score(fillers_per100: float) -> float:
    """每百字口癖数 → 流畅度。0 → 100，3 → 76，5 → 64，10 → 41。"""
    return round(100 * math.exp(-FLUENCY_K * fillers_per100), 1)


def concision_score(issues_per100: float) -> float:
    """每百字（重复 + 改口）数 → 简洁度。0 → 100，2 → 79，5 → 55。"""
    return round(100 * math.exp(-CONCISION_K * issues_per100), 1)


def _segments(raw_text: str, index: list[int], labels: list[str]) -> list[list[str]]:
    """把原文切成 [文本, 类型] 段；标点与空白一律视为保留。"""
    per_char = ["keep"] * len(raw_text)
    for k, pos in enumerate(index):
        per_char[pos] = labels[k]
    segs: list[list[str]] = []
    for ch, label in zip(raw_text, per_char):
        if segs and segs[-1][1] == label:
            segs[-1][0] += ch
        else:
            segs.append([ch, label])
    return segs


def aggregate(rows: list[dict]) -> dict:
    """汇总多条记录的指标（按字数加权，先汇总计数再算分）。只统计可评分的记录。"""
    rows = [r for r in rows if r.get("scoreable")]
    chars = sum(r["chars"] for r in rows)
    fillers: dict[str, int] = {}
    for r in rows:
        for w, c in r["fillers"].items():
            fillers[w] = fillers.get(w, 0) + c
    filler_count = sum(fillers.values())
    issues = sum(r["repeat_count"] + r["correction_count"] for r in rows)
    ratios = [r["rewrite_ratio"] for r in rows if r.get("rewrite_ratio") is not None]
    per100 = lambda c: c / chars * 100 if chars else 0.0  # noqa: E731
    return {
        "n": len(rows),
        "chars": chars,
        "fillers": fillers,
        "filler_count": filler_count,
        "fluency": fluency_score(per100(filler_count)) if chars else None,
        "concision": concision_score(per100(issues)) if chars else None,
        "rewrite_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
        "filler_stats": sorted(
            ({"word": w, "count": c, "per100": round(per100(c), 2)} for w, c in fillers.items()),
            key=lambda x: -x["count"],
        ),
    }
