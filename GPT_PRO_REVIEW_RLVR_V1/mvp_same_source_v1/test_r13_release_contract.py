from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import r13_release_contract as contract


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "formal_g1_development_r1" / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
RUNTIME_REFERENCE = (
    ROOT / "formal_g1_development_r1" / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json"
)


def hashes() -> dict[str, str]:
    return {
        role: hashlib.sha256(f"r13-role:{role}".encode("ascii")).hexdigest()
        for role in contract.REQUIRED_BINDING_ROLES
    }


def test_policy(context: bytes, payload: dict) -> tuple[contract.TrustedSignerPolicy, dict]:
    key = b"unit-test-external-policy-only"
    message = context + contract.canonical_json_bytes(payload)
    signature = hashlib.sha256(key + message).digest()
    fingerprint = hashlib.sha256(key).hexdigest()

    def verify_signature(selected: bytes, observed: bytes) -> bool:
        return observed == hashlib.sha256(key + selected).digest()

    policy = contract.TrustedSignerPolicy(
        signer_id="unit-test-external-signer",
        key_fingerprint_sha256=fingerprint,
        signature_algorithm="Ed25519",
        verify_signature=verify_signature,
    )
    envelope = {
        "schema_version": contract.LAUNCH_ENVELOPE_SCHEMA,
        "payload": payload,
        "signature": {
            "algorithm": policy.signature_algorithm,
            "signer_id": policy.signer_id,
            "key_fingerprint_sha256": policy.key_fingerprint_sha256,
            "value_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    return policy, envelope


def sign_human_receipt(receipt: dict) -> tuple[dict, object]:
    key = b"unit-test-human-review-policy-only"
    selected = copy.deepcopy(receipt)
    selected["signature"] = hashlib.sha256(
        key
        + contract.HUMAN_REVIEW_CONTEXT
        + contract.canonical_json_bytes(
            {name: value for name, value in selected.items() if name != "signature"}
        )
    ).hexdigest()

    def verify_human_review_signature(
        message: bytes, signature: str, algorithm: str, key_id: str
    ) -> bool:
        return (
            algorithm == "TEST-SHA256"
            and key_id == "unit-test-human-review-key"
            and signature == hashlib.sha256(key + message).hexdigest()
        )

    return selected, verify_human_review_signature


class ProtocolAndMasterTests(unittest.TestCase):
    def protocol(self) -> dict:
        return contract.read_strict_json(PROTOCOL)

    def test_real_protocol_closes_exact_design_and_remains_non_authorizing(self) -> None:
        value = self.protocol()
        contract.validate_protocol(value)
        self.assertFalse(value["run_eligible"])
        self.assertEqual(len(contract.process_ids("MINIMAL_4_PROCESS_A")), 4)
        self.assertEqual(len(contract.process_ids("REPRODUCIBILITY_8_PROCESS_AB")), 8)

    def test_human_review_schema_and_domain_match_result_validator(self) -> None:
        path = ROOT / "formal_g1_development_r1" / "validate_r13_target_alignment_results_r1.py"
        spec = importlib.util.spec_from_file_location(
            "r13_result_validator_for_contract_schema_test", path
        )
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = validator
        spec.loader.exec_module(validator)
        self.assertEqual(contract.HUMAN_RECEIPT_SCHEMA, validator.HUMAN_RECEIPT_SCHEMA)
        self.assertEqual(contract.HUMAN_REVIEW_CONTEXT, validator.HUMAN_REVIEW_CONTEXT)
        self.assertEqual(contract.HUMAN_REQUIRED_CHECKS, validator.HUMAN_REQUIRED_CHECKS)
        self.assertEqual(contract.HUMAN_COMPLETION_RULE, validator.HUMAN_COMPLETION_RULE)
        self.assertEqual(contract.HUMAN_ASSET_BINDING_KEYS, validator.HUMAN_ASSET_BINDING_KEYS)
        self.assertEqual(
            contract.HUMAN_RECEIPT_KEYS,
            validator.HUMAN_RECEIPT_KEYS
            | {"protocol_sha256", "allowlist_receipt_sha256"},
        )

    def test_protocol_q_count_and_authorization_drift_fail(self) -> None:
        value = self.protocol()
        mutations = []
        changed = copy.deepcopy(value)
        changed["target_alignment_intervention"]["H1_SWITCHED_TARGET"]["A_TO_B"]["q_surface_by_r"]["1"] = 4
        mutations.append(changed)
        changed = copy.deepcopy(value)
        changed["execution_variants"]["MINIMAL_4_PROCESS_A"]["post_target_identity_reads"] = 239
        mutations.append(changed)
        changed = copy.deepcopy(value)
        changed["authorization"]["model_forward"] = True
        mutations.append(changed)
        changed = copy.deepcopy(value)
        changed["authorization"]["emergency_override"] = True
        mutations.append(changed)
        changed = copy.deepcopy(value)
        changed["unvalidated_execution_hook"] = "RUN"
        mutations.append(changed)
        for mutation in mutations:
            with self.assertRaises(contract.R13ContractError):
                contract.validate_protocol(mutation)

    def test_master_binds_one_variant_exact_roles_and_never_authorizes(self) -> None:
        master = contract.build_master(
            selected_variant="MINIMAL_4_PROCESS_A",
            binding_sha256_by_role=hashes(),
        )
        contract.validate_master(master)
        self.assertEqual(master["design_counts"]["technical_update_executions"], 20)
        self.assertFalse(master["model_execution_authorized"])

    def test_master_rejects_missing_role_cross_role_alias_and_flag_drift(self) -> None:
        values = hashes()
        values.pop("runner_tests")
        with self.assertRaises(contract.R13ContractError):
            contract.build_master(selected_variant="MINIMAL_4_PROCESS_A", binding_sha256_by_role=values)
        values = hashes()
        values["runner_tests"] = values["runner_core"]
        with self.assertRaises(contract.R13ContractError):
            contract.build_master(selected_variant="MINIMAL_4_PROCESS_A", binding_sha256_by_role=values)
        master = contract.build_master(selected_variant="MINIMAL_4_PROCESS_A", binding_sha256_by_role=hashes())
        master["scientific_evidence"] = True
        with self.assertRaises(contract.R13ContractError):
            contract.validate_master(master)


class ExternalReviewAndLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
        self.bindings = hashes()
        self.runtime_environment_reference = {
            "schema_version": "r13-synthetic-fully-bound-runtime-test-fixture-r1",
            "status": "SYNTHETIC_TEST_ONLY",
            "full_dependency_content_hash_bound": True,
        }
        self.bindings["runtime_environment_reference"] = contract.sha256_bytes(
            contract.canonical_json_bytes(self.runtime_environment_reference)
        )
        self.master = contract.build_master(
            selected_variant="MINIMAL_4_PROCESS_A",
            binding_sha256_by_role=self.bindings,
        )
        self.master_sha = contract.sha256_bytes(contract.canonical_json_bytes(self.master))
        self.review_receipt, self.verify_human_review_signature = (
            sign_human_receipt(self.review_payload())
        )
        self.review_receipt_sha = contract.sha256_bytes(
            contract.canonical_json_bytes(self.review_receipt)
        )
        self.consumed_nonces: set[tuple[str, str]] = set()

    def review_payload(self) -> dict:
        return {
            "schema_version": contract.HUMAN_RECEIPT_SCHEMA,
            "status": contract.HUMAN_RECEIPT_STATUS,
            "template_only": False,
            "review_completed": True,
            "decision": "PASS",
            "completion_rule": contract.HUMAN_COMPLETION_RULE,
            "required_checks": list(contract.HUMAN_REQUIRED_CHECKS),
            "reviewed_at_utc": contract.format_rfc3339_utc(self.now),
            "reviewer_name": "independent-human-reviewer",
            "reviewer_affiliation_or_role": "independent research auditor",
            "review_method": "manual comparison of all 28 matched pairs",
            "review_notes": "All required checks were independently completed.",
            "protocol_sha256": self.bindings["protocol"],
            "allowlist_receipt_sha256": self.bindings["matched_panel_allowlist_audit"],
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
            "signature_key_id": "unit-test-human-review-key",
        }

    def test_external_review_signature_and_exact_bindings_pass(self) -> None:
        receipt, verifier = sign_human_receipt(self.review_payload())
        observed = contract.validate_human_review_receipt(
            receipt,
            verify_signature=verifier,
            expected_protocol_sha256=self.bindings["protocol"],
            expected_panel_manifest_sha256=self.bindings["matched_panel_manifest"],
            expected_panel_jsonl_sha256=self.bindings["matched_panel_jsonl"],
            expected_raw_inventory_commitment_sha256=self.bindings[
                "matched_panel_raw_inventory_commitment"
            ],
            expected_allowlist_receipt_sha256=self.bindings["matched_panel_allowlist_audit"],
            now=self.now,
        )
        self.assertEqual(observed, receipt)

    def test_review_tamper_stale_or_self_authorization_fail(self) -> None:
        receipt, verifier = sign_human_receipt(self.review_payload())
        receipt["required_checks"] = receipt["required_checks"][:-1]
        with self.assertRaises(contract.R13ContractError):
            contract.validate_human_review_receipt(
                receipt,
                verify_signature=verifier,
                expected_protocol_sha256=self.bindings["protocol"],
                expected_panel_manifest_sha256=self.bindings["matched_panel_manifest"],
                expected_panel_jsonl_sha256=self.bindings["matched_panel_jsonl"],
                expected_raw_inventory_commitment_sha256=self.bindings[
                    "matched_panel_raw_inventory_commitment"
                ],
                expected_allowlist_receipt_sha256=self.bindings["matched_panel_allowlist_audit"],
                now=self.now,
            )
        payload = self.review_payload()
        payload["reviewed_at_utc"] = contract.format_rfc3339_utc(self.now - timedelta(days=31))
        receipt, verifier = sign_human_receipt(payload)
        with self.assertRaises(contract.R13ContractError):
            contract.validate_human_review_receipt(
                receipt,
                verify_signature=verifier,
                expected_protocol_sha256=self.bindings["protocol"],
                expected_panel_manifest_sha256=self.bindings["matched_panel_manifest"],
                expected_panel_jsonl_sha256=self.bindings["matched_panel_jsonl"],
                expected_raw_inventory_commitment_sha256=self.bindings[
                    "matched_panel_raw_inventory_commitment"
                ],
                expected_allowlist_receipt_sha256=self.bindings["matched_panel_allowlist_audit"],
                now=self.now,
            )

    def launch_payload(self) -> dict:
        return {
            "schema_version": contract.LAUNCH_PAYLOAD_SCHEMA,
            "status": contract.LAUNCH_STATUS,
            "action_id": contract.ACTION_ID,
            "authorization_id": str(uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")),
            "single_use_nonce": "abcdefghijklmnopqrstuvwxyzABCDEF",
            "issued_at": contract.format_rfc3339_utc(self.now - timedelta(minutes=1)),
            "expires_at": contract.format_rfc3339_utc(self.now + timedelta(minutes=9)),
            "selected_variant": "MINIMAL_4_PROCESS_A",
            "ordered_process_ids": contract.process_ids("MINIMAL_4_PROCESS_A"),
            "master_sha256": self.master_sha,
            "protocol_sha256": self.bindings["protocol"],
            "model_inventory_sha256": self.bindings["model_inventory"],
            "matched_panel_manifest_sha256": self.bindings["matched_panel_manifest"],
            "human_review_receipt_sha256": self.review_receipt_sha,
            "runner_sha256": self.bindings["runner_core"],
            "result_validator_sha256": self.bindings["result_validator"],
            "release_member_manifest_sha256": "2" * 64,
            "output_root_binding_sha256": "3" * 64,
            "allowed_operations": list(contract.ALLOWED_OPERATIONS),
            "forbidden_operations": list(contract.FORBIDDEN_OPERATIONS),
            "one_shot": True,
            "adaptive_retry": False,
            "automatic_progression": False,
            "development_only": True,
            "formal_confirmatory": False,
            "model_execution_authorized": True,
        }

    def validate_launch(
        self,
        payload: dict,
        selected_now: datetime | None = None,
        *,
        master: dict | None = None,
        human_review_receipt: dict | None = None,
        runtime_environment_reference: dict | None = None,
        dependency_closure_current: bool = True,
        provision_external_policy: bool = True,
    ) -> dict:
        launch_policy, envelope = test_policy(contract.LAUNCH_CONTEXT, payload)

        def runtime_dependency_closure_is_current(digest: str) -> bool:
            return (
                dependency_closure_current
                and digest == self.bindings["runtime_environment_reference"]
            )

        def consume_authorization_nonce(
            authorization_id: str, single_use_nonce: str
        ) -> bool:
            key = (authorization_id, single_use_nonce)
            if key in self.consumed_nonces:
                return False
            self.consumed_nonces.add(key)
            return True

        external = contract.ExternalTrustPolicy(
            launch_signer=launch_policy,
            verify_human_review_signature=self.verify_human_review_signature,
            runtime_dependency_closure_is_current=(
                runtime_dependency_closure_is_current
            ),
            consume_authorization_nonce=consume_authorization_nonce,
        )
        return contract.validate_signed_launch(
            envelope,
            trusted_policy=external if provision_external_policy else None,
            master=master or self.master,
            human_review_receipt=(
                human_review_receipt or self.review_receipt
            ),
            runtime_environment_reference=(
                runtime_environment_reference
                or self.runtime_environment_reference
            ),
            expected_member_manifest_sha256="2" * 64,
            expected_output_root_binding_sha256="3" * 64,
            now=selected_now or self.now,
        )

    def test_signed_launch_exactly_binds_selected_four_process_mvp(self) -> None:
        payload = self.launch_payload()
        self.assertEqual(self.validate_launch(payload), payload)

    def test_launch_derives_master_and_human_review_receipt_hashes(self) -> None:
        payload = self.launch_payload()
        changed_master = copy.deepcopy(self.master)
        changed_master["binding_sha256_by_role"]["runner_tests"] = "8" * 64
        contract.validate_master(changed_master)
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload, master=changed_master)

        changed_review = copy.deepcopy(self.review_receipt)
        changed_review["review_notes"] = "tampered"
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload, human_review_receipt=changed_review)

    def test_launch_requires_external_policy_dependency_closure_and_atomic_nonce(self) -> None:
        payload = self.launch_payload()
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload, provision_external_policy=False)
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload, dependency_closure_current=False)
        self.assertEqual(self.validate_launch(payload), payload)
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload)

    def test_current_real_runtime_reference_is_explicitly_launch_blocked(self) -> None:
        current = contract.read_strict_json(RUNTIME_REFERENCE, canonical=True)
        current_sha = contract.sha256_bytes(contract.canonical_json_bytes(current))
        with self.assertRaisesRegex(
            contract.R13ContractError,
            "BLOCKED_FULL_DEPENDENCY_CONTENT_BINDING_INCOMPLETE",
        ):
            contract.validate_runtime_dependency_closure_reference(
                current, current_sha
            )

    def test_launch_expiry_process_hash_and_operation_drift_fail(self) -> None:
        payload = self.launch_payload()
        with self.assertRaises(contract.R13ContractError):
            self.validate_launch(payload, self.now + timedelta(minutes=10))
        mutations = []
        changed = self.launch_payload()
        changed["ordered_process_ids"] = changed["ordered_process_ids"][:-1]
        mutations.append(changed)
        changed = self.launch_payload()
        changed["runner_sha256"] = "9" * 64
        mutations.append(changed)
        changed = self.launch_payload()
        changed["adaptive_retry"] = True
        mutations.append(changed)
        for mutation in mutations:
            with self.assertRaises(contract.R13ContractError):
                self.validate_launch(mutation)


class StrictJsonTests(unittest.TestCase):
    def test_duplicate_nonfinite_and_noncanonical_are_rejected(self) -> None:
        with self.assertRaises(contract.R13ContractError):
            contract.strict_json_bytes(b'{"x":1,"x":2}', "duplicate")
        with self.assertRaises(contract.R13ContractError):
            contract.strict_json_bytes(b'{"x":NaN}', "nonfinite")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "value.json"
            path.write_text(json.dumps({"x": 1}, indent=2), encoding="utf-8")
            with self.assertRaises(contract.R13ContractError):
                contract.read_strict_json(path, canonical=True)


if __name__ == "__main__":
    unittest.main()
