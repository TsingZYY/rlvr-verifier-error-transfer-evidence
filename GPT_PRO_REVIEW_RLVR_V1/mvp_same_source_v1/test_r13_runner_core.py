from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import tempfile
import unittest
from typing import Any

import r13_runner_core as core


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class TickClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 6, 1, 2, 3, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(microseconds=1)
        return value


class BackwardsClock(TickClock):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        if self.calls == 3:
            return self.current - timedelta(seconds=2)
        return super().__call__()


class FakeBackend:
    """No model object exists; all behavior is deterministic in-memory arithmetic."""

    def __init__(
        self,
        initial_hash: str,
        *,
        reset_hash: str | None = None,
        update_no_change: bool = False,
        update_same_for_all: bool = False,
        update_salt: str = "",
        update_optimizer_steps: int = 1,
        update_clipping: bool = False,
        update_non_finite: bool = False,
        update_diagnostics: dict[str, Any] | None = None,
        read_wrong_type: bool = False,
        read_gradient_count: int = 0,
        read_optimizer_steps: int = 0,
        read_state_mutations: int = 0,
        read_non_finite: bool = False,
        mutate_on_read_call: int | None = None,
        row_count: int = 7,
        reverse_rows: bool = False,
        score_count: int = 7,
        non_finite_score: bool = False,
        score_salt: float = 0.0,
    ) -> None:
        self.initial_hash = initial_hash
        self.reset_hash = reset_hash or initial_hash
        self.state_hash = initial_hash
        self.update_no_change = update_no_change
        self.update_same_for_all = update_same_for_all
        self.update_salt = update_salt
        self.update_optimizer_steps = update_optimizer_steps
        self.update_clipping = update_clipping
        self.update_non_finite = update_non_finite
        self.update_diagnostics = update_diagnostics or {
            "raw_gradient_norm": 0.125,
            "realized_update_norm": 0.03125,
        }
        self.read_wrong_type = read_wrong_type
        self.read_gradient_count = read_gradient_count
        self.read_optimizer_steps = read_optimizer_steps
        self.read_state_mutations = read_state_mutations
        self.read_non_finite = read_non_finite
        self.mutate_on_read_call = mutate_on_read_call
        self.row_count = row_count
        self.reverse_rows = reverse_rows
        self.score_count = score_count
        self.non_finite_score = non_finite_score
        self.score_salt = score_salt
        self.reset_calls = 0
        self.parameter_hash_calls = 0
        self.update_calls: list[core.SourceUpdateCall] = []
        self.read_calls: list[core.TargetReadCall] = []

    def reset_trainable(self) -> None:
        self.reset_calls += 1
        self.state_hash = self.reset_hash

    def parameter_hash(self) -> str:
        self.parameter_hash_calls += 1
        return self.state_hash

    def update_source(self, request: core.SourceUpdateCall) -> core.SourceUpdateResult:
        self.update_calls.append(request)
        if not self.update_no_change:
            if self.update_same_for_all:
                self.state_hash = digest(
                    f"updated|{request.stack_id}|same|{self.update_salt}"
                )
            else:
                self.state_hash = digest(
                    f"updated|{request.stack_id}|{request.source_rule_identity}|"
                    f"{request.source_seed_material_sha256}|{self.update_salt}"
                )
        return core.SourceUpdateResult(
            optimizer_step_count=self.update_optimizer_steps,
            clipping_triggered=self.update_clipping,
            non_finite_observed=self.update_non_finite,
            diagnostics=self.update_diagnostics,
        )

    def read_target(self, request: core.TargetReadCall) -> core.TargetReadResult:
        self.read_calls.append(request)
        if self.read_wrong_type:
            return {}  # type: ignore[return-value]
        row_ids = list(request.ordered_row_ids)
        if self.reverse_rows:
            row_ids.reverse()
        row_ids = row_ids[: self.row_count]
        arm_term = 10.0 if request.arm == core.ARMS[1] else 0.0
        phase_term = 1.0 if request.phase == "POST" else 0.0
        identity_term = (
            0.0
            if request.source_rule_identity is None
            else float(core.SOURCE_IDENTITIES.index(request.source_rule_identity) + 1) / 10.0
        )
        rows: list[core.RawCandidateRow] = []
        for row_index, row_id in enumerate(row_ids):
            scores = [
                arm_term
                + phase_term
                + identity_term
                + row_index / 100.0
                + candidate_index / 1000.0
                + self.score_salt
                for candidate_index in range(self.score_count)
            ]
            if self.non_finite_score and not rows and scores:
                scores[0] = math.nan
            rows.append(
                core.RawCandidateRow(
                    row_id=row_id,
                    ordered_candidate_scores=tuple(scores),
                )
            )
        if self.mutate_on_read_call == len(self.read_calls):
            self.state_hash = digest("illegal-readout-mutation")
        return core.TargetReadResult(
            rows=tuple(rows),
            gradient_count=self.read_gradient_count,
            optimizer_step_count=self.read_optimizer_steps,
            state_mutation_count=self.read_state_mutations,
            non_finite_observed=self.read_non_finite,
        )


