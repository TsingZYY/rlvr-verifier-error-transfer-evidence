"""Independent, model-free validator for the R13 target-alignment pilot.

The validator deliberately imports neither the R13 runner nor any ML package.
It reconstructs every scientific quantity from bound seven-candidate traces.
The current R13 protocol is a non-authorizing draft, so the command-line path
fails closed until a separately frozen protocol and external authorization are
provided.  ``synthetic_test_mode`` exists only as an in-process unit-test hook;
it is intentionally absent from the CLI.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, Iterable, Sequence


STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
SOURCE_IDENTITIES = tuple(f"Z7_PLUS{i}" for i in range(1, 6))
TARGET_OFFSETS = tuple(range(1, 7))
CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))
VARIANT_REPLICATES = {
    "MINIMAL_4_PROCESS_A": ("A",),
    "REPRODUCIBILITY_8_PROCESS_AB": ("A", "B"),
}
REPLICATE_ARM_ORDER = {
    "A": ARMS,
    "B": tuple(reversed(ARMS)),
}
TECHNICAL_TOLERANCE = 1e-12
DIRECTIONAL_EPSILON = 1e-10
MAX_HUMAN_REVIEW_AGE = timedelta(days=30)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.:|-]{1,200}")

PROTOCOL_SCHEMA = "r13-fixed-update-target-alignment-pilot-draft-r1"
RESULT_SCHEMA = "r13-target-alignment-result-bundle-r1"
PROCESS_SCHEMA = "r13-target-blind-runner-core-result-r1"
ALLOWLIST_RECEIPT_SCHEMA = "r13-matched-target-panel-allowlist-validation-r1"
HUMAN_RECEIPT_SCHEMA = "r13-matched-panel-human-review-receipt-r1"
HUMAN_RECEIPT_TEMPLATE_SCHEMA = (
    "r13-matched-target-panel-human-review-receipt-template-r1"
)
HUMAN_RECEIPT_STATUS = "COMPLETED_INDEPENDENT_HUMAN_REVIEW"
HUMAN_REVIEW_CONTEXT = b"RLVR-R13-HUMAN-PANEL-REVIEW-V1\x00"
AUTHORIZATION_SCHEMA = "r13-target-alignment-run-authorization-r1"
MATCHED_PANEL_MANIFEST_SCHEMA = "r13-matched-target-panel-manifest-r1"
MATCHED_PANEL_WRAPPER_SCHEMA = "r13-matched-target-panels-r1"
MATCHED_PANEL_MANIFEST_NAME = "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
MATCHED_PANEL_JSONL_NAME = "R13_MATCHED_TARGET_PANELS_R1.jsonl"
MATCHED_PANEL_ALLOWLIST_NAME = "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"
HUMAN_REQUIRED_CHECKS = (
    "independently compare all 28 H0/H1 prompt pairs and confirm only the CODEBOOK line differs",
    "independently compare all 28 H0/H1 raw-row pairs and confirm only allowlisted mechanically implied leaves differ",
    "confirm every H0 prompt and raw row is byte-identical to its bound build_v5_repair_a original",
    "confirm K0..K6 candidate bijection and mechanically implied gold/shared/local labels in every row",
    "confirm q_H0 and q_H1 tables and r/q_H0/q_H1 distinctness for r=1..5",
)
HUMAN_COMPLETION_RULE = (
    "A reviewer must create a separate completed receipt, bind these exact hashes, "
    "fill every reviewer field, record PASS or REJECT, and provide a real "
    "externally verifiable signature. Editing this template in place is forbidden."
)

BINDING_KEYS = (
    "protocol_sha256",
    "panel_bundle_sha256",
    "allowlist_receipt_sha256",
    "human_review_receipt_sha256",
    "authorization_sha256",
    "runner_sha256",
    "validator_sha256",
    "model_inventory_sha256",
    "source_assets_sha256",
)
AUTH_BOUND_BINDING_KEYS = tuple(
    key for key in BINDING_KEYS if key != "authorization_sha256"
)

RESULT_KEYS = {
    "schema_version",
    "status",
    "synthetic_fixture",
    "run_id",
    "created_at_utc",
    "selected_variant",
    "scientific_evidence",
    "formal_experiment",
    "formal_confirmatory",
    "development_only",
    "sampled_rlvr",
    "same_empirical_or_policy_fpr",
    "model_execution_performed",
    "static_core_only",
    "model_execution_authorized_by_core",
    "ordered_candidate_set",
    "bindings",
    "ordered_process_ids",
    "counts",
    "events",
    "process_results",
}
PROCESS_KEYS = {
    "schema_version",
    "status",
    "run_id",
    "bundle_run_id",
    "created_at_utc",
    "static_core_only",
    "model_execution_authorized_by_core",
    "scientific_evidence",
    "formal_experiment",
    "development_only",
    "stack_id",
    "replicate_id",
    "arm_evaluation_order",
    "bindings",
    "ordered_candidate_set",
    "initial_parameter_hash",
    "counts",
    "events",
    "pre_target_readouts",
    "source_update_executions",
}
READOUT_KEYS = {
    "readout_ordinal",
    "phase",
    "arm",
    "source_rule_identity",
    "target_panel_sha256",
    "parameter_hash_before",
    "parameter_hash_after",
    "row_traces",
}
TRACE_KEYS = {
    "row_id",
    "panel_row_sha256",
    "state",
    "arm",
    "source_rule_identity",
    "source_update_hash",
    "ordered_candidate_scores",
}
EXECUTION_KEYS = {
    "execution_event_id",
    "execution_ordinal",
    "execution_key",
    "source_rule_identity",
    "initial_parameter_hash_after_reset",
    "update_parameter_hash",
    "source_update_call_sha256",
    "optimizer_step_count",
    "readout_gradient_count",
    "readout_optimizer_step_count",
    "readout_state_mutation_count",
    "backend_update_receipt",
    "readouts",
}
EVENT_KEYS = {
    "execution_event_id",
    "process_id",
    "execution_key",
    "source_rule_identity",
    "update_parameter_hash",
}
PROCESS_COUNT_KEYS = {
    "technical_processes",
    "fresh_reset_calls",
    "source_update_calls",
    "technical_update_executions",
    "unique_design_source_updates",
    "target_read_calls",
    "pre_target_vectors",
    "pre_target_identity_reads",
    "post_target_vectors",
    "post_target_identity_reads",
    "total_target_identity_metric_cells",
    "raw_target_row_traces",
    "raw_candidate_scores",
    "parameter_hash_observations",
    "event_count",
}
TOTAL_COUNT_KEYS = {
    "os_processes",
    "unique_design_source_updates",
    "technical_update_executions",
    "pre_target_vectors",
    "pre_target_identity_reads",
    "post_target_vectors",
    "post_target_identity_reads",
    "total_target_identity_metric_cells",
}

ALLOWLIST_RECEIPT_KEYS = {
    "authorization_boundary",
    "bindings",
    "human_review_completed",
    "implementation_bindings",
    "model_actions",
    "model_execution_authorized",
    "model_execution_performed",
    "run_eligible",
    "schema_version",
    "scientific_evidence",
    "validated_pairs",
    "validated_raw_files",
    "validated_rows",
    "validated_stacks",
    "verdict",
}
ALLOWLIST_BINDING_KEYS = {
    "human_review_template_sha256",
    "manifest_sha256",
    "panel_jsonl_sha256",
    "protocol_sha256",
    "raw_inventory_commitment_sha256",
    "source_panel_sha256",
}
ALLOWLIST_IMPLEMENTATION_BINDING_KEYS = {
    "generator_relpath",
    "generator_sha256",
    "validator_relpath",
    "validator_sha256",
}
ALLOWLIST_IMPLEMENTATION_RELPATHS = {
    "generator": "r13_matched_panels_generate_r1.py",
    "validator": "r13_matched_panels_validate_r1.py",
}
MATCHED_PANEL_MANIFEST_KEYS = {
    "authorization_boundary",
    "counts",
    "human_review",
    "input_bindings",
    "matched_pair_allowlist",
    "model_execution_authorized",
    "model_execution_performed",
    "ordered_arms",
    "ordered_stack_ids",
    "ordering_rule",
    "panel_jsonl",
    "raw_file_inventory",
    "raw_inventory_commitment_sha256",
    "run_eligible",
    "schema_version",
    "scientific_evidence",
    "status",
}
MATCHED_PANEL_WRAPPER_KEYS = {
    "arm",
    "arm_index",
    "canonical_order_index",
    "canonical_z",
    "direction",
    "exact_h0_original_prompt_bytes",
    "exact_h0_original_row_bytes",
    "generated",
    "human_review_status",
    "mapping_stack_id",
    "model_execution_performed",
    "q_surface_by_r",
    "q_surface_factor",
    "run_eligible",
    "schema_version",
    "scientific_evidence",
    "source_original",
    "stack_index",
    "status",
    "target_codebook",
}
MATCHED_PANEL_SOURCE_REF_KEYS = {
    "lineage_id",
    "prompt_relpath",
    "prompt_sha256",
    "row_id",
    "row_relpath",
    "row_sha256",
}
MATCHED_PANEL_GENERATED_REF_KEYS = {
    "prompt_relpath",
    "prompt_sha256",
    "row_relpath",
    "row_sha256",
}
HUMAN_RECEIPT_KEYS = {
    "asset_bindings",
    "completion_rule",
    "decision",
    "required_checks",
    "review_completed",
    "review_method",
    "review_notes",
    "reviewed_at_utc",
    "reviewer_affiliation_or_role",
    "reviewer_name",
    "schema_version",
    "signature",
    "signature_algorithm",
    "signature_key_id",
    "status",
    "template_only",
}
HUMAN_ASSET_BINDING_KEYS = {
    "manifest_relpath",
    "manifest_sha256",
    "panel_jsonl_relpath",
    "panel_jsonl_sha256",
    "raw_inventory_commitment_sha256",
}
AUTHORIZATION_KEYS = {
    "schema_version",
    "status",
    "synthetic_fixture",
    "authorization_id",
    "run_id",
    "issued_at_utc",
    "expires_at_utc",
    "selected_variant",
    "ordered_process_ids",
    "bindings",
    "model_execution_authorized",
    "single_use",
    "allowed_operations",
    "forbidden_operations",
    "trusted_signer_id",
    "trusted_signer_key_fingerprint_sha256",
    "signature_algorithm",
    "signature",
}

ALLOWED_PANEL_DIFF_PATTERNS = (
    "arm",
    "codebook.codebook_id",
    "codebook.formula",
    "codebook.intercept_mod7",
    "codebook.latent_to_candidate[*]",
    "codebook.multiplier_mod7",
    "gold_candidate",
    "shared_bug_candidate",
    "local_bug_candidate",
    "candidate_records[*].is_gold",
    "candidate_records[*].is_shared_wrong",
    "candidate_records[*].is_task_local_wrong",
    "prompt_text",
    "row_id",
    "lineage_id",
)


class R13ValidationError(RuntimeError):
    """A technical or custody gate failed."""


class R13AuthorizationError(R13ValidationError):
    """The current inputs do not authorize a real model result."""


@dataclass(frozen=True)
class ExternalTrustPolicy:
    authorization_signer_id: str
    authorization_key_fingerprint_sha256: str
    authorization_signature_algorithm: str
    verify_authorization_signature: Callable[[bytes, str], bool]
    verify_human_review_signature: Callable[[bytes, str, str, str], bool]
    authorization_nonce_is_unconsumed: Callable[[str, str], bool]


@dataclass(frozen=True)
class PanelRecord:
    stack_id: str
    arm: str
    canonical_z: int
    row_id: str
    row_sha256: str
    prompt_sha256: str
    row: dict[str, Any]
    row_bytes: bytes
    prompt_bytes: bytes


@dataclass(frozen=True)
class LoadedMatchedPanelBundle:
    """A manifest-hash-bound snapshot loaded from the real bundle on disk."""

    records: tuple[PanelRecord, ...]
    canonical_instance_ids: tuple[str, ...]
    manifest_sha256: str
    panel_jsonl_sha256: str
    raw_inventory_commitment_sha256: str
    allowlist_receipt_sha256: str
    allowlist_receipt: dict[str, Any]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R13ValidationError(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise R13ValidationError("value is not canonical finite JSON") from exc


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_sha256(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and SHA256_RE.fullmatch(value) is not None,
        f"{label}: expected lowercase SHA-256",
    )
    return value


def valid_uuid4(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and UUID4_RE.fullmatch(value) is not None,
        f"{label}: expected lowercase UUIDv4",
    )
    return value


def parse_utc(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{label}: UTC timestamp must end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise R13ValidationError(f"{label}: invalid RFC3339 UTC") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), f"{label}: not UTC")
    return parsed


def finite_number(value: Any, label: str) -> float:
    require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        f"{label}: expected number",
    )
    number = float(value)
    require(math.isfinite(number), f"{label}: non-finite number")
    return number


def assert_finite(value: Any, label: str = "root") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        require(math.isfinite(value), f"{label}: non-finite number")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{label}.{key}")
        return
    raise R13ValidationError(f"{label}: unsupported value type {type(value)!r}")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise R13ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise R13ValidationError(f"non-finite JSON constant: {value}")


def strict_json_loads(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise R13ValidationError(f"{label}: not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise R13ValidationError(f"{label}: invalid strict JSON") from exc
    assert_finite(value, label)
    return value


def strict_jsonl_loads(raw: bytes, label: str) -> list[Any]:
    rows: list[Any] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        require(bool(line.strip()), f"{label}:{line_number}: blank JSONL row")
        rows.append(strict_json_loads(line, f"{label}:{line_number}"))
    require(bool(rows), f"{label}: empty JSONL")
    return rows


def read_regular_snapshot(path: Path, label: str) -> tuple[bytes, str]:
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), f"{label}: not a regular file")
    require(not path.is_symlink(), f"{label}: symbolic links forbidden")
    raw = path.read_bytes()
    after = path.lstat()
    require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        f"{label}: file changed while reading",
    )
    return raw, sha256_bytes(raw)


def _asset_canonical_json_bytes(value: Any) -> bytes:
    """Canonical form used by the matched-panel raw-inventory commitment."""

    assert_finite(value, "matched-panel asset commitment")
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise R13ValidationError("matched-panel asset commitment is not canonical JSON") from exc


def _safe_bound_bundle_path(root: Path, relpath: Any, label: str) -> Path:
    require(
        isinstance(relpath, str)
        and bool(relpath)
        and "\\" not in relpath,
        f"{label}: invalid POSIX relative path",
    )
    pure = PurePosixPath(relpath)
    require(
        not pure.is_absolute()
        and pure.as_posix() == relpath
        and all(part not in ("", ".", "..") for part in pure.parts),
        f"{label}: unsafe relative path",
    )
    candidate = root.joinpath(*pure.parts).resolve()
    require(candidate.is_relative_to(root), f"{label}: path escapes bundle root")
    return candidate


def load_bound_matched_panel_bundle(
    bundle_root: Path,
    *,
    expected_manifest_sha256: str,
    expected_allowlist_receipt_sha256: str,
) -> LoadedMatchedPanelBundle:
    """Load records only after recomputing the real manifest/JSONL/raw hash chain.

    This is the production provenance boundary.  A caller-created ``PanelRecord``
    sequence is not evidence that it came from the authorization-bound manifest.
    """

    valid_sha256(expected_manifest_sha256, "expected matched-panel manifest hash")
    valid_sha256(expected_allowlist_receipt_sha256, "expected allowlist receipt hash")
    supplied_root = Path(bundle_root)
    root_stat = supplied_root.lstat()
    require(stat.S_ISDIR(root_stat.st_mode), "matched-panel bundle root is not a directory")
    require(not supplied_root.is_symlink(), "matched-panel bundle root must not be a symbolic link")
    root = supplied_root.resolve()

    manifest_bytes, manifest_sha256 = read_regular_snapshot(
        root / MATCHED_PANEL_MANIFEST_NAME, "matched-panel manifest"
    )
    require(
        manifest_sha256 == expected_manifest_sha256,
        "matched-panel manifest external hash mismatch",
    )
    manifest = strict_json_loads(manifest_bytes, "matched-panel manifest")
    require(
        isinstance(manifest, dict) and set(manifest) == MATCHED_PANEL_MANIFEST_KEYS,
        "matched-panel manifest schema drift",
    )
    require(
        manifest["schema_version"] == MATCHED_PANEL_MANIFEST_SCHEMA,
        "matched-panel manifest version drift",
    )
    require(
        manifest["status"]
        == "STATIC_ASSETS_GENERATED_PENDING_INDEPENDENT_HUMAN_REVIEW_NOT_AUTHORIZED",
        "matched-panel manifest status drift",
    )
    for field in (
        "run_eligible",
        "model_execution_authorized",
        "model_execution_performed",
        "scientific_evidence",
    ):
        require(manifest[field] is False, f"matched-panel manifest boundary drift: {field}")
    require(tuple(manifest["ordered_stack_ids"]) == STACKS, "matched-panel manifest stack order drift")
    require(tuple(manifest["ordered_arms"]) == ARMS, "matched-panel manifest arm order drift")
    require(
        manifest["ordering_rule"]
        == "stack order, then H0/H1 arm order, then canonical z=0..6",
        "matched-panel manifest ordering rule drift",
    )
    require(
        manifest["counts"]
        == {
            "stacks": 4,
            "arms_per_stack": 2,
            "canonical_rows_per_arm": 7,
            "panel_records": 56,
            "raw_files": 112,
        },
        "matched-panel manifest counts drift",
    )

    panel_meta = manifest["panel_jsonl"]
    require(
        isinstance(panel_meta, dict)
        and set(panel_meta) == {"byte_length", "relpath", "sha256"},
        "matched-panel JSONL metadata schema drift",
    )
    require(panel_meta["relpath"] == MATCHED_PANEL_JSONL_NAME, "matched-panel JSONL relpath drift")
    expected_panel_sha256 = valid_sha256(panel_meta["sha256"], "matched-panel JSONL hash")
    panel_bytes, panel_jsonl_sha256 = read_regular_snapshot(
        root / MATCHED_PANEL_JSONL_NAME, "matched-panel JSONL"
    )
    require(panel_jsonl_sha256 == expected_panel_sha256, "matched-panel JSONL hash mismatch")
    require(
        type(panel_meta["byte_length"]) is int
        and panel_meta["byte_length"] == len(panel_bytes),
        "matched-panel JSONL byte length mismatch",
    )

    inventory = manifest["raw_file_inventory"]
    require(isinstance(inventory, list) and len(inventory) == 112, "matched-panel raw inventory count drift")
    inventory_bytes: dict[str, bytes] = {}
    inventory_order: list[str] = []
    for index, entry in enumerate(inventory):
        label = f"matched-panel raw inventory[{index}]"
        require(
            isinstance(entry, dict)
            and set(entry) == {"byte_length", "relpath", "sha256"},
            f"{label}: schema drift",
        )
        relpath = entry["relpath"]
        require(relpath not in inventory_bytes, f"{label}: duplicate relpath")
        expected_hash = valid_sha256(entry["sha256"], f"{label}.sha256")
        raw, actual_hash = read_regular_snapshot(
            _safe_bound_bundle_path(root, relpath, label), label
        )
        require(actual_hash == expected_hash, f"{label}: hash mismatch")
        require(
            type(entry["byte_length"]) is int
            and entry["byte_length"] == len(raw),
            f"{label}: byte length mismatch",
        )
        inventory_order.append(relpath)
        inventory_bytes[relpath] = raw
    require(inventory_order == sorted(inventory_order), "matched-panel raw inventory order drift")
    raw_inventory_commitment = sha256_bytes(_asset_canonical_json_bytes(inventory))
    require(
        manifest["raw_inventory_commitment_sha256"] == raw_inventory_commitment,
        "matched-panel raw inventory commitment mismatch",
    )

    wrappers = strict_jsonl_loads(panel_bytes, "matched-panel JSONL")
    require(len(wrappers) == 56, "matched-panel wrapper count drift")
    expected_order = [
        (stack, arm, z) for stack in STACKS for arm in ARMS for z in range(7)
    ]
    records: list[PanelRecord] = []
    canonical_instance_ids: list[str] = []
    generated_paths: set[str] = set()
    record_index: dict[tuple[str, str, int], PanelRecord] = {}
    canonical_index: dict[tuple[str, str, int], str] = {}
    for index, (wrapper, expected_key) in enumerate(zip(wrappers, expected_order)):
        label = f"matched-panel wrapper[{index}]"
        require(
            isinstance(wrapper, dict) and set(wrapper) == MATCHED_PANEL_WRAPPER_KEYS,
            f"{label}: schema drift",
        )
        require(wrapper["schema_version"] == MATCHED_PANEL_WRAPPER_SCHEMA, f"{label}: version drift")
        for field in ("run_eligible", "model_execution_performed", "scientific_evidence"):
            require(wrapper[field] is False, f"{label}: boundary drift for {field}")
        key = (wrapper["mapping_stack_id"], wrapper["arm"], wrapper["canonical_z"])
        require(key == expected_key, f"{label}: order/coverage drift")
        require(wrapper["stack_index"] == STACKS.index(key[0]), f"{label}: stack index drift")
        require(wrapper["arm_index"] == ARMS.index(key[1]), f"{label}: arm index drift")
        require(wrapper["canonical_order_index"] == key[2], f"{label}: canonical index drift")

        source_ref = wrapper["source_original"]
        generated = wrapper["generated"]
        require(
            isinstance(source_ref, dict) and set(source_ref) == MATCHED_PANEL_SOURCE_REF_KEYS,
            f"{label}: source reference schema drift",
        )
        require(
            isinstance(generated, dict) and set(generated) == MATCHED_PANEL_GENERATED_REF_KEYS,
            f"{label}: generated reference schema drift",
        )
        canonical_instance_id = valid_sha256(
            source_ref["lineage_id"], f"{label}.source_original.lineage_id"
        )
        for field in ("prompt_sha256", "row_sha256"):
            valid_sha256(source_ref[field], f"{label}.source_original.{field}")
            valid_sha256(generated[field], f"{label}.generated.{field}")
        row_relpath = generated["row_relpath"]
        prompt_relpath = generated["prompt_relpath"]
        require(
            row_relpath in inventory_bytes and prompt_relpath in inventory_bytes,
            f"{label}: generated file absent from raw inventory",
        )
        require(
            row_relpath not in generated_paths and prompt_relpath not in generated_paths,
            f"{label}: duplicate generated file reference",
        )
        generated_paths.update((row_relpath, prompt_relpath))
        row_bytes = inventory_bytes[row_relpath]
        prompt_bytes = inventory_bytes[prompt_relpath]
        require(sha256_bytes(row_bytes) == generated["row_sha256"], f"{label}: row hash drift")
        require(sha256_bytes(prompt_bytes) == generated["prompt_sha256"], f"{label}: prompt hash drift")
        row = strict_json_loads(row_bytes, f"{label}: raw row")
        require(isinstance(row, dict), f"{label}: raw row is not an object")
        require(row.get("mapping_stack_id") == key[0], f"{label}: raw row stack drift")
        require(row.get("canonical_z") == key[2], f"{label}: raw row z drift")
        require(isinstance(row.get("row_id"), str) and bool(row["row_id"]), f"{label}: raw row id missing")
        require(row.get("target_codebook", row.get("codebook")) == wrapper["target_codebook"], f"{label}: wrapper/raw codebook drift")
        require(
            isinstance(row.get("prompt_text"), str)
            and row["prompt_text"].encode("utf-8") == prompt_bytes,
            f"{label}: raw prompt bytes drift",
        )
        record = PanelRecord(
            stack_id=key[0],
            arm=key[1],
            canonical_z=key[2],
            row_id=row["row_id"],
            row_sha256=generated["row_sha256"],
            prompt_sha256=generated["prompt_sha256"],
            row=row,
            row_bytes=row_bytes,
            prompt_bytes=prompt_bytes,
        )
        records.append(record)
        canonical_instance_ids.append(canonical_instance_id)
        record_index[key] = record
        canonical_index[key] = canonical_instance_id
    require(generated_paths == set(inventory_bytes), "matched-panel raw inventory has unreferenced files")
    for stack in STACKS:
        observed_canonical: set[str] = set()
        for z in range(7):
            h0_key = (stack, ARMS[0], z)
            h1_key = (stack, ARMS[1], z)
            require(
                canonical_index[h0_key] == canonical_index[h1_key],
                f"{stack}/z{z}: H0/H1 canonical source lineage mismatch",
            )
            observed_canonical.add(canonical_index[h0_key])
            require(
                record_index[h0_key].row_id != record_index[h1_key].row_id,
                f"{stack}/z{z}: H0/H1 arm-specific row ids collapsed",
            )
        require(len(observed_canonical) == 7, f"{stack}: canonical source lineages are not unique")

    allowlist_bytes, allowlist_sha256 = read_regular_snapshot(
        root / MATCHED_PANEL_ALLOWLIST_NAME, "matched-panel allowlist receipt"
    )
    require(
        allowlist_sha256 == expected_allowlist_receipt_sha256,
        "matched-panel allowlist receipt external hash mismatch",
    )
    allowlist_receipt = strict_json_loads(
        allowlist_bytes, "matched-panel allowlist receipt"
    )
    require(isinstance(allowlist_receipt, dict), "matched-panel allowlist receipt is not an object")
    return LoadedMatchedPanelBundle(
        records=tuple(records),
        canonical_instance_ids=tuple(canonical_instance_ids),
        manifest_sha256=manifest_sha256,
        panel_jsonl_sha256=panel_jsonl_sha256,
        raw_inventory_commitment_sha256=raw_inventory_commitment,
        allowlist_receipt_sha256=allowlist_sha256,
        allowlist_receipt=allowlist_receipt,
    )


def read_hashed_json(path: Path, expected_sha256: str, label: str) -> tuple[Any, str]:
    valid_sha256(expected_sha256, f"expected {label} hash")
    raw, actual = read_regular_snapshot(path, label)
    require(actual == expected_sha256, f"{label}: external hash mismatch")
    return strict_json_loads(raw, label), actual


def safe_mean(values: Iterable[float], label: str) -> float:
    items = [finite_number(value, label) for value in values]
    require(bool(items), f"{label}: empty mean")
    result = math.fsum(items) / len(items)
    require(math.isfinite(result), f"{label}: non-finite mean")
    return result


def expected_process_ids(variant: str) -> tuple[str, ...]:
    require(variant in VARIANT_REPLICATES, "unknown selected variant")
    return tuple(
        f"{stack}|{replicate}"
        for stack in STACKS
        for replicate in VARIANT_REPLICATES[variant]
    )


def expected_total_counts(variant: str) -> dict[str, int]:
    replicates = len(VARIANT_REPLICATES[variant])
    return {
        "os_processes": 4 * replicates,
        "unique_design_source_updates": 20,
        "technical_update_executions": 20 * replicates,
        "pre_target_vectors": 8 * replicates,
        "pre_target_identity_reads": 48 * replicates,
        "post_target_vectors": 40 * replicates,
        "post_target_identity_reads": 240 * replicates,
        "total_target_identity_metric_cells": 288 * replicates,
    }


def expected_process_counts() -> dict[str, int]:
    return {
        "technical_processes": 1,
        "fresh_reset_calls": 6,
        "source_update_calls": 5,
        "technical_update_executions": 5,
        "unique_design_source_updates": 5,
        "target_read_calls": 12,
        "pre_target_vectors": 2,
        "pre_target_identity_reads": 12,
        "post_target_vectors": 10,
        "post_target_identity_reads": 60,
        "total_target_identity_metric_cells": 72,
        "raw_target_row_traces": 84,
        "raw_candidate_scores": 588,
        "parameter_hash_observations": 35,
        "event_count": 25,
    }


def validate_protocol(protocol: Any, *, synthetic_test_mode: bool) -> None:
    require(isinstance(protocol, dict), "protocol must be an object")
    require(protocol.get("schema_version") == PROTOCOL_SCHEMA, "protocol schema drift")
    require(
        tuple(protocol.get("fixed_source_stacks", {}).get("ordered_stack_ids", []))
        == STACKS,
        "protocol stack coverage/order drift",
    )
    update = protocol.get("fixed_source_update_contract", {})
    require(tuple(update.get("source_identities", [])) == tuple(range(1, 6)), "protocol source identities drift")
    require(update.get("candidate_panel_size") == 7, "protocol candidate panel size drift")
    estimands = protocol.get("potential_outcomes_and_estimands", {})
    require(tuple(estimands.get("target_identities", [])) == TARGET_OFFSETS, "protocol target offsets drift")
    intervention = protocol.get("target_alignment_intervention", {})
    require(tuple(intervention.get("ordered_arms", [])) == ARMS, "protocol arm order drift")
    require(intervention.get("q6_required") is True, "protocol q6 gate disabled")
    numerical = protocol.get("numerical_contract", {})
    require(numerical.get("technical_absolute_tolerance") == TECHNICAL_TOLERANCE, "protocol technical tolerance drift")
    require(numerical.get("directional_dead_zone_epsilon") == DIRECTIONAL_EPSILON, "protocol epsilon drift")
    model = protocol.get("model_contract", {})
    require(model.get("revision") == "a10cc1512eabd3dde888204e902eca88bddb4951", "protocol model revision drift")
    for direction in ("A_TO_B", "B_TO_A"):
        alignments: dict[str, dict[int, int]] = {}
        for arm in ARMS:
            arm_spec = intervention.get(arm, {}).get(direction, {})
            source_a, _ = parse_affine_formula(arm_spec.get("source_codebook"), f"protocol.{arm}.{direction}.source")
            target_a, _ = parse_affine_formula(arm_spec.get("target_codebook"), f"protocol.{arm}.{direction}.target")
            factor = (pow(target_a, -1, 7) * source_a) % 7
            require(arm_spec.get("surface_factor") == factor, f"protocol {arm}/{direction} surface factor drift")
            recorded = arm_spec.get("q_surface_by_r")
            require(isinstance(recorded, dict) and set(recorded) == {str(i) for i in range(1, 6)}, f"protocol {arm}/{direction} q table drift")
            computed = {r: (factor * r) % 7 for r in range(1, 6)}
            require({int(r): q for r, q in recorded.items()} == computed, f"protocol {arm}/{direction} q algebra mismatch")
            alignments[arm] = computed
        for r in range(1, 6):
            require(
                len({r, alignments[ARMS[0]][r], alignments[ARMS[1]][r]}) == 3,
                f"protocol distinctness failed: {direction}/r={r}",
            )
    require(protocol.get("run_eligible") is False, "protocol must remain non-authorizing")
    require(protocol.get("model_execution_authorized") is False, "protocol self-authorizes model execution")
    require(protocol.get("model_execution_performed") is False, "protocol claims model execution")
    require(
        protocol.get("authorization", {}).get("run_r13_target_alignment_pilot") is False,
        "protocol run flag must remain false; authority is external",
    )


def parse_affine_formula(value: Any, label: str) -> tuple[int, int]:
    require(isinstance(value, str), f"{label}: affine formula missing")
    match = re.fullmatch(r"K\(\((\d+)\*z\+(\d+)\) mod 7\)", value)
    require(match is not None, f"{label}: non-canonical affine formula")
    multiplier, intercept = int(match.group(1)), int(match.group(2))
    require(multiplier in range(1, 7) and intercept in range(7), f"{label}: invalid affine coefficients")
    return multiplier, intercept


def codebook_mapping(multiplier: int, intercept: int) -> list[str]:
    return [f"FINAL=K{(multiplier * z + intercept) % 7}" for z in range(7)]


def direction_for_stack(stack_id: str) -> str:
    require(stack_id in STACKS, f"unknown stack: {stack_id}")
    return "A_TO_B" if stack_id.endswith("A_TO_B") else "B_TO_A"


def q_alignment(protocol: dict[str, Any], stack_id: str, arm: str) -> dict[int, int]:
    direction = direction_for_stack(stack_id)
    spec = protocol["target_alignment_intervention"][arm][direction]
    source_a, _ = parse_affine_formula(spec["source_codebook"], "source codebook")
    target_a, _ = parse_affine_formula(spec["target_codebook"], "target codebook")
    factor = (pow(target_a, -1, 7) * source_a) % 7
    output = {r: (factor * r) % 7 for r in range(1, 6)}
    require(all(q in TARGET_OFFSETS for q in output.values()), "q alignment maps to zero")
    return output


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if type(left) is not type(right):
        return [prefix]
    if isinstance(left, dict):
        output: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                output.append(path)
            else:
                output.extend(_diff_paths(left[key], right[key], path))
        return output
    if isinstance(left, list):
        if len(left) != len(right):
            return [prefix]
        output = []
        for index, (lvalue, rvalue) in enumerate(zip(left, right)):
            output.extend(_diff_paths(lvalue, rvalue, f"{prefix}[{index}]"))
        return output
    return [] if left == right else [prefix]


def _normalize_diff_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def _codebook_line(mapping: Sequence[str]) -> str:
    return "CODEBOOK=" + ";".join(
        f"z{z}->{candidate.removeprefix('FINAL=')}"
        for z, candidate in enumerate(mapping)
    )


def validate_panel_row(record: PanelRecord, protocol: dict[str, Any]) -> None:
    require(record.stack_id in STACKS and record.arm in ARMS, "panel record stack/arm invalid")
    require(type(record.canonical_z) is int and record.canonical_z in range(7), "panel canonical_z invalid")
    valid_sha256(record.row_sha256, "panel row hash")
    valid_sha256(record.prompt_sha256, "panel prompt hash")
    require(sha256_bytes(record.row_bytes) == record.row_sha256, "panel row bytes/hash mismatch")
    require(strict_json_loads(record.row_bytes, "panel raw row") == record.row, "panel parsed/raw row mismatch")
    require(sha256_bytes(record.prompt_bytes) == record.prompt_sha256, "panel prompt bytes/hash mismatch")
    row = record.row
    require(row.get("mapping_stack_id") == record.stack_id, "panel row stack mismatch")
    require(row.get("canonical_z") == record.canonical_z, "panel row z mismatch")
    require(row.get("row_id") == record.row_id, "panel record row id mismatch")
    require(tuple(row.get("candidate_order", [])) == CANDIDATES, "panel candidate order drift")
    require(row.get("panel_role") == "CALIBRATION_ONLY", "non-calibration panel row")
    require(row.get("split_role") == "TARGET_CALIBRATION", "panel split role drift")
    direction = direction_for_stack(record.stack_id)
    require(row.get("mirror_role") == direction, "panel direction mismatch")
    codebook = row.get("codebook")
    require(isinstance(codebook, dict), "panel codebook missing")
    spec = protocol["target_alignment_intervention"][record.arm][direction]
    multiplier, intercept = parse_affine_formula(spec["target_codebook"], "protocol target codebook")
    require(codebook.get("multiplier_mod7") == multiplier, "panel multiplier drift")
    require(codebook.get("intercept_mod7") == intercept, "panel intercept drift")
    require(codebook.get("formula") == spec["target_codebook"], "panel formula drift")
    mapping = codebook_mapping(multiplier, intercept)
    require(codebook.get("latent_to_candidate") == mapping, "panel latent mapping drift")
    z = record.canonical_z
    require(row.get("gold_candidate") == mapping[z], "panel gold candidate mismatch")
    for latent_key, candidate_key in (
        ("shared_bug_z", "shared_bug_candidate"),
        ("local_bug_z", "local_bug_candidate"),
    ):
        latent = row.get(latent_key)
        require(type(latent) is int and latent in range(7), f"panel {latent_key} invalid")
        require(row.get(candidate_key) == mapping[latent], f"panel {candidate_key} mismatch")
    records = row.get("candidate_records")
    require(isinstance(records, list) and len(records) == 7, "panel candidate records invalid")
    for index, candidate in enumerate(CANDIDATES):
        item = records[index]
        require(isinstance(item, dict), "panel candidate record is not object")
        require(item.get("candidate") == candidate and item.get("candidate_index") == index, "candidate record order drift")
        require(item.get("is_gold") is (candidate == row["gold_candidate"]), "candidate gold flag drift")
        require(item.get("is_shared_wrong") is (candidate == row["shared_bug_candidate"]), "candidate shared flag drift")
        require(item.get("is_task_local_wrong") is (candidate == row["local_bug_candidate"]), "candidate local flag drift")
    prompt_text = row.get("prompt_text")
    require(isinstance(prompt_text, str), "panel prompt_text missing")
    require(prompt_text.encode("utf-8") == record.prompt_bytes, "row prompt_text/raw prompt mismatch")
    lines = prompt_text.splitlines()
    codebook_lines = [line for line in lines if line.startswith("CODEBOOK=")]
    require(codebook_lines == [_codebook_line(mapping)], "panel CODEBOOK line mismatch")


def validate_matched_panels(
    records: Sequence[PanelRecord], protocol: dict[str, Any]
) -> tuple[dict[tuple[str, str], list[PanelRecord]], dict[tuple[str, str], str]]:
    require(len(records) == 4 * 2 * 7, "panel record count mismatch")
    expected_order = [
        (stack, arm, z) for stack in STACKS for arm in ARMS for z in range(7)
    ]
    require(
        [(record.stack_id, record.arm, record.canonical_z) for record in records]
        == expected_order,
        "panel record order/coverage drift",
    )
    row_ids: set[str] = set()
    by_panel: dict[tuple[str, str], list[PanelRecord]] = {}
    for record in records:
        validate_panel_row(record, protocol)
        require(record.row_id not in row_ids, f"duplicate panel row id: {record.row_id}")
        row_ids.add(record.row_id)
        by_panel.setdefault((record.stack_id, record.arm), []).append(record)
    for stack in STACKS:
        h0 = by_panel[(stack, ARMS[0])]
        h1 = by_panel[(stack, ARMS[1])]
        for left, right in zip(h0, h1):
            require(left.canonical_z == right.canonical_z, "matched-panel z mismatch")
            differences = _diff_paths(left.row, right.row)
            normalized = {_normalize_diff_path(path) for path in differences}
            unexpected = normalized - set(ALLOWED_PANEL_DIFF_PATTERNS)
            require(not unexpected, f"non-allowlisted panel differences: {sorted(unexpected)}")
            left_lines = left.row["prompt_text"].splitlines()
            right_lines = right.row["prompt_text"].splitlines()
            require(len(left_lines) == len(right_lines), "matched prompt line count drift")
            prompt_diffs = [
                index for index, (lvalue, rvalue) in enumerate(zip(left_lines, right_lines))
                if lvalue != rvalue
            ]
            require(
                len(prompt_diffs) == 1
                and left_lines[prompt_diffs[0]].startswith("CODEBOOK=")
                and right_lines[prompt_diffs[0]].startswith("CODEBOOK="),
                "matched prompts differ outside the single CODEBOOK line",
            )
    panel_hashes = {
        key: sha256_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "r13-target-panel-readout-binding-r1",
                    "stack_id": key[0],
                    "arm": key[1],
                    "ordered_rows": [
                        {
                            "row_id": record.row_id,
                            "row_sha256": record.row_sha256,
                            "prompt_sha256": record.prompt_sha256,
                        }
                        for record in value
                    ],
                }
            )
        )
        for key, value in by_panel.items()
    }
    return by_panel, panel_hashes


def validate_bindings(bindings: Any, expected: dict[str, str], label: str) -> None:
    require(isinstance(bindings, dict) and set(bindings) == set(BINDING_KEYS), f"{label}: binding schema drift")
    require(set(expected) == set(BINDING_KEYS), f"{label}: expected binding schema drift")
    for key in BINDING_KEYS:
        valid_sha256(bindings[key], f"{label}.{key}")
        require(bindings[key] == expected[key], f"{label}: binding mismatch for {key}")


def validate_allowlist_receipt(
    receipt: Any,
    *,
    expected_protocol_sha256: str,
    expected_panel_bundle_sha256: str,
    synthetic_test_mode: bool,
) -> None:
    require(isinstance(receipt, dict) and set(receipt) == ALLOWLIST_RECEIPT_KEYS, "allowlist receipt schema drift")
    require(receipt["schema_version"] == ALLOWLIST_RECEIPT_SCHEMA, "allowlist receipt version drift")
    bindings = receipt["bindings"]
    require(isinstance(bindings, dict) and set(bindings) == ALLOWLIST_BINDING_KEYS, "allowlist binding schema drift")
    for key, value in bindings.items():
        valid_sha256(value, f"allowlist.bindings.{key}")
    require(bindings["protocol_sha256"] == expected_protocol_sha256, "allowlist receipt/protocol mismatch")
    require(bindings["manifest_sha256"] == expected_panel_bundle_sha256, "allowlist receipt/manifest mismatch")
    implementation = receipt["implementation_bindings"]
    require(
        isinstance(implementation, dict)
        and set(implementation) == ALLOWLIST_IMPLEMENTATION_BINDING_KEYS,
        "allowlist implementation-binding schema drift",
    )
    implementation_root = Path(__file__).resolve().parent
    for role in ("generator", "validator"):
        relpath_key = f"{role}_relpath"
        sha256_key = f"{role}_sha256"
        expected_relpath = ALLOWLIST_IMPLEMENTATION_RELPATHS[role]
        require(
            implementation[relpath_key] == expected_relpath,
            f"allowlist {role} relpath drift",
        )
        expected_hash = valid_sha256(
            implementation[sha256_key], f"allowlist.implementation_bindings.{sha256_key}"
        )
        _, actual_hash = read_regular_snapshot(
            implementation_root / expected_relpath,
            f"allowlist-bound {role} implementation",
        )
        require(
            actual_hash == expected_hash,
            f"allowlist {role} implementation hash mismatch",
        )
    require(receipt["validated_pairs"] == 28, "allowlist receipt pair count drift")
    require(receipt["validated_rows"] == 56 and receipt["validated_raw_files"] == 112, "allowlist receipt asset counts drift")
    require(receipt["validated_stacks"] == 4, "allowlist receipt stack count drift")
    require(receipt["verdict"] == "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY", "allowlist validation did not pass")
    require(receipt["model_actions"] == 0, "allowlist receipt reports model action")
    require(receipt["model_execution_authorized"] is False, "allowlist receipt grants model authority")
    require(receipt["model_execution_performed"] is False, "allowlist receipt reports model execution")
    require(receipt["run_eligible"] is False and receipt["scientific_evidence"] is False, "allowlist boundary washing")
    require(receipt["human_review_completed"] is False, "machine receipt pretends human review")
    require(isinstance(receipt["authorization_boundary"], str) and bool(receipt["authorization_boundary"]), "allowlist boundary missing")


def validate_human_receipt(
    receipt: Any,
    *,
    expected_protocol_sha256: str,
    expected_panel_bundle_sha256: str,
    expected_allowlist_receipt_sha256: str,
    synthetic_test_mode: bool,
    trusted_policy: ExternalTrustPolicy | None,
    authorization_issued_at: datetime,
    result_created_at: datetime,
) -> datetime | None:
    expected_keys = (
        HUMAN_RECEIPT_KEYS
        if synthetic_test_mode
        else HUMAN_RECEIPT_KEYS | {"protocol_sha256", "allowlist_receipt_sha256"}
    )
    require(isinstance(receipt, dict) and set(receipt) == expected_keys, "human-review receipt schema drift")
    bindings = receipt["asset_bindings"]
    require(isinstance(bindings, dict) and set(bindings) == HUMAN_ASSET_BINDING_KEYS, "human receipt asset-binding drift")
    for key in ("manifest_sha256", "panel_jsonl_sha256", "raw_inventory_commitment_sha256"):
        valid_sha256(bindings[key], f"human_receipt.asset_bindings.{key}")
    require(bindings["manifest_sha256"] == expected_panel_bundle_sha256, "human receipt/manifest mismatch")
    require(bindings["manifest_relpath"] == "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json", "human manifest relpath drift")
    require(bindings["panel_jsonl_relpath"] == "R13_MATCHED_TARGET_PANELS_R1.jsonl", "human panel relpath drift")
    require(receipt["required_checks"] == list(HUMAN_REQUIRED_CHECKS), "human required-check list drift")
    require(receipt["completion_rule"] == HUMAN_COMPLETION_RULE, "human completion rule drift")
    if synthetic_test_mode:
        require(receipt["schema_version"] == HUMAN_RECEIPT_TEMPLATE_SCHEMA, "synthetic human template version drift")
        require(receipt["status"] == "UNCOMPLETED_TEMPLATE_NOT_A_REVIEW_RECEIPT", "synthetic human receipt status drift")
        require(receipt["template_only"] is True and receipt["review_completed"] is False and receipt["decision"] is None, "synthetic fixture pretends human review")
        require(
            all(
                receipt[key] is None
                for key in (
                    "review_method",
                    "review_notes",
                    "reviewed_at_utc",
                    "reviewer_affiliation_or_role",
                    "reviewer_name",
                    "signature",
                    "signature_algorithm",
                    "signature_key_id",
                )
            ),
            "synthetic human receipt contains fake reviewer material",
        )
        return None
    if trusted_policy is None:
        raise R13AuthorizationError(
            "BLOCKED_EXTERNAL_TRUST_VERIFIER_NOT_PROVIDED: human-review signature cannot be verified"
        )
    require(receipt["schema_version"] == HUMAN_RECEIPT_SCHEMA, "human-review receipt version drift")
    require(receipt["protocol_sha256"] == expected_protocol_sha256, "human receipt/protocol mismatch")
    require(receipt["allowlist_receipt_sha256"] == expected_allowlist_receipt_sha256, "human receipt/machine receipt mismatch")
    require(receipt["status"] == HUMAN_RECEIPT_STATUS, "human review status incomplete")
    require(receipt["template_only"] is False and receipt["review_completed"] is True and receipt["decision"] == "PASS", "human review did not pass")
    require(isinstance(receipt["reviewer_name"], str) and bool(receipt["reviewer_name"]), "human reviewer name missing")
    require(isinstance(receipt["reviewer_affiliation_or_role"], str) and bool(receipt["reviewer_affiliation_or_role"]), "human reviewer role missing")
    require(isinstance(receipt["review_method"], str) and bool(receipt["review_method"]), "human review method missing")
    require(isinstance(receipt["review_notes"], str) and bool(receipt["review_notes"]), "human review notes missing")
    reviewed_at = parse_utc(receipt["reviewed_at_utc"], "human review timestamp")
    require(
        reviewed_at <= authorization_issued_at,
        "human review postdates authorization issue time",
    )
    require(
        reviewed_at <= result_created_at,
        "human review postdates result creation time",
    )
    require(
        authorization_issued_at - reviewed_at <= MAX_HUMAN_REVIEW_AGE,
        "human review is stale at authorization issue time",
    )
    require(isinstance(receipt["signature"], str) and bool(receipt["signature"]), "human review signature missing")
    require(isinstance(receipt["signature_algorithm"], str) and bool(receipt["signature_algorithm"]), "human signature algorithm missing")
    require(isinstance(receipt["signature_key_id"], str) and bool(receipt["signature_key_id"]), "human signature key id missing")
    human_payload = {key: value for key, value in receipt.items() if key != "signature"}
    human_message = HUMAN_REVIEW_CONTEXT + canonical_json_bytes(human_payload)
    require(
        trusted_policy.verify_human_review_signature(
            human_message,
            receipt["signature"],
            receipt["signature_algorithm"],
            receipt["signature_key_id"],
        ),
        "human-review signature verification failed",
    )
    return reviewed_at


def validate_authorization(
    authorization: Any,
    *,
    expected_bindings: dict[str, str],
    run_id: str,
    selected_variant: str,
    ordered_process_ids: Sequence[str],
    created_at: datetime,
    synthetic_test_mode: bool,
    trusted_policy: ExternalTrustPolicy | None,
) -> tuple[datetime, datetime]:
    require(isinstance(authorization, dict) and set(authorization) == AUTHORIZATION_KEYS, "authorization schema drift")
    require(authorization["schema_version"] == AUTHORIZATION_SCHEMA, "authorization version drift")
    valid_uuid4(authorization["authorization_id"], "authorization id")
    require(authorization["run_id"] == run_id, "authorization run id mismatch")
    valid_uuid4(authorization["run_id"], "authorized run id")
    issued = parse_utc(authorization["issued_at_utc"], "authorization issue time")
    expires = parse_utc(authorization["expires_at_utc"], "authorization expiry")
    require(issued <= created_at < expires and issued < expires, "result outside authorization time window")
    require((expires - issued).total_seconds() <= 24 * 60 * 60, "authorization TTL exceeds 24 hours")
    require(authorization["selected_variant"] == selected_variant, "authorization variant mismatch")
    require(authorization["ordered_process_ids"] == list(ordered_process_ids), "authorization process plan mismatch")
    bound = authorization["bindings"]
    require(
        isinstance(bound, dict) and set(bound) == set(AUTH_BOUND_BINDING_KEYS),
        "authorization binding schema drift",
    )
    for key in AUTH_BOUND_BINDING_KEYS:
        valid_sha256(bound[key], f"authorization.bindings.{key}")
        require(bound[key] == expected_bindings[key], f"authorization binding mismatch: {key}")
    require(authorization["single_use"] is True, "authorization is not single-use")
    require(isinstance(authorization["forbidden_operations"], list), "authorization forbidden-operation list missing")
    required_forbidden = {
        "adaptive_retry",
        "outcome_dependent_rerun",
        "hidden_audit",
        "sampled_rlvr",
        "formal_or_confirmatory_claim",
    }
    require(set(authorization["forbidden_operations"]) == required_forbidden, "authorization forbidden-operation drift")
    valid_sha256(authorization["trusted_signer_key_fingerprint_sha256"], "trusted signer fingerprint")
    require(isinstance(authorization["trusted_signer_id"], str) and bool(authorization["trusted_signer_id"]), "trusted signer id missing")
    require(isinstance(authorization["signature_algorithm"], str) and bool(authorization["signature_algorithm"]), "authorization signature algorithm missing")
    require(isinstance(authorization["signature"], str) and bool(authorization["signature"]), "authorization signature missing")
    if synthetic_test_mode:
        require(authorization["synthetic_fixture"] is True, "synthetic authorization marker missing")
        require(authorization["status"] == "SYNTHETIC_TEST_ONLY_NO_MODEL_AUTHORITY", "synthetic authorization status drift")
        require(authorization["model_execution_authorized"] is False, "synthetic authorization permits model execution")
        require(authorization["allowed_operations"] == ["cpu_static_synthetic_validation"], "synthetic allowed-operation drift")
        return issued, expires
    if trusted_policy is None:
        raise R13AuthorizationError(
            "BLOCKED_EXTERNAL_TRUST_VERIFIER_NOT_PROVIDED: authorization signature and replay state cannot be verified"
        )
    require(authorization["synthetic_fixture"] is False, "production authorization marked synthetic")
    require(authorization["status"] == "AUTHORIZED_BEFORE_MODEL_LOAD", "model execution not authorized before load")
    require(authorization["model_execution_authorized"] is True, "authorization denies model execution")
    require(
        authorization["allowed_operations"]
        == [
            "tokenizer_load",
            "model_weight_load",
            "model_forward",
            "gradient",
            "optimizer_step",
            "run_r13_target_alignment_pilot",
        ],
        "authorization allowed-operation drift",
    )
    require(
        authorization["trusted_signer_id"] == trusted_policy.authorization_signer_id
        and authorization["trusted_signer_key_fingerprint_sha256"]
        == trusted_policy.authorization_key_fingerprint_sha256
        and authorization["signature_algorithm"]
        == trusted_policy.authorization_signature_algorithm,
        "authorization signer policy mismatch",
    )
    authorization_payload = {
        key: value for key, value in authorization.items() if key != "signature"
    }
    authorization_message = (
        b"RLVR-R13-EXTERNAL-RUN-AUTHORIZATION-V1\x00"
        + canonical_json_bytes(authorization_payload)
    )
    require(
        trusted_policy.verify_authorization_signature(
            authorization_message, authorization["signature"]
        ),
        "authorization signature verification failed",
    )
    require(
        trusted_policy.authorization_nonce_is_unconsumed(
            authorization["authorization_id"], authorization["run_id"]
        ),
        "authorization replay/nonce gate failed",
    )
    return issued, expires


def _target_candidate(row: dict[str, Any], q: int) -> str:
    require(q in TARGET_OFFSETS, f"target offset outside frozen q=1..6 grid: {q}")
    z = row["canonical_z"]
    mapping = row["codebook"]["latent_to_candidate"]
    candidate = mapping[(z + q) % 7]
    require(candidate in CANDIDATES, "target candidate outside candidate panel")
    return candidate


def validate_trace_readout(
    readout: Any,
    *,
    expected_phase: str,
    expected_ordinal: int,
    expected_arm: str,
    expected_source_identity: str | None,
    expected_parameter_hash: str,
    expected_panel_hash: str,
    panel_records: Sequence[PanelRecord],
    label: str,
) -> dict[str, dict[str, Any]]:
    require(isinstance(readout, dict) and set(readout) == READOUT_KEYS, f"{label}: readout schema drift")
    require(readout["readout_ordinal"] == expected_ordinal, f"{label}: readout ordinal drift")
    require(readout["phase"] == expected_phase, f"{label}: phase mismatch")
    require(readout["arm"] == expected_arm, f"{label}: arm mismatch")
    require(readout["source_rule_identity"] == expected_source_identity, f"{label}: source identity mismatch")
    require(readout["target_panel_sha256"] == expected_panel_hash, f"{label}: target panel hash mismatch")
    require(readout["parameter_hash_before"] == expected_parameter_hash, f"{label}: parameter hash before readout drift")
    require(readout["parameter_hash_after"] == expected_parameter_hash, f"{label}: readout mutated parameters")
    valid_sha256(expected_parameter_hash, f"{label}: expected parameter hash")
    traces = readout["row_traces"]
    require(isinstance(traces, list) and len(traces) == 7, f"{label}: expected seven raw row traces")
    output: dict[str, dict[str, Any]] = {}
    for index, (trace, record) in enumerate(zip(traces, panel_records)):
        trace_label = f"{label}.row_traces[{index}]"
        require(isinstance(trace, dict) and set(trace) == TRACE_KEYS, f"{trace_label}: schema drift")
        require(trace["row_id"] == record.row_id, f"{trace_label}: row order/id mismatch")
        require(trace["panel_row_sha256"] == record.row_sha256, f"{trace_label}: row hash mismatch")
        require(trace["state"] == f"{expected_phase}_TARGET", f"{trace_label}: state mismatch")
        require(trace["arm"] == expected_arm, f"{trace_label}: arm mismatch")
        require(trace["source_rule_identity"] == expected_source_identity, f"{trace_label}: source identity mismatch")
        require(trace["source_update_hash"] == expected_parameter_hash, f"{trace_label}: update hash mismatch")
        scores = trace["ordered_candidate_scores"]
        require(isinstance(scores, list) and len(scores) == 7, f"{trace_label}: seven candidate scores required")
        for score_index, score in enumerate(scores):
            finite_number(score, f"{trace_label}.scores[{score_index}]")
        require(trace["row_id"] not in output, f"{label}: duplicate trace row id")
        output[trace["row_id"]] = trace
    require(len(output) == 7, f"{label}: trace coverage mismatch")
    return output


def trace_metric(
    traces: dict[str, dict[str, Any]],
    panel_records: Sequence[PanelRecord],
    q: int,
    label: str,
) -> float:
    candidate_index = {candidate: index for index, candidate in enumerate(CANDIDATES)}
    values: list[float] = []
    for record in panel_records:
        trace = traces[record.row_id]
        scores = trace["ordered_candidate_scores"]
        wrong = _target_candidate(record.row, q)
        gold = record.row["gold_candidate"]
        values.append(
            finite_number(scores[candidate_index[wrong]], f"{label}.{record.row_id}.q{q}")
            - finite_number(scores[candidate_index[gold]], f"{label}.{record.row_id}.gold")
        )
    return safe_mean(values, f"{label}.q{q}")


def double_center(
    matrix: dict[int, dict[int, float]]
) -> dict[int, dict[int, float]]:
    require(set(matrix) == set(range(1, 6)), "effect matrix source coverage mismatch")
    require(all(set(matrix[r]) == set(TARGET_OFFSETS) for r in range(1, 6)), "effect matrix q coverage mismatch")
    row_means = {
        r: safe_mean((matrix[r][q] for q in TARGET_OFFSETS), f"row mean r={r}")
        for r in range(1, 6)
    }
    column_means = {
        q: safe_mean((matrix[r][q] for r in range(1, 6)), f"column mean q={q}")
        for q in TARGET_OFFSETS
    }
    grand = safe_mean(row_means.values(), "grand mean")
    return {
        r: {
            q: matrix[r][q] - row_means[r] - column_means[q] + grand
            for q in TARGET_OFFSETS
        }
        for r in range(1, 6)
    }


def _matrix_max_difference(
    left: dict[int, dict[int, float]],
    right: dict[int, dict[int, float]],
) -> float:
    return max(
        abs(left[r][q] - right[r][q])
        for r in range(1, 6)
        for q in TARGET_OFFSETS
    )


def _runner_json_bytes(value: Any) -> bytes:
    assert_finite(value, "runner-bound value")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise R13ValidationError("runner-bound value is not strict JSON") from exc


def expected_source_update_call_sha256(
    stack_id: str, identity: str, bindings: dict[str, str]
) -> str:
    seed_material = sha256_bytes(
        _runner_json_bytes(
            {
                "protocol_sha256": bindings["protocol_sha256"],
                "source_assets_sha256": bindings["source_assets_sha256"],
                "stack_id": stack_id,
                "source_rule_identity": identity,
            }
        )
    )
    return sha256_bytes(
        _runner_json_bytes(
            {
                "stack_id": stack_id,
                "source_rule_identity": identity,
                "source_assets_sha256": bindings["source_assets_sha256"],
                "protocol_sha256": bindings["protocol_sha256"],
                "source_seed_material_sha256": seed_material,
            }
        )
    )


def _validate_target_blind_diagnostics(value: Any, path: str = "diagnostics") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            require(isinstance(key, str), f"{path}: non-string diagnostic key")
            lowered = key.lower()
            require(
                not any(token in lowered for token in ("arm", "target", "panel")),
                f"{path}: target information leaked through diagnostic key {key}",
            )
            _validate_target_blind_diagnostics(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_target_blind_diagnostics(child, f"{path}[{index}]")
        return
    assert_finite(value, path)


def validate_backend_update_receipt(value: Any, label: str) -> None:
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "optimizer_step_count",
            "clipping_triggered",
            "non_finite_observed",
            "diagnostics",
        },
        f"{label}: backend receipt schema drift",
    )
    require(value["optimizer_step_count"] == 1, f"{label}: backend optimizer-step drift")
    require(value["clipping_triggered"] is False, f"{label}: realized clipping")
    require(value["non_finite_observed"] is False, f"{label}: backend non-finite computation")
    require(isinstance(value["diagnostics"], dict), f"{label}: diagnostics must be an object")
    _validate_target_blind_diagnostics(value["diagnostics"], f"{label}.diagnostics")


def validate_process_events(
    process: dict[str, Any],
    label: str,
    *,
    authorization_window: tuple[datetime, datetime],
    result_created_at: datetime,
) -> None:
    base_keys = {"event_index", "event_id", "event_type", "at_utc"}
    details: list[tuple[str, dict[str, Any]]] = [
        (
            "RUN_STARTED",
            {
                "replicate_id": process["replicate_id"],
                "stack_id": process["stack_id"],
                "arm_evaluation_order": process["arm_evaluation_order"],
            },
        ),
        (
            "FRESH_RESET_VERIFIED",
            {
                "phase": "PRE",
                "execution_key": None,
                "parameter_hash": process["initial_parameter_hash"],
            },
        ),
    ]
    for readout in process["pre_target_readouts"]:
        details.append(
            (
                "PRE_TARGET_READOUT_VERIFIED",
                {
                    "execution_key": None,
                    "readout_ordinal": readout["readout_ordinal"],
                    "arm": readout["arm"],
                    "source_rule_identity": None,
                    "parameter_hash": process["initial_parameter_hash"],
                },
            )
        )
    for execution in process["source_update_executions"]:
        details.append(
            (
                "FRESH_RESET_VERIFIED",
                {
                    "phase": "POST",
                    "execution_key": execution["execution_key"],
                    "parameter_hash": process["initial_parameter_hash"],
                },
            )
        )
        details.append(
            (
                "SOURCE_UPDATE_VERIFIED",
                {
                    "execution_key": execution["execution_key"],
                    "execution_ordinal": execution["execution_ordinal"],
                    "source_rule_identity": execution["source_rule_identity"],
                    "source_update_call_sha256": execution["source_update_call_sha256"],
                    "update_parameter_hash": execution["update_parameter_hash"],
                    "optimizer_step_count": 1,
                },
            )
        )
        for readout in execution["readouts"]:
            details.append(
                (
                    "POST_TARGET_READOUT_VERIFIED",
                    {
                        "execution_key": execution["execution_key"],
                        "readout_ordinal": readout["readout_ordinal"],
                        "arm": readout["arm"],
                        "source_rule_identity": execution["source_rule_identity"],
                        "parameter_hash": execution["update_parameter_hash"],
                    },
                )
            )
    details.append(("RUN_COMPLETED", {"counts": process["counts"]}))
    events = process["events"]
    require(isinstance(events, list) and len(events) == 25 == len(details), f"{label}: event count drift")
    previous: datetime | None = None
    for index, (event, (event_type, expected_details)) in enumerate(zip(events, details), 1):
        require(isinstance(event, dict), f"{label}.events[{index - 1}]: not an object")
        require(set(event) == base_keys | set(expected_details), f"{label}.events[{index - 1}]: schema drift")
        require(event["event_index"] == index, f"{label}: event index drift")
        require(event["event_id"] == f"{process['run_id']}|event|{index:04d}", f"{label}: event id drift")
        require(event["event_type"] == event_type, f"{label}: event type/order drift")
        for key, expected in expected_details.items():
            require(event[key] == expected, f"{label}: event detail drift for {event_type}.{key}")
        observed = parse_utc(event["at_utc"], f"{label}.events[{index - 1}].at_utc")
        require(
            authorization_window[0] <= observed < authorization_window[1],
            f"{label}: event outside authorization time window",
        )
        require(
            observed <= result_created_at,
            f"{label}: event postdates result creation time",
        )
        if previous is not None:
            require(observed >= previous, f"{label}: event clock moved backwards")
        previous = observed
    require(process["created_at_utc"] == events[-1]["at_utc"], f"{label}: completion timestamp mismatch")


def validate_process(
    process: Any,
    *,
    expected_process_id: str,
    expected_bundle_run_id: str,
    expected_bindings: dict[str, str],
    protocol: dict[str, Any],
    panels: dict[tuple[str, str], list[PanelRecord]],
    panel_hashes: dict[tuple[str, str], str],
    authorization_window: tuple[datetime, datetime],
    result_created_at: datetime,
    global_event_ids: set[str],
    global_process_run_ids: set[str],
) -> dict[str, Any]:
    require(isinstance(process, dict) and set(process) == PROCESS_KEYS, f"{expected_process_id}: process schema drift")
    require(process["schema_version"] == PROCESS_SCHEMA, f"{expected_process_id}: process version drift")
    stack_id, replicate = expected_process_id.split("|")
    require(process["stack_id"] == stack_id and process["replicate_id"] == replicate, f"{expected_process_id}: stack/replicate mismatch")
    require(process["status"] == "R13_STATIC_CORE_TRACE_COMPLETE_NOT_SCIENTIFIC_RESULT", f"{expected_process_id}: process status drift")
    require(process["bundle_run_id"] == expected_bundle_run_id, f"{expected_process_id}: bundle run id mismatch")
    process_run_id = process["run_id"]
    require(isinstance(process_run_id, str) and SAFE_ID_RE.fullmatch(process_run_id) is not None, f"{expected_process_id}: invalid process run id")
    require(process_run_id not in global_process_run_ids, f"{expected_process_id}: duplicate process run id")
    global_process_run_ids.add(process_run_id)
    parse_utc(process["created_at_utc"], f"{expected_process_id}.created_at_utc")
    for field, expected in (
        ("static_core_only", True),
        ("model_execution_authorized_by_core", False),
        ("scientific_evidence", False),
        ("formal_experiment", False),
        ("development_only", True),
    ):
        require(process[field] is expected, f"{expected_process_id}: boundary drift for {field}")
    order = REPLICATE_ARM_ORDER[replicate]
    require(tuple(process["arm_evaluation_order"]) == order, f"{expected_process_id}: target-arm order drift")
    validate_bindings(process["bindings"], expected_bindings, f"{expected_process_id}.bindings")
    require(tuple(process["ordered_candidate_set"]) == CANDIDATES, f"{expected_process_id}: candidate order drift")
    initial_hash = valid_sha256(process["initial_parameter_hash"], f"{expected_process_id}.initial_parameter_hash")
    require(
        isinstance(process["counts"], dict)
        and set(process["counts"]) == PROCESS_COUNT_KEYS
        and process["counts"] == expected_process_counts(),
        f"{expected_process_id}: process counts do not close",
    )

    pre_readouts = process["pre_target_readouts"]
    require(isinstance(pre_readouts, list) and len(pre_readouts) == 2, f"{expected_process_id}: pre readout count drift")
    pre_metrics: dict[str, dict[int, float]] = {}
    for ordinal, (arm, readout) in enumerate(zip(order, pre_readouts), 1):
        trace_map = validate_trace_readout(
            readout,
            expected_phase="PRE",
            expected_ordinal=ordinal,
            expected_arm=arm,
            expected_source_identity=None,
            expected_parameter_hash=initial_hash,
            expected_panel_hash=panel_hashes[(stack_id, arm)],
            panel_records=panels[(stack_id, arm)],
            label=f"{expected_process_id}.pre.{arm}",
        )
        pre_metrics[arm] = {
            q: trace_metric(trace_map, panels[(stack_id, arm)], q, f"{expected_process_id}.pre.{arm}")
            for q in TARGET_OFFSETS
        }

    executions = process["source_update_executions"]
    require(isinstance(executions, list) and len(executions) == 5, f"{expected_process_id}: source execution count drift")
    effects: dict[str, dict[int, dict[int, float]]] = {
        arm: {} for arm in ARMS
    }
    update_hashes: dict[int, str] = {}
    event_rows: list[dict[str, Any]] = []
    readout_ordinal = 2
    for r, execution in enumerate(executions, 1):
        identity = f"Z7_PLUS{r}"
        label = f"{expected_process_id}.execution.{identity}"
        require(isinstance(execution, dict) and set(execution) == EXECUTION_KEYS, f"{label}: schema drift")
        event_id = execution["execution_event_id"]
        require(isinstance(event_id, str) and event_id == f"{process_run_id}|event|{(6 + (r - 1) * 4):04d}", f"{label}: execution event id drift")
        require(event_id not in global_event_ids, f"{label}: duplicate execution event id")
        global_event_ids.add(event_id)
        require(execution["execution_ordinal"] == r, f"{label}: execution ordinal drift")
        expected_key = f"{replicate}|{stack_id}|{identity}"
        require(execution["execution_key"] == expected_key, f"{label}: execution key drift")
        require(execution["source_rule_identity"] == identity, f"{label}: source identity drift")
        require(execution["initial_parameter_hash_after_reset"] == initial_hash, f"{label}: reset hash drift")
        update_hash = valid_sha256(execution["update_parameter_hash"], f"{label}.update_parameter_hash")
        require(update_hash != initial_hash, f"{label}: update did not change parameter hash")
        observed_call_hash = valid_sha256(execution["source_update_call_sha256"], f"{label}.source_update_call_sha256")
        require(
            observed_call_hash
            == expected_source_update_call_sha256(stack_id, identity, expected_bindings),
            f"{label}: source-update call is not the frozen target-blind input",
        )
        require(execution["optimizer_step_count"] == 1, f"{label}: optimizer-step count drift")
        require(execution["readout_gradient_count"] == 0, f"{label}: gradient occurred during readout")
        require(execution["readout_optimizer_step_count"] == 0, f"{label}: optimizer action occurred during readout")
        require(execution["readout_state_mutation_count"] == 0, f"{label}: state mutation occurred during readout")
        validate_backend_update_receipt(execution["backend_update_receipt"], label)
        update_hashes[r] = update_hash
        readouts = execution["readouts"]
        require(isinstance(readouts, list) and len(readouts) == 2, f"{label}: post readout count drift")
        for arm, readout in zip(order, readouts):
            readout_ordinal += 1
            trace_map = validate_trace_readout(
                readout,
                expected_phase="POST",
                expected_ordinal=readout_ordinal,
                expected_arm=arm,
                expected_source_identity=identity,
                expected_parameter_hash=update_hash,
                expected_panel_hash=panel_hashes[(stack_id, arm)],
                panel_records=panels[(stack_id, arm)],
                label=f"{label}.post.{arm}",
            )
            post_metrics = {
                q: trace_metric(trace_map, panels[(stack_id, arm)], q, f"{label}.post.{arm}")
                for q in TARGET_OFFSETS
            }
            effects[arm][r] = {
                q: post_metrics[q] - pre_metrics[arm][q]
                for q in TARGET_OFFSETS
            }
        event_rows.append(
            {
                "execution_event_id": event_id,
                "process_id": expected_process_id,
                "execution_key": expected_key,
                "source_rule_identity": identity,
                "update_parameter_hash": update_hash,
            }
        )
    require(len(set(update_hashes.values())) == 5, f"{expected_process_id}: distinct source updates collapsed")
    require(readout_ordinal == 12, f"{expected_process_id}: readout ordinal closure failed")
    validate_process_events(
        process,
        expected_process_id,
        authorization_window=authorization_window,
        result_created_at=result_created_at,
    )

    residuals = {arm: double_center(effects[arm]) for arm in ARMS}
    alignments = {arm: q_alignment(protocol, stack_id, arm) for arm in ARMS}
    g: dict[str, dict[int, float]] = {arm: {} for arm in ARMS}
    c: dict[str, dict[int, float]] = {arm: {} for arm in ARMS}
    f: dict[int, float] = {}
    for r in range(1, 6):
        q0 = alignments[ARMS[0]][r]
        q1 = alignments[ARMS[1]][r]
        require(len({r, q0, q1}) == 3, f"{expected_process_id}: q distinctness failed for r={r}")
        for arm in ARMS:
            qh = alignments[arm][r]
            g[arm][r] = residuals[arm][r][qh] - residuals[arm][r][r]
            c[arm][r] = -g[arm][r]
        f[r] = (
            residuals[ARMS[1]][r][q1]
            - residuals[ARMS[1]][r][q0]
            - residuals[ARMS[0]][r][q1]
            + residuals[ARMS[0]][r][q0]
        )
    assert_finite({"E": effects, "R": residuals, "G": g, "F": f, "C": c}, expected_process_id)
    return {
        "stack_id": stack_id,
        "replicate_id": replicate,
        "initial_parameter_hash": initial_hash,
        "update_hashes": update_hashes,
        "E": effects,
        "R": residuals,
        "G": g,
        "F": f,
        "C": c,
        "events": event_rows,
    }


def _average_nested_by_rep(
    derived_by_rep: dict[str, dict[str, Any]],
    field: str,
) -> Any:
    replicates = tuple(derived_by_rep)
    first = derived_by_rep[replicates[0]][field]

    def recurse(path: tuple[Any, ...], template: Any) -> Any:
        if isinstance(template, dict):
            return {
                key: recurse(path + (key,), template[key])
                for key in template
            }
        values = []
        for replicate in replicates:
            value = derived_by_rep[replicate][field]
            for key in path:
                value = value[key]
            values.append(value)
        return safe_mean(values, f"replicate average {field}{path}")

    return recurse((), first)


def summarize_results(
    derived: dict[str, dict[str, dict[str, Any]]],
    *,
    selected_variant: str,
    synthetic_fixture: bool,
    model_execution_performed: bool,
) -> dict[str, Any]:
    replicates = VARIANT_REPLICATES[selected_variant]
    ab_max_by_stack_and_arm: dict[str, dict[str, float | None]] = {}
    averaged: dict[str, dict[str, Any]] = {}
    for stack in STACKS:
        by_rep = derived[stack]
        if replicates == ("A", "B"):
            require(
                by_rep["A"]["initial_parameter_hash"]
                == by_rep["B"]["initial_parameter_hash"],
                f"{stack}: A/B initial parameter hash mismatch",
            )
            require(
                by_rep["A"]["update_hashes"] == by_rep["B"]["update_hashes"],
                f"{stack}: A/B source update hashes mismatch",
            )
            arm_differences = {
                arm: _matrix_max_difference(by_rep["A"]["E"][arm], by_rep["B"]["E"][arm])
                for arm in ARMS
            }
            for arm, maximum in arm_differences.items():
                require(maximum <= TECHNICAL_TOLERANCE, f"{stack}/{arm}: A/B E matrix mismatch {maximum}")
            ab_max_by_stack_and_arm[stack] = arm_differences
        else:
            ab_max_by_stack_and_arm[stack] = {arm: None for arm in ARMS}
        averaged[stack] = {
            field: _average_nested_by_rep(by_rep, field)
            for field in ("E", "R", "G", "F", "C")
        }

    by_stack: dict[str, Any] = {}
    all_stack_pass = True
    any_dead_zone = False
    for stack in STACKS:
        f_values = averaged[stack]["F"]
        g_values = averaged[stack]["G"]
        mean_f = safe_mean(f_values.values(), f"{stack}.mean_F")
        mean_g = {
            arm: safe_mean(g_values[arm].values(), f"{stack}.mean_G.{arm}")
            for arm in ARMS
        }
        stack_pass = (
            mean_f > DIRECTIONAL_EPSILON
            and all(value > DIRECTIONAL_EPSILON for value in mean_g.values())
        )
        all_stack_pass = all_stack_pass and stack_pass
        any_dead_zone = any_dead_zone or any(
            abs(value) <= DIRECTIONAL_EPSILON
            for value in (mean_f, *mean_g.values())
        )
        by_stack[stack] = {
            "mean_F": mean_f,
            "mean_G": mean_g,
            "stack_gate": "PASS" if stack_pass else "FAIL",
            "F_by_source_identity": {f"Z7_PLUS{r}": f_values[r] for r in range(1, 6)},
            "G_by_arm_and_source_identity": {
                arm: {f"Z7_PLUS{r}": g_values[arm][r] for r in range(1, 6)}
                for arm in ARMS
            },
            "C_by_arm_and_source_identity": {
                arm: {f"Z7_PLUS{r}": averaged[stack]["C"][arm][r] for r in range(1, 6)}
                for arm in ARMS
            },
            "ab_max_abs_E_difference_by_arm": ab_max_by_stack_and_arm[stack],
        }

    task_pairs = {
        task_pair: [stack for stack in STACKS if stack.startswith(task_pair)]
        for task_pair in ("TP1", "TP2")
    }
    by_task_pair = {
        task_pair: {
            "mean_F": safe_mean((by_stack[stack]["mean_F"] for stack in stacks), f"{task_pair}.F"),
            "mean_G": {
                arm: safe_mean((by_stack[stack]["mean_G"][arm] for stack in stacks), f"{task_pair}.G.{arm}")
                for arm in ARMS
            },
        }
        for task_pair, stacks in task_pairs.items()
    }
    overall = {
        "mean_F": safe_mean((by_stack[stack]["mean_F"] for stack in STACKS), "overall F"),
        "mean_G": {
            arm: safe_mean((by_stack[stack]["mean_G"][arm] for stack in STACKS), f"overall G.{arm}")
            for arm in ARMS
        },
    }
    descriptive_positive = (
        all(item["mean_F"] > 0 for item in by_task_pair.values())
        and all(value > 0 for item in by_task_pair.values() for value in item["mean_G"].values())
        and overall["mean_F"] > 0
        and all(value > 0 for value in overall["mean_G"].values())
    )
    passed = all_stack_pass and descriptive_positive
    if passed:
        decision = "R13_DEVELOPMENT_SURFACE_ALIGNMENT_SWITCH_PASS_ONLY"
    elif any_dead_zone:
        decision = "DEAD_ZONE"
    elif any(by_stack[stack]["mean_F"] <= DIRECTIONAL_EPSILON for stack in STACKS):
        decision = "F_GATE_FAILURE"
    else:
        decision = "G_H1_GATE_FAILURE"
    report = {
        "schema_version": "r13-target-alignment-independent-validation-report-r1",
        "status": "COMPLETED_MODEL_FREE_RESULT_VALIDATION",
        "decision": decision,
        "technical_pass": True,
        "selected_variant": selected_variant,
        "synthetic_fixture": synthetic_fixture,
        "model_execution_performed": model_execution_performed,
        "q6_reconstructed_from_raw_seven_candidate_traces": True,
        "a_b_checked_before_averaging": selected_variant == "REPRODUCIBILITY_8_PROCESS_AB",
        "by_stack": by_stack,
        "by_task_pair": by_task_pair,
        "descriptive_overall": overall,
        "scientific_evidence": False,
        "development_only": True,
        "post_hoc_outcome_aware": True,
        "formal_confirmatory": False,
        "inferential_p_value": None,
        "sampled_rlvr": False,
        "same_empirical_or_policy_fpr": False,
        "automatic_progression": False,
    }
    assert_finite(report, "validation report")
    return report


def validate_result_bundle(
    result: Any,
    *,
    protocol: dict[str, Any],
    panel_records: Sequence[PanelRecord],
    allowlist_receipt: dict[str, Any],
    human_review_receipt: dict[str, Any],
    authorization: dict[str, Any],
    expected_bindings: dict[str, str],
    synthetic_test_mode: bool = False,
    trusted_policy: ExternalTrustPolicy | None = None,
    matched_panel_bundle_root: Path | None = None,
) -> dict[str, Any]:
    """Validate one complete A or A/B bundle and return recomputed metrics.

    ``synthetic_test_mode`` is an explicit in-process test seam.  It accepts
    only artifacts marked synthetic, forbids claimed model execution, and does
    not exist in :func:`main` or the command-line parser.
    """

    if not synthetic_test_mode and trusted_policy is None:
        raise R13AuthorizationError(
            "BLOCKED_EXTERNAL_TRUST_VERIFIER_NOT_PROVIDED: production validation requires external signature and replay verification"
        )
    if not synthetic_test_mode and matched_panel_bundle_root is None:
        raise R13AuthorizationError(
            "BLOCKED_BOUND_MATCHED_PANEL_LOADER_NOT_PROVIDED: production validation cannot trust caller-created PanelRecord objects"
        )

    validate_protocol(protocol, synthetic_test_mode=synthetic_test_mode)
    require(isinstance(result, dict) and set(result) == RESULT_KEYS, "result bundle schema drift")
    assert_finite(result, "result bundle")
    require(result["schema_version"] == RESULT_SCHEMA, "result bundle version drift")
    expected_status = "SYNTHETIC_TEST_FIXTURE_NO_MODEL" if synthetic_test_mode else "FROZEN_POST_RUN_PRE_VALIDATION"
    require(result["status"] == expected_status, "result status drift")
    require(result["synthetic_fixture"] is synthetic_test_mode, "result synthetic marker drift")
    require(result["scientific_evidence"] is False, "result status washing: scientific evidence")
    require(result["formal_experiment"] is False and result["formal_confirmatory"] is False, "result formal status washing")
    require(result["development_only"] is True, "result development boundary missing")
    require(result["sampled_rlvr"] is False, "result sampled-RLVR status washing")
    require(result["same_empirical_or_policy_fpr"] is False, "result FPR boundary washing")
    require(result["static_core_only"] is True, "unbound non-static runner core")
    require(result["model_execution_authorized_by_core"] is False, "runner core claims model authorization")
    require(result["model_execution_performed"] is (not synthetic_test_mode), "model execution marker inconsistent with validation mode")
    require(tuple(result["ordered_candidate_set"]) == CANDIDATES, "result candidate order drift")
    run_id = valid_uuid4(result["run_id"], "result run id")
    created_at = parse_utc(result["created_at_utc"], "result creation time")
    selected_variant = result["selected_variant"]
    require(selected_variant in VARIANT_REPLICATES, "result variant invalid")
    ordered_process_ids = expected_process_ids(selected_variant)
    require(result["ordered_process_ids"] == list(ordered_process_ids), "result process order/coverage drift")
    validate_bindings(result["bindings"], expected_bindings, "result.bindings")
    require(
        isinstance(result["counts"], dict)
        and set(result["counts"]) == TOTAL_COUNT_KEYS
        and result["counts"] == expected_total_counts(selected_variant),
        "result total counts do not close",
    )
    validate_allowlist_receipt(
        allowlist_receipt,
        expected_protocol_sha256=expected_bindings["protocol_sha256"],
        expected_panel_bundle_sha256=expected_bindings["panel_bundle_sha256"],
        synthetic_test_mode=synthetic_test_mode,
    )
    authorization_window = validate_authorization(
        authorization,
        expected_bindings=expected_bindings,
        run_id=run_id,
        selected_variant=selected_variant,
        ordered_process_ids=ordered_process_ids,
        created_at=created_at,
        synthetic_test_mode=synthetic_test_mode,
        trusted_policy=trusted_policy,
    )
    validate_human_receipt(
        human_review_receipt,
        expected_protocol_sha256=expected_bindings["protocol_sha256"],
        expected_panel_bundle_sha256=expected_bindings["panel_bundle_sha256"],
        expected_allowlist_receipt_sha256=expected_bindings["allowlist_receipt_sha256"],
        synthetic_test_mode=synthetic_test_mode,
        trusted_policy=trusted_policy,
        authorization_issued_at=authorization_window[0],
        result_created_at=created_at,
    )
    effective_panel_records: Sequence[PanelRecord] = panel_records
    if matched_panel_bundle_root is not None:
        # The validator itself performs the disk read and hash-chain
        # recomputation. A caller-created LoadedMatchedPanelBundle is never a
        # production input and cannot mint provenance by copying signed hashes.
        matched_panel_bundle = load_bound_matched_panel_bundle(
            Path(matched_panel_bundle_root),
            expected_manifest_sha256=expected_bindings["panel_bundle_sha256"],
            expected_allowlist_receipt_sha256=expected_bindings[
                "allowlist_receipt_sha256"
            ],
        )
        require(
            matched_panel_bundle.manifest_sha256
            == expected_bindings["panel_bundle_sha256"],
            "loaded matched-panel manifest/result binding mismatch",
        )
        require(
            matched_panel_bundle.allowlist_receipt_sha256
            == expected_bindings["allowlist_receipt_sha256"],
            "loaded allowlist receipt/result binding mismatch",
        )
        require(
            matched_panel_bundle.panel_jsonl_sha256
            == allowlist_receipt["bindings"]["panel_jsonl_sha256"],
            "loaded matched-panel JSONL/allowlist binding mismatch",
        )
        require(
            matched_panel_bundle.raw_inventory_commitment_sha256
            == allowlist_receipt["bindings"]["raw_inventory_commitment_sha256"],
            "loaded raw inventory/allowlist binding mismatch",
        )
        require(
            matched_panel_bundle.allowlist_receipt == allowlist_receipt,
            "caller allowlist receipt differs from hash-bound bundle receipt",
        )
        require(
            tuple(panel_records) == matched_panel_bundle.records,
            "caller panel records differ from manifest-loaded records",
        )
        effective_panel_records = matched_panel_bundle.records
    panels, panel_hashes = validate_matched_panels(effective_panel_records, protocol)

    process_results = result["process_results"]
    require(isinstance(process_results, list) and len(process_results) == len(ordered_process_ids), "process result count drift")
    derived: dict[str, dict[str, dict[str, Any]]] = {stack: {} for stack in STACKS}
    global_event_ids: set[str] = set()
    global_process_run_ids: set[str] = set()
    flattened_events: list[dict[str, Any]] = []
    for expected_process_id, process in zip(ordered_process_ids, process_results):
        item = validate_process(
            process,
            expected_process_id=expected_process_id,
            expected_bundle_run_id=run_id,
            expected_bindings=expected_bindings,
            protocol=protocol,
            panels=panels,
            panel_hashes=panel_hashes,
            authorization_window=authorization_window,
            result_created_at=created_at,
            global_event_ids=global_event_ids,
            global_process_run_ids=global_process_run_ids,
        )
        derived[item["stack_id"]][item["replicate_id"]] = item
        flattened_events.extend(item["events"])
    require(
        len(
            {
                item["initial_parameter_hash"]
                for by_replicate in derived.values()
                for item in by_replicate.values()
            }
        )
        == 1,
        "global initial trainable snapshot hash mismatch",
    )
    events = result["events"]
    require(isinstance(events, list) and len(events) == len(flattened_events), "top-level execution event count drift")
    for index, (observed, expected) in enumerate(zip(events, flattened_events)):
        require(isinstance(observed, dict) and set(observed) == EVENT_KEYS, f"events[{index}]: schema drift")
        require(observed == expected, f"events[{index}]: execution ledger mismatch")
    require(len(global_event_ids) == expected_total_counts(selected_variant)["technical_update_executions"], "global execution-event cardinality drift")
    require(len(global_process_run_ids) == len(ordered_process_ids), "process run-id cardinality drift")
    return summarize_results(
        derived,
        selected_variant=selected_variant,
        synthetic_fixture=result["synthetic_fixture"],
        model_execution_performed=result["model_execution_performed"],
    )
