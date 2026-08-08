from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


IDENTITIES = [f"Z7_PLUS{i}" for i in range(1, 6)]
STACKS = [
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
]
REPLICATES = ["A", "B"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def classify_reward_interface(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    update = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "update_for_source_rule"
        ),
        None,
    )
    if update is None:
        raise ValueError("update_for_source_rule is missing")
    argument_names = [argument.arg for argument in update.args.args]
    argument_names.extend(argument.arg for argument in update.args.kwonlyargs)
    normalized = {name.lower() for name in argument_names}
    explicit_arm_input = bool(
        normalized.intersection({"arm", "reward_mode", "reward_mask", "reward_spec"})
    )
    hardcoded_gold = 'reward[candidate_index[str(row["gold_candidate"])]] = 1.0' in source
    hardcoded_wrong = (
        "reward[candidate_index[candidate_for_offset(row, offset)]] = 1.0" in source
    )
    return {
        "update_function_arguments": argument_names,
        "explicit_arm_or_reward_mask_input": explicit_arm_input,
        "hardcoded_gold_assignment": hardcoded_gold,
        "hardcoded_wrong_assignment": hardcoded_wrong,
        "can_generate_gold_only_with_same_runner_hash": bool(
            explicit_arm_input and hardcoded_gold
        ),
    }


def expected_clean_cost() -> dict[str, int]:
    stacks = len(STACKS)
    identities = len(IDENTITIES)
    arms = 2
    replicates = len(REPLICATES)
    return {
        "new_os_processes": stacks * arms * replicates,
        "unique_arm_specific_design_cells": stacks * identities * arms,
        "technical_update_executions": stacks * identities * arms * replicates,
        "target_identity_evaluation_cells": (
            stacks * identities * len(IDENTITIES) * arms * replicates
        ),
    }