def make_bindings() -> dict[str, str]:
    return {key: digest(f"binding|{key}") for key in core.BINDING_KEYS}


def make_contract(
    *,
    replicate: str = "A",
    stack: str = core.STACK_IDS[0],
    run_suffix: str = "0",
    bindings: dict[str, str] | None = None,
    initial_hash: str | None = None,
) -> core.R13RunContract:
    rows_h0 = tuple(
        core.TargetRowBinding(
            canonical_instance_id=f"canonical-instance-{index}",
            row_id=f"H0-row-{index}",
            panel_row_sha256=digest(f"H0|row|{index}"),
        )
        for index in range(7)
    )
    rows_h1 = tuple(
        core.TargetRowBinding(
            canonical_instance_id=f"canonical-instance-{index}",
            row_id=f"H1-row-{index}",
            panel_row_sha256=digest(f"H1|row|{index}"),
        )
        for index in range(7)
    )
    return core.R13RunContract(
        run_id=f"r13-{replicate}-{stack}-{run_suffix}",
        bundle_run_id="r13-static-test-bundle",
        replicate_id=replicate,
        stack_id=stack,
        bindings=bindings or make_bindings(),
        initial_parameter_hash=initial_hash or digest("initial-trainable-state"),
        ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        target_panels=(
            core.TargetPanelBinding(
                arm=core.ARMS[0],
                target_panel_sha256=digest("H0-target-panel"),
                ordered_rows=rows_h0,
            ),
            core.TargetPanelBinding(
                arm=core.ARMS[1],
                target_panel_sha256=digest("H1-target-panel"),
                ordered_rows=rows_h1,
            ),
        ),
    )


def run_one(
    *,
    replicate: str = "A",
    stack: str = core.STACK_IDS[0],
    run_suffix: str = "0",
    bindings: dict[str, str] | None = None,
    backend_kwargs: dict[str, Any] | None = None,
    clock: Any | None = None,
) -> tuple[dict[str, Any], FakeBackend, core.R13RunContract]:
    contract = make_contract(
        replicate=replicate,
        stack=stack,
        run_suffix=run_suffix,
        bindings=bindings,
    )
    backend = FakeBackend(contract.initial_parameter_hash, **(backend_kwargs or {}))
    result = core.run_stack_process(contract, backend, clock=clock or TickClock())
    return result, backend, contract


