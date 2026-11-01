# 提示词

每个 `.txt` 文件的内容与 `desktop/umless/prompts.py` 里对应的字符串**逐字节相等**：UTF-8、无 BOM、换行为 LF、**末尾没有换行**。读取时按原样使用，不要 `trim()`（`spec/.gitattributes` 强制 LF，`desktop/tests/test_spec.py` 按字节比对）。

| 文件 | Python 常量 | 用途 |
|---|---|---|
| `polish_base.txt` | `POLISH_BASE` | 润色的基本要求与硬约束 |
| `intensity_faithful.txt` / `_moderate` / `_restructure` | `INTENSITY_RULES[...]` | 润色强度 |
| `scene_chat.txt` / `_document` / `_code` / `_email` / `_default` | `SCENE_STYLES[...]` | 场景风格 |
| `scene_search.txt` | （无） | **仅 Android**：搜索框 / 网址栏 |
| `diagnosis_system.txt` / `diagnosis_en.txt` | `DIAGNOSIS_SYSTEM` / `DIAGNOSIS_EN` | 每日诊断 |
| `topics_system.txt` / `topics_en.txt` | `TOPICS_SYSTEM` / `TOPICS_EN` | 练习出题 |
| `practice_system.txt` / `practice_en.txt` | `PRACTICE_SYSTEM` / `PRACTICE_EN` | 练习评测 |

拼装结果的样例见 `spec/cases/prompts/*.json`（`builder` + `input` → 期望的 `messages` 数组）。

## 1. 润色 `build_messages(raw_text, glossary, scene, intensity)`

```
system = join("\n\n", [
    polish_base,
    intensity_<intensity>        // 未知值 → moderate
    scene_<scene>                // 未知值 → default
    "术语表（识别结果中发音相近的词应纠正为这些写法）：" + join("、", glossary)   // 仅 glossary 非空时追加
])
user = "<transcript>\n" + raw_text + "\n</transcript>"
messages = [{role: system, content: system}, {role: user, content: user}]
```

- 润色不受界面语言影响。
- 请求参数：`temperature` 用配置值，`stream: false`，再合并配置里的 `extra_body`。回复里的 `<think>…</think>` 块删掉后 `strip()`。
- **搜索场景（仅 Android）**：输入框类型为搜索 / URI 时 `scene = "search"`，使用 `scene_search.txt`，并且**强制 `intensity = "faithful"`**、不分段。桌面端没有这个场景（Python 收到 `"search"` 会回落到 `default`），所以 `cases/prompts` 里不含搜索场景的样例，Android 端自己补测试。

## 2. 输出语言 `_with_lang(system, lang, en_rule)`

`lang == "en"` 时返回 `system + "\n\n" + en_rule`，其他任何值都原样返回 `system`。诊断、出题、练习评测都用它，分别搭配 `diagnosis_en.txt`、`topics_en.txt`、`practice_en.txt`。

## 3. 每日诊断 `build_diagnosis_messages(date, utterances, stats, lexicon, glossary, active_habits, lang)`

system = `_with_lang(diagnosis_system, lang, diagnosis_en)`；user 由以下各行用 `"\n"` 连接：

```
日期：{date}，共 {n} 条可评分的语音输入。
规则指标：流畅度 {fluency}（满分 100），简洁度 {concision}，每百字口癖 {fillers_per100} 次，重复 {repeat_count} 次，改口 {correction_count} 次。
口癖统计：{w1}×{c1}、{w2}×{c2}…          ← 仅 filler_stats 非空时；最多前 8 个
已知口癖词表：{join("、", lexicon) 或 "（空）"}
术语表：{join("、", glossary) 或 "（空）"}
进行中的习惯：{join("、", active_habits) 或 "（无）"}
                                          ← 空行
<utterances>
1. （{app}）{raw_text}                    ← app 为空串时省略整个"（…）"
2. …
</utterances>
```

- `{n}` = `stats["n"]`，stats 没有这个键时用 `len(utterances)`。
- 花括号里的值按 **Python `str()`** 格式化：float 用最短往返表示且至少带一位小数（`76.0`、`2.95`），int 不带小数点（`fillers_per100` 在没有字数时是整数 `0`），null 写成 `None`，缺失的键也是 `None`。这里的 float 都已取整到 1～2 位小数、范围 0–100，Kotlin 可用 `BigDecimal(x).setScale(2, HALF_EVEN).stripTrailingZeros().toPlainString()`，没有小数部分时补 `.0`；不要依赖 `Double.toString()`（旧版 JDK 对部分值不输出最短表示）。`null` 必须手动输出 `None`。
- `utterances` 是第 [metrics.md 8.1](../metrics.md) 节取样的结果，`stats` 是当天的 `stats_of`，`lexicon` 是启用的口癖词，`active_habits` 是进行中习惯卡的标题（按 id 降序）。
- 请求：`temperature = 0.3`，先带 `response_format: {"type": "json_object"}`，接口返回 400/422 时去掉它重试。

## 4. 练习出题 `build_topics_messages(recent_texts, weak_points, lang)`

system = `_with_lang(topics_system, lang, topics_en)`；user 各行用 `"\n"` 连接：

```
用户最近说过的内容（节选）：
- {text1}                                 ← recent_texts 为空时这里是一行 "（无）"
- {text2}
用户近期需要改进的方面：{join("；", weak_points)}   ← 仅 weak_points 非空时
```

- `recent_texts`：最近 14 天（`created_at >= 今天-14 天 00:00:00`）按 id 降序最多 200 条记录的 `output_text`，`strip()` 后长度 ≥ 15 码点的，按长度降序稳定排序，取前 25 条，每条截前 120 码点。
- `weak_points`：进行中习惯卡标题（id 降序）+ 最近一次诊断的前 3 个问题标题。
- 请求：`temperature = 0.8`，JSON 模式同上。LLM 未配置或失败时用 [`../practice_fallback_topics.json`](../practice_fallback_topics.json)（按界面语言取 `zh` / `en`，随机打乱，时长都是 60 秒）。

## 5. 练习评测 `build_practice_messages(topic, raw_text, duration_s, lang)`

system = `_with_lang(practice_system, lang, practice_en)`；

```
user = "题目：" + topicJson + "\n"
     + "用时：" + fmt0(duration_s) + " 秒（建议 " + str(topic.duration ?: 60) + " 秒）\n\n"
     + "<transcript>\n" + raw_text + "\n</transcript>"
```

- `topicJson`：按 `title, prompt, hint` 的顺序，只取值为真（非空）的键，用 Python `json.dumps(obj, ensure_ascii=False)` 的格式：`{"title": "…", "hint": "…"}`——键值之间 `": "`、项之间 `", "`；没有可用的键时是 `{}`。转义规则：`"` → `\"`，`\` → `\\`，`\n \r \t \b \f` 用短转义，其他 U+0000–U+001F 写成 `\u00XX`（**小写**十六进制）；其余字符（含中文、emoji、U+2028、`/`）原样输出。建议 Kotlin 手写这个小函数，不要依赖 JSON 库的默认转义。
- `fmt0(x)` = Python `f"{x:.0f}"`：HALF_EVEN 且按二进制值判断（`62.5 → "62"`，`63.5 → "64"`，`59.99 → "60"`），见 [metrics.md 0.2](../metrics.md)。
- `topic.duration` 缺失时写 `60`；存在则按 `str()` 输出（通常是 `60` 或 `90`）。
- 请求：`temperature = 0.3`，JSON 模式同上。
