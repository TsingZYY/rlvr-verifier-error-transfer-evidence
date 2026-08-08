from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from unittest import mock
from typing import Any

import r13_lazy_backend_adapter as adapter
import r13_model_inventory
import r13_runner_core as core


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_g1_development_r1"
SOURCE_BUNDLES = ROOT / "real_assets" / "build_r4_a" / "REAL_SOURCE_BUNDLES_V1.jsonl"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class TickClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 6, 3, 4, 5, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(microseconds=1)
        return result


class FakeNoModelRuntime:
    """In-memory arithmetic only; it never owns or imports a model object."""

    def __init__(
        self,
        *,
        initial_hash: str | None = None,
        action_counts: adapter.ProhibitedModelActionCounts | None = None,
        mutate_on_target_read: bool = False,
    ) -> None:
        self.initial_hash = initial_hash or digest("synthetic-initial-parameter-state")
        self.state_hash = self.initial_hash
        self.action_counts = action_counts or adapter.ProhibitedModelActionCounts()
        self.mutate_on_target_read = mutate_on_target_read
        self.reset_calls = 0
        self.hash_calls = 0
        self.update_specs: list[adapter.SourceExecutionSpec] = []
        self.target_specs: list[adapter.TargetReadSpec] = []

    def prohibited_model_action_counts(self) -> adapter.ProhibitedModelActionCounts:
        return self.action_counts

    def reset_trainable(self) -> None:
        self.reset_calls += 1
        self.state_hash = self.initial_hash

    def parameter_hash(self) -> str:
        self.hash_calls += 1
        return self.state_hash

    def update_source(self, spec: adapter.SourceExecutionSpec) -> core.SourceUpdateResult:
        self.update_specs.append(spec)
        self.state_hash = digest(
            "synthetic-update|"
            + spec.stack_id
            + "|"
            + spec.source_rule_identity
            + "|"
            + spec.source_seed_material_sha256
        )
        return core.SourceUpdateResult(
            optimizer_step_count=1,
            clipping_triggered=False,
            non_finite_observed=False,
            diagnostics={
                "synthetic_raw_norm": 0.125,
                "synthetic_delta_norm": 0.03125,
                "actual_model_action_count": 0,
            },
        )

    def read_target(self, spec: adapter.TargetReadSpec) -> core.TargetReadResult:
        self.target_specs.append(spec)
        arm_term = 10.0 if spec.arm == core.ARMS[1] else 0.0
        phase_term = 1.0 if spec.phase == "POST" else 0.0
        rows = tuple(
            core.RawCandidateRow(
                row_id=row_id,
                ordered_candidate_scores=tuple(
                    arm_term + phase_term + row_index / 100.0 + candidate / 1000.0
                    for candidate in range(7)
                ),
            )
            for row_index, row_id in enumerate(spec.ordered_row_ids)
        )
        if self.mutate_on_target_read:
            self.state_hash = digest("illegal-target-mutation")
        return core.TargetReadResult(
            rows=rows,
            gradient_count=0,
            optimizer_step_count=0,
            state_mutation_count=0,
            non_finite_observed=False,
        )


def bind_contract() -> adapter.StaticAdapterContract:
    return adapter.bind_static_contract(
        protocol_path=FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json",
        update_recipe_path=FORMAL / "R13_UPDATE_RECIPE_CONTRACT_R1.json",
        source_bundles_path=SOURCE_BUNDLES,
        model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
        runtime_environment_path=FORMAL / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json",
    )


def make_run_contract(
    static: adapter.StaticAdapterContract,
    runtime: FakeNoModelRuntime,
    *,
    replicate: str,
) -> core.R13RunContract:
    bindings = {key: digest(f"binding|{key}") for key in core.BINDING_KEYS}
    bindings["protocol_sha256"] = static.protocol_sha256
    bindings["model_inventory_sha256"] = static.model_inventory_sha256
    bindings["source_assets_sha256"] = static.source_assets_sha256
    panels = []
    for arm_index, arm in enumerate(core.ARMS):
        panels.append(
            core.TargetPanelBinding(
                arm=arm,
                target_panel_sha256=digest(f"synthetic-panel|{arm}"),
                ordered_rows=tuple(
                    core.TargetRowBinding(
                        canonical_instance_id=f"canonical-{row_index}",
                        row_id=f"synthetic-{arm_index}-row-{row_index}",
                        panel_row_sha256=digest(f"synthetic-row|{arm}|{row_index}"),
                    )
                    for row_index in range(7)
                ),
            )
        )
    return core.R13RunContract(
        run_id=f"r13-synthetic-{replicate}",
        bundle_run_id="r13-synthetic-no-model-bundle",
        replicate_id=replicate,
        stack_id=core.STACK_IDS[0],
        bindings=bindings,
        initial_parameter_hash=runtime.initial_hash,
        ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        target_panels=tuple(panels),
    )


