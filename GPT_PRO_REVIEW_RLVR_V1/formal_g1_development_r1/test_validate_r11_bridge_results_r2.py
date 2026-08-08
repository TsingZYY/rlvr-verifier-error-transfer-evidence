from __future__ import annotations

import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import uuid

import validate_r11_bridge_results_r2 as validator


HASH = "0" * 64
ISSUED_TEXT = "2026-08-05T00:00:00Z"
STARTED_TEXT = "2026-08-05T00:10:00Z"
CLAIMED_TEXT = "2026-08-05T00:20:00Z"
EXPIRES_TEXT = "2026-08-05T02:00:00Z"
ISSUED = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 8, 5, 0, 10, tzinfo=timezone.utc)
CLAIMED = datetime(2026, 8, 5, 0, 20, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def master_fixture() -> dict:
    project_root = Path(validator.__file__).resolve().parents[1]
    validator_hash = validator.sha256_file(Path(validator.__file__).resolve())
    local = project_root / "mvp_same_source_v1"
    return {
        "schema_version": "r11-bridge-32-cell-master-inclusion-r1",
        "status": "R11_32_CELL_FROZEN_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": validator.EVIDENCE_BOUNDARY,
        "model_execution_performed": False,
        "ordered_cell_ids": list(validator.CELL_IDS),
        "required_process_count": 32,
        "required_unique_arm_specific_design_cells": 48,
        "required_technical_update_executions": 96,
        "required_target_identity_evaluation_cells": 480,
        "config_sha256_by_stack": {
            stack: digest(f"config:{stack}") for stack in validator.STACKS
        },
        "manifest_sha256_by_cell": {
            cell: digest(f"manifest:{cell}") for cell in validator.CELL_IDS
        },
        "arm_contract_sha256_by_cell": {
            cell: digest(f"arm:{cell}") for cell in validator.CELL_IDS
        },
        "shared_runner_sha256": validator.sha256_file(
            local / "run_same_source_bridge_r11.py"
        ),
        "shared_validator_sha256": validator_hash,
        "shared_completion_validator_sha256": validator_hash,
        "shared_static_contract_sha256": validator.sha256_file(
            local / "r11_static_contract.py"
        ),
        "shared_bridge_contract_sha256": validator.sha256_file(
            local / "r11_bridge_contract.py"
        ),
        "shared_parent_r10_runner_sha256": validator.sha256_file(
            local / "run_same_source_mvp.py"
        ),
        "shared_r11_protocol_sha256": validator.sha256_file(
            project_root
            / "formal_g1_development_r1"
            / "R11_GOLD_ONLY_DID_BRIDGE_PROTOCOL_R2.json"
        ),
        "shared_model_recursive_inventory_sha256": digest("model-inventory"),
        "inclusion_rule": "RUN_ALL_32_STACK_ARM_REPLICATE_CELLS_WITHOUT_SELECTION",
        "exclusion_rule": "NO_CELL_IDENTITY_THRESHOLD_HYPERPARAMETER_RETRY_OR_OUTCOME_ADAPTATION",
        "claim_boundary": validator.R11_CLAIM_BOUNDARY,
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
    }


def summary_execution_events(arm: str) -> list[dict]:
    identities = (
        list(validator.IDENTITIES) if arm == "BUG" else ["SHARED_GOLD_CONTROL"]
    )
    return [
        {
            "execution_event_id": str(uuid.uuid4()),
            "execution_ordinal": index,
            "execution_identity": identity,
            "source_update_hash": digest(f"summary-update:{arm}:{identity}"),
            "reward_mask_sha256": digest(f"summary-mask:{arm}:{identity}"),
            "optimizer_step_count": 1,
            "target_identity_read_count": 5,
            "reused_for_source_rule_identities": (
                [identity]
                if arm == "BUG"
                else list(validator.IDENTITIES)
            ),
        }
        for index, identity in enumerate(identities, start=1)
    ]


def clean_source_image(destination: Path) -> Path:
    source_root = Path(validator.__file__).resolve().parents[1]
    for relative in validator.EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
    return destination


class StrictJsonTests(unittest.TestCase):
    def test_duplicate_key_and_nonfinite_are_rejected(self) -> None:
        for raw in (
            b'{"outer":{"x":1,"x":2}}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'{"x":-Infinity}',
        ):
            with self.subTest(raw=raw), self.assertRaises(
                validator.R11V2ValidationError
            ):
                validator.strict_json_loads(raw, "fixture")

    def test_read_hashed_json_requires_exact_canonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object.json"
            canonical = validator.canonical_json_bytes({"a": 1, "b": [2.0]})
            path.write_bytes(canonical)
            parsed, observed = validator.read_hashed_json(
                path, validator.sha256_bytes(canonical), "canonical fixture"
            )
            self.assertEqual(parsed, {"a": 1, "b": [2.0]})
            self.assertEqual(observed, validator.sha256_bytes(canonical))
            noncanonical = b'{"b":[2.0], "a":1}\n'
            path.write_bytes(noncanonical)
            with self.assertRaisesRegex(
                validator.R11V2ValidationError, "not canonical JSON"
            ):
                validator.read_hashed_json(
                    path,
                    validator.sha256_bytes(noncanonical),
                    "noncanonical fixture",
                )


class FrozenSemanticsTests(unittest.TestCase):
    def test_master_requires_shared_gold_counts_and_current_dependencies(self) -> None:
        master = master_fixture()
        with mock.patch.object(
            validator, "validate_bound_runtime_compatibility"
        ):
            validator.validate_master(master, digest("master"))
            for field, invalid in (
                ("required_unique_arm_specific_design_cells", 80),
                ("required_technical_update_executions", 160),
                ("required_target_identity_evaluation_cells", 800),
            ):
                mutated = dict(master)
                mutated[field] = invalid
                with self.subTest(field=field), self.assertRaisesRegex(
                    validator.R11V2ValidationError, "shared GOLD semantics"
                ):
                    validator.validate_master(mutated, digest("master"))

    def test_current_runner_and_reward_contract_are_explicitly_blocked(self) -> None:
        with self.assertRaisesRegex(
            validator.RuntimeSemanticsRepairRequired,
            validator.BLOCKED_RUNTIME_SEMANTICS_NOT_REFROZEN,
        ):
            validator.validate_bound_runtime_compatibility(master_fixture())

    def test_gold_mask_is_shared_while_bug_masks_are_identity_specific(self) -> None:
        semantics = [
            {
                "row_id": "row-1",
                "gold_candidate": "FINAL=K0",
                "offset_candidates": {
                    identity: f"FINAL=K{index}"
                    for index, identity in enumerate(validator.IDENTITIES, start=1)
                },
            }
        ]
        gold = {
            validator.reward_mask_commitment(semantics, "GOLD_ONLY", identity)
            for identity in validator.IDENTITIES
        }
        bug = {
            validator.reward_mask_commitment(semantics, "BUG", identity)
            for identity in validator.IDENTITIES
        }
        self.assertEqual(len(gold), 1)
        self.assertEqual(len(bug), 5)

    def test_gold_requires_one_execution_event_with_five_labeled_reuses(self) -> None:
        shared_hash = digest("shared-gold-update")
        shared_mask = digest("shared-gold-mask")
        parameter_hashes = {
            identity: shared_hash for identity in validator.IDENTITIES
        }
        updates = {
            identity: {"reward_mask_sha256": shared_mask}
            for identity in validator.IDENTITIES
        }
        event = {
            "execution_event_id": str(uuid.uuid4()),
            "execution_ordinal": 1,
            "execution_identity": "SHARED_GOLD_CONTROL",
            "source_update_hash": shared_hash,
            "reward_mask_sha256": shared_mask,
            "optimizer_step_count": 1,
            "target_identity_read_count": 5,
            "reused_for_source_rule_identities": list(validator.IDENTITIES),
        }
        count, target_reads, event_ids = validator.validate_update_execution_events(
            [event],
            arm="GOLD_ONLY",
            parameter_hashes=parameter_hashes,
            update_by_identity=updates,
        )
        self.assertEqual((count, target_reads, len(event_ids)), (1, 5, 1))
        duplicated = [dict(event), {**event, "execution_event_id": str(uuid.uuid4())}]
        with self.assertRaisesRegex(
            validator.R11V2ValidationError, "count violates shared GOLD"
        ):
            validator.validate_update_execution_events(
                duplicated,
                arm="GOLD_ONLY",
                parameter_hashes=parameter_hashes,
                update_by_identity=updates,
            )
        boolean_count = {**event, "optimizer_step_count": True}
        with self.assertRaisesRegex(
            validator.R11V2ValidationError, "optimizer-step count drift"
        ):
            validator.validate_update_execution_events(
                [boolean_count],
                arm="GOLD_ONLY",
                parameter_hashes=parameter_hashes,
                update_by_identity=updates,
            )

    def test_master_rejects_cross_cell_hash_aliases(self) -> None:
        for field, pattern in (
            ("manifest_sha256_by_cell", "aliases two cell manifests"),
            ("arm_contract_sha256_by_cell", "aliases two cell arm contracts"),
        ):
            master = master_fixture()
            selected = list(validator.CELL_IDS[:2])
            master[field][selected[1]] = master[field][selected[0]]
            with mock.patch.object(
                validator, "validate_bound_runtime_compatibility"
            ), self.subTest(field=field), self.assertRaisesRegex(
                validator.R11V2ValidationError, pattern
            ):
                validator.validate_master(master, digest("master"))

    def test_armwise_replicate_check_rejects_compensating_drift(self) -> None:
        equal_fields = set(validator.REPLICATE_EQUAL_FIELDS) | set(
            validator.CROSS_ARM_EQUAL_FIELDS
        )
        results = {
            stack: {
                arm: {
                    replicate: {
                        **{field: None for field in equal_fields},
                        "source_update_execution_events": summary_execution_events(
                            arm
                        ),
                    }
                    for replicate in validator.REPLICATES
                }
                for arm in validator.ARMS
            }
            for stack in validator.STACKS
        }
        zero = [[0.0] * 5 for _ in range(5)]
        one = [[1.0] * 5 for _ in range(5)]
        matrices = {
            stack: {
                "BUG": {"A": zero, "B": zero},
                "GOLD_ONLY": {"A": zero, "B": zero},
            }
            for stack in validator.STACKS
        }
        validator.summarize(results, matrices)
        selected = validator.STACKS[0]
        matrices[selected]["BUG"]["B"] = one
        matrices[selected]["GOLD_ONLY"]["B"] = one
        with self.assertRaisesRegex(
            validator.R11V2ValidationError, "raw replicate matrices disagree"
        ):
            validator.summarize(results, matrices)

    def test_trainable_parameter_space_cannot_drift_across_arms(self) -> None:
        equal_fields = set(validator.REPLICATE_EQUAL_FIELDS) | set(
            validator.CROSS_ARM_EQUAL_FIELDS
        )
        results = {
            stack: {
                arm: {
                    replicate: {
                        **{
                            field: (
                                1 if field == "trainable_parameters" else None
                            )
                            for field in equal_fields
                        },
                        "source_update_execution_events": summary_execution_events(
                            arm
                        ),
                    }
                    for replicate in validator.REPLICATES
                }
                for arm in validator.ARMS
            }
            for stack in validator.STACKS
        }
        matrices = {
            stack: {
                arm: {replicate: [[0.0] * 5 for _ in range(5)] for replicate in validator.REPLICATES}
                for arm in validator.ARMS
            }
            for stack in validator.STACKS
        }
        results[validator.STACKS[0]]["BUG"]["A"]["trainable_parameters"] = 2
        with self.assertRaisesRegex(
            validator.R11V2ValidationError, "drift: trainable_parameters"
        ):
            validator.summarize(results, matrices)


class InventoryTests(unittest.TestCase):
    def model_bindings(self) -> dict:
        inventory = [
            {"path": path, "sha256": sha256, "size": 1}
            for path, sha256 in sorted(
                validator.EXPECTED_MODEL_INVENTORY_HASHES.items()
            )
        ]
        return {
            "model_recursive_inventory": inventory,
            "model_recursive_inventory_sha256": validator.sha256_bytes(
                validator.canonical_json_bytes(inventory)
            ),
        }

    def test_exact_semantic_model_inventory_passes(self) -> None:
        validator.validate_model_inventory(self.model_bindings())

    def test_cache_bytecode_and_revision_hash_drift_fail(self) -> None:
        for bad_path in (".cache/huggingface/x", "__pycache__/x.pyc"):
            bindings = self.model_bindings()
            bindings["model_recursive_inventory"].append(
                {"path": bad_path, "sha256": digest(bad_path), "size": 1}
            )
            bindings["model_recursive_inventory_sha256"] = validator.sha256_bytes(
                validator.canonical_json_bytes(bindings["model_recursive_inventory"])
            )
            with self.subTest(path=bad_path), self.assertRaises(
                validator.R11V2ValidationError
            ):
                validator.validate_model_inventory(bindings)
        bindings = self.model_bindings()
        bindings["model_recursive_inventory"][0]["sha256"] = digest(
            "wrong-revision"
        )
        bindings["model_recursive_inventory_sha256"] = validator.sha256_bytes(
            validator.canonical_json_bytes(bindings["model_recursive_inventory"])
        )
        with self.assertRaisesRegex(
            validator.R11V2ValidationError, "semantic join"
        ):
            validator.validate_model_inventory(bindings)

    def test_source_dependency_inventory_is_exact_source_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = clean_source_image(Path(temporary) / "source-image")
            inventory = validator.source_dependency_inventory(source_root)
            self.assertEqual(
                list(inventory),
                list(validator.EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS),
            )
            self.assertTrue(all(path.endswith(".py") for path in inventory))
            self.assertTrue(
                all(
                    "__pycache__" not in path and not path.endswith(".pyc")
                    for path in inventory
                )
            )
            selected = source_root / "mvp_same_source_v1" / "__pycache__"
            selected.mkdir()
            (selected / "run_same_source_mvp.cpython-313.pyc").write_bytes(b"x")
            with self.assertRaisesRegex(
                validator.R11V2ValidationError, "unbound Python bytecode"
            ):
                validator.source_dependency_inventory(source_root)


class SignedLaunchTests(unittest.TestCase):
    secret = b"unit-test-secret-not-a-production-key"

    def policy(self) -> validator.TrustedSignerPolicy:
        return validator.TrustedSignerPolicy(
            signer_id="unit-test-operator",
            key_fingerprint_sha256=digest("unit-test-key"),
            signature_algorithm="HMAC-SHA256-TEST-ONLY",
            verify_signature=lambda message, signature: hmac.compare_digest(
                signature, hmac.new(self.secret, message, hashlib.sha256).digest()
            ),
        )

    def envelope(self, payload: dict) -> dict:
        message = validator.SIGNED_LAUNCH_CONTEXT + validator.canonical_json_bytes(
            payload
        )
        signature = hmac.new(self.secret, message, hashlib.sha256).digest()
        policy = self.policy()
        return {
            "schema_version": validator.SIGNED_LAUNCH_ENVELOPE_SCHEMA,
            "payload": payload,
            "signature": {
                "algorithm": policy.signature_algorithm,
                "signer_id": policy.signer_id,
                "key_fingerprint_sha256": policy.key_fingerprint_sha256,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
        }

    def fixture(self, directory: Path) -> tuple[dict, dict, dict, Path, Path]:
        run_root = directory / "run"
        run_root.mkdir()
        master_path = directory / "custody" / "master.json"
        master_path.parent.mkdir()
        master_path.write_bytes(b"{}\n")
        index_cells = {}
        outputs = {}
        nonces = {}
        starts = {}
        for position, cell in enumerate(validator.CELL_IDS):
            relative = f"results/{position:02d}.json"
            path = run_root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"{}\n")
            index_cells[cell] = {"result_path": relative}
            outputs[cell] = str(path.resolve())
            nonces[cell] = str(uuid.uuid4())
            starts[cell] = STARTED_TEXT
        master = master_fixture()
        project_root = Path(validator.__file__).resolve().parents[1]
        payload = {
            "schema_version": validator.SIGNED_LAUNCH_PAYLOAD_SCHEMA,
            "status": validator.SIGNED_LAUNCH_STATUS,
            "action_id": "RUN_R11_BUG_GOLD_DEVELOPMENT_BRIDGE_32_PROCESS_R1",
            "version": validator.SIGNED_LAUNCH_VERSION,
            "authorization_id": str(uuid.uuid4()),
            "issued_at_utc": ISSUED_TEXT,
            "expires_at_utc": EXPIRES_TEXT,
            "master_inclusion_contract_path": str(master_path.resolve()),
            "master_inclusion_contract_sha256": digest("master"),
            "r11_authorization_receipt_sha256": digest("authorization"),
            "ordered_cell_ids": list(validator.CELL_IDS),
            "manifest_sha256_by_cell": master["manifest_sha256_by_cell"],
            "arm_contract_sha256_by_cell": master[
                "arm_contract_sha256_by_cell"
            ],
            "run_nonce_by_cell": nonces,
            "expected_result_output_path_by_cell": outputs,
            "started_at_utc_by_cell": starts,
            "claim_ledger_root": str((directory / "custody" / "ledger").resolve()),
            "r12_custody_contract_sha256": validator.sha256_file(
                project_root / "mvp_same_source_v1" / "r12_invocation_custody.py"
            ),
            "r12_custody_runner_sha256": validator.sha256_file(
                project_root
                / "mvp_same_source_v1"
                / "run_same_source_bridge_r12_custody.py"
            ),
            "model_revision": validator.EXPECTED_MODEL_REVISION,
            "source_dependency_sha256_by_path": validator.source_dependency_inventory(
                clean_source_image(directory / "source-image")
            ),
            "allowed_operations": validator.ALLOWED_OPERATIONS,
            "forbidden_operations": validator.FORBIDDEN_OPERATIONS,
            "model_execution_authorized": True,
            "evidence_boundary": validator.EVIDENCE_BOUNDARY,
        }
        index = {"cells": index_cells}
        return payload, index, master, master_path, run_root

    def validate(self, payload: dict, index: dict, master: dict, master_path: Path, run_root: Path) -> None:
        authorization_id = payload["authorization_id"]
        envelope = self.envelope(payload)
        with mock.patch.object(
            validator,
            "source_dependency_inventory",
            return_value=payload["source_dependency_sha256_by_path"],
        ):
            validator.validate_signed_launch_plan(
                envelope,
                actual_sha256=digest("launch-envelope"),
                run_root=run_root,
                index=index,
                master=master,
                master_path=master_path,
                master_sha256=digest("master"),
                authorization_sha256=digest("authorization"),
                authorization_id=authorization_id,
                authorization_issued=ISSUED,
                authorization_expires=EXPIRES,
                trusted_signer_policy=self.policy(),
            )

    def test_signed_launch_binds_revision_sources_cells_and_unique_nonces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, index, master, master_path, run_root = self.fixture(
                Path(temporary)
            )
            self.validate(payload, index, master, master_path, run_root)
            for mutation, pattern in (
                (
                    lambda value: value.__setitem__("model_revision", "f" * 40),
                    "model revision drift",
                ),
                (
                    lambda value: value["source_dependency_sha256_by_path"].pop(
                        next(iter(value["source_dependency_sha256_by_path"]))
                    ),
                    "dependency inventory coverage",
                ),
                (
                    lambda value: value["run_nonce_by_cell"].__setitem__(
                        validator.CELL_IDS[1],
                        value["run_nonce_by_cell"][validator.CELL_IDS[0]],
                    ),
                    "reuses a run nonce",
                ),
            ):
                mutated = {
                    **payload,
                    "source_dependency_sha256_by_path": dict(
                        payload["source_dependency_sha256_by_path"]
                    ),
                    "run_nonce_by_cell": dict(payload["run_nonce_by_cell"]),
                }
                mutation(mutated)
                with self.subTest(pattern=pattern), self.assertRaisesRegex(
                    validator.R11V2ValidationError, pattern
                ):
                    self.validate(mutated, index, master, master_path, run_root)


class BundleOrchestrationTests(unittest.TestCase):
    def test_missing_external_trust_anchor_blocks_before_file_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                validator.ExternalTrustAnchorRequired,
                validator.BLOCKED_EXTERNAL_TRUST_ANCHOR,
            ):
                validator.validate_bundle(
                    run_root=root / "missing-run",
                    custody_root=root / "missing-custody",
                    run_index_path=root / "missing-index.json",
                    expected_run_index_sha256=HASH,
                    master_path=root / "missing-master.json",
                    expected_master_sha256=HASH,
                    authorization_path=root / "missing-authorization.json",
                    expected_authorization_sha256=HASH,
                    signed_launch_plan_path=root / "missing-launch.json",
                    expected_signed_launch_plan_sha256=HASH,
                )

    def test_full_orchestrator_counts_shared_gold_without_interface_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            custody_root = root / "custody"
            run_root.mkdir()
            custody_root.mkdir()
            index_path = run_root / "index.json"
            master_path = custody_root / "master.json"
            authorization_path = custody_root / "authorization.json"
            launch_path = custody_root / "launch.json"
            master = master_fixture()
            cells = {}
            nonces = {}
            for position, cell in enumerate(validator.CELL_IDS):
                cells[cell] = {
                    "result_path": f"results/{position}.json",
                    "result_sha256": digest(f"result:{cell}"),
                    "manifest_path": f"manifests/{position}.json",
                    "manifest_sha256": master["manifest_sha256_by_cell"][cell],
                    "arm_contract_path": f"arms/{position}.json",
                    "arm_contract_sha256": master[
                        "arm_contract_sha256_by_cell"
                    ][cell],
                    "invocation_receipt_path": f"invocations/{position}.json",
                    "invocation_receipt_sha256": digest(f"invocation:{cell}"),
                    "invocation_plan_path": f"plans/{position}.json",
                    "invocation_plan_sha256": digest(f"plan:{cell}"),
                    "consumption_claim_path": f"claims/{position}.json",
                    "consumption_claim_sha256": digest(f"claim:{cell}"),
                }
                nonces[cell] = str(uuid.uuid4())
            index = {"cells": cells}
            launch_payload = {
                "claim_ledger_root": str((custody_root / "ledger").resolve()),
                "model_revision": validator.EXPECTED_MODEL_REVISION,
                "source_dependency_sha256_by_path": {
                    relative: digest(f"source:{relative}")
                    for relative in validator.EXPECTED_SOURCE_DEPENDENCY_RELATIVE_PATHS
                },
                "run_nonce_by_cell": nonces,
                "started_at_utc_by_cell": {
                    cell: STARTED_TEXT for cell in validator.CELL_IDS
                },
            }
            authorization = {}
            launch = {}
            policy = validator.TrustedSignerPolicy(
                signer_id="unit-test-operator",
                key_fingerprint_sha256=digest("unit-test-key"),
                signature_algorithm="HMAC-SHA256-TEST-ONLY",
                verify_signature=lambda _message, _signature: True,
            )

            def read_top(_path, _expected, label):
                values = {
                    "run index": (index, digest("index")),
                    "master": (master, digest("master")),
                    "authorization receipt": (
                        authorization,
                        digest("authorization"),
                    ),
                    "signed launch plan": (launch, digest("launch")),
                }
                return values[label]

            def read_relative(selected_root, _relative, _expected, label):
                for prefix in (
                    "result ",
                    "manifest ",
                    "arm contract ",
                    "invocation plan ",
                    "invocation ",
                    "consumption claim ",
                ):
                    if label.startswith(prefix):
                        cell = label[len(prefix) :]
                        break
                else:
                    raise AssertionError(label)
                entry = cells[cell]
                stack, arm, replicate = cell.split("|")
                del stack, replicate
                if prefix == "result ":
                    update_hashes = (
                        [digest(f"update:{cell}:{identity}") for identity in validator.IDENTITIES]
                        if arm == "BUG"
                        else [digest(f"shared-gold:{cell}")] * 5
                    )
                    value = {
                        "source_updates": [
                            {
                                "source_rule_identity": identity,
                                "source_update_hash": update_hash,
                            }
                            for identity, update_hash in zip(
                                validator.IDENTITIES, update_hashes
                            )
                        ],
                        "source_update_execution_events": [
                            {
                                "execution_event_id": str(uuid.uuid4()),
                                "target_identity_read_count": 5,
                            }
                            for _ in range(5 if arm == "BUG" else 1)
                        ],
                        "evaluation_cells": [{} for _ in range(25)],
                    }
                    sha = entry["result_sha256"]
                    path = run_root / entry["result_path"]
                elif prefix == "manifest ":
                    value, sha, path = {}, entry["manifest_sha256"], custody_root / entry["manifest_path"]
                elif prefix == "arm contract ":
                    value, sha, path = {}, entry["arm_contract_sha256"], custody_root / entry["arm_contract_path"]
                elif prefix == "invocation ":
                    value = {"run_nonce": nonces[cell], "started_at_utc": STARTED_TEXT}
                    sha, path = entry["invocation_receipt_sha256"], custody_root / entry["invocation_receipt_path"]
                elif prefix == "invocation plan ":
                    value, sha, path = {}, entry["invocation_plan_sha256"], custody_root / entry["invocation_plan_path"]
                else:
                    value = {"claim_key_sha256": digest(f"claim-key:{cell}")}
                    sha, path = entry["consumption_claim_sha256"], custody_root / entry["consumption_claim_path"]
                return value, sha, path.resolve()

            shared_bindings = {
                field: digest(f"binding:{field}")
                for field in (
                    "asset_validation_sha256",
                    "chat_template_sha256",
                    "mapping_stacks_sha256",
                    "source_bundles_sha256",
                    "target_calibration_sha256",
                    "model_recursive_inventory_sha256",
                    "runner_sha256",
                    "validator_sha256",
                    "static_contract_sha256",
                    "r11_bridge_contract_sha256",
                    "parent_r10_runner_sha256",
                    "r11_protocol_sha256",
                )
            }
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(validator, "read_hashed_json", side_effect=read_top))
                stack.enter_context(mock.patch.object(validator, "read_relative_json", side_effect=read_relative))
                stack.enter_context(mock.patch.object(validator, "validate_master"))
                stack.enter_context(mock.patch.object(validator, "validate_authorization", return_value=(str(uuid.uuid4()), ISSUED, EXPIRES)))
                stack.enter_context(mock.patch.object(validator, "validate_run_index"))
                stack.enter_context(mock.patch.object(validator, "validate_signed_launch_plan", return_value=(launch_payload, digest("launch-message"), ISSUED, EXPIRES)))
                stack.enter_context(mock.patch.object(validator, "validate_manifest", return_value=shared_bindings))
                stack.enter_context(mock.patch.object(validator, "validate_arm_contract"))
                stack.enter_context(mock.patch.object(validator, "validate_invocation", side_effect=lambda value, **_kwargs: (value["run_nonce"], STARTED)))
                stack.enter_context(mock.patch.object(validator, "validate_invocation_plan", return_value=(EXPIRES, digest("r12-contract"), digest("r12-runner"))))
                stack.enter_context(mock.patch.object(validator, "validate_consumption_claim", return_value=CLAIMED))
                stack.enter_context(mock.patch.object(validator, "validate_result", return_value=[[0.0] * 5 for _ in range(5)]))
                stack.enter_context(mock.patch.object(validator, "summarize", return_value={"checked": True}))
                report = validator.validate_bundle(
                    run_root=run_root,
                    custody_root=custody_root,
                    run_index_path=index_path,
                    expected_run_index_sha256=digest("index"),
                    master_path=master_path,
                    expected_master_sha256=digest("master"),
                    authorization_path=authorization_path,
                    expected_authorization_sha256=digest("authorization"),
                    signed_launch_plan_path=launch_path,
                    expected_signed_launch_plan_sha256=digest("launch"),
                    trusted_signer_policy=policy,
                )
            self.assertEqual(report["process_results_checked"], 32)
            self.assertEqual(report["unique_arm_specific_design_cells"], 48)
            self.assertEqual(report["technical_update_executions"], 96)
            self.assertEqual(report["target_identity_evaluation_cells"], 480)
            self.assertEqual(
                report["labeled_target_references_including_shared_gold_reuse"],
                800,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
