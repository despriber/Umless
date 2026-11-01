"""汇总 results/raw/*.json，生成并排对比报告 results/compare.md。

不需要人工参考稿：只统计各模型输出里的语气词 / 口头禅 / 紧邻重复次数，
配合并排文本直观判断哪个模型更"逐字"。
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
RAW = ROOT / "results" / "raw"
OUT = ROOT / "results" / "compare.md"

# 纯语气词（几乎只作为填充出现）
INTERJECTIONS = ["嗯", "呃", "额", "啊", "哦", "噢", "唉", "诶", "欸", "咦"]
# 口头禅（可能有正常用法，计数只作参考）
DISCOURSE = ["然后", "就是", "那个", "对吧", "的话", "反正", "其实"]
# 紧邻重复：如"这这"、"我觉得我觉得"、"可以吗可以吗"
REPEAT = re.compile(r"([一-鿿]{1,6})\1")
ORDER = ["sensevoice", "sensevoice_itn", "paraformer", "qwen3_asr", "funasr_nano", "funasr_nano_itn"]


def count(text: str) -> dict:
    return {
        "语气词": sum(text.count(w) for w in INTERJECTIONS),
        "口头禅": sum(text.count(w) for w in DISCOURSE),
        "重复": len(REPEAT.findall(text)),
    }


def main() -> None:
    results = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in RAW.glob("*.json")}
    names = [n for n in ORDER if n in results] + sorted(set(results) - set(ORDER))

    lines = ["# ASR 对比报告", "", "## 总览", ""]
    lines.append("| 模型 | 加载(s) | RTF | 内存峰值(MB) | 语气词 | 口头禅 | 重复 |")
    lines.append("|---|---|---|---|---|---|---|")
    for n in names:
        r = results[n]
        total = {"语气词": 0, "口头禅": 0, "重复": 0}
        for row in r["rows"]:
            for k, v in count(row["text"]).items():
                total[k] += v
        lines.append(
            f"| {n} | {r['load_s']} | {r['avg_rtf']} | {r['peak_mem_mb']} "
            f"| {total['语气词']} | {total['口头禅']} | {total['重复']} |"
        )

    lines += ["", "RTF = 转写耗时 / 音频时长，越小越快；CPU 4 线程。", "", "## 逐条对比", ""]
    ids = [row["id"] for row in results[names[0]]["rows"]]
    for i, rid in enumerate(ids):
        dur = results[names[0]]["rows"][i]["duration"]
        lines += [f"### {rid}（{dur}s）", ""]
        for n in names:
            row = results[n]["rows"][i]
            c = count(row["text"])
            lines.append(
                f"- **{n}**（{row['elapsed']}s｜语气词 {c['语气词']} 口头禅 {c['口头禅']} 重复 {c['重复']}）：{row['text']}"
            )
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4 + len(names) + 2]))
    print(f"\n完整报告：{OUT}")


if __name__ == "__main__":
    main()
