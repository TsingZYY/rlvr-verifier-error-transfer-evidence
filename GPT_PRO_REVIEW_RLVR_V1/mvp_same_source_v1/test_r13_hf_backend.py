from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import inspect
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from typing import Any

import r13_hf_backend as hf
import r13_lazy_backend_adapter as lazy
import r13_runner_core as core


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_g1_development_r1"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind_static() -> lazy.StaticAdapterContract:
    return lazy.bind_static_contract(
        protocol_path=FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json",
        update_recipe_path=FORMAL / "R13_UPDATE_RECIPE_CONTRACT_R1.json",
        source_bundles_path=ROOT
        / "real_assets"
        / "build_r4_a"
        / "REAL_SOURCE_BUNDLES_V1.jsonl",
        model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
        runtime_environment_path=FORMAL / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json",
    )


class FakeTokenizer:
    def __init__(self, *, unstable_prefix: bool = False) -> None:
        self.unstable_prefix = unstable_prefix
        self.calls: list[tuple[Any, bool, bool]] = []

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> str:
        self.calls.append((messages, tokenize, add_generation_prompt))
        base = "<system>" + messages[0]["content"] + "<user>" + messages[1]["content"]
        prefix = base + "<assistant>"
        if add_generation_prompt:
            return prefix
        if self.unstable_prefix:
            prefix = base + "<different_assistant>"
        return prefix + messages[-1]["content"] + "<assistant_end>"

    def __call__(self, text: str, *, add_special_tokens: bool) -> dict[str, list[int]]:
        if add_special_tokens:
            raise AssertionError("reference scorer must disable implicit special tokens")
        return {"input_ids": [ord(character) for character in text]}


class FakeNumpyView:
    def __init__(self, values: tuple[float, ...]) -> None:
        self.values = values

    def tobytes(self, *, order: str) -> bytes:
        if order != "C":
            raise AssertionError("parameter hash must use C order")
        return b"".join(struct.pack("<f", value) for value in self.values)


class FakeTensor:
    def __init__(self, values: tuple[float, ...], *, requires_grad: bool) -> None:
        self.values = tuple(float(value) for value in values)
        self.requires_grad = requires_grad
        self.shape = (len(values),)
        self.device = "cpu"
        self.dtype = "float32"

    def detach(self) -> "FakeTensor":
        return self

    def float(self) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def contiguous(self) -> "FakeTensor":
        return self

    def numpy(self) -> FakeNumpyView:
        return FakeNumpyView(self.values)

    def clone(self) -> "FakeTensor":
        return FakeTensor(self.values, requires_grad=self.requires_grad)

    def to(self, device: str, dtype: str) -> "FakeTensor":
        copied = self.clone()
        copied.device = device
        copied.dtype = dtype
        return copied

    def copy_(self, other: "FakeTensor") -> None:
        self.values = other.values
        self.shape = other.shape


class FakeModel:
    def __init__(self, parameters: list[tuple[str, FakeTensor]]) -> None:
        self._parameters = parameters

    def named_parameters(self) -> list[tuple[str, FakeTensor]]:
        return list(self._parameters)


class FakeTargetProvider:
    def __init__(self, payload: hf.BoundTargetPayload) -> None:
        self.payload = payload
        self.requests: list[lazy.TargetReadSpec] = []

    def resolve(self, request: lazy.TargetReadSpec) -> hf.BoundTargetPayload:
        self.requests.append(request)
        return self.payload


