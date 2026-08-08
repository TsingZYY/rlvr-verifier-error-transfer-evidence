from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader


WORKSPACE = Path(__file__).resolve().parents[2]
LITERATURE = WORKSPACE / "literature"
MANIFEST = LITERATURE / "technical_reports_manifest.csv"
REPORT = LITERATURE / "technical_reports_validation.csv"
SUMMARY = LITERATURE / "technical_reports_validation_summary.json"
KEYWORDS = LITERATURE / "technical_reports_keyword_audit.csv"

TERMS = {
    "verifier": "verifier",
    "verifiable_reward": "verifiable reward",
    "reward_hacking": "reward hacking",
    "false_positive": "false positive",
    "false_negative": "false negative",
    "generalization": "generalization",
    "cross_task": "cross-task",
    "grader": "grader",
    "unit_test": "unit test",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


with MANIFEST.open("r", encoding="utf-8-sig", newline="") as stream:
    rows = list(csv.DictReader(stream))

validation_rows: list[dict[str, object]] = []
keyword_rows: list[dict[str, object]] = []
for row in rows:
    path = WORKSPACE / row["local_pdf"]
    exists = path.is_file()
    header_ok = exists and path.read_bytes()[:5] == b"%PDF-"
    actual_hash = sha256(path) if exists else ""
    hash_ok = bool(actual_hash) and actual_hash == row["sha256"]
    pages = 0
    parse_ok = False
    error = ""
    text = ""
    if exists and header_ok:
        try:
            reader = PdfReader(str(path))
            pages = len(reader.pages)
            parse_ok = pages > 0
            text = "\n".join((page.extract_text() or "") for page in reader.pages).lower()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    validation_rows.append(
        {
            "report_id": row["report_id"],
            "company": row["company"],
            "local_pdf": row["local_pdf"],
            "exists": exists,
            "pdf_header_ok": header_ok,
            "hash_ok": hash_ok,
            "pages": pages,
            "parse_ok": parse_ok,
            "error": error,
        }
    )
    keyword_row: dict[str, object] = {
        "report_id": row["report_id"],
        "company": row["company"],
        "title": row["title"],
        "pages": pages,
    }
    for column, term in TERMS.items():
        keyword_row[column] = text.count(term)
    keyword_rows.append(keyword_row)

with REPORT.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(validation_rows[0]))
    writer.writeheader()
    writer.writerows(validation_rows)

with KEYWORDS.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(keyword_rows[0]))
    writer.writeheader()
    writer.writerows(keyword_rows)

summary = {
    "manifest_entries": len(rows),
    "existing_files": sum(bool(row["exists"]) for row in validation_rows),
    "valid_pdf_headers": sum(bool(row["pdf_header_ok"]) for row in validation_rows),
    "hash_matches": sum(bool(row["hash_ok"]) for row in validation_rows),
    "parse_ok": sum(bool(row["parse_ok"]) for row in validation_rows),
    "total_pages": sum(int(row["pages"]) for row in validation_rows),
    "total_bytes": sum(int(row["bytes"]) for row in rows),
    "priority_counts": {
        priority: sum(row["priority"] == priority for row in rows)
        for priority in sorted({row["priority"] for row in rows})
    },
    "company_counts": {
        company: sum(row["company"] == company for row in rows)
        for company in sorted({row["company"] for row in rows})
    },
}
SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
