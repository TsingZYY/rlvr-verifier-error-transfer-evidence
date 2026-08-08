from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import mvp_static_contract as base
import r11_static_contract as contract


HASH = "a" * 64
OTHER_HASH = "b" * 64
UUID = "00000000-0000-4000-8000-000000000001"


def write_json(path: Path, value: dict) -> str:
    path.write_bytes(base.canonical_json_bytes(value))
    return base.sha256_file(path)


def fake_manifest(selected_cell: str) -> dict:
    stack, arm, replicate = contract.parse_cell_id(selected_cell)
    return {
        "schema_version": contract.MANIFEST_SCHEMA,
        "status": contract.MANIFEST_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "mapping_stack_ids": list(contract.EXPECTED_STACK_IDS),
        "selected_mapping_stack_id": stack,
        "stack_membership_commitments": {},
        "source_rule_offsets_mod7": [1, 2, 3, 4, 5],
        "target_rule_offsets_mod7": [1, 2, 3, 4, 5],
        "bindings": {
            "asset_validation_sha256": HASH,
            "audit_file_sha256_from_seal": HASH,
            "audit_seal_receipt_sha256": HASH,
            "chat_template_sha256": HASH,
            "config_sha256": HASH,
            "determinism_addendum_sha256": HASH,
            "mapping_stacks_sha256": HASH,
            "model_recursive_inventory": [],
            "runner_sha256": HASH,
            "source_bundles_sha256": HASH,
            "validator_sha256": HASH,
            "static_contract_sha256": HASH,
            "target_calibration_sha256": HASH,
            "model_recursive_inventory_sha256": HASH,
            "parent_r10_runner_sha256": HASH,
            "r11_protocol_sha256": HASH,
            "r11_arm_contract_sha256": HASH,
            "r11_bridge_contract_sha256": HASH,
        },
        "runtime_expected": {},
        "runtime_observed_at_manifest_build": {},
        "determinism_environment": {},
        "audit_contract": {},
        "authorization": dict(contract.STATIC_AUTHORIZATION),
        "bridge_cell_id": selected_cell,
        "arm": arm,
        "replicate_id": replicate,
        "development_screen_only": True,
        "formal_confirmatory": False,
        "unique_permitted_treatment_difference": "reward_mask",
    }


