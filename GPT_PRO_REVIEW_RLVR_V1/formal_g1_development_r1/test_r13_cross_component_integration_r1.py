from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


FORMAL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = FORMAL_DIR.parent
MVP_DIR = PROJECT_DIR / "mvp_same_source_v1"
ASSET_ROOT = FORMAL_DIR / "r13_assets_r1"
PANEL_JSONL = ASSET_ROOT / "R13_MATCHED_TARGET_PANELS_R1.jsonl"
PANEL_MANIFEST = ASSET_ROOT / "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
ALLOWLIST_RECEIPT = ASSET_ROOT / "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"
HUMAN_TEMPLATE = ASSET_ROOT / "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json"
PROTOCOL_PATH = FORMAL_DIR / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
UPDATE_RECIPE_PATH = FORMAL_DIR / "R13_UPDATE_RECIPE_CONTRACT_R1.json"
MODEL_INVENTORY_PATH = FORMAL_DIR / "R13_MODEL_INVENTORY_R1.json"
SOURCE_BUNDLES_PATH = PROJECT_DIR / "real_assets" / "build_v5_repair_a" / "REAL_SOURCE_BUNDLES_V1.jsonl"
RUNNER_PATH = MVP_DIR / "r13_runner_core.py"


import r13_matched_panels_validate_r1 as asset_validator
import validate_r13_target_alignment_results_r1 as result_validator


runner_spec = importlib.util.spec_from_file_location(
    "r13_runner_core_for_real_asset_integration_test", RUNNER_PATH
)
assert runner_spec is not None and runner_spec.loader is not None
runner = importlib.util.module_from_spec(runner_spec)
sys.modules[runner_spec.name] = runner
runner_spec.loader.exec_module(runner)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def strict_json_file(path: Path) -> dict:
    value = result_validator.strict_json_loads(path.read_bytes(), str(path))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def load_real_wrappers_and_records() -> tuple[
    list[dict],
    list[result_validator.PanelRecord],
    dict[tuple[str, str, int], dict],
]:
    wrappers = asset_validator.read_jsonl(PANEL_JSONL)
    wrapper_index: dict[tuple[str, str, int], dict] = {}
    records: list[result_validator.PanelRecord] = []
    for position, wrapper in enumerate(wrappers):
        key = (
            wrapper["mapping_stack_id"],
            wrapper["arm"],
            wrapper["canonical_z"],
        )
        if key in wrapper_index:
            raise AssertionError(f"duplicate real wrapper key: {key}")
        wrapper_index[key] = wrapper
        generated = wrapper["generated"]
        row_path = ASSET_ROOT / generated["row_relpath"]
        prompt_path = ASSET_ROOT / generated["prompt_relpath"]
        row_bytes = row_path.read_bytes()
        prompt_bytes = prompt_path.read_bytes()
        if sha256_bytes(row_bytes) != generated["row_sha256"]:
            raise AssertionError(f"real row hash mismatch at wrapper {position}")
        if sha256_bytes(prompt_bytes) != generated["prompt_sha256"]:
            raise AssertionError(f"real prompt hash mismatch at wrapper {position}")
        row = result_validator.strict_json_loads(
            row_bytes, f"real matched row {key}"
        )
        records.append(
            result_validator.PanelRecord(
                stack_id=key[0],
                arm=key[1],
                canonical_z=key[2],
                row_id=row["row_id"],
                row_sha256=generated["row_sha256"],
                prompt_sha256=generated["prompt_sha256"],
                row=row,
                row_bytes=row_bytes,
                prompt_bytes=prompt_bytes,
            )
        )
    return wrappers, records, wrapper_index


