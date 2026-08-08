from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
import uuid

import r13_launch_result_authorization_bridge_r1 as bridge_contract
from formal_g1_development_r1 import validate_r13_target_alignment_results_r1 as validator
from mvp_same_source_v1 import r13_release_contract as release


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class SyntheticAtomicLedger:
    """Unit-test-only state machine; it is not a production trust backend."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str], tuple[str, str, str]] = {}
        self.launch_claim_calls = 0
        self.result_claim_calls = 0

    def claim_launch(
        self, authorization_id: str, nonce: str, run_id: str, batch_sha256: str
    ) -> bool:
        self.launch_claim_calls += 1
        key = (authorization_id, nonce)
        if key in self.states:
            return False
        self.states[key] = ("START_CLAIMED", run_id, batch_sha256)
        return True

    def claim_result_validation(
        self, authorization_id: str, nonce: str, run_id: str, batch_sha256: str
    ) -> bool:
        self.result_claim_calls += 1
        key = (authorization_id, nonce)
        if self.states.get(key) != ("START_CLAIMED", run_id, batch_sha256):
            return False
        self.states[key] = ("RESULT_VALIDATION_CLAIMED", run_id, batch_sha256)
        return True


class LaunchResultAuthorizationBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.run_id = str(uuid.UUID("11111111-2222-4333-8444-555555555555"))
        self.authorization_id = str(
            uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        )
        self.nonce = "abcdefghijklmnopqrstuvwxyzABCDEF"
        self.member_manifest_sha256 = digest("member-manifest")
        self.output_root_sha256 = digest("output-root")
        self.runtime_reference = {
            "schema_version": "synthetic-test-runtime-reference-r1",
            "full_dependency_content_hash_bound": True,
        }
        self.bindings = {
            role: digest(f"role:{role}") for role in release.REQUIRED_BINDING_ROLES
        }
        self.bindings["runtime_environment_reference"] = digest(
            "placeholder-runtime"
        )
        self.bindings["runtime_environment_reference"] = release.sha256_bytes(
            release.canonical_json_bytes(self.runtime_reference)
        )
        self.master = release.build_master(
            selected_variant="MINIMAL_4_PROCESS_A",
            binding_sha256_by_role=self.bindings,
        )
        self.master_sha256 = release.sha256_bytes(
            release.canonical_json_bytes(self.master)
        )
        self.human_key = b"synthetic-unit-test-human-key"
        self.human_review = self._signed_human_review()
        self.human_review_sha256 = release.sha256_bytes(
            release.canonical_json_bytes(self.human_review)
        )
        self.launch_key = b"synthetic-unit-test-launch-key"
        self.launch_messages: list[bytes] = []
        self.launch_signer = release.TrustedSignerPolicy(
            signer_id="synthetic-bridge-test-launch-signer",
            key_fingerprint_sha256=digest("synthetic-launch-public-key"),
            signature_algorithm="TEST-SHA256",
            verify_signature=self._verify_launch_signature,
        )
        self.launch_payload = self._launch_payload()
        self.envelope = self._signed_launch_envelope(self.launch_payload)
        self.ledger = SyntheticAtomicLedger()
        self.external = bridge_contract.ExternalBridgeTrustPolicy(
            launch_signer=self.launch_signer,
            verify_human_review_signature=self._verify_human_review_signature,
            runtime_dependency_closure_is_current=(
                lambda observed: observed
                == self.bindings["runtime_environment_reference"]
            ),
            atomic_claim_launch=self.ledger.claim_launch,
            atomic_claim_result_validation=self.ledger.claim_result_validation,
        )

    def _signed_human_review(self) -> dict:
        receipt = {
            "schema_version": release.HUMAN_RECEIPT_SCHEMA,
            "status": release.HUMAN_RECEIPT_STATUS,
            "template_only": False,
            "review_completed": True,
            "decision": "PASS",
            "completion_rule": release.HUMAN_COMPLETION_RULE,
            "required_checks": list(release.HUMAN_REQUIRED_CHECKS),
            "reviewed_at_utc": release.format_rfc3339_utc(
                self.now - timedelta(hours=1)
            ),
            "reviewer_name": "Synthetic Unit Test Reviewer",
            "reviewer_affiliation_or_role": "TEST ONLY",
            "review_method": "SYNTHETIC UNIT TEST",
            "review_notes": "Not a production review or trust assertion.",
            "protocol_sha256": self.bindings["protocol"],
            "allowlist_receipt_sha256": self.bindings[
                "matched_panel_allowlist_audit"
            ],
            "asset_bindings": {
                "manifest_relpath": "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json",
                "manifest_sha256": self.bindings["matched_panel_manifest"],
                "panel_jsonl_relpath": "R13_MATCHED_TARGET_PANELS_R1.jsonl",
                "panel_jsonl_sha256": self.bindings["matched_panel_jsonl"],
                "raw_inventory_commitment_sha256": self.bindings[
                    "matched_panel_raw_inventory_commitment"
                ],
            },
            "signature": "",
            "signature_algorithm": "TEST-SHA256",
            "signature_key_id": "synthetic-human-test-key",
        }
        message = release.HUMAN_REVIEW_CONTEXT + release.canonical_json_bytes(
            {key: value for key, value in receipt.items() if key != "signature"}
        )
        receipt["signature"] = hashlib.sha256(self.human_key + message).hexdigest()
        return receipt

    def _verify_human_review_signature(
        self, message: bytes, signature: str, algorithm: str, key_id: str
    ) -> bool:
        return (
            algorithm == "TEST-SHA256"
            and key_id == "synthetic-human-test-key"
            and signature == hashlib.sha256(self.human_key + message).hexdigest()
        )

    def _verify_launch_signature(self, message: bytes, signature: bytes) -> bool:
        self.launch_messages.append(message)
        return signature == hashlib.sha256(self.launch_key + message).digest()

    def _launch_payload(self) -> dict:
        return {
            "schema_version": release.LAUNCH_PAYLOAD_SCHEMA,
            "status": release.LAUNCH_STATUS,
            "action_id": release.ACTION_ID,
            "authorization_id": self.authorization_id,
            "single_use_nonce": self.nonce,
            "issued_at": release.format_rfc3339_utc(
                self.now - timedelta(minutes=1)
            ),
            "expires_at": release.format_rfc3339_utc(
                self.now + timedelta(minutes=9)
            ),
            "selected_variant": "MINIMAL_4_PROCESS_A",
            "ordered_process_ids": release.process_ids("MINIMAL_4_PROCESS_A"),
            "master_sha256": self.master_sha256,
            "protocol_sha256": self.bindings["protocol"],
            "model_inventory_sha256": self.bindings["model_inventory"],
            "matched_panel_manifest_sha256": self.bindings[
                "matched_panel_manifest"
            ],
            "human_review_receipt_sha256": self.human_review_sha256,
            "runner_sha256": self.bindings["runner_core"],
            "result_validator_sha256": self.bindings["result_validator"],
            "release_member_manifest_sha256": self.member_manifest_sha256,
            "output_root_binding_sha256": self.output_root_sha256,
            "allowed_operations": list(release.ALLOWED_OPERATIONS),
            "forbidden_operations": list(release.FORBIDDEN_OPERATIONS),
            "one_shot": True,
            "adaptive_retry": False,
            "automatic_progression": False,
            "development_only": True,
            "formal_confirmatory": False,
            "model_execution_authorized": True,
        }

    def _signed_launch_envelope(self, payload: dict) -> dict:
        message = release.LAUNCH_CONTEXT + release.canonical_json_bytes(payload)
        signature = hashlib.sha256(self.launch_key + message).digest()
        return {
            "schema_version": release.LAUNCH_ENVELOPE_SCHEMA,
            "payload": copy.deepcopy(payload),
            "signature": {
                "algorithm": self.launch_signer.signature_algorithm,
                "signer_id": self.launch_signer.signer_id,
                "key_fingerprint_sha256": (
                    self.launch_signer.key_fingerprint_sha256
                ),
                "value_base64": base64.b64encode(signature).decode("ascii"),
            },
        }

    def build_bridge(
        self,
        *,
        envelope: dict | None = None,
        policy: bridge_contract.ExternalBridgeTrustPolicy | None | object = ..., 
        run_id: str | None = None,
    ) -> bridge_contract.AuthorizationBridge:
        selected_policy = self.external if policy is ... else policy
        return bridge_contract.bridge_signed_launch_to_result_authorization(
            envelope or self.envelope,
            run_id=run_id or self.run_id,
            trusted_policy=selected_policy,  # type: ignore[arg-type]
            master=self.master,
            human_review_receipt=self.human_review,
            runtime_environment_reference=self.runtime_reference,
            expected_member_manifest_sha256=self.member_manifest_sha256,
            expected_output_root_binding_sha256=self.output_root_sha256,
            now=self.now,
        )

    def test_projection_passes_frozen_validator_authorization_gate(self) -> None:
        bridged = self.build_bridge()
        key = (self.authorization_id, self.nonce)
        self.assertEqual(
            self.ledger.states[key],
            ("START_CLAIMED", self.run_id, bridged.batch_commitment_sha256),
        )
        observed = validator.validate_authorization(
            bridged.validator_authorization,
            expected_bindings=bridged.validator_expected_bindings,
            run_id=self.run_id,
            selected_variant="MINIMAL_4_PROCESS_A",
            ordered_process_ids=release.process_ids("MINIMAL_4_PROCESS_A"),
            created_at=self.now,
            synthetic_test_mode=False,
            trusted_policy=bridged.validator_trusted_policy,
        )
        self.assertEqual(observed[0], self.now - timedelta(minutes=1))
        self.assertEqual(
            self.ledger.states[key],
            (
                "RESULT_VALIDATION_CLAIMED",
                self.run_id,
                bridged.batch_commitment_sha256,
            ),
        )

    def test_result_validation_replay_fails_after_second_atomic_claim(self) -> None:
        bridged = self.build_bridge()
        arguments = {
            "expected_bindings": bridged.validator_expected_bindings,
            "run_id": self.run_id,
            "selected_variant": "MINIMAL_4_PROCESS_A",
            "ordered_process_ids": release.process_ids("MINIMAL_4_PROCESS_A"),
            "created_at": self.now,
            "synthetic_test_mode": False,
            "trusted_policy": bridged.validator_trusted_policy,
        }
        validator.validate_authorization(bridged.validator_authorization, **arguments)
        with self.assertRaisesRegex(validator.R13ValidationError, "nonce gate"):
            validator.validate_authorization(
                bridged.validator_authorization, **arguments
            )
        self.assertEqual(self.ledger.result_claim_calls, 2)

    def test_launch_replay_fails_without_minting_a_second_projection(self) -> None:
        self.build_bridge()
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError,
            "replay or atomic nonce consumption failed",
        ):
            self.build_bridge()
        self.assertEqual(self.ledger.launch_claim_calls, 2)

    def test_original_signature_context_is_reverified_not_reinterpreted(self) -> None:
        bridged = self.build_bridge()
        authorization = bridged.validator_authorization
        flat_message = (
            bridge_contract.RESULT_AUTHORIZATION_CONTEXT
            + bridge_contract.canonical_json_bytes(
                {
                    key: value
                    for key, value in authorization.items()
                    if key != "signature"
                }
            )
        )
        self.assertTrue(
            bridged.validator_trusted_policy.verify_authorization_signature(
                flat_message, authorization["signature"]
            )
        )
        self.assertGreaterEqual(len(self.launch_messages), 2)
        self.assertTrue(
            all(
                message.startswith(release.LAUNCH_CONTEXT)
                for message in self.launch_messages
            )
        )
        self.assertFalse(
            bridged.validator_trusted_policy.verify_authorization_signature(
                flat_message + b"tamper", authorization["signature"]
            )
        )

    def test_flat_projection_tamper_does_not_inherit_launch_signature(self) -> None:
        bridged = self.build_bridge()
        changed = copy.deepcopy(bridged.validator_authorization)
        changed["run_id"] = str(uuid.UUID("99999999-8888-4777-8666-555555555555"))
        with self.assertRaisesRegex(
            validator.R13ValidationError, "signature verification failed"
        ):
            validator.validate_authorization(
                changed,
                expected_bindings=bridged.validator_expected_bindings,
                run_id=changed["run_id"],
                selected_variant="MINIMAL_4_PROCESS_A",
                ordered_process_ids=release.process_ids("MINIMAL_4_PROCESS_A"),
                created_at=self.now,
                synthetic_test_mode=False,
                trusted_policy=bridged.validator_trusted_policy,
            )
        self.assertEqual(self.ledger.result_claim_calls, 0)

    def test_tampered_launch_fails_before_atomic_claim(self) -> None:
        changed = copy.deepcopy(self.envelope)
        changed["payload"]["selected_variant"] = "REPRODUCIBILITY_8_PROCESS_AB"
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError,
            "external signature verification failed",
        ):
            self.build_bridge(envelope=changed)
        self.assertEqual(self.ledger.launch_claim_calls, 0)
        self.assertEqual(self.ledger.states, {})

    def test_missing_external_bridge_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError,
            "BLOCKED_EXTERNAL_BRIDGE_TRUST_POLICY_NOT_PROVISIONED",
        ):
            self.build_bridge(policy=None)
        self.assertEqual(self.ledger.launch_claim_calls, 0)

    def test_invalid_run_id_fails_before_signature_or_ledger(self) -> None:
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError, "run id is not UUIDv4"
        ):
            self.build_bridge(run_id="not-a-run-id")
        self.assertEqual(self.launch_messages, [])
        self.assertEqual(self.ledger.launch_claim_calls, 0)

    def test_binding_and_operation_projection_is_exact(self) -> None:
        bridged = self.build_bridge()
        expected = {
            "protocol_sha256": self.bindings["protocol"],
            "panel_bundle_sha256": self.bindings["matched_panel_manifest"],
            "allowlist_receipt_sha256": self.bindings[
                "matched_panel_allowlist_audit"
            ],
            "human_review_receipt_sha256": self.human_review_sha256,
            "runner_sha256": self.bindings["runner_core"],
            "validator_sha256": self.bindings["result_validator"],
            "model_inventory_sha256": self.bindings["model_inventory"],
            "source_assets_sha256": self.bindings["source_bundles"],
        }
        self.assertEqual(bridged.validator_authorization["bindings"], expected)
        self.assertEqual(
            bridged.validator_authorization["allowed_operations"],
            list(bridge_contract.VALIDATOR_ALLOWED_OPERATIONS),
        )
        self.assertEqual(
            set(bridged.validator_authorization["forbidden_operations"]),
            set(bridge_contract.VALIDATOR_FORBIDDEN_OPERATIONS),
        )
        self.assertEqual(
            bridged.validator_expected_bindings["authorization_sha256"],
            bridge_contract.sha256_bytes(
                bridge_contract.canonical_json_bytes(
                    bridged.validator_authorization
                )
            ),
        )

    def test_sealed_batch_allows_serial_execution_but_withholds_results(self) -> None:
        bridged = self.build_bridge()
        batch = bridged.batch_commitment
        self.assertTrue(batch["physical_serial_execution_allowed"])
        self.assertTrue(batch["all_cells_frozen_before_first_model_action"])
        self.assertFalse(batch["intermediate_scientific_result_release_allowed"])
        self.assertFalse(batch["result_dependent_change_to_later_cells_allowed"])
        self.assertFalse(batch["same_nonce_retry_allowed"])
        self.assertIn("ABORT_AND_BURN_AUTHORIZATION", batch["technical_failure_disposition"])
        self.assertTrue(
            bridged.audit["authorization_schema_and_context_compatibility_closed"]
        )
        self.assertFalse(
            bridged.audit["batch_commitment_directly_signed_in_launch_payload"]
        )
        self.assertFalse(bridged.audit["production_result_validation_chain_closed"])
        self.assertFalse(bridged.audit["scientific_result_release_authorized"])
        self.assertEqual(len(bridged.audit["unresolved_p0"]), 5)

    def test_scientific_result_release_remains_blocked_without_receipts(self) -> None:
        bridged = self.build_bridge()
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError,
            "BLOCKED_EXECUTION_AND_BATCH_RELEASE_CHAIN_INCOMPLETE",
        ):
            bridge_contract.assert_scientific_result_release_authorized(bridged)

    def test_external_atomic_claim_exception_fails_closed(self) -> None:
        external = bridge_contract.ExternalBridgeTrustPolicy(
            launch_signer=self.launch_signer,
            verify_human_review_signature=self._verify_human_review_signature,
            runtime_dependency_closure_is_current=lambda observed: True,
            atomic_claim_launch=lambda *args: (_ for _ in ()).throw(
                RuntimeError("synthetic ledger outage")
            ),
            atomic_claim_result_validation=self.ledger.claim_result_validation,
        )
        with self.assertRaisesRegex(
            bridge_contract.R13AuthorizationBridgeError,
            "replay or atomic nonce consumption failed",
        ):
            self.build_bridge(policy=external)

    def test_result_claim_binding_mismatch_fails_closed(self) -> None:
        bridged = self.build_bridge()
        self.ledger.states[(self.authorization_id, self.nonce)] = (
            "START_CLAIMED",
            self.run_id,
            digest("wrong-batch"),
        )
        with self.assertRaisesRegex(validator.R13ValidationError, "nonce gate"):
            validator.validate_authorization(
                bridged.validator_authorization,
                expected_bindings=bridged.validator_expected_bindings,
                run_id=self.run_id,
                selected_variant="MINIMAL_4_PROCESS_A",
                ordered_process_ids=release.process_ids("MINIMAL_4_PROCESS_A"),
                created_at=self.now,
                synthetic_test_mode=False,
                trusted_policy=bridged.validator_trusted_policy,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
