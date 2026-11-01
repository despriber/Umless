"""界面语言测试：中英字典一致、语言解析、提示条文字，以及诊断 / 出题 / 评测 prompt 的输出语言。"""

import string

import pytest

from umless import i18n, prompts
from umless.overlay_render import Frame, _label


@pytest.fixture(autouse=True)
def restore_language():
    yield
    i18n.set_language("zh")


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_dictionaries_have_same_keys_and_placeholders():
    assert set(i18n.ZH) == set(i18n.EN)
    mismatched = [k for k in i18n.ZH if _fields(i18n.ZH[k]) != _fields(i18n.EN[k])]
    assert mismatched == []
    assert all(v.strip() for v in (*i18n.ZH.values(), *i18n.EN.values()))


def test_resolve_and_translate(monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.resolve("auto") == "en" and i18n.resolve("bogus") == "en"
    assert i18n.resolve("zh") == "zh"
    assert i18n.set_language("en") == "en" and i18n.current() == "en"
    assert i18n.t("coach.too_few", n=2, min=3) == "Only 2 scoreable records that day; at least 3 are needed"
    i18n.set_language("zh")
    assert i18n.t("coach.too_few", n=2, min=3) == "这一天可评分的记录只有 2 条，至少需要 3 条"
    assert i18n.t("no.such.key") == "no.such.key"
    assert i18n.system_language() in ("zh", "en")


def test_overlay_labels_follow_language():
    i18n.set_language("en")
    assert _label(Frame("recording")) == "Listening"
    assert _label(Frame("done")) == "Inserted"
    assert _label(Frame("info", text="自定义文字")) == "自定义文字"
    i18n.set_language("zh")
    assert _label(Frame("recording")) == "正在聆听"


# ---- LLM 输出语言 ----

STATS = {"n": 3, "fluency": 80, "concision": 90, "fillers_per100": 1.2, "repeat_count": 0,
         "correction_count": 0, "filler_stats": []}


def test_prompts_unchanged_for_zh_and_extended_for_en():
    cases = [
        (lambda lang: prompts.build_diagnosis_messages("2026-10-12", [{"app": "a", "raw_text": "嗯好"}],
                                                       STATS, ["嗯"], [], [], lang=lang),
         prompts.DIAGNOSIS_SYSTEM, prompts.DIAGNOSIS_EN),
        (lambda lang: prompts.build_topics_messages(["最近在做语音输入"], [], lang=lang),
         prompts.TOPICS_SYSTEM, prompts.TOPICS_EN),
        (lambda lang: prompts.build_practice_messages({"title": "介绍项目"}, "嗯我们", 30, lang=lang),
         prompts.PRACTICE_SYSTEM, prompts.PRACTICE_EN),
    ]
    for build, system, en_rule in cases:
        zh = build("zh")
        assert zh[0]["content"] == system  # 中文界面：与原来完全一致
        en = build("en")
        assert en[0]["content"] == f"{system}\n\n{en_rule}"
        assert en[1] == zh[1]  # 用户消息不变
        assert "English" in en_rule
    # 引用原话的字段明确要求保持原语言
    for field in ("evidence", "original", "better", "personal_fillers", "terms"):
        assert field in prompts.DIAGNOSIS_EN
    assert "clean_text" in prompts.PRACTICE_EN and "better_version" in prompts.PRACTICE_EN


def test_default_lang_is_zh():
    msgs = prompts.build_topics_messages([], [])
    assert msgs[0]["content"] == prompts.TOPICS_SYSTEM
