"""Frozen post-run development analysis for the R10 eight-stack MVP.

This analyzer is deliberately incapable of authorizing a formal G1, sampled
RLVR, or hidden-audit access.  It reconstructs all estimands from the immutable
25-cell matrices and reports deterministic identity-alignment controls.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any


STACK_IDS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
)
IDENTITIES = tuple(f"Z7_PLUS{index}" for index in range(1, 6))
PERMUTATIONS = tuple(itertools.permutations(range(5)))
TOLERANCE = 1e-12


class AnalysisError(RuntimeError):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON object required: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AnalysisError(message)


def finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise AnalysisError(f"boolean is not numeric: {label}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise AnalysisError(f"invalid numeric value: {label}") from error
    if not math.isfinite(result):
        raise AnalysisError(f"non-finite numeric value: {label}")
    return result


def effect_matrix(result: dict[str, Any]) -> list[list[float]]:
    cells = result.get("evaluation_cells")
    require(isinstance(cells, list) and len(cells) == 25, "exactly 25 cells required")
    matrix: list[list[float | None]] = [[None] * 5 for _ in range(5)]
    for cell in cells:
        require(isinstance(cell, dict), "cell must be an object")
        source = str(cell.get("source_rule_identity"))
        target = str(cell.get("target_rule_identity"))
        require(source in IDENTITIES and target in IDENTITIES, "unknown identity")
        row = IDENTITIES.index(source)
        column = IDENTITIES.index(target)
        require(matrix[row][column] is None, "duplicate source-target cell")
        matrix[row][column] = finite_float(cell.get("effect"), f"{source}/{target}")
        require(cell.get("same_update_reference") is True, "same update reference required")
    require(all(value is not None for row in matrix for value in row), "incomplete matrix")
    return [[float(value) for value in row] for row in matrix]


def diagonal_excess_by_identity(matrix: list[list[float]]) -> list[float]:
    require(len(matrix) == 5 and all(len(row) == 5 for row in matrix), "5x5 matrix required")
    return [
        row[index] - sum(value for column, value in enumerate(row) if column != index) / 4
        for index, row in enumerate(matrix)
    ]


def matched_mean(matrix: list[list[float]], permutation: tuple[int, ...]) -> float:
    require(len(permutation) == 5 and set(permutation) == set(range(5)), "invalid permutation")
    return sum(matrix[row][permutation[row]] for row in range(5)) / 5


def alignment_control(matrix: list[list[float]]) -> dict[str, Any]:
    observed = matched_mean(matrix, (0, 1, 2, 3, 4))
    reference = [matched_mean(matrix, permutation) for permutation in PERMUTATIONS]
    strictly_greater = sum(value > observed + TOLERANCE for value in reference)
    less_or_equal = sum(value <= observed + TOLERANCE for value in reference)
    return {
        "observed_identity_matched_mean": observed,
        "rank_descending_among_120": 1 + strictly_greater,
        "fraction_permutations_less_or_equal": less_or_equal / len(reference),
        "reference_min": min(reference),
        "reference_median": sorted(reference)[len(reference) // 2 - 1 : len(reference) // 2 + 1],
        "reference_max": max(reference),
        "inferential_p_value": None,
        "interpretation": "DETERMINISTIC_LABEL_ALIGNMENT_SENSITIVITY_ONLY",
    }


def mean(values: list[float]) -> float:
    require(bool(values), "cannot average empty values")
    return sum(values) / len(values)


def grouped_mean(values: dict[str, float], predicate: Any) -> float:
    selected = [value for key, value in values.items() if predicate(key)]
    return mean(selected)


def validate_prereg(prereg: dict[str, Any], run_root: Path, analyzer_path: Path) -> None:
    require(
        prereg.get("schema_version") == "r10-formal-g1-development-replay-prereg-r1",
        "wrong prereg schema",
    )
    require(
        prereg.get("status") == "FROZEN_POST_RUN_DEVELOPMENT_REPLAY_ONLY",
        "prereg status washing or drift",
    )
    require(prereg.get("formal_g1") is False, "formal G1 status washing")
    require(prereg.get("scientific_evidence") is False, "scientific status washing")
    require(prereg.get("new_model_action_authorized") is False, "model authorization forbidden")
    require(prereg.get("hidden_audit_access_authorized") is False, "hidden audit authorization forbidden")
    require(prereg.get("sampled_rlvr_authorized") is False, "sampled RLVR authorization forbidden")
    require(prereg.get("ordered_stack_ids") == list(STACK_IDS), "stack order drift")
    require(
        prereg.get("analyzer_sha256") == sha256_file(analyzer_path),
        "analyzer hash drift",
    )
    require(
        prereg.get("run_index_sha256") == sha256_file(run_root / "RUN_INDEX.json"),
        "run index hash drift",
    )
    require(
        prereg.get("completion_validation_sha256")
        == sha256_file(run_root / "completion" / "eight_stack_completion.json"),
        "completion hash drift",
    )
    rules = prereg.get("decision_rules")
    require(isinstance(rules, dict), "decision rules missing")
    require(rules.get("stack_sign_rule") == "MIN_ALL_EIGHT_STACK_EFFECTS_GT_ZERO", "stack rule drift")
    require(rules.get("identity_sign_rule") == "MIN_ALL_FIVE_IDENTITY_EFFECTS_GT_ZERO", "identity rule drift")
    require(rules.get("task_pair_sign_rule") == "BOTH_TASK_PAIR_EFFECTS_GT_ZERO", "pair rule drift")
    require(rules.get("practical_margin_status") == "NOT_FROZEN_BEFORE_R10_OUTCOMES", "margin status drift")
    require(rules.get("go_decision_permitted") is False, "GO must be forbidden")


def analyze(run_root: Path, prereg: dict[str, Any]) -> dict[str, Any]:
    run_index_path = run_root / "RUN_INDEX.json"
    completion_path = run_root / "completion" / "eight_stack_completion.json"
    run_index = read_json(run_index_path)
    completion = read_json(completion_path)
    require(run_index.get("ordered_stack_ids") == list(STACK_IDS), "run stack order drift")
    require(completion.get("validation_status") == "PASS", "R10 completion is not PASS")
    require(completion.get("scientific_evidence") is False, "completion status washing")

    stack_effects: dict[str, float] = {}
    stack_identity_effects: dict[str, dict[str, float]] = {}
    alignment_by_stack: dict[str, dict[str, Any]] = {}
    matrices: dict[str, list[list[float]]] = {}
    validations: dict[str, str] = {}

    for ordinal, stack_id in enumerate(STACK_IDS, start=1):
        slug = f"{ordinal:02d}_{stack_id}"
        result_a_path = run_root / "results" / slug / "A" / "result.json"
        result_b_path = run_root / "results" / slug / "B" / "result.json"
        validation_path = run_root / "validation" / f"{slug}_replicate_validation.json"
        bound = run_index["stacks"][stack_id]
        require(sha256_file(result_a_path) == bound["result_a_sha256"], f"A hash drift: {stack_id}")
        require(sha256_file(result_b_path) == bound["result_b_sha256"], f"B hash drift: {stack_id}")
        require(
            sha256_file(validation_path) == bound["validation_report_sha256"],
            f"validation hash drift: {stack_id}",
        )
        result_a = read_json(result_a_path)
        result_b = read_json(result_b_path)
        validation = read_json(validation_path)
        require(result_a.get("mapping_stack_id") == stack_id, f"A stack drift: {stack_id}")
        require(result_b.get("mapping_stack_id") == stack_id, f"B stack drift: {stack_id}")
        require(result_a.get("scientific_evidence") is False, "result status washing")
        require(result_b.get("scientific_evidence") is False, "result status washing")
        require(validation.get("validation_status") == "PASS", f"validator failed: {stack_id}")
        require(validation.get("substantive_replicates_identical") is True, f"replicas differ: {stack_id}")
        matrix_a = effect_matrix(result_a)
        matrix_b = effect_matrix(result_b)
        require(matrix_a == matrix_b, f"A/B matrices differ: {stack_id}")
        matrices[stack_id] = matrix_a
        identity_effects = diagonal_excess_by_identity(matrix_a)
        recomputed = mean(identity_effects)
        stored = finite_float(
            result_a.get("stack_summary", {}).get("mean_diagonal_excess"),
            f"stored stack summary/{stack_id}",
        )
        require(abs(recomputed - stored) <= TOLERANCE, f"stack summary mismatch: {stack_id}")
        stack_effects[stack_id] = recomputed
        stack_identity_effects[stack_id] = dict(zip(IDENTITIES, identity_effects))
        alignment_by_stack[stack_id] = alignment_control(matrix_a)
        validations[stack_id] = "PASS"

    identity_effects = {
        identity: mean([stack_identity_effects[stack][identity] for stack in STACK_IDS])
        for identity in IDENTITIES
    }
    pair_effects = {
        "TP1": grouped_mean(stack_effects, lambda key: key.startswith("TP1-")),
        "TP2": grouped_mean(stack_effects, lambda key: key.startswith("TP2-")),
    }
    direction_effects = {
        "A_TO_B": grouped_mean(stack_effects, lambda key: key.endswith("A_TO_B")),
        "B_TO_A": grouped_mean(stack_effects, lambda key: key.endswith("B_TO_A")),
    }
    mapping_effects = {
        "M0": grouped_mean(stack_effects, lambda key: "-M0-" in key),
        "M1": grouped_mean(stack_effects, lambda key: "-M1-" in key),
    }

    global_reference = []
    for permutation in PERMUTATIONS:
        global_reference.append(mean([matched_mean(matrices[stack], permutation) for stack in STACK_IDS]))
    global_observed = global_reference[0]
    global_rank = 1 + sum(value > global_observed + TOLERANCE for value in global_reference)

    stack_sign = min(stack_effects.values()) > 0
    identity_sign = min(identity_effects.values()) > 0
    pair_sign = min(pair_effects.values()) > 0
    sign_consistency = stack_sign and identity_sign and pair_sign
    if not sign_consistency:
        decision = "INCONCLUSIVE_SIGN_HETEROGENEITY_DO_NOT_ADVANCE_TO_SAMPLED_RLVR"
    else:
        decision = "INCONCLUSIVE_PRACTICAL_MARGIN_NOT_FROZEN_DO_NOT_ADVANCE_TO_SAMPLED_RLVR"

    return {
        "schema_version": "r10-formal-g1-development-replay-result-r1",
        "status": "COMPLETED_POST_RUN_DEVELOPMENT_REPLAY_ONLY",
        "decision": decision,
        "formal_g1": False,
        "scientific_evidence": False,
        "new_model_execution_performed": False,
        "sampled_rlvr_authorized": False,
        "hidden_audit_accessed": False,
        "input_bindings": {
            "run_index_sha256": sha256_file(run_index_path),
            "completion_validation_sha256": sha256_file(completion_path),
            "prereg_sha256": hashlib.sha256(canonical_bytes(prereg)).hexdigest(),
        },
        "validation_by_stack": validations,
        "recomputed_estimands": {
            "equal_weight_overall_mean_diagonal_excess": mean(list(stack_effects.values())),
            "task_pair_effects": pair_effects,
            "direction_effects": direction_effects,
            "mapping_effects": mapping_effects,
            "stack_effects": stack_effects,
            "identity_effects": identity_effects,
            "minimum_stack_effect": min(stack_effects.values()),
            "minimum_identity_effect": min(identity_effects.values()),
        },
        "reviewer_sign_consistency_gate": {
            "both_task_pair_effects_gt_zero": pair_sign,
            "all_eight_stack_effects_gt_zero": stack_sign,
            "all_five_identity_effects_gt_zero": identity_sign,
            "all_required_sign_conditions_met": sign_consistency,
            "practical_margin_frozen_before_r10_outcomes": False,
            "go_decision_permitted": False,
        },
        "identity_alignment_negative_control": {
            "per_stack": alignment_by_stack,
            "top_5_percent_stack_count_rank_le_6_of_120": sum(
                control["rank_descending_among_120"] <= 6
                for control in alignment_by_stack.values()
            ),
            "common_permutation_global_control": {
                "observed_identity_matched_mean": global_observed,
                "rank_descending_among_120": global_rank,
                "fraction_permutations_less_or_equal": sum(
                    value <= global_observed + TOLERANCE for value in global_reference
                )
                / len(global_reference),
                "inferential_p_value": None,
                "interpretation": "DETERMINISTIC_LABEL_ALIGNMENT_SENSITIVITY_ONLY",
            },
        },
        "evidence_boundary": {
            "data_scope": "ALREADY_OBSERVED_R10_DEVELOPMENT_CALIBRATION",
            "same_fpr_evidence": "NOT_SAME_FPR_EVIDENCE",
            "formal_g1_status": "NOT_FORMAL_G1",
            "rlvr_mode": "NOT_SAMPLED_RLVR",
            "audit_status": "NOT_HIDDEN_AUDIT",
            "confirmatory_inference": "FORBIDDEN",
        },
        "next_required_step": (
            "EXTERNAL_REVIEW_AND_FREEZE_OF_FRESH_G1_MARGIN_CONTROLS_AND_NEW_TASK_PAIR_CLUSTERS"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--prereg", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise AnalysisError(f"refusing to overwrite output: {args.output}")
    prereg = read_json(args.prereg)
    validate_prereg(prereg, args.run_root.resolve(), Path(__file__).resolve())
    result = analyze(args.run_root.resolve(), prereg)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
