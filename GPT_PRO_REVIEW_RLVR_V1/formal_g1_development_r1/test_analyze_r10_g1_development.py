from __future__ import annotations

import math
import unittest

import analyze_r10_g1_development as analysis


class DevelopmentAnalyzerTests(unittest.TestCase):
    def test_clear_diagonal_is_top_rank(self) -> None:
        matrix = [[10.0 if row == column else 0.0 for column in range(5)] for row in range(5)]
        control = analysis.alignment_control(matrix)
        self.assertEqual(control["rank_descending_among_120"], 1)
        self.assertEqual(control["fraction_permutations_less_or_equal"], 1.0)

    def test_shifted_alignment_rejects_identity_mapping(self) -> None:
        matrix = [[10.0 if column == (row + 1) % 5 else 0.0 for column in range(5)] for row in range(5)]
        control = analysis.alignment_control(matrix)
        self.assertGreater(control["rank_descending_among_120"], 1)
        self.assertEqual(control["observed_identity_matched_mean"], 0.0)

    def test_diagonal_excess_formula(self) -> None:
        matrix = [[float(row * 5 + column) for column in range(5)] for row in range(5)]
        observed = analysis.diagonal_excess_by_identity(matrix)
        expected = []
        for index, row in enumerate(matrix):
            expected.append(row[index] - sum(v for j, v in enumerate(row) if j != index) / 4)
        self.assertEqual(observed, expected)

    def test_non_finite_cell_is_rejected(self) -> None:
        result = {"evaluation_cells": []}
        for row, source in enumerate(analysis.IDENTITIES):
            for column, target in enumerate(analysis.IDENTITIES):
                result["evaluation_cells"].append(
                    {
                        "source_rule_identity": source,
                        "target_rule_identity": target,
                        "effect": math.nan if (row, column) == (0, 0) else 0.0,
                        "same_update_reference": True,
                    }
                )
        with self.assertRaises(analysis.AnalysisError):
            analysis.effect_matrix(result)

    def test_duplicate_cell_is_rejected(self) -> None:
        cells = []
        for source in analysis.IDENTITIES:
            for target in analysis.IDENTITIES:
                cells.append(
                    {
                        "source_rule_identity": source,
                        "target_rule_identity": target,
                        "effect": 0.0,
                        "same_update_reference": True,
                    }
                )
        cells[-1] = dict(cells[0])
        with self.assertRaises(analysis.AnalysisError):
            analysis.effect_matrix({"evaluation_cells": cells})

    def test_same_update_reference_is_required(self) -> None:
        cells = []
        for source in analysis.IDENTITIES:
            for target in analysis.IDENTITIES:
                cells.append(
                    {
                        "source_rule_identity": source,
                        "target_rule_identity": target,
                        "effect": 0.0,
                        "same_update_reference": not (
                            source == analysis.IDENTITIES[0]
                            and target == analysis.IDENTITIES[0]
                        ),
                    }
                )
        with self.assertRaises(analysis.AnalysisError):
            analysis.effect_matrix({"evaluation_cells": cells})


if __name__ == "__main__":
    unittest.main()
