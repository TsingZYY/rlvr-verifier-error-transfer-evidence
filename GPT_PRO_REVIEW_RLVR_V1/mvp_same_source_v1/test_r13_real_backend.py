from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from typing import Any

import r13_hf_backend as hf
import r13_lazy_backend_adapter as lazy
import r13_real_backend as real
import r13_runner_core as core


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_g1_development_r1"
MODEL_DIR = ROOT.parent / "models" / "SmolLM2-360M-Instruct-a10cc151"


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
        / "build_v5_repair_a"
        / "REAL_SOURCE_BUNDLES_V1.jsonl",
        model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
        runtime_environment_path=FORMAL / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json",
    )


def load_provider() -> hf.DiskBoundTargetPayloadProvider:
    asset_root = FORMAL / "r13_assets_r1"
    validator = FORMAL / "validate_r13_target_alignment_results_r1.py"
    protocol = FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
    return hf.load_disk_bound_target_payload_provider(
        validator_path=validator,
        expected_validator_sha256=file_digest(validator),
        protocol_path=protocol,
        expected_protocol_sha256=file_digest(protocol),
        bundle_root=asset_root,
        expected_manifest_sha256=file_digest(
            asset_root / "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json"
        ),
        expected_allowlist_receipt_sha256=file_digest(
            asset_root / "R13_MATCHED_TARGET_PANEL_ALLOWLIST_VALIDATION_R1.json"
        ),
    )


class FakeNumpyView:
    def __init__(self, values: tuple[float, ...]) -> None:
        self.values = values

    def tobytes(self, *, order: str) -> bytes:
        if order != "C":
            raise AssertionError("hash must use C-order bytes")
        return b"".join(struct.pack("<f", float(value)) for value in self.values)


class FakeTensor:
    def __init__(
        self,
        values: tuple[float, ...],
        *,
        requires_grad: bool = True,
        mutation_counter: list[int] | None = None,
    ) -> None:
        self.values = tuple(float(value) for value in values)
        self.requires_grad = requires_grad
        self.shape = (len(values),)
        self.device = "cuda:0"
        self.dtype = "float32"
        self.grad: FakeTensor | None = None
        self._mutation_counter = mutation_counter

    def detach(self) -> "FakeTensor":
        return self

    def float(self) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def contiguous(self) -> "FakeTensor":
        return self

    def reshape(self, *_shape: int) -> "FakeTensor":
        return self

    def tolist(self) -> list[float]:
        return list(self.values)

    def numpy(self) -> FakeNumpyView:
        return FakeNumpyView(self.values)

    def clone(self) -> "FakeTensor":
        copied = FakeTensor(
            self.values,
            requires_grad=self.requires_grad,
            mutation_counter=self._mutation_counter,
        )
        copied.device = self.device
        copied.dtype = self.dtype
        return copied

    def to(self, device: str, dtype: str) -> "FakeTensor":
        copied = self.clone()
        copied.device = device
        copied.dtype = dtype
        return copied

    def copy_(self, other: "FakeTensor") -> None:
        self.values = tuple(other.values)
        self.shape = other.shape
        if self._mutation_counter is not None:
            self._mutation_counter[0] += 1

    def add_(self, other: "FakeTensor", *, alpha: float) -> None:
        self.values = tuple(
            left + alpha * right for left, right in zip(self.values, other.values)
        )
        if self._mutation_counter is not None:
            self._mutation_counter[0] += 1


class FakeModel:
    def __init__(self) -> None:
        self.mutations = [0]
        self.parameter = FakeTensor((1.0, -1.0), mutation_counter=self.mutations)
        self.device = "cuda:0"
        self.zero_grad_calls = 0

    def named_parameters(self) -> list[tuple[str, FakeTensor]]:
        return [("base_model.layer.q_proj.lora_A.default.weight", self.parameter)]

    def zero_grad(self, *, set_to_none: bool) -> None:
        if not set_to_none:
            raise AssertionError("backend must zero with set_to_none=True")
        self.zero_grad_calls += 1
        self.parameter.grad = None


class FakeTorch:
    float32 = "float32"

    @staticmethod
    @contextmanager
    def no_grad():
        yield


class FakeTokenizer:
    pad_token_id = 0
    eos_token = "<eos>"
    padding_side = "right"

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> str:
        if tokenize:
            raise AssertionError("reference tokenizer must return text")
        prefix = (
            "<system>"
            + messages[0]["content"]
            + "<user>"
            + messages[1]["content"]
            + "<assistant>"
        )
        if add_generation_prompt:
            return prefix
        return prefix + messages[-1]["content"] + "<end>"

    def __call__(self, text: str, *, add_special_tokens: bool) -> dict[str, list[int]]:
        if add_special_tokens:
            raise AssertionError("implicit special tokens are forbidden")
        return {"input_ids": [ord(character) for character in text]}


