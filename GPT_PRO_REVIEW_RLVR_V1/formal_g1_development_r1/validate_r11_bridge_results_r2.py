"""Fail-closed post-run validator for the R11 BUG/GOLD_ONLY bridge.

This module is intentionally separate from the frozen R11 release.  It does
not load a tokenizer or model.  A PASS requires an externally supplied hash
for both the frozen master and the post-run index, plus an exact custody chain
for every one of the 32 stack/arm/replicate processes.
"""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import hashlib
import itertools
import json
import math
import os
from pathlib import Path, PurePath
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence


STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
)
ARMS = ("BUG", "GOLD_ONLY")
REPLICATES = ("A", "B")
IDENTITIES = tuple(f"Z7_PLUS{i}" for i in range(1, 6))
CELL_IDS = tuple(
    f"{stack}|{arm}|{replicate}"
    for stack in STACKS
    for arm in ARMS
    for replicate in REPLICATES
)
CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))

LEGACY_INCOMPATIBLE_PROTOCOL_SHA256 = (
    "c9015c8d04da3b95d43162ab087e5fe4b4dcf1c4a5e689f2df610d2e379efd78"
)
EXPECTED_PARENT_RUNNER_SHA256 = (
    "439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d"
)
EXPECTED_MODEL_REVISION = "a10cc1512eabd3dde888204e902eca88bddb4951"
EXPECTED_MODEL_INVENTORY_HASHES = {
    "config.json": "224f72354f10d617a359cc82ad15a3c96e866b9b2ffadb81997eeea9e88e22ee",
    "generation_config.json": "87b916edaaab66b3899b9d0dd0752727dff6666686da0504d89ae0a6e055a013",
    "merges.txt": "0b54e8aa4e53d5383e2e4bc635a56b43f9647f7b13832d5d9ecd8f82dac4f510",
    "model.safetensors": "e6bffe7435d7ddc10fd3b9a9efd429dafbacb1cb17015fb5562664e7532bf86e",
    "special_tokens_map.json": "2b7379f3ae813529281a5c602bc5a11c1d4e0a99107aaa597fe936c1e813ca52",
    "tokenizer.json": "9ca9acddb6525a194ec8ac7a87f24fbba7232a9a15ffa1af0c1224fcd888e47c",
    "tokenizer_config.json": "4ec77d44f62efeb38d7e044a1db318f6a939438425312dfa333b8382dbad98df",
    "vocab.json": "82b84012e3add4d01d12ba14442026e49b8cbbaead1f79ecf3d919784f82dc79",
}
EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS = (
    "commitment_core.py",
    "formal_g1_development_r1/validate_r11_bridge_results_r2.py",
    "mvp_same_source_v1/mvp_static_contract.py",
    "mvp_same_source_v1/r11_bridge_contract.py",
    "mvp_same_source_v1/r11_static_contract.py",
    "mvp_same_source_v1/r12_invocation_custody.py",
    "mvp_same_source_v1/run_same_source_bridge_r11.py",
    "mvp_same_source_v1/run_same_source_bridge_r12_custody.py",
    "mvp_same_source_v1/run_same_source_mvp.py",
    "mvp_same_source_v1/validate_mvp_replicates_r3.py",
)
# Deliberately unset until the runner computes GOLD once per process, emits a
# single execution event reused across five identity labels, and the reward
# contract emits the R12 domain-separated commitment.  Filling these with the
# old R11 hashes would turn unique artifacts into a false execution count.
APPROVED_SHARED_GOLD_RUNNER_SHA256: str | None = None
APPROVED_R12_REWARD_CONTRACT_SHA256: str | None = None
APPROVED_SHARED_GOLD_PROTOCOL_SHA256: str | None = None
BLOCKED_RUNTIME_SEMANTICS_NOT_REFROZEN = (
    "BLOCKED_RUNTIME_SEMANTICS_NOT_REFROZEN"
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
METRIC_TOLERANCE = 2e-7
REPLICATE_TOLERANCE = 1e-12
NUMERICAL_DEAD_ZONE_EPSILON = 1e-6

EVIDENCE_BOUNDARY = {
    "audit_status": "NOT_HIDDEN_AUDIT",
    "data_scope": "CALIBRATION_ONLY",
    "formal_g1_status": "NOT_FORMAL_G1",
    "rlvr_mode": "NOT_SAMPLED_RLVR",
    "same_fpr_evidence": "NOT_SAME_FPR_EVIDENCE",
    "scientific_evidence_status": "SCIENTIFIC_EVIDENCE_FALSE",
}
NORM_EVIDENCE_BOUNDARY = {
    "independent_validation_input": "NOT_AN_INDEPENDENT_VALIDATION_INPUT",
    "used_for_diagnostic_gates": False,
    "used_for_scientific_explanation": False,
    "validation_status": "RUNNER_REPORTED_INTERNAL_CONSISTENCY_ONLY",
}
RESULT_CLAIM_BOUNDARY = [
    "Not a formal run and not confirmatory evidence.",
    "The eight-stack fixed-candidate screen is diagnostic development evidence, not a formal G1 result.",
    "Fixed-candidate exact expected-reward optimization is an RLVR surrogate, not sampled RLVR.",
    "Calibration-only readouts are development data and cannot be reported as hidden audit performance.",
    "A pass only justifies the next frozen G1 and sampled-RLVR bridge experiments.",
]
R11_CLAIM_BOUNDARY = [
    "Development screen only; not formal or confirmatory evidence.",
    "BUG versus GOLD_ONLY differs only by the signed reward mask.",
    "Two task pairs are the scientific clusters; 32 processes are not 32 scientific samples.",
    "Calibration-only fixed-candidate optimization is not sampled RLVR or hidden audit evidence.",
    "A pass does not automatically authorize any later stage.",
]
STATIC_AUTHORIZATION = {
    "cpu_static_repair": True,
    "tokenizer_load": False,
    "model_weight_load": False,
    "model_forward": False,
    "gradient": False,
    "optimizer_step": False,
    "fixed_candidate_r11_bug_gold_development_bridge": False,
    "sampled_rlvr": False,
    "hidden_audit": False,
}
ALLOWED_OPERATIONS = [
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "fixed_candidate_r11_bug_gold_development_bridge",
]
FORBIDDEN_OPERATIONS = [
    "audit_row_access",
    "hidden_audit",
    "sampled_rlvr",
    "multi_step_rlvr",
    "formal_or_confirmatory_claim",
    "adaptive_retry",
    "outcome_dependent_rerun",
]
INVOCATION_CUSTODY = (
    "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY"
)
R12_PLAN_SCHEMA = "r12-r11-invocation-freeze-plan-r1"
R12_PLAN_STATUS = "FROZEN_UNCONSUMED_BEFORE_MODEL_LOAD"
R12_CLAIM_SCHEMA = "r12-r11-invocation-consumption-claim-r1"
R12_CLAIM_STATUS = "CONSUMED_ONCE_BEFORE_MODEL_LOAD"
R12_CONSUMPTION_POLICY = "ONE_CLAIM_PER_SIGNED_LAUNCH_AND_CELL_AND_UNIQUE_NONCE"
R12_PLAN_CUSTODY = (
    "STORE_PLAN_AND_RECEIPT_OUTSIDE_RESULT_DIRECTORY_AND_ANCHOR_BOTH_HASHES"
)
R12_MAX_INVOCATION_AGE = timedelta(minutes=10)
R12_MAX_SIGNED_LAUNCH_TTL = timedelta(hours=24)
SIGNED_LAUNCH_ENVELOPE_SCHEMA = (
    "r12-signed-32-cell-launch-authorization-envelope-r1"
)
SIGNED_LAUNCH_PAYLOAD_SCHEMA = (
    "r12-signed-32-cell-launch-authorization-payload-r1"
)
SIGNED_LAUNCH_STATUS = "AUTHORIZED_BY_EXTERNAL_TRUSTED_SIGNER"
SIGNED_LAUNCH_VERSION = "r12-signed-32-cell-launch-v1"
SIGNED_LAUNCH_CONTEXT = b"RLVR-R12-SIGNED-32-CELL-LAUNCH-AUTHORIZATION-V1\x00"
# Deliberately unset.  A production embedding must inject a verifier backed by
# a human-controlled trust store outside this writable repository.  There is no
# CLI option for installing a signer, key, fingerprint, or verifier.
PRODUCTION_TRUSTED_SIGNER_POLICY: "TrustedSignerPolicy | None" = None
BLOCKED_EXTERNAL_TRUST_ANCHOR = "BLOCKED_EXTERNAL_TRUST_ANCHOR"
REWARD_MASK_COMMITMENT_SCHEME = (
    "r12-reward-mask-v1:sha256-domain-separated-canonical-ascii-json-lf"
)

RESULT_KEYS = {
    "schema_version",
    "run_label",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "replicate_id",
    "run_nonce",
    "invocation_start_receipt_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "norm_evidence_boundary",
    "created_at_utc",
    "execution_manifest_sha256",
    "execution_manifest_bindings_sha256",
    "config_sha256",
    "runner_sha256",
    "validator_sha256",
    "mapping_stacks_sha256",
    "asset_validation_sha256",
    "determinism_addendum_sha256",
    "model_recursive_inventory",
    "model_recursive_inventory_sha256",
    "chat_template_sha256",
    "determinism_fail_closed",
    "source_bundles_sha256",
    "target_calibration_sha256",
    "mapping_stack_id",
    "source_row_count",
    "target_row_count",
    "source_rows_commitment",
    "target_rows_commitment",
    "ordered_candidate_set",
    "trainable_parameters",
    "initial_trainable_hash",
    "initial_parameter_hash",
    "candidate_supervised_token_count_min",
    "candidate_supervised_token_count_max",
    "candidate_supervised_token_count_range",
    "base_source_gate_metrics",
    "base_target_metrics",
    "pre_source_row_traces",
    "pre_target_row_traces",
    "post_target_row_traces_by_source_identity",
    "source_update_parameter_hashes",
    "diagnostic_gate_status",
    "diagnostic_gate_failures",
    "source_updates",
    "source_update_execution_events",
    "evaluation_cells",
    "stack_summary",
    "restore_max_abs_target_score_error",
    "unique_source_update_hash_count",
    "run_status",
    "runtime_seconds",
    "peak_gpu_memory_gib",
    "claim_boundary",
    "bridge_schema_version",
    "arm",
    "r11_arm_contract_sha256",
    "r11_protocol_sha256",
    "parent_r10_runner_sha256",
    "unique_permitted_treatment_difference",
    "development_screen_only",
    "formal_confirmatory",
    "sampled_rlvr",
    "hidden_audit",
    "reward_mask_commitment_scheme",
    "r12_custody_schema_version",
    "r12_invocation_plan_sha256",
    "r12_consumption_claim_sha256",
    "r12_consumption_claim_path",
    "r12_signed_launch_authorization_sha256",
    "r12_signed_launch_message_sha256",
    "r12_trusted_signer_id",
    "r12_trusted_signer_key_fingerprint_sha256",
    "r12_custody_runner_sha256",
    "r12_final_output_path",
    "r12_claimed_before_model_load",
}
UPDATE_KEYS = {
    "source_rule_identity",
    "source_update_hash",
    "arm",
    "reward_mask_sha256",
    "mean_pre_update_expected_reward",
    "raw_gradient_norm",
    "clip_grad_norm_return",
    "realized_update_norm",
    "clipping_not_triggered",
    "norm_evidence_boundary",
    "diagonal_excess",
}
UPDATE_EXECUTION_EVENT_KEYS = {
    "execution_event_id",
    "execution_ordinal",
    "execution_identity",
    "source_update_hash",
    "reward_mask_sha256",
    "optimizer_step_count",
    "target_identity_read_count",
    "reused_for_source_rule_identities",
}
CELL_KEYS = {
    "source_rule_identity",
    "target_rule_identity",
    "source_update_hash",
    "same_update_reference",
    "pre_target_metric",
    "post_target_metric",
    "effect",
    "is_diagonal",
}
TRACE_KEYS = {
    "row_id",
    "state",
    "source_rule_identity",
    "source_update_hash",
    "ordered_candidate_scores",
    "supervised_token_counts",
    "gold_candidate",
    "offset_candidates",
}
SEMANTIC_ROW_KEYS = {"row_id", "gold_candidate", "offset_candidates"}
MEMBERSHIP_KEYS = {
    "source_rows_commitment",
    "source_trace_semantics",
    "target_rows_commitment",
    "target_trace_semantics",
}
INVENTORY_ROW_KEYS = {"path", "sha256", "size"}
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "model_execution_performed",
    "mapping_stack_ids",
    "selected_mapping_stack_id",
    "stack_membership_commitments",
    "source_rule_offsets_mod7",
    "target_rule_offsets_mod7",
    "bindings",
    "runtime_expected",
    "runtime_observed_at_manifest_build",
    "determinism_environment",
    "audit_contract",
    "authorization",
    "bridge_cell_id",
    "arm",
    "replicate_id",
    "development_screen_only",
    "formal_confirmatory",
    "unique_permitted_treatment_difference",
}
MANIFEST_BINDING_KEYS = {
    "asset_validation_sha256",
    "audit_file_sha256_from_seal",
    "audit_seal_receipt_sha256",
    "chat_template_sha256",
    "config_sha256",
    "determinism_addendum_sha256",
    "mapping_stacks_sha256",
    "model_recursive_inventory",
    "model_recursive_inventory_sha256",
    "runner_sha256",
    "source_bundles_sha256",
    "static_contract_sha256",
    "target_calibration_sha256",
    "validator_sha256",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "r11_arm_contract_sha256",
    "r11_bridge_contract_sha256",
}
ARM_CONTRACT_KEYS = {
    "schema_version",
    "status",
    "cell_id",
    "arm",
    "reward_specification",
    "unique_permitted_treatment_difference",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "mapping_stack_id",
    "replicate_id",
    "model_execution_authorized",
}
MASTER_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "model_execution_performed",
    "ordered_cell_ids",
    "required_process_count",
    "required_unique_arm_specific_design_cells",
    "required_technical_update_executions",
    "required_target_identity_evaluation_cells",
    "config_sha256_by_stack",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "shared_runner_sha256",
    "shared_validator_sha256",
    "shared_completion_validator_sha256",
    "shared_static_contract_sha256",
    "shared_bridge_contract_sha256",
    "shared_parent_r10_runner_sha256",
    "shared_r11_protocol_sha256",
    "shared_model_recursive_inventory_sha256",
    "inclusion_rule",
    "exclusion_rule",
    "claim_boundary",
    "authorization",
}
AUTHORIZATION_KEYS = {
    "schema_version",
    "status",
    "action_id",
    "version",
    "authorization_id",
    "issued_at_utc",
    "expires_at_utc",
    "master_inclusion_contract_sha256",
    "ordered_cell_ids",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "runner_sha256",
    "validator_sha256",
    "completion_validator_sha256",
    "static_contract_sha256",
    "bridge_contract_sha256",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "model_recursive_inventory_sha256",
    "allowed_operations",
    "forbidden_operations",
    "model_execution_authorized",
    "evidence_boundary",
}
INVOCATION_KEYS = {
    "schema_version",
    "status",
    "evidence_boundary",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "started_at_utc",
    "custody_requirement",
}
PLAN_KEYS = {
    "schema_version",
    "status",
    "consumption_policy",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "expected_result_output_path",
    "claim_ledger_root",
    "master_inclusion_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "signed_launch_authorization_sha256",
    "signed_launch_message_sha256",
    "trusted_signer_id",
    "trusted_signer_key_fingerprint_sha256",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "r11_invocation_receipt_sha256",
    "r12_custody_contract_sha256",
    "r12_custody_runner_sha256",
    "started_at_utc",
    "claim_not_after_utc",
    "custody_requirement",
    "model_execution_performed",
}
CLAIM_KEYS = {
    "schema_version",
    "status",
    "claim_key_sha256",
    "consumption_policy",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "expected_result_output_path",
    "output_absent_at_claim",
    "master_inclusion_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "signed_launch_authorization_sha256",
    "signed_launch_message_sha256",
    "trusted_signer_id",
    "trusted_signer_key_fingerprint_sha256",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "invocation_receipt_sha256",
    "invocation_plan_sha256",
    "started_at_utc",
    "claimed_at_utc",
}
SIGNED_LAUNCH_ENVELOPE_KEYS = {
    "schema_version",
    "payload",
    "signature",
}
SIGNED_LAUNCH_SIGNATURE_KEYS = {
    "algorithm",
    "signer_id",
    "key_fingerprint_sha256",
    "signature_base64",
}
SIGNED_LAUNCH_PAYLOAD_KEYS = {
    "schema_version",
    "status",
    "action_id",
    "version",
    "authorization_id",
    "issued_at_utc",
    "expires_at_utc",
    "master_inclusion_contract_path",
    "master_inclusion_contract_sha256",
    "r11_authorization_receipt_sha256",
    "ordered_cell_ids",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "run_nonce_by_cell",
    "expected_result_output_path_by_cell",
    "started_at_utc_by_cell",
    "claim_ledger_root",
    "r12_custody_contract_sha256",
    "r12_custody_runner_sha256",
    "model_revision",
    "source_dependency_sha256_by_path",
    "allowed_operations",
    "forbidden_operations",
    "model_execution_authorized",
    "evidence_boundary",
}
RUN_INDEX_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "master_inclusion_contract_sha256",
    "authorization_receipt_sha256",
    "ordered_cell_ids",
    "cells",
}
RUN_INDEX_CELL_KEYS = {
    "result_path",
    "result_sha256",
    "manifest_path",
    "manifest_sha256",
    "arm_contract_path",
    "arm_contract_sha256",
    "invocation_receipt_path",
    "invocation_receipt_sha256",
    "invocation_plan_path",
    "invocation_plan_sha256",
    "consumption_claim_path",
    "consumption_claim_sha256",
}


