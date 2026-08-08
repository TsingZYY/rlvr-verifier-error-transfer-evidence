"""Build and verify the local-only R13 eight-process execution manifest.

This module is standard-library only and performs no tokenizer, model, forward,
gradient, or optimizer operation.  It deliberately separates explicit user
authorization for a local development run from paper/production custody.  A
manifest can be built only after the future real backend file exists and every
frozen input can be hashed from disk.

The scientific scope is not configurable: it is permanently fixed to
``REPRODUCIBILITY_8_PROCESS_AB`` (four M0 stacks times A/B technical repeats).
Physical serial execution is permitted, but the complete eight-process batch is
sealed; intermediate scientific outcomes may not be released or used to change
later cells.  Any technical failure stops the batch.  A rerun requires a new
manifest, run id, and output root.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable
import uuid

try:  # Support both direct-script and package imports without ML dependencies.
    from . import r13_local_runtime_bootstrap as runtime_bootstrap_contract
except ImportError:  # pragma: no cover - exercised by direct-script tests.
    import r13_local_runtime_bootstrap as runtime_bootstrap_contract


SCHEMA_VERSION = "r13-local-development-execution-manifest-r1"
STATUS = "LOCAL_DEVELOPMENT_EIGHT_PROCESS_MANIFEST_READY_MODEL_NOT_RUN"
SELECTED_VARIANT = "REPRODUCIBILITY_8_PROCESS_AB"
EVIDENCE_LABEL = "LOCAL_DEVELOPMENT_ONLY_NONCONFIRMATORY"
FROZEN_SEED = 20260804
PROJECT_DIR_NAME = "GPT_PRO_REVIEW_RLVR_V1"
FUTURE_REAL_BACKEND_RELPATH = "mvp_same_source_v1/r13_real_backend.py"
LOCAL_BATCH_RUNNER_RELPATH = "mvp_same_source_v1/r13_local_batch_runner.py"
LOCAL_MANIFEST_BUILDER_RELPATH = (
    "mvp_same_source_v1/r13_local_development_manifest.py"
)
LOCAL_RESULT_VALIDATOR_RELPATH = (
    "formal_g1_development_r1/validate_r13_local_development_results_r1.py"
)
LOCAL_RUNTIME_BOOTSTRAP_RELPATH = "mvp_same_source_v1/r13_local_runtime_bootstrap.py"
LOCAL_RUNTIME_PREFLIGHT_RECEIPT_RELPATH = (
    "formal_g1_development_r1/R13_LOCAL_RUNTIME_PREFLIGHT_RECEIPT_R1.json"
)
LOCAL_EXECUTION_SEMANTICS_RELPATH = (
    "formal_g1_development_r1/"
    "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json"
)
EXPECTED_LOCAL_EXECUTION_SEMANTICS_SHA256 = (
    "0f7dc4d795c9f397644fdb168735cb99084cc52db815fc6d2e952d091f065714"
)
LOCAL_TECHNICAL_CANARY_SPEC_RELPATH = (
    "formal_g1_development_r1/R13_LOCAL_TECHNICAL_CANARY_SPEC_R1.json"
)
EXPECTED_LOCAL_TECHNICAL_CANARY_SPEC_SHA256 = (
    "3c03ce09b60e6a97abc5706815513e52b7fc5ea4a5e5fcd54818a9653cc6644b"
)
PYTHON_RELPATH_FROM_WORKSPACE = ".mvp-venv/Scripts/python.exe"
OUTPUT_ROOT_PREFIX = "authorized_runs/r13_local_dev_ab_"

STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
REPLICATES = ("A", "B")
ORDERED_PROCESS_IDS = tuple(
    f"{stack}|{replicate}" for stack in STACKS for replicate in REPLICATES
)
ARM_ORDER_BY_REPLICATE = {
    "A": ["H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET"],
    "B": ["H1_SWITCHED_TARGET", "H0_ORIGINAL_M0_TARGET"],
}
EXPECTED_COUNTS = {
    "os_processes": 8,
    "unique_design_source_updates": 20,
    "technical_update_executions": 40,
    "pre_target_vectors": 16,
    "pre_target_identity_reads": 96,
    "post_target_vectors": 80,
    "post_target_identity_reads": 480,
    "total_target_identity_metric_cells": 576,
}

ARTIFACT_RELATIVE_PATHS = {
    "protocol": "formal_g1_development_r1/R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json",
    "absolute_effect_addendum": "formal_g1_development_r1/R13_ABSOLUTE_EFFECT_GATE_ADDENDUM_R1.json",
    "local_execution_semantics_addendum": LOCAL_EXECUTION_SEMANTICS_RELPATH,
    "local_technical_canary_spec": LOCAL_TECHNICAL_CANARY_SPEC_RELPATH,
    "update_recipe": "formal_g1_development_r1/R13_UPDATE_RECIPE_CONTRACT_R1.json",
    # Use the current R13 real-asset lineage.  Its bytes equal the older R4
    # source bundle, but the path must match the matched-panel build lineage.
    "source_bundle": "real_assets/build_v5_repair_a/REAL_SOURCE_BUNDLES_V1.jsonl",
    "matched_panel_manifest": "formal_g1_development_r1/r13_assets_r1/R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json",
    "matched_panel_jsonl": "formal_g1_development_r1/r13_assets_r1/R13_MATCHED_TARGET_PANELS_R1.jsonl",
    "matched_panel_allowlist": "formal_g1_development_r1/r13_assets_r1/R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json",
    "model_inventory": "formal_g1_development_r1/R13_MODEL_INVENTORY_R1.json",
    "runtime_reference": "formal_g1_development_r1/R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json",
    "runner": "mvp_same_source_v1/r13_runner_core.py",
    "real_backend": FUTURE_REAL_BACKEND_RELPATH,
    "batch_runner": LOCAL_BATCH_RUNNER_RELPATH,
    "manifest_builder": LOCAL_MANIFEST_BUILDER_RELPATH,
    "local_result_validator": LOCAL_RESULT_VALIDATOR_RELPATH,
    "runtime_bootstrap": LOCAL_RUNTIME_BOOTSTRAP_RELPATH,
    "runtime_preflight_receipt": LOCAL_RUNTIME_PREFLIGHT_RECEIPT_RELPATH,
    "result_validator": "formal_g1_development_r1/validate_r13_target_alignment_results_r1.py",
}
DYNAMIC_ARTIFACT_ROLES = {"canary_receipt"}
ALL_ARTIFACT_ROLES = set(ARTIFACT_RELATIVE_PATHS) | DYNAMIC_ARTIFACT_ROLES

CANARY_RECEIPT_SCHEMA = "r13-local-technical-canary-receipt-r1"
CANARY_RECEIPT_STATUS = "PASS_LOCAL_ENGINEERING_CANARY"
CANARY_EVIDENCE_BOUNDARY = "LOCAL_ENGINEERING_CANARY_ONLY_NOT_R13_EVIDENCE"
CANARY_RECEIPT_FIELDS = (
    "schema_version",
    "status",
    "created_at_utc",
    "canary_spec_sha256",
    "backend_sha256",
    "model_inventory_sha256",
    "runtime_reference_sha256",
    "dependency_versions",
    "device_name",
    "device_capability",
    "bf16_supported",
    "trainable_parameter_count",
    "candidate_supervised_token_count_min",
    "candidate_supervised_token_count_max",
    "all_candidate_scores_finite",
    "all_gradients_finite",
    "raw_gradient_norm",
    "raw_gradient_norm_threshold_pass",
    "parameter_changed_after_update",
    "target_read_hash_preserved",
    "reset_hash_restored",
    "peak_gpu_memory_bytes",
    "elapsed_seconds",
    "scientific_source_cells",
    "scientific_target_cells",
    "model_action_counts",
    "evidence_boundary",
)
CANARY_FORBIDDEN_FIELDS = (
    "ordered_candidate_scores",
    "candidate_probabilities",
    "r13_source_result",
    "r13_target_result",
    "scientific_gate",
    "mechanism_decision",
)
CANARY_MODEL_ACTION_COUNT_KEYS = {
    "tokenizer_loads",
    "model_weight_loads",
    "model_forward_calls",
    "backward_calls",
    "manual_parameter_steps",
}

SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class R13LocalManifestError(RuntimeError):
    """The local development manifest is incomplete, unsafe, or drifted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R13LocalManifestError(message)


