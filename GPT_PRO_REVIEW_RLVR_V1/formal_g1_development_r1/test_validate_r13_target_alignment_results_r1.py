from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
import uuid


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
PROTOCOL_PATH = HERE / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
RUNNER_PATH = PROJECT / "mvp_same_source_v1" / "r13_runner_core.py"

import validate_r13_target_alignment_results_r1 as validator

spec = importlib.util.spec_from_file_location("r13_runner_core_for_validator_test", RUNNER_PATH)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def digest(label: str) -> str:
    return validator.sha256_bytes(label.encode("utf-8"))


def load_protocol() -> dict:
    return validator.strict_json_loads(PROTOCOL_PATH.read_bytes(), "protocol")


def make_row(stack: str, arm: str, z: int, protocol: dict) -> validator.PanelRecord:
    direction = validator.direction_for_stack(stack)
    spec = protocol["target_alignment_intervention"][arm][direction]
    a, b = validator.parse_affine_formula(spec["target_codebook"], "fixture")
    mapping = validator.codebook_mapping(a, b)
    shared_z = (z + 1) % 7
    local_z = (z + 2) % 7
    row_id = f"fixture-{stack}-{0 if arm == validator.ARMS[0] else 1}-z{z}"
    prompt = (
        f"TASK=FIXTURE_{stack}\n"
        + validator._codebook_line(mapping)
        + "\nCANDIDATES=FINAL=K0|FINAL=K1|FINAL=K2|FINAL=K3|FINAL=K4|FINAL=K5|FINAL=K6\n"
    )
    gold = mapping[z]
    shared = mapping[shared_z]
    local = mapping[local_z]
    row = {
        "candidate_order": list(validator.CANDIDATES),
        "candidate_records": [
            {
                "candidate": candidate,
                "candidate_index": index,
                "is_gold": candidate == gold,
                "is_shared_wrong": candidate == shared,
                "is_task_local_wrong": candidate == local,
            }
            for index, candidate in enumerate(validator.CANDIDATES)
        ],
        "canonical_z": z,
        "codebook": {
            "codebook_id": f"fixture-{direction}-{arm}",
            "formula": spec["target_codebook"],
            "intercept_mod7": b,
            "latent_to_candidate": mapping,
            "multiplier_mod7": a,
            "task_slot": "B" if direction == "A_TO_B" else "A",
        },
        "gold_candidate": gold,
        "lineage_id": digest(row_id),
        "local_bug_candidate": local,
        "local_bug_z": local_z,
        "mapping_stack_id": stack,
        "mirror_role": direction,
        "panel_role": "CALIBRATION_ONLY",
        "prompt_text": prompt,
        "row_id": row_id,
        "shared_bug_candidate": shared,
        "shared_bug_z": shared_z,
        "split_role": "TARGET_CALIBRATION",
        "task_id": f"FIXTURE_{stack}",
        "task_input": {"z": z},
    }
    row_bytes = validator.canonical_json_bytes(row)
    prompt_bytes = prompt.encode("utf-8")
    return validator.PanelRecord(
        stack_id=stack,
        arm=arm,
        canonical_z=z,
        row_id=row_id,
        row_sha256=validator.sha256_bytes(row_bytes),
        prompt_sha256=validator.sha256_bytes(prompt_bytes),
        row=row,
        row_bytes=row_bytes,
        prompt_bytes=prompt_bytes,
    )


class FakeBackend:
    def __init__(self, initial_hash: str, rows_by_id: dict[str, validator.PanelRecord], protocol: dict, stack: str):
        self.initial_hash = initial_hash
        self.current_hash = initial_hash
        self.rows_by_id = rows_by_id
        self.protocol = protocol
        self.stack = stack

    def reset_trainable(self) -> None:
        self.current_hash = self.initial_hash

    def parameter_hash(self) -> str:
        return self.current_hash

    def update_source(self, request):
        self.current_hash = digest(f"update:{request.stack_id}:{request.source_rule_identity}")
        return runner.SourceUpdateResult(
            optimizer_step_count=1,
            clipping_triggered=False,
            non_finite_observed=False,
            diagnostics={"raw_gradient_norm": 0.5},
        )

    def read_target(self, request):
        rows = []
        for row_id in request.ordered_row_ids:
            record = self.rows_by_id[row_id]
            scores = [0.0] * 7
            if request.phase == "POST":
                r = int(request.source_rule_identity.removeprefix("Z7_PLUS"))
                q = validator.q_alignment(self.protocol, self.stack, request.arm)[r]
                candidate = validator._target_candidate(record.row, q)
                scores[validator.CANDIDATES.index(candidate)] = 2.0
            rows.append(runner.RawCandidateRow(row_id=row_id, ordered_candidate_scores=tuple(scores)))
        return runner.TargetReadResult(
            rows=tuple(rows),
            gradient_count=0,
            optimizer_step_count=0,
            state_mutation_count=0,
            non_finite_observed=False,
        )


