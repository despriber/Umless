# 规则指标与评分规范

> 参考实现：`desktop/umless/metrics.py`（逐条指标）、`coach.py`（汇总、综合分、诊断取样、习惯卡）、`practice.py`（练习评分）。
> 常量见 [`constants.json`](constants.json)，黄金样例见 [`cases/`](cases/)。本文与样例冲突时以样例为准，并修正本文。

目标是**逐字节一致**：同样的输入，两端算出的计数、标注、分数必须完全相同（浮点数按 IEEE 754 double 比较相等）。下面凡是写"必须"的地方，都是 Kotlin 移植时容易出错、样例专门覆盖的点。

## 0. 通用约定

### 0.1 字符 = Unicode 码点

所有"字""长度""下标"都按**码点**计（Python `str` 的语义），不是 UTF-16 码元。Kotlin 必须先把字符串转成 `IntArray`（`text.codePoints().toArray()`）再处理；直接用 `String.length`、`indexOf`、`substring` 会在扩展区汉字（如 `𠮷` U+20BB7）和 emoji 上出错。样例 `cjk_ext_b_supplementary`、`emoji` 覆盖这一点。

### 0.2 取整：一律 HALF_EVEN，且作用在二进制值上

Python 的 `round(x, n)` 是**银行家舍入**，并且按 double 的**精确二进制值**判断是否"恰好一半"：

| Python | Kotlin 等价写法 | 注意 |
|---|---|---|
| `round(x, n)`（返回 float） | `BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).toDouble()` | 必须用 `BigDecimal(Double)` 构造器（精确值）。**不能**用 `BigDecimal.valueOf(x)`：它先转成最短十进制字符串，`0.425` 会被当成恰好一半 → `0.42`/`0.43` 判断错误（Python 得 `0.42`，因为 0.425 的二进制值略小于 0.425） |
| `round(x)`（返回 int） | `Math.rint(x).toLong()` | `rint` 是就近取偶；**不能**用 `Math.round` / `roundToInt()`（它们是 HALF_UP：`76.5 → 77`，Python 得 `76`） |
| `f"{x:.0f}"`（提示词里的用时） | `BigDecimal(x).setScale(0, HALF_EVEN).toPlainString()` | `String.format("%.0f")` 是 HALF_UP：`62.5` 会得到 `63`，Python 得 `62` |

用到 `round` 的位置：`rewrite_ratio`（3 位）、`cpm`（1 位）、`fluency` / `concision`（1 位）、`overall_score`（1 位）、`aggregate.rewrite_ratio`（3 位）、`filler_stats.per100`（2 位）、`stats_of.fillers_per100`（2 位）、习惯值（规则类 2 位、LLM 类 1 位）、习惯目标（2 位）、习惯进度（2 位）、展示用整数分 `_r`（0 位）、练习各维度与综合分（0 位）。样例 `scores/*` 里名为 `half_*` 的条目专门构造了"十进制恰好一半"的输入，能区分正确与错误的取整实现。

### 0.3 浮点求和：Neumaier 补偿求和

参考实现运行在 CPython ≥ 3.12，内置 `sum()` 对 float 使用 **Neumaier 补偿求和**（3.11 及以前是朴素累加，结果可能差 1 ulp）。凡是下文写 `pySum(...)` 的地方，Kotlin 必须按下面的算法实现（与 CPython `builtin_sum_impl` 一致），不能用 `sumOf` / 朴素 `+=`：

```kotlin
fun pySum(xs: List<Double>): Double {
    if (xs.isEmpty()) return 0.0          // Python 返回 int 0；本规范里空列表都已提前判掉
    var f = xs[0]                         // 0 + x0
    var c = 0.0
    for (k in 1 until xs.size) {
        val x = xs[k]
        val t = f + x
        c += if (abs(f) >= abs(x)) (f - t) + x else (x - t) + f
        f = t
    }
    if (c != 0.0 && c.isFinite()) f += c
    return f
}
```

例：练习权重 `0.25 + 0.15 + 0.20 + 0.15` 朴素累加得 `0.7500000000000001`，`pySum` 得 `0.75`。样例 `overall_score.neumaier_*`、`aggregate.ratio_neumaier_*` 用朴素累加会算错。整数求和（字数、次数）没有这个问题。

