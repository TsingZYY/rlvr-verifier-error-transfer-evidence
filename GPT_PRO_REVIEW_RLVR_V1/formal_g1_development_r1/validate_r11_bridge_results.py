from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any, Iterable

import analyze_r10_g1_development as r10_analysis


STACKS = list(r10_analysis.STACK_IDS)
IDENTITIES = list(r10_analysis.IDENTITIES)
ARMS = ["BUG", "GOLD_ONLY"]
REPLICATES = ["A", "B"]
EXPECTED_PROTOCOL_SHA256 = (
    "c9015c8d04da3b95d43162ab087e5fe4b4dcf1c4a5e689f2df610d2e379efd78"
)
EXPECTED_PARENT_RUNNER_SHA256 = (
    "439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d"
)
FLOAT_TOLERANCE = 1e-12


class R11ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R11ValidationError(message)


def finite_float(value: Any, label: str) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{label} is not numeric")
    result = float(value)
    require(math.isfinite(result), f"{label} is non-finite")
    return result


def mean(values: Iterable[float]) -> float:
    items = list(values)
    require(bool(items), "mean of empty collection")
    return sum(items) / len(items)


def matrix_close(
    left: list[list[float]], right: list[list[float]], tolerance: float = FLOAT_TOLERANCE
) -> bool:
    if len(left) != len(right):
        return False
    return all(
        len(lrow) == len(rrow)
        and all(abs(lvalue - rvalue) <= tolerance for lvalue, rvalue in zip(lrow, rrow))
        for lrow, rrow in zip(left, right)
    )


def effect_matrix(result: dict[str, Any], expected_arm: str) -> list[list[float]]:
    require(result.get("bridge_schema_version") == "r11-same-source-bridge-result-r1", "bridge result schema mismatch")
    require(result.get("arm") == expected_arm, f"result arm mismatch: expected {expected_arm}")
    require(result.get("r11_protocol_sha256") == EXPECTED_PROTOCOL_SHA256, "protocol hash mismatch")
    require(result.get("parent_r10_runner_sha256") == EXPECTED_PARENT_RUNNER_SHA256, "parent runner hash mismatch")
    require(result.get("diagnostic_gate_status") == "PASS", "diagnostic gate did not PASS")
    require(result.get("run_status") == "MVP_COMPLETED_DIAGNOSTIC_ONLY", "run did not complete diagnostic path")
    require(result.get("development_screen_only") is True, "development-only boundary missing")
    require(result.get("formal_confirmatory") is False, "formal-confirmatory boundary violated")
    require(result.get("sampled_rlvr") is False, "sampled RLVR boundary violated")
    require(result.get("hidden_audit") is False, "hidden audit boundary violated")

    updates = result.get("source_updates")
    require(isinstance(updates, list) and len(updates) == 5, "expected five source updates")
    require([item.get("source_rule_identity") for item in updates] == IDENTITIES, "source update identity/order mismatch")
    update_hashes: dict[str, str] = {}
    for item in updates:
        identity = str(item["source_rule_identity"])
        require(item.get("arm") == expected_arm, f"source update arm mismatch for {identity}")
        require(item.get("clipping_not_triggered") is True, f"clipping gate failed for {identity}")
        require(bool(item.get("reward_mask_sha256")), f"reward-mask hash missing for {identity}")
        update_hashes[identity] = str(item.get("source_update_hash"))
    require(len(set(update_hashes.values())) == 5, "source update hashes are not unique")

    cells = result.get("evaluation_cells")
    require(isinstance(cells, list) and len(cells) == 25, "expected 25 evaluation cells")
    matrix: list[list[float | None]] = [[None] * 5 for _ in range(5)]
    observed_pairs: list[tuple[str, str]] = []
    for index, cell in enumerate(cells):
        source = str(cell.get("source_rule_identity"))
        target = str(cell.get("target_rule_identity"))
        require(source in IDENTITIES and target in IDENTITIES, f"unknown identity at cell {index}")
        observed_pairs.append((source, target))
        require(cell.get("same_update_reference") is True, f"same-update flag failed for {(source, target)}")
        require(cell.get("source_update_hash") == update_hashes[source], f"source update hash mismatch for {(source, target)}")
        row = IDENTITIES.index(source)
        column = IDENTITIES.index(target)
        require(matrix[row][column] is None, f"duplicate cell {(source, target)}")
        matrix[row][column] = finite_float(cell.get("effect"), f"effect[{source},{target}]")
    expected_pairs = [(source, target) for source in IDENTITIES for target in IDENTITIES]
    require(observed_pairs == expected_pairs, "evaluation cells are not the ordered 5x5 product")
    require(all(value is not None for row in matrix for value in row), "effect matrix incomplete")
    return [[float(value) for value in row] for row in matrix]  # type: ignore[arg-type]


