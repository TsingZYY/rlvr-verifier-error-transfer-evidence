from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA_VERSION = "r13-matched-target-panels-r1"
FORMAL_DIR = Path(__file__).resolve().parent
PROTOCOL_PATH = FORMAL_DIR / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
SOURCE_ROOT = FORMAL_DIR.parent / "real_assets" / "build_v5_repair_a"
SOURCE_PANEL_PATH = SOURCE_ROOT / "TARGET_CALIBRATION_REAL_V1.jsonl"
DEFAULT_OUTPUT_ROOT = FORMAL_DIR / "r13_assets_r1"

STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))

H1_CODEBOOKS = {
    "A_TO_B": {
        "codebook_id": "r13_h1_target_B_a5_b1",
        "formula": "K((5*z+1) mod 7)",
        "multiplier_mod7": 5,
        "intercept_mod7": 1,
        "task_slot": "B",
    },
    "B_TO_A": {
        "codebook_id": "r13_h1_target_A_a3_b0",
        "formula": "K((3*z+0) mod 7)",
        "multiplier_mod7": 3,
        "intercept_mod7": 0,
        "task_slot": "A",
    },
}

ALLOWED_ROW_DIFF_PATHS = (
    "candidate_records[*].is_gold",
    "candidate_records[*].is_shared_wrong",
    "candidate_records[*].is_task_local_wrong",
    "codebook.codebook_id",
    "codebook.formula",
    "codebook.intercept_mod7",
    "codebook.latent_to_candidate",
    "codebook.multiplier_mod7",
    "gold_candidate",
    "lineage_id",
    "local_bug_candidate",
    "prompt_text",
    "row_id",
    "shared_bug_candidate",
)


class AssetError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssetError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(token: str) -> Any:
    raise AssetError(f"non-finite JSON constant: {token}")


def strict_json_loads(data: bytes | str) -> Any:
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    try:
        return json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetError(f"invalid strict JSON: {exc}") from exc


def canonical_json_bytes(value: Any, *, newline: bool = False) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return payload + (b"\n" if newline else b"")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def safe_relpath(root: Path, relpath: str) -> Path:
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relpath:
        raise AssetError(f"unsafe POSIX relative path: {relpath}")
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*pure.parts).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AssetError(f"path escapes root: {relpath}") from exc
    return candidate


def read_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            raise AssetError(f"blank JSONL line at {path}:{line_number}")
        value = strict_json_loads(line)
        if not isinstance(value, dict):
            raise AssetError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


def parse_affine(formula: str) -> tuple[int, int]:
    match = re.fullmatch(r"K\(\((\d+)\*z\+(\d+)\) mod 7\)", formula)
    if match is None:
        raise AssetError(f"non-canonical affine formula: {formula}")
    return int(match.group(1)), int(match.group(2))


def latent_to_candidates(multiplier: int, intercept: int) -> list[str]:
    if multiplier % 7 == 0:
        raise AssetError("target multiplier must be invertible modulo 7")
    labels = [f"FINAL=K{(multiplier * z + intercept) % 7}" for z in range(7)]
    if set(labels) != set(CANDIDATES) or len(labels) != len(set(labels)):
        raise AssetError("codebook is not a K0..K6 bijection")
    return labels


def codebook_line(codebook: dict[str, Any]) -> bytes:
    labels = codebook["latent_to_candidate"]
    return ("CODEBOOK=" + ";".join(f"z{z}->{label.removeprefix('FINAL=')}" for z, label in enumerate(labels))).encode("ascii")


def switch_prompt_codebook(prompt: bytes, codebook: dict[str, Any]) -> bytes:
    if b"\r" in prompt:
        raise AssetError("source prompt is not LF-normalized")
    lines = prompt.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if line.startswith(b"CODEBOOK=")]
    if len(positions) != 1:
        raise AssetError("source prompt must contain exactly one CODEBOOK line")
    index = positions[0]
    ending = b"\n" if lines[index].endswith(b"\n") else b""
    lines[index] = codebook_line(codebook) + ending
    return b"".join(lines)


