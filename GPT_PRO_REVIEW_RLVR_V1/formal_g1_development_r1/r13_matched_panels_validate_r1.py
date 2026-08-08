from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


FORMAL_DIR = Path(__file__).resolve().parent
PROTOCOL_PATH = FORMAL_DIR / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
SOURCE_ROOT = FORMAL_DIR.parent / "real_assets" / "build_v5_repair_a"
SOURCE_PANEL_PATH = SOURCE_ROOT / "TARGET_CALIBRATION_REAL_V1.jsonl"
DEFAULT_BUNDLE_ROOT = FORMAL_DIR / "r13_assets_r1"

MANIFEST_NAME = "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
PANEL_NAME = "R13_MATCHED_TARGET_PANELS_R1.jsonl"
TEMPLATE_NAME = "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json"
MACHINE_RECEIPT_NAME = "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"

STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))
H1_CODEBOOK_IDS = {
    "A_TO_B": "r13_h1_target_B_a5_b1",
    "B_TO_A": "r13_h1_target_A_a3_b0",
}

WRAPPER_KEYS = {
    "schema_version",
    "status",
    "run_eligible",
    "model_execution_performed",
    "scientific_evidence",
    "human_review_status",
    "stack_index",
    "mapping_stack_id",
    "direction",
    "arm_index",
    "arm",
    "canonical_order_index",
    "canonical_z",
    "source_original",
    "generated",
    "exact_h0_original_prompt_bytes",
    "exact_h0_original_row_bytes",
    "target_codebook",
    "q_surface_factor",
    "q_surface_by_r",
}
SOURCE_REF_KEYS = {
    "row_id",
    "lineage_id",
    "prompt_relpath",
    "prompt_sha256",
    "row_relpath",
    "row_sha256",
}
GENERATED_REF_KEYS = {
    "prompt_relpath",
    "prompt_sha256",
    "row_relpath",
    "row_sha256",
}
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "run_eligible",
    "model_execution_authorized",
    "model_execution_performed",
    "scientific_evidence",
    "input_bindings",
    "ordered_stack_ids",
    "ordered_arms",
    "ordering_rule",
    "counts",
    "panel_jsonl",
    "raw_file_inventory",
    "raw_inventory_commitment_sha256",
    "matched_pair_allowlist",
    "human_review",
    "authorization_boundary",
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


class ValidationError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(token: str) -> Any:
    raise ValidationError(f"non-finite JSON constant: {token}")


def strict_json_loads(data: bytes | str) -> Any:
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"non-UTF-8 JSON: {exc}") from exc
    try:
        return json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            raise ValidationError(f"blank JSONL line: {path}:{number}")
        row = strict_json_loads(line)
        if not isinstance(row, dict):
            raise ValidationError(f"non-object JSONL row: {path}:{number}")
        rows.append(row)
    return rows


def canonical_json_bytes(value: Any, *, newline: bool = False) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value is not finite canonical JSON: {exc}") from exc
    return payload + (b"\n" if newline else b"")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relpath: str) -> Path:
    if not isinstance(relpath, str):
        raise ValidationError("relative path must be a string")
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relpath:
        raise ValidationError(f"unsafe POSIX relative path: {relpath}")
    root = root.resolve()
    candidate = root.joinpath(*pure.parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"path escapes root: {relpath}") from exc
    return candidate


def require_exact_keys(value: dict[str, Any], keys: set[str], context: str) -> None:
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise ValidationError(f"{context} keys mismatch; missing={missing}, extra={extra}")


def parse_affine(formula: str) -> tuple[int, int]:
    match = re.fullmatch(r"K\(\((\d+)\*z\+(\d+)\) mod 7\)", formula)
    if match is None:
        raise ValidationError(f"non-canonical affine formula: {formula}")
    return int(match.group(1)), int(match.group(2))


def latent_to_candidates(a: int, b: int) -> list[str]:
    if a % 7 == 0:
        raise ValidationError("codebook multiplier is not invertible modulo 7")
    labels = [f"FINAL=K{(a * z + b) % 7}" for z in range(7)]
    if set(labels) != set(CANDIDATES) or len(set(labels)) != 7:
        raise ValidationError("latent_to_candidate is not a K0..K6 bijection")
    return labels