class R13RunnerHappyPathTests(unittest.TestCase):
    def test_module_has_no_model_runtime_import(self) -> None:
        path = Path(core.__file__).resolve()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "torch",
                    "transformers",
                    "peft",
                    "huggingface_hub",
                    "tensorflow",
                    "jax",
                }
            )
        )

    def test_source_update_input_is_structurally_target_blind(self) -> None:
        result_a, backend_a, _ = run_one(replicate="A", run_suffix="blind-a")
        result_b, backend_b, _ = run_one(replicate="B", run_suffix="blind-b")
        observed_fields = tuple(field.name for field in fields(core.SourceUpdateCall))
        self.assertEqual(observed_fields, core.SOURCE_UPDATE_CALL_FIELDS)
        for name in observed_fields:
            lowered = name.lower()
            self.assertNotIn("target", lowered)
            self.assertNotIn("panel", lowered)
            self.assertNotIn("arm", lowered)
            self.assertNotIn("replicate", lowered)
        self.assertEqual(
            [asdict(value) for value in backend_a.update_calls],
            [asdict(value) for value in backend_b.update_calls],
        )
        self.assertEqual(
            [item["source_update_call_sha256"] for item in result_a["source_update_executions"]],
            [item["source_update_call_sha256"] for item in result_b["source_update_executions"]],
        )

    def test_a_process_closes_exact_calls_hashes_events_and_raw_scores(self) -> None:
        result, backend, contract = run_one(run_suffix="happy-a")
        self.assertEqual(result["arm_evaluation_order"], list(core.ARMS))
        self.assertEqual(result["counts"], core.EXPECTED_PROCESS_COUNTS)
        self.assertEqual(backend.reset_calls, 6)
        self.assertEqual(len(backend.update_calls), 5)
        self.assertEqual(len(backend.read_calls), 12)
        self.assertEqual(backend.parameter_hash_calls, 35)
        self.assertEqual(
            [call.source_rule_identity for call in backend.update_calls],
            list(core.SOURCE_IDENTITIES),
        )
        self.assertEqual(
            [call.arm for call in backend.read_calls[:2]],
            list(core.ARMS),
        )
        for execution in result["source_update_executions"]:
            update_hash = execution["update_parameter_hash"]
            self.assertEqual(execution["initial_parameter_hash_after_reset"], contract.initial_parameter_hash)
            self.assertEqual(execution["optimizer_step_count"], 1)
            self.assertEqual(execution["readout_gradient_count"], 0)
            self.assertEqual(execution["readout_optimizer_step_count"], 0)
            self.assertEqual(len(execution["readouts"]), 2)
            for readout in execution["readouts"]:
                self.assertEqual(readout["parameter_hash_before"], update_hash)
                self.assertEqual(readout["parameter_hash_after"], update_hash)
                self.assertEqual(len(readout["row_traces"]), 7)
                self.assertTrue(
                    all(
                        len(trace["ordered_candidate_scores"]) == 7
                        for trace in readout["row_traces"]
                    )
                )
        self.assertEqual(len(result["events"]), 25)
        self.assertTrue(all(event["at_utc"].endswith("Z") for event in result["events"]))
        core.validate_runner_result(result)

    def test_b_process_reverses_every_pre_and_post_arm_order(self) -> None:
        result, backend, _ = run_one(replicate="B", run_suffix="happy-b")
        expected = list(reversed(core.ARMS))
        self.assertEqual(result["arm_evaluation_order"], expected)
        self.assertEqual([item["arm"] for item in result["pre_target_readouts"]], expected)
        self.assertTrue(
            all([readout["arm"] for readout in item["readouts"]] == expected for item in result["source_update_executions"])
        )
        self.assertEqual([call.arm for call in backend.read_calls[:2]], expected)

    def test_ab_reproducibility_indexes_away_reversed_order(self) -> None:
        bindings = make_bindings()
        result_a, _, _ = run_one(replicate="A", run_suffix="ab-a", bindings=bindings)
        result_b, _, _ = run_one(replicate="B", run_suffix="ab-b", bindings=bindings)
        receipt = core.verify_ab_reproducibility(result_a, result_b)
        self.assertEqual(receipt["status"], "PASS_AB_REVERSED_ORDER_REPRODUCIBILITY")
        self.assertEqual(receipt["compared_readout_vectors"], 12)
        self.assertEqual(receipt["compared_raw_candidate_scores"], 588)
        self.assertEqual(receipt["maximum_absolute_raw_score_difference"], 0.0)

    def test_minimal_four_process_collection_closes_protocol_counts(self) -> None:
        bindings = make_bindings()
        results = [
            run_one(
                replicate="A",
                stack=stack,
                run_suffix=f"minimal-{index}",
                bindings=bindings,
            )[0]
            for index, stack in enumerate(core.STACK_IDS)
        ]
        receipt = core.validate_process_collection(results, variant="MINIMAL_4_PROCESS_A")
        self.assertEqual(receipt["counts"]["os_processes"], 4)
        self.assertEqual(receipt["counts"]["unique_design_source_updates"], 20)
        self.assertEqual(receipt["counts"]["technical_update_executions"], 20)
        self.assertEqual(receipt["counts"]["pre_target_identity_reads"], 48)
        self.assertEqual(receipt["counts"]["post_target_identity_reads"], 240)
        self.assertEqual(receipt["counts"]["total_target_identity_metric_cells"], 288)
        self.assertEqual(receipt["ab_reproducibility_receipts"], [])

    def test_eight_process_ab_collection_closes_protocol_counts_and_pairs(self) -> None:
        bindings = make_bindings()
        results = []
        for stack_index, stack in enumerate(core.STACK_IDS):
            for replicate in ("A", "B"):
                results.append(
                    run_one(
                        replicate=replicate,
                        stack=stack,
                        run_suffix=f"full-{stack_index}-{replicate}",
                        bindings=bindings,
                    )[0]
                )
        receipt = core.validate_process_collection(
            list(reversed(results)), variant="REPRODUCIBILITY_8_PROCESS_AB"
        )
        self.assertEqual(receipt["counts"]["os_processes"], 8)
        self.assertEqual(receipt["counts"]["unique_design_source_updates"], 20)
        self.assertEqual(receipt["counts"]["technical_update_executions"], 40)
        self.assertEqual(receipt["counts"]["pre_target_identity_reads"], 96)
        self.assertEqual(receipt["counts"]["post_target_identity_reads"], 480)
        self.assertEqual(receipt["counts"]["total_target_identity_metric_cells"], 576)
        self.assertEqual(len(receipt["ab_reproducibility_receipts"]), 4)

    def test_strict_json_round_trip_preserves_result(self) -> None:
        result, _, _ = run_one(run_suffix="strict-roundtrip")
        payload = core.strict_json_dumps(result)
        self.assertTrue(payload.endswith("\n"))
        self.assertEqual(core.strict_json_loads(payload), result)

    def test_strict_writer_exclusive_creates_and_never_overwrites(self) -> None:
        result, _, _ = run_one(run_suffix="exclusive-write")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            core.write_result_strict(path, result)
            self.assertEqual(core.strict_json_loads(path.read_text(encoding="utf-8")), result)
            with self.assertRaisesRegex(core.R13RunnerError, "refusing to overwrite"):
                core.write_result_strict(path, result)


