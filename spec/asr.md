# 识别结果后处理

> 参考实现：`desktop/umless/textfix.py`（`fix_case`）。常量见 [`constants.json`](constants.json) 的 `asr` 段，黄金样例见 [`cases/asr/`](cases/asr/)。本文与样例冲突时以样例为准，并修正本文。

SenseVoice 的英文词表是全大写，识别结果里的英文全是大写（`我们用 GPU 跑 PYTHON 脚本`、`HELLO EVERYONE THIS IS A TEST`）。开了大模型润色时由润色修正，但没有大模型时输出的就是全大写，历史里的原文也是。`fix_case(text, glossary)` 在识别完成后立即修正大小写。

## 1. 何时调用

- 只处理 **SenseVoice** 的输出：本地引擎 `sensevoice`，以及云端 OpenAI 兼容接口里模型名包含 `sensevoice`（不区分大小写，如默认的 `FunAudioLLM/SenseVoiceSmall`）的情况。Qwen3-ASR 与其他云端模型本身给出正常大小写，不处理。
- 在识别之后、润色与指标计算之前调用：写入历史的 `raw_text` 也是修正后的。语音输入、练习、麦克风测试都经过这一步（两端都在识别引擎 / 识别客户端里统一处理）。
- `glossary` 是当前配置里的术语表（`glossary.terms`），每次识别时读取，改了术语表不必重新加载模型。

## 2. 规则

记号：

- **ASCII 空白**：` \t\n\r\f\v` 六个字符（不含全角空格等）。
- **单词**：正则 `[A-Za-z]+(?:['’][A-Za-z]+)*`，即 ASCII 字母串，中间可以有撇号（`'` 或 `’`）连接，如 `IT'S`、`I’M`。数字、下划线、连字符都不属于单词：`GPT4` 是单词 `GPT` + `4`，`E-MAIL` 是 `E` 和 `MAIL`。
- **全大写**：单词里没有 `a`–`z`。

### 2.1 术语表恢复

1. 术语先去掉首尾 ASCII 空白；不含 ASCII 字母的术语（空串、纯中文）跳过。
2. 按长度（码点数）**从长到短**处理，长度相同保持术语表里的原顺序。
3. 每个术语按 ASCII 空白拆成若干段，段与段之间匹配**任意多个（≥1）ASCII 空白**（`Claude Code` 能匹配 `CLAUDE   CODE`；全角空格不算空白，按字面匹配）；每段按字面匹配，只做 **ASCII 大小写不敏感**，非 ASCII 字符必须完全相同（Python `re.ASCII | re.IGNORECASE`）。
4. 从左到右查找。一个匹配只有同时满足下面两点才替换，否则从**匹配起点 + 1** 处继续查找：
   - 整词：匹配的前一个字符和后一个字符（如果有）都不是 `[A-Za-z0-9]`；
   - 不与之前任何术语替换出的区间重叠（先替换的长术语不会被短术语改掉：`CLAUDE CODE 写的 CODE` + `["code", "Claude Code"]` → `Claude Code 写的 code`）。
5. 替换为术语本身的写法（多段术语之间的空白也变成术语里的写法），替换出的区间记为"术语区间"，从替换结果末尾继续查找。

### 2.2 单词大小写

在 2.1 的结果上，按顺序处理每个单词（下面的"前一个 / 后一个字符"都是在 2.1 的结果里看）：