def _reject_constant(token: str) -> Any:
    raise R13LocalManifestError(f"non-finite JSON constant forbidden: {token}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise R13LocalManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_strict_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise R13LocalManifestError(f"cannot read {label}: {path}") from error
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R13LocalManifestError(f"invalid strict JSON for {label}: {path}") from error
    require(isinstance(value, dict), f"{label} must be a JSON object")
    assert_finite(value, label)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    assert_finite(value, "canonical JSON value")
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise R13LocalManifestError("value is not canonical-JSON serializable") from error


def assert_finite(value: Any, label: str) -> None:
    if isinstance(value, float):
        require(math.isfinite(value), f"non-finite value in {label}")
    elif isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str), f"non-string JSON key in {label}")
            assert_finite(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_finite(item, f"{label}[{index}]")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise R13LocalManifestError(f"cannot hash artifact: {path}") from error
    return digest.hexdigest()


def valid_sha256(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and SHA256_RE.fullmatch(value) is not None,
        f"invalid SHA-256 for {label}",
    )
    return value


def valid_uuid4(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and UUID4_RE.fullmatch(value) is not None,
        f"{label} is not canonical UUIDv4",
    )
    try:
        parsed = uuid.UUID(value, version=4)
    except (ValueError, AttributeError) as error:
        raise R13LocalManifestError(f"{label} is not UUIDv4") from error
    require(str(parsed) == value, f"{label} is not canonical UUIDv4")
    return value


def parse_utc(
    value: Any,
    label: str,
    *,
    require_whole_seconds: bool = True,
) -> datetime:
    require(
        isinstance(value, str) and value.endswith("Z"),
        f"{label} must be RFC3339 UTC",
    )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise R13LocalManifestError(f"invalid {label}") from error
    require(parsed.tzinfo is not None, f"{label} lacks timezone")
    require(parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0, f"{label} is not UTC")
    if require_whole_seconds:
        require(parsed.microsecond == 0, f"{label} must use whole seconds")
    return parsed.astimezone(timezone.utc)


def _safe_relative_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label} path missing")
    pure = PurePosixPath(value)
    require(not pure.is_absolute(), f"{label} path must be relative")
    require(".." not in pure.parts, f"{label} path escapes root")
    require("\\" not in value, f"{label} path must use POSIX separators")
    require(str(pure) == value, f"{label} path is not normalized")
    return value


def _resolve_beneath(root: Path, relpath: str, label: str) -> Path:
    normalized = _safe_relative_path(relpath, label)
    selected_root = root.resolve()
    resolved = (selected_root / Path(*PurePosixPath(normalized).parts)).resolve()
    try:
        resolved.relative_to(selected_root)
    except ValueError as error:
        raise R13LocalManifestError(f"{label} path escapes root") from error
    return resolved


def _artifact_record(project_root: Path, role: str, relpath: str) -> dict[str, Any]:
    path = _resolve_beneath(project_root, relpath, f"artifact {role}")
    require(path.exists(), f"BLOCKED_MISSING_ARTIFACT[{role}]: {path}")
    require(path.is_file(), f"BLOCKED_NONFILE_ARTIFACT[{role}]: {path}")
    require(not path.is_symlink(), f"BLOCKED_SYMLINK_ARTIFACT[{role}]: {path}")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise R13LocalManifestError(f"cannot stat artifact {role}: {path}") from error
    require(size > 0, f"BLOCKED_EMPTY_ARTIFACT[{role}]: {path}")
    return {"path": relpath, "sha256": sha256_file(path), "byte_length": size}


def _read_bound_json(
    project_root: Path, artifacts: dict[str, dict[str, Any]], role: str
) -> dict[str, Any]:
    record = artifacts[role]
    path = _resolve_beneath(project_root, record["path"], f"artifact {role}")
    return read_strict_json(path, role)