def human_template(panel_bundle_sha: str, panel_jsonl_sha: str, raw_sha: str) -> dict:
    return {
        "asset_bindings": {
            "manifest_relpath": "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json",
            "manifest_sha256": panel_bundle_sha,
            "panel_jsonl_relpath": "R13_MATCHED_TARGET_PANELS_R1.jsonl",
            "panel_jsonl_sha256": panel_jsonl_sha,
            "raw_inventory_commitment_sha256": raw_sha,
        },
        "completion_rule": validator.HUMAN_COMPLETION_RULE,
        "decision": None,
        "required_checks": list(validator.HUMAN_REQUIRED_CHECKS),
        "review_completed": False,
        "review_method": None,
        "review_notes": None,
        "reviewed_at_utc": None,
        "reviewer_affiliation_or_role": None,
        "reviewer_name": None,
        "schema_version": "r13-matched-target-panel-human-review-receipt-template-r1",
        "signature": None,
        "signature_algorithm": None,
        "signature_key_id": None,
        "status": "UNCOMPLETED_TEMPLATE_NOT_A_REVIEW_RECEIPT",
        "template_only": True,
    }


def build_fixture(variant: str = "REPRODUCIBILITY_8_PROCESS_AB") -> dict:
    protocol = load_protocol()
    records = [
        make_row(stack, arm, z, protocol)
        for stack in validator.STACKS
        for arm in validator.ARMS
        for z in range(7)
    ]
    panels, panel_hashes = validator.validate_matched_panels(records, protocol)
    panel_bundle_sha = digest("synthetic-panel-manifest")
    panel_jsonl_sha = digest("synthetic-panel-jsonl")
    raw_sha = digest("synthetic-raw-inventory")
    protocol_sha = validator.sha256_file(PROTOCOL_PATH)
    human = human_template(panel_bundle_sha, panel_jsonl_sha, raw_sha)
    human_sha = validator.sha256_bytes(validator.canonical_json_bytes(human))
    allowlist = {
        "authorization_boundary": "Machine validation only; no model authority.",
        "bindings": {
            "human_review_template_sha256": human_sha,
            "manifest_sha256": panel_bundle_sha,
            "panel_jsonl_sha256": panel_jsonl_sha,
            "protocol_sha256": protocol_sha,
            "raw_inventory_commitment_sha256": raw_sha,
            "source_panel_sha256": digest("source-panel"),
        },
        "human_review_completed": False,
        "implementation_bindings": {
            "generator_relpath": "r13_matched_panels_generate_r1.py",
            "generator_sha256": validator.sha256_file(
                HERE / "r13_matched_panels_generate_r1.py"
            ),
            "validator_relpath": "r13_matched_panels_validate_r1.py",
            "validator_sha256": validator.sha256_file(
                HERE / "r13_matched_panels_validate_r1.py"
            ),
        },
        "model_actions": 0,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "run_eligible": False,
        "schema_version": validator.ALLOWLIST_RECEIPT_SCHEMA,
        "scientific_evidence": False,
        "validated_pairs": 28,
        "validated_raw_files": 112,
        "validated_rows": 56,
        "validated_stacks": 4,
        "verdict": "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY",
    }
    allowlist_sha = validator.sha256_bytes(validator.canonical_json_bytes(allowlist))
    bundle_run_id = "00000000-0000-4000-8000-000000000013"
    process_ids = validator.expected_process_ids(variant)
    partial_bindings = {
        "protocol_sha256": protocol_sha,
        "panel_bundle_sha256": panel_bundle_sha,
        "allowlist_receipt_sha256": allowlist_sha,
        "human_review_receipt_sha256": human_sha,
        "runner_sha256": validator.sha256_file(RUNNER_PATH),
        "validator_sha256": validator.sha256_file(Path(validator.__file__)),
        "model_inventory_sha256": digest("synthetic-model-inventory"),
        "source_assets_sha256": digest("synthetic-source-assets"),
    }
    authorization = {
        "schema_version": validator.AUTHORIZATION_SCHEMA,
        "status": "SYNTHETIC_TEST_ONLY_NO_MODEL_AUTHORITY",
        "synthetic_fixture": True,
        "authorization_id": "00000000-0000-4000-8000-000000000014",
        "run_id": bundle_run_id,
        "issued_at_utc": "2026-01-01T00:00:00Z",
        "expires_at_utc": "2026-01-02T00:00:00Z",
        "selected_variant": variant,
        "ordered_process_ids": list(process_ids),
        "bindings": {key: partial_bindings[key] for key in validator.AUTH_BOUND_BINDING_KEYS},
        "model_execution_authorized": False,
        "single_use": True,
        "allowed_operations": ["cpu_static_synthetic_validation"],
        "forbidden_operations": [
            "adaptive_retry",
            "outcome_dependent_rerun",
            "hidden_audit",
            "sampled_rlvr",
            "formal_or_confirmatory_claim",
        ],
        "trusted_signer_id": "synthetic-test-only",
        "trusted_signer_key_fingerprint_sha256": digest("synthetic-key"),
        "signature_algorithm": "SYNTHETIC-NONE",
        "signature": "synthetic-no-signature",
    }
    authorization_sha = validator.sha256_bytes(validator.canonical_json_bytes(authorization))
    bindings = {
        "protocol_sha256": partial_bindings["protocol_sha256"],
        "panel_bundle_sha256": partial_bindings["panel_bundle_sha256"],
        "allowlist_receipt_sha256": partial_bindings["allowlist_receipt_sha256"],
        "human_review_receipt_sha256": partial_bindings["human_review_receipt_sha256"],
        "authorization_sha256": authorization_sha,
        "runner_sha256": partial_bindings["runner_sha256"],
        "validator_sha256": partial_bindings["validator_sha256"],
        "model_inventory_sha256": partial_bindings["model_inventory_sha256"],
        "source_assets_sha256": partial_bindings["source_assets_sha256"],
    }
    processes = []
    for position, process_id in enumerate(process_ids):
        stack, replicate = process_id.split("|")
        initial_hash = digest("global-initial-trainable-snapshot")
        target_panels = tuple(
            runner.TargetPanelBinding(
                arm=arm,
                target_panel_sha256=panel_hashes[(stack, arm)],
                ordered_rows=tuple(
                    runner.TargetRowBinding(
                        canonical_instance_id=f"{stack}|z{record.canonical_z}",
                        row_id=record.row_id,
                        panel_row_sha256=record.row_sha256,
                    )
                    for record in panels[(stack, arm)]
                ),
            )
            for arm in validator.ARMS
        )
        contract = runner.R13RunContract(
            run_id=f"fixture-proc-{position:02d}",
            bundle_run_id=bundle_run_id,
            replicate_id=replicate,
            stack_id=stack,
            bindings=bindings,
            initial_parameter_hash=initial_hash,
            ordered_candidate_set=validator.CANDIDATES,
            target_panels=target_panels,
        )
        rows_by_id = {
            record.row_id: record
            for arm in validator.ARMS
            for record in panels[(stack, arm)]
        }
        processes.append(
            runner.run_stack_process(
                contract,
                FakeBackend(initial_hash, rows_by_id, protocol, stack),
                clock=lambda: datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            )
        )
    flattened = []
    for process_id, process in zip(process_ids, processes):
        for execution in process["source_update_executions"]:
            flattened.append(
                {
                    "execution_event_id": execution["execution_event_id"],
                    "process_id": process_id,
                    "execution_key": execution["execution_key"],
                    "source_rule_identity": execution["source_rule_identity"],
                    "update_parameter_hash": execution["update_parameter_hash"],
                }
            )
    result = {
        "schema_version": validator.RESULT_SCHEMA,
        "status": "SYNTHETIC_TEST_FIXTURE_NO_MODEL",
        "synthetic_fixture": True,
        "run_id": bundle_run_id,
        "created_at_utc": "2026-01-01T12:00:00Z",
        "selected_variant": variant,
        "scientific_evidence": False,
        "formal_experiment": False,
        "formal_confirmatory": False,
        "development_only": True,
        "sampled_rlvr": False,
        "same_empirical_or_policy_fpr": False,
        "model_execution_performed": False,
        "static_core_only": True,
        "model_execution_authorized_by_core": False,
        "ordered_candidate_set": list(validator.CANDIDATES),
        "bindings": bindings,
        "ordered_process_ids": list(process_ids),
        "counts": validator.expected_total_counts(variant),
        "events": flattened,
        "process_results": processes,
    }
    return {
        "result": result,
        "protocol": protocol,
        "records": records,
        "allowlist": allowlist,
        "human": human,
        "authorization": authorization,
        "bindings": bindings,
    }


