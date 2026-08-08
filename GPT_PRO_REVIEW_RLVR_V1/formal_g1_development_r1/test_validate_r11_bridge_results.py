from __future__ import annotations

import copy
import unittest

import validate_r11_bridge_results as validator


def make_result(arm: str, *, shift: float, stack: str = "TP1-M0-A_TO_B", replicate: str = "A") -> dict:
    updates = []
    cells = []
    for source_index, source in enumerate(validator.IDENTITIES):
        update_hash = f"{arm}-{source}"
        updates.append(
            {
                "source_rule_identity": source,
                "source_update_hash": update_hash,
                "arm": arm,
                "reward_mask_sha256": f"mask-{arm}-{source}",
                "clipping_not_triggered": True,
            }
        )
        for target_index, target in enumerate(validator.IDENTITIES):
            effect = shift + (0.2 if source_index == target_index else 0.0)
            cells.append(
                {
                    "source_rule_identity": source,
                    "target_rule_identity": target,
                    "source_update_hash": update_hash,
                    "same_update_reference": True,
                    "effect": effect,
                }
            )
    return {
        "bridge_schema_version": "r11-same-source-bridge-result-r1",
        "arm": arm,
        "r11_protocol_sha256": validator.EXPECTED_PROTOCOL_SHA256,
        "parent_r10_runner_sha256": validator.EXPECTED_PARENT_RUNNER_SHA256,
        "diagnostic_gate_status": "PASS",
        "run_status": "MVP_COMPLETED_DIAGNOSTIC_ONLY",
        "development_screen_only": True,
        "formal_confirmatory": False,
        "sampled_rlvr": False,
        "hidden_audit": False,
        "mapping_stack_id": stack,
        "replicate_id": replicate,
        "config_sha256": "config",
        "model_recursive_inventory_sha256": "model",
        "chat_template_sha256": "chat",
        "source_bundles_sha256": "source",
        "target_calibration_sha256": "target",
        "mapping_stacks_sha256": "mapping",
        "asset_validation_sha256": "asset",
        "initial_trainable_hash": "initial-trainable",
        "initial_parameter_hash": "initial-parameter",
        "source_rows_commitment": "source-rows",
        "target_rows_commitment": "target-rows",
        "ordered_candidate_set": ["A", "B", "C"],
        "pre_target_row_traces": [{"x": 1}],
        "pre_source_row_traces": [{"x": 2}],
        "source_updates": updates,
        "evaluation_cells": cells,
    }


class PairTests(unittest.TestCase):
    def test_computes_adjusted_absolute_and_structural_effects(self) -> None:
        bug = make_result("BUG", shift=0.1)
        gold = make_result("GOLD_ONLY", shift=0.0)
        paired = validator.pair_results(
            bug, gold, expected_stack="TP1-M0-A_TO_B", expected_replicate="A"
        )
        self.assertAlmostEqual(paired["absolute_stack_mean"], 0.1)
        self.assertAlmostEqual(paired["structural_stack_mean"], 0.0)

    def test_identity_specific_bug_increment_produces_positive_D(self) -> None:
        bug = make_result("BUG", shift=0.1)
        gold = make_result("GOLD_ONLY", shift=0.0)
        for cell in bug["evaluation_cells"]:
            if cell["source_rule_identity"] == cell["target_rule_identity"]:
                cell["effect"] += 0.3
        paired = validator.pair_results(
            bug, gold, expected_stack="TP1-M0-A_TO_B", expected_replicate="A"
        )
        self.assertAlmostEqual(paired["absolute_stack_mean"], 0.4)
        self.assertAlmostEqual(paired["structural_stack_mean"], 0.3)

    def test_rejects_cross_arm_config_drift(self) -> None:
        bug = make_result("BUG", shift=0.1)
        gold = make_result("GOLD_ONLY", shift=0.0)
        gold["config_sha256"] = "different"
        with self.assertRaises(validator.R11ValidationError):
            validator.pair_results(
                bug, gold, expected_stack="TP1-M0-A_TO_B", expected_replicate="A"
            )

    def test_rejects_clipped_update(self) -> None:
        result = make_result("BUG", shift=0.1)
        result["source_updates"][0]["clipping_not_triggered"] = False
        with self.assertRaises(validator.R11ValidationError):
            validator.effect_matrix(result, "BUG")

    def test_rejects_missing_cell(self) -> None:
        result = make_result("BUG", shift=0.1)
        result["evaluation_cells"].pop()
        with self.assertRaises(validator.R11ValidationError):
            validator.effect_matrix(result, "BUG")


class SummaryTests(unittest.TestCase):
    def build_paired(self, positive: bool = True) -> dict:
        paired = {}
        for stack in validator.STACKS:
            paired[stack] = {}
            for replicate in validator.REPLICATES:
                matrix = []
                for source in range(5):
                    row = []
                    for target in range(5):
                        base = 0.1 if positive else -0.3
                        row.append(base + (0.2 if source == target else 0.0))
                    matrix.append(row)
                paired[stack][replicate] = {
                    "adjusted_matrix": matrix,
                }
        return paired

    def test_all_positive_gates_pass_development_screen(self) -> None:
        summary = validator.summarize(self.build_paired(True))
        self.assertEqual(
            summary["development_screen_decision"], "DEVELOPMENT_SCREEN_PASS_ONLY"
        )
        self.assertFalse(summary["formal_confirmatory"])

    def test_negative_absolute_gate_is_inconclusive(self) -> None:
        summary = validator.summarize(self.build_paired(False))
        self.assertEqual(
            summary["development_screen_decision"], "INCONCLUSIVE_SIGN_GATE_FAILURE"
        )

    def test_replicate_disagreement_fails(self) -> None:
        paired = self.build_paired(True)
        paired[validator.STACKS[0]]["B"]["adjusted_matrix"][0][0] += 1e-6
        with self.assertRaises(validator.R11ValidationError):
            validator.summarize(paired)


if __name__ == "__main__":
    unittest.main()