class RealAssetStaticBackend:
    """Deterministic no-model backend that consumes the frozen real target rows."""

    def __init__(
        self,
        initial_hash: str,
        *,
        source_assets_sha256: str,
        protocol: dict,
        stack: str,
        records_by_id: dict[str, result_validator.PanelRecord],
        panel_rows_by_sha256: dict[str, tuple[str, ...]],
    ) -> None:
        self.initial_hash = initial_hash
        self.current_hash = initial_hash
        self.source_assets_sha256 = source_assets_sha256
        self.protocol = protocol
        self.stack = stack
        self.records_by_id = records_by_id
        self.panel_rows_by_sha256 = panel_rows_by_sha256

    def reset_trainable(self) -> None:
        self.current_hash = self.initial_hash

    def parameter_hash(self) -> str:
        return self.current_hash

    def update_source(self, request):
        if request.source_assets_sha256 != self.source_assets_sha256:
            raise AssertionError("runner did not pass the real source-bundle commitment")
        self.current_hash = sha256_bytes(
            runner.canonical_json_bytes(asdict(request)) + b"|static-no-model-update"
        )
        return runner.SourceUpdateResult(
            optimizer_step_count=1,
            clipping_triggered=False,
            non_finite_observed=False,
            diagnostics={"raw_gradient_norm": 0.5, "realized_update_norm": 0.125},
        )

    def read_target(self, request):
        expected_ids = self.panel_rows_by_sha256.get(request.target_panel_sha256)
        if expected_ids is None:
            raise AssertionError("runner requested an unbound real target panel")
        if tuple(request.ordered_row_ids) != expected_ids:
            raise AssertionError("runner target row ids do not match the bound real panel")
        rows = []
        for row_id in request.ordered_row_ids:
            record = self.records_by_id[row_id]
            scores = [0.0] * 7
            if request.phase == "POST":
                r = int(request.source_rule_identity.removeprefix("Z7_PLUS"))
                q = result_validator.q_alignment(
                    self.protocol, self.stack, request.arm
                )[r]
                candidate = result_validator._target_candidate(record.row, q)
                scores[result_validator.CANDIDATES.index(candidate)] = 2.0
            rows.append(
                runner.RawCandidateRow(
                    row_id=row_id,
                    ordered_candidate_scores=tuple(scores),
                )
            )
        return runner.TargetReadResult(
            rows=tuple(rows),
            gradient_count=0,
            optimizer_step_count=0,
            state_mutation_count=0,
            non_finite_observed=False,
        )


