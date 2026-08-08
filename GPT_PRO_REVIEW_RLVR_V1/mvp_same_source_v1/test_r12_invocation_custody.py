from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mvp_static_contract as base
import r11_static_contract as r11
import r12_invocation_custody as custody


HASH = "a" * 64
AUTHORIZATION_ID = "00000000-0000-4000-8000-000000000001"
NONCE_ONE = "00000000-0000-4000-8000-000000000011"
NONCE_TWO = "00000000-0000-4000-8000-000000000012"
NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
TRUSTED_TEST_KEY = b"r12-test-only-external-trust-key"
TRUSTED_TEST_SIGNER_ID = "test-only-external-signer-v1"
TRUSTED_TEST_ALGORITHM = "TEST-ONLY-HMAC-SHA256-NOT-FOR-PRODUCTION"
TRUSTED_TEST_FINGERPRINT = hashlib.sha256(
    b"test-only-external-public-key-identity"
).hexdigest()


def trusted_test_policy() -> custody.TrustedSignerPolicy:
    """A deterministic test double for the external verifier interface.

    Production command-line entrypoints never load this policy and therefore
    remain unauthorized.  HMAC is deliberately not a production substitute
    for the required externally provisioned asymmetric signer.
    """

    def verify(message: bytes, signature: bytes) -> bool:
        expected = hmac.new(TRUSTED_TEST_KEY, message, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)

    return custody.TrustedSignerPolicy(
        signer_id=TRUSTED_TEST_SIGNER_ID,
        key_fingerprint_sha256=TRUSTED_TEST_FINGERPRINT,
        signature_algorithm=TRUSTED_TEST_ALGORITHM,
        verify_signature=verify,
    )


def write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = base.canonical_json_bytes(value)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def fake_manifest(selected_cell: str, arm_contract_sha256: str) -> dict:
    stack, arm, replicate = r11.parse_cell_id(selected_cell)
    return {
        "schema_version": r11.MANIFEST_SCHEMA,
        "status": r11.MANIFEST_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "mapping_stack_ids": list(r11.EXPECTED_STACK_IDS),
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
            "model_recursive_inventory_sha256": HASH,
            "runner_sha256": HASH,
            "source_bundles_sha256": HASH,
            "static_contract_sha256": HASH,
            "target_calibration_sha256": HASH,
            "validator_sha256": HASH,
            "parent_r10_runner_sha256": HASH,
            "r11_protocol_sha256": HASH,
            "r11_arm_contract_sha256": arm_contract_sha256,
            "r11_bridge_contract_sha256": HASH,
        },
        "runtime_expected": {},
        "runtime_observed_at_manifest_build": {},
        "determinism_environment": {},
        "audit_contract": {},
        "authorization": dict(r11.STATIC_AUTHORIZATION),
        "bridge_cell_id": selected_cell,
        "arm": arm,
        "replicate_id": replicate,
        "development_screen_only": True,
        "formal_confirmatory": False,
        "unique_permitted_treatment_difference": "reward_mask",
    }


