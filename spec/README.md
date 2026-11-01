# Umless 共享规范（spec）

桌面端（Python）和 Android 端（Kotlin）是两套独立实现。为了让同样的逐字稿在两端算出**完全相同**的口癖计数、高亮、分数，识别结果的后处理（英文大小写修正）一致，并且发给 LLM 的提示词一字不差，两端共同遵守本目录。

## 规则

> **spec 是事实来源。** 改规则时：先改 spec（文档、常量、提示词）→ 再改两端实现 → 重新生成样例 → 两端测试都通过，才算完成。

- 不允许只改一端。规则变化影响已有指标时，`constants.json` 的 `metrics_version` +1（两端据此重算历史数据）。
- 样例的期望值由桌面端 Python 实现生成（它是参考实现），不要手改 `cases/` 下的 JSON。
- 文本文件一律 UTF-8、LF（见 `.gitattributes`）。

## 内容

| 路径 | 内容 |
|---|---|
| [`metrics.md`](metrics.md) | 规则指标与评分的精确算法：字符规范化、difflib 复刻、口癖 / 改口 / 重复判定、分段高亮、汇总、综合分、练习分、习惯卡、取整与求和规则 |
| [`asr.md`](asr.md) | 识别结果后处理：SenseVoice 全大写英文的大小写修正（术语表恢复、缩写保留、句首大写） |
| [`update.md`](update.md) | 应用更新（清单、版本、安装）；第 2 节「下载源」（GitHub 直连 / 镜像、测试连接）同时适用于识别模型下载 |
| [`constants.json`](constants.json) | 内置口癖词表、纯语气词、改口标记、权重、阈值、公式系数、指标版本；`asr` 段为大小写修正用的常见短词表等 |
| [`prompts/`](prompts/) | 所有 LLM 提示词（纯文本），以及消息拼装规则 [`prompts/README.md`](prompts/README.md) |
| [`practice_fallback_topics.json`](practice_fallback_topics.json) | 没有 LLM 时的内置练习题（中 / 英） |
| `cases/metrics/*.json` | `analyze()` 黄金样例：`{name, raw, polished, duration_s, lexicon, expected}`，`polished` / `duration_s` 可为 null，`lexicon` 为 null 表示用内置词表 |
| `cases/scores/*.json` | 每个文件一种函数：`{kind, cases: [{name, input, expected}]}`，覆盖流畅度、简洁度、综合分、展示取整、练习分、汇总、诊断取样、习惯卡等，含专门的 .5 取整边界 |
| `cases/prompts/*.json` | 消息拼装样例：`{name, builder, input, expected}`，`expected` 是完整的 messages 数组 |
| `cases/asr/*.json` | `fix_case()` 黄金样例：`{name, input, glossary, expected}`，`glossary` 为术语表（可为空列表） |
| [`tools/gen_metrics_cases.py`](tools/gen_metrics_cases.py) | 从 Python 实现生成全部样例；`--check` 只比对 |

## 各端如何使用

**桌面端（Python）**：常量和提示词仍写在 `desktop/umless/*.py` 里（安装包不依赖仓库根目录的文件）。`desktop/tests/test_spec.py` 保证：

1. `prompts/*.txt` 与 Python 字符串逐字节相等；
2. `constants.json`、`practice_fallback_topics.json` 与 Python 常量相等（写在函数体里的阈值通过行为核对）；
3. `cases/` 下全部样例在 Python 实现上通过。

```bash
cd desktop
uv run pytest tests -q -p no:cacheprovider                 # 含 spec 一致性测试
uv run python ../spec/tools/gen_metrics_cases.py --check   # 样例是否需要重新生成
uv run python ../spec/tools/gen_metrics_cases.py           # 改了规则或新增样例后重新生成
```

**Android 端（Kotlin）**：构建时把 `spec/` 复制进来（git submodule 或同步脚本），`prompts/*.txt`、`constants.json`、`practice_fallback_topics.json` 打进 assets 运行时读取；单元测试遍历 `cases/` 下全部 JSON，逐条比对。比较规则：

- 整数、字符串、布尔、null 严格相等；浮点按 double 严格相等（不设容差）；
- `fillers` 等映射按键值比较，不要求键顺序；列表（如 `highlights`、`filler_stats`）要求顺序一致；
- 实现细节务必读 `metrics.md` 第 0 节（码点、HALF_EVEN、Neumaier 求和、运算顺序）。

`prompts/scene_search.txt` 只有 Android 使用（输入框为搜索 / URI 时：只用忠实档、不分段），桌面端没有对应场景，见 `prompts/README.md`。

## 新增样例

在 `tools/gen_metrics_cases.py` 里添加输入（用通用的句子，**不要使用任何个人数据**），运行生成脚本，确认 diff 合理后连同脚本一起提交，然后在 Android 端跑一遍。