### 0.4 运算顺序

浮点表达式必须按参考实现的顺序计算，不要化简：

- 每百字：`c / n * 100`（先除后乘），**不是** `c * 100 / n`。
- 语速：`n / duration_s * 60`。
- 相似度：`2.0 * M / T`。
- 指数：`100 * exp(-k * per100)`，`k` 取 `constants.json` 里的 double 字面量。`exp` 用 `kotlin.math.exp`（即 `Math.exp`）；它与 C 库 `exp` 理论上可能差 1 ulp，若某条样例因此失败，再改用 `StrictMath.exp` 或记录为已知差异。

## 1. 规范化（content chars）

```
normalize(text) -> (norm: IntArray, index: IntArray)
for 每个码点 ch（下标 i，按码点计）:
    if category(ch) 属于 L*（Lu Ll Lt Lm Lo）或 N*（Nd Nl No）:
        norm.append(lower(ch)); index.append(i)
```

- 标点、空白、符号（含 emoji，`So`）、组合附加符号（`Mn`/`Mc`，如分解形式 `e` + U+0301 里的 U+0301）都丢掉。
- Kotlin：`Character.getType(cp)` 属于 `UPPERCASE_LETTER, LOWERCASE_LETTER, TITLECASE_LETTER, MODIFIER_LETTER, OTHER_LETTER, DECIMAL_DIGIT_NUMBER, LETTER_NUMBER, OTHER_NUMBER`。
- `lower(ch)`：**逐码点**的简单小写映射，Kotlin 用 `Character.toLowerCase(cp)`。不能对整串调用 `String.lowercase()`：它会按上下文把词尾 `Σ` 变成 `ς`（Python 逐字处理得 `σ`，见样例 `greek_final_sigma`），默认 locale 还可能是土耳其语。唯一会变长的码点 `İ`（U+0130）在参考实现里取 `"i"`（样例 `turkish_dotted_i`）。
- **不做** NFKC：全角 `Ａ１` 与半角 `A1` 是不同字符（样例 `fullwidth_vs_halfwidth`）；`①`、`½`、`Ⅻ`、`²` 都算有效字符（`No`/`Nl`）。
- Unicode 版本：参考实现是 Python 3.13（Unicode 15.1）。Android 的 `Character` 数据版本可能较旧，只会影响新近分配的码点，不影响常用中英文。

`n = len(raw_norm)` 即"字数"（`chars`）。

## 2. 保留掩码与相似度（difflib 复刻）

`has_polish = polished_text 非 null 且非空串`（Python `bool(polished_text)`；`""` 等同于没有润色稿）。

有润色稿时，对 `a = normalize(raw).norm`、`b = normalize(polished).norm` 运行 Python `difflib.SequenceMatcher(None, a, b, autojunk=False)`，取所有 matching blocks：

- `kept[i] = true` 当且仅当 `i` 落在某个 matching block 内（即 `get_opcodes()` 中 `equal` 段）。
- `M = Σ 块长`，`T = len(a) + len(b)`；`similarity = T > 0 ? 2.0 * M / T : 1.0`。
- 没有润色稿：`kept` 全为 true，`similarity = null`。

因为 `isjunk=None` 且 `autojunk=False`，difflib 没有任何 junk / popular 元素，算法化简为：

```
findLongestMatch(alo, ahi, blo, bhi):
    best = (i=alo, j=blo, size=0)
    prev = 空映射（j -> 以 (i-1, j) 结尾的公共子串长度）
    for i in alo until ahi:                 // i 升序
        cur = 空映射
        for j in blo until bhi:             // j 升序（difflib 遍历 b2j[a[i]]，也是升序）
            if a[i] == b[j]:
                k = prev.getOrDefault(j-1, 0) + 1
                cur[j] = k
                if k > best.size:           // 严格大于：并列时保留先找到的
                    best = (i-k+1, j-k+1, k)
        prev = cur
    return best

matchingBlocks(alo, ahi, blo, bhi):        // 初始调用 (0, la, 0, lb)
    (i, j, k) = findLongestMatch(...)
    if k == 0: return
    if alo < i && blo < j: matchingBlocks(alo, i, blo, j)      // 左侧递归
    输出 (i, j, k)
    if i+k < ahi && j+k < bhi: matchingBlocks(i+k, ahi, j+k, bhi)  // 右侧递归
```