def build_real_asset_fixture() -> dict:
    asset_report = asset_validator.validate_bundle()
    protocol = strict_json_file(PROTOCOL_PATH)
    wrappers, records, wrapper_index = load_real_wrappers_and_records()
    result_validator.validate_protocol(protocol, synthetic_test_mode=True)
    panels, panel_hashes = result_validator.validate_matched_panels(records, protocol)

    update_recipe = strict_json_file(UPDATE_RECIPE_PATH)
    protocol_sha256 = sha256_file(PROTOCOL_PATH)
    source_assets_sha256 = sha256_file(SOURCE_BUNDLES_PATH)
    model_inventory_sha256 = sha256_file(MODEL_INVENTORY_PATH)
    if update_recipe["protocol_sha256"] != protocol_sha256:
        raise AssertionError("update recipe/protocol commitment drift")
    if update_recipe["source_bundles_sha256"] != source_assets_sha256:
        raise AssertionError("update recipe/real source bundle commitment drift")
    if update_recipe["model_inventory_sha256"] != model_inventory_sha256:
        raise AssertionError("update recipe/model inventory commitment drift")

    allowlist = strict_json_file(ALLOWLIST_RECEIPT)
    human = strict_json_file(HUMAN_TEMPLATE)
    bundle_run_id = "00000000-0000-4000-8000-000000000113"
    variant = "MINIMAL_4_PROCESS_A"
    process_ids = result_validator.expected_process_ids(variant)
    partial_bindings = {
        "protocol_sha256": protocol_sha256,
        "panel_bundle_sha256": sha256_file(PANEL_MANIFEST),
        "allowlist_receipt_sha256": sha256_file(ALLOWLIST_RECEIPT),
        "human_review_receipt_sha256": sha256_file(HUMAN_TEMPLATE),
        "runner_sha256": sha256_file(RUNNER_PATH),
        "validator_sha256": sha256_file(Path(result_validator.__file__).resolve()),
        "model_inventory_sha256": model_inventory_sha256,
        "source_assets_sha256": source_assets_sha256,
    }
    loaded_bundle = result_validator.load_bound_matched_panel_bundle(
        ASSET_ROOT,
        expected_manifest_sha256=partial_bindings["panel_bundle_sha256"],
        expected_allowlist_receipt_sha256=partial_bindings[
            "allowlist_receipt_sha256"
        ],
    )
    if tuple(records) != loaded_bundle.records:
        raise AssertionError("manual audit records differ from manifest-loaded records")
    authorization = {
        "schema_version": result_validator.AUTHORIZATION_SCHEMA,
        "status": "SYNTHETIC_TEST_ONLY_NO_MODEL_AUTHORITY",
        "synthetic_fixture": True,
        "authorization_id": "00000000-0000-4000-8000-000000000114",
        "run_id": bundle_run_id,
        "issued_at_utc": "2026-01-01T00:00:00Z",
        "expires_at_utc": "2026-01-02T00:00:00Z",
        "selected_variant": variant,
        "ordered_process_ids": list(process_ids),
        "bindings": {
            key: partial_bindings[key]
            for key in result_validator.AUTH_BOUND_BINDING_KEYS
        },
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
        "trusted_signer_id": "synthetic-integration-test-only",
        "trusted_signer_key_fingerprint_sha256": digest("synthetic-key"),
        "signature_algorithm": "SYNTHETIC-NONE",
        "signature": "synthetic-no-signature",
    }
    bindings = {
        **partial_bindings,
        "authorization_sha256": sha256_bytes(
            result_validator.canonical_json_bytes(authorization)
        ),
    }

    contracts: dict[str, object] = {}
    processes = []
    for position, process_id in enumerate(process_ids):
        stack, replicate = process_id.split("|")
        target_panels = []
        for arm in result_validator.ARMS:
            panel_records = panels[(stack, arm)]
            target_panels.append(
                runner.TargetPanelBinding(
                    arm=arm,
                    target_panel_sha256=panel_hashes[(stack, arm)],
                    ordered_rows=tuple(
                        runner.TargetRowBinding(
                            # The stable cross-arm instance is the original
                            # source lineage, not the arm-specific generated
                            # raw row lineage.
                            canonical_instance_id=wrapper_index[
                                (stack, arm, record.canonical_z)
                            ]["source_original"]["lineage_id"],
                            row_id=record.row_id,
                            panel_row_sha256=record.row_sha256,
                        )
                        for record in panel_records
                    ),
                )
            )
        initial_hash = digest("real-asset-static-global-initial")
        contract = runner.R13RunContract(
            run_id=f"real-asset-static-proc-{position:02d}",
            bundle_run_id=bundle_run_id,
            replicate_id=replicate,
            stack_id=stack,
            bindings=bindings,
            initial_parameter_hash=initial_hash,
            ordered_candidate_set=result_validator.CANDIDATES,
            target_panels=tuple(target_panels),
        )
        contracts[stack] = contract
        records_by_id = {
            record.row_id: record
            for arm in result_validator.ARMS
            for record in panels[(stack, arm)]
        }
        panel_rows_by_sha256 = {
            panel_hashes[(stack, arm)]: tuple(
                record.row_id for record in panels[(stack, arm)]
            )
            for arm in result_validator.ARMS
        }
        processes.append(
            runner.run_stack_process(
                contract,
                RealAssetStaticBackend(
                    initial_hash,
                    source_assets_sha256=source_assets_sha256,
                    protocol=protocol,
                    stack=stack,
                    records_by_id=records_by_id,
                    panel_rows_by_sha256=panel_rows_by_sha256,
                ),
                clock=lambda: datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            )
        )

    flattened_events = []
    for process_id, process in zip(process_ids, processes):
        for execution in process["source_update_executions"]:
            flattened_events.append(
                {
                    "execution_event_id": execution["execution_event_id"],
                    "process_id": process_id,
                    "execution_key": execution["execution_key"],
                    "source_rule_identity": execution["source_rule_identity"],
                    "update_parameter_hash": execution["update_parameter_hash"],
                }
            )
    result = {
        "schema_version": result_validator.RESULT_SCHEMA,
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
        "ordered_candidate_set": list(result_validator.CANDIDATES),
        "bindings": bindings,
        "ordered_process_ids": list(process_ids),
        "counts": result_validator.expected_total_counts(variant),
        "events": flattened_events,
        "process_results": processes,
    }
    return {
        "asset_report": asset_report,
        "authorization": authorization,
        "bindings": bindings,
        "contracts": contracts,
        "human": human,
        "loaded_bundle": loaded_bundle,
        "allowlist": allowlist,
        "panels": panels,
        "panel_hashes": panel_hashes,
        "protocol": protocol,
        "records": records,
        "result": result,
        "wrapper_index": wrapper_index,
        "wrappers": wrappers,
    }