def validate_codebook(codebook: dict[str, Any], expected_formula: str, context: str) -> None:
    required = {
        "codebook_id",
        "formula",
        "intercept_mod7",
        "latent_to_candidate",
        "multiplier_mod7",
        "task_slot",
    }
    require_exact_keys(codebook, required, f"{context}.codebook")
    if codebook["formula"] != expected_formula:
        raise ValidationError(f"{context} formula mismatch")
    a, b = parse_affine(codebook["formula"])
    if (codebook["multiplier_mod7"], codebook["intercept_mod7"]) != (a, b):
        raise ValidationError(f"{context} formula/coefficients mismatch")
    if codebook["latent_to_candidate"] != latent_to_candidates(a, b):
        raise ValidationError(f"{context} latent_to_candidate mismatch")


def validate_candidate_contract(row: dict[str, Any], context: str) -> None:
    if tuple(row.get("candidate_order", ())) != CANDIDATES:
        raise ValidationError(f"{context} candidate_order is not exact K0..K6")
    records = row.get("candidate_records")
    if not isinstance(records, list) or len(records) != 7:
        raise ValidationError(f"{context} candidate_records count mismatch")
    if tuple(record.get("candidate") for record in records) != CANDIDATES:
        raise ValidationError(f"{context} candidate_records order/bijection mismatch")
    if len({record.get("candidate") for record in records}) != 7:
        raise ValidationError(f"{context} duplicate candidate")
    for index, record in enumerate(records):
        if record.get("candidate_index") != index:
            raise ValidationError(f"{context} candidate_index mismatch")
    z = int(row["canonical_z"])
    codebook = row["codebook"]
    expected_labels = {
        "gold_candidate": codebook["latent_to_candidate"][z],
        "shared_bug_candidate": codebook["latent_to_candidate"][int(row["shared_bug_z"])],
        "local_bug_candidate": codebook["latent_to_candidate"][int(row["local_bug_z"])],
    }
    for field, expected in expected_labels.items():
        if row.get(field) != expected:
            raise ValidationError(f"{context} mechanically implied {field} mismatch")
    for record in records:
        candidate = record["candidate"]
        expected_flags = {
            "is_gold": candidate == row["gold_candidate"],
            "is_shared_wrong": candidate == row["shared_bug_candidate"],
            "is_task_local_wrong": candidate == row["local_bug_candidate"],
        }
        for field, expected in expected_flags.items():
            if record.get(field) is not expected:
                raise ValidationError(f"{context} candidate flag {field} mismatch")


def leaf_differences(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "<root>"]
    if isinstance(left, dict):
        if set(left) != set(right):
            return [path or "<root>"]
        result: list[str] = []
        for key in sorted(left):
            child = f"{path}.{key}" if path else key
            result.extend(leaf_differences(left[key], right[key], child))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [path or "<root>"]
        result = []
        for index, (l_item, r_item) in enumerate(zip(left, right, strict=True)):
            result.extend(leaf_differences(l_item, r_item, f"{path}[{index}]"))
        return result
    return [] if left == right else [path or "<root>"]


def is_allowed_row_difference(path: str) -> bool:
    if path in {
        "codebook.codebook_id",
        "codebook.formula",
        "codebook.intercept_mod7",
        "codebook.multiplier_mod7",
        "gold_candidate",
        "lineage_id",
        "local_bug_candidate",
        "prompt_text",
        "row_id",
        "shared_bug_candidate",
    }:
        return True
    if re.fullmatch(r"codebook\.latent_to_candidate\[[0-6]\]", path):
        return True
    if re.fullmatch(
        r"candidate_records\[[0-6]\]\.(is_gold|is_shared_wrong|is_task_local_wrong)",
        path,
    ):
        return True
    return False


def expected_codebook_line(codebook: dict[str, Any]) -> bytes:
    labels = codebook["latent_to_candidate"]
    return ("CODEBOOK=" + ";".join(f"z{z}->{label.removeprefix('FINAL=')}" for z, label in enumerate(labels))).encode("ascii")