class Hooks:
    def __init__(self, *, accumulated_gradient: float = 0.5) -> None:
        self.accumulated_gradient = accumulated_gradient
        self.backward_rows: list[Any] = []
        self.score_calls: list[tuple[str, bool]] = []
        self.mutate_during_score = False

    def backward(self, row: Any, candidates: tuple[str, ...], runtime: real.LocalRuntime) -> float:
        self.backward_rows.append(row)
        if candidates != core.ORDERED_CANDIDATE_SET:
            raise AssertionError("candidate order drift")
        parameter = runtime.model.parameter
        previous = (0.0, 0.0) if parameter.grad is None else parameter.grad.values
        contribution = self.accumulated_gradient / 14.0
        parameter.grad = FakeTensor(
            (previous[0] + contribution, previous[1]),
            requires_grad=False,
        )
        return float(len(self.backward_rows)) / 14.0

    def score(
        self,
        prompt: str,
        candidates: tuple[str, ...],
        require_grad: bool,
        runtime: real.LocalRuntime,
    ) -> tuple[float, ...]:
        self.score_calls.append((prompt, require_grad))
        if require_grad:
            raise AssertionError("read-only score hook received require_grad=True")
        if candidates != core.ORDERED_CANDIDATE_SET:
            raise AssertionError("candidate order drift")
        if self.mutate_during_score:
            self.mutate_during_score = False
            runtime.model.parameter.add_(FakeTensor((0.1, 0.0)), alpha=1.0)
        return tuple(-float(index) for index in range(7))


def make_runtime(model: FakeModel | None = None) -> real.LocalRuntime:
    model = model or FakeModel()
    dependencies = hf.HFDependencies(
        torch=FakeTorch,
        functional=None,
        auto_model_for_causal_lm=None,
        auto_tokenizer=None,
        lora_config=None,
        task_type=None,
        get_peft_model=None,
    )
    return real.LocalRuntime(
        dependencies=dependencies,
        tokenizer=FakeTokenizer(),
        model=model,
        dependency_versions={"torch": "fake-1", "transformers": "fake-2", "peft": "fake-3"},
        device_name="FAKE CUDA",
        device_capability=(9, 0),
        bf16_supported=True,
    )