def fake_master(manifest_hashes: dict[str, str], arm_hashes: dict[str, str]) -> dict:
    manifests = {cell: HASH for cell in r11.EXPECTED_CELL_IDS}
    arms = {cell: HASH for cell in r11.EXPECTED_CELL_IDS}
    manifests.update(manifest_hashes)
    arms.update(arm_hashes)
    return {
        "schema_version": r11.MASTER_SCHEMA,
        "status": r11.MASTER_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_cell_ids": list(r11.EXPECTED_CELL_IDS),
        "required_process_count": 32,
        "required_unique_arm_specific_design_cells": 80,
        "required_technical_update_executions": 160,
        "required_target_identity_evaluation_cells": 800,
        "config_sha256_by_stack": {stack: HASH for stack in r11.EXPECTED_STACK_IDS},
        "manifest_sha256_by_cell": manifests,
        "arm_contract_sha256_by_cell": arms,
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
        "claim_boundary": list(r11.CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
    }


def authorization_receipt(master: dict, master_sha256: str) -> dict:
    return {
        "schema_version": r11.AUTHORIZATION_SCHEMA,
        "status": "AUTHORIZED_BY_USER",
        "action_id": r11.AUTHORIZATION_ACTION_ID,
        "version": r11.AUTHORIZATION_VERSION,
        "authorization_id": AUTHORIZATION_ID,
        "issued_at_utc": "2026-08-05T00:00:00Z",
        "expires_at_utc": "2026-08-06T00:00:00Z",
        "master_inclusion_contract_sha256": master_sha256,
        "ordered_cell_ids": list(r11.EXPECTED_CELL_IDS),
        "manifest_sha256_by_cell": dict(master["manifest_sha256_by_cell"]),
        "arm_contract_sha256_by_cell": dict(master["arm_contract_sha256_by_cell"]),
        "runner_sha256": master["shared_runner_sha256"],
        "validator_sha256": master["shared_validator_sha256"],
        "completion_validator_sha256": master["shared_completion_validator_sha256"],
        "static_contract_sha256": master["shared_static_contract_sha256"],
        "bridge_contract_sha256": master["shared_bridge_contract_sha256"],
        "parent_r10_runner_sha256": master["shared_parent_r10_runner_sha256"],
        "r11_protocol_sha256": master["shared_r11_protocol_sha256"],
        "model_recursive_inventory_sha256": master[
            "shared_model_recursive_inventory_sha256"
        ],
        "allowed_operations": list(r11.ALLOWED_OPERATIONS),
        "forbidden_operations": list(r11.FORBIDDEN_OPERATIONS),
        "model_execution_authorized": True,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
    }


class R12InvocationCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.assets = self.root / "assets"
        self.custody_dir = self.root / "custody" / "invocations"
        self.ledger = self.root / "custody" / "ledger"
        self.results = self.root / "results"
        self.cells = [r11.EXPECTED_CELL_IDS[0], r11.EXPECTED_CELL_IDS[1]]
        self.arm_paths: dict[str, Path] = {}
        self.arm_hashes: dict[str, str] = {}
        self.manifest_paths: dict[str, Path] = {}
        self.manifest_hashes: dict[str, str] = {}
        for index, cell in enumerate(self.cells):
            stack, arm, replicate = r11.parse_cell_id(cell)
            arm_value = r11.build_arm_contract(
                stack_id=stack,
                arm=arm,
                replicate_id=replicate,
                parent_runner_sha256=HASH,
                protocol_sha256=HASH,
            )
            arm_path = self.assets / f"arm-{index}.json"
            arm_hash = write_json(arm_path, arm_value)
            manifest_path = self.assets / f"manifest-{index}.json"
            manifest_hash = write_json(
                manifest_path, fake_manifest(cell, arm_hash)
            )
            self.arm_paths[cell] = arm_path
            self.arm_hashes[cell] = arm_hash
            self.manifest_paths[cell] = manifest_path
            self.manifest_hashes[cell] = manifest_hash
        master = fake_master(self.manifest_hashes, self.arm_hashes)
        self.master_path = self.assets / "master.json"
        self.master_hash = write_json(self.master_path, master)
        authorization = authorization_receipt(master, self.master_hash)
        self.authorization_path = self.assets / "authorization.json"
        self.authorization_hash = write_json(
            self.authorization_path, authorization
        )
        self.policy = trusted_test_policy()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write_signed_launch(
        self,
        *,
        path: Path,
        selected_cell: str,
        selected_nonce: str,
        selected_output: Path,
        selected_started_at_utc: str,
        issued_at_utc: str,
        expires_at_utc: str,
        mutate_payload=None,
        signer_id: str = TRUSTED_TEST_SIGNER_ID,
        fingerprint: str = TRUSTED_TEST_FINGERPRINT,
        signing_key: bytes = TRUSTED_TEST_KEY,
        include_signature: bool = True,
    ) -> tuple[Path, str]:
        nonce_map = {
            cell: f"10000000-0000-4000-8000-{index + 256:012x}"
            for index, cell in enumerate(r11.EXPECTED_CELL_IDS)
        }
        nonce_map[selected_cell] = selected_nonce
        output_map = {
            cell: str(
                custody.canonical_path(
                    self.results / "signed-defaults" / f"cell-{index:02d}" / "result.json"
                )
            )
            for index, cell in enumerate(r11.EXPECTED_CELL_IDS)
        }
        output_map[selected_cell] = str(custody.canonical_path(selected_output))
        start_map = {
            cell: selected_started_at_utc for cell in r11.EXPECTED_CELL_IDS
        }
        payload = {
            "schema_version": custody.SIGNED_LAUNCH_PAYLOAD_SCHEMA,
            "status": custody.SIGNED_LAUNCH_STATUS,
            "action_id": r11.AUTHORIZATION_ACTION_ID,
            "version": custody.SIGNED_LAUNCH_VERSION,
            "authorization_id": AUTHORIZATION_ID,
            "issued_at_utc": issued_at_utc,
            "expires_at_utc": expires_at_utc,
            "master_inclusion_contract_path": str(
                custody.canonical_path(self.master_path)
            ),
            "master_inclusion_contract_sha256": self.master_hash,
            "r11_authorization_receipt_sha256": self.authorization_hash,
            "ordered_cell_ids": list(r11.EXPECTED_CELL_IDS),
            "manifest_sha256_by_cell": dict(
                fake_master(self.manifest_hashes, self.arm_hashes)[
                    "manifest_sha256_by_cell"
                ]
            ),
            "arm_contract_sha256_by_cell": dict(
                fake_master(self.manifest_hashes, self.arm_hashes)[
                    "arm_contract_sha256_by_cell"
                ]
            ),
            "run_nonce_by_cell": nonce_map,
            "expected_result_output_path_by_cell": output_map,
            "started_at_utc_by_cell": start_map,
            "claim_ledger_root": str(custody.canonical_path(self.ledger)),
            "r12_custody_contract_sha256": custody.sha256_file_snapshot(
                custody.R12_CONTRACT_PATH
            ),
            "r12_custody_runner_sha256": custody.sha256_file_snapshot(
                custody.R12_RUNNER_PATH
            ),
            "allowed_operations": list(r11.ALLOWED_OPERATIONS),
            "forbidden_operations": list(r11.FORBIDDEN_OPERATIONS),
            "model_execution_authorized": True,
            "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        }
        if mutate_payload is not None:
            mutate_payload(payload)
        signature_bytes = hmac.new(
            signing_key,
            custody.signed_launch_message(payload),
            hashlib.sha256,
        ).digest()
        envelope = {
            "schema_version": custody.SIGNED_LAUNCH_ENVELOPE_SCHEMA,
            "payload": payload,
            "signature": {
                "algorithm": TRUSTED_TEST_ALGORITHM,
                "signer_id": signer_id,
                "key_fingerprint_sha256": fingerprint,
                "signature_base64": base64.b64encode(signature_bytes).decode("ascii"),
            },
        }
        if not include_signature:
            envelope.pop("signature")
        return path, write_json(path, envelope)

    def freeze(
        self,
        *,
        cell: str,
        nonce: str,
        receipt_name: str,
        result_name: str,
        now: datetime = NOW,
        started_at_utc: str | None = None,
        mutate_signed_payload=None,
        trusted_signer_policy: custody.TrustedSignerPolicy | None | object = Ellipsis,
        signed_signer_id: str = TRUSTED_TEST_SIGNER_ID,
        signed_fingerprint: str = TRUSTED_TEST_FINGERPRINT,
        signed_key: bytes = TRUSTED_TEST_KEY,
        include_signature: bool = True,
    ) -> tuple[custody.FrozenInvocation, Path]:
        receipt_path = self.custody_dir / receipt_name
        plan_path = self.custody_dir / f"{receipt_name}.plan.json"
        output_path = self.results / result_name / "result.json"
        started_text = started_at_utc or custody.format_rfc3339_utc(now)
        issued = custody.format_rfc3339_utc(now - timedelta(minutes=1))
        expires = custody.format_rfc3339_utc(now + timedelta(minutes=30))
        signed_path, signed_hash = self.write_signed_launch(
            path=self.custody_dir / f"{receipt_name}.signed-launch.json",
            selected_cell=cell,
            selected_nonce=nonce,
            selected_output=output_path,
            selected_started_at_utc=started_text,
            issued_at_utc=issued,
            expires_at_utc=expires,
            mutate_payload=mutate_signed_payload,
            signer_id=signed_signer_id,
            fingerprint=signed_fingerprint,
            signing_key=signed_key,
            include_signature=include_signature,
        )
        policy = self.policy if trusted_signer_policy is Ellipsis else trusted_signer_policy
        frozen = custody.freeze_r12_invocation(
            receipt_output_path=receipt_path,
            plan_output_path=plan_path,
            expected_result_output_path=output_path,
            claim_ledger_root=self.ledger,
            run_nonce=nonce,
            signed_launch_path=signed_path,
            expected_signed_launch_sha256=signed_hash,
            trusted_signer_policy=policy,
            master_path=self.master_path,
            expected_master_sha256=self.master_hash,
            authorization_path=self.authorization_path,
            expected_authorization_sha256=self.authorization_hash,
            manifest_path=self.manifest_paths[cell],
            expected_manifest_sha256=self.manifest_hashes[cell],
            arm_contract_path=self.arm_paths[cell],
            expected_arm_contract_sha256=self.arm_hashes[cell],
            selected_cell_id=cell,
            started_at_utc=started_text,
            now=now,
        )
        return frozen, output_path

    def claim(
        self,
        *,
        cell: str,
        nonce: str,
        receipt: custody.FrozenInvocation,
        output_path: Path,
        arm: str | None = None,
        ledger_root: Path | None = None,
        now: datetime = NOW,
    ) -> custody.HashedJson:
        selected_arm = arm or r11.parse_cell_id(cell)[1]
        return custody.claim_invocation_once(
            invocation_receipt_path=receipt.receipt.path,
            expected_invocation_sha256=receipt.receipt.sha256,
            invocation_plan_path=receipt.plan.path,
            expected_invocation_plan_sha256=receipt.plan.sha256,
            signed_launch_path=receipt.signed_launch.path,
            expected_signed_launch_sha256=receipt.signed_launch.sha256,
            trusted_signer_policy=self.policy,
            ledger_root=ledger_root or self.ledger,
            expected_result_output_path=output_path,
            expected_run_nonce=nonce,
            expected_arm=selected_arm,
            master_path=self.master_path,
            expected_master_sha256=self.master_hash,
            authorization_path=self.authorization_path,
            expected_authorization_sha256=self.authorization_hash,
            manifest_path=self.manifest_paths[cell],
            expected_manifest_sha256=self.manifest_hashes[cell],
            arm_contract_path=self.arm_paths[cell],
            expected_arm_contract_sha256=self.arm_hashes[cell],
            selected_cell_id=cell,
            now=now,
        )

    def wrapper_args(
        self,
        *,
        frozen: custody.FrozenInvocation,
        output_path: Path,
        cell: str,
        nonce: str,
    ) -> argparse.Namespace:
        _, _, replicate = r11.parse_cell_id(cell)
        return argparse.Namespace(
            config=self.assets / "unused-config.json",
            model=self.assets / "unused-model",
            source_bundles=self.assets / "unused-source.jsonl",
            target_calibration=self.assets / "unused-target.jsonl",
            mapping_stacks=self.assets / "unused-mapping.jsonl",
            execution_manifest=self.manifest_paths[cell],
            validator=self.assets / "unused-validator.py",
            asset_validation=self.assets / "unused-validation.json",
            determinism_addendum=self.assets / "unused-determinism.json",
            audit_seal=self.assets / "unused-audit-seal.json",
            expected_manifest_sha256=self.manifest_hashes[cell],
            master_inclusion_contract=self.master_path,
            authorization_receipt=self.authorization_path,
            expected_authorization_receipt_sha256=self.authorization_hash,
            replicate_id=replicate,
            run_nonce=nonce,
            invocation_start_receipt=frozen.receipt.path,
            expected_invocation_start_receipt_sha256=frozen.receipt.sha256,
            arm_contract=self.arm_paths[cell],
            expected_arm_contract_sha256=self.arm_hashes[cell],
            output=output_path,
            invocation_plan=frozen.plan.path,
            expected_invocation_plan_sha256=frozen.plan.sha256,
            signed_launch_authorization=frozen.signed_launch.path,
            expected_signed_launch_authorization_sha256=frozen.signed_launch.sha256,
            expected_master_sha256=self.master_hash,
            claim_ledger_root=self.ledger,
        )

    def test_freezer_produces_exact_r11_receipt_from_one_byte_snapshots(self) -> None:
        receipt, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="one.json",
            result_name="one",
        )
        self.assertEqual(set(receipt.receipt.value), r11.INVOCATION_KEYS)
        self.assertEqual(receipt.receipt.value["cell_id"], self.cells[0])
        self.assertEqual(receipt.receipt.value["run_nonce"], NONCE_ONE)
        self.assertEqual(
            hashlib.sha256(receipt.receipt.raw).hexdigest(), receipt.receipt.sha256
        )
        self.assertEqual(
            receipt.plan.value["expected_result_output_path"],
            str(custody.canonical_path(output)),
        )
        self.assertFalse(output.exists())

    def test_exact_signed_launch_and_receipt_replay_is_rejected(self) -> None:
        receipt, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="one.json",
            result_name="one",
        )
        self.claim(cell=self.cells[0], nonce=NONCE_ONE, receipt=receipt, output_path=output)
        with self.assertRaisesRegex(custody.R12ReplayError, "already consumed"):
            self.claim(
                cell=self.cells[0], nonce=NONCE_ONE, receipt=receipt, output_path=output
            )

    def test_claim_binds_output_path_and_rejects_swap(self) -> None:
        receipt, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="one.json",
            result_name="one",
        )
        swapped_output = self.results / "swapped" / "result.json"
        with self.assertRaisesRegex(custody.R12CustodyError, "signed launch output path mismatch"):
            self.claim(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt=receipt,
                output_path=swapped_output,
            )
        with self.assertRaisesRegex(custody.R12CustodyError, "signed launch claim ledger mismatch"):
            self.claim(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt=receipt,
                output_path=output,
                ledger_root=self.root / "custody" / "alternate-ledger",
            )
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])
        claim = self.claim(
            cell=self.cells[0], nonce=NONCE_ONE, receipt=receipt, output_path=output
        )
        with self.assertRaisesRegex(custody.R12CustodyError, "output path mismatch"):
            custody.validate_claim_record(
                claim.value,
                expected_cell_id=self.cells[0],
                expected_arm="BUG",
                expected_run_nonce=NONCE_ONE,
                expected_result_output_path=swapped_output,
                expected_authorization_sha256=self.authorization_hash,
                expected_signed_launch_sha256=receipt.signed_launch.sha256,
                expected_signed_message_sha256=(
                    custody.signed_launch_message_sha256(
                        receipt.signed_launch.value["payload"]
                    )
                ),
                expected_trusted_signer_id=TRUSTED_TEST_SIGNER_ID,
                expected_trusted_signer_fingerprint=TRUSTED_TEST_FINGERPRINT,
                expected_manifest_sha256=self.manifest_hashes[self.cells[0]],
                expected_arm_contract_sha256=self.arm_hashes[self.cells[0]],
                expected_invocation_sha256=receipt.receipt.sha256,
                expected_plan_sha256=receipt.plan.sha256,
            )

    def test_arm_and_nonce_swaps_fail_before_claim_creation(self) -> None:
        receipt, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="one.json",
            result_name="one",
        )
        with self.assertRaisesRegex(custody.R12CustodyError, "expected arm"):
            self.claim(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt=receipt,
                output_path=output,
                arm="GOLD_ONLY",
            )
        with self.assertRaisesRegex(custody.R12CustodyError, "nonce mismatch"):
            self.claim(
                cell=self.cells[0],
                nonce=NONCE_TWO,
                receipt=receipt,
                output_path=output,
            )
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])

    def test_nonce_reuse_across_cells_is_rejected(self) -> None:
        def duplicate_nonce(payload: dict) -> None:
            payload["run_nonce_by_cell"][self.cells[1]] = NONCE_ONE

        with self.assertRaisesRegex(custody.R12CustodyError, "nonces must be unique"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="duplicate-nonce.json",
                result_name="duplicate-nonce",
                mutate_signed_payload=duplicate_nonce,
            )
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])

    def test_missing_policy_and_unsigned_launch_are_rejected_before_receipt(self) -> None:
        with self.assertRaisesRegex(
            custody.R12CustodyError,
            "no externally provisioned trusted signer policy",
        ):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="no-policy.json",
                result_name="no-policy",
                trusted_signer_policy=None,
            )
        self.assertFalse((self.custody_dir / "no-policy.json").exists())

        with self.assertRaisesRegex(
            custody.R12CustodyError,
            "unsigned launch plan",
        ):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="unsigned.json",
                result_name="unsigned",
                include_signature=False,
            )
        self.assertFalse((self.custody_dir / "unsigned.json").exists())
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])

    def test_untrusted_key_and_forged_trusted_fingerprint_are_rejected(self) -> None:
        attacker_key = b"attacker-self-minted-key"
        attacker_fingerprint = hashlib.sha256(b"attacker-key-id").hexdigest()
        with self.assertRaisesRegex(
            custody.R12CustodyError,
            "fingerprint is not pinned",
        ):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="untrusted-key.json",
                result_name="untrusted-key",
                signed_fingerprint=attacker_fingerprint,
                signed_key=attacker_key,
            )

        with self.assertRaisesRegex(
            custody.R12CustodyError,
            "detached signature is invalid",
        ):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="forged-pinned-id.json",
                result_name="forged-pinned-id",
                signed_fingerprint=TRUSTED_TEST_FINGERPRINT,
                signed_key=attacker_key,
            )
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])

    def test_signed_different_master_and_excessive_ttl_are_rejected(self) -> None:
        def different_master(payload: dict) -> None:
            payload["master_inclusion_contract_sha256"] = "b" * 64

        with self.assertRaisesRegex(custody.R12CustodyError, "master hash mismatch"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="different-master.json",
                result_name="different-master",
                mutate_signed_payload=different_master,
            )

        def excessive_ttl(payload: dict) -> None:
            payload["expires_at_utc"] = "2026-08-06T12:00:00Z"

        with self.assertRaisesRegex(custody.R12CustodyError, "TTL exceeds"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="excessive-ttl.json",
                result_name="excessive-ttl",
                mutate_signed_payload=excessive_ttl,
            )

    def test_signed_start_swap_incomplete_map_and_duplicate_output_are_rejected(self) -> None:
        def swapped_start(payload: dict) -> None:
            payload["started_at_utc_by_cell"][self.cells[0]] = (
                "2026-08-05T12:00:01Z"
            )

        with self.assertRaisesRegex(custody.R12CustodyError, "start time mismatch"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="start-swap.json",
                result_name="start-swap",
                mutate_signed_payload=swapped_start,
            )

        def incomplete_nonce_map(payload: dict) -> None:
            payload["run_nonce_by_cell"].pop(r11.EXPECTED_CELL_IDS[-1])

        with self.assertRaisesRegex(custody.R12CustodyError, "nonce map cell set drift"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="incomplete-map.json",
                result_name="incomplete-map",
                mutate_signed_payload=incomplete_nonce_map,
            )

        def duplicate_output(payload: dict) -> None:
            payload["expected_result_output_path_by_cell"][self.cells[1]] = payload[
                "expected_result_output_path_by_cell"
            ][self.cells[0]]

        with self.assertRaisesRegex(custody.R12CustodyError, "output paths must be unique"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="duplicate-output.json",
                result_name="duplicate-output",
                mutate_signed_payload=duplicate_output,
            )

    def test_reformatted_valid_envelope_keeps_same_replay_namespace(self) -> None:
        frozen, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="stable-replay.json",
            result_name="stable-replay",
        )
        self.claim(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt=frozen,
            output_path=output,
        )
        reformatted_path = self.custody_dir / "stable-replay.reformatted.json"
        reformatted_path.write_text(
            json.dumps(frozen.signed_launch.value, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        reformatted_hash = hashlib.sha256(reformatted_path.read_bytes()).hexdigest()
        self.assertNotEqual(reformatted_hash, frozen.signed_launch.sha256)
        replacement_plan = dict(frozen.plan.value)
        replacement_plan["signed_launch_authorization_sha256"] = reformatted_hash
        replacement_plan_path = self.custody_dir / "stable-replay.replacement-plan.json"
        replacement_plan_hash = write_json(replacement_plan_path, replacement_plan)
        with self.assertRaisesRegex(custody.R12ReplayError, "already consumed"):
            custody.claim_invocation_once(
                invocation_receipt_path=frozen.receipt.path,
                expected_invocation_sha256=frozen.receipt.sha256,
                invocation_plan_path=replacement_plan_path,
                expected_invocation_plan_sha256=replacement_plan_hash,
                signed_launch_path=reformatted_path,
                expected_signed_launch_sha256=reformatted_hash,
                trusted_signer_policy=self.policy,
                ledger_root=self.ledger,
                expected_result_output_path=output,
                expected_run_nonce=NONCE_ONE,
                expected_arm=r11.parse_cell_id(self.cells[0])[1],
                master_path=self.master_path,
                expected_master_sha256=self.master_hash,
                authorization_path=self.authorization_path,
                expected_authorization_sha256=self.authorization_hash,
                manifest_path=self.manifest_paths[self.cells[0]],
                expected_manifest_sha256=self.manifest_hashes[self.cells[0]],
                arm_contract_path=self.arm_paths[self.cells[0]],
                expected_arm_contract_sha256=self.arm_hashes[self.cells[0]],
                selected_cell_id=self.cells[0],
                now=NOW,
            )

    def test_future_and_expired_started_at_are_rejected(self) -> None:
        future = custody.format_rfc3339_utc(NOW + timedelta(minutes=1))
        with self.assertRaisesRegex(custody.R12CustodyError, "in the future"):
            self.freeze(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt_name="future.json",
                result_name="future",
                started_at_utc=future,
            )

        old_now = NOW - timedelta(minutes=20)
        old_started = custody.format_rfc3339_utc(old_now)
        expired, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="expired.json",
            result_name="expired",
            now=old_now,
            started_at_utc=old_started,
        )
        with self.assertRaisesRegex(custody.R12CustodyError, "expired"):
            self.claim(
                cell=self.cells[0],
                nonce=NONCE_ONE,
                receipt=expired,
                output_path=output,
                now=NOW,
            )

    def test_hash_mismatch_and_non_independent_receipt_are_rejected(self) -> None:
        snapshot_path = self.assets / "snapshot.json"
        expected = write_json(snapshot_path, {"version": 1})
        write_json(snapshot_path, {"version": 2})
        with self.assertRaisesRegex(custody.R12CustodyError, "expected hash mismatch"):
            custody.read_hashed_json(snapshot_path, expected_sha256=expected)

        output = self.results / "not-independent" / "result.json"
        receipt_inside_output_dir = output.parent / "invocation.json"
        with self.assertRaisesRegex(custody.R12CustodyError, "independent directory"):
            custody.freeze_r11_invocation_receipt(
                receipt_output_path=receipt_inside_output_dir,
                expected_result_output_path=output,
                run_nonce=NONCE_ONE,
                master_path=self.master_path,
                expected_master_sha256=self.master_hash,
                authorization_path=self.authorization_path,
                expected_authorization_sha256=self.authorization_hash,
                manifest_path=self.manifest_paths[self.cells[0]],
                expected_manifest_sha256=self.manifest_hashes[self.cells[0]],
                arm_contract_path=self.arm_paths[self.cells[0]],
                expected_arm_contract_sha256=self.arm_hashes[self.cells[0]],
                selected_cell_id=self.cells[0],
                now=NOW,
            )

    def test_duplicate_keys_and_nonfinite_json_are_rejected(self) -> None:
        duplicate = self.assets / "duplicate.json"
        duplicate.write_text('{"cell":"one","cell":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(custody.R12CustodyError, "duplicate JSON key"):
            custody.read_hashed_json(duplicate)

        nonfinite = self.assets / "nonfinite.json"
        nonfinite.write_text('{"score":NaN}', encoding="utf-8")
        with self.assertRaisesRegex(custody.R12CustodyError, "non-finite JSON"):
            custody.read_hashed_json(nonfinite)

    def test_runtime_wrapper_claims_before_inner_run_and_writes_final_once(self) -> None:
        import run_same_source_bridge_r12_custody as wrapper

        frozen, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="runtime.json",
            result_name="runtime",
        )
        args = self.wrapper_args(
            frozen=frozen, output_path=output, cell=self.cells[0], nonce=NONCE_ONE
        )
        stack, arm, replicate = r11.parse_cell_id(self.cells[0])
        observed: dict[str, object] = {}

        def fake_inner(inner_args: argparse.Namespace) -> dict:
            observed["claim_count_before_inner"] = len(
                list(self.ledger.rglob("*.claim.json"))
            )
            observed["inner_output"] = inner_args.output
            inner_args.output.parent.mkdir(parents=True, exist_ok=True)
            inner_args.output.write_text("private inner artifact", encoding="utf-8")
            return {
                "bridge_schema_version": "r11-same-source-bridge-result-r1",
                "mapping_stack_id": stack,
                "arm": arm,
                "replicate_id": replicate,
                "run_nonce": NONCE_ONE,
                "execution_manifest_sha256": self.manifest_hashes[self.cells[0]],
                "r11_arm_contract_sha256": self.arm_hashes[self.cells[0]],
                "authorization_receipt_sha256": self.authorization_hash,
                "authorization_id": AUTHORIZATION_ID,
                "invocation_start_receipt_sha256": frozen.receipt.sha256,
                "development_screen_only": True,
                "formal_confirmatory": False,
                "run_status": "MVP_COMPLETED_DIAGNOSTIC_ONLY",
            }

        result, final_snapshot = wrapper.run_custodied(
            args,
            now=NOW,
            inner_runner=fake_inner,
            trusted_signer_policy=self.policy,
        )
        self.assertEqual(observed["claim_count_before_inner"], 1)
        self.assertNotEqual(custody.canonical_path(observed["inner_output"]), output)
        self.assertTrue(output.is_file())
        self.assertEqual(final_snapshot.path, custody.canonical_path(output))
        self.assertTrue(result["r12_claimed_before_model_load"])
        self.assertEqual(list(output.parent.glob(".r12-private-*")), [])

    def test_runtime_without_external_policy_never_calls_inner_runner(self) -> None:
        import run_same_source_bridge_r12_custody as wrapper

        frozen, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="runtime-no-trust.json",
            result_name="runtime-no-trust",
        )
        args = self.wrapper_args(
            frozen=frozen,
            output_path=output,
            cell=self.cells[0],
            nonce=NONCE_ONE,
        )
        observed = {"inner_called": False}

        def forbidden_inner(_inner_args: argparse.Namespace) -> dict:
            observed["inner_called"] = True
            return {}

        with self.assertRaisesRegex(
            custody.R12CustodyError,
            "no externally provisioned trusted signer policy",
        ):
            wrapper.run_custodied(
                args,
                now=NOW,
                inner_runner=forbidden_inner,
                trusted_signer_policy=None,
            )
        self.assertFalse(observed["inner_called"])
        self.assertFalse(output.exists())
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])

    def test_runtime_wrapper_postcheck_failure_leaves_no_final_and_consumes_claim(self) -> None:
        import run_same_source_bridge_r12_custody as wrapper

        frozen, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="runtime-fail.json",
            result_name="runtime-fail",
        )
        args = self.wrapper_args(
            frozen=frozen, output_path=output, cell=self.cells[0], nonce=NONCE_ONE
        )
        bad_result = {
            "bridge_schema_version": "r11-same-source-bridge-result-r1",
            "mapping_stack_id": r11.parse_cell_id(self.cells[0])[0],
            "arm": "GOLD_ONLY",
        }
        with self.assertRaisesRegex(custody.R12CustodyError, "inner result arm mismatch"):
            wrapper.run_custodied(
                args,
                now=NOW,
                inner_runner=lambda _inner_args: bad_result,
                trusted_signer_policy=self.policy,
            )
        self.assertFalse(output.exists())
        self.assertEqual(len(list(self.ledger.rglob("*.claim.json"))), 1)

    def test_runtime_wrapper_nonreentrant_guard_fails_before_claim(self) -> None:
        import run_same_source_bridge_r12_custody as wrapper

        frozen, output = self.freeze(
            cell=self.cells[0],
            nonce=NONCE_ONE,
            receipt_name="guard.json",
            result_name="guard",
        )
        args = self.wrapper_args(
            frozen=frozen, output_path=output, cell=self.cells[0], nonce=NONCE_ONE
        )
        self.assertTrue(wrapper._RUN_GUARD.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(custody.R12CustodyError, "non-reentrant"):
                wrapper.run_custodied(
                    args,
                    now=NOW,
                    trusted_signer_policy=self.policy,
                )
        finally:
            wrapper._RUN_GUARD.release()
        self.assertEqual(list(self.ledger.rglob("*.claim.json")), [])


if __name__ == "__main__":
    unittest.main()
