"""Model-free latent-identity versus candidate-displacement diagnostic.

This module reads only already-recorded R10 JSON assets.  It deliberately has
no imports from the model runner or any ML package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


ORDERED_STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
)
REPLICATES = ("A", "B")
SOURCE_IDENTITIES = (1, 2, 3, 4, 5)
TARGET_IDENTITIES = (1, 2, 3, 4, 5, 6)
EXPECTED_CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))
AB_TOLERANCE = 1e-12
DIRECTIONAL_EPSILON = 1e-10
EXPECTED_RESULT_SCHEMA = "same-source-diagnostic-mvp-result-r5"
EXPECTED_R10_RUNNER_SHA256 = (
    "439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d"
)


class DiagnosticError(RuntimeError):
    """Raised when a frozen input or technical gate is invalid."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise DiagnosticError(f"non-finite JSON constant: {value}")


def strict_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiagnosticError(f"{label}: JSON is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise DiagnosticError(f"{label}: invalid strict JSON: {exc}") from exc
    assert_finite(value, label)
    return value


def read_json(path: Path) -> Any:
    return strict_json_bytes(path.read_bytes(), str(path))


def read_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    for line_number, payload in enumerate(path.read_bytes().splitlines(), 1):
        if not payload.strip():
            raise DiagnosticError(f"{path}:{line_number}: blank JSONL row")
        rows.append(strict_json_bytes(payload, f"{path}:{line_number}"))
    return rows


def assert_finite(value: Any, path: str = "root") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DiagnosticError(f"{path}: non-finite number")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")
        return
    raise DiagnosticError(f"{path}: unsupported JSON value type {type(value)!r}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticError(message)


def _as_finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosticError(f"{label}: expected numeric value")
    result = float(value)
    if not math.isfinite(result):
        raise DiagnosticError(f"{label}: non-finite value")
    return result


def validate_protocol(protocol: Any) -> None:
    _require(isinstance(protocol, dict), "protocol must be an object")
    _require(
        protocol.get("schema_version")
        == "r10-latent-vs-surface-diagnostic-protocol-r1",
        "wrong protocol",
    )
    _require(
        protocol.get("status")
        == "FROZEN_MODEL_FREE_POST_HOC_DEVELOPMENT_DIAGNOSTIC",
        "protocol is not the frozen model-free diagnostic",
    )
    _require(protocol.get("frozen_before_diagnostic_execution") is True, "protocol was not frozen")
    _require(protocol.get("new_model_execution_authorized") is False, "protocol authorizes model execution")
    _require(protocol.get("new_model_execution_performed") is False, "protocol claims new model execution")
    _require(protocol.get("scientific_evidence") is False, "protocol evidence boundary drift")
    _require(protocol.get("formal_experiment") is False, "protocol formal status drift")
    _require(protocol.get("confirmatory_inference") is False, "protocol confirmatory status drift")
    inputs = protocol.get("inputs")
    _require(isinstance(inputs, dict), "protocol inputs missing")
    _require(tuple(inputs.get("ordered_stacks", [])) == ORDERED_STACKS, "protocol stack order drift")
    _require(tuple(inputs.get("ordered_replicates", [])) == REPLICATES, "protocol replicate order drift")
    _require(tuple(inputs.get("source_identities", [])) == SOURCE_IDENTITIES, "protocol source identities drift")
    _require(tuple(inputs.get("target_identities", [])) == TARGET_IDENTITIES, "protocol target identities drift")
    reconstruction = protocol.get("reconstruction")
    _require(isinstance(reconstruction, dict), "protocol reconstruction missing")
    _require(
        reconstruction.get("surface_alignment")
        == "q_surface(r) = inverse_mod7(a_target) * a_source * r mod 7, where a_source and a_target are the bound affine codebook multipliers.",
        "protocol surface-alignment formula drift",
    )
    _require(
        reconstruction.get("primary_contrast") == "L = S_latent - S_surface.",
        "protocol primary contrast drift",
    )
    gates = protocol.get("technical_gates")
    _require(isinstance(gates, dict), "protocol technical gates missing")
    _require(gates.get("finite_values_only") is True, "finite-value gate drift")
    _require(gates.get("exact_stack_and_replicate_coverage") is True, "coverage gate drift")
    _require(gates.get("a_b_absolute_tolerance") == AB_TOLERANCE, "A/B tolerance drift")
    _require(
        gates.get("directional_dead_zone_epsilon") == DIRECTIONAL_EPSILON,
        "directional epsilon drift",
    )
    _require(
        gates.get("require_q_surface_nonzero_and_distinct_from_r") is True,
        "surface-alignment distinctness gate drift",
    )
    authorization = protocol.get("authorization")
    _require(isinstance(authorization, dict), "protocol authorization missing")
    _require(authorization.get("read_existing_json_and_compute_cpu_diagnostic") is True, "CPU diagnostic disabled")
    for operation in (
        "tokenizer_load",
        "model_weight_load",
        "model_forward",
        "gradient",
        "optimizer_step",
    ):
        _require(authorization.get(operation) is False, f"protocol permits {operation}")


def validate_mapping_and_target_inputs(
    mappings: list[Any], target_rows: list[Any]
) -> dict[str, dict[str, Any]]:
    mapping_by_stack: dict[str, dict[str, Any]] = {}
    for index, mapping in enumerate(mappings):
        _require(isinstance(mapping, dict), f"mapping[{index}] must be an object")
        stack_id = str(mapping.get("mapping_stack_id"))
        _require(stack_id in ORDERED_STACKS, f"unexpected mapping stack: {stack_id}")
        _require(stack_id not in mapping_by_stack, f"duplicate mapping stack: {stack_id}")
        for label in ("source_codebook", "target_codebook"):
            codebook = mapping.get(label)
            _require(isinstance(codebook, dict), f"{stack_id}: missing {label}")
            candidates = codebook.get("latent_to_candidate")
            _require(
                isinstance(candidates, list)
                and tuple(candidates) != ()
                and len(candidates) == 7
                and set(candidates) == set(EXPECTED_CANDIDATES),
                f"{stack_id}: invalid {label} candidate permutation",
            )
            _require(
                type(codebook.get("multiplier_mod7")) is int
                and codebook["multiplier_mod7"] in range(1, 7),
                f"{stack_id}: invalid {label} multiplier",
            )
        mapping_by_stack[stack_id] = mapping
    _require(set(mapping_by_stack) == set(ORDERED_STACKS), "mapping stack coverage mismatch")

    _require(
        len(target_rows) == len(ORDERED_STACKS) * 7,
        "target calibration must contain exactly eight stacks by seven rows",
    )
    row_ids: set[str] = set()
    count_by_stack = {stack_id: 0 for stack_id in ORDERED_STACKS}
    for index, row in enumerate(target_rows):
        _require(isinstance(row, dict), f"target row {index} must be an object")
        stack_id = str(row.get("mapping_stack_id"))
        _require(stack_id in mapping_by_stack, f"target row has unknown stack: {stack_id}")
        _require(row.get("split_role") == "TARGET_CALIBRATION", f"{stack_id}: non-calibration row")
        row_id = str(row.get("row_id"))
        _require(row_id not in row_ids, f"duplicate target row id: {row_id}")
        row_ids.add(row_id)
        count_by_stack[stack_id] += 1
        _require(row.get("codebook") == mapping_by_stack[stack_id].get("target_codebook"), f"{stack_id}: target codebook drift")
        _require(tuple(row.get("candidate_order", [])) == EXPECTED_CANDIDATES, f"{stack_id}: target candidate order drift")
        z = row.get("canonical_z")
        _require(type(z) is int and z in range(7), f"{stack_id}: invalid canonical_z")
        _require(row.get("gold_candidate") == row["codebook"]["latent_to_candidate"][z], f"{stack_id}: target gold mismatch")
    _require(all(count == 7 for count in count_by_stack.values()), "target stack row coverage mismatch")
    return mapping_by_stack


def _target_candidate(row: dict[str, Any], q: int) -> str:
    z = int(row["canonical_z"])
    codebook = row["codebook"]
    mapping = codebook["latent_to_candidate"]
    _require(isinstance(mapping, list) and len(mapping) == 7, "invalid target codebook")
    candidate = str(mapping[(z + q) % 7])
    _require(candidate in EXPECTED_CANDIDATES, "target codebook candidate outside panel")
    return candidate


def _trace_map(
    traces: Any,
    *,
    label: str,
    expected_source_identity: str | None,
    expected_state: str,
    expected_update_hash: str,
    target_rows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    _require(isinstance(traces, list), f"{label}: traces must be a list")
    _require(len(traces) == 7, f"{label}: expected seven target traces")
    output: dict[str, dict[str, Any]] = {}
    for trace in traces:
        _require(isinstance(trace, dict), f"{label}: trace must be an object")
        row_id = str(trace.get("row_id"))
        _require(row_id in target_rows, f"{label}: unbound target row {row_id}")
        _require(row_id not in output, f"{label}: duplicate target row {row_id}")
        _require(
            trace.get("source_rule_identity") == expected_source_identity,
            f"{label}: source identity mismatch for {row_id}",
        )
        _require(trace.get("state") == expected_state, f"{label}: state mismatch for {row_id}")
        _require(
            trace.get("source_update_hash") == expected_update_hash,
            f"{label}: update hash mismatch for {row_id}",
        )
        scores = trace.get("ordered_candidate_scores")
        _require(isinstance(scores, list) and len(scores) == 7, f"{label}: bad scores")
        for index, score in enumerate(scores):
            _as_finite_float(score, f"{label}.{row_id}.scores[{index}]")
        row = target_rows[row_id]
        _require(trace.get("gold_candidate") == row.get("gold_candidate"), f"{label}: gold mismatch")
        offsets = trace.get("offset_candidates")
        _require(isinstance(offsets, dict), f"{label}: missing offset candidates")
        for q in SOURCE_IDENTITIES:
            key = f"Z7_PLUS{q}"
            _require(offsets.get(key) == _target_candidate(row, q), f"{label}: {key} mismatch")
        output[row_id] = trace
    _require(set(output) == set(target_rows), f"{label}: target row coverage mismatch")
    return output


def _metric(
    traces: dict[str, dict[str, Any]],
    target_rows: dict[str, dict[str, Any]],
    q: int,
) -> float:
    candidate_index = {candidate: index for index, candidate in enumerate(EXPECTED_CANDIDATES)}
    values: list[float] = []
    for row_id in sorted(target_rows):
        row = target_rows[row_id]
        trace = traces[row_id]
        scores = trace["ordered_candidate_scores"]
        wrong = _target_candidate(row, q)
        gold = str(row["gold_candidate"])
        values.append(
            _as_finite_float(scores[candidate_index[wrong]], f"{row_id}.wrong")
            - _as_finite_float(scores[candidate_index[gold]], f"{row_id}.gold")
        )
    return sum(values) / len(values)


def effect_matrix_from_result(
    result: dict[str, Any],
    target_rows_list: Iterable[dict[str, Any]],
    *,
    expected_mapping_sha256: str,
    expected_target_sha256: str,
) -> dict[int, dict[int, float]]:
    stack_id = str(result.get("mapping_stack_id"))
    _require(result.get("schema_version") == EXPECTED_RESULT_SCHEMA, f"{stack_id}: result schema drift")
    _require(result.get("scientific_evidence") is False, f"{stack_id}: scientific evidence drift")
    _require(result.get("formal_experiment") is False, f"{stack_id}: formal status drift")
    _require(result.get("runner_sha256") == EXPECTED_R10_RUNNER_SHA256, f"{stack_id}: runner hash drift")
    _require(result.get("mapping_stacks_sha256") == expected_mapping_sha256, f"{stack_id}: mapping input hash drift")
    _require(result.get("target_calibration_sha256") == expected_target_sha256, f"{stack_id}: target input hash drift")
    _require(
        result.get("run_status") == "MVP_COMPLETED_DIAGNOSTIC_ONLY",
        f"{stack_id}: run not completed under the frozen diagnostic label",
    )
    _require(result.get("diagnostic_gate_status") == "PASS", f"{stack_id}: diagnostic gate failed")
    _require(tuple(result.get("ordered_candidate_set", [])) == EXPECTED_CANDIDATES, f"{stack_id}: candidate order mismatch")
    rows = [row for row in target_rows_list if str(row.get("mapping_stack_id")) == stack_id]
    _require(len(rows) == 7, f"{stack_id}: expected seven bound target rows")
    target_rows = {str(row["row_id"]): row for row in rows}
    _require(len(target_rows) == 7, f"{stack_id}: duplicate target row id")
    initial_hash = str(result.get("initial_parameter_hash"))
    _require(initial_hash == result.get("initial_trainable_hash"), f"{stack_id}: initial hash mismatch")
    pre = _trace_map(
        result.get("pre_target_row_traces"),
        label=f"{stack_id}.pre",
        expected_source_identity=None,
        expected_state="PRE_TARGET",
        expected_update_hash=initial_hash,
        target_rows=target_rows,
    )
    posts = result.get("post_target_row_traces_by_source_identity")
    _require(isinstance(posts, dict), f"{stack_id}: missing post traces")
    expected_keys = {f"Z7_PLUS{r}" for r in SOURCE_IDENTITIES}
    _require(set(posts) == expected_keys, f"{stack_id}: post source identity coverage mismatch")
    update_hashes = result.get("source_update_parameter_hashes")
    _require(isinstance(update_hashes, dict) and set(update_hashes) == expected_keys, f"{stack_id}: update-hash coverage mismatch")
    pre_metrics = {q: _metric(pre, target_rows, q) for q in TARGET_IDENTITIES}
    matrix: dict[int, dict[int, float]] = {}
    for r in SOURCE_IDENTITIES:
        identity = f"Z7_PLUS{r}"
        post = _trace_map(
            posts[identity],
            label=f"{stack_id}.post.{identity}",
            expected_source_identity=identity,
            expected_state="POST_TARGET",
            expected_update_hash=str(update_hashes[identity]),
            target_rows=target_rows,
        )
        matrix[r] = {
            q: _metric(post, target_rows, q) - pre_metrics[q]
            for q in TARGET_IDENTITIES
        }

    # The q=1..5 self-reported cells are not trusted as inputs, but disagreement
    # with the trace reconstruction is a technical failure.
    cells = result.get("evaluation_cells")
    _require(isinstance(cells, list) and len(cells) == 25, f"{stack_id}: invalid evaluation cells")
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        source = str(cell.get("source_rule_identity", ""))
        target = str(cell.get("target_rule_identity", ""))
        _require(source.startswith("Z7_PLUS") and target.startswith("Z7_PLUS"), f"{stack_id}: bad cell identity")
        r = int(source.removeprefix("Z7_PLUS"))
        q = int(target.removeprefix("Z7_PLUS"))
        _require(r in SOURCE_IDENTITIES and q in SOURCE_IDENTITIES, f"{stack_id}: cell outside frozen grid")
        _require((r, q) not in seen, f"{stack_id}: duplicate evaluation cell")
        seen.add((r, q))
        reported = _as_finite_float(cell.get("effect"), f"{stack_id}.cell.effect")
        _require(
            cell.get("source_update_hash") == update_hashes[source],
            f"{stack_id}: evaluation-cell update hash mismatch",
        )
        _require(abs(reported - matrix[r][q]) <= AB_TOLERANCE, f"{stack_id}: trace/cell effect mismatch")
    _require(len(seen) == 25, f"{stack_id}: incomplete evaluation grid")
    return matrix


def double_center(
    matrix: dict[int, dict[int, float]]
) -> dict[int, dict[int, float]]:
    _require(set(matrix) == set(SOURCE_IDENTITIES), "matrix source coverage mismatch")
    for r in SOURCE_IDENTITIES:
        _require(set(matrix[r]) == set(TARGET_IDENTITIES), "matrix target coverage mismatch")
    row_means = {
        r: sum(matrix[r][q] for q in TARGET_IDENTITIES) / len(TARGET_IDENTITIES)
        for r in SOURCE_IDENTITIES
    }
    column_means = {
        q: sum(matrix[r][q] for r in SOURCE_IDENTITIES) / len(SOURCE_IDENTITIES)
        for q in TARGET_IDENTITIES
    }
    grand = sum(row_means.values()) / len(row_means)
    return {
        r: {
            q: matrix[r][q] - row_means[r] - column_means[q] + grand
            for q in TARGET_IDENTITIES
        }
        for r in SOURCE_IDENTITIES
    }


def surface_alignment(source_multiplier: int, target_multiplier: int) -> dict[int, int]:
    _require(source_multiplier in range(1, 7), "invalid source multiplier")
    _require(target_multiplier in range(1, 7), "invalid target multiplier")
    inverse_target = pow(target_multiplier, -1, 7)
    alignment = {
        r: (inverse_target * source_multiplier * r) % 7 for r in SOURCE_IDENTITIES
    }
    for r, q in alignment.items():
        _require(q in TARGET_IDENTITIES, "surface alignment unexpectedly maps to zero")
        _require(q != r, "surface and latent alignments are not distinct")
    return alignment


def compare_matrices(
    first: dict[int, dict[int, float]],
    second: dict[int, dict[int, float]],
    tolerance: float,
) -> float:
    maximum = max(
        abs(first[r][q] - second[r][q])
        for r in SOURCE_IDENTITIES
        for q in TARGET_IDENTITIES
    )
    _require(maximum <= tolerance, f"A/B matrix mismatch: {maximum} > {tolerance}")
    return maximum


def _find_stack_directory(results_root: Path, stack_id: str) -> Path:
    matches = [path for path in results_root.iterdir() if path.is_dir() and path.name.endswith(f"_{stack_id}")]
    _require(len(matches) == 1, f"{stack_id}: expected exactly one result directory")
    return matches[0]


def run_diagnostic(
    *,
    protocol_path: Path,
    results_root: Path,
    mapping_path: Path,
    target_path: Path,
) -> dict[str, Any]:
    protocol = read_json(protocol_path)
    validate_protocol(protocol)
    mappings = read_jsonl(mapping_path)
    target_rows = read_jsonl(target_path)
    mapping_by_stack = validate_mapping_and_target_inputs(mappings, target_rows)
    mapping_sha256 = sha256_file(mapping_path)
    target_sha256 = sha256_file(target_path)

    stack_results: dict[str, Any] = {}
    input_result_sha256: dict[str, dict[str, str]] = {}
    for stack_id in ORDERED_STACKS:
        stack_dir = _find_stack_directory(results_root, stack_id)
        matrices: dict[str, dict[int, dict[int, float]]] = {}
        input_result_sha256[stack_id] = {}
        for replicate in REPLICATES:
            result_path = stack_dir / replicate / "result.json"
            _require(result_path.is_file(), f"{stack_id}/{replicate}: missing result")
            result = read_json(result_path)
            _require(result.get("replicate_id") == replicate, f"{stack_id}: replicate mismatch")
            _require(result.get("mapping_stack_id") == stack_id, f"{stack_id}: result stack mismatch")
            matrices[replicate] = effect_matrix_from_result(
                result,
                target_rows,
                expected_mapping_sha256=mapping_sha256,
                expected_target_sha256=target_sha256,
            )
            input_result_sha256[stack_id][replicate] = sha256_file(result_path)
        ab_max = compare_matrices(matrices["A"], matrices["B"], AB_TOLERANCE)
        matrix = {
            r: {
                q: (matrices["A"][r][q] + matrices["B"][r][q]) / 2.0
                for q in TARGET_IDENTITIES
            }
            for r in SOURCE_IDENTITIES
        }
        residual = double_center(matrix)
        mapping = mapping_by_stack[stack_id]
        alignment = surface_alignment(
            int(mapping["source_codebook"]["multiplier_mod7"]),
            int(mapping["target_codebook"]["multiplier_mod7"]),
        )
        latent_by_r = {r: residual[r][r] for r in SOURCE_IDENTITIES}
        surface_by_r = {r: residual[r][alignment[r]] for r in SOURCE_IDENTITIES}
        latent_score = sum(latent_by_r.values()) / len(latent_by_r)
        surface_score = sum(surface_by_r.values()) / len(surface_by_r)
        contrast = latent_score - surface_score
        _require(math.isfinite(contrast), f"{stack_id}: non-finite contrast")
        stack_results[stack_id] = {
            "task_pair_id": str(mapping["task_pair_id"]),
            "mapping_id": str(mapping["mapping_id"]),
            "direction": str(mapping["mirror_role"]),
            "source_multiplier_mod7": int(mapping["source_codebook"]["multiplier_mod7"]),
            "target_multiplier_mod7": int(mapping["target_codebook"]["multiplier_mod7"]),
            "surface_alignment_by_source_identity": {f"Z7_PLUS{r}": f"Z7_PLUS{alignment[r]}" for r in SOURCE_IDENTITIES},
            "ab_max_abs_effect_matrix_difference": ab_max,
            "latent_residual_by_source_identity": {f"Z7_PLUS{r}": latent_by_r[r] for r in SOURCE_IDENTITIES},
            "surface_residual_by_source_identity": {f"Z7_PLUS{r}": surface_by_r[r] for r in SOURCE_IDENTITIES},
            "s_latent": latent_score,
            "s_surface": surface_score,
            "latent_minus_surface": contrast,
            "stack_gate": "PASS" if contrast > DIRECTIONAL_EPSILON else "FAIL",
        }

    failing = [stack_id for stack_id in ORDERED_STACKS if stack_results[stack_id]["stack_gate"] != "PASS"]
    task_pair_groups: dict[str, list[float]] = {}
    for stack in stack_results.values():
        task_pair_groups.setdefault(stack["task_pair_id"], []).append(stack["latent_minus_surface"])
    task_pair_means = {key: sum(values) / len(values) for key, values in sorted(task_pair_groups.items())}
    decision = (
        "LATENT_ALIGNMENT_DEVELOPMENT_SCREEN_PASS_ONLY"
        if not failing
        else "STOP_SHARED_IDENTITY_CLAIM_SURFACE_COMPETITOR_NOT_DEFEATED"
    )
    return {
        "schema_version": "r10-latent-vs-surface-diagnostic-result-r1",
        "status": "COMPLETED_MODEL_FREE_POST_HOC_DEVELOPMENT_DIAGNOSTIC",
        "decision": decision,
        "scientific_evidence": False,
        "formal_experiment": False,
        "confirmatory_inference": False,
        "new_model_execution_performed": False,
        "input_bindings": {
            "protocol_path": str(protocol_path),
            "protocol_sha256": sha256_file(protocol_path),
            "mapping_stacks_sha256": sha256_file(mapping_path),
            "target_calibration_sha256": sha256_file(target_path),
            "result_sha256_by_stack_and_replicate": input_result_sha256,
        },
        "technical_contract": {
            "a_b_absolute_tolerance": AB_TOLERANCE,
            "directional_dead_zone_epsilon": DIRECTIONAL_EPSILON,
            "stack_count": len(ORDERED_STACKS),
            "technical_result_files": len(ORDERED_STACKS) * len(REPLICATES),
            "top_level_scientific_clusters": 2,
        },
        "stack_results": stack_results,
        "failing_stacks": failing,
        "all_eight_stacks_latent_gt_surface": not failing,
        "task_pair_mean_latent_minus_surface": task_pair_means,
        "descriptive_overall_mean_latent_minus_surface": sum(
            stack_results[stack_id]["latent_minus_surface"] for stack_id in ORDERED_STACKS
        ) / len(ORDERED_STACKS),
        "evidence_boundary": {
            "data_scope": "ALREADY_OBSERVED_R10_DEVELOPMENT_CALIBRATION",
            "outcome_aware_post_hoc": True,
            "inferential_p_value": None,
            "same_empirical_or_policy_weighted_fpr": False,
            "sampled_rlvr": False,
            "hidden_audit": False,
            "causal_shared_identity_proof": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path(__file__).resolve().with_name("R10_LATENT_VS_SURFACE_DIAGNOSTIC_PROTOCOL_R1.json"),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=project_root / "authorized_runs" / "r10_devcal_20260804T130238Z" / "results",
    )
    parser.add_argument(
        "--mapping-stacks",
        type=Path,
        default=project_root / "real_assets" / "build_r4_a" / "REAL_MAPPING_STACKS_V1.jsonl",
    )
    parser.add_argument(
        "--target-calibration",
        type=Path,
        default=project_root / "real_assets" / "build_r4_a" / "TARGET_CALIBRATION_REAL_V1.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().with_name("R10_LATENT_VS_SURFACE_DIAGNOSTIC_RESULT_R1.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.output.exists():
        raise DiagnosticError(f"refusing to overwrite existing output: {args.output}")
    result = run_diagnostic(
        protocol_path=args.protocol,
        results_root=args.results_root,
        mapping_path=args.mapping_stacks,
        target_path=args.target_calibration,
    )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"decision": result["decision"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
