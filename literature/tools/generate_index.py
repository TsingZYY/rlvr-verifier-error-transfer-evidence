from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.csv"
OUTPUT = ROOT / "INDEX.md"

CATEGORY_TITLES = {
    "01_core_verifier_error_structure": "1. Verifier 错误结构与 RLVR reward hacking（核心）",
    "02_verifiers_prms_and_benchmarks": "2. Verifier、PRM 与评测基准",
    "03_reward_hacking_and_misspecification_foundations": "3. Reward hacking、Goodhart 与目标错设基础",
    "04_rlvr_generalization_context": "4. RLVR 泛化与方法背景",
}

with MANIFEST.open("r", encoding="utf-8-sig", newline="") as stream:
    rows = list(csv.DictReader(stream))

lines = [
    "# 本地论文索引",
    "",
    "本索引由 `corpus_manifest.csv` 自动生成。每条均包含本地 PDF、原始落地页、版本状态和 SHA-256。",
    "",
]

for category, heading in CATEGORY_TITLES.items():
    subset = sorted(
        (row for row in rows if row["category"] == category),
        key=lambda row: (row["year"], row["title"]),
        reverse=True,
    )
    lines.extend([f"## {heading}", "", f"共 {len(subset)} 篇。", ""])
    for row in subset:
        local = Path(row["local_pdf"])
        relative = local.relative_to("literature") if local.parts[0] == "literature" else local
        status = row["publication_status"]
        venue = row["venue"]
        lines.append(
            f"- **{row['title']}** ({row['year']}; {venue}; {status})  "
            f"[[PDF]]({relative.as_posix()}) "
            f"[[source]]({row['landing_url']})  "
            f"`{row['paper_id']}`"
        )
    lines.append("")

lines.extend(
    [
        "## 完整性记录",
        "",
        "- `corpus_manifest.csv`: 全部元数据、来源 URL、本地路径、文件大小与 SHA-256。",
        "- `validation_report.csv`: 每个 PDF 的存在性、文件头、哈希、页数与解析结果。",
        "- `validation_summary.json`: corpus 级统计。",
        "- `arxiv_candidates.csv`: 六组系统检索得到的 542 篇去重候选，用于追溯未纳入项与后续更新。",
        "",
    ]
)

OUTPUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {OUTPUT} with {len(rows)} papers")