要点：

1. **并列规则**：在所有最长公共子串中，选**结束位置 i 最小**的；结束位置相同时选 **j 最小**的。由于长度相同，等价于 difflib 文档说的"a 中起点最早，其次 b 中起点最早"。实现上就是"`i`、`j` 都升序遍历 + 只在 `k > best` 时更新"。样例 `repeat_tie_second_copy_cut`（`好的好的` vs `好的` → 保留第一份）、`repeat_first_copy_cut`（`我们我们去吃饭` → 更长的匹配在后一份，删的是第一份）、`difflib_tie_earliest_j` 覆盖。
2. **贪心而非 LCS**：先取最长块，再在两侧递归，不是最长公共子序列。`difflib_greedy_not_lcs`、`difflib_reordered`、`correction_bushibushi_difflib` 的结果与 LCS 不同。
3. Python 用 LIFO 队列处理子区间再排序，与上面的中序递归输出顺序一致；difflib 最后的"相邻块合并"不改变 `kept` 和 `M`，可以省略。
4. 因为没有 junk，difflib 里 find_longest_match 末尾的四个"向两侧扩展"循环都不会生效，不用实现。
5. 复杂度 O(la·lb)，几百字的句子没有问题；可用两行 `IntArray(lb+1)` 代替映射。

`rewrite_ratio = similarity == null ? null : round(1 - similarity, 3)`。

边界：raw 为空且润色稿规范化后也为空 → `T = 0` → `similarity = 1.0` → `rewrite_ratio = 0.0`；raw 为空、润色稿有字 → `0.0` → `1.0`；润色稿只有标点（如 `"。"`）仍算"有润色稿"，raw 的字全部视为被删除（样例 `polish_only_punct`、`empty_raw_with_polish`、`empty_both`）。

`removed(a, b)` 定义为 `kept[a until b]` 全为 false。

## 3. 标注数组

`labels[k]`（长度 n，对应规范化后的每个字）初值：`kept[k] ? "keep" : "cut"`。之后依次执行口癖 → 改口 → 重复三步，`taken[k]`（初值 false）记录已被口癖或改口占用的位置。

### 3.1 口癖

1. 词表 `lexicon`：参数为 null 时用 `builtin_fillers`；传入空列表就是"没有口癖词"。
2. **去重**：保留首次出现的顺序（Python `dict.fromkeys`）。
3. **排序**：按**原始词**（未规范化）的码点长度降序，**稳定排序**（等长保持去重后的顺序）。注意是原始长度：`"嗯。"` 长度 2，排在 `"嗯"` 前面（样例 `lexicon_dup_and_punct`：计数记在键 `"嗯。"` 下）。
4. 对每个词 `w`：`wn = normalize(w).norm`；`wn` 为空（如 `"，"`）则跳过。在规范化原文里找出 `wn` 的**所有出现位置**（允许重叠：每次从上一次命中位置 +1 继续找），对每个起点 `i`（升序），`j = i + len(wn)`：
   - 若 `taken[i until j]` 任一为 true → 跳过；
   - 判定 `ok`：有润色稿时 `ok = removed(i, j)`；没有润色稿时 `ok = (wn ∈ interjections)`（`wn` 必须整体等于某个纯语气词，`"嗯嗯"` 不算）；
   - `ok` 时：`fillers[w] += 1`（**键是原始词 `w`**，不是 `wn`），`taken`、`labels` 在 `[i, j)` 置为 true / `"filler"`。
5. 匹配是**子串匹配**，不看词边界：英文词表 `"so"` 会命中 `also` 里的 `so`（只要被删）。样例 `english_lexicon_substring`。
6. 同长度且可能重叠的两个词（如自定义词表里的 `就是` 和 `是说`），结果取决于它们在词表里的先后（样例 `lexicon_same_length_overlap_order(_rev)`）。内置词表里没有这种组合。

`fillers` 是"词 → 次数"映射，只含次数 > 0 的词。比较结果时按映射比较，**键顺序不重要**（但 Kotlin 建议用 `LinkedHashMap` 按首次计数的顺序插入，与 Python 一致）。`filler_count = Σ fillers 的值`。