def validate_prompt_pair(h0: bytes, h1: bytes, h1_codebook: dict[str, Any], context: str) -> None:
    if b"\r" in h0 or b"\r" in h1:
        raise ValidationError(f"{context} prompts must be LF-normalized")
    h0_lines = h0.splitlines(keepends=True)
    h1_lines = h1.splitlines(keepends=True)
    if len(h0_lines) != len(h1_lines):
        raise ValidationError(f"{context} prompt line count changed")
    codebook_positions = [i for i, line in enumerate(h0_lines) if line.startswith(b"CODEBOOK=")]
    if len(codebook_positions) != 1:
        raise ValidationError(f"{context} H0 prompt CODEBOOK line count mismatch")
    position = codebook_positions[0]
    changed = [i for i, pair in enumerate(zip(h0_lines, h1_lines, strict=True)) if pair[0] != pair[1]]
    if changed != [position]:
        raise ValidationError(f"{context} non-allowlisted prompt line differences: {changed}")
    ending = b"\n" if h1_lines[position].endswith(b"\n") else b""
    if h1_lines[position] != expected_codebook_line(h1_codebook) + ending:
        raise ValidationError(f"{context} H1 CODEBOOK text is not mechanically derived")


def _source_raw_row(record: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result.pop("prompt_bytes", None)
    result.pop("row_bytes", None)
    return result


def _validate_fail_closed(value: dict[str, Any], context: str) -> None:
    if value["run_eligible"] is not False:
        raise ValidationError(f"{context} run_eligible must be false")
    if value["model_execution_performed"] is not False:
        raise ValidationError(f"{context} model_execution_performed must be false")
    if value["scientific_evidence"] is not False:
        raise ValidationError(f"{context} scientific_evidence must be false")


def validate_bundle(
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    *,
    protocol_path: Path = PROTOCOL_PATH,
    source_root: Path = SOURCE_ROOT,
) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
    manifest_path = bundle_root / MANIFEST_NAME
    panel_path = bundle_root / PANEL_NAME
    template_path = bundle_root / TEMPLATE_NAME
    manifest_bytes = manifest_path.read_bytes()
    panel_bytes = panel_path.read_bytes()
    template_bytes = template_path.read_bytes()
    manifest = strict_json_loads(manifest_bytes)
    template = strict_json_loads(template_bytes)
    require_exact_keys(manifest, MANIFEST_KEYS, "manifest")
    if manifest["schema_version"] != "r13-matched-target-panel-manifest-r1":
        raise ValidationError("manifest schema mismatch")
    if manifest["run_eligible"] is not False or manifest["model_execution_authorized"] is not False:
        raise ValidationError("manifest must remain unauthorized")
    if manifest["model_execution_performed"] is not False or manifest["scientific_evidence"] is not False:
        raise ValidationError("manifest overstates evidence")

    protocol_bytes = protocol_path.read_bytes()
    protocol = strict_json_loads(protocol_bytes)
    if protocol["run_eligible"] or protocol["model_execution_authorized"]:
        raise ValidationError("bound protocol unexpectedly authorizes execution")
    bindings = manifest["input_bindings"]
    if bindings != {
        "protocol_relpath": protocol_path.name,
        "protocol_sha256": sha256_bytes(protocol_bytes),
        "source_build_id": "build_v5_repair_a",
        "source_panel_relpath": "real_assets/build_v5_repair_a/TARGET_CALIBRATION_REAL_V1.jsonl",
        "source_panel_sha256": sha256_bytes((source_root / "TARGET_CALIBRATION_REAL_V1.jsonl").read_bytes()),
    }:
        raise ValidationError("manifest input bindings mismatch")
    if manifest["ordered_stack_ids"] != list(STACKS) or manifest["ordered_arms"] != list(ARMS):
        raise ValidationError("manifest stack/arm order mismatch")
    if protocol["fixed_source_stacks"]["ordered_stack_ids"] != list(STACKS):
        raise ValidationError("protocol stack order mismatch")
    if protocol["target_alignment_intervention"]["ordered_arms"] != list(ARMS):
        raise ValidationError("protocol arm order mismatch")

    panel_meta = manifest["panel_jsonl"]
    if panel_meta != {
        "relpath": PANEL_NAME,
        "byte_length": len(panel_bytes),
        "sha256": sha256_bytes(panel_bytes),
    }:
        raise ValidationError("panel JSONL commitment mismatch")
    panel_rows = read_jsonl(panel_path)
    if len(panel_rows) != 56:
        raise ValidationError(f"panel record count mismatch: {len(panel_rows)}")
    counts = manifest["counts"]
    if counts != {
        "stacks": 4,
        "arms_per_stack": 2,
        "canonical_rows_per_arm": 7,
        "panel_records": 56,
        "raw_files": 112,
    }:
        raise ValidationError("manifest counts mismatch")

    inventory = manifest["raw_file_inventory"]
    if not isinstance(inventory, list) or len(inventory) != 112:
        raise ValidationError("raw inventory count mismatch")
    if inventory != sorted(inventory, key=lambda entry: entry["relpath"]):
        raise ValidationError("raw inventory order mismatch")
    inventory_paths: set[str] = set()
    for index, entry in enumerate(inventory):
        require_exact_keys(entry, {"byte_length", "relpath", "sha256"}, f"inventory[{index}]")
        if entry["relpath"] in inventory_paths:
            raise ValidationError(f"duplicate raw inventory relpath: {entry['relpath']}")
        inventory_paths.add(entry["relpath"])
        data = safe_path(bundle_root, entry["relpath"]).read_bytes()
        if (len(data), sha256_bytes(data)) != (entry["byte_length"], entry["sha256"]):
            raise ValidationError(f"raw inventory commitment mismatch: {entry['relpath']}")
    inventory_commitment = sha256_bytes(canonical_json_bytes(inventory))
    if manifest["raw_inventory_commitment_sha256"] != inventory_commitment:
        raise ValidationError("raw inventory commitment hash mismatch")
    allowlist = manifest["matched_pair_allowlist"]
    if allowlist != {
        "raw_row_allowed_leaf_paths": list(ALLOWED_ROW_DIFF_PATHS),
        "prompt_allowed_difference": "exactly one CODEBOOK= line, mechanically derived from target codebook",
        "all_other_prompt_bytes_and_raw_row_leaves_must_match": True,
        "h0_raw_prompt_and_row_must_byte_match_source_original": True,
    }:
        raise ValidationError("manifest allowlist drift")

    source_panel_path = source_root / "TARGET_CALIBRATION_REAL_V1.jsonl"
    source_records = read_jsonl(source_panel_path)
    source_index: dict[tuple[str, int], dict[str, Any]] = {}
    for record in source_records:
        stack = record.get("mapping_stack_id")
        if stack not in STACKS:
            continue
        key = (stack, int(record["canonical_z"]))
        if key in source_index:
            raise ValidationError(f"duplicate source row key: {key}")
        source_index[key] = record
    if set(source_index) != {(stack, z) for stack in STACKS for z in range(7)}:
        raise ValidationError("source panel coverage mismatch")

    wrapper_index: dict[tuple[str, str, int], dict[str, Any]] = {}
    generated_paths: set[str] = set()
    expected_order = [
        (stack, arm, z)
        for stack in STACKS
        for arm in ARMS
        for z in range(7)
    ]
    observed_order: list[tuple[str, str, int]] = []
    for index, wrapper in enumerate(panel_rows):
        require_exact_keys(wrapper, WRAPPER_KEYS, f"panel[{index}]")
        require_exact_keys(wrapper["source_original"], SOURCE_REF_KEYS, f"panel[{index}].source_original")
        require_exact_keys(wrapper["generated"], GENERATED_REF_KEYS, f"panel[{index}].generated")
        _validate_fail_closed(wrapper, f"panel[{index}]")
        key = (wrapper["mapping_stack_id"], wrapper["arm"], int(wrapper["canonical_z"]))
        if key in wrapper_index:
            raise ValidationError(f"duplicate panel key: {key}")
        wrapper_index[key] = wrapper
        observed_order.append(key)
        stack_index = STACKS.index(key[0]) if key[0] in STACKS else -1
        arm_index = ARMS.index(key[1]) if key[1] in ARMS else -1
        if wrapper["stack_index"] != stack_index or wrapper["arm_index"] != arm_index:
            raise ValidationError(f"panel index metadata mismatch: {key}")
        if wrapper["canonical_order_index"] != key[2] or key[2] not in range(7):
            raise ValidationError(f"canonical order metadata mismatch: {key}")
        direction = "A_TO_B" if key[0].endswith("A_TO_B") else "B_TO_A"
        if wrapper["direction"] != direction:
            raise ValidationError(f"direction mismatch: {key}")
        generated = wrapper["generated"]
        for field in ("prompt_relpath", "row_relpath"):
            if generated[field] in generated_paths:
                raise ValidationError(f"duplicate generated path: {generated[field]}")
            generated_paths.add(generated[field])
            if generated[field] not in inventory_paths:
                raise ValidationError(f"generated path absent from inventory: {generated[field]}")
    if observed_order != expected_order:
        raise ValidationError("panel JSONL order mismatch")
    if generated_paths != inventory_paths:
        raise ValidationError("raw inventory has unreferenced files")

    for stack in STACKS:
        direction = "A_TO_B" if stack.endswith("A_TO_B") else "B_TO_A"
        for z in range(7):
            source_record = source_index[(stack, z)]
            source_ref_expected = {
                "row_id": source_record["row_id"],
                "lineage_id": source_record["lineage_id"],
                "prompt_relpath": source_record["prompt_bytes"]["raw_relpath"],
                "prompt_sha256": source_record["prompt_bytes"]["sha256"],
                "row_relpath": source_record["row_bytes"]["raw_relpath"],
                "row_sha256": source_record["row_bytes"]["sha256"],
            }
            source_prompt = safe_path(source_root, source_ref_expected["prompt_relpath"]).read_bytes()
            source_row_bytes = safe_path(source_root, source_ref_expected["row_relpath"]).read_bytes()
            if sha256_bytes(source_prompt) != source_ref_expected["prompt_sha256"]:
                raise ValidationError(f"source prompt hash mismatch: {stack}/z{z}")
            if sha256_bytes(source_row_bytes) != source_ref_expected["row_sha256"]:
                raise ValidationError(f"source row hash mismatch: {stack}/z{z}")
            source_row = strict_json_loads(source_row_bytes)
            if source_row != _source_raw_row(source_record):
                raise ValidationError(f"source raw row/JSONL mismatch: {stack}/z{z}")
            if source_row["prompt_text"].encode("ascii") != source_prompt:
                raise ValidationError(f"source prompt/raw row mismatch: {stack}/z{z}")

            h0 = wrapper_index[(stack, ARMS[0], z)]
            h1 = wrapper_index[(stack, ARMS[1], z)]
            if h0["source_original"] != source_ref_expected or h1["source_original"] != source_ref_expected:
                raise ValidationError(f"source reference mismatch: {stack}/z{z}")
            h0_prompt = safe_path(bundle_root, h0["generated"]["prompt_relpath"]).read_bytes()
            h0_row_bytes = safe_path(bundle_root, h0["generated"]["row_relpath"]).read_bytes()
            h1_prompt = safe_path(bundle_root, h1["generated"]["prompt_relpath"]).read_bytes()
            h1_row_bytes = safe_path(bundle_root, h1["generated"]["row_relpath"]).read_bytes()
            for wrapper, prompt_bytes, row_bytes in (
                (h0, h0_prompt, h0_row_bytes),
                (h1, h1_prompt, h1_row_bytes),
            ):
                generated = wrapper["generated"]
                if sha256_bytes(prompt_bytes) != generated["prompt_sha256"]:
                    raise ValidationError(f"wrapper prompt hash mismatch: {stack}/{wrapper['arm']}/z{z}")
                if sha256_bytes(row_bytes) != generated["row_sha256"]:
                    raise ValidationError(f"wrapper row hash mismatch: {stack}/{wrapper['arm']}/z{z}")
            if not h0["exact_h0_original_prompt_bytes"] or not h0["exact_h0_original_row_bytes"]:
                raise ValidationError(f"H0 exact reuse flags false: {stack}/z{z}")
            if h1["exact_h0_original_prompt_bytes"] or h1["exact_h0_original_row_bytes"]:
                raise ValidationError(f"H1 exact H0 reuse flags true: {stack}/z{z}")
            if h0_prompt != source_prompt or h0_row_bytes != source_row_bytes:
                raise ValidationError(f"H0 is not byte-exact source reuse: {stack}/z{z}")

            h0_row = strict_json_loads(h0_row_bytes)
            h1_row = strict_json_loads(h1_row_bytes)
            if h1_row_bytes != canonical_json_bytes(h1_row, newline=True):
                raise ValidationError(f"H1 row is not canonical JSON+LF: {stack}/z{z}")
            if h0_row["canonical_z"] != z or h1_row["canonical_z"] != z:
                raise ValidationError(f"canonical z mismatch: {stack}/z{z}")
            if h0_row["prompt_text"].encode("ascii") != h0_prompt:
                raise ValidationError(f"H0 prompt_text mismatch: {stack}/z{z}")
            if h1_row["prompt_text"].encode("ascii") != h1_prompt:
                raise ValidationError(f"H1 prompt_text mismatch: {stack}/z{z}")

            intervention = protocol["target_alignment_intervention"]
            h0_formula = intervention[ARMS[0]][direction]["target_codebook"]
            h1_formula = intervention[ARMS[1]][direction]["target_codebook"]
            validate_codebook(h0_row["codebook"], h0_formula, f"{stack}/H0/z{z}")
            validate_codebook(h1_row["codebook"], h1_formula, f"{stack}/H1/z{z}")
            if h1_row["codebook"]["codebook_id"] != H1_CODEBOOK_IDS[direction]:
                raise ValidationError(f"H1 codebook_id mismatch: {stack}/z{z}")
            if h0["target_codebook"] != h0_row["codebook"] or h1["target_codebook"] != h1_row["codebook"]:
                raise ValidationError(f"wrapper/raw codebook mismatch: {stack}/z{z}")
            validate_candidate_contract(h0_row, f"{stack}/H0/z{z}")
            validate_candidate_contract(h1_row, f"{stack}/H1/z{z}")
            validate_prompt_pair(h0_prompt, h1_prompt, h1_row["codebook"], f"{stack}/z{z}")

            differences = leaf_differences(h0_row, h1_row)
            forbidden = [path for path in differences if not is_allowed_row_difference(path)]
            if forbidden:
                raise ValidationError(f"non-allowlisted raw-row differences at {stack}/z{z}: {forbidden}")
            required_changed = {
                "codebook.codebook_id",
                "codebook.formula",
                "codebook.multiplier_mod7",
                "lineage_id",
                "prompt_text",
                "row_id",
            }
            if not required_changed.issubset(set(differences)):
                raise ValidationError(f"required H1 changes missing at {stack}/z{z}")
            if not any(
                path.startswith("codebook.latent_to_candidate[")
                for path in differences
            ):
                raise ValidationError(f"H1 codebook permutation did not change at {stack}/z{z}")

            commitment = {
                "schema_version": "r13-matched-target-panels-r1",
                "arm": ARMS[1],
                "source_lineage_id": source_row["lineage_id"],
                "source_raw_row_sha256": source_ref_expected["row_sha256"],
                "source_raw_prompt_sha256": source_ref_expected["prompt_sha256"],
                "target_codebook": h1_row["codebook"],
            }
            expected_lineage = sha256_bytes(canonical_json_bytes(commitment))
            expected_row_id = f"r13-h1-{stack}-z{z}-{expected_lineage[:12]}"
            if h1_row["lineage_id"] != expected_lineage or h1_row["row_id"] != expected_row_id:
                raise ValidationError(f"H1 lineage commitment mismatch: {stack}/z{z}")

            q_tables: dict[str, dict[int, int]] = {}
            for arm, wrapper in ((ARMS[0], h0), (ARMS[1], h1)):
                entry = intervention[arm][direction]
                source_a, _ = parse_affine(entry["source_codebook"])
                target_a, _ = parse_affine(entry["target_codebook"])
                factor = (pow(target_a, -1, 7) * source_a) % 7
                q = {r: (factor * r) % 7 for r in range(1, 6)}
                recorded = {int(r): int(value) for r, value in wrapper["q_surface_by_r"].items()}
                if wrapper["q_surface_factor"] != factor or recorded != q:
                    raise ValidationError(f"q table mismatch: {stack}/{arm}/z{z}")
                if entry["surface_factor"] != factor or {int(r): int(v) for r, v in entry["q_surface_by_r"].items()} != q:
                    raise ValidationError(f"protocol q table mismatch: {stack}/{arm}")
                q_tables[arm] = q
            for r in range(1, 6):
                if len({r, q_tables[ARMS[0]][r], q_tables[ARMS[1]][r]}) != 3:
                    raise ValidationError(f"r/q_H0/q_H1 distinctness failure: {stack}/r{r}")

    template_keys = {
        "schema_version",
        "status",
        "template_only",
        "review_completed",
        "decision",
        "reviewer_name",
        "reviewer_affiliation_or_role",
        "reviewed_at_utc",
        "review_method",
        "review_notes",
        "signature_algorithm",
        "signature_key_id",
        "signature",
        "asset_bindings",
        "required_checks",
        "completion_rule",
    }
    require_exact_keys(template, template_keys, "human review template")
    if template["template_only"] is not True or template["review_completed"] is not False:
        raise ValidationError("human review template falsely claims completion")
    nullable = (
        "decision",
        "reviewer_name",
        "reviewer_affiliation_or_role",
        "reviewed_at_utc",
        "review_method",
        "review_notes",
        "signature_algorithm",
        "signature_key_id",
        "signature",
    )
    if any(template[field] is not None for field in nullable):
        raise ValidationError("human review template contains forged review/signature values")
    expected_template_bindings = {
        "manifest_relpath": MANIFEST_NAME,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "panel_jsonl_relpath": PANEL_NAME,
        "panel_jsonl_sha256": sha256_bytes(panel_bytes),
        "raw_inventory_commitment_sha256": inventory_commitment,
    }
    if template["asset_bindings"] != expected_template_bindings:
        raise ValidationError("human review template asset bindings mismatch")
    if manifest["human_review"] != {
        "required_before_authorization": True,
        "completed": False,
        "receipt_template_relpath": TEMPLATE_NAME,
        "template_is_not_a_receipt": True,
    }:
        raise ValidationError("manifest human review boundary mismatch")

    report = {
        "schema_version": "r13-matched-target-panel-allowlist-validation-r1",
        "verdict": "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY",
        "run_eligible": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "model_actions": 0,
        "scientific_evidence": False,
        "human_review_completed": False,
        "validated_stacks": 4,
        "validated_pairs": 28,
        "validated_rows": 56,
        "validated_raw_files": 112,
        "bindings": {
            "protocol_sha256": sha256_bytes(protocol_bytes),
            "source_panel_sha256": bindings["source_panel_sha256"],
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "panel_jsonl_sha256": sha256_bytes(panel_bytes),
            "raw_inventory_commitment_sha256": inventory_commitment,
            "human_review_template_sha256": sha256_bytes(template_bytes),
        },
        "implementation_bindings": {
            "generator_relpath": "r13_matched_panels_generate_r1.py",
            "generator_sha256": sha256_bytes(
                (FORMAL_DIR / "r13_matched_panels_generate_r1.py").read_bytes()
            ),
            "validator_relpath": "r13_matched_panels_validate_r1.py",
            "validator_sha256": sha256_bytes(Path(__file__).resolve().read_bytes()),
        },
        "authorization_boundary": "Machine allowlist validation is not independent human review and grants no model execution authority.",
    }
    machine_receipt_path = bundle_root / MACHINE_RECEIPT_NAME
    if machine_receipt_path.exists():
        expected_receipt_bytes = (
            json.dumps(
                report,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            ).encode("ascii")
            + b"\n"
        )
        if machine_receipt_path.read_bytes() != expected_receipt_bytes:
            raise ValidationError("existing machine allowlist receipt does not match recomputed report")
    return report


def write_machine_receipt(bundle_root: Path, report: dict[str, Any]) -> Path:
    path = bundle_root.resolve() / MACHINE_RECEIPT_NAME
    if path.exists():
        raise ValidationError(f"refusing to overwrite machine receipt: {path}")
    data = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).encode("ascii") + b"\n"
    path.write_bytes(data)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Strictly validate the static R13 matched target-panel chain.")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--write-machine-receipt", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_bundle(args.bundle)
        if args.write_machine_receipt:
            receipt_path = write_machine_receipt(args.bundle, report)
            report = dict(report)
            report["machine_receipt_path"] = str(receipt_path)
            report["machine_receipt_sha256"] = sha256_bytes(receipt_path.read_bytes())
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
        return 0
    except (OSError, ValidationError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"verdict": "FAIL_CLOSED", "error": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