def _candidate_for_z(codebook: dict[str, Any], z: int) -> str:
    return codebook["latent_to_candidate"][z % 7]


def _derive_h1_row(
    source_row: dict[str, Any],
    source_row_sha256: str,
    source_prompt_sha256: str,
    codebook: dict[str, Any],
    prompt: bytes,
) -> dict[str, Any]:
    row = copy.deepcopy(source_row)
    z = int(row["canonical_z"])
    row["codebook"] = copy.deepcopy(codebook)
    row["gold_candidate"] = _candidate_for_z(codebook, z)
    row["shared_bug_candidate"] = _candidate_for_z(
        codebook, int(row["shared_bug_z"])
    )
    row["local_bug_candidate"] = _candidate_for_z(
        codebook, int(row["local_bug_z"])
    )
    for record in row["candidate_records"]:
        candidate = record["candidate"]
        record["is_gold"] = candidate == row["gold_candidate"]
        record["is_shared_wrong"] = candidate == row["shared_bug_candidate"]
        record["is_task_local_wrong"] = candidate == row["local_bug_candidate"]
    row["prompt_text"] = prompt.decode("ascii")

    commitment = {
        "schema_version": SCHEMA_VERSION,
        "arm": "H1_SWITCHED_TARGET",
        "source_lineage_id": source_row["lineage_id"],
        "source_raw_row_sha256": source_row_sha256,
        "source_raw_prompt_sha256": source_prompt_sha256,
        "target_codebook": codebook,
    }
    lineage = sha256_bytes(canonical_json_bytes(commitment))
    row["lineage_id"] = lineage
    row["row_id"] = (
        f"r13-h1-{row['mapping_stack_id']}-z{z}-{lineage[:12]}"
    )
    return row


def _assert_candidate_contract(row: dict[str, Any]) -> None:
    if tuple(row.get("candidate_order", ())) != CANDIDATES:
        raise AssetError(f"candidate_order mismatch in {row.get('row_id')}")
    records = row.get("candidate_records")
    if not isinstance(records, list) or len(records) != 7:
        raise AssetError(f"candidate_records count mismatch in {row.get('row_id')}")
    observed = [record.get("candidate") for record in records]
    if tuple(observed) != CANDIDATES or len(set(observed)) != 7:
        raise AssetError(f"candidate_records are not the ordered K0..K6 bijection")
    for index, record in enumerate(records):
        if record.get("candidate_index") != index:
            raise AssetError(f"candidate_index mismatch in {row.get('row_id')}")
    codebook = row.get("codebook", {})
    a, b = parse_affine(codebook.get("formula", ""))
    if a != codebook.get("multiplier_mod7") or b != codebook.get("intercept_mod7"):
        raise AssetError(f"codebook formula/coefficients mismatch in {row.get('row_id')}")
    expected = latent_to_candidates(a, b)
    if codebook.get("latent_to_candidate") != expected:
        raise AssetError(f"latent_to_candidate mismatch in {row.get('row_id')}")