### 3.2 改口

仅在有润色稿时执行。按 `correction_markers` 的顺序，对每个标记在规范化原文中找所有出现位置（同上，允许重叠），若 `[i, j)` 没有被 `taken` 且 `removed(i, j)`：`corrections += 1`，`taken`、`labels` 置为 true / `"correction"`。

口癖先于改口：`哦不` 里的 `哦` 是语气词，被删时先算作口癖，`哦不` 就因为位置已被占用而不计改口（样例 `correction_oubu_eaten_by_filler`；词表里没有 `哦` 时才计改口，见 `correction_oubu_custom_lexicon`）。

### 3.3 相邻重复

```
repeats = 0; i = 0
while i < n:
    found = 0
    for L in min(8, (n - i) / 2) downTo 1:          // 整除；8 = repeat.max_len
        if raw[i, i+L) != raw[i+L, i+2L): continue
        firstCut = removed(i, i+L); secondCut = removed(i+L, i+2L)
        if has_polish and (firstCut or secondCut): found = L
        elif not has_polish and L >= 2: found = L    // 2 = repeat.min_len_without_polish
        if found: break
    if found:
        repeats += 1
        start = (has_polish and removed(i, i+found)) ? i : i+found
        for k in start until start+found:
            if labels[k] in ("cut", "keep"): labels[k] = "repeat"
        i += 2 * found
    else:
        i += 1
```

- 比较的是规范化后的字（忽略标点空白），所以 `我觉得，我觉得` 也算相邻重复；中间夹了别的字（如 `我觉得嗯我觉得`）就不算（样例 `repeat_separated_by_filler`）。
- 不看 `taken`：口癖本身也可能构成重复（`嗯嗯` 被删 → 口癖 2 次 **且** 重复 1 次，样例 `filler_double_with_repeat`）。
- 长度从长到短试，某个长度两份相等但都没被删时，继续试更短的长度。
- 标注：有润色稿时标被删的那一份（第一份全删就标第一份，否则标第二份）；没有润色稿时标第二份。已经是 `filler` / `correction` 的位置不覆盖。
- 超过 8 字的重复检测不到（样例 `repeat_len10_not_detected`）。
- 没有润色稿时，`研究研究` 这类正常叠词也会被计为重复（已知的误报，样例 `no_polish_reduplication_false_positive`）。

## 4. 分段高亮 `highlights`

```
perChar = 原文每个码点一个标签，初值 "keep"（标点、空白、emoji 等一律 keep）
for k, pos in index: perChar[pos] = labels[k]
按原文顺序把相邻同标签的码点拼成一段 → [[文本, 标签], ...]
```

标签取值：`keep`、`cut`、`filler`、`correction`、`repeat`。空原文 → `[]`。拼接是按码点进行的，emoji、扩展区汉字不会被拆开。

## 5. `analyze()` 输出

| 字段 | 计算 |
|---|---|
| `chars` | `n` |
| `fillers` | 3.1 的映射 |
| `filler_count` | 映射值之和 |
| `repeat_count` | 3.3 的 `repeats` |
| `correction_count` | 3.2 的 `corrections` |
| `rewrite_ratio` | 第 2 节，3 位小数或 null |
| `cpm` | `duration_s` 为 null 或 0 → null；否则 `round(n / duration_s * 60, 1)` |
| `fluency` | `fluency_score(per100(filler_count))` |
| `concision` | `concision_score(per100(repeat_count + correction_count))` |
| `scoreable` | `n >= 15`（`min_scoreable_chars`） |
| `highlights` | 第 4 节 |
| `version` | `metrics_version`（规则变化时 +1，两端同步） |

`per100(c) = n > 0 ? c / n * 100 : 0.0`。空文本的 `fluency`、`concision` 都是 `100.0`。

```
fluency_score(p)   = round(100 * exp(-0.09 * p), 1)     // 0 → 100，3 → 76.3，5 → 63.8，10 → 40.7
concision_score(p) = round(100 * exp(-0.12 * p), 1)     // 0 → 100，2 → 78.7，5 → 54.9
```

## 6. 汇总

### 6.1 `aggregate(rows)`

输入是多条 `analyze` 结果（至少含 `chars, fillers, repeat_count, correction_count, rewrite_ratio, scoreable`）。

