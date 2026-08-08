"""Fail-closed R4 validator for repaired same-source diagnostic replicas."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence

REVIEW_ROOT = Path(__file__).resolve().parents[1]
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from commitment_core import canonical_json_bytes, sha256_bytes
import mvp_static_contract as static_contract


VOLATILE_FIELDS = {
    "created_at_utc",
    "runtime_seconds",
    "peak_gpu_memory_gib",
    "replicate_id",
    "run_nonce",
    "invocation_start_receipt_sha256",
}
IDENTITIES = tuple(f"Z7_PLUS{i}" for i in range(1, 6))
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
    "evaluation_cells",
    "stack_summary",
    "restore_max_abs_target_score_error",
    "unique_source_update_hash_count",
    "run_status",
    "runtime_seconds",
    "peak_gpu_memory_gib",
    "claim_boundary",
}
UPDATE_KEYS = {
    "source_rule_identity",
    "source_update_hash",
    "mean_pre_update_expected_reward",
    "raw_gradient_norm",
    "clip_grad_norm_return",
    "realized_update_norm",
    "norm_evidence_boundary",
    "diagonal_excess",
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
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ADDENDUM_KEYS = {
    "schema_version",
    "status",
    "base_config",
    "base_config_sha256",
    "reason",
    "only_change",
    "forbidden_changes",
    "replicate_count",
    "comparison_rule",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
}
RESULT_ANCHOR_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "mapping_stack_id",
    "master_inclusion_contract_sha256",
    "execution_manifest_sha256",
    "result_a_sha256",
    "result_b_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "result_a_replicate_id",
    "result_b_replicate_id",
    "result_a_run_nonce",
    "result_b_run_nonce",
    "invocation_start_receipt_a_sha256",
    "invocation_start_receipt_b_sha256",
    "custody_requirement",
}
INVOCATION_RECEIPT_KEYS = {
    "schema_version",
    "status",
    "evidence_boundary",
    "replicate_id",
    "run_nonce",
    "mapping_stack_id",
    "execution_manifest_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "started_at_utc",
    "custody_requirement",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non-finite value at {path}")
    elif isinstance(value, dict):
        for key, child in value.items():
            _finite(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite(child, f"{path}[{index}]", errors)


def _close(left: Any, right: Any, *, atol: float = 1e-15) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=atol)
    )


def substantive(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in VOLATILE_FIELDS}


def _replica_path_errors(left: Path, right: Path) -> list[str]:
    errors: list[str] = []
    if left.resolve() == right.resolve():
        errors.append("replica A and B resolve to the same result path")
    try:
        if os.path.samefile(left, right):
            errors.append("replica A and B are the same filesystem object")
    except (FileNotFoundError, OSError):
        errors.append("replica result file identity could not be verified")
    return errors


def _validate_invocation_receipt(
    receipt: dict[str, Any],
    *,
    actual_sha256: str,
    expected_sha256: str,
    result: dict[str, Any],
    manifest_sha256: str,
) -> list[str]:
    errors: list[str] = []
    if set(receipt) != INVOCATION_RECEIPT_KEYS:
        errors.append("invocation start receipt schema drift")
    if (
        receipt.get("schema_version") != static_contract.INVOCATION_RECEIPT_SCHEMA
        or receipt.get("status") != "FROZEN_BEFORE_MODEL_LOAD"
    ):
        errors.append("invocation start receipt status drift")
    if receipt.get("evidence_boundary") != static_contract.EVIDENCE_BOUNDARY:
        errors.append("invocation receipt exact evidence-boundary labels required")
    if actual_sha256 != expected_sha256:
        errors.append("external expected invocation receipt hash mismatch")
    direct = {
        "replicate_id": "replicate_id",
        "run_nonce": "run_nonce",
        "mapping_stack_id": "mapping_stack_id",
        "authorization_receipt_sha256": "authorization_receipt_sha256",
        "authorization_id": "authorization_id",
    }
    for receipt_key, result_key in direct.items():
        if receipt.get(receipt_key) != result.get(result_key):
            errors.append(f"invocation/result binding mismatch: {receipt_key}")
    if receipt.get("execution_manifest_sha256") != manifest_sha256:
        errors.append("invocation/manifest binding mismatch")
    if result.get("invocation_start_receipt_sha256") != actual_sha256:
        errors.append("result/invocation receipt hash mismatch")
    if receipt.get("custody_requirement") != (
        "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY"
    ):
        errors.append("invocation custody contract drift")
    return errors


def _trace_metrics(
    traces: list[dict[str, Any]], candidates: list[str]
) -> dict[str, float]:
    candidate_index = {candidate: index for index, candidate in enumerate(candidates)}
    result: dict[str, float] = {}
    for identity in IDENTITIES:
        values: list[float] = []
        for trace in traces:
            scores = trace["ordered_candidate_scores"]
            gold = trace["gold_candidate"]
            wrong = trace["offset_candidates"][identity]
            values.append(
                float(scores[candidate_index[wrong]])
                - float(scores[candidate_index[gold]])
            )
        result[identity] = sum(values) / len(values)
    return result


def _softmax(values: list[float]) -> list[float]:
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _source_gate_metrics(
    traces: list[dict[str, Any]], candidates: list[str]
) -> dict[str, dict[str, float]]:
    candidate_index = {candidate: index for index, candidate in enumerate(candidates)}
    result: dict[str, dict[str, float]] = {}
    for identity in IDENTITIES:
        wrong_mass: list[float] = []
        gold_mass: list[float] = []
        accepted_mass: list[float] = []
        relative_advantage: list[float] = []
        entropies: list[float] = []
        for trace in traces:
            probabilities = _softmax(
                [float(value) for value in trace["ordered_candidate_scores"]]
            )
            wrong = probabilities[
                candidate_index[trace["offset_candidates"][identity]]
            ]
            gold = probabilities[candidate_index[trace["gold_candidate"]]]
            accepted = wrong + gold
            wrong_mass.append(wrong)
            gold_mass.append(gold)
            accepted_mass.append(accepted)
            relative_advantage.append(1.0 - accepted)
            entropies.append(
                -sum(
                    probability * math.log(max(probability, 1e-30))
                    for probability in probabilities
                )
            )
        result[identity] = {
            "mean_wrong_probability_mass": sum(wrong_mass) / len(wrong_mass),
            "mean_gold_probability_mass": sum(gold_mass) / len(gold_mass),
            "mean_accepted_probability_mass": sum(accepted_mass) / len(accepted_mass),
            "mean_wrong_relative_advantage": sum(relative_advantage)
            / len(relative_advantage),
            "mean_candidate_entropy_nats": sum(entropies) / len(entropies),
        }
    return result


def _compare_nested_metrics(
    observed: Any,
    expected: Any,
    path: str,
    errors: list[str],
) -> None:
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or set(observed) != set(expected):
            errors.append(f"{path} metric schema mismatch")
            return
        for key, value in expected.items():
            _compare_nested_metrics(observed[key], value, f"{path}.{key}", errors)
    elif not _close(observed, expected, atol=2e-7):
        errors.append(f"{path} metric mismatch")


def _validate_traces(
    traces: Any,
    *,
    count: int,
    state: str,
    source_identity: str | None,
    expected_hash: str,
    candidates: list[str],
    path: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(traces, list) or len(traces) != count:
        errors.append(f"{path} trace count mismatch")
        return []
    row_ids: set[str] = set()
    for index, trace in enumerate(traces):
        trace_path = f"{path}[{index}]"
        if not isinstance(trace, dict) or set(trace) != TRACE_KEYS:
            errors.append(f"{trace_path} schema mismatch")
            continue
        row_id = trace.get("row_id")
        if not isinstance(row_id, str) or not row_id or row_id in row_ids:
            errors.append(f"{trace_path} row id invalid or reused")
        row_ids.add(str(row_id))
        if trace.get("state") != state:
            errors.append(f"{trace_path} state mismatch")
        if trace.get("source_rule_identity") != source_identity:
            errors.append(f"{trace_path} source identity mismatch")
        if trace.get("source_update_hash") != expected_hash:
            errors.append(f"{trace_path} update hash mismatch")
        scores = trace.get("ordered_candidate_scores")
        counts = trace.get("supervised_token_counts")
        if not isinstance(scores, list) or len(scores) != 7:
            errors.append(f"{trace_path} score vector mismatch")
        if (
            not isinstance(counts, list)
            or len(counts) != 7
            or any(type(value) is not int or value <= 0 for value in counts)
        ):
            errors.append(f"{trace_path} token counts invalid")
        if trace.get("gold_candidate") not in candidates:
            errors.append(f"{trace_path} gold candidate invalid")
        offsets = trace.get("offset_candidates")
        if (
            not isinstance(offsets, dict)
            or set(offsets) != set(IDENTITIES)
            or any(candidate not in candidates for candidate in offsets.values())
        ):
            errors.append(f"{trace_path} offset candidates invalid")
    return traces


def _trace_semantics(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "row_id": trace["row_id"],
            "gold_candidate": trace["gold_candidate"],
            "offset_candidates": trace["offset_candidates"],
        }
        for trace in traces
    ]


def validate_addendum(addendum: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(addendum) != ADDENDUM_KEYS:
        errors.append("addendum schema drift")
    if addendum.get("schema_version") != static_contract.ADDENDUM_SCHEMA:
        errors.append("wrong addendum schema")
    if addendum.get("status") != "FROZEN_BEFORE_AUTHORIZED_REPLICATES":
        errors.append("addendum is not frozen")
    if addendum.get("scientific_evidence") is not False:
        errors.append("addendum scientific status washing")
    if addendum.get("formal_experiment") is not False:
        errors.append("addendum formal status washing")
    if addendum.get("evidence_boundary") != static_contract.EVIDENCE_BOUNDARY:
        errors.append("addendum exact evidence-boundary labels required")
    if addendum.get("replicate_count") != 2:
        errors.append("addendum replicate count drift")
    if addendum.get("base_config_sha256") != manifest.get("bindings", {}).get("config_sha256"):
        errors.append("addendum/config binding mismatch")
    if addendum.get("only_change") != {
        "environment_variable": "CUBLAS_WORKSPACE_CONFIG",
        "value": ":4096:8",
    }:
        errors.append("addendum determinism environment drift")
    return errors


def validate_one(
    result: dict[str, Any],
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> list[str]:
    errors: list[str] = []
    if set(result) != RESULT_KEYS:
        errors.append("result schema has missing or extra fields")
    if result.get("schema_version") != "same-source-diagnostic-mvp-result-r5":
        errors.append("wrong result schema")
    if result.get("run_label") != "DIAGNOSTIC_MVP_NOT_FORMAL":
        errors.append("wrong run label")
    if result.get("scientific_evidence") is not False:
        errors.append("scientific status washing")
    if result.get("formal_experiment") is not False:
        errors.append("formal status washing")
    if result.get("evidence_boundary") != static_contract.EVIDENCE_BOUNDARY:
        errors.append("result exact evidence-boundary labels required")
    if result.get("norm_evidence_boundary") != static_contract.NORM_EVIDENCE_BOUNDARY:
        errors.append("norms must be explicitly labeled non-independent and non-scientific")
    if result.get("replicate_id") not in {"A", "B"}:
        errors.append("replicate_id must be A or B")
    if not isinstance(result.get("run_nonce"), str) or static_contract.UUID4_RE.fullmatch(
        result.get("run_nonce", "")
    ) is None:
        errors.append("run_nonce must be a random UUIDv4")
    for field in ("invocation_start_receipt_sha256", "authorization_receipt_sha256"):
        if not isinstance(result.get(field), str) or SHA256_RE.fullmatch(result.get(field, "")) is None:
            errors.append(f"{field} is invalid")
    if not isinstance(result.get("authorization_id"), str) or static_contract.UUID4_RE.fullmatch(
        result.get("authorization_id", "")
    ) is None:
        errors.append("authorization_id must be a UUIDv4")
    if result.get("claim_boundary") != static_contract.FROZEN_CLAIM_BOUNDARY:
        errors.append("result claim boundary drift")
    if result.get("run_status") != "MVP_COMPLETED_DIAGNOSTIC_ONLY":
        errors.append("diagnostic run did not complete")
    if result.get("diagnostic_gate_status") != "PASS" or result.get("diagnostic_gate_failures") != []:
        errors.append("diagnostic gate is not clean PASS")
    if result.get("determinism_fail_closed") is not True:
        errors.append("determinism did not fail closed")
    if result.get("source_row_count") != 14 or result.get("target_row_count") != 7:
        errors.append("row counts drifted")
    if result.get("candidate_supervised_token_count_range", 999) > 1:
        errors.append("candidate token-count range exceeds frozen gate")
    if result.get("restore_max_abs_target_score_error") != 0.0:
        errors.append("LoRA restoration is not exact")
    if not isinstance(result.get("trainable_parameters"), int) or result.get("trainable_parameters", 0) <= 0:
        errors.append("trainable parameter count is invalid")

    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        return [*errors, "manifest bindings missing"]
    if result.get("execution_manifest_sha256") != manifest_sha256:
        errors.append("execution manifest hash mismatch")
    if result.get("execution_manifest_bindings_sha256") != sha256_bytes(canonical_json_bytes(bindings)):
        errors.append("execution manifest binding hash mismatch")
    direct = {
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
    for result_key, binding_key in direct.items():
        if result.get(result_key) != bindings.get(binding_key):
            errors.append(f"provenance mismatch: {result_key}")
    if result.get("mapping_stack_id") != manifest.get("selected_mapping_stack_id"):
        errors.append("selected mapping stack mismatch")
    membership = manifest.get("stack_membership_commitments", {}).get(
        manifest.get("selected_mapping_stack_id"), {}
    )
    if result.get("source_rows_commitment") != membership.get(
        "source_rows_commitment"
    ):
        errors.append("source rows commitment mismatch")
    if result.get("target_rows_commitment") != membership.get(
        "target_rows_commitment"
    ):
        errors.append("target rows commitment mismatch")

    candidates = result.get("ordered_candidate_set")
    if candidates != [f"FINAL=K{i}" for i in range(7)]:
        errors.append("ordered candidate set mismatch")
        candidates = [f"FINAL=K{i}" for i in range(7)]
    initial_hash = result.get("initial_parameter_hash")
    if (
        not isinstance(initial_hash, str)
        or SHA256_RE.fullmatch(initial_hash) is None
        or result.get("initial_trainable_hash") != initial_hash
    ):
        errors.append("initial parameter hash invalid")
        initial_hash = str(initial_hash)
    parameter_hashes = result.get("source_update_parameter_hashes")
    if (
        not isinstance(parameter_hashes, dict)
        or set(parameter_hashes) != set(IDENTITIES)
        or any(
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
            for value in parameter_hashes.values()
        )
    ):
        errors.append("source update parameter hashes invalid")
        parameter_hashes = {}
    else:
        final_hashes = list(parameter_hashes.values())
        if len(set(final_hashes)) != 5:
            errors.append("source update parameter hashes are not unique")
        if initial_hash in final_hashes:
            errors.append("source update hash equals initial parameter hash")

    pre_source_traces = _validate_traces(
        result.get("pre_source_row_traces"),
        count=14,
        state="PRE_SOURCE",
        source_identity=None,
        expected_hash=initial_hash,
        candidates=candidates,
        path="pre_source_row_traces",
        errors=errors,
    )
    pre_target_traces = _validate_traces(
        result.get("pre_target_row_traces"),
        count=7,
        state="PRE_TARGET",
        source_identity=None,
        expected_hash=initial_hash,
        candidates=candidates,
        path="pre_target_row_traces",
        errors=errors,
    )
    if pre_source_traces and _trace_semantics(pre_source_traces) != membership.get(
        "source_trace_semantics"
    ):
        errors.append("source trace semantics do not match manifest")
    if pre_target_traces and _trace_semantics(pre_target_traces) != membership.get(
        "target_trace_semantics"
    ):
        errors.append("target trace semantics do not match manifest")
    post_by_source = result.get("post_target_row_traces_by_source_identity")
    validated_post: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(post_by_source, dict) or set(post_by_source) != set(
        IDENTITIES
    ):
        errors.append("post target trace source coverage mismatch")
    else:
        for source in IDENTITIES:
            validated_post[source] = _validate_traces(
                post_by_source[source],
                count=7,
                state="POST_TARGET",
                source_identity=source,
                expected_hash=str(parameter_hashes.get(source)),
                candidates=candidates,
                path=f"post_target_row_traces_by_source_identity.{source}",
                errors=errors,
            )
            if pre_target_traces and validated_post[source]:
                if [row["row_id"] for row in validated_post[source]] != [
                    row["row_id"] for row in pre_target_traces
                ]:
                    errors.append(f"post target row order mismatch: {source}")
                if _trace_semantics(validated_post[source]) != _trace_semantics(
                    pre_target_traces
                ):
                    errors.append(f"post target trace semantics mismatch: {source}")
                if [row["supervised_token_counts"] for row in validated_post[source]] != [
                    row["supervised_token_counts"] for row in pre_target_traces
                ]:
                    errors.append(f"post target token-count drift: {source}")

    expected_gates: dict[str, dict[str, float]] = {}
    if pre_source_traces:
        expected_gates = _source_gate_metrics(pre_source_traces, candidates)
        _compare_nested_metrics(
            result.get("base_source_gate_metrics"),
            expected_gates,
            "base_source_gate_metrics",
            errors,
        )
        for identity, values in expected_gates.items():
            if values["mean_wrong_probability_mass"] < float(
                static_contract.FROZEN_GATES[
                    "mean_source_wrong_probability_mass_min"
                ]
            ):
                errors.append(f"{identity}: recomputed wrong-mass gate failed")
            if values["mean_wrong_relative_advantage"] < float(
                static_contract.FROZEN_GATES[
                    "mean_source_relative_advantage_min"
                ]
            ):
                errors.append(f"{identity}: recomputed relative-advantage gate failed")
    if pre_target_traces:
        expected_base_target = _trace_metrics(pre_target_traces, candidates)
        _compare_nested_metrics(
            result.get("base_target_metrics"),
            expected_base_target,
            "base_target_metrics",
            errors,
        )
    else:
        expected_base_target = {}
    trace_token_counts = [
        count
        for trace in [*pre_source_traces, *pre_target_traces]
        for count in trace.get("supervised_token_counts", [])
    ]
    if trace_token_counts:
        expected_min = min(trace_token_counts)
        expected_max = max(trace_token_counts)
        expected_range = expected_max - expected_min
        if result.get("candidate_supervised_token_count_min") != expected_min:
            errors.append("candidate token-count minimum mismatch")
        if result.get("candidate_supervised_token_count_max") != expected_max:
            errors.append("candidate token-count maximum mismatch")
        if result.get("candidate_supervised_token_count_range") != expected_range:
            errors.append("candidate token-count range mismatch")

    updates = result.get("source_updates")
    cells = result.get("evaluation_cells")
    if not isinstance(updates, list) or len(updates) != 5:
        return [*errors, "source update count is not 5"]
    if not isinstance(cells, list) or len(cells) != 25:
        return [*errors, "evaluation cell count is not 25"]
    if any(not isinstance(row, dict) or set(row) != UPDATE_KEYS for row in updates):
        errors.append("source update schema drift")
    if any(not isinstance(row, dict) or set(row) != CELL_KEYS for row in cells):
        errors.append("evaluation cell schema drift")

    update_by_identity: dict[str, dict[str, Any]] = {}
    for row in updates:
        identity = row.get("source_rule_identity")
        if identity in update_by_identity:
            errors.append(f"duplicate source update identity: {identity}")
        update_by_identity[str(identity)] = row
    if set(update_by_identity) != set(IDENTITIES):
        errors.append("source identity coverage is incomplete")
    for identity, update in update_by_identity.items():
        update_hash = update.get("source_update_hash")
        if update_hash != parameter_hashes.get(identity):
            errors.append(f"update record/parameter hash mismatch: {identity}")
        if update.get("norm_evidence_boundary") != static_contract.NORM_EVIDENCE_BOUNDARY:
            errors.append(f"{identity}: exact non-independent norm labels required")
        for field in (
            "mean_pre_update_expected_reward",
            "raw_gradient_norm",
            "clip_grad_norm_return",
            "realized_update_norm",
        ):
            value = update.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                errors.append(f"{identity}: {field} is non-finite")
            elif field == "mean_pre_update_expected_reward":
                if not 0.0 <= float(value) <= 1.0:
                    errors.append(f"{identity}: expected reward outside [0,1]")
            elif float(value) < 0.0:
                errors.append(f"{identity}: {field} is negative")
        if identity in expected_gates and not _close(
            update.get("mean_pre_update_expected_reward"),
            expected_gates[identity]["mean_accepted_probability_mass"],
            atol=2e-7,
        ):
            errors.append(f"{identity}: expected reward not source-trace-derived")
    pairs = Counter(
        (row.get("source_rule_identity"), row.get("target_rule_identity"))
        for row in cells
    )
    expected_pairs = {(source, target) for source in IDENTITIES for target in IDENTITIES}
    if set(pairs) != expected_pairs or any(count != 1 for count in pairs.values()):
        errors.append("ordered identity-pair coverage is not exact 25/25")

    cells_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cells_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cells:
        source = str(row.get("source_rule_identity"))
        target = str(row.get("target_rule_identity"))
        cells_by_source[source].append(row)
        cells_by_pair[(source, target)] = row
        if row.get("same_update_reference") is not True:
            errors.append(f"same-update flag false: {source}/{target}")
        if row.get("is_diagonal") is not (source == target):
            errors.append(f"diagonal marker mismatch: {source}/{target}")
        expected_effect = float(row.get("post_target_metric", math.nan)) - float(
            row.get("pre_target_metric", math.nan)
        )
        if not _close(row.get("effect"), expected_effect):
            errors.append(f"effect not recomputed from pre/post: {source}/{target}")
        update = update_by_identity.get(source)
        if update is None or row.get("source_update_hash") != update.get("source_update_hash"):
            errors.append(f"source identity/update-hash binding mismatch: {source}/{target}")

    if expected_base_target and set(validated_post) == set(IDENTITIES):
        for source in IDENTITIES:
            post_metrics = _trace_metrics(validated_post[source], candidates)
            for target in IDENTITIES:
                cell = cells_by_pair.get((source, target))
                if cell is None:
                    continue
                expected_pre = expected_base_target[target]
                expected_post = post_metrics[target]
                expected_effect = expected_post - expected_pre
                if not _close(cell.get("pre_target_metric"), expected_pre, atol=2e-7):
                    errors.append(f"cell pre metric not trace-derived: {source}/{target}")
                if not _close(cell.get("post_target_metric"), expected_post, atol=2e-7):
                    errors.append(f"cell post metric not trace-derived: {source}/{target}")
                if not _close(cell.get("effect"), expected_effect, atol=2e-7):
                    errors.append(f"cell effect not trace-derived: {source}/{target}")

    recomputed_excess: dict[str, float] = {}
    for source in IDENTITIES:
        source_cells = cells_by_source.get(source, [])
        if len(source_cells) != 5:
            continue
        by_target = {str(row["target_rule_identity"]): float(row["effect"]) for row in source_cells}
        diagonal = by_target[source]
        off = [value for target, value in by_target.items() if target != source]
        excess = diagonal - sum(off) / 4.0
        recomputed_excess[source] = excess
        update = update_by_identity[source]
        if not _close(update.get("diagonal_excess"), excess):
            errors.append(f"diagonal excess mismatch: {source}")

    summary = result.get("stack_summary")
    if not isinstance(summary, dict) or set(summary) != {
        "mean_diagonal_excess",
        "positive_diagonal_excess_count",
        "source_rule_count",
    }:
        errors.append("stack summary schema drift")
    elif len(recomputed_excess) == 5:
        values = [recomputed_excess[source] for source in IDENTITIES]
        if not _close(summary.get("mean_diagonal_excess"), sum(values) / 5.0):
            errors.append("stack mean diagonal excess mismatch")
        if summary.get("positive_diagonal_excess_count") != sum(value > 0 for value in values):
            errors.append("positive diagonal excess count mismatch")
        if summary.get("source_rule_count") != 5:
            errors.append("stack source-rule count mismatch")
    if result.get("unique_source_update_hash_count") != len(
        {row.get("source_update_hash") for row in updates}
    ):
        errors.append("unique source-update hash count mismatch")
    _finite(result, "result", errors)
    return errors


def validate_replicates(
    left: dict[str, Any],
    right: dict[str, Any],
    addendum: dict[str, Any],
    manifest: dict[str, Any],
    manifest_sha256: str,
    expected_manifest_sha256: str,
    master: dict[str, Any],
    master_sha256: str,
    expected_master_sha256: str,
    result_anchor: dict[str, Any],
    result_anchor_sha256: str,
    expected_result_anchor_sha256: str,
    result_a_sha256: str,
    result_b_sha256: str,
    result_a_path: Path,
    result_b_path: Path,
    invocation_a: dict[str, Any],
    invocation_b: dict[str, Any],
    invocation_a_sha256: str,
    invocation_b_sha256: str,
    expected_invocation_a_sha256: str,
    expected_invocation_b_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    errors.extend(_replica_path_errors(result_a_path, result_b_path))
    if manifest_sha256 != expected_manifest_sha256:
        errors.append("external expected manifest hash mismatch")
    try:
        static_contract.validate_master_inclusion_contract(master)
    except static_contract.ContractError as error:
        errors.append(f"master inclusion contract invalid: {error}")
    if master_sha256 != expected_master_sha256:
        errors.append("external expected master hash mismatch")
    selected_stack = manifest.get("selected_mapping_stack_id")
    if master.get("manifest_sha256_by_stack", {}).get(selected_stack) != manifest_sha256:
        errors.append("master/selected manifest binding mismatch")
    if set(result_anchor) != RESULT_ANCHOR_KEYS:
        errors.append("result anchor schema drift")
    if (
        result_anchor.get("schema_version") != "same-source-result-pair-anchor-r5"
        or result_anchor.get("status") != "FROZEN_POST_RUN_PRE_VALIDATION"
        or result_anchor.get("scientific_evidence") is not False
        or result_anchor.get("formal_experiment") is not False
    ):
        errors.append("result anchor status drift")
    if result_anchor.get("evidence_boundary") != static_contract.EVIDENCE_BOUNDARY:
        errors.append("result anchor exact evidence-boundary labels required")
    if result_anchor_sha256 != expected_result_anchor_sha256:
        errors.append("external expected result-anchor hash mismatch")
    if result_anchor.get("mapping_stack_id") != selected_stack:
        errors.append("result anchor stack binding mismatch")
    if result_anchor.get("master_inclusion_contract_sha256") != master_sha256:
        errors.append("result anchor/master binding mismatch")
    if result_anchor.get("execution_manifest_sha256") != manifest_sha256:
        errors.append("result anchor/manifest binding mismatch")
    if result_anchor.get("result_a_sha256") != result_a_sha256:
        errors.append("external anchored result A hash mismatch")
    if result_anchor.get("result_b_sha256") != result_b_sha256:
        errors.append("external anchored result B hash mismatch")
    anchor_bindings = {
        "authorization_receipt_sha256": left.get("authorization_receipt_sha256"),
        "authorization_id": left.get("authorization_id"),
        "result_a_replicate_id": left.get("replicate_id"),
        "result_b_replicate_id": right.get("replicate_id"),
        "result_a_run_nonce": left.get("run_nonce"),
        "result_b_run_nonce": right.get("run_nonce"),
        "invocation_start_receipt_a_sha256": invocation_a_sha256,
        "invocation_start_receipt_b_sha256": invocation_b_sha256,
    }
    for field, expected in anchor_bindings.items():
        if result_anchor.get(field) != expected:
            errors.append(f"result anchor replica binding mismatch: {field}")
    if left.get("replicate_id") != "A" or right.get("replicate_id") != "B":
        errors.append("replicate identities must be ordered A then B")
    if left.get("run_nonce") == right.get("run_nonce"):
        errors.append("replica run nonces must be distinct")
    if invocation_a_sha256 == invocation_b_sha256:
        errors.append("replica invocation receipts must be distinct")
    if left.get("authorization_receipt_sha256") != right.get("authorization_receipt_sha256"):
        errors.append("replicas do not share one externally authorized action")
    if left.get("authorization_id") != right.get("authorization_id"):
        errors.append("replica authorization_id mismatch")
    errors.extend(
        f"invocation A: {message}"
        for message in _validate_invocation_receipt(
            invocation_a,
            actual_sha256=invocation_a_sha256,
            expected_sha256=expected_invocation_a_sha256,
            result=left,
            manifest_sha256=manifest_sha256,
        )
    )
    errors.extend(
        f"invocation B: {message}"
        for message in _validate_invocation_receipt(
            invocation_b,
            actual_sha256=invocation_b_sha256,
            expected_sha256=expected_invocation_b_sha256,
            result=right,
            manifest_sha256=manifest_sha256,
        )
    )
    if (
        result_anchor.get("custody_requirement")
        != "STORE_OUTSIDE_BOTH_RESULT_OUTPUT_DIRECTORIES_AND_RECORD_SHA256_EXTERNALLY"
    ):
        errors.append("result anchor custody contract drift")
    errors.extend([
        *(f"A: {message}" for message in validate_one(left, manifest=manifest, manifest_sha256=manifest_sha256)),
        *(f"B: {message}" for message in validate_one(right, manifest=manifest, manifest_sha256=manifest_sha256)),
        *(f"addendum: {message}" for message in validate_addendum(addendum, manifest)),
    ])
    identical = canonical_json_bytes(substantive(left)) == canonical_json_bytes(substantive(right))
    if not identical:
        errors.append("R5 replicates differ in substantive fields")
    return {
        "schema_version": "same-source-diagnostic-mvp-validation-r5",
        "validation_status": "PASS" if not errors else "FAIL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(static_contract.EVIDENCE_BOUNDARY),
        "mapping_stack_id": selected_stack,
        "result_anchor_sha256": result_anchor_sha256,
        "substantive_replicates_identical": identical,
        "errors": errors,
        "stack_summary": left.get("stack_summary"),
        "norm_evidence_boundary": dict(static_contract.NORM_EVIDENCE_BOUNDARY),
        "evidence_label": "CPU_STATIC_REPAIR_VALIDATOR_ONLY_R5",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--addendum", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--result-anchor", type=Path, required=True)
    parser.add_argument("--expected-result-anchor-sha256", required=True)
    parser.add_argument("--invocation-receipt-a", type=Path, required=True)
    parser.add_argument("--invocation-receipt-b", type=Path, required=True)
    parser.add_argument("--expected-invocation-receipt-a-sha256", required=True)
    parser.add_argument("--expected-invocation-receipt-b-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    left = json.loads(args.a.read_text(encoding="utf-8"))
    right = json.loads(args.b.read_text(encoding="utf-8"))
    addendum = json.loads(args.addendum.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    master = json.loads(args.master_inclusion_contract.read_text(encoding="utf-8"))
    result_anchor = json.loads(args.result_anchor.read_text(encoding="utf-8"))
    invocation_a = json.loads(args.invocation_receipt_a.read_text(encoding="utf-8"))
    invocation_b = json.loads(args.invocation_receipt_b.read_text(encoding="utf-8"))
    for label, value in (
        ("manifest", args.expected_manifest_sha256),
        ("master", args.expected_master_sha256),
        ("result anchor", args.expected_result_anchor_sha256),
        ("invocation receipt A", args.expected_invocation_receipt_a_sha256),
        ("invocation receipt B", args.expected_invocation_receipt_b_sha256),
    ):
        if SHA256_RE.fullmatch(value) is None:
            raise SystemExit(f"expected {label} SHA-256 must be 64 lowercase hex characters")
    report = validate_replicates(
        left,
        right,
        addendum,
        manifest,
        sha256_file(args.manifest),
        args.expected_manifest_sha256,
        master,
        sha256_file(args.master_inclusion_contract),
        args.expected_master_sha256,
        result_anchor,
        sha256_file(args.result_anchor),
        args.expected_result_anchor_sha256,
        sha256_file(args.a),
        sha256_file(args.b),
        args.a,
        args.b,
        invocation_a,
        invocation_b,
        sha256_file(args.invocation_receipt_a),
        sha256_file(args.invocation_receipt_b),
        args.expected_invocation_receipt_a_sha256,
        args.expected_invocation_receipt_b_sha256,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["validation_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