def make_source_spec() -> lazy.SourceExecutionSpec:
    candidates = core.ORDERED_CANDIDATE_SET
    rows = []
    for index in range(14):
        gold_index = index % 7
        wrong_index = (gold_index + 1) % 7
        reward = tuple(
            1 if candidate_index in (gold_index, wrong_index) else 0
            for candidate_index in range(7)
        )
        rows.append(
            lazy.SourceTrainingRowSpec(
                row_id=f"source-row-{index}",
                prompt_text=f"PROMPT={index}\n",
                prompt_sha256=digest(f"prompt-{index}"),
                row_bytes_sha256=digest(f"row-{index}"),
                canonical_z=gold_index,
                gold_candidate=candidates[gold_index],
                source_wrong_candidate=candidates[wrong_index],
                ordered_candidate_set=candidates,
                reward_mask=reward,
            )
        )
    return lazy.SourceExecutionSpec(
        stack_id=core.STACK_IDS[0],
        source_rule_identity=core.SOURCE_IDENTITIES[0],
        source_offset_mod7=1,
        source_assets_sha256=digest("source-assets"),
        source_bundle_sha256=digest("source-bundle"),
        protocol_sha256=digest("protocol"),
        source_seed_material_sha256=digest("seed"),
        model_inventory_sha256=digest("model-inventory"),
        update_recipe_sha256=digest("update-recipe"),
        update=lazy.UpdateHyperparameters(
            learning_rate=0.1,
            gradient_clip_norm=1.0,
            lora_rank=4,
            lora_alpha=8,
            lora_dropout=0.0,
            lora_targets=("q_proj", "v_proj"),
            optimizer="manual_sgd_no_state",
            steps=1,
        ),
        ordered_candidate_set=candidates,
        ordered_source_rows=tuple(rows),
    )