1. 只保留 `scoreable == true` 的记录（保持顺序）。
2. `chars = Σ chars`；`fillers` 按记录顺序、每条记录内的键顺序依次累加（插入顺序 = 首次出现顺序）；`filler_count = Σ`；`issues = Σ (repeat_count + correction_count)`。
3. `ratios` = 非 null 的 `rewrite_ratio` 列表。
4. 输出：

| 字段 | 计算 |
|---|---|
| `n` | 记录数 |
| `chars` | 总字数 |
| `fillers` | 合并后的映射 |
| `filler_count` | 总次数 |
| `fluency` | `chars > 0 ? fluency_score(per100(filler_count)) : null` |
| `concision` | `chars > 0 ? concision_score(per100(issues)) : null` |
| `rewrite_ratio` | `ratios` 非空 ? `round(pySum(ratios) / len(ratios), 3)` : null |
| `filler_stats` | 每个词 `{word, count, per100: round(per100(count), 2)}`，按 `count` 降序**稳定排序**（并列保持插入顺序，样例 `filler_stats_tie_order`） |

这里 `per100(c) = chars > 0 ? c / chars * 100 : 0.0`。先汇总计数再算分（按字数加权），不是对每条的分数取平均。

### 6.2 `stats_of(rows)`（教练用的统计）

在 `aggregate` 基础上（没有指标的记录先跳过）追加：`repeat_count`、`correction_count`（仅可评分记录之和）、`fillers_per100 = chars > 0 ? round(filler_count / chars * 100, 2) : 0`。注意 `chars == 0` 时是**整数 0**，会以 `0` 而不是 `0.0` 出现在诊断提示词里（见 `prompts/README.md`）。

## 7. 分数

### 7.1 LLM 分

LLM 给的 1–5 分先经 `_score_1to5`：能被 Python `float()` 解析的值（数字、`true/false` = 1/0、数字字符串，允许首尾空白、`"nan"`、`"inf"`、数字间下划线）→ 夹到 `[1.0, 5.0]`；其他（null、`"abc"`、数组）→ null。`NaN` 夹完是 `1.0`（Python `max(1.0, nan) == 1.0`；Kotlin 的 `coerceIn` / `Math.max` 会得到 NaN，要特殊处理）。

- 诊断：`logic = v * 20`、`clarity = v * 20`（不取整，存 double；Kotlin 用 `v * 20.0` 即与 Python 的 IEEE 双精度乘法结果相同）。`v` 为 null 时维度为 null。
- 练习：`round(v * 20)`（整数，HALF_EVEN）。

### 7.2 综合分 `overall_score(fluency, concision, logic, clarity)`

```
avail = 按 fluency, concision, logic, clarity 的顺序取非 null 的维度
if avail 为空: return null
total = pySum(W[k] for k in avail)
return round(pySum(W[k] * v for k, v in avail) / total, 1)
```

`W = overall_weights = {fluency 0.30, concision 0.20, logic 0.25, clarity 0.25}`。缺维度时按剩余权重重新归一。诊断时 `fluency`、`concision` 取当天 `stats_of` 的值。

### 7.3 练习评分 `practice_scores(llm_scores, fluency, concision)`

```
scores.structure/logic/wording = v = _score_1to5(llm_scores[k]);  v != null ? round(v * 20) : null
scores.fluency   = round(fluency)       // analyze 的 fluency（已是 1 位小数）再取整，76.5 → 76
scores.concision = round(concision)
avail   = 按 structure, logic, wording, fluency, concision 顺序取非 null 的
overall = round(pySum(PW[k] * v for avail) / pySum(PW[k] for avail))   // 整数
```

`PW = practice_weights = {structure 0.25, logic 0.25, wording 0.15, fluency 0.20, concision 0.15}`。练习的 `analyze` 以 LLM 给的 `clean_text` 作为润色稿（为空时等同于没有润色稿），词表用用户当前词表；`cpm` 展示时再 `round()` 成整数。

### 7.4 展示用整数 `_r(x) = x == null ? null : round(x)`

报告、概览、趋势里的分数都经 `_r`（HALF_EVEN）转整数；`delta = _r(今天综合分 - 上次综合分)`（两次都有值时）。