1. 与术语区间重叠 → 不动（术语写法优先，也不做句首大写：术语 `python` 在句首仍是 `python`）。
2. 不是全大写（含小写字母，如 `iPhone`、`macOS`、`Hello`）→ 不动。
3. **紧贴**（不隔空白）的前一个或后一个字符是 ASCII 数字、`_`、或码点在 U+0080–U+02FF（拉丁扩展字母，如 `É`）→ 不动。这类是型号、标识符或外文单词的一部分：`GPT4`、`PYTHON3`、`A4`、`MY_VAR`、`CAFÉ`。
4. `I` → `I`；以 `I` 加撇号开头（`I'M`、`I'LL`、`I'D`、`I'VE`）→ `I` 后面的部分小写（`I'm`、`I’m`），保留原撇号。
5. 不含撇号、长度 ≤ `asr.short_max`（3）的单词：
   - 小写形式不在 `asr.common_words` 里 → 视为缩写，保留大写（`GPU`、`API`、`AI`、`OK`、`PDF`、`SQL`、`APP`）；
   - 在表里，但**孤立地夹在中文里** → 保留大写（`A 股`、`IT 部门`、`用A方案`）。孤立的判断：分别向左、向右跳过 ASCII 空白找到最近的字符（到头记为无），两侧都不是 ASCII 字母，且至少一侧的码点 ≥ `asr.cjk_min_codepoint`（U+2E80，汉字、全角标点、假名、谚文、emoji 等都在此之上）；
   - 否则按普通单词处理（下一条）。
6. 其余单词（普通单词、含撇号的缩略形式如 `IT'S`）→ 全部小写；如果它在**句首**，首字母改大写。句首：向左跳过 ASCII 空白后到达文本开头，或遇到 `.!?。！？` 之一。换行本身不算句末（`FIRST LINE\nSECOND LINE` → `First line\nsecond line`）。

只有第 6 条会做句首大写；中文、其他文字（希腊、西里尔、带重音的拉丁字母）一律原样保留。文本里没有 ASCII 字母时直接原样返回。

### 2.3 性质

- **幂等**：`fix_case(fix_case(x, g), g) == fix_case(x, g)`（生成样例时逐条断言）。
- 只改 ASCII 字母的大小写，以及术语替换时的空白；不增删其他字符。

## 3. 例子

| 输入 | 术语表 | 输出 |
|---|---|---|
| `HELLO EVERYONE THIS IS A TEST` | | `Hello everyone this is a test` |
| `我们用 GPU 跑 PYTHON 脚本` | | `我们用 GPU 跑 python 脚本` |
| `I THINK IT'S OK. THE API IS FINE` | | `I think it's OK. The API is fine` |
| `PYTHON 很好用，我在 GITHUB 上看到 CLAUDE CODE` | `Python`、`GitHub`、`Claude Code` | `Python 很好用，我在 GitHub 上看到 Claude Code` |
| `我买了 A 股，IT 部门说 NO` | | 不变 |
| `这个 IT DEPARTMENT 很忙` | | `这个 it department 很忙` |
| `GPT4 和 PYTHON3，A4 纸，4K 屏幕` | | 不变 |
| `E-MAIL 和 WI-FI，COVID-19` | | `E-mail 和 WI-FI，covid-19` |

已知局限（没有词典，只靠规则）：人名、地名等专有名词会变成小写（`NEW YORK` → `new york`）；≥ 4 个字母的缩写会变成小写（`NASA` → `nasa`）。需要固定写法的词请加进术语表。

## 4. Kotlin 移植要点

- 常量写在代码里，单元测试与 `constants.json` 的 `asr` 段核对（`common_words` 要求升序、全小写、长度 ≤ `short_max`）。
- 下标可以直接用 UTF-16 下标：所有判断都只看 ASCII 字符、U+0080–U+02FF 与"码点 ≥ U+2E80"，代理项（U+D800–U+DFFF）本身也 ≥ U+2E80，按 `Char` 判断与按码点判断结果相同。唯一要按码点算的是 2.1 第 2 条的术语长度（`codePointCount`）。
- 术语匹配**不要**用正则的忽略大小写：Kotlin 的 `RegexOption.IGNORE_CASE` 会自动加上 `UNICODE_CASE`，Android 的 ICU 正则也总是按 Unicode 折叠（`K` 会匹配开尔文符号 U+212A）。逐字符手写比较即可：ASCII 字母按小写比较，其他字符要求相等；段间空白贪婪地吃掉全部 ASCII 空白（段首字符不是空白，贪婪与正则回溯结果相同）。
- 单词正则 `[A-Za-z]+(?:['’][A-Za-z]+)*` 不带任何标志，两端行为一致。
