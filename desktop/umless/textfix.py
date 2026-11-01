"""识别结果后处理：修正 SenseVoice 输出的全大写英文（"HELLO WORLD" → "Hello world"）。

规则见 spec/asr.md，黄金样例见 spec/cases/asr/。只处理 ASCII 字母，中文与其他文字原样保留。
"""

import re

# 常见英文短词（≤3 个字母、小写）：全大写时按单词处理改成小写；不在表里的短词视为缩写保留大写
COMMON_WORDS = [
    "a", "ago", "ah", "all", "am", "an", "and", "any", "are", "as", "ask", "at",
    "bad", "be", "big", "but", "buy", "by", "can", "day", "did", "do", "few", "for",
    "get", "go", "got", "had", "has", "he", "her", "hey", "hi", "him", "his", "how",
    "if", "in", "is", "it", "its", "let", "lot", "man", "may", "me", "my", "new",
    "no", "nor", "not", "now", "of", "off", "oh", "old", "on", "one", "or", "our",
    "out", "own", "put", "run", "say", "see", "set", "she", "so", "the", "to", "too",
    "try", "two", "up", "us", "use", "was", "way", "we", "who", "why", "yes", "yet", "you",
]
_COMMON = frozenset(COMMON_WORDS)
SHORT_MAX = 3  # 不超过这个长度的全大写词才可能是缩写
CJK_MIN = 0x2E80  # 码点 ≥ 此值视为中文语境（汉字、全角标点、假名、谚文、emoji 等）

_WS = " \t\n\r\f\v"  # 只认 ASCII 空白
_WS_RUN = r"[ \t\n\r\f\v]+"
_SENTENCE_END = ".!?。！？"
_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


def _is_ascii_letter(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def _is_ascii_alnum(ch: str) -> bool:
    return _is_ascii_letter(ch) or "0" <= ch <= "9"


def _glued(ch: str) -> bool:
    """紧贴单词的字符是数字、下划线或拉丁扩展字母：说明是型号 / 标识符 / 外文单词的一部分，不改大小写。"""
    return "0" <= ch <= "9" or ch == "_" or 0x80 <= ord(ch) <= 0x2FF


def _term_pattern(term: str) -> re.Pattern:
    # 多词术语：词与词之间可以是任意多个 ASCII 空白；只做 ASCII 大小写不敏感
    return re.compile(_WS_RUN.join(map(re.escape, re.split(_WS_RUN, term))), re.ASCII | re.IGNORECASE)


def _apply_glossary(text: str, glossary: list[str]) -> tuple[str, list[tuple[int, int]]]:
    """按术语表恢复大小写，返回新文本和被替换的区间（左闭右开）。"""
    terms = [g.strip(_WS) for g in glossary]
    terms = [g for g in terms if any(_is_ascii_letter(c) for c in g)]
    terms.sort(key=len, reverse=True)  # 长词优先；同长度保持原顺序
    spans: list[tuple[int, int]] = []
    for term in terms:
        pat = _term_pattern(term)
        pos = 0
        while (m := pat.search(text, pos)) is not None:
            s, e = m.span()
            ok = (s == 0 or not _is_ascii_alnum(text[s - 1])) and (e == len(text) or not _is_ascii_alnum(text[e]))
            if not ok or any(s < pe and ps < e for ps, pe in spans):
                pos = s + 1
                continue
            delta = len(term) - (e - s)
            text = text[:s] + term + text[e:]
            spans = [(ps + delta, pe + delta) if ps >= e else (ps, pe) for ps, pe in spans]
            spans.append((s, s + len(term)))
            pos = s + len(term)
    return text, spans


def _neighbor(text: str, i: int, step: int) -> str:
    """从下标 i 起按 step 方向跳过空白后的第一个字符；到头返回空串。"""
    while 0 <= i < len(text) and text[i] in _WS:
        i += step
    return text[i] if 0 <= i < len(text) else ""


def _in_cjk(text: str, s: int, e: int) -> bool:
    """单词孤立地夹在中文里（如"A 股""IT 部门"）：两侧都不是英文单词，且至少一侧是中文语境字符。"""
    left, right = _neighbor(text, s - 1, -1), _neighbor(text, e, 1)
    if (left and _is_ascii_letter(left)) or (right and _is_ascii_letter(right)):
        return False
    return any(c and ord(c) >= CJK_MIN for c in (left, right))


def _sentence_start(text: str, s: int) -> bool:
    prev = _neighbor(text, s - 1, -1)
    return prev == "" or prev in _SENTENCE_END


def _fix_word(text: str, s: int, e: int) -> str:
    w = text[s:e]
    if any("a" <= c <= "z" for c in w):
        return w  # 已有小写字母：模型或用户给出的大小写，不动
    if (s > 0 and _glued(text[s - 1])) or (e < len(text) and _glued(text[e])):
        return w
    if w == "I" or (w[0] == "I" and w[1] in "'’"):
        return "I" + w[1:].lower()  # I、I'm、I'll …
    plain = "'" not in w and "’" not in w
    if plain and len(w) <= SHORT_MAX and (w.lower() not in _COMMON or _in_cjk(text, s, e)):
        return w  # 缩写（GPU、API、OK），或夹在中文里的单个短词
    w = w.lower()
    return w[0].upper() + w[1:] if _sentence_start(text, s) else w


def fix_case(text: str, glossary: list[str]) -> str:
    """修正全大写英文：先按术语表恢复写法，再把普通单词改成小写、句首大写，缩写保留大写。"""
    if not any(_is_ascii_letter(c) for c in text):
        return text
    text, spans = _apply_glossary(text, glossary)
    out, last = [], 0
    for m in _TOKEN.finditer(text):
        s, e = m.span()
        if any(s < pe and ps < e for ps, pe in spans):
            continue  # 术语表替换出来的词
        out.append(text[last:s])
        out.append(_fix_word(text, s, e))
        last = e
    out.append(text[last:])
    return "".join(out)
