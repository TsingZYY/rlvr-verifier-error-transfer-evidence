"""Mutation tests for the fail-closed R4 replicate validator."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
VALIDATOR_PATH = HERE / "validate_mvp_replicates_r3.py"
SPEC = importlib.util.spec_from_file_location("validate_mvp_replicates_r3", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def fixture() -> tuple[dict, dict, dict]:
    fake = lambda char: char * 64
    bindings = {
        "config_sha256": fake("1"),
        "source_bundles_sha256": fake("2"),
        "target_calibration_sha256": fake("3"),
        "mapping_stacks_sha256": fake("4"),
        "runner_sha256": fake("5"),
        "validator_sha256": fake("6"),
        "asset_validation_sha256": fake("7"),
        "determinism_addendum_sha256": fake("8"),
        "audit_seal_receipt_sha256": fake("9"),
        "audit_file_sha256_from_seal": fake("a"),
        "model_recursive_inventory": [
            {"path": "model.safetensors", "size": 1, "sha256": fake("b")}
        ],
        "model_recursive_inventory_sha256": fake("b"),
        "chat_template_sha256": fake("c"),
    }
    updates = []
    cells = []
    identities = list(validator.IDENTITIES)
    candidates = [f"FINAL=K{i}" for i in range(7)]
    offsets = {
        identity: f"FINAL=K{index}"
        for index, identity in enumerate(identities, 1)
    }
    uniform_gate = {
        "mean_wrong_probability_mass": 1.0 / 7.0,
        "mean_gold_probability_mass": 1.0 / 7.0,
        "mean_accepted_probability_mass": 2.0 / 7.0,
        "mean_wrong_relative_advantage": 5.0 / 7.0,
        "mean_candidate_entropy_nats": __import__("math").log(7.0),
    }
    pre_source_traces = [
        {
            "row_id": f"source-{index}",
            "state": "PRE_SOURCE",
            "source_rule_identity": None,
            "source_update_hash": fake("0"),
            "ordered_candidate_scores": [0.0] * 7,
            "supervised_token_counts": [2] * 7,
            "gold_candidate": "FINAL=K0",
            "offset_candidates": offsets,
        }
        for index in range(14)
    ]
    pre_target_traces = [
        {
            "row_id": f"target-{index}",
            "state": "PRE_TARGET",
            "source_rule_identity": None,
            "source_update_hash": fake("0"),
            "ordered_candidate_scores": [0.0] * 7,
            "supervised_token_counts": [2] * 7,
            "gold_candidate": "FINAL=K0",
            "offset_candidates": offsets,
        }
        for index in range(7)
    ]
    manifest = {
        "bindings": bindings,
        "selected_mapping_stack_id": "TP1-M0-A_TO_B",
        "stack_membership_commitments": {
            "TP1-M0-A_TO_B": {
                "source_rows_commitment": fake("e"),
                "target_rows_commitment": fake("f"),
                "source_trace_semantics": validator._trace_semantics(
                    pre_source_traces
                ),
                "target_trace_semantics": validator._trace_semantics(
                    pre_target_traces
                ),
            }
        },
    }
    post_traces: dict[str, list[dict]] = {}
    parameter_hashes: dict[str, str] = {}
    for source_index, source in enumerate(identities, 1):
        update_hash = f"{source_index:064x}"
        parameter_hashes[source] = update_hash
        score_vector = [0.0] * 7
        score_vector[source_index] = 0.1
        post_traces[source] = [
            {
                "row_id": f"target-{index}",
                "state": "POST_TARGET",
                "source_rule_identity": source,
                "source_update_hash": update_hash,
                "ordered_candidate_scores": list(score_vector),
                "supervised_token_counts": [2] * 7,
                "gold_candidate": "FINAL=K0",
                "offset_candidates": offsets,
            }
            for index in range(7)
        ]
        updates.append(
            {
                "source_rule_identity": source,
                "source_update_hash": update_hash,
                "mean_pre_update_expected_reward": 2.0 / 7.0,
                "raw_gradient_norm": 0.05,
                "clip_grad_norm_return": 0.05,
                "realized_update_norm": 0.005,
                "norm_evidence_boundary": dict(
                    validator.static_contract.NORM_EVIDENCE_BOUNDARY
                ),
                "diagonal_excess": 0.1,
            }
        )
        for target in identities:
            effect = 0.1 if source == target else 0.0
            cells.append(
                {
                    "source_rule_identity": source,
                    "target_rule_identity": target,
                    "source_update_hash": update_hash,
                    "same_update_reference": True,
                    "pre_target_metric": 0.0,
                    "post_target_metric": effect,
                    "effect": effect,
                    "is_diagonal": source == target,
                }
            )
    result = {
        "schema_version": "same-source-diagnostic-mvp-result-r5",
        "run_label": "DIAGNOSTIC_MVP_NOT_FORMAL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(validator.static_contract.EVIDENCE_BOUNDARY),
        "replicate_id": "A",
        "run_nonce": "11111111-1111-4111-8111-111111111111",
        "invocation_start_receipt_sha256": fake("9"),
        "authorization_receipt_sha256": fake("8"),
        "authorization_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "norm_evidence_boundary": dict(
            validator.static_contract.NORM_EVIDENCE_BOUNDARY
        ),
        "created_at_utc": "2026-08-04T00:00:00+00:00",
        "execution_manifest_sha256": fake("d"),
        "execution_manifest_bindings_sha256": validator.sha256_bytes(
            validator.canonical_json_bytes(bindings)
        ),
        "config_sha256": bindings["config_sha256"],
        "runner_sha256": bindings["runner_sha256"],
        "validator_sha256": bindings["validator_sha256"],
        "mapping_stacks_sha256": bindings["mapping_stacks_sha256"],
        "asset_validation_sha256": bindings["asset_validation_sha256"],
        "determinism_addendum_sha256": bindings["determinism_addendum_sha256"],
        "model_recursive_inventory": bindings["model_recursive_inventory"],
        "model_recursive_inventory_sha256": bindings[
            "model_recursive_inventory_sha256"
        ],
        "chat_template_sha256": bindings["chat_template_sha256"],
        "determinism_fail_closed": True,
        "source_bundles_sha256": bindings["source_bundles_sha256"],
        "target_calibration_sha256": bindings["target_calibration_sha256"],
        "mapping_stack_id": "TP1-M0-A_TO_B",
        "source_row_count": 14,
        "target_row_count": 7,
        "source_rows_commitment": fake("e"),
        "target_rows_commitment": fake("f"),
        "ordered_candidate_set": candidates,
        "trainable_parameters": 1024,
        "initial_trainable_hash": fake("0"),
        "initial_parameter_hash": fake("0"),
        "candidate_supervised_token_count_min": 2,
        "candidate_supervised_token_count_max": 2,
        "candidate_supervised_token_count_range": 0,
        "base_source_gate_metrics": {
            identity: dict(uniform_gate) for identity in identities
        },
        "base_target_metrics": {identity: 0.0 for identity in identities},
        "pre_source_row_traces": pre_source_traces,
        "pre_target_row_traces": pre_target_traces,
        "post_target_row_traces_by_source_identity": post_traces,
        "source_update_parameter_hashes": parameter_hashes,
        "diagnostic_gate_status": "PASS",
        "diagnostic_gate_failures": [],
        "source_updates": updates,
        "evaluation_cells": cells,
        "stack_summary": {
            "mean_diagonal_excess": 0.1,
            "positive_diagonal_excess_count": 5,
            "source_rule_count": 5,
        },
        "restore_max_abs_target_score_error": 0.0,
        "unique_source_update_hash_count": 5,
        "run_status": "MVP_COMPLETED_DIAGNOSTIC_ONLY",
        "runtime_seconds": 1.0,
        "peak_gpu_memory_gib": 1.0,
        "claim_boundary": list(validator.static_contract.FROZEN_CLAIM_BOUNDARY),
    }
    addendum = {
        "schema_version": validator.static_contract.ADDENDUM_SCHEMA,
        "status": "FROZEN_BEFORE_AUTHORIZED_REPLICATES",
        "base_config": "MVP_CONFIG_V1.json",
        "base_config_sha256": bindings["config_sha256"],
        "reason": "deterministic CUDA workspace",
        "only_change": {
            "environment_variable": "CUBLAS_WORKSPACE_CONFIG",
            "value": ":4096:8",
        },
        "forbidden_changes": ["all scientific choices"],
        "replicate_count": 2,
        "comparison_rule": "substantive equality",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(validator.static_contract.EVIDENCE_BOUNDARY),
    }
    return result, manifest, addendum


def external_chain(
    manifest_sha: str,
    result_a_sha: str,
    result_b_sha: str,
    left: dict,
    right: dict,
    invocation_a_sha: str,
    invocation_b_sha: str,
) -> tuple[dict, str, dict, str]:
    stack_hashes = {stack: "a" * 64 for stack in validator.static_contract.EXPECTED_STACK_IDS}
    stack_hashes["TP1-M0-A_TO_B"] = manifest_sha
    master = {
        "schema_version": validator.static_contract.MASTER_CONTRACT_SCHEMA,
        "status": "FROZEN_EIGHT_STACK_SCREEN_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(validator.static_contract.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_stack_ids": list(validator.static_contract.EXPECTED_STACK_IDS),
        "required_stack_count": 8,
        "config_sha256_by_stack": {
            stack: "b" * 64 for stack in validator.static_contract.EXPECTED_STACK_IDS
        },
        "manifest_sha256_by_stack": stack_hashes,
        "shared_runner_sha256": "c" * 64,
        "shared_validator_sha256": "d" * 64,
        "shared_completion_validator_sha256": "9" * 64,
        "shared_static_contract_sha256": "e" * 64,
        "shared_model_recursive_inventory_sha256": "f" * 64,
        "inclusion_rule": "RUN_ALL_EIGHT_STACKS_WITHOUT_SELECTION_OR_DELETION",
        "exclusion_rule": "NO_STACK_IDENTITY_THRESHOLD_OR_HYPERPARAMETER_ADAPTATION",
        "claim_boundary": list(validator.static_contract.FROZEN_CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
        },
    }
    master_sha = validator.sha256_bytes(validator.canonical_json_bytes(master))
    anchor = {
        "schema_version": "same-source-result-pair-anchor-r5",
        "status": "FROZEN_POST_RUN_PRE_VALIDATION",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(validator.static_contract.EVIDENCE_BOUNDARY),
        "mapping_stack_id": "TP1-M0-A_TO_B",
        "master_inclusion_contract_sha256": master_sha,
        "execution_manifest_sha256": manifest_sha,
        "result_a_sha256": result_a_sha,
        "result_b_sha256": result_b_sha,
        "authorization_receipt_sha256": left["authorization_receipt_sha256"],
        "authorization_id": left["authorization_id"],
        "result_a_replicate_id": left["replicate_id"],
        "result_b_replicate_id": right["replicate_id"],
        "result_a_run_nonce": left["run_nonce"],
        "result_b_run_nonce": right["run_nonce"],
        "invocation_start_receipt_a_sha256": invocation_a_sha,
        "invocation_start_receipt_b_sha256": invocation_b_sha,
        "custody_requirement": "STORE_OUTSIDE_BOTH_RESULT_OUTPUT_DIRECTORIES_AND_RECORD_SHA256_EXTERNALLY",
    }
    anchor_sha = validator.sha256_bytes(validator.canonical_json_bytes(anchor))
    return master, master_sha, anchor, anchor_sha


class R4ValidatorMutationTests(unittest.TestCase):
    @staticmethod
    def invocation(result: dict) -> dict:
        return {
            "schema_version": validator.static_contract.INVOCATION_RECEIPT_SCHEMA,
            "status": "FROZEN_BEFORE_MODEL_LOAD",
            "evidence_boundary": dict(validator.static_contract.EVIDENCE_BOUNDARY),
            "replicate_id": result["replicate_id"],
            "run_nonce": result["run_nonce"],
            "mapping_stack_id": result["mapping_stack_id"],
            "execution_manifest_sha256": result["execution_manifest_sha256"],
            "authorization_receipt_sha256": result["authorization_receipt_sha256"],
            "authorization_id": result["authorization_id"],
            "started_at_utc": "2026-08-04T00:00:00Z",
            "custody_requirement": "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
        }

    def validate(
        self,
        left: dict,
        right: dict,
        manifest: dict,
        addendum: dict,
        *,
        anchored_left_sha: str | None = None,
        anchored_right_sha: str | None = None,
        duplicate_nonce: bool = False,
        same_path: bool = False,
        same_inode: bool = False,
        actual_manifest_sha: str = "d" * 64,
        expected_manifest_sha: str = "d" * 64,
    ) -> dict:
        left["replicate_id"] = "A"
        left["run_nonce"] = "11111111-1111-4111-8111-111111111111"
        right["replicate_id"] = "B"
        right["run_nonce"] = (
            left["run_nonce"]
            if duplicate_nonce
            else "22222222-2222-4222-8222-222222222222"
        )
        invocation_a = self.invocation(left)
        invocation_b = self.invocation(right)
        invocation_a_sha = validator.sha256_bytes(
            validator.canonical_json_bytes(invocation_a)
        )
        invocation_b_sha = validator.sha256_bytes(
            validator.canonical_json_bytes(invocation_b)
        )
        left["invocation_start_receipt_sha256"] = invocation_a_sha
        right["invocation_start_receipt_sha256"] = invocation_b_sha
        left_sha = validator.sha256_bytes(validator.canonical_json_bytes(left))
        right_sha = validator.sha256_bytes(validator.canonical_json_bytes(right))
        master, master_sha, anchor, anchor_sha = external_chain(
            actual_manifest_sha,
            anchored_left_sha or left_sha,
            anchored_right_sha or right_sha,
            left,
            right,
            invocation_a_sha,
            invocation_b_sha,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left_path = root / "a.json"
            right_path = left_path if same_path else root / "b.json"
            left_path.write_bytes(validator.canonical_json_bytes(left))
            if not same_path:
                right_path.write_bytes(validator.canonical_json_bytes(right))
            if same_inode:
                right_path.unlink()
                os.link(left_path, right_path)
            invocation_a_path = root / "invocation-a.json"
            invocation_b_path = root / "invocation-b.json"
            invocation_a_path.write_bytes(validator.canonical_json_bytes(invocation_a))
            invocation_b_path.write_bytes(validator.canonical_json_bytes(invocation_b))
            return validator.validate_replicates(
                left,
                right,
                addendum,
                manifest,
                actual_manifest_sha,
                expected_manifest_sha,
                master,
                master_sha,
                master_sha,
                anchor,
                anchor_sha,
                anchor_sha,
                left_sha,
                right_sha,
                left_path,
                right_path,
                invocation_a,
                invocation_b,
                invocation_a_sha,
                invocation_b_sha,
                invocation_a_sha,
                invocation_b_sha,
            )

    def test_valid_synthetic_pair_passes(self) -> None:
        result, manifest, addendum = fixture()
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "PASS", report["errors"])

    def test_forged_matrix_is_rejected(self) -> None:
        result, manifest, addendum = fixture()
        result["evaluation_cells"][0]["effect"] = 999.0
        result["source_updates"][0]["diagonal_excess"] = 999.0
        result["stack_summary"]["mean_diagonal_excess"] = 999.0
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("effect not recomputed" in error for error in report["errors"]))

    def test_swapped_update_binding_is_rejected(self) -> None:
        result, manifest, addendum = fixture()
        first, second = result["source_updates"][:2]
        first["source_update_hash"], second["source_update_hash"] = (
            second["source_update_hash"],
            first["source_update_hash"],
        )
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("update-hash binding mismatch" in error for error in report["errors"]))

    def test_fake_provenance_is_rejected(self) -> None:
        result, manifest, addendum = fixture()
        result["config_sha256"] = "0" * 64
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("provenance mismatch" in error for error in report["errors"]))

    def test_unknown_schema_field_is_rejected(self) -> None:
        result, manifest, addendum = fixture()
        result["forged_pass"] = True
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("schema" in error for error in report["errors"]))

    def test_warn_only_determinism_is_rejected(self) -> None:
        result, manifest, addendum = fixture()
        result["determinism_fail_closed"] = False
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("fail closed" in error for error in report["errors"]))

    def test_fake_row_commitments_are_rejected(self) -> None:
        result, manifest, addendum = fixture()
        result["source_rows_commitment"] = "a" * 64
        result["target_rows_commitment"] = "b" * 64
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("rows commitment mismatch" in error for error in report["errors"]))

    def test_coordinated_fake_provenance_fails_external_anchor(self) -> None:
        result, manifest, addendum = fixture()
        report = self.validate(
            result,
            copy.deepcopy(result),
            manifest,
            addendum,
            actual_manifest_sha="e" * 64,
            expected_manifest_sha="d" * 64,
        )
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertIn("external expected manifest hash mismatch", report["errors"])

    def test_coherent_source_identity_relabel_fails_result_anchor(self) -> None:
        result, manifest, addendum = fixture()
        original_sha = validator.sha256_bytes(
            validator.canonical_json_bytes(result)
        )
        mutated = copy.deepcopy(result)
        first, second = validator.IDENTITIES[:2]
        for update in mutated["source_updates"]:
            if update["source_rule_identity"] == first:
                update["source_rule_identity"] = second
            elif update["source_rule_identity"] == second:
                update["source_rule_identity"] = first
        report = self.validate(
            mutated,
            copy.deepcopy(mutated),
            manifest,
            addendum,
            anchored_left_sha=original_sha,
            anchored_right_sha=original_sha,
        )
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertIn("external anchored result A hash mismatch", report["errors"])

    def test_duplicate_update_hashes_are_rejected(self) -> None:
        result, manifest, addendum = fixture()
        first, second = validator.IDENTITIES[:2]
        duplicate = result["source_update_parameter_hashes"][first]
        result["source_update_parameter_hashes"][second] = duplicate
        for update in result["source_updates"]:
            if update["source_rule_identity"] == second:
                update["source_update_hash"] = duplicate
        for cell in result["evaluation_cells"]:
            if cell["source_rule_identity"] == second:
                cell["source_update_hash"] = duplicate
        for trace in result["post_target_row_traces_by_source_identity"][second]:
            trace["source_update_hash"] = duplicate
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("not unique" in error for error in report["errors"]))

    def test_row_semantics_mutation_is_rejected_even_when_reanchored(self) -> None:
        result, manifest, addendum = fixture()
        result["pre_target_row_traces"][0]["gold_candidate"] = "FINAL=K6"
        for traces in result["post_target_row_traces_by_source_identity"].values():
            traces[0]["gold_candidate"] = "FINAL=K6"
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(
            any("trace semantics" in error for error in report["errors"])
        )

    def test_expected_reward_must_be_recomputed_from_source_traces(self) -> None:
        result, manifest, addendum = fixture()
        result["source_updates"][0]["mean_pre_update_expected_reward"] = 0.9
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(
            any("source-trace-derived" in error for error in report["errors"])
        )

    def test_same_result_path_cannot_be_replica_a_and_b(self) -> None:
        result, manifest, addendum = fixture()
        report = self.validate(
            result, copy.deepcopy(result), manifest, addendum, same_path=True
        )
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("same result path" in error for error in report["errors"]))

    def test_same_inode_cannot_be_replica_a_and_b(self) -> None:
        result, manifest, addendum = fixture()
        report = self.validate(
            result, copy.deepcopy(result), manifest, addendum, same_inode=True
        )
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("same filesystem object" in error for error in report["errors"]))

    def test_duplicate_run_nonce_rejected(self) -> None:
        result, manifest, addendum = fixture()
        report = self.validate(
            result, copy.deepcopy(result), manifest, addendum, duplicate_nonce=True
        )
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("nonces must be distinct" in error for error in report["errors"]))

    def test_norms_are_explicitly_non_independent_and_non_scientific(self) -> None:
        result, manifest, addendum = fixture()
        result["norm_evidence_boundary"]["used_for_diagnostic_gates"] = True
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("norms must be explicitly" in error for error in report["errors"]))

    def test_exact_evidence_boundary_labels_required(self) -> None:
        result, manifest, addendum = fixture()
        result["evidence_boundary"]["data_scope"] = "HIDDEN_AUDIT"
        report = self.validate(result, copy.deepcopy(result), manifest, addendum)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("exact evidence-boundary" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