class R13RunnerFailClosedBackendTests(unittest.TestCase):
    def assert_backend_failure(self, pattern: str, **backend_kwargs: Any) -> None:
        with self.assertRaisesRegex(core.R13RunnerError, pattern):
            run_one(run_suffix="negative", backend_kwargs=backend_kwargs)

    def test_fresh_reset_hash_mismatch_stops_before_readout(self) -> None:
        self.assert_backend_failure("fresh reset failed", reset_hash=digest("wrong-reset"))

    def test_source_update_that_does_not_change_parameters_is_rejected(self) -> None:
        self.assert_backend_failure("did not change parameters", update_no_change=True)

    def test_five_identities_must_not_collapse_to_one_update_hash(self) -> None:
        self.assert_backend_failure("five distinct update hashes", update_same_for_all=True)

    def test_source_update_must_report_exactly_one_optimizer_step(self) -> None:
        self.assert_backend_failure("exactly one optimizer step", update_optimizer_steps=0)
        self.assert_backend_failure("exactly one optimizer step", update_optimizer_steps=2)

    def test_source_update_rejects_realized_clipping(self) -> None:
        self.assert_backend_failure("realized clipping", update_clipping=True)

    def test_source_update_rejects_non_finite_backend_computation(self) -> None:
        self.assert_backend_failure("non-finite computation", update_non_finite=True)

    def test_source_update_diagnostics_cannot_smuggle_target_inputs(self) -> None:
        self.assert_backend_failure(
            "target information leaked",
            update_diagnostics={"target_panel_seen": True},
        )

    def test_source_update_diagnostics_must_be_finite_strict_json(self) -> None:
        self.assert_backend_failure(
            "non-finite JSON number",
            update_diagnostics={"raw_gradient_norm": math.nan},
        )

    def test_target_read_backend_type_is_strict(self) -> None:
        self.assert_backend_failure("wrong target-read type", read_wrong_type=True)

    def test_target_read_rejects_any_gradient(self) -> None:
        self.assert_backend_failure("reported a gradient", read_gradient_count=1)

    def test_target_read_rejects_any_optimizer_step(self) -> None:
        self.assert_backend_failure("reported an optimizer step", read_optimizer_steps=1)

    def test_target_read_rejects_any_state_mutation_receipt(self) -> None:
        self.assert_backend_failure("reported state mutation", read_state_mutations=1)

    def test_target_read_rejects_non_finite_backend_computation(self) -> None:
        self.assert_backend_failure("non-finite computation", read_non_finite=True)

    def test_target_read_parameter_mutation_is_caught_by_hash(self) -> None:
        self.assert_backend_failure("mutated parameters", mutate_on_read_call=1)

    def test_target_read_requires_exactly_seven_rows(self) -> None:
        self.assert_backend_failure("must return seven target rows", row_count=6)

    def test_target_read_requires_bound_row_order(self) -> None:
        self.assert_backend_failure("row order/id drift", reverse_rows=True)

    def test_target_read_requires_seven_candidate_scores(self) -> None:
        self.assert_backend_failure("must contain seven raw scores", score_count=6)

    def test_target_read_rejects_non_finite_raw_score(self) -> None:
        self.assert_backend_failure("non-finite raw score", non_finite_score=True)