def _parse_runtime_receipt_utc(value: Any) -> datetime:
    require(isinstance(value, str) and bool(value), "runtime-preflight timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise R13LocalManifestError("invalid runtime-preflight timestamp") from error
    require(
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0,
        "runtime-preflight timestamp is not UTC",
    )
    return parsed.astimezone(timezone.utc)


def _validate_runtime_preflight_receipt(
    *,
    project_root: Path,
    artifacts: dict[str, dict[str, Any]],
    manifest_frozen_at_utc: str,
) -> None:
    record = artifacts["runtime_preflight_receipt"]
    path = _resolve_beneath(
        project_root, record["path"], "artifact runtime_preflight_receipt"
    )
    raw = path.read_bytes()
    receipt = read_strict_json(path, "runtime preflight receipt")
    require(
        raw == runtime_bootstrap_contract.canonical_json_bytes(receipt),
        "runtime-preflight receipt is not canonical JSON plus one LF",
    )
    try:
        runtime_bootstrap_contract.validate_receipt(receipt)
    except runtime_bootstrap_contract.LocalRuntimeBootstrapError as error:
        raise R13LocalManifestError(f"invalid runtime-preflight receipt: {error}") from error
    require(
        _parse_runtime_receipt_utc(receipt["created_at_utc"])
        < parse_utc(manifest_frozen_at_utc, "manifest freeze time"),
        "runtime-preflight receipt must predate manifest freeze",
    )
    bootstrap = receipt["bootstrap"]
    require(
        bootstrap["sha256"] == artifacts["runtime_bootstrap"]["sha256"]
        and bootstrap["size_bytes"]
        == artifacts["runtime_bootstrap"]["byte_length"],
        "runtime-preflight/bootstrap artifact binding mismatch",
    )
    require(
        receipt["runtime_reference"]["sha256"]
        == artifacts["runtime_reference"]["sha256"],
        "runtime-preflight/runtime-reference binding mismatch",
    )
    require(
        receipt["model_action_count"] == 0
        and receipt["run_eligible"] is False
        and receipt["model_execution_authorized"] is False
        and receipt["full_dependency_content_hash_bound"] is False
        and receipt["release_authorization_compatible"] is False,
        "runtime-preflight authorization or evidence boundary drift",
    )
    require(
        receipt["dispatch"]["model_capable_dispatch_enabled"] is False
        and receipt["evidence_boundary"]
        == {
            "scope": "LOCAL_DEVELOPMENT_STATIC_PREFLIGHT_ONLY",
            "scientific_evidence": False,
            "formal_experiment": False,
            "production_runtime": False,
        },
        "runtime-preflight dispatch/evidence boundary drift",
    )


def _validate_cross_bindings(
    project_root: Path,
    artifacts: dict[str, dict[str, Any]],
    *,
    manifest_frozen_at_utc: str,
) -> dict[str, Any]:
    protocol = _read_bound_json(project_root, artifacts, "protocol")
    require(
        protocol.get("schema_version")
        == "r13-fixed-update-target-alignment-pilot-draft-r1",
        "protocol schema drift",
    )
    variants = protocol.get("execution_variants")
    require(isinstance(variants, dict), "protocol execution variants missing")
    variant = variants.get(SELECTED_VARIANT)
    require(isinstance(variant, dict), "frozen eight-process variant missing")
    require(variant.get("replicates") == list(REPLICATES), "protocol A/B replicate drift")
    for field, expected in EXPECTED_COUNTS.items():
        require(variant.get(field) == expected, f"protocol eight-process count drift: {field}")
    require(
        protocol.get("execution_variants", {}).get("preauthorization_choice_rule")
        == "Select and freeze exactly one variant before authorization. Inspecting A and then adding B is forbidden.",
        "protocol variant freeze rule drift",
    )

    protocol_sha = artifacts["protocol"]["sha256"]
    addendum = _read_bound_json(project_root, artifacts, "absolute_effect_addendum")
    require(
        addendum.get("schema_version") == "r13-absolute-effect-gate-addendum-r1",
        "absolute-effect addendum schema drift",
    )
    require(
        addendum.get("parent_protocol", {}).get("sha256") == protocol_sha,
        "absolute-effect addendum parent hash mismatch",
    )
    require(
        addendum.get("count_contract", {})
        .get(SELECTED_VARIANT, {})
        .get("raw_E_cells_before_technical_averaging")
        == 480,
        "absolute-effect addendum A/B raw-E count drift",
    )
    require(
        addendum.get("threshold_contract", {}).get("threshold_adaptation_after_results")
        == "FORBIDDEN_REQUIRES_NEW_ADDENDUM_VERSION",
        "absolute-effect threshold adaptation rule drift",
    )

    semantics_record = artifacts["local_execution_semantics_addendum"]
    require(
        semantics_record["sha256"] == EXPECTED_LOCAL_EXECUTION_SEMANTICS_SHA256,
        "local execution-semantics addendum hash drift",
    )
    semantics = _read_bound_json(
        project_root, artifacts, "local_execution_semantics_addendum"
    )
    require(
        semantics.get("schema_version")
        == "r13-local-development-execution-semantics-addendum-r1",
        "local execution-semantics addendum schema drift",
    )
    semantics_parents = semantics.get("parent_bindings")
    require(isinstance(semantics_parents, dict), "execution-semantics parent bindings missing")
    expected_semantics_parents = {
        "protocol_sha256": artifacts["protocol"]["sha256"],
        "update_recipe_sha256": artifacts["update_recipe"]["sha256"],
        "absolute_effect_gate_sha256": artifacts["absolute_effect_addendum"]["sha256"],
        "runner_core_sha256": artifacts["runner"]["sha256"],
    }
    for field, expected in expected_semantics_parents.items():
        require(
            semantics_parents.get(field) == expected,
            f"execution-semantics parent binding mismatch: {field}",
        )
    semantics_scope = semantics.get("selected_execution_scope")
    require(isinstance(semantics_scope, dict), "execution-semantics scope missing")
    require(
        semantics_scope.get("variant") == SELECTED_VARIANT
        and semantics_scope.get("technical_replicates") == list(REPLICATES)
        and semantics_scope.get("os_processes") == 8
        and semantics_scope.get("technical_update_executions") == 40
        and semantics_scope.get("total_target_identity_metric_cells") == 576,
        "execution-semantics eight-process A/B scope drift",
    )
    accumulation = semantics.get("gradient_accumulation_resolution")
    require(isinstance(accumulation, dict), "gradient accumulation semantics missing")
    require(
        accumulation.get("source_rows") == 14
        and accumulation.get("backward_calls") == 14
        and accumulation.get("backward_order") == "exact bound source-row order"
        and accumulation.get("logical_update_steps") == 1
        and accumulation.get("manual_parameter_steps") == 1
        and accumulation.get("adaptive_microbatching") is False,
        "execution-semantics 14-backward contract drift",
    )
    norm_gate = semantics.get("gradient_norm_and_clipping_resolution")
    require(isinstance(norm_gate, dict), "gradient norm semantics missing")
    stop_label = "TECHNICAL_STOP_BEFORE_PARAMETER_MUTATION_NO_SCIENTIFIC_INTERPRETATION"
    require(
        norm_gate.get("threshold") == 1.0
        and norm_gate.get("check_time")
        == "after gradient accumulation and before any parameter mutation"
        and norm_gate.get("if_non_finite") == stop_label
        and norm_gate.get("if_raw_norm_greater_than_threshold") == stop_label
        and norm_gate.get("if_raw_norm_at_or_below_threshold")
        == "perform exactly one manual SGD step theta := theta - 0.1 * gradient"
        and norm_gate.get("clipping_triggered_on_valid_result") is False
        and norm_gate.get("failed_cell_retry_under_same_manifest") is False,
        "execution-semantics pre-step raw-gradient-norm stop gate drift",
    )
    semantics_evidence = semantics.get("authorization_and_evidence_boundary")
    require(isinstance(semantics_evidence, dict), "execution-semantics evidence boundary missing")
    require(
        semantics_evidence.get("authorization_basis")
        == "USER_IN_THREAD_EXPLICIT_AUTHORIZATION_FOR_CODEX_TO_RUN_THE_MVP"
        and semantics_evidence.get("this_addendum_self_authorizes_model_execution")
        is False
        and semantics_evidence.get("final_local_manifest_required") is True
        and semantics_evidence.get("human_review_completed") is False
        and semantics_evidence.get("panel_review_level")
        == "INDEPENDENT_AGENT_AND_MACHINE_REVIEW_ONLY_NOT_HUMAN"
        and semantics_evidence.get("paper_custody") is False
        and semantics_evidence.get("production_custody") is False
        and semantics_evidence.get("maximum_result_label") == EVIDENCE_LABEL
        and semantics_evidence.get("formal_or_confirmatory_claim_allowed") is False
        and semantics_evidence.get("shared_semantic_identity_claim_allowed") is False
        and semantics_evidence.get("population_generalization_allowed") is False
        and semantics_evidence.get("same_empirical_or_policy_fpr_claim_allowed")
        is False,
        "execution-semantics evidence boundary drift",
    )
    require(
        semantics.get("model_actions_when_frozen")
        == {
            "tokenizer_loads": 0,
            "model_weight_loads": 0,
            "model_forwards": 0,
            "gradient_calls": 0,
            "manual_parameter_steps": 0,
        },
        "execution-semantics freeze action count drift",
    )

    canary_spec_record = artifacts["local_technical_canary_spec"]
    require(
        canary_spec_record["sha256"]
        == EXPECTED_LOCAL_TECHNICAL_CANARY_SPEC_SHA256,
        "local technical-canary spec hash drift",
    )
    canary_spec = _read_bound_json(
        project_root, artifacts, "local_technical_canary_spec"
    )
    require(
        canary_spec.get("schema_version")
        == "r13-local-technical-canary-spec-r1",
        "local technical-canary spec schema drift",
    )
    require(
        canary_spec.get("parent_semantics", {}).get("sha256")
        == artifacts["local_execution_semantics_addendum"]["sha256"],
        "technical-canary parent semantics hash mismatch",
    )
    exclusion = canary_spec.get("scientific_cell_exclusion")
    require(isinstance(exclusion, dict), "technical-canary scientific exclusion missing")
    require(
        exclusion.get("uses_r13_source_bundle") is False
        and exclusion.get("uses_r13_matched_target_panel") is False
        and exclusion.get("uses_r10_or_r13_task_id") is False
        and exclusion.get("canary_outcome_may_change_scientific_design_or_gates")
        is False,
        "technical-canary scientific-cell exclusion drift",
    )
    source_canary = canary_spec.get("source_canary")
    require(isinstance(source_canary, dict), "technical-canary source shape missing")
    require(
        source_canary.get("row_count") == 14
        and source_canary.get("backward_calls") == 14
        and source_canary.get("raw_gradient_norm_threshold") == 1.0
        and source_canary.get("raw_norm_failure_action")
        == "STOP_BEFORE_PARAMETER_MUTATION"
        and source_canary.get("manual_parameter_steps_on_pass") == 1,
        "technical-canary backward/norm/step semantics drift",
    )
    require(
        canary_spec.get("expected_action_shape")
        == {
            "tokenizer_loads": 1,
            "model_weight_loads": 1,
            "model_forward_calls": 16,
            "backward_calls": 14,
            "manual_parameter_steps": 1,
            "scientific_source_cells": 0,
            "scientific_target_cells": 0,
        },
        "technical-canary expected action shape drift",
    )
    require(
        canary_spec.get("allowed_receipt_fields") == list(CANARY_RECEIPT_FIELDS),
        "technical-canary allowed receipt fields drift",
    )
    require(
        canary_spec.get("receipt_forbidden_fields")
        == list(CANARY_FORBIDDEN_FIELDS),
        "technical-canary forbidden receipt fields drift",
    )
    require(
        canary_spec.get("candidate_scoring", {}).get(
            "candidate_scores_may_be_written_or_displayed"
        )
        is False
        and canary_spec.get("readonly_canary", {}).get(
            "scores_may_be_written_or_displayed"
        )
        is False,
        "technical-canary candidate-score disclosure boundary drift",
    )
    require(
        canary_spec.get("evidence_boundary") == CANARY_EVIDENCE_BOUNDARY
        and canary_spec.get("model_actions_when_spec_frozen") == 0,
        "technical-canary evidence/action boundary drift",
    )

    recipe = _read_bound_json(project_root, artifacts, "update_recipe")
    require(
        recipe.get("schema_version") == "r13-source-update-recipe-contract-r1",
        "update recipe schema drift",
    )
    expected_recipe_hashes = {
        "protocol_sha256": artifacts["protocol"]["sha256"],
        "source_bundles_sha256": artifacts["source_bundle"]["sha256"],
        "model_inventory_sha256": artifacts["model_inventory"]["sha256"],
        "runtime_environment_reference_sha256": artifacts["runtime_reference"]["sha256"],
    }
    for field, expected in expected_recipe_hashes.items():
        require(recipe.get(field) == expected, f"update recipe binding mismatch: {field}")
    require(recipe.get("update", {}).get("seed") == FROZEN_SEED, "update recipe seed drift")
    require(
        recipe.get("update", {}).get("steps") == 1
        and recipe.get("update", {}).get("optimizer") == "manual_sgd_no_state",
        "update recipe step/optimizer drift",
    )
    require(
        recipe.get("same_update_target_readout_contract", {}).get(
            "both_target_arms_share_exact_parameter_hash"
        )
        is True,
        "same-update target-arm contract drift",
    )

    panel_manifest = _read_bound_json(project_root, artifacts, "matched_panel_manifest")
    require(
        panel_manifest.get("schema_version") == "r13-matched-target-panel-manifest-r1",
        "matched-panel manifest schema drift",
    )
    require(
        panel_manifest.get("panel_jsonl", {}).get("sha256")
        == artifacts["matched_panel_jsonl"]["sha256"],
        "matched-panel JSONL hash mismatch",
    )
    require(
        panel_manifest.get("input_bindings", {}).get("source_build_id")
        == "build_v5_repair_a"
        and artifacts["source_bundle"]["path"].startswith(
            "real_assets/build_v5_repair_a/"
        ),
        "source-bundle path does not match the current R13 asset lineage",
    )
    require(
        panel_manifest.get("counts")
        == {
            "arms_per_stack": 2,
            "canonical_rows_per_arm": 7,
            "panel_records": 56,
            "raw_files": 112,
            "stacks": 4,
        },
        "matched-panel count drift",
    )
    require(
        panel_manifest.get("human_review", {}).get("completed") is False,
        "human-review status unexpectedly changed; build a new manifest contract",
    )

    allowlist = _read_bound_json(project_root, artifacts, "matched_panel_allowlist")
    require(
        allowlist.get("schema_version")
        == "r13-matched-target-panel-allowlist-validation-r1",
        "matched-panel allowlist schema drift",
    )
    allow_bindings = allowlist.get("bindings")
    require(isinstance(allow_bindings, dict), "allowlist bindings missing")
    expected_allowlist = {
        "manifest_sha256": artifacts["matched_panel_manifest"]["sha256"],
        "panel_jsonl_sha256": artifacts["matched_panel_jsonl"]["sha256"],
        "protocol_sha256": artifacts["protocol"]["sha256"],
    }
    for field, expected in expected_allowlist.items():
        require(allow_bindings.get(field) == expected, f"allowlist binding mismatch: {field}")
    require(
        allowlist.get("verdict") == "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY",
        "matched-panel machine allowlist did not pass",
    )
    require(
        allowlist.get("human_review_completed") is False,
        "allowlist human-review boundary drift",
    )

    inventory = _read_bound_json(project_root, artifacts, "model_inventory")
    require(
        inventory.get("schema_version") == "r13-semantic-model-inventory-r1",
        "model inventory schema drift",
    )
    require(
        inventory.get("repository") == "HuggingFaceTB/SmolLM2-360M-Instruct"
        and inventory.get("revision")
        == "a10cc1512eabd3dde888204e902eca88bddb4951",
        "model identity/revision drift",
    )
    require(
        inventory.get("status") == "STATIC_BYTES_BOUND_MODEL_NOT_LOADED",
        "model inventory load boundary drift",
    )

    runtime = _read_bound_json(project_root, artifacts, "runtime_reference")
    require(
        runtime.get("schema_version")
        == "r13-observed-isolated-runtime-environment-r1",
        "runtime reference schema drift",
    )
    require(
        runtime.get("full_dependency_content_hash_bound") is False,
        "local manifest contract expects the disclosed non-production runtime boundary",
    )
    _validate_runtime_preflight_receipt(
        project_root=project_root,
        artifacts=artifacts,
        manifest_frozen_at_utc=manifest_frozen_at_utc,
    )
    return {
        "protocol_run_eligible": protocol.get("run_eligible"),
        "protocol_model_execution_authorized": protocol.get(
            "model_execution_authorized"
        ),
        "runtime_full_dependency_content_hash_bound": runtime.get(
            "full_dependency_content_hash_bound"
        ),
        "human_review_completed": False,
        "machine_allowlist_verdict": allowlist.get("verdict"),
    }


def _validate_canary_receipt(
    *,
    project_root: Path,
    artifacts: dict[str, dict[str, Any]],
    manifest_frozen_at_utc: str,
) -> None:
    receipt = _read_bound_json(project_root, artifacts, "canary_receipt")
    require(
        set(receipt) == set(CANARY_RECEIPT_FIELDS),
        "technical-canary receipt field coverage drift or forbidden field present",
    )
    for field in CANARY_FORBIDDEN_FIELDS:
        require(field not in receipt, f"forbidden technical-canary receipt field: {field}")
    require(receipt["schema_version"] == CANARY_RECEIPT_SCHEMA, "technical-canary receipt schema drift")
    require(receipt["status"] == CANARY_RECEIPT_STATUS, "technical-canary did not pass")
    receipt_time = parse_utc(
        receipt["created_at_utc"],
        "technical-canary receipt time",
        require_whole_seconds=False,
    )
    manifest_time = parse_utc(manifest_frozen_at_utc, "manifest freeze time")
    require(receipt_time < manifest_time, "technical-canary receipt must predate manifest freeze")
    expected_hashes = {
        "canary_spec_sha256": artifacts["local_technical_canary_spec"]["sha256"],
        "backend_sha256": artifacts["real_backend"]["sha256"],
        "model_inventory_sha256": artifacts["model_inventory"]["sha256"],
        "runtime_reference_sha256": artifacts["runtime_reference"]["sha256"],
    }
    for field, expected in expected_hashes.items():
        valid_sha256(receipt[field], f"canary receipt {field}")
        require(receipt[field] == expected, f"technical-canary hash mismatch: {field}")
    versions = receipt["dependency_versions"]
    require(
        isinstance(versions, dict)
        and bool(versions)
        and all(
            isinstance(name, str)
            and bool(name)
            and isinstance(version, str)
            and bool(version)
            for name, version in versions.items()
        ),
        "technical-canary dependency versions missing",
    )
    require(isinstance(receipt["device_name"], str) and bool(receipt["device_name"].strip()), "technical-canary device name missing")
    capability = receipt["device_capability"]
    require(
        isinstance(capability, list)
        and len(capability) == 2
        and all(type(item) is int and item >= 0 for item in capability),
        "technical-canary device capability invalid",
    )
    require(receipt["bf16_supported"] is True, "technical-canary BF16 gate failed")
    require(type(receipt["trainable_parameter_count"]) is int and receipt["trainable_parameter_count"] > 0, "technical-canary trainable parameter count invalid")
    token_min = receipt["candidate_supervised_token_count_min"]
    token_max = receipt["candidate_supervised_token_count_max"]
    require(
        type(token_min) is int
        and type(token_max) is int
        and 0 < token_min <= token_max,
        "technical-canary supervised candidate token count gate failed",
    )
    require(receipt["all_candidate_scores_finite"] is True, "technical-canary finite-score gate failed")
    require(receipt["all_gradients_finite"] is True, "technical-canary finite-gradient gate failed")
    raw_norm = receipt["raw_gradient_norm"]
    require(
        type(raw_norm) in (int, float)
        and math.isfinite(float(raw_norm))
        and 0.0 <= float(raw_norm) <= 1.0,
        "technical-canary raw-gradient-norm gate failed",
    )
    require(receipt["raw_gradient_norm_threshold_pass"] is True, "technical-canary raw-gradient threshold marker failed")
    require(receipt["parameter_changed_after_update"] is True, "technical-canary parameter-change gate failed")
    require(receipt["target_read_hash_preserved"] is True, "technical-canary read-only hash gate failed")
    require(receipt["reset_hash_restored"] is True, "technical-canary reset-hash gate failed")
    require(type(receipt["peak_gpu_memory_bytes"]) is int and receipt["peak_gpu_memory_bytes"] > 0, "technical-canary GPU memory receipt invalid")
    require(
        type(receipt["elapsed_seconds"]) in (int, float)
        and math.isfinite(float(receipt["elapsed_seconds"]))
        and float(receipt["elapsed_seconds"]) > 0,
        "technical-canary elapsed time invalid",
    )
    require(
        receipt["scientific_source_cells"] == 0
        and receipt["scientific_target_cells"] == 0,
        "technical-canary touched a scientific cell",
    )
    action_counts = receipt["model_action_counts"]
    require(
        isinstance(action_counts, dict)
        and set(action_counts) == CANARY_MODEL_ACTION_COUNT_KEYS
        and action_counts
        == {
            "tokenizer_loads": 1,
            "model_weight_loads": 1,
            "model_forward_calls": 16,
            "backward_calls": 14,
            "manual_parameter_steps": 1,
        },
        "technical-canary model action count drift",
    )
    require(
        receipt["evidence_boundary"] == CANARY_EVIDENCE_BOUNDARY,
        "technical-canary evidence boundary drift",
    )


ARTIFACT_RECORD_KEYS = {"path", "sha256", "byte_length"}
TOP_KEYS = {
    "schema_version",
    "status",
    "frozen_at_utc",
    "run_id",
    "selected_variant",
    "ordered_process_ids",
    "process_plan",
    "user_authorization",
    "review_status",
    "evidence_boundary",
    "artifacts",
    "execution",
    "output",
    "result_policy",
    "preflight",
    "model_execution_performed",
    "model_action_count_at_manifest_build",
}
PROCESS_PLAN_KEYS = {
    "ordered_stack_ids",
    "ordered_replicates",
    "arm_evaluation_order_by_replicate",
    "counts",
    "physical_processes_may_run_serially",
}
USER_AUTH_KEYS = {"authorized", "source", "scope", "recorded_statement"}
REVIEW_KEYS = {
    "human_review_completed",
    "review_level",
    "machine_allowlist_required",
    "independent_human_receipt_claimed",
}
EVIDENCE_KEYS = {
    "label",
    "development_only",
    "formal_experiment",
    "formal_confirmatory",
    "scientific_evidence_at_manifest_build",
    "paper_custody",
    "production_custody",
    "sampled_rlvr",
    "same_empirical_or_policy_fpr",
}
EXECUTION_KEYS = {
    "seed",
    "command_working_directory",
    "command",
    "environment",
    "sealed_batch",
    "intermediate_scientific_result_release_allowed",
    "result_dependent_change_to_later_cells_allowed",
    "adaptive_rerun_allowed",
    "same_manifest_retry_allowed",
    "failure_policy",
    "retry_policy",
    "automatic_progression",
}
OUTPUT_KEYS = {
    "root",
    "binding_sha256",
    "must_be_absent_at_manifest_freeze",
    "overwrite_allowed",
    "manifest_path",
}
RESULT_POLICY_KEYS = {
    "release_condition",
    "validator_required",
    "local_result_validator_required",
    "official_core_validator_required",
    "absolute_effect_addendum_required",
    "technical_failure_label",
    "maximum_positive_claim",
    "forbidden_claims",
}
PREFLIGHT_KEYS = {
    "all_required_artifacts_exist_and_are_hash_bound",
    "real_backend_exists_and_is_hash_bound",
    "batch_runner_exists_and_is_hash_bound",
    "local_result_validator_exists_and_is_hash_bound",
    "official_core_validator_exists_and_is_hash_bound",
    "runtime_bootstrap_and_preflight_receipt_hash_bound",
    "technical_canary_receipt_passed_and_hash_bound",
    "runtime_full_dependency_content_hash_bound",
    "human_review_completed",
    "review_level",
    "paper_custody",
    "production_custody",
    "local_model_execution_authorized_by_user",
    "manifest_builder_loaded_model",
}


def _workspace_rel(path: Path, workspace_root: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(workspace_root.resolve())
    except ValueError as error:
        raise R13LocalManifestError(f"{label} must be beneath workspace root") from error
    value = relative.as_posix()
    return _safe_relative_path(value, label)


def _expected_command(project_root: Path, manifest_path: Path) -> list[str]:
    workspace_root = project_root.resolve().parent
    python_path = _resolve_beneath(
        workspace_root, PYTHON_RELPATH_FROM_WORKSPACE, "Python executable"
    )
    require(
        python_path.exists() and python_path.is_file(),
        f"BLOCKED_MISSING_COMMAND_PYTHON: {python_path}",
    )
    batch_runner_path = _resolve_beneath(
        project_root, LOCAL_BATCH_RUNNER_RELPATH, "local batch runner"
    )
    manifest_rel = _workspace_rel(manifest_path, workspace_root, "manifest output")
    batch_runner_rel = _workspace_rel(
        batch_runner_path, workspace_root, "local batch runner"
    )
    return [
        PYTHON_RELPATH_FROM_WORKSPACE,
        "-I",
        "-B",
        batch_runner_rel,
        "coordinate",
        "--manifest",
        manifest_rel,
    ]


def _output_binding(root_rel: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": "r13-local-development-output-root-binding-r1",
                "root": root_rel,
                "selected_variant": SELECTED_VARIANT,
                "must_be_absent_at_manifest_freeze": True,
                "overwrite_allowed": False,
            }
        )
    )


