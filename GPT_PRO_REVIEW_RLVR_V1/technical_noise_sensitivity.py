"""Audit what the saved Stage-ISB artifacts can say about technical noise.

This script intentionally does not bootstrap a treatment effect.  The saved
kernel artifacts contain only condition aggregates, so prompt-by-candidate
gradient or post-update outcomes are unavailable.  The script instead:

1. checks whether the v1/v2 saved source-score tables are byte-numerically
   identical at action level; and
2. summarizes the independent model-reload forward-replay discrepancies that
   were retained per prompt.

The resulting bound applies only to frozen forward scoring.  It does not
identify one-step optimizer/update-outcome reproducibility or mapping-level
scientific variance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def describe(values: list[float]) -> dict[str, float | int]:
    median = statistics.median(values)
    absolute_deviations = [abs(value - median) for value in values]
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": median,
        "median_absolute_deviation": statistics.median(absolute_deviations),
        "sample_variance_across_prompt_maxima": (
            statistics.variance(values) if len(values) > 1 else 0.0
        ),
        "empirical_p95": quantile(values, 0.95),
        "empirical_p99": quantile(values, 0.99),
        "maximum": max(values),
    }


def compare_source_scores(v1_path: Path, v2_path: Path) -> dict[str, Any]:
    v1 = load_json(v1_path)
    v2 = load_json(v2_path)

    def action_map(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        for row in payload["actions"]:
            key = (row["prompt_id"], row["source_action_sha256"])
            if key in rows:
                raise ValueError(f"duplicate action key: {key}")
            rows[key] = row
        return rows

    actions_v1 = action_map(v1)
    actions_v2 = action_map(v2)
    keys_equal = actions_v1.keys() == actions_v2.keys()
    shared_keys = sorted(actions_v1.keys() & actions_v2.keys())

    numeric_fields = (
        "base_mean_logp_including_eos",
        "base_sum_logp_including_eos",
    )
    max_abs_differences = {
        field: max(
            (
                abs(float(actions_v1[key][field]) - float(actions_v2[key][field]))
                for key in shared_keys
            ),
            default=0.0,
        )
        for field in numeric_fields
    }
    prompt_hashes_v1 = {
        row["prompt_id"]: row["prompt_record_sha256"] for row in v1["prompts"]
    }
    prompt_hashes_v2 = {
        row["prompt_id"]: row["prompt_record_sha256"] for row in v2["prompts"]
    }

    return {
        "v1_path": str(v1_path),
        "v2_path": str(v2_path),
        "v1_sha256": sha256_file(v1_path),
        "v2_sha256": sha256_file(v2_path),
        "v1_actions": len(actions_v1),
        "v2_actions": len(actions_v2),
        "shared_actions": len(shared_keys),
        "action_key_sets_equal": keys_equal,
        "prompt_record_hashes_equal": prompt_hashes_v1 == prompt_hashes_v2,
        "max_abs_differences": max_abs_differences,
        "all_saved_numeric_values_identical": (
            keys_equal
            and prompt_hashes_v1 == prompt_hashes_v2
            and all(value == 0.0 for value in max_abs_differences.values())
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--delta", default=math.log(1.05), type=float)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the exact JSON payload to this path instead of stdout.",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    stage_root = (
        project_root
        / "outputs"
        / "arithmetic-gate1-stage-ISB"
        / "engineering"
    )
    v1_root = stage_root / "block-b000-root93001-real-v1"
    v2_root = stage_root / "block-b000-root93001-real-v2"

    comparisons: dict[str, Any] = {}
    for split in ("train", "h-id"):
        comparisons[split] = compare_source_scores(
            v1_root / "blocks" / split / "source-score.json",
            v2_root / "blocks" / split / "source-score.json",
        )

    forward_audit_path = v2_root / "source-forward-audit.json"
    forward_audit = load_json(forward_audit_path)
    prompt_rows: list[dict[str, Any]] = []
    for split in ("train", "h-id"):
        for row in forward_audit["splits"][split]["prompt_rows"]:
            prompt_rows.append({"split": split, **row})

    sum_logp_errors = [
        abs(float(row["maximum_action_sum_logp_error"])) for row in prompt_rows
    ]
    probability_errors = [
        abs(float(row["maximum_probability_error"])) for row in prompt_rows
    ]
    delta_quarter = args.delta / 4.0
    maximum_sum_logp_error = max(sum_logp_errors)
    conservative_bug_minus_gold_error_bound = 2.0 * maximum_sum_logp_error

    result = {
        "schema_version": "rlvr-technical-noise-sensitivity-v1",
        "analysis_scope": "saved forward-scoring reproducibility only",
        "project_root": str(project_root),
        "candidate_practical_delta_nats": args.delta,
        "candidate_delta_quarter_nats": delta_quarter,
        "source_score_v1_v2_comparisons": comparisons,
        "source_forward_audit": {
            "path": str(forward_audit_path),
            "sha256": sha256_file(forward_audit_path),
            "audit_kind": forward_audit["audit_kind"],
            "actions_checked": forward_audit["actions_checked"],
            "passed_under_original_thresholds": forward_audit["passed"],
            "prompt_rows_retained": len(prompt_rows),
            "maximum_action_sum_logp_error_by_prompt": describe(sum_logp_errors),
            "maximum_probability_error_by_prompt": describe(probability_errors),
            "conservative_bug_minus_gold_logp_error_bound": (
                conservative_bug_minus_gold_error_bound
            ),
            "conservative_bound_as_fraction_of_delta_quarter": (
                conservative_bug_minus_gold_error_bound / delta_quarter
            ),
            "forward_scoring_below_candidate_noise_gate": (
                conservative_bug_minus_gold_error_bound < delta_quarter
            ),
        },
        "requested_hierarchical_bootstrap": {
            "status": "NOT_IDENTIFIABLE_FROM_SAVED_ARTIFACTS",
            "reason": (
                "Kernel artifacts retain condition aggregates and prompt-level "
                "mean norms, not prompt-by-candidate gradient/transfer outcomes "
                "or repeated post-update target logits. Resampling saved rows "
                "would estimate dataset-row variation, not technical "
                "reproducibility of the one-step outcome."
            ),
            "not_estimated": [
                "mapping_level_sigma",
                "one_step_update_outcome_technical_noise",
                "treatment_effect_tau",
                "power_for_the_new_five_or_nine_cell_design",
            ],
        },
        "decision": {
            "forward_scoring_replay": (
                "PASS_FOR_NUMERICAL_FORWARD_REPLAY_ONLY"
                if conservative_bug_minus_gold_error_bound < delta_quarter
                else "FAIL_FORWARD_REPLAY_NOISE_GATE"
            ),
            "one_step_core_outcome_noise": "NOT_IDENTIFIABLE",
            "scientific_claim": "NO_CHANGE",
        },
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
