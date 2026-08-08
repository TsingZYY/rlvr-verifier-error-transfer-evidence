from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import uuid

import r13_local_development_manifest as contract


class R13LocalDevelopmentManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.project = self.workspace / contract.PROJECT_DIR_NAME
        self.source_project = Path(__file__).resolve().parents[1]
        for role, relpath in contract.ARTIFACT_RELATIVE_PATHS.items():
            destination = self.project / Path(*Path(relpath).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if role == "real_backend":
                destination.write_text(
                    "# synthetic unit-test backend placeholder; never imported\n"
                    "SCHEMA_VERSION = 'r13-real-model-backend-r1'\n"
                    "REAL_MODEL_BACKEND_IMPLEMENTED = True\n",
                    encoding="utf-8",
                )
            else:
                shutil.copy2(self.source_project / relpath, destination)
        python_path = self.workspace / Path(
            *Path(contract.PYTHON_RELPATH_FROM_WORKSPACE).parts
        )
        python_path.parent.mkdir(parents=True, exist_ok=True)
        python_path.write_bytes(b"synthetic unit-test python launcher\n")
        (self.project / "authorized_runs").mkdir(parents=True, exist_ok=True)
        self.run_id = str(uuid.UUID("11111111-2222-4333-8444-555555555555"))
        self.manifest_path = (
            self.project
            / "formal_g1_development_r1"
            / f"R13_LOCAL_DEVELOPMENT_EXECUTION_MANIFEST_AB_{self.run_id}.json"
        )
        self.output_root = (
            self.project / "authorized_runs" / f"r13_local_dev_ab_{self.run_id}"
        )
        self.frozen_at = "2026-08-06T04:00:00Z"
        self.canary_receipt = (
            self.project
            / "formal_g1_development_r1"
            / "r13_local_canary_receipts"
            / f"CANARY_{self.run_id}.json"
        )
        self.canary_receipt.parent.mkdir(parents=True, exist_ok=True)
        self.canary_value = self._valid_canary_receipt()
        self._write_canary_receipt(self.canary_value)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict:
        return contract.build_manifest(
            project_root=self.project,
            manifest_path=self.manifest_path,
            output_root=self.output_root,
            canary_receipt_path=self.canary_receipt,
            run_id=self.run_id,
            frozen_at_utc=self.frozen_at,
        )

    def _valid_canary_receipt(self) -> dict:
        return {
            "schema_version": contract.CANARY_RECEIPT_SCHEMA,
            "status": contract.CANARY_RECEIPT_STATUS,
            "created_at_utc": "2026-08-06T03:45:00Z",
            "canary_spec_sha256": contract.EXPECTED_LOCAL_TECHNICAL_CANARY_SPEC_SHA256,
            "backend_sha256": contract.sha256_file(
                self.project / contract.FUTURE_REAL_BACKEND_RELPATH
            ),
            "model_inventory_sha256": contract.sha256_file(
                self.project
                / contract.ARTIFACT_RELATIVE_PATHS["model_inventory"]
            ),
            "runtime_reference_sha256": contract.sha256_file(
                self.project
                / contract.ARTIFACT_RELATIVE_PATHS["runtime_reference"]
            ),
            "dependency_versions": {
                "torch": "test-only",
                "transformers": "test-only",
                "peft": "test-only",
            },
            "device_name": "synthetic-unit-test-device",
            "device_capability": [8, 9],
            "bf16_supported": True,
            "trainable_parameter_count": 1024,
            "candidate_supervised_token_count_min": 2,
            "candidate_supervised_token_count_max": 2,
            "all_candidate_scores_finite": True,
            "all_gradients_finite": True,
            "raw_gradient_norm": 0.5,
            "raw_gradient_norm_threshold_pass": True,
            "parameter_changed_after_update": True,
            "target_read_hash_preserved": True,
            "reset_hash_restored": True,
            "peak_gpu_memory_bytes": 1,
            "elapsed_seconds": 1.0,
            "scientific_source_cells": 0,
            "scientific_target_cells": 0,
            "model_action_counts": {
                "tokenizer_loads": 1,
                "model_weight_loads": 1,
                "model_forward_calls": 16,
                "backward_calls": 14,
                "manual_parameter_steps": 1,
            },
            "evidence_boundary": contract.CANARY_EVIDENCE_BOUNDARY,
        }

    def _write_canary_receipt(self, value: dict) -> None:
        self.canary_receipt.write_bytes(contract.canonical_json_bytes(value) + b"\n")

    def test_build_freezes_exact_eight_process_ab_scope(self) -> None:
        manifest = self.build()
        self.assertEqual(manifest["selected_variant"], contract.SELECTED_VARIANT)
        self.assertEqual(manifest["ordered_process_ids"], list(contract.ORDERED_PROCESS_IDS))
        self.assertEqual(len(manifest["ordered_process_ids"]), 8)
        self.assertEqual(manifest["process_plan"]["counts"], contract.EXPECTED_COUNTS)
        self.assertEqual(
            manifest["process_plan"]["arm_evaluation_order_by_replicate"],
            contract.ARM_ORDER_BY_REPLICATE,
        )

    def test_local_authorization_review_and_custody_are_honest(self) -> None:
        manifest = self.build()
        self.assertTrue(manifest["user_authorization"]["authorized"])
        self.assertEqual(
            manifest["review_status"]["review_level"],
            "independent_agent_and_machine_review_only",
        )
        self.assertFalse(manifest["review_status"]["human_review_completed"])
        self.assertEqual(
            manifest["evidence_boundary"]["label"], contract.EVIDENCE_LABEL
        )
        self.assertFalse(manifest["evidence_boundary"]["paper_custody"])
        self.assertFalse(manifest["evidence_boundary"]["production_custody"])
        self.assertFalse(manifest["model_execution_performed"])
        self.assertEqual(manifest["model_action_count_at_manifest_build"], 0)

    def test_sealed_batch_stop_and_no_adaptive_rerun_are_frozen(self) -> None:
        execution = self.build()["execution"]
        self.assertTrue(execution["sealed_batch"])
        self.assertFalse(execution["intermediate_scientific_result_release_allowed"])
        self.assertFalse(execution["result_dependent_change_to_later_cells_allowed"])
        self.assertFalse(execution["adaptive_rerun_allowed"])
        self.assertFalse(execution["same_manifest_retry_allowed"])
        self.assertEqual(
            execution["failure_policy"],
            "STOP_ENTIRE_BATCH_NO_SCIENTIFIC_INTERPRETATION",
        )
        self.assertIn("NEW_PREFROZEN_MANIFEST", execution["retry_policy"])

    def test_every_required_artifact_is_actual_hash_bound(self) -> None:
        manifest = self.build()
        self.assertEqual(
            set(manifest["artifacts"]), contract.ALL_ARTIFACT_ROLES
        )
        for role, record in manifest["artifacts"].items():
            path = self.project / Path(*Path(record["path"]).parts)
            with self.subTest(role=role):
                self.assertEqual(contract.sha256_file(path), record["sha256"])
                self.assertEqual(path.stat().st_size, record["byte_length"])

    def test_execution_semantics_addendum_is_exactly_hash_bound(self) -> None:
        manifest = self.build()
        record = manifest["artifacts"]["local_execution_semantics_addendum"]
        self.assertEqual(record["path"], contract.LOCAL_EXECUTION_SEMANTICS_RELPATH)
        self.assertEqual(
            record["sha256"], contract.EXPECTED_LOCAL_EXECUTION_SEMANTICS_SHA256
        )
        changed = copy.deepcopy(manifest)
        changed["artifacts"]["local_execution_semantics_addendum"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "local execution-semantics addendum hash drift",
        ):
            contract.validate_manifest(changed)

    def test_execution_semantics_variant_backward_norm_and_evidence_drift_fail(self) -> None:
        path = self.project / contract.LOCAL_EXECUTION_SEMANTICS_RELPATH
        original = path.read_bytes()
        base = json.loads(original.decode("utf-8"))
        mutations = []
        changed = copy.deepcopy(base)
        changed["selected_execution_scope"]["variant"] = "MINIMAL_4_PROCESS_A"
        mutations.append((changed, "eight-process A/B scope drift"))
        changed = copy.deepcopy(base)
        changed["gradient_accumulation_resolution"]["backward_calls"] = 13
        mutations.append((changed, "14-backward contract drift"))
        changed = copy.deepcopy(base)
        changed["gradient_norm_and_clipping_resolution"][
            "if_raw_norm_greater_than_threshold"
        ] = "CLIP_AND_CONTINUE"
        mutations.append((changed, "pre-step raw-gradient-norm stop gate drift"))
        changed = copy.deepcopy(base)
        changed["authorization_and_evidence_boundary"]["paper_custody"] = True
        mutations.append((changed, "evidence boundary drift"))
        for value, message in mutations:
            with self.subTest(message=message):
                path.write_bytes(contract.canonical_json_bytes(value) + b"\n")
                changed_sha = contract.sha256_file(path)
                with mock.patch.object(
                    contract,
                    "EXPECTED_LOCAL_EXECUTION_SEMANTICS_SHA256",
                    changed_sha,
                ):
                    with self.assertRaisesRegex(
                        contract.R13LocalManifestError, message
                    ):
                        self.build()
                path.write_bytes(original)

    def test_missing_future_real_backend_fails_closed(self) -> None:
        (self.project / contract.FUTURE_REAL_BACKEND_RELPATH).unlink()
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            r"BLOCKED_MISSING_ARTIFACT\[real_backend\]",
        ):
            self.build()

    def test_missing_or_external_canary_receipt_fails_closed(self) -> None:
        self.canary_receipt.unlink()
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            r"BLOCKED_MISSING_ARTIFACT\[canary_receipt\]",
        ):
            self.build()

        external_receipt = self.workspace / "CANARY_EXTERNAL.json"
        external_receipt.write_bytes(
            contract.canonical_json_bytes(self.canary_value) + b"\n"
        )
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "canary receipt must be beneath workspace root",
        ):
            contract.build_manifest(
                project_root=self.project,
                manifest_path=self.manifest_path,
                output_root=self.output_root,
                canary_receipt_path=external_receipt,
                run_id=self.run_id,
                frozen_at_utc=self.frozen_at,
            )

    def test_canary_status_scientific_cells_forbidden_fields_and_time_fail(self) -> None:
        mutations = []
        changed = copy.deepcopy(self.canary_value)
        changed["status"] = "FAIL_LOCAL_ENGINEERING_CANARY"
        mutations.append((changed, "technical-canary did not pass"))
        changed = copy.deepcopy(self.canary_value)
        changed["scientific_source_cells"] = 1
        mutations.append((changed, "technical-canary touched a scientific cell"))
        changed = copy.deepcopy(self.canary_value)
        changed["ordered_candidate_scores"] = [0.0]
        mutations.append((changed, "field coverage drift or forbidden field present"))
        changed = copy.deepcopy(self.canary_value)
        changed["created_at_utc"] = self.frozen_at
        mutations.append((changed, "technical-canary receipt must predate manifest freeze"))
        for value, message in mutations:
            with self.subTest(message=message):
                self._write_canary_receipt(value)
                with self.assertRaisesRegex(contract.R13LocalManifestError, message):
                    self.build()

    def test_canary_rfc3339_fractional_seconds_are_accepted(self) -> None:
        changed = copy.deepcopy(self.canary_value)
        changed["created_at_utc"] = "2026-08-06T03:45:00.123456Z"
        self._write_canary_receipt(changed)
        manifest = self.build()
        self.assertEqual(
            manifest["artifacts"]["canary_receipt"]["sha256"],
            contract.sha256_file(self.canary_receipt),
        )

    def test_canary_bound_hashes_and_model_action_counts_must_match(self) -> None:
        for field in (
            "canary_spec_sha256",
            "backend_sha256",
            "model_inventory_sha256",
            "runtime_reference_sha256",
        ):
            changed = copy.deepcopy(self.canary_value)
            changed[field] = "0" * 64
            with self.subTest(field=field):
                self._write_canary_receipt(changed)
                with self.assertRaisesRegex(
                    contract.R13LocalManifestError,
                    f"technical-canary hash mismatch: {field}",
                ):
                    self.build()

        changed = copy.deepcopy(self.canary_value)
        changed["model_action_counts"]["model_forward_calls"] = 15
        self._write_canary_receipt(changed)
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "technical-canary model action count drift",
        ):
            self.build()

    def test_canary_finite_norm_parameter_readonly_and_reset_gates_must_pass(self) -> None:
        mutations = (
            ("all_candidate_scores_finite", False, "finite-score gate failed"),
            ("all_gradients_finite", False, "finite-gradient gate failed"),
            ("raw_gradient_norm", 1.01, "raw-gradient-norm gate failed"),
            (
                "raw_gradient_norm_threshold_pass",
                False,
                "raw-gradient threshold marker failed",
            ),
            (
                "parameter_changed_after_update",
                False,
                "parameter-change gate failed",
            ),
            (
                "target_read_hash_preserved",
                False,
                "read-only hash gate failed",
            ),
            ("reset_hash_restored", False, "reset-hash gate failed"),
        )
        for field, value, message in mutations:
            changed = copy.deepcopy(self.canary_value)
            changed[field] = value
            with self.subTest(field=field):
                self._write_canary_receipt(changed)
                with self.assertRaisesRegex(contract.R13LocalManifestError, message):
                    self.build()

    def test_canary_receipt_must_be_strict_json(self) -> None:
        self.canary_receipt.write_text(
            '{"status":"PASS_LOCAL_ENGINEERING_CANARY",'
            '"status":"PASS_LOCAL_ENGINEERING_CANARY"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(contract.R13LocalManifestError, "duplicate JSON key"):
            self.build()

        self.canary_receipt.write_text('{"raw_gradient_norm":NaN}\n', encoding="utf-8")
        with self.assertRaisesRegex(contract.R13LocalManifestError, "non-finite"):
            self.build()

    def test_missing_or_empty_any_bound_artifact_fails_closed(self) -> None:
        for role in (
            "protocol",
            "source_bundle",
            "model_inventory",
            "real_backend",
            "batch_runner",
            "local_result_validator",
            "runtime_bootstrap",
            "runtime_preflight_receipt",
            "result_validator",
        ):
            with self.subTest(role=role):
                with tempfile.TemporaryDirectory() as isolated:
                    root = Path(isolated) / contract.PROJECT_DIR_NAME
                    shutil.copytree(self.project, root)
                    target = root / contract.ARTIFACT_RELATIVE_PATHS[role]
                    target.write_bytes(b"")
                    with self.assertRaisesRegex(
                        contract.R13LocalManifestError,
                        f"BLOCKED_EMPTY_ARTIFACT\\[{role}\\]",
                    ):
                        contract.build_manifest(
                            project_root=root,
                            manifest_path=root / "formal_g1_development_r1" / "m.json",
                            output_root=root / "authorized_runs" / "r13_local_dev_ab_empty",
                            canary_receipt_path=(
                                root
                                / "formal_g1_development_r1"
                                / "r13_local_canary_receipts"
                                / f"CANARY_{self.run_id}.json"
                            ),
                            run_id=self.run_id,
                            frozen_at_utc=self.frozen_at,
                        )

    def test_preexisting_output_root_or_manifest_is_rejected(self) -> None:
        self.output_root.mkdir()
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "BLOCKED_OUTPUT_ROOT_ALREADY_EXISTS"
        ):
            self.build()
        self.output_root.rmdir()
        self.manifest_path.write_bytes(b"existing")
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "BLOCKED_MANIFEST_OUTPUT_ALREADY_EXISTS",
        ):
            self.build()

    def test_selected_variant_or_process_order_mutation_is_rejected(self) -> None:
        manifest = self.build()
        changed = copy.deepcopy(manifest)
        changed["selected_variant"] = "MINIMAL_4_PROCESS_A"
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "eight-process A/B"
        ):
            contract.validate_manifest(changed)
        changed = copy.deepcopy(manifest)
        changed["ordered_process_ids"] = changed["ordered_process_ids"][:4]
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "ordered eight-process plan drift"
        ):
            contract.validate_manifest(changed)

    def test_seed_seal_retry_or_release_mutation_is_rejected(self) -> None:
        manifest = self.build()
        mutations = (
            ("seed", 7),
            ("sealed_batch", False),
            ("intermediate_scientific_result_release_allowed", True),
            ("result_dependent_change_to_later_cells_allowed", True),
            ("adaptive_rerun_allowed", True),
            ("same_manifest_retry_allowed", True),
            ("failure_policy", "CONTINUE"),
            ("automatic_progression", True),
        )
        for field, value in mutations:
            changed = copy.deepcopy(manifest)
            changed["execution"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(contract.R13LocalManifestError):
                    contract.validate_manifest(changed)

    def test_human_review_or_custody_laundering_is_rejected(self) -> None:
        manifest = self.build()
        changed = copy.deepcopy(manifest)
        changed["review_status"]["human_review_completed"] = True
        with self.assertRaisesRegex(contract.R13LocalManifestError, "review boundary drift"):
            contract.validate_manifest(changed)
        for field in ("paper_custody", "production_custody"):
            changed = copy.deepcopy(manifest)
            changed["evidence_boundary"][field] = True
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    contract.R13LocalManifestError,
                    "local development evidence boundary drift",
                ):
                    contract.validate_manifest(changed)

    def test_artifact_tamper_after_freeze_is_rejected(self) -> None:
        manifest = self.build()
        source = self.project / contract.ARTIFACT_RELATIVE_PATHS["source_bundle"]
        with source.open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "artifact byte-length mismatch: source_bundle"
        ):
            contract.verify_manifest_against_filesystem(
                manifest,
                project_root=self.project,
                require_output_root_absent=True,
            )

    def test_command_is_exact_workspace_root_batch_coordinator_and_manifest(self) -> None:
        manifest = self.build()
        command = manifest["execution"]["command"]
        self.assertEqual(
            command,
            [
                contract.PYTHON_RELPATH_FROM_WORKSPACE,
                "-I",
                "-B",
                f"{contract.PROJECT_DIR_NAME}/{contract.LOCAL_BATCH_RUNNER_RELPATH}",
                "coordinate",
                "--manifest",
                str(self.manifest_path.relative_to(self.workspace)).replace("\\", "/"),
            ],
        )
        mutations = {
            0: "python.exe",
            1: "-E",
            2: "-O",
            3: "unbound_batch_runner.py",
            4: "worker",
            5: "--other-manifest",
            6: "unbound_manifest.json",
        }
        for index, value in mutations.items():
            changed = copy.deepcopy(manifest)
            changed["execution"]["command"][index] = value
            with self.subTest(index=index):
                with self.assertRaisesRegex(
                    contract.R13LocalManifestError, "execution command drift"
                ):
                    contract.verify_manifest_against_filesystem(
                        changed,
                        project_root=self.project,
                        require_output_root_absent=True,
                    )

    def test_new_execution_closure_artifact_tamper_after_freeze_is_rejected(self) -> None:
        critical_roles = (
            "real_backend",
            "batch_runner",
            "manifest_builder",
            "local_result_validator",
            "runtime_bootstrap",
            "runtime_preflight_receipt",
            "result_validator",
            "local_technical_canary_spec",
            "canary_receipt",
        )
        for role in critical_roles:
            manifest = self.build()
            record = manifest["artifacts"][role]
            selected = self.project / Path(*Path(record["path"]).parts)
            original = selected.read_bytes()
            try:
                selected.write_bytes(original + b"tamper")
                with self.subTest(role=role):
                    with self.assertRaisesRegex(
                        contract.R13LocalManifestError,
                        f"artifact byte-length mismatch: {role}",
                    ):
                        contract.verify_manifest_against_filesystem(
                            manifest,
                            project_root=self.project,
                            require_output_root_absent=True,
                        )
            finally:
                selected.write_bytes(original)

    def test_runtime_preflight_receipt_and_bootstrap_cross_binding_fail_closed(self) -> None:
        receipt_path = self.project / contract.LOCAL_RUNTIME_PREFLIGHT_RECEIPT_RELPATH
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw.decode("ascii"))
        changed = copy.deepcopy(receipt)
        changed["model_action_count"] = 1
        receipt_path.write_bytes(
            contract.runtime_bootstrap_contract.canonical_json_bytes(changed)
        )
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "invalid runtime-preflight receipt: model action count drift",
        ):
            self.build()
        receipt_path.write_bytes(receipt_raw)

        changed = copy.deepcopy(receipt)
        changed["created_at_utc"] = "2026-08-06T04:00:01+00:00"
        receipt_path.write_bytes(
            contract.runtime_bootstrap_contract.canonical_json_bytes(changed)
        )
        with self.assertRaisesRegex(
            contract.R13LocalManifestError,
            "runtime-preflight receipt must predate manifest freeze",
        ):
            self.build()
        receipt_path.write_bytes(receipt_raw)

        bootstrap_path = self.project / contract.LOCAL_RUNTIME_BOOTSTRAP_RELPATH
        bootstrap_raw = bootstrap_path.read_bytes()
        try:
            bootstrap_path.write_bytes(bootstrap_raw + b"# drift\n")
            with self.assertRaisesRegex(
                contract.R13LocalManifestError,
                "runtime-preflight/bootstrap artifact binding mismatch",
            ):
                self.build()
        finally:
            bootstrap_path.write_bytes(bootstrap_raw)

    def test_preflight_preserves_hybrid_nonproduction_boundary_and_full_closure(self) -> None:
        preflight = self.build()["preflight"]
        for field in (
            "all_required_artifacts_exist_and_are_hash_bound",
            "real_backend_exists_and_is_hash_bound",
            "batch_runner_exists_and_is_hash_bound",
            "local_result_validator_exists_and_is_hash_bound",
            "official_core_validator_exists_and_is_hash_bound",
            "runtime_bootstrap_and_preflight_receipt_hash_bound",
            "technical_canary_receipt_passed_and_hash_bound",
        ):
            self.assertTrue(preflight[field])
        self.assertFalse(preflight["runtime_full_dependency_content_hash_bound"])
        self.assertFalse(preflight["human_review_completed"])
        self.assertFalse(preflight["paper_custody"])
        self.assertFalse(preflight["production_custody"])

    def test_output_binding_and_overwrite_policy_are_enforced(self) -> None:
        manifest = self.build()
        changed = copy.deepcopy(manifest)
        changed["output"]["root"] += "_changed"
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "output binding mismatch"
        ):
            contract.validate_manifest(changed)
        changed = copy.deepcopy(manifest)
        changed["output"]["overwrite_allowed"] = True
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "output overwrite enabled"
        ):
            contract.validate_manifest(changed)

    def test_canonical_manifest_round_trip_and_no_overwrite(self) -> None:
        manifest = self.build()
        data = contract.canonical_json_bytes(manifest) + b"\n"
        contract.atomic_write_new(self.manifest_path, data)
        observed = contract.read_manifest(self.manifest_path)
        self.assertEqual(observed, manifest)
        with self.assertRaisesRegex(
            contract.R13LocalManifestError, "refusing to overwrite manifest"
        ):
            contract.atomic_write_new(self.manifest_path, data)

    def test_duplicate_nonfinite_and_noncanonical_manifest_are_rejected(self) -> None:
        path = self.project / "formal_g1_development_r1" / "bad.json"
        path.write_text('{"a":1,"a":2}\n', encoding="utf-8")
        with self.assertRaisesRegex(contract.R13LocalManifestError, "duplicate JSON key"):
            contract.read_strict_json(path, "bad")
        path.write_text('{"a":NaN}\n', encoding="utf-8")
        with self.assertRaisesRegex(contract.R13LocalManifestError, "non-finite"):
            contract.read_strict_json(path, "bad")
        manifest = self.build()
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(contract.R13LocalManifestError, "not canonical JSON"):
            contract.read_manifest(path)

    def test_cli_build_and_prelaunch_verify_are_model_free(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = contract.main(
                [
                    "build",
                    "--project-root",
                    str(self.project),
                    "--output",
                    str(self.manifest_path),
                    "--output-root",
                    str(self.output_root),
                    "--canary-receipt",
                    str(self.canary_receipt),
                    "--run-id",
                    self.run_id,
                    "--frozen-at-utc",
                    self.frozen_at,
                ]
            )
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["processes"], 8)
        self.assertEqual(report["model_actions"], 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = contract.main(
                [
                    "verify",
                    "--project-root",
                    str(self.project),
                    "--manifest",
                    str(self.manifest_path),
                    "--prelaunch",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["model_actions"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