class R11V2ValidationError(RuntimeError):
    pass


class ExternalTrustAnchorRequired(R11V2ValidationError):
    pass


class RuntimeSemanticsRepairRequired(R11V2ValidationError):
    pass


@dataclass(frozen=True)
class TrustedSignerPolicy:
    signer_id: str
    key_fingerprint_sha256: str
    signature_algorithm: str
    verify_signature: Callable[[bytes, bytes], bool]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R11V2ValidationError(message)


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
    except (TypeError, ValueError) as error:
        raise R11V2ValidationError("value is not canonical finite JSON") from error


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
        f"{label} must be a canonical lowercase SHA-256",
    )
    return value


def valid_uuid4(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and UUID4_RE.fullmatch(value) is not None,
        f"{label} must be a lowercase UUIDv4",
    )
    return value


def parse_rfc3339_utc(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{label} must end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise R11V2ValidationError(f"{label} is not RFC3339 UTC") from error
    require(parsed.tzinfo is not None, f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc)


def finite_number(value: Any, label: str) -> float:
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{label} is not numeric",
    )
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise R11V2ValidationError(f"{label} cannot be represented finitely") from error
    require(math.isfinite(result), f"{label} is non-finite")
    return result


def assert_finite(value: Any, path: str = "root") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        finite_number(value, path)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")
        return
    raise R11V2ValidationError(f"{path} contains unsupported value type")


def safe_subtract(left: Any, right: Any, label: str) -> float:
    result = finite_number(left, f"{label}.left") - finite_number(
        right, f"{label}.right"
    )
    require(math.isfinite(result), f"{label} overflowed")
    return result


def safe_mean(values: Iterable[Any], label: str) -> float:
    items = [finite_number(value, f"{label}[{index}]") for index, value in enumerate(values)]
    require(bool(items), f"{label} is empty")
    try:
        total = math.fsum(items)
    except (OverflowError, ValueError) as error:
        raise R11V2ValidationError(f"{label} sum overflowed") from error
    result = total / len(items)
    require(math.isfinite(result), f"{label} mean is non-finite")
    return result


def close(left: Any, right: Any, *, tolerance: float = METRIC_TOLERANCE) -> bool:
    try:
        lvalue = finite_number(left, "close.left")
        rvalue = finite_number(right, "close.right")
    except R11V2ValidationError:
        return False
    return abs(lvalue - rvalue) <= tolerance


def strict_json_loads(raw: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise R11V2ValidationError(f"{label} contains forbidden constant {value}")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise R11V2ValidationError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R11V2ValidationError(f"{label} is not strict UTF-8 JSON") from error
    require(isinstance(value, dict), f"{label} must contain a JSON object")
    assert_finite(value, label)
    return value


def require_no_link_ancestors(path: Path, label: str) -> None:
    selected = path.expanduser()
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    cursor = selected
    while True:
        require(not cursor.is_symlink(), f"{label} path contains a symlink")
        is_junction = getattr(cursor, "is_junction", None)
        if is_junction is not None:
            require(not is_junction(), f"{label} path contains a junction")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent


def read_regular_file_snapshot(path: Path, label: str) -> tuple[bytes, str]:
    require_no_link_ancestors(path, label)
    try:
        path_before = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise R11V2ValidationError(f"cannot stat {label}: {path}") from error
    require(stat.S_ISREG(path_before.st_mode), f"{label} is not a regular file")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise R11V2ValidationError(f"cannot open {label}: {path}") from error
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), f"{label} is not a regular file")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise R11V2ValidationError(f"cannot restat {label}: {path}") from error
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    require(
        identity(path_before)
        == identity(before)
        == identity(after)
        == identity(path_after),
        f"{label} path or bytes changed while it was read",
    )
    raw = b"".join(chunks)
    actual = sha256_bytes(raw)
    return raw, actual


def read_hashed_json(path: Path, expected_sha256: str, label: str) -> tuple[dict[str, Any], str]:
    valid_sha256(expected_sha256, f"expected {label} hash")
    raw, actual = read_regular_file_snapshot(path, label)
    require(actual == expected_sha256, f"{label} hash mismatch")
    parsed = strict_json_loads(raw, label)
    require(raw == canonical_json_bytes(parsed), f"{label} raw bytes are not canonical JSON")
    return parsed, actual


def safe_relative_path(root: Path, relative: Any, label: str) -> Path:
    require(isinstance(relative, str) and relative, f"{label} path is invalid")
    pure = PurePath(relative)
    require(not pure.is_absolute() and ".." not in pure.parts, f"{label} path escapes root")
    require(
        all(part.casefold() != "__pycache__" for part in pure.parts)
        and pure.suffix.casefold() not in {".pyc", ".pyo"},
        f"{label} path refers to generated Python bytecode",
    )
    unresolved = root / relative
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"{label} path contains a symlink")
        is_junction = getattr(cursor, "is_junction", None)
        if is_junction is not None:
            require(not is_junction(), f"{label} path contains a junction")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise R11V2ValidationError(f"{label} path escapes root") from error
    require(candidate.is_file(), f"{label} is not a file")
    return candidate


def read_relative_json(
    root: Path, relative: Any, expected_sha256: str, label: str
) -> tuple[dict[str, Any], str, Path]:
    path = safe_relative_path(root, relative, label)
    value, actual = read_hashed_json(path, expected_sha256, label)
    return value, actual, path


def normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def same_path(left: Path, right: Path) -> bool:
    return normalized_path(left) == normalized_path(right)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.expanduser().resolve(strict=False).relative_to(
            parent.expanduser().resolve(strict=False)
        )
    except ValueError:
        return False
    return True


def source_dependency_inventory(
    source_image_root: Path | None = None,
) -> dict[str, str]:
    """Hash and statically close the local Python dependency graph.

    The signed launch envelope commits this exact map.  Only literal local
    imports are accepted: an unlisted local module, generated bytecode, or a
    non-literal ``import_module`` target blocks validation and requires a new
    freeze.  Third-party and standard-library modules are intentionally bound
    by the frozen runtime manifest rather than copied into this source map.
    """

    project_root = (
        Path(__file__).resolve().parents[1]
        if source_image_root is None
        else source_image_root.resolve()
    )
    expected_paths = set(EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS)
    module_paths = {
        Path(relative).stem: relative
        for relative in EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS
    }
    local_source_root = project_root / "mvp_same_source_v1"
    available_local_modules = {
        path.stem
        for root in (project_root, local_source_root)
        for path in root.glob("*.py")
        if path.is_file()
    }
    inventory: dict[str, str] = {}
    referenced_local_modules: set[str] = set()
    for relative in EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS:
        pure = PurePath(relative)
        require(
            not pure.is_absolute()
            and ".." not in pure.parts
            and pure.suffix.casefold() == ".py"
            and all(part.casefold() != "__pycache__" for part in pure.parts),
            f"source dependency path is not canonical source: {relative}",
        )
        path = project_root / relative
        require_no_link_ancestors(path, f"source dependency {relative}")
        require(path.is_file(), f"source dependency is missing: {relative}")
        bytecode_candidates = [
            path.with_suffix(".pyc"),
            path.with_suffix(".pyo"),
        ]
        cache_root = path.parent / "__pycache__"
        if cache_root.is_dir():
            bytecode_candidates.extend(cache_root.glob(f"{path.stem}.*.pyc"))
            bytecode_candidates.extend(cache_root.glob(f"{path.stem}.*.pyo"))
        require(
            not any(candidate.exists() for candidate in bytecode_candidates),
            f"unbound Python bytecode exists for source dependency: {relative}",
        )
        raw, source_sha256 = read_regular_file_snapshot(
            path, f"source dependency {relative}"
        )
        try:
            tree = ast.parse(raw, filename=str(path))
        except (SyntaxError, UnicodeError) as error:
            raise R11V2ValidationError(
                f"source dependency cannot be parsed: {relative}"
            ) from error
        importlib_module_aliases = {"importlib"}
        import_module_function_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        importlib_module_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_function_aliases.add(
                            alias.asname or alias.name
                        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                referenced_local_modules.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name.split(".", 1)[0] in available_local_modules
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                local_name = node.module.split(".", 1)[0]
                if local_name in available_local_modules:
                    referenced_local_modules.add(local_name)
            elif isinstance(node, ast.Call) and (
                (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in importlib_module_aliases
                    and node.func.attr == "import_module"
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id in import_module_function_aliases
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                )
            ):
                require(
                    len(node.args) >= 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str),
                    f"non-literal dynamic import in source dependency: {relative}",
                )
                local_name = node.args[0].value.split(".", 1)[0]
                if local_name in available_local_modules:
                    referenced_local_modules.add(local_name)
        inventory[relative] = source_sha256
    missing_modules = sorted(referenced_local_modules - set(module_paths))
    require(
        not missing_modules,
        "local source dependency closure is incomplete: "
        + ", ".join(missing_modules),
    )
    require(
        set(inventory) == expected_paths,
        "local source dependency inventory coverage drift",
    )
    return dict(sorted(inventory.items()))


def validate_bound_runtime_compatibility(master: dict[str, Any]) -> None:
    """Refuse to bless the old five-times-GOLD runner as a 96-event design."""

    if (
        APPROVED_SHARED_GOLD_RUNNER_SHA256 is None
        or APPROVED_R12_REWARD_CONTRACT_SHA256 is None
        or APPROVED_SHARED_GOLD_PROTOCOL_SHA256 is None
    ):
        raise RuntimeSemanticsRepairRequired(
            f"{BLOCKED_RUNTIME_SEMANTICS_NOT_REFROZEN}: repair the runner to "
            "compute GOLD once, emit one execution event with five labeled "
            "reuses, implement the R12 reward-mask commitment, replace the "
            "legacy 160-execution protocol, then review and freeze all three "
            "source hashes in this validator"
        )
    valid_sha256(
        APPROVED_SHARED_GOLD_RUNNER_SHA256,
        "approved shared-GOLD runner hash",
    )
    valid_sha256(
        APPROVED_R12_REWARD_CONTRACT_SHA256,
        "approved R12 reward-contract hash",
    )
    valid_sha256(
        APPROVED_SHARED_GOLD_PROTOCOL_SHA256,
        "approved shared-GOLD protocol hash",
    )
    require(
        master["shared_runner_sha256"] == APPROVED_SHARED_GOLD_RUNNER_SHA256,
        "master runner is not the reviewed shared-GOLD-once implementation",
    )
    require(
        master["shared_bridge_contract_sha256"]
        == APPROVED_R12_REWARD_CONTRACT_SHA256,
        "master bridge contract is not the reviewed R12 reward commitment",
    )
    require(
        master["shared_r11_protocol_sha256"]
        == APPROVED_SHARED_GOLD_PROTOCOL_SHA256,
        "master protocol is not the reviewed 96-execution shared-GOLD protocol",
    )


def require_independent_roots(run_root: Path, custody_root: Path) -> None:
    require(
        not is_within(run_root, custody_root)
        and not is_within(custody_root, run_root),
        "custody root and result root must be independent directories",
    )


def claim_key_sha256(signed_launch_message_sha256: str, selected_cell: str) -> str:
    valid_sha256(signed_launch_message_sha256, "signed launch message hash")
    require(selected_cell in CELL_IDS, "claim cell is not frozen")
    return sha256_bytes(
        canonical_json_bytes(
            {
                "signed_launch_message_sha256": signed_launch_message_sha256,
                "cell_id": selected_cell,
            }
        )
    )


def cell_id(stack: str, arm: str, replicate: str) -> str:
    require(stack in STACKS, f"unknown stack {stack}")
    require(arm in ARMS, f"unknown arm {arm}")
    require(replicate in REPLICATES, f"unknown replicate {replicate}")
    return f"{stack}|{arm}|{replicate}"