def fake_master() -> dict:
    return {
        "schema_version": contract.MASTER_SCHEMA,
        "status": contract.MASTER_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_cell_ids": list(contract.EXPECTED_CELL_IDS),
        "required_process_count": 32,
        "required_unique_arm_specific_design_cells": 80,
        "required_technical_update_executions": 160,
        "required_target_identity_evaluation_cells": 800,
        "config_sha256_by_stack": {
            stack: HASH for stack in contract.EXPECTED_STACK_IDS
        },
        "manifest_sha256_by_cell": {
            cell: HASH for cell in contract.EXPECTED_CELL_IDS
        },
        "arm_contract_sha256_by_cell": {
            cell: HASH for cell in contract.EXPECTED_CELL_IDS
        },
        "shared_runner_sha256": HASH,
        "shared_validator_sha256": HASH,
        "shared_completion_validator_sha256": HASH,
        "shared_static_contract_sha256": HASH,
        "shared_bridge_contract_sha256": HASH,
        "shared_parent_r10_runner_sha256": HASH,
        "shared_r11_protocol_sha256": HASH,
        "shared_model_recursive_inventory_sha256": HASH,
        "inclusion_rule": "RUN_ALL_32_STACK_ARM_REPLICATE_CELLS_WITHOUT_SELECTION",
        "exclusion_rule": "NO_CELL_IDENTITY_THRESHOLD_HYPERPARAMETER_RETRY_OR_OUTCOME_ADAPTATION",
        "claim_boundary": list(contract.CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
    }


def authorization_receipt(master: dict, master_sha256: str) -> dict:
    return {
        "schema_version": contract.AUTHORIZATION_SCHEMA,
        "status": "AUTHORIZED_BY_USER",
        "action_id": contract.AUTHORIZATION_ACTION_ID,
        "version": contract.AUTHORIZATION_VERSION,
        "authorization_id": UUID,
        "issued_at_utc": "2026-08-05T00:00:00Z",
        "expires_at_utc": "2026-08-06T00:00:00Z",
        "master_inclusion_contract_sha256": master_sha256,
        "ordered_cell_ids": list(contract.EXPECTED_CELL_IDS),
        "manifest_sha256_by_cell": dict(master["manifest_sha256_by_cell"]),
        "arm_contract_sha256_by_cell": dict(master["arm_contract_sha256_by_cell"]),
        "runner_sha256": master["shared_runner_sha256"],
        "validator_sha256": master["shared_validator_sha256"],
        "completion_validator_sha256": master["shared_completion_validator_sha256"],
        "static_contract_sha256": master["shared_static_contract_sha256"],
        "bridge_contract_sha256": master["shared_bridge_contract_sha256"],
        "parent_r10_runner_sha256": master["shared_parent_r10_runner_sha256"],
        "r11_protocol_sha256": master["shared_r11_protocol_sha256"],
        "model_recursive_inventory_sha256": master["shared_model_recursive_inventory_sha256"],
        "allowed_operations": list(contract.ALLOWED_OPERATIONS),
        "forbidden_operations": list(contract.FORBIDDEN_OPERATIONS),
        "model_execution_authorized": True,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
    }


class R11StaticContractTests(unittest.TestCase):
    def test_cell_matrix_is_exactly_8_by_2_by_2(self) -> None:
        self.assertEqual(len(contract.EXPECTED_CELL_IDS), 32)
        self.assertEqual(len(set(contract.EXPECTED_CELL_IDS)), 32)
        self.assertEqual(
            contract.EXPECTED_CELL_IDS[0], "TP1-M0-A_TO_B|BUG|A"
        )
        self.assertEqual(
            contract.EXPECTED_CELL_IDS[-1], "TP2-M1-B_TO_A|GOLD_ONLY|B"
        )

    def test_arm_contract_is_non_authorizing_and_cell_bound(self) -> None:
        value = contract.build_arm_contract(
            stack_id=contract.EXPECTED_STACK_IDS[0],
            arm="BUG",
            replicate_id="A",
            parent_runner_sha256=HASH,
            protocol_sha256=OTHER_HASH,
        )
        self.assertEqual(
            contract.validate_arm_contract(
                value,
                expected_parent_runner_sha256=HASH,
                expected_protocol_sha256=OTHER_HASH,
            ),
            contract.EXPECTED_CELL_IDS[0],
        )
        value["model_execution_authorized"] = True
        with self.assertRaisesRegex(contract.R11ContractError, "self-authorization"):
            contract.validate_arm_contract(
                value,
                expected_parent_runner_sha256=HASH,
                expected_protocol_sha256=OTHER_HASH,
            )

    def test_manifest_rejects_arm_and_schema_drift(self) -> None:
        manifest = fake_manifest(contract.EXPECTED_CELL_IDS[0])
        self.assertEqual(
            contract.validate_manifest_shape(manifest), contract.EXPECTED_CELL_IDS[0]
        )
        manifest["arm"] = "GOLD_ONLY"
        with self.assertRaisesRegex(contract.R11ContractError, "cell binding"):
            contract.validate_manifest_shape(manifest)
        manifest = fake_manifest(contract.EXPECTED_CELL_IDS[0])
        manifest["unfrozen_extra"] = True
        with self.assertRaisesRegex(contract.R11ContractError, "schema drift"):
            contract.validate_manifest_shape(manifest)

    def test_master_rejects_missing_or_reordered_cells(self) -> None:
        master = fake_master()
        contract.validate_master(master)
        master["ordered_cell_ids"] = master["ordered_cell_ids"][:-1]
        with self.assertRaisesRegex(contract.R11ContractError, "cell order"):
            contract.validate_master(master)
        master = fake_master()
        del master["manifest_sha256_by_cell"][contract.EXPECTED_CELL_IDS[-1]]
        with self.assertRaisesRegex(contract.R11ContractError, "coverage"):
            contract.validate_master(master)

    def test_authorization_binds_exact_cell_maps_and_expiry(self) -> None:
        selected_cell = contract.EXPECTED_CELL_IDS[0]
        manifest = fake_manifest(selected_cell)
        master = fake_master()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.json"
            receipt_path = root / "authorization.json"
            master_sha256 = write_json(master_path, master)
            receipt = authorization_receipt(master, master_sha256)
            receipt_sha256 = write_json(receipt_path, receipt)
            verified = contract.verify_authorization_receipt(
                receipt_path=receipt_path,
                expected_receipt_sha256=receipt_sha256,
                master_path=master_path,
                selected_cell_id=selected_cell,
                selected_manifest_sha256=HASH,
                selected_arm_contract_sha256=HASH,
                manifest=manifest,
                now=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(verified["authorization_id"], UUID)

            tampered = copy.deepcopy(receipt)
            tampered["arm_contract_sha256_by_cell"][selected_cell] = OTHER_HASH
            tampered_hash = write_json(receipt_path, tampered)
            with self.assertRaisesRegex(contract.R11ContractError, "arm-contract map"):
                contract.verify_authorization_receipt(
                    receipt_path=receipt_path,
                    expected_receipt_sha256=tampered_hash,
                    master_path=master_path,
                    selected_cell_id=selected_cell,
                    selected_manifest_sha256=HASH,
                    selected_arm_contract_sha256=HASH,
                    manifest=manifest,
                    now=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
                )

            receipt_sha256 = write_json(receipt_path, receipt)
            with self.assertRaisesRegex(contract.R11ContractError, "expired"):
                contract.verify_authorization_receipt(
                    receipt_path=receipt_path,
                    expected_receipt_sha256=receipt_sha256,
                    master_path=master_path,
                    selected_cell_id=selected_cell,
                    selected_manifest_sha256=HASH,
                    selected_arm_contract_sha256=HASH,
                    manifest=manifest,
                    now=datetime(2026, 8, 7, tzinfo=timezone.utc),
                )

    def test_invocation_rejects_arm_or_nonce_swap(self) -> None:
        selected_cell = contract.EXPECTED_CELL_IDS[0]
        stack, arm, replicate = contract.parse_cell_id(selected_cell)
        receipt = {
            "schema_version": contract.INVOCATION_SCHEMA,
            "status": "FROZEN_BEFORE_MODEL_LOAD",
            "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
            "cell_id": selected_cell,
            "mapping_stack_id": stack,
            "arm": arm,
            "replicate_id": replicate,
            "run_nonce": UUID,
            "execution_manifest_sha256": HASH,
            "arm_contract_sha256": HASH,
            "authorization_receipt_sha256": HASH,
            "authorization_id": UUID,
            "started_at_utc": "2026-08-05T00:00:00Z",
            "custody_requirement": "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invocation.json"
            receipt_sha256 = write_json(path, receipt)
            contract.verify_invocation_start_receipt(
                receipt_path=path,
                expected_receipt_sha256=receipt_sha256,
                replicate_id=replicate,
                run_nonce=UUID,
                mapping_stack_id=stack,
                arm=arm,
                execution_manifest_sha256=HASH,
                arm_contract_sha256=HASH,
                authorization_receipt_sha256=HASH,
                authorization_id=UUID,
            )
            tampered = copy.deepcopy(receipt)
            tampered["arm"] = "GOLD_ONLY"
            tampered_hash = write_json(path, tampered)
            with self.assertRaisesRegex(contract.R11ContractError, "arm mismatch"):
                contract.verify_invocation_start_receipt(
                    receipt_path=path,
                    expected_receipt_sha256=tampered_hash,
                    replicate_id=replicate,
                    run_nonce=UUID,
                    mapping_stack_id=stack,
                    arm=arm,
                    execution_manifest_sha256=HASH,
                    arm_contract_sha256=HASH,
                    authorization_receipt_sha256=HASH,
                    authorization_id=UUID,
                )


if __name__ == "__main__":
    unittest.main()