## 8. 教练相关常量与规则

### 8.1 诊断

- 当天可评分记录数 `stats.n < min_diagnosis_samples (3)` 时不诊断。
- **送给 LLM 的逐字稿取样**（`_sample_for_llm`）：只取可评分记录；按 `chars`（规范化字数）**降序稳定排序**；依次取 `text = raw_text` 的前 `diagnosis_text_max (300)` 个码点，若 `已用 + len(text) > diagnosis_budget (8000)` 则**跳过这条继续看下一条**（不是停止）；选中的按 `id` 升序输出 `{app, raw_text: text}`。注意排序用规范化字数，预算用截断后原文的码点数（含标点）。样例 `sample_for_llm.budget_8000`。
- 自动诊断：诊断"昨天"；当天可评分记录数 ≥ `max(配置的 min_utterances, 3)`；失败后间隔 1 小时重试，同一天最多 3 次。

### 8.2 概览里的"节省时间"

`spoken = pySum(duration_s) / 60`，`minutes_spoken = round(spoken, 1)`；`typed = Σ content_len(output_text) / typing_cpm (40)`；`minutes_saved = round(max(0.0, typed - spoken), 1)`（用取整前的 `spoken`）。`content_len` 是去掉 `[\s\W_]` 后的长度，经全码点验证与第 1 节的 L*/N* 判定完全一致（Kotlin 直接数有效字符即可）。

### 8.3 习惯卡

- 类型：`word`（某个口癖词）、`repeat`、`correction` 越低越好；`logic`、`clarity` 越高越好。
- **习惯值** `habit_value(kind, word, end_day)`，窗口为 `[end_day - 6, end_day]` 共 7 天（含两端，按记录的本地日期）：
  - 规则类：取窗口内**可评分**记录，`chars = Σ chars`；为 0 时值为 null。`count` = `Σ fillers[word]`（word）/ `Σ repeat_count` / `Σ correction_count`；值 `round(count / chars * 100, 2)`（次/百字），样本数 = 记录条数。
  - LLM 类：窗口内每天取最新一次诊断的该维度值（0–100，非 null），值 `round(pySum(vals) / len(vals), 1)`，样本数 = 天数。
- **目标** `habit_target(kind, baseline)`：baseline 为 null → 不建卡；越低越好：`baseline < 0.3` 不建卡，否则 `round(baseline / 2, 2)`；越高越好：`baseline >= 90` 不建卡，否则 `min(100.0, baseline + 20)`。baseline 取诊断日期的习惯值。
- 新建：进行中的卡最多 `max_active_habits (3)` 张；同一 key（`word:<词>` 或类型名）已存在（任何状态）则跳过；`word` 类的词不在词表里时先加入词表（来源 llm）并重算指标。
- **达成** `habit_reached`：`reached`（越低越好 `current <= target`，越高越好 `current >= target`）且样本足够（越低越好 ≥ 10 条，越高越好 ≥ 2 次诊断）且创建日期 `<= 今天 - 3 天`（按日期字符串比较，跨月、闰年用真实日历计算）。每次诊断后对所有进行中的卡用"今天"重算。
- **进度** `progress`：baseline、target、current 都非 null 且 baseline ≠ target 时，越低越好 `(base - cur) / (base - target)`，越高越好 `(cur - base) / (target - base)`，否则 0；已达成固定为 1；最后夹到 `[0, 1]` 并 `round(·, 2)`。

### 8.4 LLM 诊断结果清洗（两端须一致）

`summary` 截 600 字；`strengths` 最多 3 条、每条截 200；`issues` 最多 4 条（`title` 截 60，`type` 不在 `filler|repeat|correction|structure|redundancy|wording` 时改为 `wording`，`evidence` 最多 3 条，`advice` 截 300）；`examples` 需有 `original` 和 `better`，最多 4 条；`personal_fillers` 先取前 5 个（每个截 8 字），再去掉已在词表中的；`terms` 需有 `wrong`、`right`，各截 40，`right` 不区分大小写已在术语表中的去掉，最多 5 条；`habits` 的 `kind` 必须合法，`word` 类必须有词，最多 2 个（`title` 截 30，`advice` 截 200）。"截 N 字"均按码点，列表项先 `strip()`、去掉空串再截断和计数。
