from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any


VOLATILE_FIELDS = {
    "created_at_utc",
    "runtime_seconds",
    "peak_gpu_memory_gib",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def substantive(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key not in VOLATILE_FIELDS}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_one(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    require(
        result.get("run_label") == "DIAGNOSTIC_MVP_NOT_FORMAL",
        "wrong run label",
        errors,
    )
    require(result.get("scientific_evidence") is False, "status washing", errors)
    require(result.get("formal_experiment") is False, "formal status washing", errors)
    require(
        result.get("run_status") == "MVP_COMPLETED_DIAGNOSTIC_ONLY",
        "run did not complete diagnostically",
        errors,
    )
    require(result.get("diagnostic_gate_status") == "PASS", "gate did not pass", errors)
    require(not result.get("diagnostic_gate_failures"), "gate failures are nonempty", errors)
    require(result.get("source_row_count") == 14, "source row count is not 14", errors)
    require(result.get("target_row_count") == 7, "target row count is not 7", errors)
    require(
        result.get("candidate_supervised_token_count_range") == 0,
        "candidate token counts are imbalanced",
        errors,
    )
    require(
        result.get("restore_max_abs_target_score_error") == 0.0,
        "LoRA restore is not exact",
        errors,
    )
    updates = list(result.get("source_updates", []))
    cells = list(result.get("evaluation_cells", []))
    require(len(updates) == 5, "source update count is not 5", errors)
    require(len(cells) == 25, "evaluation cell count is not 25", errors)
    identities = {f"Z7_PLUS{offset}" for offset in range(1, 6)}
    require(
        {row.get("source_rule_identity") for row in updates} == identities,
        "source identity coverage is incomplete",
        errors,
    )
    pairs = Counter(
        (row.get("source_rule_identity"), row.get("target_rule_identity"))
        for row in cells
    )
    require(
        set(pairs) == {(source, target) for source in identities for target in identities},
        "ordered identity-pair coverage is incomplete",
        errors,
    )
    require(all(count == 1 for count in pairs.values()), "duplicate identity pair", errors)
    hash_by_source: dict[str, set[str]] = defaultdict(set)
    for row in cells:
        hash_by_source[str(row.get("source_rule_identity"))].add(
            str(row.get("source_update_hash"))
        )
        require(row.get("same_update_reference") is True, "same-update flag false", errors)
        expected_diagonal = row.get("source_rule_identity") == row.get(
            "target_rule_identity"
        )
        require(
            row.get("is_diagonal") is expected_diagonal,
            "diagonal marker mismatch",
            errors,
        )
    require(
        all(len(values) == 1 for values in hash_by_source.values()),
        "a source identity references multiple updates",
        errors,
    )
    update_hashes = {str(row.get("source_update_hash")) for row in updates}
    require(len(update_hashes) == 5, "source update hashes are not unique", errors)
    require(
        update_hashes == {next(iter(values)) for values in hash_by_source.values()},
        "cell hashes disagree with source-update hashes",
        errors,
    )
    require(
        result.get("unique_source_update_hash_count") == 5,
        "reported unique source-update count is not 5",
        errors,
    )

    def finite_walk(value: Any, path: str = "root") -> None:
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"non-finite value at {path}")
        elif isinstance(value, dict):
            for key, child in value.items():
                finite_walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                finite_walk(child, f"{path}[{index}]")

    finite_walk(result)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--addendum", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    left = json.loads(args.a.read_text(encoding="utf-8"))
    right = json.loads(args.b.read_text(encoding="utf-8"))
    addendum = json.loads(args.addendum.read_text(encoding="utf-8"))
    left_substantive = substantive(left)
    right_substantive = substantive(right)
    errors_a = validate_one(left)
    errors_b = validate_one(right)
    byte_identical_substantive = canonical_bytes(left_substantive) == canonical_bytes(
        right_substantive
    )
    errors: list[str] = [
        *(f"A: {message}" for message in errors_a),
        *(f"B: {message}" for message in errors_b),
    ]
    if not byte_identical_substantive:
        errors.append("R2 replicates differ in substantive fields")
    report = {
        "schema_version": "same-source-diagnostic-mvp-validation-r2",
        "validation_status": "PASS" if not errors else "FAIL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "replicate_a_sha256": sha256_file(args.a),
        "replicate_b_sha256": sha256_file(args.b),
        "determinism_addendum_sha256": sha256_file(args.addendum),
        "substantive_sha256": hashlib.sha256(
            canonical_bytes(left_substantive)
        ).hexdigest(),
        "substantive_replicates_identical": byte_identical_substantive,
        "excluded_volatile_fields": sorted(VOLATILE_FIELDS),
        "errors": errors,
        "verified_invariants": [
            "diagnostic-only status",
            "14 source rows and 7 calibration target rows",
            "5 source updates and exact 25/25 ordered identity-pair coverage",
            "one source-update hash reused across all five target identities",
            "five distinct reset-based source updates",
            "balanced candidate supervised-token counts",
            "exact LoRA restoration",
            "finite recorded values",
            "substantive replicate equality",
        ],
        "stack_summary": left["stack_summary"],
        "per_source_diagonal_excess": {
            row["source_rule_identity"]: row["diagonal_excess"]
            for row in left["source_updates"]
        },
        "runtime_seconds": {
            "a": left["runtime_seconds"],
            "b": right["runtime_seconds"],
        },
        "peak_gpu_memory_gib": {
            "a": left["peak_gpu_memory_gib"],
            "b": right["peak_gpu_memory_gib"],
        },
        "claim_boundary": addendum.get("scientific_evidence") is False,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
