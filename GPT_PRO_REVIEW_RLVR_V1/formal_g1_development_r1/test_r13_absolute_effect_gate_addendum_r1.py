from __future__ import annotations

import copy
import math
import unittest
from pathlib import Path

import r13_absolute_effect_gate_addendum_r1 as gate


HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
ADDENDUM = HERE / "R13_ABSOLUTE_EFFECT_GATE_ADDENDUM_R1.json"


def raw_e(offset: float = 0.0) -> dict[str, dict[int, dict[int, float]]]:
    return {
        arm: {
            r: {
                q: float(100 * arm_index + 10 * r + q) + offset
                for q in gate.TARGET_OFFSETS
            }
            for r in gate.SOURCE_IDENTITIES
        }
        for arm_index, arm in enumerate(gate.ARMS)
    }


def alignments() -> dict[str, dict[int, int]]:
    return {
        gate.ARMS[0]: {r: r for r in gate.SOURCE_IDENTITIES},
        gate.ARMS[1]: {r: 6 - r for r in gate.SOURCE_IDENTITIES},
    }


class R13AbsoluteEffectGateAddendumTests(unittest.TestCase):
    def test_exact_artifact_and_parent_protocol_binding_validate(self) -> None:
        artifact = gate.strict_json(ADDENDUM)
        self.assertEqual(artifact, gate.build_addendum())
        gate.validate_addendum(artifact, protocol_path=PROTOCOL)
        self.assertFalse(artifact["authorization_boundary"]["run_eligible"])
        self.assertEqual(artifact["scope"]["new_model_actions"], 0)

    def test_anchor_formula_selects_raw_E_without_centering(self) -> None:
        anchors = gate.derive_absolute_anchors(raw_e(), alignments())
        for r in gate.SOURCE_IDENTITIES:
            original = float(11 * r)
            aligned = float(100 + 10 * r + (6 - r))
            self.assertEqual(anchors["A_original_by_source_identity"][r], original)
            self.assertEqual(anchors["A_aligned_by_source_identity"][r], aligned)
            self.assertEqual(anchors["D_absolute_by_source_identity"][r], aligned - original)

    def test_A_and_AB_counts_preserve_design_cardinality(self) -> None:
        a = gate.expected_counts(1)
        ab = gate.expected_counts(2)
        self.assertEqual(a["raw_E_cells_before_technical_averaging"], 240)
        self.assertEqual(ab["raw_E_cells_before_technical_averaging"], 480)
        for field in (
            "design_level_original_anchor_cells_after_technical_averaging",
            "design_level_aligned_anchor_cells_after_technical_averaging",
            "design_level_descriptive_difference_cells",
            "stack_by_identity_rows_per_anchor",
        ):
            self.assertEqual(a[field], 20)
            self.assertEqual(ab[field], 20)
        with self.assertRaises(gate.AbsoluteEffectGateError):
            gate.expected_counts(3)

    def test_schema_formula_count_threshold_and_authorization_tampering_fail(self) -> None:
        base = gate.build_addendum()
        mutations = []
        for path, replacement in (
            (("schema_version",), "r13-absolute-effect-gate-addendum-r2"),
            (("absolute_anchor_estimands", "switched_aligned_target_anchor"), "A_aligned=R(...)"),
            (("count_contract", "MINIMAL_4_PROCESS_A", "raw_E_cells_before_technical_averaging"), 239),
            (("threshold_contract", "directional_dead_zone_epsilon"), 0.0),
            (("scope", "new_target_model_reads"), 1),
            (("authorization_boundary", "run_eligible"), True),
            (("interpretation_grid",), []),
        ):
            changed = copy.deepcopy(base)
            cursor = changed
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = replacement
            mutations.append(changed)
        for mutation in mutations:
            with self.assertRaises(gate.AbsoluteEffectGateError):
                gate.validate_addendum(mutation)

    def test_raw_E_coverage_finiteness_and_AB_tolerance_fail_closed(self) -> None:
        a = raw_e()
        averaged = gate.average_technical_raw_e({"A": a})
        self.assertEqual(averaged, a)

        b = raw_e()
        b[gate.ARMS[1]][5][6] += gate.TECHNICAL_TOLERANCE / 2.0
        gate.average_technical_raw_e({"A": a, "B": b})
        b[gate.ARMS[1]][5][6] += gate.TECHNICAL_TOLERANCE * 2.0
        with self.assertRaises(gate.AbsoluteEffectGateError):
            gate.average_technical_raw_e({"A": a, "B": b})

        missing = raw_e()
        del missing[gate.ARMS[0]][1][6]
        with self.assertRaises(gate.AbsoluteEffectGateError):
            gate.validate_raw_e_matrix(missing)
        nonfinite = raw_e()
        nonfinite[gate.ARMS[0]][1][1] = math.nan
        with self.assertRaises(gate.AbsoluteEffectGateError):
            gate.validate_raw_e_matrix(nonfinite)
        with self.assertRaises(gate.AbsoluteEffectGateError):
            gate.average_technical_raw_e({"B": raw_e(), "A": raw_e()})

    def test_interpretation_grid_never_calls_nonpositive_absolute_effect_amplification(self) -> None:
        eps = gate.DIRECTIONAL_EPSILON
        self.assertEqual(
            gate.classify_stack(2 * eps, -1.0),
            "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION",
        )
        self.assertEqual(
            gate.classify_stack(2 * eps, eps / 2.0),
            "RELATIVE_SWITCH_WITH_ABSOLUTE_DEAD_ZONE_NOT_AMPLIFICATION",
        )
        self.assertEqual(
            gate.classify_stack(2 * eps, 2 * eps),
            "RELATIVE_SWITCH_WITH_ABSOLUTE_ALIGNED_WRONG_VS_GOLD_AMPLIFICATION_DEVELOPMENT_ONLY",
        )
        self.assertEqual(
            gate.classify_stack(0.0, 2 * eps),
            "ABSOLUTE_WRONG_VS_GOLD_AMPLIFICATION_WITHOUT_INTERFACE_SWITCH_ATTRIBUTION",
        )


if __name__ == "__main__":
    unittest.main()
