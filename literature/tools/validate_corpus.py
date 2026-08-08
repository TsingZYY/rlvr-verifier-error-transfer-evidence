from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.csv"
REPORT = ROOT / "validation_report.csv"
SUMMARY = ROOT / "validation_summary.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


with MANIFEST.open("r", encoding="utf-8-sig", newline="") as stream:
    rows = list(csv.DictReader(stream))

results: list[dict[str, object]] = []
hash_to_ids: dict[str, list[str]] = defaultdict(list)

for row in rows:
    local_path = Path(row["local_pdf"])
    path = local_path if local_path.is_absolute() else ROOT.parent / local_path
    result: dict[str, object] = {
        "paper_id": row["paper_id"],
        "category": row["category"],
        "local_pdf": row["local_pdf"],
        "exists": path.exists(),
        "pdf_header": False,
        "size_bytes": 0,
        "sha256_matches_manifest": False,
        "page_count": 0,
        "encrypted": False,
        "first_page_text_chars": 0,
        "parse_status": "not-run",
        "error": "",
    }
    try:
        if not path.exists():
            raise FileNotFoundError(path)
        result["size_bytes"] = path.stat().st_size
        with path.open("rb") as stream:
            result["pdf_header"] = stream.read(5) == b"%PDF-"
        actual_hash = sha256(path)
        result["sha256_matches_manifest"] = actual_hash == row["sha256"].upper()
        hash_to_ids[actual_hash].append(row["paper_id"])
        reader = PdfReader(str(path), strict=False)
        result["encrypted"] = bool(reader.is_encrypted)
        result["page_count"] = len(reader.pages)
        first_text = reader.pages[0].extract_text() or "" if reader.pages else ""
        result["first_page_text_chars"] = len(first_text)
        result["parse_status"] = "ok" if reader.pages else "zero-pages"
    except Exception as exc:  # keep the full corpus audit running
        result["parse_status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    results.append(result)

duplicate_groups = [
    {"sha256": digest, "paper_ids": ids}
    for digest, ids in hash_to_ids.items()
    if len(ids) > 1
]

with REPORT.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(results[0]))
    writer.writeheader()
    writer.writerows(results)

category_counts = Counter(str(row["category"]) for row in rows)
summary = {
    "manifest_entries": len(rows),
    "existing_files": sum(bool(item["exists"]) for item in results),
    "valid_pdf_headers": sum(bool(item["pdf_header"]) for item in results),
    "hash_matches": sum(bool(item["sha256_matches_manifest"]) for item in results),
    "parse_ok": sum(item["parse_status"] == "ok" for item in results),
    "zero_page_or_parse_failures": sum(item["parse_status"] != "ok" for item in results),
    "total_pages": sum(int(item["page_count"]) for item in results),
    "total_bytes": sum(int(item["size_bytes"]) for item in results),
    "duplicate_hash_groups": duplicate_groups,
    "category_counts": dict(sorted(category_counts.items())),
}
SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