class R13RealBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static = bind_static()
        cls.provider = load_provider()

    def make_backend(
        self,
        *,
        provider: hf.DiskBoundTargetPayloadProvider | None = None,
        accumulated_gradient: float = 0.5,
        initialize: bool = True,
        events: list[str] | None = None,
    ) -> tuple[real.R13RealBackend, Hooks, FakeModel]:
        hooks = Hooks(accumulated_gradient=accumulated_gradient)
        model = FakeModel()
        events = events if events is not None else []

        def inventory_validator(_inventory: dict[str, Any], directory: Path) -> None:
            self.assertEqual(directory, MODEL_DIR.resolve())
            events.append("inventory")

        def loader(directory: Path) -> real.LocalRuntime:
            self.assertEqual(directory, MODEL_DIR.resolve())
            events.append("loader")
            return make_runtime(model)

        backend = real.R13RealBackend(
            static_contract=self.static,
            semantics_path=FORMAL
            / "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json",
            canary_spec_path=FORMAL / "R13_LOCAL_TECHNICAL_CANARY_SPEC_R1.json",
            model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
            model_dir=MODEL_DIR,
            target_payload_provider=provider,
            runtime_loader=loader,
            inventory_validator=inventory_validator,
            source_backward_hook=hooks.backward,
            score_hook=hooks.score,
            peak_memory_reader=lambda: 123456,
        )
        if initialize:
            backend.initialize_local(self.permit())
        return backend, hooks, model

    def permit(self) -> real.LocalInitializationPermit:
        return real.LocalInitializationPermit(
            authorization_basis="USER_IN_THREAD_EXPLICIT_AUTHORIZATION_FOR_CODEX_TO_RUN_THE_MVP",
            scope="R13_LOCAL_TECHNICAL_CANARY_THEN_HASH_BOUND_MANIFEST",
            addendum_sha256=real.EXPECTED_SEMANTICS_SHA256,
            canary_spec_sha256=real.EXPECTED_CANARY_SPEC_SHA256,
            model_inventory_sha256=self.static.model_inventory_sha256,
            runtime_reference_sha256=self.static.runtime_environment_reference_sha256,
            allow_local_model_initialization=True,
        )

    def authorize_unit_science(self, backend: real.R13RealBackend) -> None:
        # Public authorization is covered separately by manifest-activation tests.
        backend._scientific_manifest_sha256 = digest("unit-test-manifest")

    def source_call(self, *, identity: str = "Z7_PLUS1") -> core.SourceUpdateCall:
        stack = core.STACK_IDS[0]
        seed = real._source_seed(
            protocol_sha256=self.static.protocol_sha256,
            source_assets_sha256=self.static.source_assets_sha256,
            stack_id=stack,
            source_rule_identity=identity,
        )
        return core.SourceUpdateCall(
            stack_id=stack,
            source_rule_identity=identity,
            source_assets_sha256=self.static.source_assets_sha256,
            protocol_sha256=self.static.protocol_sha256,
            source_seed_material_sha256=seed,
        )

    def target_call(
        self,
        backend: real.R13RealBackend,
        *,
        arm: str,
        phase: str,
        identity: str | None,
    ) -> core.TargetReadCall:
        payload = self.provider.panel_payload(core.STACK_IDS[0], arm)
        return core.TargetReadCall(
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            phase=phase,
            arm=arm,
            source_rule_identity=identity,
            source_update_hash=backend.parameter_hash(),
            target_panel_sha256=payload.target_panel_sha256,
            ordered_row_ids=tuple(row.row_id for row in payload.ordered_rows),
            ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        )

    def test_constructor_is_ml_import_free_and_initialize_orders_inventory_first(self) -> None:
        events: list[str] = []
        backend, _hooks, _model = self.make_backend(
            provider=None,
            initialize=False,
            events=events,
        )
        self.assertFalse(backend.initialized)
        self.assertEqual(events, [])
        backend.initialize_local(self.permit())
        self.assertEqual(events, ["inventory", "loader"])
        self.assertTrue(backend.initialized)
        self.assertEqual(backend.model_action_counts["model_forward_calls"], 0)
        self.assertRegex(backend.initial_parameter_hash, r"^[0-9a-f]{64}$")

    def test_default_ml_import_happens_only_inside_explicit_initialize(self) -> None:
        backend = real.R13RealBackend(
            static_contract=self.static,
            semantics_path=FORMAL
            / "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json",
            canary_spec_path=FORMAL / "R13_LOCAL_TECHNICAL_CANARY_SPEC_R1.json",
            model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
            model_dir=MODEL_DIR,
            target_payload_provider=None,
            inventory_validator=lambda *_args: None,
        )
        with mock.patch.object(
            real.importlib,
            "import_module",
            side_effect=RuntimeError("sentinel ML import"),
        ) as imported:
            self.assertFalse(imported.called)
            with self.assertRaisesRegex(RuntimeError, "sentinel ML import"):
                backend.initialize_local(self.permit())
            imported.assert_called_once_with("torch")

    def test_exact_semantics_and_canary_hashes_are_bound(self) -> None:
        self.assertEqual(
            file_digest(
                FORMAL
                / "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json"
            ),
            real.EXPECTED_SEMANTICS_SHA256,
        )
        self.assertEqual(
            file_digest(FORMAL / "R13_LOCAL_TECHNICAL_CANARY_SPEC_R1.json"),
            real.EXPECTED_CANARY_SPEC_SHA256,
        )
        backend, _hooks, _model = self.make_backend(provider=None, initialize=False)
        bad = self.permit()
        bad = real.LocalInitializationPermit(
            **{**bad.__dict__, "addendum_sha256": digest("wrong")}
        )
        with self.assertRaisesRegex(real.R13RealBackendError, "addendum_sha256"):
            backend.initialize_local(bad)

    def test_canary_needs_no_target_provider_and_never_scores_scientific_prompts(self) -> None:
        backend, hooks, _model = self.make_backend(provider=None)
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "canary.json"
            receipt = backend.run_technical_canary(receipt_path=receipt_path)
            self.assertEqual(set(receipt), set(real.CANARY_RECEIPT_FIELDS))
            self.assertFalse(set(receipt) & real.CANARY_FORBIDDEN_FIELDS)
            self.assertNotIn("ordered_candidate_scores", receipt)
            self.assertNotIn("candidate_probabilities", receipt)
            self.assertEqual(receipt["model_action_counts"]["model_forward_calls"], 16)
            self.assertEqual(receipt["model_action_counts"]["backward_calls"], 14)
            self.assertEqual(len(hooks.backward_rows), 14)
            self.assertEqual(len(hooks.score_calls), 2)
            self.assertTrue(
                all(
                    real.CANARY_NAMESPACE in row.prompt_text
                    for row in hooks.backward_rows
                )
            )
            self.assertTrue(
                all(real.CANARY_NAMESPACE in prompt for prompt, _ in hooks.score_calls)
            )
            self.assertEqual(receipt["scientific_source_cells"], 0)
            self.assertEqual(receipt["scientific_target_cells"], 0)
            self.assertEqual(
                hf.sha256_trainable_reference(backend._runtime().model),
                backend.initial_parameter_hash,
            )

    def test_existing_canary_attestation_is_action_free_and_fail_closed(self) -> None:
        producer, _hooks, _model = self.make_backend(provider=None)
        worker, _worker_hooks, _worker_model = self.make_backend(provider=self.provider)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canary.json"
            producer.run_technical_canary(receipt_path=path)
            before = dict(worker.model_action_counts)
            observed = worker.attest_existing_canary_receipt(receipt_path=path)
            self.assertEqual(observed, file_digest(path))
            self.assertEqual(dict(worker.model_action_counts), before)
            second, _hooks2, _model2 = self.make_backend(provider=self.provider)
            value = json.loads(path.read_text(encoding="ascii"))
            value["model_action_counts"]["model_forward_calls"] = 15
            tampered = Path(directory) / "tampered.json"
            tampered.write_bytes(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
                    "ascii"
                )
                + b"\n"
            )
            with self.assertRaisesRegex(real.R13RealBackendError, "action-count"):
                second.attest_existing_canary_receipt(receipt_path=tampered)

    def test_source_projection_is_target_blind_and_uses_exact_bound_row_order(self) -> None:
        backend, _hooks, _model = self.make_backend(provider=self.provider)
        spec = backend._build_source_spec(self.source_call(identity="Z7_PLUS3"))
        self.assertEqual(len(spec.ordered_source_rows), 14)
        self.assertEqual(
            tuple(row.row_id for row in spec.ordered_source_rows),
            tuple(
                row.row_id
                for row in self.static.source_stack(core.STACK_IDS[0]).ordered_rows
            ),
        )
        for field in spec.__dataclass_fields__:
            self.assertFalse(real._is_forbidden_source_name(field))
        for row in spec.ordered_source_rows:
            self.assertEqual(sum(row.reward_mask), 2)

    def test_fourteen_backward_calls_numeric_norm_one_manual_step_and_reset(self) -> None:
        backend, hooks, model = self.make_backend(provider=self.provider)
        self.authorize_unit_science(backend)
        backend.reset_trainable()
        initial = backend.parameter_hash()
        mutations_before_update = model.mutations[0]
        result = backend.update_source(self.source_call())
        self.assertEqual(len(hooks.backward_rows), 14)
        self.assertEqual(result.optimizer_step_count, 1)
        self.assertFalse(result.clipping_triggered)
        self.assertAlmostEqual(result.diagnostics["raw_gradient_norm"], 0.5)
        self.assertAlmostEqual(result.diagnostics["realized_update_norm"], 0.05)
        self.assertAlmostEqual(result.diagnostics["mean_expected_reward"], 15.0 / 28.0)
        self.assertEqual(result.diagnostics["manual_parameter_steps"], 1)
        self.assertNotEqual(backend.parameter_hash(), initial)
        self.assertEqual(model.mutations[0] - mutations_before_update, 1)
        for arm in core.ARMS:
            backend.read_target(
                self.target_call(backend, arm=arm, phase="POST", identity="Z7_PLUS1")
            )
        backend.reset_trainable()
        self.assertEqual(backend.parameter_hash(), initial)
        self.assertGreaterEqual(model.mutations[0], 2)

    def test_over_threshold_stop_occurs_before_any_parameter_mutation(self) -> None:
        backend, hooks, model = self.make_backend(
            provider=self.provider,
            accumulated_gradient=1.25,
        )
        self.authorize_unit_science(backend)
        backend.reset_trainable()
        initial = backend.parameter_hash()
        mutations_before = model.mutations[0]
        with self.assertRaisesRegex(real.R13LocalTechnicalStop, "exceeds") as caught:
            backend.update_source(self.source_call())
        self.assertEqual(len(hooks.backward_rows), 14)
        self.assertEqual(model.mutations[0], mutations_before)
        self.assertEqual(backend.parameter_hash(), initial)
        self.assertTrue(
            caught.exception.diagnostics[
                "parameter_hash_preserved_before_technical_stop"
            ]
        )
        self.assertEqual(backend.model_action_counts["manual_parameter_steps"], 0)

    def test_nonfinite_stop_occurs_before_any_parameter_mutation(self) -> None:
        backend, _hooks, model = self.make_backend(
            provider=self.provider,
            accumulated_gradient=float("inf"),
        )
        self.authorize_unit_science(backend)
        backend.reset_trainable()
        mutations_before = model.mutations[0]
        with self.assertRaisesRegex(real.R13LocalTechnicalStop, "non-finite"):
            backend.update_source(self.source_call())
        self.assertEqual(model.mutations[0], mutations_before)
        self.assertEqual(backend.model_action_counts["manual_parameter_steps"], 0)

    def test_both_target_arms_use_same_hash_and_no_grad_validated_provider(self) -> None:
        backend, hooks, _model = self.make_backend(provider=self.provider)
        self.authorize_unit_science(backend)
        backend.reset_trainable()
        shared_hash = backend.parameter_hash()
        results = []
        for arm in core.ARMS:
            call = self.target_call(backend, arm=arm, phase="PRE", identity=None)
            self.assertEqual(call.source_update_hash, shared_hash)
            results.append(backend.read_target(call))
        self.assertEqual([len(result.rows) for result in results], [7, 7])
        self.assertEqual(backend.parameter_hash(), shared_hash)
        self.assertEqual(len(hooks.score_calls), 14)
        self.assertTrue(all(require_grad is False for _, require_grad in hooks.score_calls))

    def test_target_mutation_is_detected_by_before_after_hash(self) -> None:
        backend, hooks, _model = self.make_backend(provider=self.provider)
        self.authorize_unit_science(backend)
        backend.reset_trainable()
        hooks.mutate_during_score = True
        request = self.target_call(
            backend,
            arm=core.ARMS[0],
            phase="PRE",
            identity=None,
        )
        with self.assertRaisesRegex(hf.HFBackendError, "mutated trainable parameters"):
            backend.read_target(request)

    def test_scientific_activation_requires_provider_and_preserves_worker_preflight_mode(self) -> None:
        no_provider, _hooks, _model = self.make_backend(provider=None)
        no_provider._test_injections_present = False
        no_provider._canary_receipt_sha256 = digest("canary")
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text("{}", encoding="ascii")
            with self.assertRaisesRegex(real.R13RealBackendError, "validated disk target provider"):
                no_provider.activate_scientific_manifest(
                    manifest_path=manifest_path,
                    expected_manifest_sha256=file_digest(manifest_path),
                )

            backend, _hooks2, _model2 = self.make_backend(provider=self.provider)
            # Exercise only activation plumbing; production activation itself
            # rejects every injected runtime hook.
            backend._test_injections_present = False
            backend._canary_receipt_sha256 = digest("canary")
            backend_hash = file_digest(Path(real.__file__))
            manifest = {
                "artifacts": {
                    "real_backend": {"sha256": backend_hash},
                    "canary_receipt": {"sha256": digest("canary")},
                    "local_execution_semantics_addendum": {
                        "sha256": real.EXPECTED_SEMANTICS_SHA256
                    },
                    "local_technical_canary_spec": {
                        "sha256": real.EXPECTED_CANARY_SPEC_SHA256
                    },
                }
            }

            class FakeManifestModule:
                calls: list[bool] = []

                @staticmethod
                def read_manifest(_path: Path) -> dict[str, Any]:
                    return manifest

                @classmethod
                def verify_manifest_against_filesystem(
                    cls,
                    _manifest: dict[str, Any],
                    *,
                    project_root: Path,
                    require_output_root_absent: bool,
                ) -> None:
                    self.assertEqual(project_root, ROOT)
                    cls.calls.append(require_output_root_absent)

            with mock.patch.object(
                real.importlib,
                "import_module",
                return_value=FakeManifestModule,
            ):
                backend.activate_scientific_manifest(
                    manifest_path=manifest_path,
                    expected_manifest_sha256=file_digest(manifest_path),
                    output_root_was_preflighted_absent_by_coordinator=True,
                )
            self.assertEqual(FakeManifestModule.calls, [False])

    def test_production_source_math_contains_exact_negative_er_over_fourteen_backward(self) -> None:
        source = inspect.getsource(real.R13RealBackend._backward_row)
        self.assertIn("loss = -expected / SOURCE_ROW_COUNT", source)
        self.assertIn("loss.backward()", source)
        update = inspect.getsource(real.R13RealBackend._apply_accumulated_update)
        self.assertIn("raw_norm > GRADIENT_NORM_THRESHOLD", update)
        self.assertLess(update.index("raw_norm > GRADIENT_NORM_THRESHOLD"), update.index("self._manual_sgd"))


if __name__ == "__main__":
    unittest.main()