class R13RunnerFailClosedContractAndReceiptTests(unittest.TestCase):
    def test_contract_rejects_missing_binding(self) -> None:
        bindings = make_bindings()
        del bindings["authorization_sha256"]
        contract = make_contract(bindings=bindings, run_suffix="missing-binding")
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "binding key set drift"):
            core.run_stack_process(contract, backend, clock=TickClock())

    def test_contract_rejects_candidate_order_drift(self) -> None:
        contract = make_contract(run_suffix="candidate-drift")
        broken = core.R13RunContract(
            **{
                **asdict(contract),
                "bindings": contract.bindings,
                "ordered_candidate_set": tuple(reversed(core.ORDERED_CANDIDATE_SET)),
                "target_panels": contract.target_panels,
            }
        )
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "ordered candidate set"):
            core.run_stack_process(broken, backend, clock=TickClock())

    def test_contract_rejects_nonmatched_h0_h1_row_order(self) -> None:
        contract = make_contract(run_suffix="row-drift")
        h0, h1 = contract.target_panels
        broken_h1 = core.TargetPanelBinding(
            arm=h1.arm,
            target_panel_sha256=h1.target_panel_sha256,
            ordered_rows=tuple(reversed(h1.ordered_rows)),
        )
        broken = core.R13RunContract(
            run_id=contract.run_id,
            bundle_run_id=contract.bundle_run_id,
            replicate_id=contract.replicate_id,
            stack_id=contract.stack_id,
            bindings=contract.bindings,
            initial_parameter_hash=contract.initial_parameter_hash,
            ordered_candidate_set=contract.ordered_candidate_set,
            target_panels=(h0, broken_h1),
        )
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "same seven canonical rows"):
            core.run_stack_process(broken, backend, clock=TickClock())

    def test_contract_rejects_same_h0_h1_panel_commitment(self) -> None:
        contract = make_contract(run_suffix="same-panel")
        h0, h1 = contract.target_panels
        broken_h1 = core.TargetPanelBinding(
            arm=h1.arm,
            target_panel_sha256=h0.target_panel_sha256,
            ordered_rows=h1.ordered_rows,
        )
        broken = core.R13RunContract(
            run_id=contract.run_id,
            bundle_run_id=contract.bundle_run_id,
            replicate_id=contract.replicate_id,
            stack_id=contract.stack_id,
            bindings=contract.bindings,
            initial_parameter_hash=contract.initial_parameter_hash,
            ordered_candidate_set=contract.ordered_candidate_set,
            target_panels=(h0, broken_h1),
        )
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "commitments must differ"):
            core.run_stack_process(broken, backend, clock=TickClock())

    def test_naive_clock_is_rejected(self) -> None:
        contract = make_contract(run_suffix="naive-clock")
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "timezone-aware"):
            core.run_stack_process(
                contract,
                backend,
                clock=lambda: datetime(2026, 8, 6, 1, 2, 3),
            )

    def test_backwards_clock_is_rejected(self) -> None:
        contract = make_contract(run_suffix="backwards-clock")
        backend = FakeBackend(contract.initial_parameter_hash)
        with self.assertRaisesRegex(core.R13RunnerError, "clock moved backwards"):
            core.run_stack_process(contract, backend, clock=BackwardsClock())

    def test_strict_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(core.R13RunnerError, "duplicate JSON key"):
            core.strict_json_loads('{"x":1,"x":2}')

    def test_strict_json_rejects_nan_and_non_json_tuple(self) -> None:
        with self.assertRaisesRegex(core.R13RunnerError, "non-finite JSON constant"):
            core.strict_json_loads('{"x":NaN}')
        with self.assertRaisesRegex(core.R13RunnerError, "non-JSON value"):
            core.strict_json_dumps({"x": (1, 2)})

    def test_result_validator_recomputes_and_rejects_tampered_counts(self) -> None:
        result, _, _ = run_one(run_suffix="tamper-count")
        broken = deepcopy(result)
        broken["counts"]["source_update_calls"] = 4
        with self.assertRaisesRegex(core.R13RunnerError, "count receipt drift"):
            core.validate_runner_result(broken)

    def test_result_validator_rejects_tampered_readout_parameter_hash(self) -> None:
        result, _, _ = run_one(run_suffix="tamper-hash")
        broken = deepcopy(result)
        broken["source_update_executions"][0]["readouts"][0][
            "parameter_hash_after"
        ] = digest("tampered")
        with self.assertRaisesRegex(core.R13RunnerError, "after hash drift"):
            core.validate_runner_result(broken)

    def test_result_validator_rejects_tampered_event_index(self) -> None:
        result, _, _ = run_one(run_suffix="tamper-event")
        broken = deepcopy(result)
        broken["events"][4]["event_index"] = 99
        with self.assertRaisesRegex(core.R13RunnerError, "event index drift"):
            core.validate_runner_result(broken)

    def test_ab_reproducibility_rejects_update_hash_drift(self) -> None:
        bindings = make_bindings()
        result_a, _, _ = run_one(replicate="A", run_suffix="ab-update-a", bindings=bindings)
        result_b, _, _ = run_one(
            replicate="B",
            run_suffix="ab-update-b",
            bindings=bindings,
            backend_kwargs={"update_salt": "different"},
        )
        with self.assertRaisesRegex(core.R13RunnerError, "source update hash mismatch"):
            core.verify_ab_reproducibility(result_a, result_b)

    def test_ab_reproducibility_rejects_raw_score_drift(self) -> None:
        bindings = make_bindings()
        result_a, _, _ = run_one(replicate="A", run_suffix="ab-score-a", bindings=bindings)
        result_b, _, _ = run_one(
            replicate="B",
            run_suffix="ab-score-b",
            bindings=bindings,
            backend_kwargs={"score_salt": 1e-6},
        )
        with self.assertRaisesRegex(core.R13RunnerError, "raw candidate scores differ"):
            core.verify_ab_reproducibility(result_a, result_b)

    def test_ab_reproducibility_accepts_difference_at_tolerance(self) -> None:
        bindings = make_bindings()
        result_a, _, _ = run_one(replicate="A", run_suffix="ab-tol-a", bindings=bindings)
        result_b, _, _ = run_one(
            replicate="B",
            run_suffix="ab-tol-b",
            bindings=bindings,
            backend_kwargs={"score_salt": 5e-13},
        )
        receipt = core.verify_ab_reproducibility(
            result_a, result_b, absolute_tolerance=1e-12
        )
        self.assertLessEqual(receipt["maximum_absolute_raw_score_difference"], 1e-12)

    def test_collection_rejects_duplicate_and_missing_process_key(self) -> None:
        bindings = make_bindings()
        one = run_one(bindings=bindings, run_suffix="duplicate-one")[0]
        with self.assertRaisesRegex(core.R13RunnerError, "duplicate process result"):
            core.validate_process_collection(
                [one, deepcopy(one)], variant="MINIMAL_4_PROCESS_A"
            )
        with self.assertRaisesRegex(core.R13RunnerError, "coverage is incomplete"):
            core.validate_process_collection([one], variant="MINIMAL_4_PROCESS_A")


if __name__ == "__main__":
    unittest.main()
