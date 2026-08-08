from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

import analyze_r10_latent_vs_surface_r1 as analysis


class StrictJsonTests(unittest.TestCase):
    def test_duplicate_key_rejected(self) -> None:
        with self.assertRaisesRegex(analysis.DiagnosticError, "duplicate JSON key"):
            analysis.strict_json_bytes(b'{"a":1,"a":2}', "duplicate")

    def test_nonfinite_rejected(self) -> None:
        with self.assertRaisesRegex(analysis.DiagnosticError, "non-finite"):
            analysis.strict_json_bytes(b'{"a":NaN}', "nan")


class AlgebraTests(unittest.TestCase):
    def test_surface_alignment_matches_frozen_affine_competitors(self) -> None:
        self.assertEqual(
            analysis.surface_alignment(1, 2),
            {1: 4, 2: 1, 3: 5, 4: 2, 5: 6},
        )
        self.assertEqual(
            analysis.surface_alignment(2, 1),
            {1: 2, 2: 4, 3: 6, 4: 1, 5: 3},
        )

    def test_double_center_has_zero_row_and_column_means(self) -> None:
        matrix = {
            r: {q: float(10 * r + q * q - r * q) for q in analysis.TARGET_IDENTITIES}
            for r in analysis.SOURCE_IDENTITIES
        }
        residual = analysis.double_center(matrix)
        for r in analysis.SOURCE_IDENTITIES:
            self.assertTrue(
                math.isclose(
                    sum(residual[r].values()) / len(analysis.TARGET_IDENTITIES),
                    0.0,
                    abs_tol=1e-12,
                )
            )
        for q in analysis.TARGET_IDENTITIES:
            self.assertTrue(
                math.isclose(
                    sum(residual[r][q] for r in analysis.SOURCE_IDENTITIES)
                    / len(analysis.SOURCE_IDENTITIES),
                    0.0,
                    abs_tol=1e-12,
                )
            )

    def test_double_center_removes_additive_row_and_column_nuisance(self) -> None:
        interaction = {
            r: {q: float((r * q + q * q) % 11) for q in analysis.TARGET_IDENTITIES}
            for r in analysis.SOURCE_IDENTITIES
        }
        shifted = {
            r: {
                q: interaction[r][q] + 100.0 * r - 7.0 * q + 13.0
                for q in analysis.TARGET_IDENTITIES
            }
            for r in analysis.SOURCE_IDENTITIES
        }
        first = analysis.double_center(interaction)
        second = analysis.double_center(shifted)
        self.assertLessEqual(
            max(
                abs(first[r][q] - second[r][q])
                for r in analysis.SOURCE_IDENTITIES
                for q in analysis.TARGET_IDENTITIES
            ),
            1e-12,
        )

    def test_q6_is_reconstructed_from_bound_codebook(self) -> None:
        row = {
            "canonical_z": 0,
            "codebook": {
                "latent_to_candidate": [
                    "FINAL=K1",
                    "FINAL=K3",
                    "FINAL=K5",
                    "FINAL=K0",
                    "FINAL=K2",
                    "FINAL=K4",
                    "FINAL=K6",
                ]
            },
        }
        self.assertEqual(analysis._target_candidate(row, 6), "FINAL=K6")

    def test_ab_gate_rejects_q6_only_mismatch(self) -> None:
        first = {
            r: {q: 0.0 for q in analysis.TARGET_IDENTITIES}
            for r in analysis.SOURCE_IDENTITIES
        }
        second = copy.deepcopy(first)
        second[3][6] = analysis.AB_TOLERANCE * 2
        with self.assertRaisesRegex(analysis.DiagnosticError, "A/B matrix mismatch"):
            analysis.compare_matrices(first, second, analysis.AB_TOLERANCE)


class ProtocolGateTests(unittest.TestCase):
    def test_source_identity_mutation_rejected(self) -> None:
        protocol_path = Path(__file__).resolve().with_name(
            "R10_LATENT_VS_SURFACE_DIAGNOSTIC_PROTOCOL_R1.json"
        )
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        protocol["inputs"]["source_identities"] = [1, 2, 3, 4]
        with self.assertRaisesRegex(analysis.DiagnosticError, "source identities drift"):
            analysis.validate_protocol(protocol)


class FrozenR10IntegrationTests(unittest.TestCase):
    @staticmethod
    def _paths() -> tuple[Path, Path, Path]:
        project_root = Path(__file__).resolve().parents[1]
        return (
            project_root
            / "authorized_runs"
            / "r10_devcal_20260804T130238Z"
            / "results",
            project_root
            / "real_assets"
            / "build_r4_a"
            / "REAL_MAPPING_STACKS_V1.jsonl",
            project_root
            / "real_assets"
            / "build_r4_a"
            / "TARGET_CALIBRATION_REAL_V1.jsonl",
        )

    def test_duplicate_mapping_stack_rejected(self) -> None:
        _, mapping_path, target_path = self._paths()
        mappings = analysis.read_jsonl(mapping_path)
        target_rows = analysis.read_jsonl(target_path)
        mappings.append(copy.deepcopy(mappings[0]))
        with self.assertRaisesRegex(analysis.DiagnosticError, "duplicate mapping stack"):
            analysis.validate_mapping_and_target_inputs(mappings, target_rows)

    def test_result_mapping_hash_mutation_rejected(self) -> None:
        results_root, mapping_path, target_path = self._paths()
        stack_id = analysis.ORDERED_STACKS[0]
        result_path = analysis._find_stack_directory(results_root, stack_id) / "A" / "result.json"
        result = analysis.read_json(result_path)
        result["mapping_stacks_sha256"] = "0" * 64
        with self.assertRaisesRegex(analysis.DiagnosticError, "mapping input hash drift"):
            analysis.effect_matrix_from_result(
                result,
                analysis.read_jsonl(target_path),
                expected_mapping_sha256=analysis.sha256_file(mapping_path),
                expected_target_sha256=analysis.sha256_file(target_path),
            )

    def test_all_frozen_results_reconstruct_without_model_code(self) -> None:
        results_root, mapping_path, target_path = self._paths()
        result = analysis.run_diagnostic(
            protocol_path=Path(__file__).resolve().with_name(
                "R10_LATENT_VS_SURFACE_DIAGNOSTIC_PROTOCOL_R1.json"
            ),
            results_root=results_root,
            mapping_path=mapping_path,
            target_path=target_path,
        )
        self.assertEqual(len(result["stack_results"]), 8)
        self.assertFalse(result["new_model_execution_performed"])
        self.assertIn(
            result["decision"],
            {
                "LATENT_ALIGNMENT_DEVELOPMENT_SCREEN_PASS_ONLY",
                "STOP_SHARED_IDENTITY_CLAIM_SURFACE_COMPETITOR_NOT_DEFEATED",
            },
        )
        for stack in result["stack_results"].values():
            self.assertLessEqual(
                stack["ab_max_abs_effect_matrix_difference"],
                analysis.AB_TOLERANCE,
            )
        self.assertEqual(
            result["failing_stacks"],
            [
                "TP1-M0-A_TO_B",
                "TP1-M0-B_TO_A",
                "TP2-M0-A_TO_B",
                "TP2-M0-B_TO_A",
            ],
        )


if __name__ == "__main__":
    unittest.main()