def build_manifest(
    *,
    project_root: Path,
    manifest_path: Path,
    output_root: Path,
    canary_receipt_path: Path,
    run_id: str,
    frozen_at_utc: str,
) -> dict[str, Any]:
    project = project_root.resolve()
    require(project.name == PROJECT_DIR_NAME, f"project root must end in {PROJECT_DIR_NAME}")
    require(project.exists() and project.is_dir(), "project root missing")
    valid_uuid4(run_id, "run id")
    parse_utc(frozen_at_utc, "manifest freeze time")
    workspace_root = project.parent
    manifest_rel = _workspace_rel(manifest_path, workspace_root, "manifest output")
    output_rel = _workspace_rel(output_root, workspace_root, "output root")
    require(
        output_rel.startswith(f"{PROJECT_DIR_NAME}/{OUTPUT_ROOT_PREFIX}"),
        "output root must use the frozen authorized_runs/r13_local_dev_ab_ prefix",
    )
    require(not output_root.exists(), "BLOCKED_OUTPUT_ROOT_ALREADY_EXISTS")
    require(not manifest_path.exists(), "BLOCKED_MANIFEST_OUTPUT_ALREADY_EXISTS")

    artifacts = {
        role: _artifact_record(project, role, relpath)
        for role, relpath in ARTIFACT_RELATIVE_PATHS.items()
    }
    canary_receipt_rel = _workspace_rel(
        canary_receipt_path.resolve(), project, "canary receipt"
    )
    require(
        canary_receipt_rel not in set(ARTIFACT_RELATIVE_PATHS.values()),
        "canary receipt must be a separate dynamic artifact",
    )
    artifacts["canary_receipt"] = _artifact_record(
        project, "canary_receipt", canary_receipt_rel
    )
    cross = _validate_cross_bindings(
        project,
        artifacts,
        manifest_frozen_at_utc=frozen_at_utc,
    )
    _validate_canary_receipt(
        project_root=project,
        artifacts=artifacts,
        manifest_frozen_at_utc=frozen_at_utc,
    )
    command = _expected_command(project, manifest_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS,
        "frozen_at_utc": frozen_at_utc,
        "run_id": run_id,
        "selected_variant": SELECTED_VARIANT,
        "ordered_process_ids": list(ORDERED_PROCESS_IDS),
        "process_plan": {
            "ordered_stack_ids": list(STACKS),
            "ordered_replicates": list(REPLICATES),
            "arm_evaluation_order_by_replicate": ARM_ORDER_BY_REPLICATE,
            "counts": EXPECTED_COUNTS,
            "physical_processes_may_run_serially": True,
        },
        "user_authorization": {
            "authorized": True,
            "source": "USER_IN_THREAD_EXPLICIT_AUTHORIZATION",
            "scope": "R13_REPRODUCIBILITY_8_PROCESS_AB_LOCAL_DEVELOPMENT_ONLY",
            "recorded_statement": (
                "User explicitly directed Codex to run the frozen eight-process "
                "development MVP; this record does not claim external custody."
            ),
        },
        "review_status": {
            "human_review_completed": False,
            "review_level": "independent_agent_and_machine_review_only",
            "machine_allowlist_required": True,
            "independent_human_receipt_claimed": False,
        },
        "evidence_boundary": {
            "label": EVIDENCE_LABEL,
            "development_only": True,
            "formal_experiment": False,
            "formal_confirmatory": False,
            "scientific_evidence_at_manifest_build": False,
            "paper_custody": False,
            "production_custody": False,
            "sampled_rlvr": False,
            "same_empirical_or_policy_fpr": False,
        },
        "artifacts": artifacts,
        "execution": {
            "seed": FROZEN_SEED,
            "command_working_directory": ".",
            "command": command,
            "environment": {
                "PYTHONDONTWRITEBYTECODE": "1",
                "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
                "TOKENIZERS_PARALLELISM": "false",
            },
            "sealed_batch": True,
            "intermediate_scientific_result_release_allowed": False,
            "result_dependent_change_to_later_cells_allowed": False,
            "adaptive_rerun_allowed": False,
            "same_manifest_retry_allowed": False,
            "failure_policy": "STOP_ENTIRE_BATCH_NO_SCIENTIFIC_INTERPRETATION",
            "retry_policy": (
                "NEW_PREFROZEN_MANIFEST_RUN_ID_AND_OUTPUT_ROOT_REQUIRED_BEFORE_ANY_RETRY"
            ),
            "automatic_progression": False,
        },
        "output": {
            "root": output_rel,
            "binding_sha256": _output_binding(output_rel),
            "must_be_absent_at_manifest_freeze": True,
            "overwrite_allowed": False,
            "manifest_path": manifest_rel,
        },
        "result_policy": {
            "release_condition": (
                "ONLY_AFTER_ALL_EIGHT_PREFROZEN_PROCESSES_COMPLETE_AND_THE_BOUND_"
                "LOCAL_RESULT_VALIDATOR_OFFICIAL_CORE_VALIDATOR_AND_ABSOLUTE_"
                "EFFECT_ADDENDUM_PASS"
            ),
            "validator_required": True,
            "local_result_validator_required": True,
            "official_core_validator_required": True,
            "absolute_effect_addendum_required": True,
            "technical_failure_label": "INVALID_BATCH_NO_SCIENTIFIC_CONCLUSION",
            "maximum_positive_claim": (
                "NARROW_LOCAL_DEVELOPMENT_SUPPORT_IN_FOUR_FIXED_M0_AFFINE_STACKS_ONLY"
            ),
            "forbidden_claims": [
                "shared_semantic_identity_causation",
                "same_empirical_or_policy_fpr",
                "population_task_generalization",
                "formal_or_confirmatory_inference",
                "paper_or_production_custody",
            ],
        },
        "preflight": {
            "all_required_artifacts_exist_and_are_hash_bound": True,
            "real_backend_exists_and_is_hash_bound": True,
            "batch_runner_exists_and_is_hash_bound": True,
            "local_result_validator_exists_and_is_hash_bound": True,
            "official_core_validator_exists_and_is_hash_bound": True,
            "runtime_bootstrap_and_preflight_receipt_hash_bound": True,
            "technical_canary_receipt_passed_and_hash_bound": True,
            "runtime_full_dependency_content_hash_bound": cross[
                "runtime_full_dependency_content_hash_bound"
            ],
            "human_review_completed": False,
            "review_level": "independent_agent_and_machine_review_only",
            "paper_custody": False,
            "production_custody": False,
            "local_model_execution_authorized_by_user": True,
            "manifest_builder_loaded_model": False,
        },
        "model_execution_performed": False,
        "model_action_count_at_manifest_build": 0,
    }
    validate_manifest(manifest)
    verify_manifest_against_filesystem(
        manifest, project_root=project, require_output_root_absent=True
    )
    return manifest