COMMON_PAIR_FIELDS = [
    "mapping_stack_id",
    "replicate_id",
    "config_sha256",
    "model_recursive_inventory_sha256",
    "chat_template_sha256",
    "source_bundles_sha256",
    "target_calibration_sha256",
    "mapping_stacks_sha256",
    "asset_validation_sha256",
    "initial_trainable_hash",
    "initial_parameter_hash",
    "source_rows_commitment",
    "target_rows_commitment",
    "ordered_candidate_set",
]


def pair_results(
    bug: dict[str, Any], gold: dict[str, Any], *, expected_stack: str, expected_replicate: str
) -> dict[str, Any]:
    bug_matrix = effect_matrix(bug, "BUG")
    gold_matrix = effect_matrix(gold, "GOLD_ONLY")
    require(bug.get("mapping_stack_id") == expected_stack, "BUG stack mismatch")
    require(gold.get("mapping_stack_id") == expected_stack, "GOLD stack mismatch")
    require(bug.get("replicate_id") == expected_replicate, "BUG replicate mismatch")
    require(gold.get("replicate_id") == expected_replicate, "GOLD replicate mismatch")
    for field in COMMON_PAIR_FIELDS:
        require(bug.get(field) == gold.get(field), f"BUG/GOLD binding mismatch: {field}")
    require(
        r10_analysis.canonical_bytes(bug.get("pre_target_row_traces"))
        == r10_analysis.canonical_bytes(gold.get("pre_target_row_traces")),
        "BUG/GOLD pre-target traces differ",
    )
    require(
        r10_analysis.canonical_bytes(bug.get("pre_source_row_traces"))
        == r10_analysis.canonical_bytes(gold.get("pre_source_row_traces")),
        "BUG/GOLD pre-source traces differ",
    )
    adjusted = [
        [bug_matrix[row][column] - gold_matrix[row][column] for column in range(5)]
        for row in range(5)
    ]
    absolute = [adjusted[index][index] for index in range(5)]
    structural = r10_analysis.diagonal_excess_by_identity(adjusted)
    return {
        "stack_id": expected_stack,
        "replicate": expected_replicate,
        "adjusted_matrix": adjusted,
        "absolute_matched_by_identity": dict(zip(IDENTITIES, absolute)),
        "structural_excess_by_identity": dict(zip(IDENTITIES, structural)),
        "absolute_stack_mean": mean(absolute),
        "structural_stack_mean": mean(structural),
    }


def identity_alignment_diagnostic(matrix: list[list[float]]) -> dict[str, Any]:
    identity = tuple(range(5))
    permutations = list(itertools.permutations(range(5)))
    scores = [r10_analysis.matched_mean(matrix, permutation) for permutation in permutations]
    observed = r10_analysis.matched_mean(matrix, identity)
    rank = 1 + sum(score > observed for score in scores)
    return {
        "identity_alignment_mean": observed,
        "identity_alignment_rank_of_120": rank,
        "fraction_deterministic_bijections_less_or_equal": sum(score <= observed for score in scores) / 120,
        "inferential_p_value": None,
        "randomization_test": False,
    }


def stack_parts(stack: str) -> tuple[str, str, str]:
    parts = stack.split("-")
    require(len(parts) == 3, f"invalid stack id: {stack}")
    return parts[0], parts[1], parts[2]


