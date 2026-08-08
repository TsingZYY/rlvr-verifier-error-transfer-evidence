from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


PROTOCOL_PATH = Path(__file__).resolve().with_name(
    "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
)
EXPECTED_STACKS = [
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
]


def load_protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def parse_affine(formula: str) -> tuple[int, int]:
    match = re.fullmatch(r"K\(\((\d+)\*z\+(\d+)\) mod 7\)", formula)
    if match is None:
        raise AssertionError(f"non-canonical affine formula: {formula}")
    return int(match.group(1)), int(match.group(2))


class R13ProtocolTests(unittest.TestCase):
    def test_draft_is_fail_closed_and_grants_no_model_action(self) -> None:
        protocol = load_protocol()
        self.assertFalse(protocol["run_eligible"])
        self.assertFalse(protocol["model_execution_authorized"])
        self.assertFalse(protocol["model_execution_performed"])
        self.assertFalse(protocol["scientific_evidence"])
        authorization = protocol["authorization"]
        self.assertTrue(authorization["cpu_static_design_and_tests"])
        for operation in (
            "tokenizer_load",
            "model_weight_load",
            "model_forward",
            "gradient",
            "optimizer_step",
            "run_r13_target_alignment_pilot",
        ):
            self.assertFalse(authorization[operation])

    def test_all_and_only_failed_m0_stacks_are_selected(self) -> None:
        protocol = load_protocol()
        self.assertEqual(
            protocol["fixed_source_stacks"]["ordered_stack_ids"],
            EXPECTED_STACKS,
        )
        self.assertEqual(protocol["fixed_source_stacks"]["top_level_scientific_clusters"], 2)

    def test_surface_tables_follow_codebook_algebra_and_are_distinct(self) -> None:
        protocol = load_protocol()
        arms = protocol["target_alignment_intervention"]
        h0 = arms["H0_ORIGINAL_M0_TARGET"]
        h1 = arms["H1_SWITCHED_TARGET"]
        for direction in ("A_TO_B", "B_TO_A"):
            alignments: dict[str, dict[int, int]] = {}
            for arm_name, arm in (("H0", h0), ("H1", h1)):
                source_multiplier, _ = parse_affine(arm[direction]["source_codebook"])
                target_multiplier, _ = parse_affine(arm[direction]["target_codebook"])
                factor = (pow(target_multiplier, -1, 7) * source_multiplier) % 7
                self.assertEqual(factor, arm[direction]["surface_factor"])
                expected = {r: (factor * r) % 7 for r in range(1, 6)}
                recorded = {
                    int(r): int(q)
                    for r, q in arm[direction]["q_surface_by_r"].items()
                }
                self.assertEqual(recorded, expected)
                alignments[arm_name] = recorded
            for r in range(1, 6):
                self.assertEqual(
                    len({r, alignments["H0"][r], alignments["H1"][r]}),
                    3,
                )
                self.assertNotEqual(alignments["H0"][r], 0)
                self.assertNotEqual(alignments["H1"][r], 0)

    def test_minimal_and_ab_counts_close_exactly(self) -> None:
        variants = load_protocol()["execution_variants"]
        minimal = variants["MINIMAL_4_PROCESS_A"]
        self.assertEqual(minimal["os_processes"], 4)
        self.assertEqual(minimal["unique_design_source_updates"], 4 * 5)
        self.assertEqual(minimal["technical_update_executions"], 20)
        self.assertEqual(minimal["pre_target_identity_reads"], 4 * 2 * 6)
        self.assertEqual(minimal["post_target_identity_reads"], 4 * 5 * 2 * 6)
        self.assertEqual(
            minimal["total_target_identity_metric_cells"],
            minimal["pre_target_identity_reads"]
            + minimal["post_target_identity_reads"],
        )

        ab = variants["REPRODUCIBILITY_8_PROCESS_AB"]
        self.assertEqual(ab["os_processes"], 8)
        self.assertEqual(ab["unique_design_source_updates"], 20)
        self.assertEqual(ab["technical_update_executions"], 40)
        self.assertEqual(ab["pre_target_identity_reads"], 8 * 2 * 6)
        self.assertEqual(ab["post_target_identity_reads"], 8 * 5 * 2 * 6)
        self.assertEqual(
            ab["total_target_identity_metric_cells"],
            ab["pre_target_identity_reads"] + ab["post_target_identity_reads"],
        )
        self.assertFalse(ab["replicates_are_scientific_samples"])

    def test_same_update_is_reused_across_target_arms(self) -> None:
        protocol = load_protocol()
        update = protocol["fixed_source_update_contract"]
        self.assertIn("compute U exactly once", update["same_update_for_both_target_arms"])
        self.assertIn("Neither target-panel arm", update["target_blindness"])
        self.assertEqual(update["gold_control"].split(".", 1)[0], "NOT_INCLUDED")
        self.assertIn(
            "same source-update parameter hash",
            " ".join(protocol["result_validator_minimum_requirements"]),
        )

    def test_claim_boundary_does_not_resurrect_old_mechanism(self) -> None:
        protocol = load_protocol()
        self.assertIn(
            "causal proof of shared semantic identity",
            protocol["claim_boundary"]["cannot_support"],
        )
        self.assertIn(
            "cannot turn the already failed uniform R10",
            protocol["development_decision_rule"]["no_latent_claim_resurrection"],
        )


if __name__ == "__main__":
    unittest.main()