class R13HFBackendReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static = bind_static()
        cls.plan = hf.build_reference_plan(
            cls.static,
            parent_r10_runner_path=ROOT / "mvp_same_source_v1" / "run_same_source_mvp.py",
        )

    def test_real_reference_plan_binds_exact_parent_and_remains_blocked(self) -> None:
        self.assertEqual(self.plan.schema_version, hf.SCHEMA_VERSION)
        self.assertEqual(
            self.plan.parent_r10_runner_sha256_reference_only,
            hf.PARENT_R10_RUNNER_SHA256,
        )
        self.assertEqual(self.plan.model_repository, "HuggingFaceTB/SmolLM2-360M-Instruct")
        self.assertEqual(
            self.plan.model_revision,
            "a10cc1512eabd3dde888204e902eca88bddb4951",
        )
        self.assertEqual(self.plan.precision, "base_bfloat16_lora_float32")
        self.assertFalse(self.plan.model_execution_authorized)
        self.assertFalse(self.plan.local_development_addendum_bound)
        self.assertFalse(self.plan.local_target_loader_bound)
        self.assertFalse(self.plan.local_runtime_invocation_bound)
        self.assertFalse(self.plan.production_backend_bound)
        self.assertFalse(self.plan.full_dependency_content_hash_bound)
        self.assertEqual(len(self.plan.unresolved_blockers), 2)
        self.assertEqual(len(hf.LOCAL_ADDENDUM_BINDINGS_REQUIRED), 3)

    def test_parent_runner_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.py"
            path.write_text("# not the bound runner\n", encoding="utf-8")
            with self.assertRaisesRegex(hf.HFBackendError, "parent R10 runner hash drift"):
                hf.build_reference_plan(self.static, parent_r10_runner_path=path)

    def test_module_imports_no_ml_package_at_top_level(self) -> None:
        source = Path(hf.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module.split(".")[0])
        self.assertTrue(
            top_level_imports.isdisjoint(
                {"torch", "transformers", "peft", "accelerate", "tokenizers", "safetensors"}
            )
        )

    def test_all_current_backend_operations_fail_before_lazy_import(self) -> None:
        backend = hf.R13HFBackend(self.plan)
        calls = (
            lambda: backend.initialize(model_dir=Path("never-read")),
            backend.reset_trainable,
            backend.parameter_hash,
            lambda: backend.update_source(object()),  # type: ignore[arg-type]
            lambda: backend.read_target(object()),  # type: ignore[arg-type]
            lambda: hf._lazy_import_hf_dependencies(self.plan),
        )
        with mock.patch.object(
            hf.importlib,
            "import_module",
            side_effect=AssertionError("ML import reached before authorization"),
        ) as imported:
            for call in calls:
                with self.subTest(call=call):
                    with self.assertRaisesRegex(hf.HFBackendError, "R13_HF_EXECUTION_BLOCKED"):
                        call()
        imported.assert_not_called()

    def test_local_development_gate_does_not_require_production_custody_flags(self) -> None:
        local_plan = replace(
            self.plan,
            model_execution_authorized=True,
            local_development_addendum_bound=True,
            local_target_loader_bound=True,
            local_runtime_invocation_bound=True,
            unresolved_blockers=(),
        )
        self.assertFalse(local_plan.production_backend_bound)
        self.assertFalse(local_plan.full_dependency_content_hash_bound)
        with mock.patch.object(
            hf.importlib,
            "import_module",
            side_effect=RuntimeError("sentinel lazy import reached"),
        ) as imported:
            with self.assertRaisesRegex(RuntimeError, "sentinel lazy import reached"):
                hf._lazy_import_hf_dependencies(local_plan)
        imported.assert_called_once_with("torch")

    def test_candidate_encoding_supervises_assistant_candidate_and_common_terminator(self) -> None:
        tokenizer = FakeTokenizer()
        candidate = "FINAL=K3"
        encoded = hf.encode_candidate_reference(tokenizer, "TASK=X\n", candidate)
        terminator = "<assistant_end>"
        self.assertEqual(encoded.supervised_token_count, len(candidate + terminator))
        self.assertEqual(encoded.supervised_mask.count(True), len(candidate + terminator))
        self.assertTrue(all(not value for value in encoded.supervised_mask[:-len(candidate + terminator)]))
        self.assertEqual(len(tokenizer.calls), 2)
        self.assertTrue(tokenizer.calls[0][2])
        self.assertFalse(tokenizer.calls[1][2])
        self.assertEqual(tokenizer.calls[1][0][-1], {"role": "assistant", "content": candidate})

    def test_candidate_encoding_rejects_non_prefix_stable_chat_template(self) -> None:
        with self.assertRaisesRegex(hf.HFBackendError, "prefix is not token-prefix stable"):
            hf.encode_candidate_reference(FakeTokenizer(unstable_prefix=True), "TASK=X", "FINAL=K0")

    def test_trainable_hash_is_sorted_float32_and_excludes_frozen_parameters(self) -> None:
        a = FakeTensor((1.0, 2.5), requires_grad=True)
        b = FakeTensor((-3.0,), requires_grad=True)
        frozen = FakeTensor((999.0,), requires_grad=False)
        model = FakeModel([("z.weight", b), ("frozen.weight", frozen), ("a.weight", a)])
        observed = hf.sha256_trainable_reference(model)
        expected = hashlib.sha256()
        for name, values in (("a.weight", (1.0, 2.5)), ("z.weight", (-3.0,))):
            expected.update(name.encode("utf-8"))
            expected.update(str((len(values),)).encode("ascii"))
            expected.update(b"".join(struct.pack("<f", value) for value in values))
        self.assertEqual(observed, expected.hexdigest())
        reordered = FakeModel([("a.weight", a), ("z.weight", b), ("frozen.weight", frozen)])
        self.assertEqual(hf.sha256_trainable_reference(reordered), observed)

    def test_snapshot_restore_recovers_exact_trainable_hash_without_touching_frozen(self) -> None:
        trainable = FakeTensor((1.0, 2.0), requires_grad=True)
        frozen = FakeTensor((7.0,), requires_grad=False)
        model = FakeModel([("adapter", trainable), ("base", frozen)])
        initial_hash = hf.sha256_trainable_reference(model)
        snapshot = hf.snapshot_trainable_reference(model)
        trainable.values = (9.0, 9.0)
        frozen.values = (8.0,)
        entered = []

        @contextmanager
        def no_grad():
            entered.append(True)
            yield

        hf.restore_trainable_reference(model, snapshot, no_grad_context=no_grad)
        self.assertEqual(hf.sha256_trainable_reference(model), initial_hash)
        self.assertEqual(frozen.values, (8.0,))
        self.assertEqual(entered, [True])

    def test_source_update_plan_freezes_math_but_refuses_ambiguous_execution(self) -> None:
        plan = hf.build_source_update_execution_plan(make_source_spec())
        self.assertEqual(len(plan.ordered_row_ids), 14)
        self.assertEqual(len(plan.ordered_reward_masks), 14)
        self.assertTrue(all(sum(mask) == 2 for mask in plan.ordered_reward_masks))
        self.assertEqual(plan.learning_rate, 0.1)
        self.assertEqual(plan.gradient_clip_norm, 1.0)
        self.assertEqual(plan.optimizer, "manual_sgd_no_state")
        self.assertFalse(plan.executable)
        self.assertEqual(len(plan.blockers), 2)
        self.assertIn("GRADIENT_CALL_SHAPE_CONFLICT", plan.blockers[0])
        self.assertIn("CLIPPING_STOP_POINT_CONFLICT", plan.blockers[1])

    def test_target_reference_forces_no_grad_and_preserves_parameter_hash(self) -> None:
        rows = tuple(
            hf.BoundTargetRow(
                row_id=f"target-row-{index}",
                prompt_text=f"TARGET={index}\n",
                prompt_sha256=hashlib.sha256(f"TARGET={index}\n".encode("utf-8")).hexdigest(),
            )
            for index in range(7)
        )
        update_hash = digest("updated-parameters")
        request = lazy.TargetReadSpec(
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            phase="POST",
            arm=core.ARMS[0],
            source_rule_identity=core.SOURCE_IDENTITIES[0],
            source_update_hash=update_hash,
            target_panel_sha256=digest("target-panel"),
            ordered_row_ids=tuple(row.row_id for row in rows),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
            require_grad=False,
            optimizer_steps_permitted=0,
            state_mutation_permitted=False,
        )
        provider = FakeTargetProvider(
            hf.BoundTargetPayload(
                stack_id=request.stack_id,
                arm=request.arm,
                target_panel_sha256=request.target_panel_sha256,
                manifest_sha256=digest("manifest"),
                allowlist_receipt_sha256=digest("allowlist"),
                ordered_rows=rows,
            )
        )
        score_calls = []

        def score(
            prompt_text: str,
            candidates: tuple[str, ...],
            *,
            require_grad: bool,
        ) -> tuple[float, ...]:
            score_calls.append((prompt_text, candidates, require_grad))
            return tuple(float(index) / 10.0 for index in range(7))

        result = hf.execute_target_readonly_reference(
            request,
            payload_provider=provider,
            score_candidates=score,
            parameter_hash=lambda: update_hash,
        )
        self.assertEqual(len(result.rows), 7)
        self.assertEqual(result.gradient_count, 0)
        self.assertEqual(result.optimizer_step_count, 0)
        self.assertEqual(result.state_mutation_count, 0)
        self.assertTrue(all(call[2] is False for call in score_calls))
        self.assertEqual(provider.requests, [request])

    def test_target_reference_detects_parameter_mutation(self) -> None:
        rows = tuple(
            hf.BoundTargetRow(
                row_id=f"row-{index}",
                prompt_text=f"P={index}",
                prompt_sha256=hashlib.sha256(f"P={index}".encode("utf-8")).hexdigest(),
            )
            for index in range(7)
        )
        initial = digest("initial")
        request = lazy.TargetReadSpec(
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            phase="PRE",
            arm=core.ARMS[1],
            source_rule_identity=None,
            source_update_hash=initial,
            target_panel_sha256=digest("panel"),
            ordered_row_ids=tuple(row.row_id for row in rows),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
            require_grad=False,
            optimizer_steps_permitted=0,
            state_mutation_permitted=False,
        )
        provider = FakeTargetProvider(
            hf.BoundTargetPayload(
                stack_id=request.stack_id,
                arm=request.arm,
                target_panel_sha256=request.target_panel_sha256,
                manifest_sha256=digest("manifest"),
                allowlist_receipt_sha256=digest("allowlist"),
                ordered_rows=rows,
            )
        )
        hashes = iter((initial, digest("mutated")))
        with self.assertRaisesRegex(hf.HFBackendError, "mutated trainable parameters"):
            hf.execute_target_readonly_reference(
                request,
                payload_provider=provider,
                score_candidates=lambda *_args, **_kwargs: tuple(0.0 for _ in range(7)),
                parameter_hash=lambda: next(hashes),
            )

    def test_real_target_payloads_enter_only_through_validated_disk_loader(self) -> None:
        asset_root = FORMAL / "r13_assets_r1"
        validator_path = FORMAL / "validate_r13_target_alignment_results_r1.py"
        protocol_path = FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
        manifest_path = asset_root / "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
        allowlist_path = asset_root / "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"
        provider = hf.load_disk_bound_target_payload_provider(
            validator_path=validator_path,
            expected_validator_sha256=file_digest(validator_path),
            protocol_path=protocol_path,
            expected_protocol_sha256=file_digest(protocol_path),
            bundle_root=asset_root,
            expected_manifest_sha256=file_digest(manifest_path),
            expected_allowlist_receipt_sha256=file_digest(allowlist_path),
        )
        payload = provider.panel_payload(core.STACK_IDS[0], core.ARMS[0])
        self.assertEqual(len(payload.ordered_rows), 7)
        self.assertTrue(all(row.prompt_text for row in payload.ordered_rows))
        request = lazy.TargetReadSpec(
            stack_id=payload.stack_id,
            replicate_id="A",
            phase="PRE",
            arm=payload.arm,
            source_rule_identity=None,
            source_update_hash=digest("initial"),
            target_panel_sha256=payload.target_panel_sha256,
            ordered_row_ids=tuple(row.row_id for row in payload.ordered_rows),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
            require_grad=False,
            optimizer_steps_permitted=0,
            state_mutation_permitted=False,
        )
        self.assertEqual(provider.resolve(request), payload)
        with self.assertRaisesRegex(hf.HFBackendError, "validating loader"):
            hf.DiskBoundTargetPayloadProvider((payload,), construction_token=object())

    def test_disk_target_loader_rejects_validator_hash_drift_before_import(self) -> None:
        asset_root = FORMAL / "r13_assets_r1"
        validator_path = FORMAL / "validate_r13_target_alignment_results_r1.py"
        protocol_path = FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
        with self.assertRaisesRegex(hf.HFBackendError, "validator byte hash drift"):
            hf.load_disk_bound_target_payload_provider(
                validator_path=validator_path,
                expected_validator_sha256=digest("wrong-validator"),
                protocol_path=protocol_path,
                expected_protocol_sha256=file_digest(protocol_path),
                bundle_root=asset_root,
                expected_manifest_sha256=file_digest(
                    asset_root / "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
                ),
                expected_allowlist_receipt_sha256=file_digest(
                    asset_root / "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"
                ),
            )

    def test_reference_code_records_exact_no_grad_scoring_and_hf_lora_load_shape(self) -> None:
        scoring = inspect.getsource(hf.candidate_log_scores_reference)
        initialization = inspect.getsource(hf._initialize_hf_reference_after_future_authorization)
        for fragment in (
            "torch.no_grad()",
            'device_type="cuda"',
            "torch.bfloat16",
            "use_cache=False",
            "supervised_mask[:, 1:]",
            "functional.log_softmax",
        ):
            self.assertIn(fragment, scoring)
        for fragment in (
            "local_files_only=True",
            "use_fast=True",
            'target_modules=["q_proj", "v_proj"]',
            "lora_alpha=8",
            "lora_dropout=0.0",
            "torch.float32",
        ):
            self.assertIn(fragment, initialization)


if __name__ == "__main__":
    unittest.main()
