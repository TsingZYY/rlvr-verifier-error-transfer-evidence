from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from pypdf import PdfReader


WORKSPACE = Path(__file__).resolve().parents[2]
MANIFEST = WORKSPACE / "literature" / "technical_reports_manifest.csv"
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


parser = argparse.ArgumentParser()
parser.add_argument("report_ids", nargs="+")
parser.add_argument(
    "--terms",
    nargs="+",
    default=["reward hacking", "false positive", "verifier", "generalization"],
)
parser.add_argument("--max-per-report", type=int, default=12)
args = parser.parse_args()

with MANIFEST.open("r", encoding="utf-8-sig", newline="") as stream:
    rows = {row["report_id"]: row for row in csv.DictReader(stream)}

for report_id in args.report_ids:
    row = rows[report_id]
    path = WORKSPACE / row["local_pdf"]
    reader = PdfReader(str(path))
    print(f"\n## {report_id}: {row['title']}")
    found = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = normalize(page.extract_text() or "")
        lowered = text.lower()
        matching = [term for term in args.terms if term.lower() in lowered]
        if not matching:
            continue
        for term in matching:
            index = lowered.find(term.lower())
            start = max(0, index - 220)
            end = min(len(text), index + len(term) + 420)
            snippet = text[start:end]
            print(f"- p.{page_number} [{term}]: {snippet}")
            found += 1
            if found >= args.max_per_report:
                break
        if found >= args.max_per_report:
            break