def _source_row_without_byte_commitments(record: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result.pop("prompt_bytes", None)
    result.pop("row_bytes", None)
    return result


def _q_contract(protocol: dict[str, Any], direction: str, arm: str) -> tuple[int, dict[str, int]]:
    intervention = protocol["target_alignment_intervention"]
    arm_entry = intervention[arm][direction]
    source_a, _ = parse_affine(arm_entry["source_codebook"])
    target_a, _ = parse_affine(arm_entry["target_codebook"])
    factor = (pow(target_a, -1, 7) * source_a) % 7
    expected = {str(r): (factor * r) % 7 for r in range(1, 6)}
    recorded = {str(k): int(v) for k, v in arm_entry["q_surface_by_r"].items()}
    if factor != arm_entry["surface_factor"] or expected != recorded:
        raise AssetError(f"protocol q contract mismatch for {arm}/{direction}")
    return factor, expected


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise AssetError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def generate(output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise AssetError(f"output directory must be absent or empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    protocol_bytes = PROTOCOL_PATH.read_bytes()
    protocol = strict_json_loads(protocol_bytes)
    if protocol["fixed_source_stacks"]["ordered_stack_ids"] != list(STACKS):
        raise AssetError("protocol stack order drift")
    if protocol["target_alignment_intervention"]["ordered_arms"] != list(ARMS):
        raise AssetError("protocol arm order drift")
    if protocol["run_eligible"] or protocol["model_execution_authorized"]:
        raise AssetError("R13 draft unexpectedly authorizes a run")

    source_panel_bytes = SOURCE_PANEL_PATH.read_bytes()
    source_records = read_jsonl_strict(SOURCE_PANEL_PATH)
    by_stack: dict[str, list[dict[str, Any]]] = {}
    for stack in STACKS:
        rows = [row for row in source_records if row.get("mapping_stack_id") == stack]
        rows.sort(key=lambda row: int(row["canonical_z"]))
        if [row["canonical_z"] for row in rows] != list(range(7)):
            raise AssetError(f"source panel coverage/order mismatch for {stack}")
        by_stack[stack] = rows

    panel_records: list[dict[str, Any]] = []
    raw_inventory: list[dict[str, Any]] = []
    seen_row_ids: set[str] = set()
    seen_output_paths: set[str] = set()

    for stack_index, stack in enumerate(STACKS):
        direction = "A_TO_B" if stack.endswith("A_TO_B") else "B_TO_A"
        h0_factor, h0_q = _q_contract(protocol, direction, ARMS[0])
        h1_factor, h1_q = _q_contract(protocol, direction, ARMS[1])
        for arm_index, arm in enumerate(ARMS):
            factor, q_by_r = (h0_factor, h0_q) if arm_index == 0 else (h1_factor, h1_q)
            for z, source_record in enumerate(by_stack[stack]):
                prompt_meta = source_record["prompt_bytes"]
                row_meta = source_record["row_bytes"]
                source_prompt_path = safe_relpath(SOURCE_ROOT, prompt_meta["raw_relpath"])
                source_row_path = safe_relpath(SOURCE_ROOT, row_meta["raw_relpath"])
                source_prompt_bytes = source_prompt_path.read_bytes()
                source_row_bytes = source_row_path.read_bytes()
                if sha256_bytes(source_prompt_bytes) != prompt_meta["sha256"]:
                    raise AssetError(f"source prompt hash mismatch: {source_prompt_path}")
                if sha256_bytes(source_row_bytes) != row_meta["sha256"]:
                    raise AssetError(f"source row hash mismatch: {source_row_path}")
                source_raw_row = strict_json_loads(source_row_bytes)
                if source_raw_row != _source_row_without_byte_commitments(source_record):
                    raise AssetError(f"source raw row/JSONL mismatch: {source_row_path}")
                if source_raw_row["prompt_text"].encode("ascii") != source_prompt_bytes:
                    raise AssetError(f"source prompt/raw row mismatch: {source_prompt_path}")
                _assert_candidate_contract(source_raw_row)

                protocol_codebook = protocol["target_alignment_intervention"][arm][direction]["target_codebook"]
                if arm == "H0_ORIGINAL_M0_TARGET":
                    if source_raw_row["codebook"]["formula"] != protocol_codebook:
                        raise AssetError(f"H0 source codebook drift for {stack}/z{z}")
                    output_prompt_bytes = source_prompt_bytes
                    output_row_bytes = source_row_bytes
                    output_row = source_raw_row
                    exact_h0_reuse = True
                    name_tag = source_raw_row["lineage_id"][:12]
                else:
                    codebook = copy.deepcopy(H1_CODEBOOKS[direction])
                    codebook["latent_to_candidate"] = latent_to_candidates(
                        codebook["multiplier_mod7"], codebook["intercept_mod7"]
                    )
                    if codebook["formula"] != protocol_codebook:
                        raise AssetError(f"H1 implementation/protocol codebook drift for {stack}")
                    output_prompt_bytes = switch_prompt_codebook(source_prompt_bytes, codebook)
                    output_row = _derive_h1_row(
                        source_raw_row,
                        row_meta["sha256"],
                        prompt_meta["sha256"],
                        codebook,
                        output_prompt_bytes,
                    )
                    output_row_bytes = canonical_json_bytes(output_row, newline=True)
                    exact_h0_reuse = False
                    name_tag = output_row["lineage_id"][:12]
                _assert_candidate_contract(output_row)
                if output_row["row_id"] in seen_row_ids:
                    raise AssetError(f"duplicate output row_id: {output_row['row_id']}")
                seen_row_ids.add(output_row["row_id"])

                base = PurePosixPath("raw_target_bytes") / stack / arm
                prompt_rel = str(base / f"r13-{arm_index}-z{z}-{name_tag}.prompt.txt")
                row_rel = str(base / f"r13-{arm_index}-z{z}-{name_tag}.row.json")
                if prompt_rel in seen_output_paths or row_rel in seen_output_paths:
                    raise AssetError("duplicate output path")
                seen_output_paths.update((prompt_rel, row_rel))
                _write_new(safe_relpath(output_root, prompt_rel), output_prompt_bytes)
                _write_new(safe_relpath(output_root, row_rel), output_row_bytes)

                prompt_sha = sha256_bytes(output_prompt_bytes)
                row_sha = sha256_bytes(output_row_bytes)
                raw_inventory.extend(
                    (
                        {"byte_length": len(output_prompt_bytes), "relpath": prompt_rel, "sha256": prompt_sha},
                        {"byte_length": len(output_row_bytes), "relpath": row_rel, "sha256": row_sha},
                    )
                )
                panel_records.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "status": "STATIC_ASSET_PENDING_INDEPENDENT_HUMAN_REVIEW",
                        "run_eligible": False,
                        "model_execution_performed": False,
                        "scientific_evidence": False,
                        "human_review_status": "PENDING_INDEPENDENT_HUMAN_REVIEW",
                        "stack_index": stack_index,
                        "mapping_stack_id": stack,
                        "direction": direction,
                        "arm_index": arm_index,
                        "arm": arm,
                        "canonical_order_index": z,
                        "canonical_z": z,
                        "source_original": {
                            "row_id": source_raw_row["row_id"],
                            "lineage_id": source_raw_row["lineage_id"],
                            "prompt_relpath": prompt_meta["raw_relpath"],
                            "prompt_sha256": prompt_meta["sha256"],
                            "row_relpath": row_meta["raw_relpath"],
                            "row_sha256": row_meta["sha256"],
                        },
                        "generated": {
                            "prompt_relpath": prompt_rel,
                            "prompt_sha256": prompt_sha,
                            "row_relpath": row_rel,
                            "row_sha256": row_sha,
                        },
                        "exact_h0_original_prompt_bytes": exact_h0_reuse,
                        "exact_h0_original_row_bytes": exact_h0_reuse,
                        "target_codebook": copy.deepcopy(output_row["codebook"]),
                        "q_surface_factor": factor,
                        "q_surface_by_r": q_by_r,
                    }
                )

    if len(panel_records) != 4 * 2 * 7 or len(raw_inventory) != 4 * 2 * 7 * 2:
        raise AssetError("generated asset counts do not close")
    raw_inventory.sort(key=lambda entry: entry["relpath"])
    panel_jsonl_bytes = b"".join(canonical_json_bytes(row, newline=True) for row in panel_records)
    panel_relpath = "R13_MATCHED_TARGET_PANELS_R1.jsonl"
    _write_new(safe_relpath(output_root, panel_relpath), panel_jsonl_bytes)

    manifest = {
        "schema_version": "r13-matched-target-panel-manifest-r1",
        "status": "STATIC_ASSETS_GENERATED_PENDING_INDEPENDENT_HUMAN_REVIEW_NOT_AUTHORIZED",
        "run_eligible": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "scientific_evidence": False,
        "input_bindings": {
            "protocol_relpath": PROTOCOL_PATH.name,
            "protocol_sha256": sha256_bytes(protocol_bytes),
            "source_build_id": "build_v5_repair_a",
            "source_panel_relpath": "real_assets/build_v5_repair_a/TARGET_CALIBRATION_REAL_V1.jsonl",
            "source_panel_sha256": sha256_bytes(source_panel_bytes),
        },
        "ordered_stack_ids": list(STACKS),
        "ordered_arms": list(ARMS),
        "ordering_rule": "stack order, then H0/H1 arm order, then canonical z=0..6",
        "counts": {
            "stacks": 4,
            "arms_per_stack": 2,
            "canonical_rows_per_arm": 7,
            "panel_records": len(panel_records),
            "raw_files": len(raw_inventory),
        },
        "panel_jsonl": {
            "relpath": panel_relpath,
            "byte_length": len(panel_jsonl_bytes),
            "sha256": sha256_bytes(panel_jsonl_bytes),
        },
        "raw_file_inventory": raw_inventory,
        "raw_inventory_commitment_sha256": sha256_bytes(canonical_json_bytes(raw_inventory)),
        "matched_pair_allowlist": {
            "raw_row_allowed_leaf_paths": list(ALLOWED_ROW_DIFF_PATHS),
            "prompt_allowed_difference": "exactly one CODEBOOK= line, mechanically derived from target codebook",
            "all_other_prompt_bytes_and_raw_row_leaves_must_match": True,
            "h0_raw_prompt_and_row_must_byte_match_source_original": True,
        },
        "human_review": {
            "required_before_authorization": True,
            "completed": False,
            "receipt_template_relpath": "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json",
            "template_is_not_a_receipt": True,
        },
        "authorization_boundary": "STATIC ASSETS ONLY. This manifest and its tests grant no tokenizer, model, forward, gradient, optimizer, or R13 execution authority.",
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).encode("ascii") + b"\n"
    manifest_relpath = "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
    _write_new(safe_relpath(output_root, manifest_relpath), manifest_bytes)

    receipt_template = {
        "schema_version": "r13-matched-target-panel-human-review-receipt-template-r1",
        "status": "UNCOMPLETED_TEMPLATE_NOT_A_REVIEW_RECEIPT",
        "template_only": True,
        "review_completed": False,
        "decision": None,
        "reviewer_name": None,
        "reviewer_affiliation_or_role": None,
        "reviewed_at_utc": None,
        "review_method": None,
        "review_notes": None,
        "signature_algorithm": None,
        "signature_key_id": None,
        "signature": None,
        "asset_bindings": {
            "manifest_relpath": manifest_relpath,
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "panel_jsonl_relpath": panel_relpath,
            "panel_jsonl_sha256": sha256_bytes(panel_jsonl_bytes),
            "raw_inventory_commitment_sha256": manifest["raw_inventory_commitment_sha256"],
        },
        "required_checks": [
            "independently compare all 28 H0/H1 prompt pairs and confirm only the CODEBOOK line differs",
            "independently compare all 28 H0/H1 raw-row pairs and confirm only allowlisted mechanically implied leaves differ",
            "confirm every H0 prompt and raw row is byte-identical to its bound build_v5_repair_a original",
            "confirm K0..K6 candidate bijection and mechanically implied gold/shared/local labels in every row",
            "confirm q_H0 and q_H1 tables and r/q_H0/q_H1 distinctness for r=1..5",
        ],
        "completion_rule": "A reviewer must create a separate completed receipt, bind these exact hashes, fill every reviewer field, record PASS or REJECT, and provide a real externally verifiable signature. Editing this template in place is forbidden.",
    }
    receipt_bytes = json.dumps(receipt_template, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).encode("ascii") + b"\n"
    receipt_relpath = "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json"
    _write_new(safe_relpath(output_root, receipt_relpath), receipt_bytes)

    return {
        "output_root": str(output_root),
        "panel_records": len(panel_records),
        "raw_files": len(raw_inventory),
        "panel_jsonl_sha256": sha256_bytes(panel_jsonl_bytes),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "receipt_template_sha256": sha256_bytes(receipt_bytes),
        "raw_inventory_commitment_sha256": manifest["raw_inventory_commitment_sha256"],
        "run_eligible": False,
        "model_actions": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate R13 static matched target panels without loading a model.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    result = generate(args.output)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