def validate_master(master: dict[str, Any], actual_sha256: str) -> None:
    require(set(master) == MASTER_KEYS, "master schema drift")
    require(master["schema_version"] == "r11-bridge-32-cell-master-inclusion-r1", "master version drift")
    require(master["status"] == "R11_32_CELL_FROZEN_AUTHORIZATION_PENDING", "master status drift")
    require(master["scientific_evidence"] is False, "master scientific status washing")
    require(master["formal_experiment"] is False, "master formal status washing")
    require(master["evidence_boundary"] == EVIDENCE_BOUNDARY, "master evidence boundary drift")
    require(master["model_execution_performed"] is False, "master execution status washing")
    require(master["ordered_cell_ids"] == list(CELL_IDS), "master cell order drift")
    expected_counts = {
        "required_process_count": 32,
        "required_unique_arm_specific_design_cells": 48,
        "required_technical_update_executions": 96,
        "required_target_identity_evaluation_cells": 480,
    }
    for field, expected_count in expected_counts.items():
        require(
            type(master[field]) is int and master[field] == expected_count,
            f"master {field} does not implement shared GOLD semantics",
        )
    require(set(master["config_sha256_by_stack"]) == set(STACKS), "master config coverage drift")
    require(set(master["manifest_sha256_by_cell"]) == set(CELL_IDS), "master manifest coverage drift")
    require(set(master["arm_contract_sha256_by_cell"]) == set(CELL_IDS), "master arm coverage drift")
    for collection in (
        master["config_sha256_by_stack"],
        master["manifest_sha256_by_cell"],
        master["arm_contract_sha256_by_cell"],
    ):
        for key, value in collection.items():
            valid_sha256(value, f"master hash {key}")
    require(
        len(set(master["config_sha256_by_stack"].values())) == len(STACKS),
        "master aliases two stack configs",
    )
    require(
        len(set(master["manifest_sha256_by_cell"].values())) == len(CELL_IDS),
        "master aliases two cell manifests",
    )
    require(
        len(set(master["arm_contract_sha256_by_cell"].values())) == len(CELL_IDS),
        "master aliases two cell arm contracts",
    )
    for field in (
        "shared_runner_sha256",
        "shared_validator_sha256",
        "shared_completion_validator_sha256",
        "shared_static_contract_sha256",
        "shared_bridge_contract_sha256",
        "shared_parent_r10_runner_sha256",
        "shared_r11_protocol_sha256",
        "shared_model_recursive_inventory_sha256",
    ):
        valid_sha256(master[field], f"master.{field}")
    current_validator = sha256_file(Path(__file__).resolve())
    require(
        master["shared_validator_sha256"] == current_validator
        and master["shared_completion_validator_sha256"] == current_validator,
        "master is not frozen to this validator v2",
    )
    project_root = Path(__file__).resolve().parents[1]
    local_bindings = {
        "shared_runner_sha256": project_root
        / "mvp_same_source_v1"
        / "run_same_source_bridge_r11.py",
        "shared_static_contract_sha256": project_root
        / "mvp_same_source_v1"
        / "r11_static_contract.py",
        "shared_bridge_contract_sha256": project_root
        / "mvp_same_source_v1"
        / "r11_bridge_contract.py",
        "shared_parent_r10_runner_sha256": project_root
        / "mvp_same_source_v1"
        / "run_same_source_mvp.py",
    }
    for field, path in local_bindings.items():
        require_no_link_ancestors(path, f"local master binding {field}")
        require(path.is_file(), f"missing local master dependency: {field}")
        require(
            master[field] == sha256_file(path),
            f"master local dependency hash drift: {field}",
        )
    validate_bound_runtime_compatibility(master)
    require(master["shared_parent_r10_runner_sha256"] == EXPECTED_PARENT_RUNNER_SHA256, "master parent runner drift")
    require(master["inclusion_rule"] == "RUN_ALL_32_STACK_ARM_REPLICATE_CELLS_WITHOUT_SELECTION", "master inclusion drift")
    require(master["exclusion_rule"] == "NO_CELL_IDENTITY_THRESHOLD_HYPERPARAMETER_RETRY_OR_OUTCOME_ADAPTATION", "master exclusion drift")
    require(master["claim_boundary"] == R11_CLAIM_BOUNDARY, "master claim boundary drift")
    require(
        master["authorization"]
        == {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
        "master self-authorization detected",
    )
    valid_sha256(actual_sha256, "actual master hash")


def validate_authorization(
    authorization: dict[str, Any],
    *,
    actual_sha256: str,
    master: dict[str, Any],
    master_sha256: str,
) -> tuple[str, datetime, datetime]:
    require(set(authorization) == AUTHORIZATION_KEYS, "authorization schema drift")
    require(authorization["schema_version"] == "r11-bridge-action-authorization-receipt-r1", "authorization version drift")
    require(authorization["status"] == "AUTHORIZED_BY_USER", "authorization is not active")
    require(authorization["action_id"] == "RUN_R11_BUG_GOLD_DEVELOPMENT_BRIDGE_32_PROCESS_R1", "authorization action drift")
    require(authorization["version"] == "R11_BUG_GOLD_32_CELL_DEVELOPMENT_R1", "authorization contract drift")
    authorization_id = valid_uuid4(authorization["authorization_id"], "authorization_id")
    issued = parse_rfc3339_utc(authorization["issued_at_utc"], "issued_at_utc")
    expires = parse_rfc3339_utc(authorization["expires_at_utc"], "expires_at_utc")
    require(issued < expires, "authorization interval is invalid")
    require(authorization["master_inclusion_contract_sha256"] == master_sha256, "authorization/master mismatch")
    require(authorization["ordered_cell_ids"] == list(CELL_IDS), "authorization cell order drift")
    require(authorization["manifest_sha256_by_cell"] == master["manifest_sha256_by_cell"], "authorization manifest map drift")
    require(authorization["arm_contract_sha256_by_cell"] == master["arm_contract_sha256_by_cell"], "authorization arm map drift")
    shared = {
        "runner_sha256": "shared_runner_sha256",
        "validator_sha256": "shared_validator_sha256",
        "completion_validator_sha256": "shared_completion_validator_sha256",
        "static_contract_sha256": "shared_static_contract_sha256",
        "bridge_contract_sha256": "shared_bridge_contract_sha256",
        "parent_r10_runner_sha256": "shared_parent_r10_runner_sha256",
        "r11_protocol_sha256": "shared_r11_protocol_sha256",
        "model_recursive_inventory_sha256": "shared_model_recursive_inventory_sha256",
    }
    for receipt_field, master_field in shared.items():
        require(authorization[receipt_field] == master[master_field], f"authorization binding drift: {receipt_field}")
    require(authorization["allowed_operations"] == ALLOWED_OPERATIONS, "authorization allowed operations drift")
    require(authorization["forbidden_operations"] == FORBIDDEN_OPERATIONS, "authorization forbidden operations drift")
    require(authorization["model_execution_authorized"] is True, "model execution is not authorized")
    require(authorization["evidence_boundary"] == EVIDENCE_BOUNDARY, "authorization evidence boundary drift")
    valid_sha256(actual_sha256, "actual authorization hash")
    return authorization_id, issued, expires


def validate_signed_launch_plan(
    launch_plan: dict[str, Any],
    *,
    actual_sha256: str,
    run_root: Path,
    index: dict[str, Any],
    master: dict[str, Any],
    master_path: Path,
    master_sha256: str,
    authorization_sha256: str,
    authorization_id: str,
    authorization_issued: datetime,
    authorization_expires: datetime,
    trusted_signer_policy: TrustedSignerPolicy | None,
) -> tuple[dict[str, Any], str, datetime, datetime]:
    if trusted_signer_policy is None:
        raise ExternalTrustAnchorRequired(
            f"{BLOCKED_EXTERNAL_TRUST_ANCHOR}: provision an operator-owned verifier outside the writable repository"
        )
    policy = trusted_signer_policy
    require(
        isinstance(policy.signer_id, str)
        and policy.signer_id.strip() == policy.signer_id
        and len(policy.signer_id) >= 8,
        "trusted signer id is invalid",
    )
    valid_sha256(policy.key_fingerprint_sha256, "trusted signer key fingerprint")
    require(
        isinstance(policy.signature_algorithm, str)
        and len(policy.signature_algorithm) >= 8,
        "trusted signature algorithm is invalid",
    )
    require(callable(policy.verify_signature), "trusted signature verifier is not callable")

    require(set(launch_plan) == SIGNED_LAUNCH_ENVELOPE_KEYS, "signed launch envelope schema drift")
    require(
        launch_plan["schema_version"] == SIGNED_LAUNCH_ENVELOPE_SCHEMA,
        "signed launch envelope version drift",
    )
    signature = launch_plan["signature"]
    require(
        isinstance(signature, dict) and set(signature) == SIGNED_LAUNCH_SIGNATURE_KEYS,
        "signed launch signature schema drift",
    )
    require(
        signature["algorithm"] == policy.signature_algorithm,
        "signed launch signature algorithm is not trusted",
    )
    require(
        signature["signer_id"] == policy.signer_id,
        "signed launch signer id is not trusted",
    )
    require(
        signature["key_fingerprint_sha256"] == policy.key_fingerprint_sha256,
        "signed launch signer fingerprint is not trusted",
    )
    signature_text = signature["signature_base64"]
    require(isinstance(signature_text, str) and signature_text, "signed launch signature missing")
    try:
        signature_bytes = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise R11V2ValidationError("signed launch signature is not strict base64") from error
    require(
        bool(signature_bytes)
        and base64.b64encode(signature_bytes).decode("ascii") == signature_text,
        "signed launch signature base64 is non-canonical",
    )
    payload = launch_plan["payload"]
    require(
        isinstance(payload, dict) and set(payload) == SIGNED_LAUNCH_PAYLOAD_KEYS,
        "signed launch payload schema drift",
    )
    signed_message = SIGNED_LAUNCH_CONTEXT + canonical_json_bytes(payload)
    try:
        verified = policy.verify_signature(signed_message, signature_bytes)
    except Exception as error:
        raise R11V2ValidationError("trusted signature verifier failed closed") from error
    require(
        verified is True,
        "signed launch-plan signature verification failed",
    )
    require(payload["schema_version"] == SIGNED_LAUNCH_PAYLOAD_SCHEMA, "signed launch payload version drift")
    require(payload["status"] == SIGNED_LAUNCH_STATUS, "signed launch is not authorized")
    require(
        payload["action_id"] == "RUN_R11_BUG_GOLD_DEVELOPMENT_BRIDGE_32_PROCESS_R1",
        "signed launch action drift",
    )
    require(payload["version"] == SIGNED_LAUNCH_VERSION, "signed launch contract version drift")
    require(
        payload["model_revision"] == EXPECTED_MODEL_REVISION,
        "signed launch model revision drift",
    )
    observed_source_inventory = payload["source_dependency_sha256_by_path"]
    require(
        isinstance(observed_source_inventory, dict)
        and list(observed_source_inventory)
        == list(EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS),
        "signed launch source dependency inventory coverage/order drift",
    )
    for relative, digest in observed_source_inventory.items():
        valid_sha256(digest, f"signed launch source dependency {relative}")
    require(
        observed_source_inventory == source_dependency_inventory(),
        "signed launch source dependency inventory/hash drift",
    )
    require(payload["authorization_id"] == authorization_id, "signed launch authorization id mismatch")
    issued = parse_rfc3339_utc(payload["issued_at_utc"], "launch issued_at_utc")
    expires = parse_rfc3339_utc(payload["expires_at_utc"], "launch expires_at_utc")
    require(
        authorization_issued <= issued < expires <= authorization_expires
        and expires - issued <= R12_MAX_SIGNED_LAUNCH_TTL,
        "signed launch-plan window is outside the authorization receipt",
    )
    require(
        isinstance(payload["master_inclusion_contract_path"], str)
        and Path(payload["master_inclusion_contract_path"]).is_absolute()
        and same_path(Path(payload["master_inclusion_contract_path"]), master_path),
        "signed launch plan/master path mismatch",
    )
    require(
        payload["master_inclusion_contract_sha256"] == master_sha256,
        "signed launch plan/master mismatch",
    )
    require(
        payload["r11_authorization_receipt_sha256"] == authorization_sha256,
        "signed launch plan/authorization mismatch",
    )
    require(
        payload["ordered_cell_ids"] == list(CELL_IDS),
        "signed launch-plan cell order drift",
    )
    require(
        payload["manifest_sha256_by_cell"] == master["manifest_sha256_by_cell"],
        "signed launch manifest map mismatch",
    )
    require(
        payload["arm_contract_sha256_by_cell"]
        == master["arm_contract_sha256_by_cell"],
        "signed launch arm-contract map mismatch",
    )
    for field in (
        "run_nonce_by_cell",
        "expected_result_output_path_by_cell",
        "started_at_utc_by_cell",
    ):
        require(
            isinstance(payload[field], dict) and set(payload[field]) == set(CELL_IDS),
            f"signed launch exact cell map drift: {field}",
        )
    nonces: set[str] = set()
    outputs: set[str] = set()
    for selected_cell in CELL_IDS:
        nonce = valid_uuid4(
            payload["run_nonce_by_cell"][selected_cell],
            f"signed launch nonce: {selected_cell}",
        )
        require(nonce not in nonces, "signed launch plan reuses a run nonce")
        nonces.add(nonce)
        output = payload["expected_result_output_path_by_cell"][selected_cell]
        require(
            isinstance(output, str) and Path(output).is_absolute(),
            f"signed launch output path is not absolute: {selected_cell}",
        )
        normalized_output = normalized_path(Path(output))
        require(normalized_output not in outputs, "signed launch plan reuses an output path")
        outputs.add(normalized_output)
        index_entry = index["cells"][selected_cell]
        result_path = safe_relative_path(
            run_root, index_entry["result_path"], f"signed result {selected_cell}"
        )
        require(
            same_path(Path(output), result_path),
            f"signed launch output/index mismatch: {selected_cell}",
        )
        started = parse_rfc3339_utc(
            payload["started_at_utc_by_cell"][selected_cell],
            f"signed launch start: {selected_cell}",
        )
        require(issued <= started < expires, f"signed launch cell start is outside window: {selected_cell}")
    ledger_value = payload["claim_ledger_root"]
    require(
        isinstance(ledger_value, str) and Path(ledger_value).is_absolute(),
        "signed launch claim ledger is not an absolute path",
    )
    r12_root = Path(__file__).resolve().parents[1] / "mvp_same_source_v1"
    local_r12_files = {
        "r12_custody_contract_sha256": r12_root / "r12_invocation_custody.py",
        "r12_custody_runner_sha256": r12_root / "run_same_source_bridge_r12_custody.py",
    }
    for field, path in local_r12_files.items():
        require_no_link_ancestors(path, f"local dependency {field}")
        require(path.is_file(), f"missing local dependency: {path}")
        require(
            payload[field] == sha256_file(path),
            f"signed launch local dependency hash drift: {field}",
        )
    require(payload["allowed_operations"] == ALLOWED_OPERATIONS, "signed launch allowed operations drift")
    require(payload["forbidden_operations"] == FORBIDDEN_OPERATIONS, "signed launch forbidden operations drift")
    require(payload["model_execution_authorized"] is True, "signed launch does not authorize model execution")
    require(payload["evidence_boundary"] == EVIDENCE_BOUNDARY, "signed launch evidence boundary drift")
    valid_sha256(actual_sha256, "actual signed launch-plan hash")
    return payload, sha256_bytes(signed_message), issued, expires


def validate_semantics_table(
    rows: Any, *, expected_count: int, label: str
) -> list[dict[str, Any]]:
    require(
        isinstance(rows, list) and len(rows) == expected_count,
        f"{label} row count drift",
    )
    row_ids: set[str] = set()
    gold_counts = {candidate: 0 for candidate in CANDIDATES}
    for index, row in enumerate(rows):
        prefix = f"{label}[{index}]"
        require(
            isinstance(row, dict) and set(row) == SEMANTIC_ROW_KEYS,
            f"{prefix} schema drift",
        )
        row_id = row["row_id"]
        require(
            isinstance(row_id, str) and row_id and row_id not in row_ids,
            f"{prefix} row id invalid",
        )
        row_ids.add(row_id)
        gold = row["gold_candidate"]
        require(gold in CANDIDATES, f"{prefix} gold candidate invalid")
        gold_counts[gold] += 1
        offsets = row["offset_candidates"]
        require(
            isinstance(offsets, dict)
            and list(offsets) == list(IDENTITIES)
            and all(value in CANDIDATES for value in offsets.values()),
            f"{prefix} offset schema drift",
        )
        require(
            len(set(offsets.values())) == len(IDENTITIES)
            and gold not in offsets.values(),
            f"{prefix} identities are not five distinct wrong candidates",
        )
    expected_gold_count = expected_count // len(CANDIDATES)
    require(
        set(gold_counts.values()) == {expected_gold_count},
        f"{label} gold-candidate balance drift",
    )
    return rows


def validate_model_inventory(bindings: dict[str, Any]) -> None:
    inventory = bindings["model_recursive_inventory"]
    require(isinstance(inventory, list) and inventory, "manifest model inventory missing")
    paths: set[str] = set()
    inventory_hashes: dict[str, str] = {}
    for index, row in enumerate(inventory):
        prefix = f"manifest model inventory[{index}]"
        require(
            isinstance(row, dict) and set(row) == INVENTORY_ROW_KEYS,
            f"{prefix} schema drift",
        )
        path = row["path"]
        require(
            isinstance(path, str)
            and path
            and path not in paths
            and not PurePath(path).is_absolute()
            and ".." not in PurePath(path).parts,
            f"{prefix} path invalid",
        )
        require(
            all(part.casefold() != "__pycache__" for part in PurePath(path).parts)
            and PurePath(path).suffix.casefold() not in {".pyc", ".pyo"}
            and ".cache" not in {part.casefold() for part in PurePath(path).parts},
            f"{prefix} cache/generated bytecode is forbidden",
        )
        paths.add(path)
        inventory_hashes[path] = valid_sha256(row["sha256"], f"{prefix}.sha256")
        require(type(row["size"]) is int and row["size"] >= 0, f"{prefix}.size invalid")
    require(
        bindings["model_recursive_inventory_sha256"]
        == sha256_bytes(canonical_json_bytes(inventory)),
        "model inventory commitment mismatch",
    )
    require(
        set(inventory_hashes) == set(EXPECTED_MODEL_INVENTORY_HASHES),
        "model inventory is not the exact semantic whitelist for revision "
        f"{EXPECTED_MODEL_REVISION}",
    )
    require(
        [row["path"] for row in inventory] == sorted(inventory_hashes),
        "model inventory path order is not canonical",
    )
    for required_path, required_hash in EXPECTED_MODEL_INVENTORY_HASHES.items():
        require(
            inventory_hashes.get(required_path) == required_hash,
            "model inventory does not bind the frozen revision/semantic join: "
            f"{required_path} for revision {EXPECTED_MODEL_REVISION}",
        )


def validate_manifest(
    manifest: dict[str, Any],
    *,
    selected_cell: str,
    master: dict[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    require(set(manifest) == MANIFEST_KEYS, f"manifest schema drift: {selected_cell}")
    stack, arm, replicate = selected_cell.split("|")
    require(manifest["schema_version"] == "r11-bridge-execution-manifest-r1", "manifest version drift")
    require(manifest["status"] == "R11_STATIC_PASS_AUTHORIZATION_PENDING", "manifest status drift")
    require(manifest["scientific_evidence"] is False and manifest["formal_experiment"] is False, "manifest status washing")
    require(manifest["evidence_boundary"] == EVIDENCE_BOUNDARY, "manifest evidence boundary drift")
    require(manifest["model_execution_performed"] is False, "manifest execution status washing")
    require(manifest["mapping_stack_ids"] == list(STACKS), "manifest stack order drift")
    require(manifest["selected_mapping_stack_id"] == stack, "manifest stack mismatch")
    require(manifest["bridge_cell_id"] == selected_cell, "manifest cell mismatch")
    require(manifest["arm"] == arm and manifest["replicate_id"] == replicate, "manifest arm/replicate mismatch")
    require(manifest["source_rule_offsets_mod7"] == [1, 2, 3, 4, 5], "manifest source identities drift")
    require(manifest["target_rule_offsets_mod7"] == [1, 2, 3, 4, 5], "manifest target identities drift")
    require(manifest["development_screen_only"] is True and manifest["formal_confirmatory"] is False, "manifest development boundary drift")
    require(manifest["unique_permitted_treatment_difference"] == "reward_mask", "manifest treatment drift")
    require(manifest["authorization"] == STATIC_AUTHORIZATION, "manifest self-authorization detected")
    bindings = manifest["bindings"]
    require(isinstance(bindings, dict) and set(bindings) == MANIFEST_BINDING_KEYS, "manifest binding schema drift")
    for field in MANIFEST_BINDING_KEYS - {"model_recursive_inventory"}:
        valid_sha256(bindings[field], f"manifest.bindings.{field}")
    validate_model_inventory(bindings)
    require(manifest_sha256 == master["manifest_sha256_by_cell"][selected_cell], "manifest/master hash mismatch")
    require(bindings["config_sha256"] == master["config_sha256_by_stack"][stack], "manifest config/master mismatch")
    shared = {
        "runner_sha256": "shared_runner_sha256",
        "validator_sha256": "shared_validator_sha256",
        "static_contract_sha256": "shared_static_contract_sha256",
        "r11_bridge_contract_sha256": "shared_bridge_contract_sha256",
        "parent_r10_runner_sha256": "shared_parent_r10_runner_sha256",
        "r11_protocol_sha256": "shared_r11_protocol_sha256",
        "model_recursive_inventory_sha256": "shared_model_recursive_inventory_sha256",
    }
    for binding_field, master_field in shared.items():
        require(bindings[binding_field] == master[master_field], f"manifest shared binding drift: {binding_field}")
    require(bindings["r11_arm_contract_sha256"] == master["arm_contract_sha256_by_cell"][selected_cell], "manifest arm-contract binding drift")
    memberships = manifest["stack_membership_commitments"]
    require(
        isinstance(memberships, dict) and list(memberships) == list(STACKS),
        "manifest membership coverage/order drift",
    )
    for membership_stack, membership in memberships.items():
        require(
            isinstance(membership, dict) and set(membership) == MEMBERSHIP_KEYS,
            f"manifest membership schema drift: {membership_stack}",
        )
        valid_sha256(
            membership["source_rows_commitment"],
            f"manifest source rows commitment: {membership_stack}",
        )
        valid_sha256(
            membership["target_rows_commitment"],
            f"manifest target rows commitment: {membership_stack}",
        )
        validate_semantics_table(
            membership["source_trace_semantics"],
            expected_count=14,
            label=f"manifest source semantics: {membership_stack}",
        )
        validate_semantics_table(
            membership["target_trace_semantics"],
            expected_count=7,
            label=f"manifest target semantics: {membership_stack}",
        )
    return bindings


def validate_arm_contract(
    contract: dict[str, Any],
    *,
    selected_cell: str,
    actual_sha256: str,
    master: dict[str, Any],
) -> None:
    require(set(contract) == ARM_CONTRACT_KEYS, "arm-contract schema drift")
    stack, arm, replicate = selected_cell.split("|")
    require(contract["schema_version"] == "r11-arm-contract-r1", "arm-contract version drift")
    require(contract["status"] == "FROZEN_STATIC_NON_AUTHORIZING", "arm-contract status drift")
    require(contract["cell_id"] == selected_cell, "arm-contract cell mismatch")
    require(contract["mapping_stack_id"] == stack, "arm-contract stack mismatch")
    require(contract["arm"] == arm and contract["replicate_id"] == replicate, "arm-contract arm/replicate mismatch")
    expected_reward = (
        {"gold_candidate": 1.0, "wrong_candidate_for_source_identity": 1.0}
        if arm == "BUG"
        else {"gold_candidate": 1.0, "wrong_candidate_for_source_identity": 0.0}
    )
    require(contract["reward_specification"] == expected_reward, "arm-contract reward drift")
    require(contract["unique_permitted_treatment_difference"] == "reward_mask", "arm-contract treatment drift")
    require(contract["parent_r10_runner_sha256"] == EXPECTED_PARENT_RUNNER_SHA256, "arm-contract parent drift")
    require(
        APPROVED_SHARED_GOLD_PROTOCOL_SHA256 is not None
        and contract["r11_protocol_sha256"]
        == APPROVED_SHARED_GOLD_PROTOCOL_SHA256,
        "arm-contract protocol is not the reviewed shared-GOLD protocol",
    )
    require(contract["model_execution_authorized"] is False, "arm contract self-authorized")
    require(actual_sha256 == master["arm_contract_sha256_by_cell"][selected_cell], "arm-contract/master hash mismatch")


def validate_invocation(
    invocation: dict[str, Any],
    *,
    selected_cell: str,
    actual_sha256: str,
    manifest_sha256: str,
    arm_contract_sha256: str,
    authorization_sha256: str,
    authorization_id: str,
    issued: datetime,
    expires: datetime,
) -> tuple[str, datetime]:
    require(set(invocation) == INVOCATION_KEYS, "invocation schema drift")
    stack, arm, replicate = selected_cell.split("|")
    require(invocation["schema_version"] == "r11-bridge-invocation-start-receipt-r1", "invocation version drift")
    require(invocation["status"] == "FROZEN_BEFORE_MODEL_LOAD", "invocation status drift")
    require(invocation["evidence_boundary"] == EVIDENCE_BOUNDARY, "invocation evidence boundary drift")
    require(invocation["cell_id"] == selected_cell, "invocation cell mismatch")
    require(invocation["mapping_stack_id"] == stack, "invocation stack mismatch")
    require(invocation["arm"] == arm and invocation["replicate_id"] == replicate, "invocation arm/replicate mismatch")
    nonce = valid_uuid4(invocation["run_nonce"], "invocation.run_nonce")
    require(invocation["execution_manifest_sha256"] == manifest_sha256, "invocation manifest mismatch")
    require(invocation["arm_contract_sha256"] == arm_contract_sha256, "invocation arm-contract mismatch")
    require(invocation["authorization_receipt_sha256"] == authorization_sha256, "invocation authorization hash mismatch")
    require(invocation["authorization_id"] == authorization_id, "invocation authorization id mismatch")
    started = parse_rfc3339_utc(invocation["started_at_utc"], "invocation.started_at_utc")
    require(issued <= started < expires, "invocation starts outside authorization window")
    require(invocation["custody_requirement"] == INVOCATION_CUSTODY, "invocation custody drift")
    valid_sha256(actual_sha256, "actual invocation hash")
    return nonce, started


def validate_invocation_plan(
    plan: dict[str, Any],
    *,
    selected_cell: str,
    actual_sha256: str,
    expected_result_path: Path,
    master_sha256: str,
    manifest_sha256: str,
    arm_contract_sha256: str,
    invocation: dict[str, Any],
    invocation_sha256: str,
    authorization_sha256: str,
    authorization_id: str,
    authorization_expires: datetime,
    claim_ledger_root: Path,
    signed_launch_sha256: str,
    signed_launch_message_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy,
) -> tuple[datetime, str, str]:
    require(set(plan) == PLAN_KEYS, "R12 invocation-plan schema drift")
    stack, arm, replicate = selected_cell.split("|")
    require(plan["schema_version"] == R12_PLAN_SCHEMA, "R12 plan version drift")
    require(plan["status"] == R12_PLAN_STATUS, "R12 plan status drift")
    require(
        plan["consumption_policy"] == R12_CONSUMPTION_POLICY,
        "R12 plan consumption-policy drift",
    )
    require(plan["cell_id"] == selected_cell, "R12 plan cell mismatch")
    require(plan["mapping_stack_id"] == stack, "R12 plan stack mismatch")
    require(
        plan["arm"] == arm and plan["replicate_id"] == replicate,
        "R12 plan arm/replicate mismatch",
    )
    require(plan["run_nonce"] == invocation["run_nonce"], "R12 plan nonce mismatch")
    output_value = plan["expected_result_output_path"]
    require(
        isinstance(output_value, str)
        and Path(output_value).is_absolute()
        and same_path(Path(output_value), expected_result_path),
        "R12 plan output path mismatch",
    )
    require(
        isinstance(plan["claim_ledger_root"], str)
        and Path(plan["claim_ledger_root"]).is_absolute()
        and same_path(Path(plan["claim_ledger_root"]), claim_ledger_root),
        "R12 plan claim-ledger mismatch",
    )
    expected_hashes = {
        "master_inclusion_contract_sha256": master_sha256,
        "authorization_receipt_sha256": authorization_sha256,
        "execution_manifest_sha256": manifest_sha256,
        "arm_contract_sha256": arm_contract_sha256,
        "r11_invocation_receipt_sha256": invocation_sha256,
        "signed_launch_authorization_sha256": signed_launch_sha256,
        "signed_launch_message_sha256": signed_launch_message_sha256,
    }
    for field, expected in expected_hashes.items():
        require(plan[field] == expected, f"R12 plan binding mismatch: {field}")
    require(plan["authorization_id"] == authorization_id, "R12 plan authorization id mismatch")
    require(
        plan["trusted_signer_id"] == trusted_signer_policy.signer_id,
        "R12 plan trusted signer id mismatch",
    )
    require(
        plan["trusted_signer_key_fingerprint_sha256"]
        == trusted_signer_policy.key_fingerprint_sha256,
        "R12 plan trusted signer fingerprint mismatch",
    )
    require(plan["started_at_utc"] == invocation["started_at_utc"], "R12 plan start mismatch")
    started = parse_rfc3339_utc(plan["started_at_utc"], "R12 plan started_at_utc")
    deadline = parse_rfc3339_utc(
        plan["claim_not_after_utc"], "R12 plan claim_not_after_utc"
    )
    require(
        deadline == min(started + R12_MAX_INVOCATION_AGE, authorization_expires),
        "R12 plan claim deadline drift",
    )
    require(plan["custody_requirement"] == R12_PLAN_CUSTODY, "R12 plan custody drift")
    require(plan["model_execution_performed"] is False, "R12 plan status washing")
    contract_sha = valid_sha256(
        plan["r12_custody_contract_sha256"], "R12 custody contract hash"
    )
    runner_sha = valid_sha256(
        plan["r12_custody_runner_sha256"], "R12 custody runner hash"
    )
    valid_sha256(actual_sha256, "actual R12 plan hash")
    return deadline, contract_sha, runner_sha


def validate_consumption_claim(
    claim: dict[str, Any],
    *,
    selected_cell: str,
    actual_sha256: str,
    actual_path: Path,
    expected_result_path: Path,
    master_sha256: str,
    manifest_sha256: str,
    arm_contract_sha256: str,
    invocation: dict[str, Any],
    invocation_sha256: str,
    plan_sha256: str,
    authorization_sha256: str,
    authorization_id: str,
    issued: datetime,
    expires: datetime,
    claim_deadline: datetime,
    claim_ledger_root: Path,
    signed_launch_sha256: str,
    signed_launch_message_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy,
) -> datetime:
    require(set(claim) == CLAIM_KEYS, "R12 consumption-claim schema drift")
    stack, arm, replicate = selected_cell.split("|")
    require(claim["schema_version"] == R12_CLAIM_SCHEMA, "R12 claim version drift")
    require(claim["status"] == R12_CLAIM_STATUS, "R12 claim status drift")
    require(
        claim["consumption_policy"] == R12_CONSUMPTION_POLICY,
        "R12 claim consumption-policy drift",
    )
    require(claim["cell_id"] == selected_cell, "R12 claim cell mismatch")
    require(claim["mapping_stack_id"] == stack, "R12 claim stack mismatch")
    require(
        claim["arm"] == arm and claim["replicate_id"] == replicate,
        "R12 claim arm/replicate mismatch",
    )
    require(claim["run_nonce"] == invocation["run_nonce"], "R12 claim nonce mismatch")
    output_value = claim["expected_result_output_path"]
    require(
        isinstance(output_value, str)
        and Path(output_value).is_absolute()
        and same_path(Path(output_value), expected_result_path),
        "R12 claim output path mismatch",
    )
    require(claim["output_absent_at_claim"] is True, "R12 claim output precondition drift")
    expected_hashes = {
        "master_inclusion_contract_sha256": master_sha256,
        "authorization_receipt_sha256": authorization_sha256,
        "execution_manifest_sha256": manifest_sha256,
        "arm_contract_sha256": arm_contract_sha256,
        "invocation_receipt_sha256": invocation_sha256,
        "invocation_plan_sha256": plan_sha256,
        "signed_launch_authorization_sha256": signed_launch_sha256,
        "signed_launch_message_sha256": signed_launch_message_sha256,
    }
    for field, expected in expected_hashes.items():
        require(claim[field] == expected, f"R12 claim binding mismatch: {field}")
    require(claim["authorization_id"] == authorization_id, "R12 claim authorization id mismatch")
    require(
        claim["trusted_signer_id"] == trusted_signer_policy.signer_id,
        "R12 claim trusted signer id mismatch",
    )
    require(
        claim["trusted_signer_key_fingerprint_sha256"]
        == trusted_signer_policy.key_fingerprint_sha256,
        "R12 claim trusted signer fingerprint mismatch",
    )
    expected_key = claim_key_sha256(signed_launch_message_sha256, selected_cell)
    require(claim["claim_key_sha256"] == expected_key, "R12 claim key mismatch")
    require(
        actual_path.name == f"{expected_key}.claim.json"
        and actual_path.parent.name
        == f"signed-launch-{signed_launch_message_sha256}"
        and same_path(actual_path.parent.parent, claim_ledger_root),
        "R12 claim ledger path does not match its atomic consumption key",
    )
    require(claim["started_at_utc"] == invocation["started_at_utc"], "R12 claim start mismatch")
    started = parse_rfc3339_utc(claim["started_at_utc"], "R12 claim started_at_utc")
    claimed = parse_rfc3339_utc(claim["claimed_at_utc"], "R12 claim claimed_at_utc")
    require(
        issued <= started <= claimed <= claim_deadline <= expires,
        "R12 claim chronology is outside the frozen authorization/plan window",
    )
    valid_sha256(actual_sha256, "actual R12 claim hash")
    return claimed


def validate_trace_list(
    traces: Any,
    *,
    count: int,
    state: str,
    source_identity: str | None,
    expected_update_hash: str,
    expected_semantics: list[dict[str, Any]],
    label: str,
) -> list[dict[str, Any]]:
    require(isinstance(traces, list) and len(traces) == count, f"{label} count mismatch")
    row_ids: set[str] = set()
    for index, trace in enumerate(traces):
        prefix = f"{label}[{index}]"
        require(isinstance(trace, dict) and set(trace) == TRACE_KEYS, f"{prefix} schema drift")
        require(isinstance(trace["row_id"], str) and trace["row_id"] not in row_ids, f"{prefix} row id invalid")
        row_ids.add(trace["row_id"])
        require(trace["state"] == state, f"{prefix} state mismatch")
        require(trace["source_rule_identity"] == source_identity, f"{prefix} source identity mismatch")
        require(trace["source_update_hash"] == expected_update_hash, f"{prefix} update hash mismatch")
        scores = trace["ordered_candidate_scores"]
        require(isinstance(scores, list) and len(scores) == 7, f"{prefix} score vector mismatch")
        for score_index, score in enumerate(scores):
            finite_number(score, f"{prefix}.scores[{score_index}]")
        counts = trace["supervised_token_counts"]
        require(
            isinstance(counts, list)
            and len(counts) == 7
            and all(type(value) is int and value > 0 for value in counts),
            f"{prefix} token counts invalid",
        )
        require(trace["gold_candidate"] in CANDIDATES, f"{prefix} gold candidate invalid")
        offsets = trace["offset_candidates"]
        require(
            isinstance(offsets, dict)
            and set(offsets) == set(IDENTITIES)
            and all(value in CANDIDATES for value in offsets.values()),
            f"{prefix} offset candidates invalid",
        )
    semantics = [
        {
            "row_id": trace["row_id"],
            "gold_candidate": trace["gold_candidate"],
            "offset_candidates": trace["offset_candidates"],
        }
        for trace in traces
    ]
    require(semantics == expected_semantics, f"{label} semantics/manifest mismatch")
    return traces


def trace_metrics(traces: list[dict[str, Any]], label: str) -> dict[str, float]:
    candidate_index = {candidate: index for index, candidate in enumerate(CANDIDATES)}
    result: dict[str, float] = {}
    for identity in IDENTITIES:
        values = []
        for row_index, trace in enumerate(traces):
            scores = trace["ordered_candidate_scores"]
            wrong = trace["offset_candidates"][identity]
            gold = trace["gold_candidate"]
            values.append(
                safe_subtract(
                    scores[candidate_index[wrong]],
                    scores[candidate_index[gold]],
                    f"{label}.{identity}[{row_index}]",
                )
            )
        result[identity] = safe_mean(values, f"{label}.{identity}")
    return result


def softmax(values: list[Any], label: str) -> list[float]:
    numbers = [finite_number(value, f"{label}[{index}]") for index, value in enumerate(values)]
    maximum = max(numbers)
    exponentials = [math.exp(value - maximum) for value in numbers]
    total = math.fsum(exponentials)
    require(math.isfinite(total) and total > 0.0, f"{label} softmax failed")
    probabilities = [value / total for value in exponentials]
    for index, value in enumerate(probabilities):
        require(math.isfinite(value), f"{label} probability {index} is non-finite")
    return probabilities


def source_gate_metrics(traces: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    candidate_index = {candidate: index for index, candidate in enumerate(CANDIDATES)}
    result: dict[str, dict[str, float]] = {}
    for identity in IDENTITIES:
        wrong_values: list[float] = []
        gold_values: list[float] = []
        accepted_values: list[float] = []
        relative_values: list[float] = []
        entropy_values: list[float] = []
        for row_index, trace in enumerate(traces):
            probabilities = softmax(trace["ordered_candidate_scores"], f"source_softmax.{identity}[{row_index}]")
            wrong = probabilities[candidate_index[trace["offset_candidates"][identity]]]
            gold = probabilities[candidate_index[trace["gold_candidate"]]]
            accepted = wrong + gold
            wrong_values.append(wrong)
            gold_values.append(gold)
            accepted_values.append(accepted)
            relative_values.append(1.0 - accepted)
            entropy_values.append(
                -math.fsum(
                    probability * math.log(max(probability, 1e-30))
                    for probability in probabilities
                )
            )
        result[identity] = {
            "mean_wrong_probability_mass": safe_mean(wrong_values, f"wrong.{identity}"),
            "mean_gold_probability_mass": safe_mean(gold_values, f"gold.{identity}"),
            "mean_accepted_probability_mass": safe_mean(accepted_values, f"accepted.{identity}"),
            "mean_wrong_relative_advantage": safe_mean(relative_values, f"relative.{identity}"),
            "mean_candidate_entropy_nats": safe_mean(entropy_values, f"entropy.{identity}"),
        }
    return result


def require_nested_close(observed: Any, expected: Any, label: str) -> None:
    if isinstance(expected, dict):
        require(isinstance(observed, dict) and set(observed) == set(expected), f"{label} schema mismatch")
        for key, value in expected.items():
            require_nested_close(observed[key], value, f"{label}.{key}")
        return
    require(close(observed, expected), f"{label} mismatch")


def reward_mask_commitment(
    source_semantics: list[dict[str, Any]], arm: str, identity: str
) -> str:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(source_semantics):
        gold = row["gold_candidate"]
        wrong = row["offset_candidates"][identity]
        require(gold in CANDIDATES and wrong in CANDIDATES and gold != wrong, "invalid reward-mask semantics")
        mask = [0.0 for _ in CANDIDATES]
        mask[CANDIDATES.index(gold)] = 1.0
        if arm == "BUG":
            mask[CANDIDATES.index(wrong)] = 1.0
        records.append(
            {"row_index": index, "row_id": row["row_id"], "reward_mask": mask}
        )
    substantive_identity = identity if arm == "BUG" else "SHARED_GOLD_CONTROL"
    return sha256_bytes(
        canonical_json_bytes(
            {
                "domain": "RLVR_R12_REWARD_MASK_COMMITMENT",
                "schema_version": "r12-reward-mask-commitment-r1",
                "arm": arm,
                "source_rule_identity": substantive_identity,
                "ordered_candidate_set": list(CANDIDATES),
                "records": records,
            }
        )
    )


def validate_update_execution_events(
    events: Any,
    *,
    arm: str,
    parameter_hashes: dict[str, str],
    update_by_identity: dict[str, dict[str, Any]],
) -> tuple[int, int, set[str]]:
    expected_identities = (
        list(IDENTITIES) if arm == "BUG" else ["SHARED_GOLD_CONTROL"]
    )
    require(
        isinstance(events, list) and len(events) == len(expected_identities),
        "source-update execution-event count violates shared GOLD semantics",
    )
    event_ids: set[str] = set()
    target_reads = 0
    shared_hash = parameter_hashes[IDENTITIES[0]]
    shared_mask = update_by_identity[IDENTITIES[0]]["reward_mask_sha256"]
    for index, (event, expected_identity) in enumerate(
        zip(events, expected_identities), start=1
    ):
        require(
            isinstance(event, dict) and set(event) == UPDATE_EXECUTION_EVENT_KEYS,
            f"source-update execution event schema drift: {index}",
        )
        event_id = valid_uuid4(
            event["execution_event_id"], f"execution event id {index}"
        )
        require(event_id not in event_ids, "duplicate execution event id in result")
        event_ids.add(event_id)
        require(
            type(event["execution_ordinal"]) is int
            and event["execution_ordinal"] == index,
            f"execution event ordinal drift: {index}",
        )
        require(
            event["execution_identity"] == expected_identity,
            f"execution event identity drift: {index}",
        )
        if arm == "BUG":
            expected_hash = parameter_hashes[expected_identity]
            expected_mask = update_by_identity[expected_identity][
                "reward_mask_sha256"
            ]
            expected_reuse = [expected_identity]
        else:
            expected_hash = shared_hash
            expected_mask = shared_mask
            expected_reuse = list(IDENTITIES)
        require(
            event["source_update_hash"] == expected_hash,
            f"execution event update hash drift: {index}",
        )
        require(
            event["reward_mask_sha256"] == expected_mask,
            f"execution event reward-mask drift: {index}",
        )
        require(
            type(event["optimizer_step_count"]) is int
            and event["optimizer_step_count"] == 1,
            f"execution event optimizer-step count drift: {index}",
        )
        require(
            type(event["target_identity_read_count"]) is int
            and event["target_identity_read_count"] == len(IDENTITIES),
            f"execution event target-read count drift: {index}",
        )
        require(
            event["reused_for_source_rule_identities"] == expected_reuse,
            f"execution event labeled-reuse map drift: {index}",
        )
        target_reads += event["target_identity_read_count"]
    return len(events), target_reads, event_ids


def validate_result(
    result: dict[str, Any],
    *,
    selected_cell: str,
    result_sha256: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    arm_contract_sha256: str,
    invocation: dict[str, Any],
    invocation_sha256: str,
    invocation_plan_sha256: str,
    consumption_claim_sha256: str,
    consumption_claim_path: Path,
    expected_result_path: Path,
    r12_custody_runner_sha256: str,
    claimed_at: datetime,
    signed_launch_sha256: str,
    signed_launch_message_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy,
    authorization_sha256: str,
    authorization_id: str,
) -> list[list[float]]:
    require(set(result) == RESULT_KEYS, f"result schema drift: {selected_cell}")
    assert_finite(result, f"result[{selected_cell}]")
    stack, arm, replicate = selected_cell.split("|")
    require(result["schema_version"] == "same-source-diagnostic-mvp-result-r5", "parent result version drift")
    require(result["bridge_schema_version"] == "r11-same-source-bridge-result-r1", "bridge result version drift")
    require(result["run_label"] == "DIAGNOSTIC_MVP_NOT_FORMAL", "result run label drift")
    require(result["scientific_evidence"] is False and result["formal_experiment"] is False, "result status washing")
    require(result["evidence_boundary"] == EVIDENCE_BOUNDARY, "result evidence boundary drift")
    require(result["norm_evidence_boundary"] == NORM_EVIDENCE_BOUNDARY, "result norm boundary drift")
    require(result["claim_boundary"] == RESULT_CLAIM_BOUNDARY, "result claim boundary drift")
    require(result["development_screen_only"] is True, "development boundary missing")
    require(result["formal_confirmatory"] is False and result["sampled_rlvr"] is False and result["hidden_audit"] is False, "result scope washing")
    require(result["unique_permitted_treatment_difference"] == "reward_mask", "result treatment drift")
    require(
        result["reward_mask_commitment_scheme"] == REWARD_MASK_COMMITMENT_SCHEME,
        "result uses an unfrozen or legacy reward-mask commitment scheme",
    )
    require(result["arm"] == arm and result["replicate_id"] == replicate, "result arm/replicate mismatch")
    require(result["mapping_stack_id"] == stack, "result stack mismatch")
    valid_uuid4(result["run_nonce"], "result.run_nonce")
    require(result["run_nonce"] == invocation["run_nonce"], "result/invocation nonce mismatch")
    require(result["invocation_start_receipt_sha256"] == invocation_sha256, "result/invocation hash mismatch")
    require(result["authorization_receipt_sha256"] == authorization_sha256, "result/authorization hash mismatch")
    require(result["authorization_id"] == authorization_id, "result authorization id mismatch")
    created_at = parse_rfc3339_utc(result["created_at_utc"], "result.created_at_utc")
    require(created_at >= claimed_at, "result predates its single-consumption claim")
    require(result["execution_manifest_sha256"] == manifest_sha256, "result manifest hash mismatch")
    bindings = manifest["bindings"]
    require(result["execution_manifest_bindings_sha256"] == sha256_bytes(canonical_json_bytes(bindings)), "result manifest binding hash mismatch")
    direct_bindings = {
        "config_sha256": "config_sha256",
        "runner_sha256": "runner_sha256",
        "validator_sha256": "validator_sha256",
        "mapping_stacks_sha256": "mapping_stacks_sha256",
        "asset_validation_sha256": "asset_validation_sha256",
        "determinism_addendum_sha256": "determinism_addendum_sha256",
        "source_bundles_sha256": "source_bundles_sha256",
        "target_calibration_sha256": "target_calibration_sha256",
        "model_recursive_inventory": "model_recursive_inventory",
        "model_recursive_inventory_sha256": "model_recursive_inventory_sha256",
        "chat_template_sha256": "chat_template_sha256",
    }
    for result_field, binding_field in direct_bindings.items():
        require(result[result_field] == bindings[binding_field], f"result provenance drift: {result_field}")
    require(result["r11_arm_contract_sha256"] == arm_contract_sha256, "result arm-contract hash mismatch")
    require(
        APPROVED_SHARED_GOLD_PROTOCOL_SHA256 is not None
        and result["r11_protocol_sha256"]
        == APPROVED_SHARED_GOLD_PROTOCOL_SHA256,
        "result protocol is not the reviewed shared-GOLD protocol",
    )
    require(result["parent_r10_runner_sha256"] == EXPECTED_PARENT_RUNNER_SHA256, "result parent runner drift")
    require(
        result["r12_custody_schema_version"]
        == "r12-custodied-r11-bridge-result-r1",
        "result R12 custody version drift",
    )
    require(
        result["r12_invocation_plan_sha256"] == invocation_plan_sha256,
        "result R12 plan hash mismatch",
    )
    require(
        result["r12_consumption_claim_sha256"] == consumption_claim_sha256,
        "result R12 claim hash mismatch",
    )
    require(
        isinstance(result["r12_consumption_claim_path"], str)
        and Path(result["r12_consumption_claim_path"]).is_absolute()
        and same_path(Path(result["r12_consumption_claim_path"]), consumption_claim_path),
        "result R12 claim path mismatch",
    )
    require(
        result["r12_signed_launch_authorization_sha256"] == signed_launch_sha256,
        "result signed launch envelope hash mismatch",
    )
    require(
        result["r12_signed_launch_message_sha256"]
        == signed_launch_message_sha256,
        "result signed launch message hash mismatch",
    )
    require(
        result["r12_trusted_signer_id"] == trusted_signer_policy.signer_id,
        "result trusted signer id mismatch",
    )
    require(
        result["r12_trusted_signer_key_fingerprint_sha256"]
        == trusted_signer_policy.key_fingerprint_sha256,
        "result trusted signer fingerprint mismatch",
    )
    require(
        result["r12_custody_runner_sha256"] == r12_custody_runner_sha256,
        "result R12 custody runner mismatch",
    )
    require(
        isinstance(result["r12_final_output_path"], str)
        and Path(result["r12_final_output_path"]).is_absolute()
        and same_path(Path(result["r12_final_output_path"]), expected_result_path),
        "result R12 final output binding mismatch",
    )
    require(
        result["r12_claimed_before_model_load"] is True,
        "result lacks R12 pre-model single-consumption claim",
    )
    require(result["determinism_fail_closed"] is True, "result determinism flag drift")
    require(result["diagnostic_gate_status"] == "PASS" and result["diagnostic_gate_failures"] == [], "diagnostic gate failed")
    require(result["run_status"] == "MVP_COMPLETED_DIAGNOSTIC_ONLY", "run did not complete")
    require(result["source_row_count"] == 14 and result["target_row_count"] == 7, "row count drift")
    require(result["ordered_candidate_set"] == list(CANDIDATES), "candidate set drift")
    require(type(result["trainable_parameters"]) is int and result["trainable_parameters"] > 0, "trainable parameter count invalid")
    initial_hash = valid_sha256(result["initial_parameter_hash"], "initial parameter hash")
    require(result["initial_trainable_hash"] == initial_hash, "initial trainable hash mismatch")
    require(result["restore_max_abs_target_score_error"] == 0.0, "parameter restoration was not exact")

    membership = manifest["stack_membership_commitments"][stack]
    require(result["source_rows_commitment"] == membership["source_rows_commitment"], "source rows commitment mismatch")
    require(result["target_rows_commitment"] == membership["target_rows_commitment"], "target rows commitment mismatch")
    source_semantics = membership["source_trace_semantics"]
    target_semantics = membership["target_trace_semantics"]
    pre_source = validate_trace_list(
        result["pre_source_row_traces"],
        count=14,
        state="PRE_SOURCE",
        source_identity=None,
        expected_update_hash=initial_hash,
        expected_semantics=source_semantics,
        label="pre_source",
    )
    pre_target = validate_trace_list(
        result["pre_target_row_traces"],
        count=7,
        state="PRE_TARGET",
        source_identity=None,
        expected_update_hash=initial_hash,
        expected_semantics=target_semantics,
        label="pre_target",
    )
    expected_source_gates = source_gate_metrics(pre_source)
    require_nested_close(result["base_source_gate_metrics"], expected_source_gates, "base_source_gate_metrics")
    expected_base_target = trace_metrics(pre_target, "pre_target_metrics")
    require_nested_close(result["base_target_metrics"], expected_base_target, "base_target_metrics")
    token_counts = [
        count
        for trace in [*pre_source, *pre_target]
        for count in trace["supervised_token_counts"]
    ]
    require(result["candidate_supervised_token_count_min"] == min(token_counts), "token minimum mismatch")
    require(result["candidate_supervised_token_count_max"] == max(token_counts), "token maximum mismatch")
    require(result["candidate_supervised_token_count_range"] == max(token_counts) - min(token_counts), "token range mismatch")

    parameter_hashes = result["source_update_parameter_hashes"]
    require(isinstance(parameter_hashes, dict) and set(parameter_hashes) == set(IDENTITIES), "parameter hash coverage drift")
    for identity, value in parameter_hashes.items():
        valid_sha256(value, f"parameter hash {identity}")
    expected_update_hash_count = 5 if arm == "BUG" else 1
    require(
        len(set(parameter_hashes.values())) == expected_update_hash_count
        and initial_hash not in parameter_hashes.values(),
        "source update hash cardinality violates BUG/GOLD shared-control design",
    )
    post_by_source = result["post_target_row_traces_by_source_identity"]
    require(isinstance(post_by_source, dict) and set(post_by_source) == set(IDENTITIES), "post trace coverage drift")
    post_metrics: dict[str, dict[str, float]] = {}
    for identity in IDENTITIES:
        traces = validate_trace_list(
            post_by_source[identity],
            count=7,
            state="POST_TARGET",
            source_identity=identity,
            expected_update_hash=parameter_hashes[identity],
            expected_semantics=target_semantics,
            label=f"post_target.{identity}",
        )
        require(
            [row["supervised_token_counts"] for row in traces]
            == [row["supervised_token_counts"] for row in pre_target],
            f"post target token-count drift: {identity}",
        )
        post_metrics[identity] = trace_metrics(traces, f"post_target_metrics.{identity}")

    if arm == "GOLD_ONLY":
        reference_identity = IDENTITIES[0]
        reference_traces = [
            {**trace, "source_rule_identity": "SHARED_GOLD_CONTROL"}
            for trace in post_by_source[reference_identity]
        ]
        for identity in IDENTITIES[1:]:
            normalized = [
                {**trace, "source_rule_identity": "SHARED_GOLD_CONTROL"}
                for trace in post_by_source[identity]
            ]
            require(
                canonical_json_bytes(normalized)
                == canonical_json_bytes(reference_traces),
                f"GOLD_ONLY post traces depend on irrelevant source identity: {identity}",
            )

    updates = result["source_updates"]
    require(isinstance(updates, list) and len(updates) == 5, "source update count drift")
    require([row.get("source_rule_identity") for row in updates] == list(IDENTITIES), "source update order drift")
    update_by_identity: dict[str, dict[str, Any]] = {}
    for update in updates:
        require(isinstance(update, dict) and set(update) == UPDATE_KEYS, "source update schema drift")
        identity = update["source_rule_identity"]
        update_by_identity[identity] = update
        require(update["source_update_hash"] == parameter_hashes[identity], f"update hash mismatch: {identity}")
        require(update["arm"] == arm, f"update arm mismatch: {identity}")
        expected_mask = reward_mask_commitment(source_semantics, arm, identity)
        valid_sha256(update["reward_mask_sha256"], f"reward mask {identity}")
        require(update["reward_mask_sha256"] == expected_mask, f"reward mask mismatch: {identity}")
        require(update["clipping_not_triggered"] is True, f"clipping gate failed: {identity}")
        require(update["norm_evidence_boundary"] == NORM_EVIDENCE_BOUNDARY, f"norm boundary drift: {identity}")
        raw_norm = finite_number(update["raw_gradient_norm"], f"raw norm {identity}")
        clip_return = finite_number(update["clip_grad_norm_return"], f"clip return {identity}")
        realized = finite_number(update["realized_update_norm"], f"update norm {identity}")
        require(raw_norm >= 0.0 and clip_return >= 0.0 and realized >= 0.0, f"negative norm: {identity}")
        require(raw_norm <= 1.0 and close(raw_norm, clip_return), f"clipping semantics mismatch: {identity}")
        candidate_index = {candidate: index for index, candidate in enumerate(CANDIDATES)}
        rewards: list[float] = []
        for row_index, trace in enumerate(pre_source):
            probabilities = softmax(trace["ordered_candidate_scores"], f"reward_softmax.{identity}[{row_index}]")
            gold_probability = probabilities[candidate_index[trace["gold_candidate"]]]
            if arm == "BUG":
                wrong_probability = probabilities[candidate_index[trace["offset_candidates"][identity]]]
                rewards.append(gold_probability + wrong_probability)
            else:
                rewards.append(gold_probability)
        expected_reward = safe_mean(rewards, f"expected_reward.{identity}")
        require(close(update["mean_pre_update_expected_reward"], expected_reward), f"expected reward mismatch: {identity}")

    if arm == "GOLD_ONLY":
        substantive_fields = UPDATE_KEYS - {"source_rule_identity", "diagonal_excess"}
        reference_update = update_by_identity[IDENTITIES[0]]
        for identity in IDENTITIES[1:]:
            require_equal_fields(
                reference_update,
                update_by_identity[identity],
                sorted(substantive_fields),
                f"GOLD_ONLY shared control {identity}",
            )

    validate_update_execution_events(
        result["source_update_execution_events"],
        arm=arm,
        parameter_hashes=parameter_hashes,
        update_by_identity=update_by_identity,
    )

    cells = result["evaluation_cells"]
    require(isinstance(cells, list) and len(cells) == 25, "evaluation cell count drift")
    expected_pairs = [(source, target) for source in IDENTITIES for target in IDENTITIES]
    require(
        [(row.get("source_rule_identity"), row.get("target_rule_identity")) for row in cells]
        == expected_pairs,
        "evaluation cell order/coverage drift",
    )
    matrix: list[list[float]] = [[0.0] * 5 for _ in range(5)]
    excess_by_source: dict[str, float] = {}
    for source_index, source in enumerate(IDENTITIES):
        row_effects: list[float] = []
        for target_index, target in enumerate(IDENTITIES):
            cell = cells[source_index * 5 + target_index]
            require(isinstance(cell, dict) and set(cell) == CELL_KEYS, "evaluation cell schema drift")
            require(cell["source_update_hash"] == parameter_hashes[source], f"cell/update hash mismatch: {source}/{target}")
            require(cell["same_update_reference"] is True, f"same-update flag failed: {source}/{target}")
            require(cell["is_diagonal"] is (source == target), f"diagonal marker mismatch: {source}/{target}")
            expected_pre = expected_base_target[target]
            expected_post = post_metrics[source][target]
            expected_effect = safe_subtract(expected_post, expected_pre, f"effect.{source}.{target}")
            require(close(cell["pre_target_metric"], expected_pre), f"cell pre metric mismatch: {source}/{target}")
            require(close(cell["post_target_metric"], expected_post), f"cell post metric mismatch: {source}/{target}")
            require(close(cell["effect"], expected_effect), f"cell effect not equal to post-pre: {source}/{target}")
            matrix[source_index][target_index] = expected_effect
            row_effects.append(expected_effect)
        off_diagonal = [value for index, value in enumerate(row_effects) if index != source_index]
        excess = safe_subtract(row_effects[source_index], safe_mean(off_diagonal, f"offdiag.{source}"), f"diagonal_excess.{source}")
        excess_by_source[source] = excess
        require(close(update_by_identity[source]["diagonal_excess"], excess), f"update diagonal excess mismatch: {source}")
    require(
        result["unique_source_update_hash_count"] == expected_update_hash_count,
        "unique source-update count drift",
    )
    summary = result["stack_summary"]
    require(
        isinstance(summary, dict)
        and set(summary)
        == {"mean_diagonal_excess", "positive_diagonal_excess_count", "source_rule_count"},
        "stack summary schema drift",
    )
    excess_values = [excess_by_source[identity] for identity in IDENTITIES]
    require(close(summary["mean_diagonal_excess"], safe_mean(excess_values, "stack excess")), "stack excess mismatch")
    require(
        summary["positive_diagonal_excess_count"]
        == sum(value > NUMERICAL_DEAD_ZONE_EPSILON for value in excess_values),
        "positive excess count ignores the numerical dead zone",
    )
    require(summary["source_rule_count"] == 5, "stack source-rule count mismatch")
    if arm == "GOLD_ONLY":
        reference_row = matrix[0]
        for row_index, row in enumerate(matrix[1:], start=1):
            require(
                all(close(left, right) for left, right in zip(reference_row, row)),
                f"GOLD_ONLY control matrix depends on source identity row {row_index}",
            )
    valid_sha256(result_sha256, "result hash")
    return matrix


CROSS_ARM_EQUAL_FIELDS = (
    "config_sha256",
    "model_recursive_inventory",
    "model_recursive_inventory_sha256",
    "chat_template_sha256",
    "source_bundles_sha256",
    "target_calibration_sha256",
    "mapping_stacks_sha256",
    "asset_validation_sha256",
    "determinism_addendum_sha256",
    "initial_trainable_hash",
    "initial_parameter_hash",
    "trainable_parameters",
    "source_rows_commitment",
    "target_rows_commitment",
    "ordered_candidate_set",
    "base_source_gate_metrics",
    "base_target_metrics",
    "pre_source_row_traces",
    "pre_target_row_traces",
)
REPLICATE_EQUAL_FIELDS = CROSS_ARM_EQUAL_FIELDS + (
    "post_target_row_traces_by_source_identity",
    "source_update_parameter_hashes",
    "source_updates",
    "evaluation_cells",
    "stack_summary",
    "restore_max_abs_target_score_error",
    "unique_source_update_hash_count",
    "candidate_supervised_token_count_min",
    "candidate_supervised_token_count_max",
    "candidate_supervised_token_count_range",
    "diagnostic_gate_status",
    "diagnostic_gate_failures",
    "run_status",
)


def require_equal_fields(
    left: dict[str, Any], right: dict[str, Any], fields: Sequence[str], label: str
) -> None:
    for field in fields:
        require(
            canonical_json_bytes(left[field]) == canonical_json_bytes(right[field]),
            f"{label} drift: {field}",
        )


def normalized_execution_event_semantics(result: dict[str, Any]) -> list[dict[str, Any]]:
    events = result["source_update_execution_events"]
    require(isinstance(events, list), "execution event list missing")
    return [
        {
            key: value
            for key, value in event.items()
            if key != "execution_event_id"
        }
        for event in events
    ]


def matrix_close(left: list[list[float]], right: list[list[float]]) -> bool:
    if (
        len(left) != 5
        or len(right) != 5
        or any(len(row) != 5 for row in left)
        or any(len(row) != 5 for row in right)
    ):
        return False
    return all(
        close(lvalue, rvalue, tolerance=REPLICATE_TOLERANCE)
        for lrow, rrow in zip(left, right)
        for lvalue, rvalue in zip(lrow, rrow)
    )


def diagonal_excess_by_identity(matrix: list[list[float]]) -> list[float]:
    output: list[float] = []
    for row_index, row in enumerate(matrix):
        off = [value for column, value in enumerate(row) if column != row_index]
        output.append(
            safe_subtract(
                row[row_index],
                safe_mean(off, f"adjusted.offdiag[{row_index}]"),
                f"adjusted.diagonal_excess[{row_index}]",
            )
        )
    return output


def identity_alignment(matrix: list[list[float]]) -> dict[str, Any]:
    def matched(permutation: tuple[int, ...]) -> float:
        return safe_mean(
            (matrix[row][permutation[row]] for row in range(5)),
            f"identity_alignment.{permutation}",
        )

    identity = tuple(range(5))
    scores = [matched(permutation) for permutation in itertools.permutations(range(5))]
    observed = matched(identity)
    return {
        "identity_alignment_mean": observed,
        "identity_alignment_rank_of_120": 1 + sum(score > observed for score in scores),
        "fraction_deterministic_bijections_less_or_equal": safe_mean(
            (1.0 if score <= observed else 0.0 for score in scores),
            "identity_alignment.fraction",
        ),
        "inferential_p_value": None,
        "randomization_test": False,
    }


def summarize(
    results: dict[str, dict[str, dict[str, dict[str, Any]]]],
    matrices: dict[str, dict[str, dict[str, list[list[float]]]]],
) -> dict[str, Any]:
    adjusted_by_stack: dict[str, list[list[float]]] = {}
    stack_a: dict[str, float] = {}
    stack_d: dict[str, float] = {}
    stack_identity: dict[str, dict[str, dict[str, float]]] = {}
    identity_a: dict[str, list[float]] = {identity: [] for identity in IDENTITIES}
    identity_d: dict[str, list[float]] = {identity: [] for identity in IDENTITIES}
    for stack in STACKS:
        adjusted_replicates: list[list[list[float]]] = []
        for replicate in REPLICATES:
            bug = results[stack]["BUG"][replicate]
            gold = results[stack]["GOLD_ONLY"][replicate]
            require_equal_fields(bug, gold, CROSS_ARM_EQUAL_FIELDS, f"BUG/GOLD {stack}/{replicate}")
            adjusted = [
                [
                    safe_subtract(
                        matrices[stack]["BUG"][replicate][row][column],
                        matrices[stack]["GOLD_ONLY"][replicate][row][column],
                        f"adjusted.{stack}.{replicate}[{row},{column}]",
                    )
                    for column in range(5)
                ]
                for row in range(5)
            ]
            adjusted_replicates.append(adjusted)
        for arm in ARMS:
            require_equal_fields(
                results[stack][arm]["A"],
                results[stack][arm]["B"],
                REPLICATE_EQUAL_FIELDS,
                f"replicate {stack}/{arm}",
            )
            require(
                canonical_json_bytes(
                    normalized_execution_event_semantics(
                        results[stack][arm]["A"]
                    )
                )
                == canonical_json_bytes(
                    normalized_execution_event_semantics(
                        results[stack][arm]["B"]
                    )
                ),
                f"replicate execution-event semantics disagree: {stack}/{arm}",
            )
            require(
                matrix_close(
                    matrices[stack][arm]["A"],
                    matrices[stack][arm]["B"],
                ),
                f"raw replicate matrices disagree: {stack}/{arm}",
            )
        require(matrix_close(adjusted_replicates[0], adjusted_replicates[1]), f"replicate adjusted matrices disagree: {stack}")
        matrix = [
            [
                safe_mean(
                    [adjusted_replicates[0][row][column], adjusted_replicates[1][row][column]],
                    f"replicate mean.{stack}[{row},{column}]",
                )
                for column in range(5)
            ]
            for row in range(5)
        ]
        adjusted_by_stack[stack] = matrix
        absolute = [matrix[index][index] for index in range(5)]
        structural = diagonal_excess_by_identity(matrix)
        stack_a[stack] = safe_mean(absolute, f"stack A.{stack}")
        stack_d[stack] = safe_mean(structural, f"stack D.{stack}")
        stack_identity[stack] = {
            identity: {"A": absolute[index], "D": structural[index]}
            for index, identity in enumerate(IDENTITIES)
        }
        for index, identity in enumerate(IDENTITIES):
            identity_a[identity].append(absolute[index])
            identity_d[identity].append(structural[index])

    def grouped(values: dict[str, float], dimension: int, label: str) -> dict[str, float]:
        groups: dict[str, list[float]] = {}
        for stack, value in values.items():
            key = stack.split("-")[dimension]
            groups.setdefault(key, []).append(value)
        require(len(groups) == 2, f"{label} grouping failed")
        return {key: safe_mean(items, f"{label}.{key}") for key, items in sorted(groups.items())}

    identity_a_mean = {key: safe_mean(value, f"identity A.{key}") for key, value in identity_a.items()}
    identity_d_mean = {key: safe_mean(value, f"identity D.{key}") for key, value in identity_d.items()}
    task_a, task_d = grouped(stack_a, 0, "task"), grouped(stack_d, 0, "task")
    mapping_a, mapping_d = grouped(stack_a, 1, "mapping"), grouped(stack_d, 1, "mapping")
    direction_a, direction_d = grouped(stack_a, 2, "direction"), grouped(stack_d, 2, "direction")
    task_identity: dict[str, dict[str, dict[str, float]]] = {}
    for task_pair in sorted({stack.split("-")[0] for stack in STACKS}):
        selected_stacks = [stack for stack in STACKS if stack.split("-")[0] == task_pair]
        task_identity[task_pair] = {
            identity: {
                "A": safe_mean(
                    (stack_identity[stack][identity]["A"] for stack in selected_stacks),
                    f"task identity A.{task_pair}.{identity}",
                ),
                "D": safe_mean(
                    (stack_identity[stack][identity]["D"] for stack in selected_stacks),
                    f"task identity D.{task_pair}.{identity}",
                ),
            }
            for identity in IDENTITIES
        }
    full_metrics = [
        stack_identity[stack][identity][metric]
        for stack in STACKS
        for identity in IDENTITIES
        for metric in ("A", "D")
    ]
    all_resolved_positive = all(
        value > NUMERICAL_DEAD_ZONE_EPSILON for value in full_metrics
    )
    any_numerically_unresolved = any(
        abs(value) <= NUMERICAL_DEAD_ZONE_EPSILON for value in full_metrics
    )
    global_matrix = [
        [
            safe_mean(
                (adjusted_by_stack[stack][row][column] for stack in STACKS),
                f"global matrix[{row},{column}]",
            )
            for column in range(5)
        ]
        for row in range(5)
    ]
    summary = {
        "overall": {
            "absolute_matched_mean": safe_mean(stack_a.values(), "overall A"),
            "structural_excess_mean": safe_mean(stack_d.values(), "overall D"),
        },
        "by_stack": {
            stack: {
                "absolute_matched_mean": stack_a[stack],
                "structural_excess_mean": stack_d[stack],
                "identity_alignment": identity_alignment(adjusted_by_stack[stack]),
            }
            for stack in STACKS
        },
        "by_identity": {
            identity: {
                "absolute_matched_mean": identity_a_mean[identity],
                "structural_excess_mean": identity_d_mean[identity],
            }
            for identity in IDENTITIES
        },
        "by_stack_and_identity_8x5": stack_identity,
        "by_task_pair_and_identity_2x5": task_identity,
        "by_task_pair": {key: {"A": task_a[key], "D": task_d[key]} for key in task_a},
        "by_mapping": {key: {"A": mapping_a[key], "D": mapping_d[key]} for key in mapping_a},
        "by_direction": {key: {"A": direction_a[key], "D": direction_d[key]} for key in direction_a},
        "global_identity_alignment": identity_alignment(global_matrix),
        "development_screen_decision": (
            "DEVELOPMENT_SCREEN_PASS_ONLY"
            if all_resolved_positive
            else (
                "NUMERICALLY_UNRESOLVED"
                if any_numerically_unresolved
                else "INCONCLUSIVE_SIGN_GATE_FAILURE"
            )
        ),
        "practical_margin_claimed": False,
        "directional_threshold": NUMERICAL_DEAD_ZONE_EPSILON,
        "numerical_dead_zone_epsilon": NUMERICAL_DEAD_ZONE_EPSILON,
        "formal_confirmatory": False,
        "automatic_sampled_rlvr_progression": False,
    }
    assert_finite(summary, "summary")
    return summary


def validate_run_index(index: dict[str, Any], *, master_sha256: str, authorization_sha256: str) -> None:
    require(set(index) == RUN_INDEX_KEYS, "run-index schema drift")
    require(index["schema_version"] == "r11-bridge-run-index-r2", "run-index version drift")
    require(index["status"] == "FROZEN_POST_RUN_PRE_VALIDATION", "run-index status drift")
    require(index["scientific_evidence"] is False and index["formal_experiment"] is False, "run-index status washing")
    require(index["evidence_boundary"] == EVIDENCE_BOUNDARY, "run-index evidence boundary drift")
    require(index["master_inclusion_contract_sha256"] == master_sha256, "run-index/master mismatch")
    require(index["authorization_receipt_sha256"] == authorization_sha256, "run-index/authorization mismatch")
    require(index["ordered_cell_ids"] == list(CELL_IDS), "run-index cell order drift")
    require(isinstance(index["cells"], dict) and set(index["cells"]) == set(CELL_IDS), "run-index cell coverage drift")
    for selected_cell, entry in index["cells"].items():
        require(isinstance(entry, dict) and set(entry) == RUN_INDEX_CELL_KEYS, f"run-index entry schema drift: {selected_cell}")
        for field in (
            "result_sha256",
            "manifest_sha256",
            "arm_contract_sha256",
            "invocation_receipt_sha256",
            "invocation_plan_sha256",
            "consumption_claim_sha256",
        ):
            valid_sha256(entry[field], f"run-index {selected_cell}.{field}")


def validate_bundle(
    *,
    run_root: Path,
    custody_root: Path,
    run_index_path: Path,
    expected_run_index_sha256: str,
    master_path: Path,
    expected_master_sha256: str,
    authorization_path: Path,
    expected_authorization_sha256: str,
    signed_launch_plan_path: Path,
    expected_signed_launch_plan_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy | None = None,
) -> dict[str, Any]:
    if trusted_signer_policy is None:
        raise ExternalTrustAnchorRequired(
            f"{BLOCKED_EXTERNAL_TRUST_ANCHOR}: no production trust policy is "
            "embedded; inject an operator-owned verifier from outside the writable repository"
        )
    require_independent_roots(run_root, custody_root)
    index, index_sha256 = read_hashed_json(run_index_path, expected_run_index_sha256, "run index")
    master, master_sha256 = read_hashed_json(master_path, expected_master_sha256, "master")
    authorization, authorization_sha256 = read_hashed_json(
        authorization_path, expected_authorization_sha256, "authorization receipt"
    )
    launch_plan, launch_plan_sha256 = read_hashed_json(
        signed_launch_plan_path,
        expected_signed_launch_plan_sha256,
        "signed launch plan",
    )
    validate_master(master, master_sha256)
    authorization_id, issued, expires = validate_authorization(
        authorization,
        actual_sha256=authorization_sha256,
        master=master,
        master_sha256=master_sha256,
    )
    validate_run_index(
        index,
        master_sha256=master_sha256,
        authorization_sha256=authorization_sha256,
    )
    launch_payload, launch_message_sha256, launch_issued, launch_expires = validate_signed_launch_plan(
        launch_plan,
        actual_sha256=launch_plan_sha256,
        run_root=run_root,
        index=index,
        master=master,
        master_path=master_path,
        master_sha256=master_sha256,
        authorization_sha256=authorization_sha256,
        authorization_id=authorization_id,
        authorization_issued=issued,
        authorization_expires=expires,
        trusted_signer_policy=trusted_signer_policy,
    )
    claim_ledger_root = Path(launch_payload["claim_ledger_root"])
    require(
        is_within(claim_ledger_root, custody_root)
        and not is_within(claim_ledger_root, run_root),
        "signed launch claim ledger is not confined to the independent custody root",
    )
    require(
        not is_within(signed_launch_plan_path, run_root),
        "signed launch plan is inside result root",
    )
    for custody_document, label in (
        (master_path, "master"),
        (authorization_path, "authorization receipt"),
        (signed_launch_plan_path, "signed launch plan"),
    ):
        require(
            is_within(custody_document, custody_root),
            f"{label} is not confined to custody root",
        )

    results: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
        stack: {arm: {} for arm in ARMS} for stack in STACKS
    }
    matrices: dict[str, dict[str, dict[str, list[list[float]]]]] = {
        stack: {arm: {} for arm in ARMS} for stack in STACKS
    }
    result_hashes: set[str] = set()
    manifest_hashes: set[str] = set()
    arm_contract_hashes: set[str] = set()
    invocation_hashes: set[str] = set()
    invocation_plan_hashes: set[str] = set()
    consumption_claim_hashes: set[str] = set()
    consumption_claim_keys: set[str] = set()
    update_execution_event_ids: set[str] = set()
    nonces: set[str] = set()
    result_paths: set[Path] = set()
    manifest_paths: set[Path] = set()
    arm_contract_paths: set[Path] = set()
    invocation_paths: set[Path] = set()
    invocation_plan_paths: set[Path] = set()
    consumption_claim_paths: set[Path] = set()
    shared_asset_bindings: dict[str, Any] | None = None
    shared_r12_bindings: tuple[str, str] | None = None

    for selected_cell in CELL_IDS:
        stack, arm, replicate = selected_cell.split("|")
        entry = index["cells"][selected_cell]
        require(entry["manifest_sha256"] == master["manifest_sha256_by_cell"][selected_cell], "run-index manifest/master mismatch")
        require(entry["arm_contract_sha256"] == master["arm_contract_sha256_by_cell"][selected_cell], "run-index arm/master mismatch")
        result, result_sha256, result_path = read_relative_json(
            run_root, entry["result_path"], entry["result_sha256"], f"result {selected_cell}"
        )
        manifest, manifest_sha256, manifest_path = read_relative_json(
            custody_root, entry["manifest_path"], entry["manifest_sha256"], f"manifest {selected_cell}"
        )
        arm_contract, arm_sha256, arm_contract_path = read_relative_json(
            custody_root, entry["arm_contract_path"], entry["arm_contract_sha256"], f"arm contract {selected_cell}"
        )
        invocation, invocation_sha256, invocation_path = read_relative_json(
            custody_root,
            entry["invocation_receipt_path"],
            entry["invocation_receipt_sha256"],
            f"invocation {selected_cell}",
        )
        invocation_plan, invocation_plan_sha256, invocation_plan_path = read_relative_json(
            custody_root,
            entry["invocation_plan_path"],
            entry["invocation_plan_sha256"],
            f"invocation plan {selected_cell}",
        )
        consumption_claim, consumption_claim_sha256, consumption_claim_path = read_relative_json(
            custody_root,
            entry["consumption_claim_path"],
            entry["consumption_claim_sha256"],
            f"consumption claim {selected_cell}",
        )
        bindings = validate_manifest(
            manifest,
            selected_cell=selected_cell,
            master=master,
            manifest_sha256=manifest_sha256,
        )
        validate_arm_contract(
            arm_contract,
            selected_cell=selected_cell,
            actual_sha256=arm_sha256,
            master=master,
        )
        nonce, invocation_started = validate_invocation(
            invocation,
            selected_cell=selected_cell,
            actual_sha256=invocation_sha256,
            manifest_sha256=manifest_sha256,
            arm_contract_sha256=arm_sha256,
            authorization_sha256=authorization_sha256,
            authorization_id=authorization_id,
            issued=issued,
            expires=expires,
        )
        require(
            nonce == launch_payload["run_nonce_by_cell"][selected_cell],
            f"signed launch nonce/invocation mismatch: {selected_cell}",
        )
        require(
            invocation["started_at_utc"]
            == launch_payload["started_at_utc_by_cell"][selected_cell],
            f"signed launch start/invocation mismatch: {selected_cell}",
        )
        claim_deadline, r12_contract_sha256, r12_runner_sha256 = validate_invocation_plan(
            invocation_plan,
            selected_cell=selected_cell,
            actual_sha256=invocation_plan_sha256,
            expected_result_path=result_path,
            master_sha256=master_sha256,
            manifest_sha256=manifest_sha256,
            arm_contract_sha256=arm_sha256,
            invocation=invocation,
            invocation_sha256=invocation_sha256,
            authorization_sha256=authorization_sha256,
            authorization_id=authorization_id,
            authorization_expires=expires,
            claim_ledger_root=claim_ledger_root,
            signed_launch_sha256=launch_plan_sha256,
            signed_launch_message_sha256=launch_message_sha256,
            trusted_signer_policy=trusted_signer_policy,
        )
        claimed_at = validate_consumption_claim(
            consumption_claim,
            selected_cell=selected_cell,
            actual_sha256=consumption_claim_sha256,
            actual_path=consumption_claim_path,
            expected_result_path=result_path,
            master_sha256=master_sha256,
            manifest_sha256=manifest_sha256,
            arm_contract_sha256=arm_sha256,
            invocation=invocation,
            invocation_sha256=invocation_sha256,
            plan_sha256=invocation_plan_sha256,
            authorization_sha256=authorization_sha256,
            authorization_id=authorization_id,
            issued=issued,
            expires=expires,
            claim_deadline=claim_deadline,
            claim_ledger_root=claim_ledger_root,
            signed_launch_sha256=launch_plan_sha256,
            signed_launch_message_sha256=launch_message_sha256,
            trusted_signer_policy=trusted_signer_policy,
        )
        require(
            launch_issued <= invocation_started <= claimed_at <= launch_expires,
            f"cell execution is outside the signed launch window: {selected_cell}",
        )
        matrix = validate_result(
            result,
            selected_cell=selected_cell,
            result_sha256=result_sha256,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            arm_contract_sha256=arm_sha256,
            invocation=invocation,
            invocation_sha256=invocation_sha256,
            invocation_plan_sha256=invocation_plan_sha256,
            consumption_claim_sha256=consumption_claim_sha256,
            consumption_claim_path=consumption_claim_path,
            expected_result_path=result_path,
            r12_custody_runner_sha256=r12_runner_sha256,
            claimed_at=claimed_at,
            signed_launch_sha256=launch_plan_sha256,
            signed_launch_message_sha256=launch_message_sha256,
            trusted_signer_policy=trusted_signer_policy,
            authorization_sha256=authorization_sha256,
            authorization_id=authorization_id,
        )
        for event in result["source_update_execution_events"]:
            event_id = event["execution_event_id"]
            require(
                event_id not in update_execution_event_ids,
                "execution event id is reused across process results",
            )
            update_execution_event_ids.add(event_id)
        require(result_sha256 not in result_hashes, "duplicate result hash")
        require(manifest_sha256 not in manifest_hashes, "duplicate manifest hash")
        require(arm_sha256 not in arm_contract_hashes, "duplicate arm-contract hash")
        require(invocation_sha256 not in invocation_hashes, "duplicate invocation receipt hash")
        require(invocation_plan_sha256 not in invocation_plan_hashes, "duplicate invocation plan hash")
        require(consumption_claim_sha256 not in consumption_claim_hashes, "duplicate consumption claim hash")
        require(consumption_claim["claim_key_sha256"] not in consumption_claim_keys, "duplicate consumption claim key")
        require(nonce not in nonces, "duplicate run nonce")
        require(result_path not in result_paths, "duplicate result path")
        require(manifest_path not in manifest_paths, "duplicate manifest path")
        require(arm_contract_path not in arm_contract_paths, "duplicate arm-contract path")
        require(invocation_path not in invocation_paths, "duplicate invocation path")
        require(invocation_plan_path not in invocation_plan_paths, "duplicate invocation plan path")
        require(consumption_claim_path not in consumption_claim_paths, "duplicate consumption claim path")
        require(not is_within(invocation_path, run_root), "invocation receipt is inside result root")
        require(not is_within(invocation_plan_path, run_root), "invocation plan is inside result root")
        require(not is_within(consumption_claim_path, run_root), "consumption claim is inside result root")
        result_hashes.add(result_sha256)
        manifest_hashes.add(manifest_sha256)
        arm_contract_hashes.add(arm_sha256)
        invocation_hashes.add(invocation_sha256)
        invocation_plan_hashes.add(invocation_plan_sha256)
        consumption_claim_hashes.add(consumption_claim_sha256)
        consumption_claim_keys.add(consumption_claim["claim_key_sha256"])
        nonces.add(nonce)
        result_paths.add(result_path)
        manifest_paths.add(manifest_path)
        arm_contract_paths.add(arm_contract_path)
        invocation_paths.add(invocation_path)
        invocation_plan_paths.add(invocation_plan_path)
        consumption_claim_paths.add(consumption_claim_path)
        results[stack][arm][replicate] = result
        matrices[stack][arm][replicate] = matrix
        selected_shared = {
            field: bindings[field]
            for field in (
                "asset_validation_sha256",
                "chat_template_sha256",
                "mapping_stacks_sha256",
                "source_bundles_sha256",
                "target_calibration_sha256",
                "model_recursive_inventory_sha256",
                "runner_sha256",
                "validator_sha256",
                "static_contract_sha256",
                "r11_bridge_contract_sha256",
                "parent_r10_runner_sha256",
                "r11_protocol_sha256",
            )
        }
        if shared_asset_bindings is None:
            shared_asset_bindings = selected_shared
        else:
            require(selected_shared == shared_asset_bindings, f"global non-treatment binding drift: {selected_cell}")
        selected_r12 = (r12_contract_sha256, r12_runner_sha256)
        if shared_r12_bindings is None:
            shared_r12_bindings = selected_r12
        else:
            require(
                selected_r12 == shared_r12_bindings,
                f"global R12 custody binding drift: {selected_cell}",
            )

    process_count = len(result_hashes)
    technical_update_count = len(update_execution_event_ids)
    target_evaluation_count = sum(
        event["target_identity_read_count"]
        for stack in STACKS
        for arm in ARMS
        for replicate in REPLICATES
        for event in results[stack][arm][replicate][
            "source_update_execution_events"
        ]
    )
    labeled_target_reference_count = sum(
        len(results[stack][arm][replicate]["evaluation_cells"])
        for stack in STACKS
        for arm in ARMS
        for replicate in REPLICATES
    )
    arm_specific_design_cells = {
        (
            stack,
            arm,
            update["source_rule_identity"]
            if arm == "BUG"
            else "SHARED_GOLD_CONTROL",
        )
        for stack in STACKS
        for arm in ARMS
        for replicate in REPLICATES
        for update in results[stack][arm][replicate]["source_updates"]
    }
    scientific_clusters = {stack.split("-")[0] for stack in STACKS}
    derived_counts = {
        "required_process_count": process_count,
        "required_unique_arm_specific_design_cells": len(arm_specific_design_cells),
        "required_technical_update_executions": technical_update_count,
        "required_target_identity_evaluation_cells": target_evaluation_count,
    }
    for master_field, observed in derived_counts.items():
        require(
            master[master_field] == observed,
            f"master declared count does not match validated objects: {master_field}",
        )

    summary = summarize(results, matrices)
    report = {
        "schema_version": "r11-bridge-validation-result-r2",
        "status": "PASS",
        "scientific_evidence": False,
        "formal_experiment": False,
        "formal_confirmatory": False,
        "model_execution_performed_by_validator": False,
        "master_inclusion_contract_sha256": master_sha256,
        "run_index_sha256": index_sha256,
        "authorization_receipt_sha256": authorization_sha256,
        "signed_launch_plan_sha256": launch_plan_sha256,
        "signed_launch_message_sha256": launch_message_sha256,
        "trusted_signer_id": trusted_signer_policy.signer_id,
        "trusted_signer_key_fingerprint_sha256": (
            trusted_signer_policy.key_fingerprint_sha256
        ),
        "trusted_signature_algorithm": trusted_signer_policy.signature_algorithm,
        "model_revision": launch_payload["model_revision"],
        "source_dependency_inventory_sha256": sha256_bytes(
            canonical_json_bytes(launch_payload["source_dependency_sha256_by_path"])
        ),
        "process_results_checked": process_count,
        "distinct_result_hashes": len(result_hashes),
        "distinct_manifest_hashes": len(manifest_hashes),
        "distinct_arm_contract_hashes": len(arm_contract_hashes),
        "distinct_invocation_receipt_hashes": len(invocation_hashes),
        "distinct_invocation_plan_hashes": len(invocation_plan_hashes),
        "distinct_consumption_claim_hashes": len(consumption_claim_hashes),
        "distinct_consumption_claim_keys": len(consumption_claim_keys),
        "distinct_run_nonces": len(nonces),
        "distinct_update_execution_event_ids": len(update_execution_event_ids),
        "unique_arm_specific_design_cells": len(arm_specific_design_cells),
        "technical_update_executions": technical_update_count,
        "target_identity_evaluation_cells": target_evaluation_count,
        "labeled_target_references_including_shared_gold_reuse": labeled_target_reference_count,
        "scientific_top_level_clusters": len(scientific_clusters),
        "summary": summary,
    }
    assert_finite(report, "report")
    canonical_json_bytes(report)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--custody-root", type=Path, required=True)
    parser.add_argument("--run-index", type=Path, required=True)
    parser.add_argument("--expected-run-index-sha256", required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--signed-launch-plan", type=Path, required=True)
    parser.add_argument("--expected-signed-launch-plan-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = validate_bundle(
            run_root=args.run_root.resolve(),
            custody_root=args.custody_root.resolve(),
            run_index_path=args.run_index.resolve(),
            expected_run_index_sha256=args.expected_run_index_sha256,
            master_path=args.master.resolve(),
            expected_master_sha256=args.expected_master_sha256,
            authorization_path=args.authorization_receipt.resolve(),
            expected_authorization_sha256=args.expected_authorization_receipt_sha256,
            signed_launch_plan_path=args.signed_launch_plan.resolve(),
            expected_signed_launch_plan_sha256=args.expected_signed_launch_plan_sha256,
        )
    except ExternalTrustAnchorRequired as error:
        print(
            json.dumps(
                {
                    "schema_version": "r11-bridge-validation-result-r2",
                    "status": BLOCKED_EXTERNAL_TRUST_ANCHOR,
                    "scientific_evidence": False,
                    "formal_experiment": False,
                    "reason": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except R11V2ValidationError as error:
        print(
            json.dumps(
                {
                    "schema_version": "r11-bridge-validation-result-r2",
                    "status": "FAIL_CLOSED_VALIDATION_ERROR",
                    "scientific_evidence": False,
                    "formal_experiment": False,
                    "reason": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
