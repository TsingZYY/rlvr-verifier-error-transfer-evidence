from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from formal_g1_development_r1 import (  # noqa: E402
    validate_r13_local_development_results_r1 as local_validator,
)
import test_validate_r13_target_alignment_results_r1 as core_fixture  # noqa: E402


SEMANTICS_PATH = HERE / "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json"
ABSOLUTE_PATH = HERE / "R13_ABSOLUTE_EFFECT_GATE_ADDENDUM_R1.json"
PROTOCOL_PATH = HERE / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"


class R13LocalDevelopmentValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = core_fixture.build_fixture()
        cls.execution_semantics = local_validator.strict_json(SEMANTICS_PATH)

    def fresh_bundle(self) -> dict:
        result = copy.deepcopy(self.fixture["result"])
        return {
            "schema_version": local_validator.SCHEMA_VERSION,
            "status": local_validator.STATUS,
            "run_id": result["run_id"],
            "created_at_utc": result["created_at_utc"],
            "selected_variant": local_validator.SELECTED_VARIANT,
            "evidence_boundary": local_validator.EVIDENCE_LABEL,
            "scientific_evidence": False,
            "formal_experiment": False,
            "formal_confirmatory": False,
            "sampled_rlvr": False,
            "same_empirical_or_policy_fpr": False,
            "model_execution_performed": True,
            "intermediate_scientific_results_released": False,
            "manifest_sha256": "",
            "bindings": result["bindings"],
            "ordered_process_ids": result["ordered_process_ids"],
            "counts": result["counts"],
            "model_action_counts": copy.deepcopy(
                local_validator.EXPECTED_MODEL_ACTION_COUNTS
            ),
            "events": result["events"],
            "process_results": result["process_results"],
        }

    def manifest_for(self, bundle: dict) -> dict:
        return {
            "run_id": bundle["run_id"],
            "frozen_at_utc": "2025-12-31T00:00:00Z",
            "artifacts": {
                "protocol": {"path": str(PROTOCOL_PATH.relative_to(PROJECT))},
                "matched_panel_allowlist": {"path": "fixture_allowlist.json"},
                "local_execution_semantics_addendum": {
                    "path": str(SEMANTICS_PATH.relative_to(PROJECT)),
                    "sha256": local_validator.sha256_file(SEMANTICS_PATH),
                },
                "absolute_effect_addendum": {
                    "path": str(ABSOLUTE_PATH.relative_to(PROJECT)),
                    "sha256": local_validator.sha256_file(ABSOLUTE_PATH),
                },
            },
        }

    def validate(
        self,
        bundle: dict,
        *,
        manifest_verification_error: Exception | None = None,
    ) -> dict:
        manifest = self.manifest_for(bundle)
        loaded = SimpleNamespace(
            records=tuple(self.fixture["records"]),
            allowlist_receipt=copy.deepcopy(self.fixture["allowlist"]),
        )
        original_strict_json = local_validator.strict_json

        def strict_json_fixture(path: Path) -> dict:
            if path.name == "fixture_allowlist.json":
                return copy.deepcopy(self.fixture["allowlist"])
            return original_strict_json(path)

        with tempfile.TemporaryDirectory(dir=PROJECT) as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_bytes(b"synthetic local-validator test manifest\n")
            bundle["manifest_sha256"] = local_validator.sha256_file(manifest_path)
            with (
                mock.patch.object(
                    local_validator.local_manifest,
                    "read_manifest",
                    return_value=manifest,
                ),
                mock.patch.object(
                    local_validator.local_manifest,
                    "validate_manifest",
                    return_value=None,
                ),
                mock.patch.object(
                    local_validator.local_manifest,
                    "verify_manifest_against_filesystem",
                    side_effect=manifest_verification_error,
                ),
                mock.patch.object(
                    local_validator,
                    "expected_bindings",
                    return_value=copy.deepcopy(self.fixture["bindings"]),
                ),
                mock.patch.object(
                    local_validator,
                    "strict_json",
                    side_effect=strict_json_fixture,
                ),
                mock.patch.object(
                    local_validator.core,
                    "load_bound_matched_panel_bundle",
                    return_value=loaded,
                ),
            ):
                return local_validator.validate_local_bundle(
                    bundle,
                    project_root=PROJECT,
                    manifest_path=manifest_path,
                )

    def test_full_eight_process_ab_bundle_closes_actions_and_absolute_gate(self) -> None:
        report = self.validate(self.fresh_bundle())
        self.assertEqual(report["decision"], local_validator.JOINT_PASS_LABEL)
        self.assertTrue(
            report["relative_interface_report"]["a_b_checked_before_averaging"]
        )
        self.assertEqual(
            report["absolute_effect_report"][
                "raw_E_cells_before_technical_averaging"
            ],
            480,
        )
        self.assertTrue(
            report["absolute_effect_report"]["all_four_stack_absolute_gate_pass"]
        )
        self.assertEqual(
            report["shape_derived_expected_model_action_counts"],
            local_validator.EXPECTED_MODEL_ACTION_COUNTS,
        )
        self.assertTrue(report["reported_model_action_counts_match_shape"])
        self.assertFalse(report["model_action_counts_are_external_telemetry"])

    def test_action_counts_are_derived_from_process_shapes(self) -> None:
        counts = local_validator.derive_model_action_counts(
            copy.deepcopy(self.fixture["result"]["process_results"]),
            copy.deepcopy(self.execution_semantics),
        )
        self.assertEqual(counts, local_validator.EXPECTED_MODEL_ACTION_COUNTS)

        bundle = self.fresh_bundle()
        bundle["model_action_counts"]["model_forward_calls"] += 1
        with self.assertRaisesRegex(
            local_validator.R13LocalValidationError,
            "reported model action counts do not match validated process shapes",
        ):
            self.validate(bundle)

    def test_execution_semantics_tamper_is_rejected(self) -> None:
        semantics = copy.deepcopy(self.execution_semantics)
        semantics["gradient_accumulation_resolution"]["backward_calls"] = 13
        with self.assertRaisesRegex(
            local_validator.R13LocalValidationError,
            "gradient accumulation semantics drift",
        ):
            local_validator.derive_model_action_counts(
                copy.deepcopy(self.fixture["result"]["process_results"]),
                semantics,
            )

    def test_b_arm_order_tamper_fails_closed(self) -> None:
        bundle = self.fresh_bundle()
        process_b = next(
            process
            for process in bundle["process_results"]
            if process["replicate_id"] == "B"
        )
        process_b["arm_evaluation_order"] = list(
            local_validator.core.REPLICATE_ARM_ORDER["A"]
        )
        with self.assertRaisesRegex(Exception, "target-arm order drift"):
            self.validate(bundle)

    def test_ab_raw_trace_tamper_fails_before_averaging(self) -> None:
        bundle = self.fresh_bundle()
        process_b = next(
            process
            for process in bundle["process_results"]
            if process["stack_id"] == local_validator.core.STACKS[0]
            and process["replicate_id"] == "B"
        )
        process_b["source_update_executions"][0]["readouts"][0]["row_traces"][0][
            "ordered_candidate_scores"
        ][0] += 1e-3
        with self.assertRaisesRegex(Exception, "A/B E matrix mismatch"):
            self.validate(bundle)

    def test_manifest_artifact_verification_failure_is_not_swallowed(self) -> None:
        failure = local_validator.local_manifest.R13LocalManifestError(
            "artifact hash mismatch: protocol"
        )
        with self.assertRaisesRegex(
            local_validator.local_manifest.R13LocalManifestError,
            "artifact hash mismatch",
        ):
            self.validate(
                self.fresh_bundle(),
                manifest_verification_error=failure,
            )

    def test_absolute_gate_can_fail_despite_positive_relative_f(self) -> None:
        protocol = copy.deepcopy(self.fixture["protocol"])
        derived: dict[str, dict[str, dict]] = {}
        for stack in local_validator.core.STACKS:
            derived[stack] = {}
            for replicate in ("A", "B"):
                derived[stack][replicate] = {
                    "E": {
                        arm: {
                            source_identity: {
                                offset: 0.0
                                for offset in local_validator.core.TARGET_OFFSETS
                            }
                            for source_identity in range(1, 6)
                        }
                        for arm in local_validator.core.ARMS
                    },
                    "F": {source_identity: 1.0 for source_identity in range(1, 6)},
                }
        report = local_validator._absolute_report(
            derived,
            protocol,
            addendum_sha256="0" * 64,
            expected_raw_e_cells=480,
        )
        self.assertFalse(report["all_four_stack_absolute_gate_pass"])
        self.assertTrue(
            all(
                item["classification"]
                == "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION"
                for item in report["by_stack"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