def validate_result_structure(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    updates = result.get("source_updates")
    if not isinstance(updates, list):
        return ["source_updates is not a list"]
    update_identities = [item.get("source_rule_identity") for item in updates]
    if update_identities != IDENTITIES:
        failures.append(
            f"source update identities/order mismatch: {update_identities!r}"
        )
    update_hashes = {
        item.get("source_rule_identity"): item.get("source_update_hash")
        for item in updates
        if isinstance(item, dict)
    }
    if len(set(update_hashes.values())) != len(IDENTITIES):
        failures.append("source update hashes are not five unique values")

    cells = result.get("evaluation_cells")
    if not isinstance(cells, list):
        return failures + ["evaluation_cells is not a list"]
    observed_pairs: list[tuple[str, str]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            failures.append("evaluation cell is not an object")
            continue
        source = cell.get("source_rule_identity")
        target = cell.get("target_rule_identity")
        observed_pairs.append((source, target))
        if cell.get("same_update_reference") is not True:
            failures.append(f"same_update_reference is not true for {(source, target)}")
        if cell.get("source_update_hash") != update_hashes.get(source):
            failures.append(f"update hash mismatch for {(source, target)}")
    expected_pairs = [(source, target) for source in IDENTITIES for target in IDENTITIES]
    if observed_pairs != expected_pairs:
        failures.append("evaluation cells do not equal the ordered 5x5 Cartesian product")
    if result.get("unique_source_update_hash_count") != 5:
        failures.append("unique_source_update_hash_count is not 5")
    if result.get("diagnostic_gate_status") != "PASS":
        failures.append("diagnostic gate did not PASS")
    return failures


def locate_stack_directory(results_root: Path, stack_id: str) -> Path:
    matches = sorted(
        path for path in results_root.iterdir() if path.is_dir() and path.name.endswith(stack_id)
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one result directory for {stack_id}: {matches}")
    return matches[0]


def audit(project_root: Path) -> dict[str, Any]:
    run_root = project_root / "authorized_runs" / "r10_devcal_20260804T130238Z"
    run_index_path = run_root / "RUN_INDEX.json"
    runner_path = project_root / "mvp_same_source_v1" / "run_same_source_mvp.py"
    run_index = read_json(run_index_path)
    failures: list[str] = []

    if run_index.get("ordered_stack_ids") != STACKS:
        failures.append("R10 ordered stack IDs differ from the frozen R11 list")
    if run_index.get("completion_validation_status") != "PASS":
        failures.append("R10 completion validation did not PASS")

    result_checks: list[dict[str, Any]] = []
    observed_runner_hashes: set[str] = set()
    results_root = run_root / "results"
    for stack_id in STACKS:
        stack_dir = locate_stack_directory(results_root, stack_id)
        expected = run_index["stacks"][stack_id]
        for replicate in REPLICATES:
            result_path = stack_dir / replicate / "result.json"
            actual_hash = sha256_file(result_path)
            expected_hash = expected[f"result_{replicate.lower()}_sha256"]
            result = read_json(result_path)
            structural_failures = validate_result_structure(result)
            if actual_hash != expected_hash:
                structural_failures.append("result hash differs from RUN_INDEX")
            observed_runner_hashes.add(str(result.get("runner_sha256")))
            failures.extend(
                f"{stack_id}/{replicate}: {failure}"
                for failure in structural_failures
            )
            result_checks.append(
                {
                    "stack_id": stack_id,
                    "replicate": replicate,
                    "result_sha256": actual_hash,
                    "source_update_cells": len(result.get("source_updates", [])),
                    "target_evaluation_cells": len(result.get("evaluation_cells", [])),
                    "status": "PASS" if not structural_failures else "FAIL",
                    "failures": structural_failures,
                }
            )

    runner_sha256 = sha256_file(runner_path)
    if observed_runner_hashes != {runner_sha256}:
        failures.append(
            "R10 result runner hashes do not all equal the current frozen R10 runner"
        )
    reward_interface = classify_reward_interface(runner_path.read_text(encoding="utf-8"))

    r10_structure_pass = not failures
    low_cost_reuse_eligible = bool(
        r10_structure_pass
        and reward_interface["can_generate_gold_only_with_same_runner_hash"]
    )
    if low_cost_reuse_eligible:
        verdict = "R10_BUG_REUSE_PATH_STATICALLY_ELIGIBLE_PENDING_RUNTIME_BRIDGE"
        next_action = (
            "Freeze a 16-process GOLD-only release and require the runtime bridge "
            "validator before any execution authorization."
        )
    else:
        verdict = "R10_BUG_REUSE_PATH_NOT_RUN_ELIGIBLE"
        next_action = (
            "Create one arm-parameterized runner, freeze BUG and GOLD_ONLY under the "
            "same package, and rerun both arms in 32 OS processes after separate authorization."
        )

    return {
        "schema_version": "r11-bridge-reuse-preflight-r1",
        "status": "PASS" if r10_structure_pass else "FAIL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "model_execution_performed": False,
        "model_execution_authorized": False,
        "parent_r10": {
            "run_index_sha256": sha256_file(run_index_path),
            "completion_validation_status": run_index.get(
                "completion_validation_status"
            ),
            "ordered_stack_count": len(run_index.get("ordered_stack_ids", [])),
            "result_process_count": len(result_checks),
            "unique_source_update_cells_per_replicate": len(STACKS)
            * len(IDENTITIES),
            "result_checks": result_checks,
        },
        "r10_runner": {
            "path": str(runner_path.resolve()),
            "sha256": runner_sha256,
            "reward_interface": reward_interface,
        },
        "bridge_decision": {
            "verdict": verdict,
            "r10_bug_reuse_low_cost_path_16_processes": low_cost_reuse_eligible,
            "reason": (
                "The frozen R10 runner hard-codes both gold and wrong reward assignments "
                "and exposes no signed arm/reward-mask input. A GOLD_ONLY result therefore "
                "requires an implementation change, so the cross-generation treatment "
                "difference cannot be certified as reward-mask-only under the same runner hash."
                if not low_cost_reuse_eligible
                else "The runner exposes a signed reward-mask interface."
            ),
            "next_action": next_action,
            "clean_fallback_cost": expected_clean_cost(),
        },
        "failures": failures,
        "authorization": {
            "cpu_static_audit": True,
            "tokenizer_load": False,
            "model_weight_load": False,
            "model_forward": False,
            "gradient": False,
            "optimizer_step": False,
            "sampled_rlvr": False,
            "hidden_audit": False,
        },
    }


def main() -> None:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=default_root)
    args = parser.parse_args()
    report = audit(args.project_root.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
