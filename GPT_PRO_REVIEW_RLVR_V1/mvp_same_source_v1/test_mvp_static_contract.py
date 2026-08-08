"""Portable CPU-only tests for the repaired execution contract."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mvp_static_contract as contract


REVIEW_ROOT = Path(__file__).resolve().parents[1]
REAL_ASSETS = REVIEW_ROOT / "real_assets"
HERE = Path(__file__).resolve().parent
BUILDER = REAL_ASSETS / "scripts" / "build_controlled_assets.py"
ASSET_VALIDATOR = REAL_ASSETS / "scripts" / "validate_native_controlled_assets.py"
CONFIG = HERE / "MVP_CONFIG_V1.json"
RUNNER = HERE / "run_same_source_mvp.py"
VALIDATOR = HERE / "validate_mvp_replicates_r3.py"
ADDENDUM = HERE / "MVP_DETERMINISM_ADDENDUM_R2.json"


def write_json(path: Path, value: dict) -> None:
    path.write_bytes(contract.canonical_json_bytes(value))


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.assets = cls.root / "assets"
        completed = subprocess.run(
            [sys.executable, str(BUILDER), "--output-dir", str(cls.assets)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.asset_validation = cls.root / "asset_validation.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ASSET_VALIDATOR),
                "--root",
                str(cls.assets),
                "--output",
                str(cls.asset_validation),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout + completed.stderr)

        cls.model = cls.root / "models" / contract.FROZEN_MODEL["local_path"].split("/")[-1]
        cls.model.mkdir(parents=True)
        for filename in contract.MODEL_ROOT_FILES:
            path = cls.model / filename
            if filename == "tokenizer_config.json":
                write_json(path, {"chat_template": "fixture {{ messages }}"})
            elif filename.endswith(".json"):
                write_json(path, {})
            else:
                path.write_bytes((filename + "\n").encode("ascii"))
        nested = cls.model / ".cache" / "metadata"
        nested.mkdir(parents=True)
        (nested / "receipt.json").write_bytes(b"{}\n")

        cls.runtime = {
            "python": contract.FROZEN_RUNTIME["python"],
            "torch": contract.FROZEN_RUNTIME["torch"],
            "transformers": contract.FROZEN_RUNTIME["transformers"],
            "peft": contract.FROZEN_RUNTIME["peft"],
            "accelerate": contract.FROZEN_RUNTIME["accelerate"],
            "platform": "PORTABLE_TEST_PLATFORM",
        }
        with mock.patch.object(contract, "_runtime_observed", return_value=cls.runtime):
            cls.manifest = contract.build_manifest(
                config_path=CONFIG,
                model_dir=cls.model,
                source_path=cls.assets / "REAL_SOURCE_BUNDLES_V1.jsonl",
                target_path=cls.assets / "TARGET_CALIBRATION_REAL_V1.jsonl",
                mapping_path=cls.assets / "REAL_MAPPING_STACKS_V1.jsonl",
                runner_path=RUNNER,
                validator_path=VALIDATOR,
                asset_validation_path=cls.asset_validation,
                determinism_addendum_path=ADDENDUM,
                audit_seal_path=cls.assets / "TARGET_AUDIT_SEAL_RECEIPT.json",
                action_authorization=False,
            )
        cls.manifest_sha256 = contract.sha256_bytes(
            contract.canonical_json_bytes(cls.manifest)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def paths(self) -> dict:
        return {
            "config_path": CONFIG,
            "model_dir": self.model,
            "source_path": self.assets / "REAL_SOURCE_BUNDLES_V1.jsonl",
            "target_path": self.assets / "TARGET_CALIBRATION_REAL_V1.jsonl",
            "mapping_path": self.assets / "REAL_MAPPING_STACKS_V1.jsonl",
            "runner_path": RUNNER,
            "validator_path": VALIDATOR,
            "asset_validation_path": self.asset_validation,
            "determinism_addendum_path": ADDENDUM,
            "audit_seal_path": self.assets / "TARGET_AUDIT_SEAL_RECEIPT.json",
        }

    def verify(self, manifest: dict, **overrides: object) -> None:
        arguments = {
            **self.paths(),
            "require_model_authorization": False,
            "expected_manifest_sha256": self.manifest_sha256,
        }
        arguments.update(overrides)
        previous = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
        try:
            with mock.patch.object(
                contract, "_runtime_observed", return_value=self.runtime
            ):
                contract.verify_preflight(manifest, **arguments)
        finally:
            if previous is None:
                os.environ.pop("CUBLAS_WORKSPACE_CONFIG", None)
            else:
                os.environ["CUBLAS_WORKSPACE_CONFIG"] = previous

    def authorization_fixture(self, root: Path) -> tuple[Path, Path, dict]:
        manifest_hashes = {stack: "a" * 64 for stack in contract.EXPECTED_STACK_IDS}
        manifest_hashes[self.manifest["selected_mapping_stack_id"]] = self.manifest_sha256
        config_hashes = {
            stack: self.manifest["bindings"]["config_sha256"]
            for stack in contract.EXPECTED_STACK_IDS
        }
        master = {
            "schema_version": contract.MASTER_CONTRACT_SCHEMA,
            "status": "FROZEN_EIGHT_STACK_SCREEN_AUTHORIZATION_PENDING",
            "scientific_evidence": False,
            "formal_experiment": False,
            "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
            "model_execution_performed": False,
            "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
            "required_stack_count": 8,
            "config_sha256_by_stack": config_hashes,
            "manifest_sha256_by_stack": manifest_hashes,
            "shared_runner_sha256": self.manifest["bindings"]["runner_sha256"],
            "shared_validator_sha256": self.manifest["bindings"]["validator_sha256"],
            "shared_completion_validator_sha256": contract.sha256_file(
                HERE / "validate_eight_stack_completion.py"
            ),
            "shared_static_contract_sha256": self.manifest["bindings"]["static_contract_sha256"],
            "shared_model_recursive_inventory_sha256": self.manifest["bindings"][
                "model_recursive_inventory_sha256"
            ],
            "inclusion_rule": "RUN_ALL_EIGHT_STACKS_WITHOUT_SELECTION_OR_DELETION",
            "exclusion_rule": "NO_STACK_IDENTITY_THRESHOLD_OR_HYPERPARAMETER_ADAPTATION",
            "claim_boundary": list(contract.FROZEN_CLAIM_BOUNDARY),
            "authorization": {
                "user_authorization_received": False,
                "model_actions_allowed": False,
                "sampled_rlvr_allowed": False,
            },
        }
        master_path = root / "master.json"
        write_json(master_path, master)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        receipt = {
            "schema_version": contract.AUTHORIZATION_SCHEMA,
            "status": "AUTHORIZED_BY_USER",
            "action_id": contract.AUTHORIZATION_ACTION_ID,
            "authorization_id": str(uuid.uuid4()),
            "issued_at_utc": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "master_inclusion_contract_sha256": contract.sha256_file(master_path),
            "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
            "config_sha256_by_stack": config_hashes,
            "manifest_sha256_by_stack": manifest_hashes,
            "runner_sha256": master["shared_runner_sha256"],
            "validator_sha256": master["shared_validator_sha256"],
            "completion_validator_sha256": master[
                "shared_completion_validator_sha256"
            ],
            "static_contract_sha256": master["shared_static_contract_sha256"],
            "model_recursive_inventory_sha256": master[
                "shared_model_recursive_inventory_sha256"
            ],
            "allowed_operations": [
                "tokenizer_load",
                "model_weight_load",
                "model_forward",
                "gradient",
                "optimizer_step",
                "fixed_candidate_eight_stack_diagnostic",
            ],
            "forbidden_operations": [
                "audit_row_access",
                "sampled_rlvr",
                "hyperparameter_adaptation",
                "threshold_adaptation",
                "stack_or_identity_deletion",
            ],
            "version": contract.AUTHORIZATION_VERSION,
            "expires_at_utc": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        }
        receipt_path = root / "authorization.json"
        write_json(receipt_path, receipt)
        return master_path, receipt_path, receipt

    def test_static_manifest_verifies_but_model_action_needs_external_receipt(self) -> None:
        self.verify(self.manifest)
        with self.assertRaisesRegex(contract.ContractError, "receipt"):
            self.verify(
                self.manifest,
                require_model_authorization=True,
                master_inclusion_contract_path=None,
                authorization_receipt_path=None,
            )

    def test_target_offset_mismatch_is_rejected(self) -> None:
        config = contract.read_json(CONFIG)
        config["data"]["target_rule_offsets_mod7"] = [1, 2, 3, 4, 6]
        with self.assertRaisesRegex(contract.ContractError, "target offsets"):
            contract.validate_config(config)

    def test_all_semantic_config_mutations_are_rejected(self) -> None:
        mutations = (
            lambda value: value["data"].__setitem__("mapping_stack_id", "BOGUS"),
            lambda value: value["update"].__setitem__("learning_rate", -999),
            lambda value: value["update"].__setitem__("lora_rank", 0),
            lambda value: value["update"].__setitem__("gradient_clip_norm", -1),
            lambda value: value["runtime"].__setitem__("device", "cpu"),
            lambda value: value["model"].__setitem__("revision", "fake"),
            lambda value: value["scoring"].__setitem__("candidate_score", "fake"),
            lambda value: value.__setitem__("claim_boundary", ["anything"]),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                config = contract.read_json(CONFIG)
                mutation(config)
                with self.assertRaises(contract.ContractError):
                    contract.validate_config(config)

    def test_relabeled_source_and_target_rows_are_rejected(self) -> None:
        bundles = contract.read_jsonl(self.assets / "REAL_SOURCE_BUNDLES_V1.jsonl")
        bundles[0]["rows"][0]["split_role"] = "TARGET_CALIBRATION"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            with source.open("wb") as handle:
                for bundle in bundles:
                    handle.write(contract.canonical_json_bytes(bundle))
            with self.assertRaisesRegex(contract.ContractError, "source membership"):
                contract.validate_stack_membership(
                    source,
                    self.assets / "TARGET_CALIBRATION_REAL_V1.jsonl",
                    self.assets / "REAL_MAPPING_STACKS_V1.jsonl",
                )
        targets = contract.read_jsonl(
            self.assets / "TARGET_CALIBRATION_REAL_V1.jsonl"
        )
        targets[0]["split_role"] = "SOURCE"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.jsonl"
            with target.open("wb") as handle:
                for row in targets:
                    handle.write(contract.canonical_json_bytes(row))
            with self.assertRaisesRegex(contract.ContractError, "relabeled target"):
                contract.validate_stack_membership(
                    self.assets / "REAL_SOURCE_BUNDLES_V1.jsonl",
                    target,
                    self.assets / "REAL_MAPPING_STACKS_V1.jsonl",
                )

    def test_fake_manifest_provenance_is_rejected_by_external_anchor(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["bindings"]["config_sha256"] = "0" * 64
        with self.assertRaises(contract.ContractError):
            self.verify(manifest)

    def test_recursive_inventory_rejects_addition_and_deletion(self) -> None:
        added = self.model / "nested" / "extra.json"
        added.parent.mkdir()
        added.write_bytes(b"{}\n")
        try:
            with self.assertRaises(contract.ContractError):
                self.verify(self.manifest)
        finally:
            added.unlink()
            added.parent.rmdir()
        deleted = self.model / "vocab.json"
        original = deleted.read_bytes()
        deleted.unlink()
        try:
            with self.assertRaises(contract.ContractError):
                self.verify(self.manifest)
        finally:
            deleted.write_bytes(original)

    def test_commitment_changes_on_lf_row_order_and_field_mutation(self) -> None:
        rows = [{"row_id": "a"}, {"row_id": "b"}]
        original = contract.rows_commitment(rows)
        self.assertNotEqual(
            original,
            contract.sha256_bytes(
                contract.canonical_json_bytes(rows).removesuffix(b"\n")
            ),
        )
        self.assertNotEqual(original, contract.rows_commitment(list(reversed(rows))))
        mutated = copy.deepcopy(rows)
        mutated[0]["extra"] = 1
        self.assertNotEqual(original, contract.rows_commitment(mutated))

    def test_master_contract_requires_all_eight_and_cannot_self_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs: dict[str, Path] = {}
            manifests: dict[str, Path] = {}
            for index, stack_id in enumerate(contract.EXPECTED_STACK_IDS, 1):
                config_path = root / f"config-{index}.json"
                addendum_path = root / f"addendum-{index}.json"
                manifest_path = root / f"manifest-{index}.json"
                config = contract.read_json(CONFIG)
                config["data"]["mapping_stack_id"] = stack_id
                write_json(config_path, config)
                addendum = contract.read_json(ADDENDUM)
                addendum["base_config"] = config_path.name
                addendum["base_config_sha256"] = contract.sha256_file(config_path)
                write_json(addendum_path, addendum)
                with mock.patch.object(
                    contract, "_runtime_observed", return_value=self.runtime
                ):
                    manifest = contract.build_manifest(
                        config_path=config_path,
                        model_dir=self.model,
                        source_path=self.assets / "REAL_SOURCE_BUNDLES_V1.jsonl",
                        target_path=self.assets / "TARGET_CALIBRATION_REAL_V1.jsonl",
                        mapping_path=self.assets / "REAL_MAPPING_STACKS_V1.jsonl",
                        runner_path=RUNNER,
                        validator_path=VALIDATOR,
                        asset_validation_path=self.asset_validation,
                        determinism_addendum_path=addendum_path,
                        audit_seal_path=self.assets / "TARGET_AUDIT_SEAL_RECEIPT.json",
                        action_authorization=False,
                    )
                write_json(manifest_path, manifest)
                configs[stack_id] = config_path
                manifests[stack_id] = manifest_path
            master = contract.build_master_inclusion_contract(
                configs_by_stack=configs,
                manifests_by_stack=manifests,
            )
            contract.validate_master_inclusion_contract(master)
            self.assertFalse(master["authorization"]["model_actions_allowed"])
            missing = dict(configs)
            missing.pop(contract.EXPECTED_STACK_IDS[-1])
            with self.assertRaisesRegex(contract.ContractError, "8/8"):
                contract.build_master_inclusion_contract(
                    configs_by_stack=missing,
                    manifests_by_stack=manifests,
                )
            tampered = copy.deepcopy(master)
            tampered["authorization"]["model_actions_allowed"] = True
            with self.assertRaisesRegex(contract.ContractError, "self-authorize"):
                contract.validate_master_inclusion_contract(tampered)

    def test_authorization_receipt_external_hash_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master_path, receipt_path, _ = self.authorization_fixture(Path(directory))
            with self.assertRaisesRegex(contract.ContractError, "external authorization receipt hash"):
                contract.verify_authorization_receipt(
                    receipt_path=receipt_path,
                    master_contract_path=master_path,
                    selected_stack_id=self.manifest["selected_mapping_stack_id"],
                    selected_manifest_sha256=self.manifest_sha256,
                    manifest=self.manifest,
                    expected_authorization_receipt_sha256="0" * 64,
                )

    def test_expired_authorization_receipt_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path, receipt_path, receipt = self.authorization_fixture(root)
            receipt["expires_at_utc"] = "2000-01-01T00:00:00Z"
            write_json(receipt_path, receipt)
            with self.assertRaisesRegex(contract.ContractError, "not currently valid"):
                contract.verify_authorization_receipt(
                    receipt_path=receipt_path,
                    master_contract_path=master_path,
                    selected_stack_id=self.manifest["selected_mapping_stack_id"],
                    selected_manifest_sha256=self.manifest_sha256,
                    manifest=self.manifest,
                    expected_authorization_receipt_sha256=contract.sha256_file(receipt_path),
                )

    def test_arbitrary_authorization_version_and_action_rejected(self) -> None:
        for field, value, message in (
            ("version", "ARBITRARY", "version"),
            ("action_id", "WRONG_ACTION", "exact action"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                master_path, receipt_path, receipt = self.authorization_fixture(root)
                receipt[field] = value
                write_json(receipt_path, receipt)
                with self.assertRaisesRegex(contract.ContractError, message):
                    contract.verify_authorization_receipt(
                        receipt_path=receipt_path,
                        master_contract_path=master_path,
                        selected_stack_id=self.manifest["selected_mapping_stack_id"],
                        selected_manifest_sha256=self.manifest_sha256,
                        manifest=self.manifest,
                        expected_authorization_receipt_sha256=contract.sha256_file(receipt_path),
                    )

    def test_receipt_replacement_cannot_self_promote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path, receipt_path, receipt = self.authorization_fixture(root)
            externally_held_hash = contract.sha256_file(receipt_path)
            receipt["authorization_id"] = str(uuid.uuid4())
            write_json(receipt_path, receipt)
            with self.assertRaisesRegex(contract.ContractError, "external authorization receipt hash"):
                contract.verify_authorization_receipt(
                    receipt_path=receipt_path,
                    master_contract_path=master_path,
                    selected_stack_id=self.manifest["selected_mapping_stack_id"],
                    selected_manifest_sha256=self.manifest_sha256,
                    manifest=self.manifest,
                    expected_authorization_receipt_sha256=externally_held_hash,
                )


if __name__ == "__main__":
    unittest.main()