def validate_fixture(fixture: dict) -> dict:
    return validator.validate_result_bundle(
        fixture["result"],
        protocol=fixture["protocol"],
        panel_records=fixture["records"],
        allowlist_receipt=fixture["allowlist"],
        human_review_receipt=fixture["human"],
        authorization=fixture["authorization"],
        expected_bindings=fixture["bindings"],
        synthetic_test_mode=True,
    )


class R13ResultValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = build_fixture()

    def fresh(self) -> dict:
        return copy.deepcopy(self.fixture)

    def test_synthetic_ab_positive_recomputes_q6_e_r_g_f_c(self) -> None:
        report = validate_fixture(self.fresh())
        self.assertEqual(report["decision"], "R13_DEVELOPMENT_SURFACE_ALIGNMENT_SWITCH_PASS_ONLY")
        self.assertTrue(report["q6_reconstructed_from_raw_seven_candidate_traces"])
        self.assertTrue(report["a_b_checked_before_averaging"])
        self.assertEqual(set(report["by_stack"]), set(validator.STACKS))
        self.assertTrue(report["synthetic_fixture"])
        self.assertFalse(report["model_execution_performed"])

    def test_cross_stack_initial_snapshot_mismatch_is_rejected(self) -> None:
        fixture = self.fresh()
        process = next(
            item
            for item in fixture["result"]["process_results"]
            if item["stack_id"] == validator.STACKS[1]
        )
        old_hash = process["initial_parameter_hash"]
        new_hash = digest("different-initial-trainable-snapshot")

        def replace_exact(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    value[key] = replace_exact(item)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    value[index] = replace_exact(item)
            elif value == old_hash:
                return new_hash
            return value

        replace_exact(process)
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "global initial trainable snapshot hash mismatch",
        ):
            validate_fixture(fixture)

    def test_event_outside_authorization_window_is_rejected(self) -> None:
        fixture = self.fresh()
        process = fixture["result"]["process_results"][0]
        for event in process["events"]:
            event["at_utc"] = "2026-01-03T00:00:00Z"
        process["created_at_utc"] = "2026-01-03T00:00:00Z"
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "event outside authorization time window",
        ):
            validate_fixture(fixture)

    def test_event_postdating_result_is_rejected(self) -> None:
        fixture = self.fresh()
        process = fixture["result"]["process_results"][0]
        for event in process["events"]:
            event["at_utc"] = "2026-01-01T13:00:00Z"
        process["created_at_utc"] = "2026-01-01T13:00:00Z"
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "event postdates result creation time",
        ):
            validate_fixture(fixture)

    def test_result_at_authorization_expiry_is_rejected(self) -> None:
        fixture = self.fresh()
        fixture["result"]["created_at_utc"] = fixture["authorization"]["expires_at_utc"]
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "result outside authorization time window",
        ):
            validate_fixture(fixture)

    def test_synthetic_model_execution_marker_cannot_be_laundered(self) -> None:
        fixture = self.fresh()
        fixture["result"]["model_execution_performed"] = True
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "model execution marker inconsistent with validation mode",
        ):
            validate_fixture(fixture)

    def test_production_human_review_time_must_precede_fresh_authorization(self) -> None:
        protocol_sha = digest("protocol")
        panel_sha = digest("panel")
        allowlist_sha = digest("allowlist")
        receipt = human_template(panel_sha, digest("panel-jsonl"), digest("raw"))
        receipt.update(
            {
                "schema_version": validator.HUMAN_RECEIPT_SCHEMA,
                "status": validator.HUMAN_RECEIPT_STATUS,
                "template_only": False,
                "review_completed": True,
                "decision": "PASS",
                "review_method": "independent byte and semantic review",
                "review_notes": "all frozen checks passed",
                "reviewer_affiliation_or_role": "independent reviewer",
                "reviewer_name": "Test Reviewer",
                "signature": "test-signature",
                "signature_algorithm": "TEST",
                "signature_key_id": "test-key",
                "protocol_sha256": protocol_sha,
                "allowlist_receipt_sha256": allowlist_sha,
            }
        )
        policy = validator.ExternalTrustPolicy(
            authorization_signer_id="unused",
            authorization_key_fingerprint_sha256=digest("unused-key"),
            authorization_signature_algorithm="UNUSED",
            verify_authorization_signature=lambda payload, signature: True,
            verify_human_review_signature=lambda payload, signature, algorithm, key_id: True,
            authorization_nonce_is_unconsumed=lambda authorization_id, run_id: True,
        )
        issued = datetime(2026, 2, 1, tzinfo=timezone.utc)
        result_created = issued + timedelta(hours=1)

        receipt["reviewed_at_utc"] = "2026-02-01T00:00:01Z"
        with self.assertRaisesRegex(
            validator.R13ValidationError,
            "postdates authorization issue time",
        ):
            validator.validate_human_receipt(
                receipt,
                expected_protocol_sha256=protocol_sha,
                expected_panel_bundle_sha256=panel_sha,
                expected_allowlist_receipt_sha256=allowlist_sha,
                synthetic_test_mode=False,
                trusted_policy=policy,
                authorization_issued_at=issued,
                result_created_at=result_created,
            )

        receipt["reviewed_at_utc"] = "2026-01-01T23:59:59Z"
        with self.assertRaisesRegex(validator.R13ValidationError, "human review is stale"):
            validator.validate_human_receipt(
                receipt,
                expected_protocol_sha256=protocol_sha,
                expected_panel_bundle_sha256=panel_sha,
                expected_allowlist_receipt_sha256=allowlist_sha,
                synthetic_test_mode=False,
                trusted_policy=policy,
                authorization_issued_at=issued,
                result_created_at=result_created,
            )

    def test_duplicate_and_nonfinite_json_are_rejected(self) -> None:
        with self.assertRaises(validator.R13ValidationError):
            validator.strict_json_loads(b'{"x":1,"x":2}', "duplicate")
        with self.assertRaises(validator.R13ValidationError):
            validator.strict_json_loads(b'{"x":NaN}', "nonfinite")

    def test_readout_parameter_mutation_is_rejected(self) -> None:
        fixture = self.fresh()
        fixture["result"]["process_results"][0]["source_update_executions"][0]["readouts"][1]["parameter_hash_after"] = digest("mutated")
        with self.assertRaisesRegex(validator.R13ValidationError, "mutated parameters"):
            validate_fixture(fixture)

    def test_target_blind_source_call_commitment_is_recomputed(self) -> None:
        fixture = self.fresh()
        fixture["result"]["process_results"][0]["source_update_executions"][0]["source_update_call_sha256"] = digest("arm-leak")
        with self.assertRaisesRegex(validator.R13ValidationError, "target-blind input"):
            validate_fixture(fixture)

    def test_ab_raw_effect_mismatch_is_rejected(self) -> None:
        fixture = self.fresh()
        process_b = next(
            item
            for item in fixture["result"]["process_results"]
            if item["stack_id"] == validator.STACKS[0] and item["replicate_id"] == "B"
        )
        process_b["source_update_executions"][0]["readouts"][0]["row_traces"][0]["ordered_candidate_scores"][0] += 1e-3
        with self.assertRaisesRegex(validator.R13ValidationError, "A/B E matrix mismatch"):
            validate_fixture(fixture)

    def test_nonallowlisted_panel_change_is_rejected(self) -> None:
        fixture = self.fresh()
        h1 = next(record for record in fixture["records"] if record.arm == validator.ARMS[1])
        object.__setattr__(h1, "row", {**h1.row, "task_input": {"z": 999}})
        object.__setattr__(h1, "row_bytes", validator.canonical_json_bytes(h1.row))
        object.__setattr__(h1, "row_sha256", validator.sha256_bytes(h1.row_bytes))
        with self.assertRaises(validator.R13ValidationError):
            validate_fixture(fixture)

    def test_production_without_external_trust_policy_is_blocked(self) -> None:
        fixture = self.fresh()
        with self.assertRaisesRegex(
            validator.R13AuthorizationError,
            "BLOCKED_EXTERNAL_TRUST_VERIFIER_NOT_PROVIDED",
        ):
            validator.validate_result_bundle(
                fixture["result"],
                protocol=fixture["protocol"],
                panel_records=fixture["records"],
                allowlist_receipt=fixture["allowlist"],
                human_review_receipt=fixture["human"],
                authorization=fixture["authorization"],
                expected_bindings=fixture["bindings"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
