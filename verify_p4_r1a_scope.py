"""Verify that a P4-R1A staging tree only adds the authorized files.

The parent archive used Windows separators for most member names.  This
checker normalizes those member names only for comparison, then compares the
original member bytes with the current staging tree.  Generated output,
return, and cache directories are outside the source-scope comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


AUTHORIZED_ADDITIONS = {
    "src/sqlite_agent_research/arithmetic_p4_r1_preflight.py",
    "scripts/run_p4_r1_cpu_preflight.py",
    "protocols/p4_r1_cpu_preflight_contract_v1.json",
    "tests/test_arithmetic_p4_r1_preflight.py",
    "tests/fixtures/p4_r1_contract_only_v1/CONTRACT.json",
    "tests/fixtures/p4_r1_contract_only_v1/MAPPING_STACKS.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/SOURCE_BUNDLES.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/TARGET_CALIBRATION.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/TARGET_AUDIT.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/P3_RULE_TEST_ONLY.json",
    "tests/fixtures/p4_r1_contract_only_v1/INPUT_SHA256SUMS.txt",
}

IGNORED_PREFIXES = (
    "outputs/",
    "returns/",
    ".pytest_cache/",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def normalize_member(name: str) -> str:
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe parent member: {name!r}")
    return PurePosixPath(*parts).as_posix()


def should_ignore(relative: str) -> bool:
    return (
        relative.startswith(IGNORED_PREFIXES)
        or "/__pycache__/" in f"/{relative}/"
        or relative.endswith(".pyc")
    )


def compare(parent_zip: Path, staging_root: Path) -> dict[str, object]:
    parent: dict[str, bytes] = {}
    duplicate_normalized: list[str] = []
    with zipfile.ZipFile(parent_zip, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            normalized = normalize_member(info.filename)
            if normalized in parent:
                duplicate_normalized.append(normalized)
                continue
            parent[normalized] = archive.read(info)

    current: dict[str, bytes] = {}
    for path in sorted(staging_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(staging_root).as_posix()
        if should_ignore(relative):
            continue
        current[relative] = path.read_bytes()

    missing_parent = sorted(set(parent) - set(current))
    modified_parent = sorted(
        relative
        for relative in set(parent) & set(current)
        if parent[relative] != current[relative]
    )
    additions = sorted(set(current) - set(parent))
    unauthorized_additions = sorted(set(additions) - AUTHORIZED_ADDITIONS)
    missing_authorized_additions = sorted(AUTHORIZED_ADDITIONS - set(additions))

    return {
        "schema_version": "p4-r1a-scope-audit-v1",
        "parent_archive": {
            "name": parent_zip.name,
            "sha256": sha256_bytes(parent_zip.read_bytes()),
            "entries": len(parent),
            "duplicate_normalized_members": duplicate_normalized,
        },
        "staging_root": staging_root.name,
        "parent_files_missing": missing_parent,
        "existing_files_modified": modified_parent,
        "authorized_additions_observed": sorted(
            set(additions) & AUTHORIZED_ADDITIONS
        ),
        "unauthorized_additions": unauthorized_additions,
        "authorized_additions_missing": missing_authorized_additions,
        "passed": not (
            duplicate_normalized
            or missing_parent
            or modified_parent
            or unauthorized_additions
            or missing_authorized_additions
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-zip", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = compare(
        args.parent_zip.resolve(strict=True),
        args.staging_root.resolve(strict=True),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
