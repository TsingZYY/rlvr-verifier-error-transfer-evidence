from __future__ import annotations

import unittest

import audit_r11_bridge_reuse_eligibility as audit


class RewardInterfaceTests(unittest.TestCase):
    def test_detects_hardcoded_r10_interface(self) -> None:
        source = '''
def update_for_source_rule(model, tokenizer, rows, candidates, offset, *, learning_rate):
    reward[candidate_index[str(row["gold_candidate"])]] = 1.0
    reward[candidate_index[candidate_for_offset(row, offset)]] = 1.0
'''
        result = audit.classify_reward_interface(source)
        self.assertFalse(result["explicit_arm_or_reward_mask_input"])
        self.assertTrue(result["hardcoded_gold_assignment"])
        self.assertTrue(result["hardcoded_wrong_assignment"])
        self.assertFalse(result["can_generate_gold_only_with_same_runner_hash"])

    def test_detects_parameterized_interface(self) -> None:
        source = '''
def update_for_source_rule(model, tokenizer, rows, candidates, offset, *, reward_mask):
    reward[candidate_index[str(row["gold_candidate"])]] = 1.0
'''
        result = audit.classify_reward_interface(source)
        self.assertTrue(result["explicit_arm_or_reward_mask_input"])
        self.assertTrue(result["can_generate_gold_only_with_same_runner_hash"])


class StructureTests(unittest.TestCase):
    def make_result(self) -> dict:
        updates = [
            {
                "source_rule_identity": identity,
                "source_update_hash": f"hash-{identity}",
            }
            for identity in audit.IDENTITIES
        ]
        cells = [
            {
                "source_rule_identity": source,
                "target_rule_identity": target,
                "source_update_hash": f"hash-{source}",
                "same_update_reference": True,
            }
            for source in audit.IDENTITIES
            for target in audit.IDENTITIES
        ]
        return {
            "source_updates": updates,
            "evaluation_cells": cells,
            "unique_source_update_hash_count": 5,
            "diagnostic_gate_status": "PASS",
        }

    def test_accepts_complete_five_by_five_result(self) -> None:
        self.assertEqual(audit.validate_result_structure(self.make_result()), [])

    def test_rejects_missing_target_cell(self) -> None:
        result = self.make_result()
        result["evaluation_cells"].pop()
        failures = audit.validate_result_structure(result)
        self.assertTrue(any("Cartesian product" in failure for failure in failures))

    def test_rejects_update_hash_mismatch(self) -> None:
        result = self.make_result()
        result["evaluation_cells"][0]["source_update_hash"] = "wrong"
        failures = audit.validate_result_structure(result)
        self.assertTrue(any("update hash mismatch" in failure for failure in failures))

    def test_clean_fallback_cost(self) -> None:
        self.assertEqual(
            audit.expected_clean_cost(),
            {
                "new_os_processes": 32,
                "unique_arm_specific_design_cells": 80,
                "technical_update_executions": 160,
                "target_identity_evaluation_cells": 800,
            },
        )


if __name__ == "__main__":
    unittest.main()