def validate_fixture(fixture: dict) -> dict:
    return result_validator.validate_result_bundle(
        fixture["result"],
        protocol=fixture["protocol"],
        panel_records=fixture["records"],
        allowlist_receipt=fixture["allowlist"],
        human_review_receipt=fixture["human"],
        authorization=fixture["authorization"],
        expected_bindings=fixture["bindings"],
        synthetic_test_mode=True,
        matched_panel_bundle_root=ASSET_ROOT,
    )


class R13RealAssetCrossComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = build_real_asset_fixture()

    def test_real_lineage_maps_to_canonical_id_and_rows_remain_arm_specific(self) -> None:
        self.assertEqual(
            self.fixture["asset_report"]["verdict"],
            "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY",
        )
        for stack in result_validator.STACKS:
            contract = self.fixture["contracts"][stack]
            by_arm = {panel.arm: panel for panel in contract.target_panels}
            h0 = by_arm[result_validator.ARMS[0]]
            h1 = by_arm[result_validator.ARMS[1]]
            canonical_h0 = [row.canonical_instance_id for row in h0.ordered_rows]
            canonical_h1 = [row.canonical_instance_id for row in h1.ordered_rows]
            expected = [
                self.fixture["wrapper_index"][(stack, result_validator.ARMS[0], z)][
                    "source_original"
                ]["lineage_id"]
                for z in range(7)
            ]
            self.assertEqual(canonical_h0, expected)
            self.assertEqual(canonical_h1, expected)
            self.assertEqual(len(set(expected)), 7)
            h0_row_ids = [row.row_id for row in h0.ordered_rows]
            h1_row_ids = [row.row_id for row in h1.ordered_rows]
            self.assertTrue(all(left != right for left, right in zip(h0_row_ids, h1_row_ids)))
            self.assertTrue(set(h0_row_ids).isdisjoint(h1_row_ids))
            self.assertEqual(
                h0.target_panel_sha256,
                self.fixture["panel_hashes"][(stack, result_validator.ARMS[0])],
            )
            self.assertEqual(
                h1.target_panel_sha256,
                self.fixture["panel_hashes"][(stack, result_validator.ARMS[1])],
            )

    def test_arm_specific_raw_lineage_cannot_impersonate_canonical_identity(self) -> None:
        stack = result_validator.STACKS[0]
        valid = self.fixture["contracts"][stack]
        wrong_panels = tuple(
            runner.TargetPanelBinding(
                arm=panel.arm,
                target_panel_sha256=panel.target_panel_sha256,
                ordered_rows=tuple(
                    runner.TargetRowBinding(
                        canonical_instance_id=self.fixture["panels"][(stack, panel.arm)][
                            index
                        ].row["lineage_id"],
                        row_id=row.row_id,
                        panel_row_sha256=row.panel_row_sha256,
                    )
                    for index, row in enumerate(panel.ordered_rows)
                ),
            )
            for panel in valid.target_panels
        )
        wrong = runner.R13RunContract(
            run_id="wrong-arm-lineage",
            bundle_run_id=valid.bundle_run_id,
            replicate_id=valid.replicate_id,
            stack_id=valid.stack_id,
            bindings=valid.bindings,
            initial_parameter_hash=valid.initial_parameter_hash,
            ordered_candidate_set=valid.ordered_candidate_set,
            target_panels=wrong_panels,
        )
        with self.assertRaisesRegex(
            runner.R13RunnerError, "same seven canonical rows"
        ):
            runner._validate_contract(wrong)

    def test_real_assets_cross_runner_and_independent_q6_validator(self) -> None:
        report = validate_fixture(self.fixture)
        self.assertTrue(report["technical_pass"])
        self.assertTrue(report["q6_reconstructed_from_raw_seven_candidate_traces"])
        self.assertFalse(report["scientific_evidence"])
        self.assertTrue(report["development_only"])
        self.assertTrue(report["synthetic_fixture"])
        self.assertFalse(report["model_execution_performed"])

        processes = self.fixture["result"]["process_results"]
        collection = runner.validate_process_collection(
            processes, variant="MINIMAL_4_PROCESS_A"
        )
        for key, expected in result_validator.expected_total_counts(
            "MINIMAL_4_PROCESS_A"
        ).items():
            self.assertEqual(collection["counts"][key], expected)

        self.assertEqual(runner.SCHEMA_VERSION, result_validator.PROCESS_SCHEMA)
        self.assertEqual(set(runner.BINDING_KEYS), set(result_validator.BINDING_KEYS))
        self.assertEqual(runner.EXPECTED_PROCESS_COUNTS, result_validator.expected_process_counts())
        self.assertEqual(set(runner._TOP_LEVEL_KEYS), set(result_validator.PROCESS_KEYS))
        self.assertEqual(set(runner._READOUT_KEYS), set(result_validator.READOUT_KEYS))
        self.assertEqual(set(runner._TRACE_KEYS), set(result_validator.TRACE_KEYS))
        self.assertEqual(set(runner._EXECUTION_KEYS), set(result_validator.EXECUTION_KEYS))

    def test_allowlist_implementation_hash_tamper_is_rejected(self) -> None:
        fixture = deepcopy(self.fixture)
        fixture["allowlist"]["implementation_bindings"]["generator_sha256"] = digest(
            "wrong-generator"
        )
        with self.assertRaisesRegex(
            result_validator.R13ValidationError,
            "generator implementation hash mismatch",
        ):
            validate_fixture(fixture)

    def test_replacing_both_arm_task_inputs_under_old_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "r13_assets_r1"
            shutil.copytree(ASSET_ROOT, copied_root)
            stack = result_validator.STACKS[0]
            for arm in result_validator.ARMS:
                wrapper = self.fixture["wrapper_index"][(stack, arm, 0)]
                row_path = copied_root / wrapper["generated"]["row_relpath"]
                row = result_validator.strict_json_loads(
                    row_path.read_bytes(), f"tampered {arm} row"
                )
                row["task_input"] = {"adversarial_replacement": True}
                row_path.write_bytes(
                    result_validator._asset_canonical_json_bytes(row) + b"\n"
                )
            with self.assertRaisesRegex(
                result_validator.R13ValidationError, "hash mismatch"
            ):
                result_validator.load_bound_matched_panel_bundle(
                    copied_root,
                    expected_manifest_sha256=self.fixture["bindings"][
                        "panel_bundle_sha256"
                    ],
                    expected_allowlist_receipt_sha256=self.fixture["bindings"][
                        "allowlist_receipt_sha256"
                    ],
                )

    def test_production_api_blocks_caller_records_without_bound_loader(self) -> None:
        policy = result_validator.ExternalTrustPolicy(
            authorization_signer_id="unused-static-test",
            authorization_key_fingerprint_sha256=digest("unused-key"),
            authorization_signature_algorithm="UNUSED",
            verify_authorization_signature=lambda payload, signature: True,
            verify_human_review_signature=lambda payload, signature, algorithm, key_id: True,
            authorization_nonce_is_unconsumed=lambda authorization_id, run_id: True,
        )
        with self.assertRaisesRegex(
            result_validator.R13AuthorizationError,
            "BLOCKED_BOUND_MATCHED_PANEL_LOADER_NOT_PROVIDED",
        ):
            result_validator.validate_result_bundle(
                self.fixture["result"],
                protocol=self.fixture["protocol"],
                panel_records=self.fixture["records"],
                allowlist_receipt=self.fixture["allowlist"],
                human_review_receipt=self.fixture["human"],
                authorization=self.fixture["authorization"],
                expected_bindings=self.fixture["bindings"],
                trusted_policy=policy,
            )

    def test_forged_loaded_bundle_is_not_an_accepted_production_argument(self) -> None:
        forged = result_validator.LoadedMatchedPanelBundle(
            records=tuple(self.fixture["records"]),
            canonical_instance_ids=self.fixture["loaded_bundle"].canonical_instance_ids,
            manifest_sha256=self.fixture["bindings"]["panel_bundle_sha256"],
            panel_jsonl_sha256=self.fixture["loaded_bundle"].panel_jsonl_sha256,
            raw_inventory_commitment_sha256=self.fixture[
                "loaded_bundle"
            ].raw_inventory_commitment_sha256,
            allowlist_receipt_sha256=self.fixture["bindings"][
                "allowlist_receipt_sha256"
            ],
            allowlist_receipt=self.fixture["allowlist"],
        )
        policy = result_validator.ExternalTrustPolicy(
            authorization_signer_id="unused-static-test",
            authorization_key_fingerprint_sha256=digest("unused-key"),
            authorization_signature_algorithm="UNUSED",
            verify_authorization_signature=lambda payload, signature: True,
            verify_human_review_signature=lambda payload, signature, algorithm, key_id: True,
            authorization_nonce_is_unconsumed=lambda authorization_id, run_id: True,
        )
        with self.assertRaisesRegex(TypeError, "matched_panel_bundle"):
            result_validator.validate_result_bundle(
                self.fixture["result"],
                protocol=self.fixture["protocol"],
                panel_records=self.fixture["records"],
                allowlist_receipt=self.fixture["allowlist"],
                human_review_receipt=self.fixture["human"],
                authorization=self.fixture["authorization"],
                expected_bindings=self.fixture["bindings"],
                trusted_policy=policy,
                matched_panel_bundle=forged,
            )

    def test_q6_uses_all_seven_real_rows_and_seven_candidate_scores(self) -> None:
        process = self.fixture["result"]["process_results"][0]
        stack = process["stack_id"]
        selected = None
        for execution in process["source_update_executions"]:
            r = int(execution["source_rule_identity"].removeprefix("Z7_PLUS"))
            for readout in execution["readouts"]:
                if result_validator.q_alignment(
                    self.fixture["protocol"], stack, readout["arm"]
                )[r] == 6:
                    selected = readout
                    break
            if selected is not None:
                break
        self.assertIsNotNone(selected, "frozen affine panels must expose a q=6 cell")
        panel_records = self.fixture["panels"][(stack, selected["arm"])]
        trace_map = {trace["row_id"]: trace for trace in selected["row_traces"]}
        observed = result_validator.trace_metric(trace_map, panel_records, 6, "real-q6")
        candidate_index = {
            candidate: index
            for index, candidate in enumerate(result_validator.CANDIDATES)
        }
        contributions = []
        for record in panel_records:
            scores = trace_map[record.row_id]["ordered_candidate_scores"]
            self.assertEqual(len(scores), 7)
            wrong = result_validator._target_candidate(record.row, 6)
            gold = record.row["gold_candidate"]
            contributions.append(
                scores[candidate_index[wrong]] - scores[candidate_index[gold]]
            )
        self.assertEqual(len(contributions), 7)
        self.assertAlmostEqual(observed, sum(contributions) / 7.0, places=15)
        self.assertAlmostEqual(observed, 2.0, places=15)

    def test_independent_validator_rejects_h1_trace_with_h0_row_id(self) -> None:
        fixture = deepcopy(self.fixture)
        process = fixture["result"]["process_results"][0]
        h0_pre = next(
            item
            for item in process["pre_target_readouts"]
            if item["arm"] == result_validator.ARMS[0]
        )
        h1_pre = next(
            item
            for item in process["pre_target_readouts"]
            if item["arm"] == result_validator.ARMS[1]
        )
        h1_pre["row_traces"][0]["row_id"] = h0_pre["row_traces"][0]["row_id"]

        # The standalone core schema has no real asset object, so this mutation
        # remains structurally valid. The independent real-asset validator must
        # close the row-id/hash-to-panel binding and reject it.
        runner.validate_runner_result(process)
        with self.assertRaisesRegex(
            result_validator.R13ValidationError, "row order/id mismatch"
        ):
            validate_fixture(fixture)


if __name__ == "__main__":
    unittest.main(verbosity=2)