def validate_manifest(manifest: Any) -> None:
    require(isinstance(manifest, dict) and set(manifest) == TOP_KEYS, "manifest schema drift")
    assert_finite(manifest, "manifest")
    require(manifest["schema_version"] == SCHEMA_VERSION, "manifest version drift")
    require(manifest["status"] == STATUS, "manifest status drift")
    parse_utc(manifest["frozen_at_utc"], "manifest freeze time")
    valid_uuid4(manifest["run_id"], "run id")
    require(manifest["selected_variant"] == SELECTED_VARIANT, "selected variant must remain eight-process A/B")
    require(manifest["ordered_process_ids"] == list(ORDERED_PROCESS_IDS), "ordered eight-process plan drift")

    plan = manifest["process_plan"]
    require(isinstance(plan, dict) and set(plan) == PROCESS_PLAN_KEYS, "process plan schema drift")
    require(plan["ordered_stack_ids"] == list(STACKS), "stack order drift")
    require(plan["ordered_replicates"] == list(REPLICATES), "replicate order drift")
    require(plan["arm_evaluation_order_by_replicate"] == ARM_ORDER_BY_REPLICATE, "A/B arm order drift")
    require(plan["counts"] == EXPECTED_COUNTS, "eight-process count drift")
    require(plan["physical_processes_may_run_serially"] is True, "physical serial execution unexpectedly forbidden")

    user = manifest["user_authorization"]
    require(isinstance(user, dict) and set(user) == USER_AUTH_KEYS, "user authorization schema drift")
    require(user["authorized"] is True, "user execution authorization missing")
    require(user["source"] == "USER_IN_THREAD_EXPLICIT_AUTHORIZATION", "user authorization source drift")
    require(user["scope"] == "R13_REPRODUCIBILITY_8_PROCESS_AB_LOCAL_DEVELOPMENT_ONLY", "user authorization scope drift")
    require(isinstance(user["recorded_statement"], str) and bool(user["recorded_statement"]), "user authorization record missing")

    review = manifest["review_status"]
    require(isinstance(review, dict) and set(review) == REVIEW_KEYS, "review schema drift")
    require(review == {
        "human_review_completed": False,
        "review_level": "independent_agent_and_machine_review_only",
        "machine_allowlist_required": True,
        "independent_human_receipt_claimed": False,
    }, "review boundary drift")

    evidence = manifest["evidence_boundary"]
    require(isinstance(evidence, dict) and set(evidence) == EVIDENCE_KEYS, "evidence schema drift")
    require(evidence == {
        "label": EVIDENCE_LABEL,
        "development_only": True,
        "formal_experiment": False,
        "formal_confirmatory": False,
        "scientific_evidence_at_manifest_build": False,
        "paper_custody": False,
        "production_custody": False,
        "sampled_rlvr": False,
        "same_empirical_or_policy_fpr": False,
    }, "local development evidence boundary drift")

    artifacts = manifest["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) == ALL_ARTIFACT_ROLES, "artifact role coverage drift")
    for role, expected_path in ARTIFACT_RELATIVE_PATHS.items():
        record = artifacts[role]
        require(isinstance(record, dict) and set(record) == ARTIFACT_RECORD_KEYS, f"artifact record schema drift: {role}")
        require(record["path"] == expected_path, f"artifact path drift: {role}")
        valid_sha256(record["sha256"], f"artifact {role}")
        require(type(record["byte_length"]) is int and record["byte_length"] > 0, f"artifact byte length invalid: {role}")
    require(
        artifacts["local_execution_semantics_addendum"]["sha256"]
        == EXPECTED_LOCAL_EXECUTION_SEMANTICS_SHA256,
        "local execution-semantics addendum hash drift",
    )
    require(
        artifacts["local_technical_canary_spec"]["sha256"]
        == EXPECTED_LOCAL_TECHNICAL_CANARY_SPEC_SHA256,
        "local technical-canary spec hash drift",
    )
    canary_record = artifacts["canary_receipt"]
    require(
        isinstance(canary_record, dict)
        and set(canary_record) == ARTIFACT_RECORD_KEYS,
        "artifact record schema drift: canary_receipt",
    )
    _safe_relative_path(canary_record["path"], "artifact canary_receipt")
    require(
        canary_record["path"] not in set(ARTIFACT_RELATIVE_PATHS.values()),
        "canary receipt path aliases a frozen artifact",
    )
    valid_sha256(canary_record["sha256"], "artifact canary_receipt")
    require(
        type(canary_record["byte_length"]) is int
        and canary_record["byte_length"] > 0,
        "artifact byte length invalid: canary_receipt",
    )

    execution = manifest["execution"]
    require(isinstance(execution, dict) and set(execution) == EXECUTION_KEYS, "execution schema drift")
    require(execution["seed"] == FROZEN_SEED, "execution seed drift")
    require(execution["command_working_directory"] == ".", "command working directory drift")
    require(isinstance(execution["command"], list) and all(isinstance(item, str) and item for item in execution["command"]), "execution command invalid")
    require(execution["environment"] == {
        "PYTHONDONTWRITEBYTECODE": "1",
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
        "TOKENIZERS_PARALLELISM": "false",
    }, "execution environment drift")
    expected_execution_flags = {
        "sealed_batch": True,
        "intermediate_scientific_result_release_allowed": False,
        "result_dependent_change_to_later_cells_allowed": False,
        "adaptive_rerun_allowed": False,
        "same_manifest_retry_allowed": False,
        "failure_policy": "STOP_ENTIRE_BATCH_NO_SCIENTIFIC_INTERPRETATION",
        "retry_policy": "NEW_PREFROZEN_MANIFEST_RUN_ID_AND_OUTPUT_ROOT_REQUIRED_BEFORE_ANY_RETRY",
        "automatic_progression": False,
    }
    for field, expected in expected_execution_flags.items():
        require(execution[field] == expected, f"execution policy drift: {field}")

    output = manifest["output"]
    require(isinstance(output, dict) and set(output) == OUTPUT_KEYS, "output schema drift")
    root = _safe_relative_path(output["root"], "output root")
    require(root.startswith(f"{PROJECT_DIR_NAME}/{OUTPUT_ROOT_PREFIX}"), "output root prefix drift")
    require(output["binding_sha256"] == _output_binding(root), "output binding mismatch")
    require(output["must_be_absent_at_manifest_freeze"] is True, "output absence gate disabled")
    require(output["overwrite_allowed"] is False, "output overwrite enabled")
    _safe_relative_path(output["manifest_path"], "manifest path")

    result = manifest["result_policy"]
    require(isinstance(result, dict) and set(result) == RESULT_POLICY_KEYS, "result policy schema drift")
    require(
        result["release_condition"]
        == "ONLY_AFTER_ALL_EIGHT_PREFROZEN_PROCESSES_COMPLETE_AND_THE_BOUND_LOCAL_RESULT_VALIDATOR_OFFICIAL_CORE_VALIDATOR_AND_ABSOLUTE_EFFECT_ADDENDUM_PASS",
        "result release condition drift",
    )
    require(
        result["validator_required"] is True
        and result["local_result_validator_required"] is True
        and result["official_core_validator_required"] is True
        and result["absolute_effect_addendum_required"] is True,
        "result validation gate disabled",
    )
    require(result["technical_failure_label"] == "INVALID_BATCH_NO_SCIENTIFIC_CONCLUSION", "technical failure label drift")
    require(result["maximum_positive_claim"] == "NARROW_LOCAL_DEVELOPMENT_SUPPORT_IN_FOUR_FIXED_M0_AFFINE_STACKS_ONLY", "maximum claim drift")
    require(result["forbidden_claims"] == [
        "shared_semantic_identity_causation",
        "same_empirical_or_policy_fpr",
        "population_task_generalization",
        "formal_or_confirmatory_inference",
        "paper_or_production_custody",
    ], "forbidden claim boundary drift")

    preflight = manifest["preflight"]
    require(isinstance(preflight, dict) and set(preflight) == PREFLIGHT_KEYS, "preflight schema drift")
    require(preflight == {
        "all_required_artifacts_exist_and_are_hash_bound": True,
        "real_backend_exists_and_is_hash_bound": True,
        "batch_runner_exists_and_is_hash_bound": True,
        "local_result_validator_exists_and_is_hash_bound": True,
        "official_core_validator_exists_and_is_hash_bound": True,
        "runtime_bootstrap_and_preflight_receipt_hash_bound": True,
        "technical_canary_receipt_passed_and_hash_bound": True,
        "runtime_full_dependency_content_hash_bound": False,
        "human_review_completed": False,
        "review_level": "independent_agent_and_machine_review_only",
        "paper_custody": False,
        "production_custody": False,
        "local_model_execution_authorized_by_user": True,
        "manifest_builder_loaded_model": False,
    }, "preflight boundary drift")
    require(manifest["model_execution_performed"] is False, "manifest claims model execution")
    require(manifest["model_action_count_at_manifest_build"] == 0, "manifest builder action count drift")


def verify_manifest_against_filesystem(
    manifest: dict[str, Any],
    *,
    project_root: Path,
    require_output_root_absent: bool,
) -> None:
    validate_manifest(manifest)
    project = project_root.resolve()
    require(project.name == PROJECT_DIR_NAME, "project root name drift")
    artifacts = manifest["artifacts"]
    for role, record in artifacts.items():
        path = _resolve_beneath(project, record["path"], f"artifact {role}")
        require(path.exists() and path.is_file(), f"BLOCKED_MISSING_ARTIFACT[{role}]: {path}")
        require(not path.is_symlink(), f"BLOCKED_SYMLINK_ARTIFACT[{role}]: {path}")
        require(path.stat().st_size == record["byte_length"], f"artifact byte-length mismatch: {role}")
        require(sha256_file(path) == record["sha256"], f"artifact hash mismatch: {role}")
    _validate_cross_bindings(
        project,
        artifacts,
        manifest_frozen_at_utc=manifest["frozen_at_utc"],
    )
    _validate_canary_receipt(
        project_root=project,
        artifacts=artifacts,
        manifest_frozen_at_utc=manifest["frozen_at_utc"],
    )
    workspace_root = project.parent
    expected_command = _expected_command(
        project,
        _resolve_beneath(workspace_root, manifest["output"]["manifest_path"], "manifest path"),
    )
    require(manifest["execution"]["command"] == expected_command, "execution command drift")
    output_root = _resolve_beneath(workspace_root, manifest["output"]["root"], "output root")
    if require_output_root_absent:
        require(not output_root.exists(), "BLOCKED_OUTPUT_ROOT_ALREADY_EXISTS")


def read_manifest(path: Path) -> dict[str, Any]:
    value = read_strict_json(path, "local development manifest")
    raw = path.read_bytes()
    require(raw == canonical_json_bytes(value) + b"\n", "manifest is not canonical JSON plus one LF")
    validate_manifest(value)
    return value


def atomic_write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise R13LocalManifestError(f"refusing to overwrite manifest: {path}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a new canonical manifest")
    build.add_argument("--project-root", type=Path, default=_default_project_root())
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--canary-receipt", type=Path, required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--frozen-at-utc", required=True)
    verify = subparsers.add_parser("verify", help="verify an existing manifest")
    verify.add_argument("--project-root", type=Path, default=_default_project_root())
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--prelaunch", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "build":
        manifest = build_manifest(
            project_root=args.project_root,
            manifest_path=args.output.resolve(),
            output_root=args.output_root.resolve(),
            canary_receipt_path=args.canary_receipt.resolve(),
            run_id=args.run_id,
            frozen_at_utc=args.frozen_at_utc,
        )
        data = canonical_json_bytes(manifest) + b"\n"
        atomic_write_new(args.output.resolve(), data)
        print(
            json.dumps(
                {
                    "status": STATUS,
                    "manifest_sha256": sha256_bytes(data),
                    "selected_variant": SELECTED_VARIANT,
                    "processes": 8,
                    "model_actions": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    manifest = read_manifest(args.manifest.resolve())
    verify_manifest_against_filesystem(
        manifest,
        project_root=args.project_root,
        require_output_root_absent=args.prelaunch,
    )
    print(
        json.dumps(
            {
                "status": "LOCAL_DEVELOPMENT_MANIFEST_VERIFIED_MODEL_NOT_LOADED",
                "selected_variant": SELECTED_VARIANT,
                "processes": 8,
                "model_actions": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
