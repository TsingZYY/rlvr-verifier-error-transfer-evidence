"""Compare two output trees by relative path and SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree-a", required=True, type=Path)
    parser.add_argument("--tree-b", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    tree_a = args.tree_a.resolve(strict=True)
    tree_b = args.tree_b.resolve(strict=True)
    files_a = inventory(tree_a)
    files_b = inventory(tree_b)
    only_a = sorted(set(files_a) - set(files_b))
    only_b = sorted(set(files_b) - set(files_a))
    mismatches = sorted(
        relative
        for relative in set(files_a) & set(files_b)
        if files_a[relative] != files_b[relative]
    )
    passed = not (only_a or only_b or mismatches)
    report = {
        "schema_version": "p4-r1a-deterministic-tree-comparison-v1",
        "tree_a": tree_a.name,
        "tree_b": tree_b.name,
        "file_count_a": len(files_a),
        "file_count_b": len(files_b),
        "only_in_a": only_a,
        "only_in_b": only_b,
        "sha256_mismatches": mismatches,
        "byte_identical": passed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