def run_synthetic(
    static: adapter.StaticAdapterContract,
    *,
    replicate: str,
) -> tuple[dict[str, Any], FakeNoModelRuntime]:
    runtime = FakeNoModelRuntime()
    backend = adapter.LazyR13BackendAdapter.for_synthetic_no_model_test(static, runtime)
    result = core.run_stack_process(
        make_run_contract(static, runtime, replicate=replicate),
        backend,
        clock=TickClock(),
    )
    return result, runtime


class LazyBackendAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static = bind_contract()

    def test_real_static_assets_bind_without_inventory_rehash_of_model_directory(self) -> None:
        with mock.patch.object(
            r13_model_inventory,
            "build_inventory",
            side_effect=AssertionError("model semantic files must not be read"),
        ):
            observed = bind_contract()
        self.assertEqual(observed.schema_version, adapter.SCHEMA_VERSION)
        self.assertEqual(observed.ordered_stack_ids, core.STACK_IDS)
        self.assertEqual(observed.source_identities, core.SOURCE_IDENTITIES)
        self.assertEqual(observed.ordered_candidate_set, core.ORDERED_CANDIDATE_SET)
        self.assertEqual(len(observed.source_stacks), 4)
        self.assertTrue(all(len(stack.ordered_rows) == 14 for stack in observed.source_stacks))
        self.assertFalse(observed.model_execution_authorized)
        self.assertFalse(observed.production_backend_bound)
        self.assertFalse(observed.full_dependency_content_hash_bound)

    def test_module_has_no_ml_import_or_dynamic_import(self) -> None:
        source = (Path(__file__).with_name("r13_lazy_backend_adapter.py")).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        dynamic_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"__import__", "exec", "eval"}:
                    dynamic_calls.append(node.func.id)
        self.assertTrue(
            imported.isdisjoint(
                {"torch", "transformers", "peft", "accelerate", "tokenizers", "safetensors"}
            )
        )
        self.assertEqual(dynamic_calls, [])

    def test_static_only_backend_blocks_every_runner_interface(self) -> None:
        backend = adapter.LazyR13BackendAdapter.static_only(self.static)
        calls = (
            backend.reset_trainable,
            backend.parameter_hash,
            lambda: backend.update_source(object()),  # type: ignore[arg-type]
            lambda: backend.read_target(object()),  # type: ignore[arg-type]
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(adapter.LazyBackendError, "R13_MODEL_EXECUTION_BLOCKED"):
                    call()

    def test_nonzero_model_action_attestation_is_rejected_at_injection(self) -> None:
        runtime = FakeNoModelRuntime(
            action_counts=adapter.ProhibitedModelActionCounts(model_forwards=1)
        )
        with self.assertRaisesRegex(adapter.LazyBackendError, "prohibited model action"):
            adapter.LazyR13BackendAdapter.for_synthetic_no_model_test(self.static, runtime)
        self.assertEqual(runtime.reset_calls, 0)
        self.assertEqual(runtime.update_specs, [])
        self.assertEqual(runtime.target_specs, [])

    def test_full_runner_shape_uses_five_updates_and_twelve_readonly_reads(self) -> None:
        result, runtime = run_synthetic(self.static, replicate="A")
        self.assertEqual(result["counts"]["fresh_reset_calls"], 6)
        self.assertEqual(result["counts"]["source_update_calls"], 5)
        self.assertEqual(result["counts"]["target_read_calls"], 12)
        self.assertEqual(runtime.reset_calls, 6)
        self.assertEqual(len(runtime.update_specs), 5)
        self.assertEqual(len(runtime.target_specs), 12)
        self.assertEqual(runtime.prohibited_model_action_counts(), adapter.ProhibitedModelActionCounts())
        self.assertTrue(all(spec.require_grad is False for spec in runtime.target_specs))
        self.assertTrue(all(spec.optimizer_steps_permitted == 0 for spec in runtime.target_specs))
        self.assertTrue(all(spec.state_mutation_permitted is False for spec in runtime.target_specs))
        for identity in core.SOURCE_IDENTITIES:
            reads = [
                spec
                for spec in runtime.target_specs
                if spec.phase == "POST" and spec.source_rule_identity == identity
            ]
            self.assertEqual(len(reads), 2)
            self.assertEqual({spec.arm for spec in reads}, set(core.ARMS))
            self.assertEqual(len({spec.source_update_hash for spec in reads}), 1)

    def test_a_b_order_reversal_cannot_change_source_execution_specs(self) -> None:
        _result_a, runtime_a = run_synthetic(self.static, replicate="A")
        _result_b, runtime_b = run_synthetic(self.static, replicate="B")
        self.assertEqual(runtime_a.update_specs, runtime_b.update_specs)
        self.assertEqual(
            [spec.arm for spec in runtime_a.target_specs[:2]],
            list(core.ARM_ORDER_BY_REPLICATE["A"]),
        )
        self.assertEqual(
            [spec.arm for spec in runtime_b.target_specs[:2]],
            list(core.ARM_ORDER_BY_REPLICATE["B"]),
        )

    def test_source_projection_is_exactly_fourteen_rows_and_target_blind(self) -> None:
        _result, runtime = run_synthetic(self.static, replicate="A")
        first = runtime.update_specs[0]
        self.assertEqual(len(first.ordered_source_rows), 14)
        self.assertEqual(first.source_rule_identity, "Z7_PLUS1")
        self.assertEqual(first.source_offset_mod7, 1)
        for row in first.ordered_source_rows:
            self.assertEqual(sum(row.reward_mask), 2)
            self.assertNotEqual(row.gold_candidate, row.source_wrong_candidate)
            self.assertEqual(len(row.ordered_candidate_set), 7)

        def walk(value: Any) -> None:
            if hasattr(value, "__dataclass_fields__"):
                for field in fields(value):
                    lowered = field.name.lower()
                    self.assertFalse(
                        lowered != "lora_targets"
                        and any(
                            token in lowered
                            for token in ("arm", "target", "panel", "replicate")
                        ),
                        field.name,
                    )
                    walk(getattr(value, field.name))
            elif isinstance(value, tuple):
                for item in value:
                    walk(item)

        walk(first)

    def test_request_hash_or_seed_tampering_fails_before_runtime_update(self) -> None:
        runtime = FakeNoModelRuntime()
        backend = adapter.LazyR13BackendAdapter.for_synthetic_no_model_test(self.static, runtime)
        backend.reset_trainable()
        base = core.SourceUpdateCall(
            stack_id=core.STACK_IDS[0],
            source_rule_identity=core.SOURCE_IDENTITIES[0],
            source_assets_sha256=self.static.source_assets_sha256,
            protocol_sha256=self.static.protocol_sha256,
            source_seed_material_sha256=digest("wrong-seed"),
        )
        with self.assertRaisesRegex(adapter.LazyBackendError, "seed commitment drift"):
            backend.update_source(base)
        with self.assertRaisesRegex(adapter.LazyBackendError, "source asset hash drift"):
            backend.update_source(replace(base, source_assets_sha256=digest("wrong-source")))
        with self.assertRaisesRegex(adapter.LazyBackendError, "source protocol hash drift"):
            backend.update_source(replace(base, protocol_sha256=digest("wrong-protocol")))
        self.assertEqual(runtime.update_specs, [])

    def test_target_mutation_is_detected_even_if_fake_receipt_claims_readonly(self) -> None:
        runtime = FakeNoModelRuntime(mutate_on_target_read=True)
        backend = adapter.LazyR13BackendAdapter.for_synthetic_no_model_test(self.static, runtime)
        backend.reset_trainable()
        request = core.TargetReadCall(
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            phase="PRE",
            arm=core.ARMS[0],
            source_rule_identity=None,
            source_update_hash=runtime.initial_hash,
            target_panel_sha256=digest("synthetic-target-panel"),
            ordered_row_ids=tuple(f"row-{index}" for index in range(7)),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        )
        with self.assertRaisesRegex(adapter.LazyBackendError, "mutated fake parameters"):
            backend.read_target(request)

    def test_incomplete_one_arm_read_blocks_reset(self) -> None:
        runtime = FakeNoModelRuntime()
        backend = adapter.LazyR13BackendAdapter.for_synthetic_no_model_test(self.static, runtime)
        backend.reset_trainable()
        request = core.TargetReadCall(
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            phase="PRE",
            arm=core.ARMS[0],
            source_rule_identity=None,
            source_update_hash=runtime.initial_hash,
            target_panel_sha256=digest("synthetic-target-panel"),
            ordered_row_ids=tuple(f"row-{index}" for index in range(7)),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        )
        backend.read_target(request)
        with self.assertRaisesRegex(adapter.LazyBackendError, "both target interfaces"):
            backend.reset_trainable()
        with self.assertRaisesRegex(adapter.LazyBackendError, "more than once"):
            backend.read_target(request)


if __name__ == "__main__":
    unittest.main()