def summarize(paired: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    stack_matrices: dict[str, list[list[float]]] = {}
    stack_a: dict[str, float] = {}
    stack_d: dict[str, float] = {}
    identity_a: dict[str, list[float]] = {identity: [] for identity in IDENTITIES}
    identity_d: dict[str, list[float]] = {identity: [] for identity in IDENTITIES}

    for stack in STACKS:
        result_a = paired[stack]["A"]
        result_b = paired[stack]["B"]
        require(matrix_close(result_a["adjusted_matrix"], result_b["adjusted_matrix"]), f"replicate adjusted matrices disagree for {stack}")
        matrix = [
            [mean([result_a["adjusted_matrix"][row][column], result_b["adjusted_matrix"][row][column]]) for column in range(5)]
            for row in range(5)
        ]
        stack_matrices[stack] = matrix
        absolute = [matrix[index][index] for index in range(5)]
        structural = r10_analysis.diagonal_excess_by_identity(matrix)
        stack_a[stack] = mean(absolute)
        stack_d[stack] = mean(structural)
        for index, identity in enumerate(IDENTITIES):
            identity_a[identity].append(absolute[index])
            identity_d[identity].append(structural[index])

    def grouped(values: dict[str, float], dimension: int, label: str) -> dict[str, float]:
        groups: dict[str, list[float]] = {}
        for stack, value in values.items():
            key = stack_parts(stack)[dimension]
            groups.setdefault(key, []).append(value)
        require(len(groups) == 2, f"expected two {label} groups")
        return {key: mean(items) for key, items in sorted(groups.items())}

    identity_a_means = {key: mean(values) for key, values in identity_a.items()}
    identity_d_means = {key: mean(values) for key, values in identity_d.items()}
    task_pair_a = grouped(stack_a, 0, "task-pair")
    task_pair_d = grouped(stack_d, 0, "task-pair")
    mapping_a = grouped(stack_a, 1, "mapping")
    mapping_d = grouped(stack_d, 1, "mapping")
    direction_a = grouped(stack_a, 2, "direction")
    direction_d = grouped(stack_d, 2, "direction")
    all_signs_positive = all(
        value > 0
        for collection in (
            stack_a,
            stack_d,
            identity_a_means,
            identity_d_means,
            task_pair_a,
            task_pair_d,
            mapping_a,
            mapping_d,
            direction_a,
            direction_d,
        )
        for value in collection.values()
    )
    global_matrix = [
        [mean(stack_matrices[stack][row][column] for stack in STACKS) for column in range(5)]
        for row in range(5)
    ]
    return {
        "overall": {
            "absolute_matched_mean": mean(stack_a.values()),
            "structural_excess_mean": mean(stack_d.values()),
        },
        "by_stack": {
            stack: {
                "absolute_matched_mean": stack_a[stack],
                "structural_excess_mean": stack_d[stack],
                "identity_alignment": identity_alignment_diagnostic(stack_matrices[stack]),
            }
            for stack in STACKS
        },
        "by_identity": {
            identity: {
                "absolute_matched_mean": identity_a_means[identity],
                "structural_excess_mean": identity_d_means[identity],
            }
            for identity in IDENTITIES
        },
        "by_task_pair": {key: {"A": task_pair_a[key], "D": task_pair_d[key]} for key in task_pair_a},
        "by_mapping": {key: {"A": mapping_a[key], "D": mapping_d[key]} for key in mapping_a},
        "by_direction": {key: {"A": direction_a[key], "D": direction_d[key]} for key in direction_a},
        "global_identity_alignment": identity_alignment_diagnostic(global_matrix),
        "development_screen_decision": (
            "DEVELOPMENT_SCREEN_PASS_ONLY" if all_signs_positive else "INCONCLUSIVE_SIGN_GATE_FAILURE"
        ),
        "practical_margin_claimed": False,
        "directional_threshold": 0.0,
        "formal_confirmatory": False,
        "automatic_sampled_rlvr_progression": False,
    }


def safe_result_path(run_root: Path, relative_path: str) -> Path:
    candidate = (run_root / relative_path).resolve()
    try:
        candidate.relative_to(run_root.resolve())
    except ValueError as error:
        raise R11ValidationError(f"result path escapes run root: {relative_path}") from error
    require(candidate.is_file() and not candidate.is_symlink(), f"result is not a regular non-symlink file: {relative_path}")
    return candidate


def validate_run_index(run_root: Path, index: dict[str, Any]) -> dict[str, Any]:
    require(index.get("schema_version") == "r11-bridge-run-index-r1", "run index schema mismatch")
    require(index.get("r11_protocol_sha256") == EXPECTED_PROTOCOL_SHA256, "run index protocol hash mismatch")
    require(index.get("ordered_stack_ids") == STACKS, "run index stack order mismatch")
    paired: dict[str, dict[str, dict[str, Any]]] = {}
    for stack in STACKS:
        paired[stack] = {}
        for replicate in REPLICATES:
            arm_results: dict[str, dict[str, Any]] = {}
            for arm in ARMS:
                entry = index["results"][stack][arm][replicate]
                path = safe_result_path(run_root, str(entry["path"]))
                require(r10_analysis.sha256_file(path) == entry["sha256"], f"result hash mismatch: {stack}/{arm}/{replicate}")
                arm_results[arm] = r10_analysis.read_json(path)
            paired[stack][replicate] = pair_results(
                arm_results["BUG"],
                arm_results["GOLD_ONLY"],
                expected_stack=stack,
                expected_replicate=replicate,
            )
    summary = summarize(paired)
    return {
        "schema_version": "r11-bridge-validation-result-r1",
        "status": "PASS",
        "scientific_evidence": False,
        "formal_experiment": False,
        "formal_confirmatory": False,
        "model_execution_performed_by_validator": False,
        "process_results_checked": 32,
        "unique_arm_specific_design_cells": 80,
        "technical_update_executions": 160,
        "scientific_top_level_clusters": 2,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--run-index", type=Path, required=True)
    args = parser.parse_args()
    report = validate_run_index(args.run_root.resolve(), r10_analysis.read_json(args.run_index))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
