from __future__ import annotations

"""Local-development Hugging Face backend for the frozen R13 runner.

Importing this module is model-action free.  ML packages and model bytes are
only touched by an explicit :meth:`R13RealBackend.initialize_local` call.  A
non-scientific technical canary must then pass and be hash-bound into the final
local manifest before the public ``R13Backend`` methods can touch a scientific
source or target cell.
"""

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping

import r13_hf_backend as hf_backend
import r13_lazy_backend_adapter as lazy_adapter
import r13_model_inventory as model_inventory_contract
import r13_runner_core as runner


SCHEMA_VERSION = "r13-real-local-development-backend-r1"
EXPECTED_SEMANTICS_SHA256 = (
    "0f7dc4d795c9f397644fdb168735cb99084cc52db815fc6d2e952d091f065714"
)
EXPECTED_CANARY_SPEC_SHA256 = (
    "3c03ce09b60e6a97abc5706815513e52b7fc5ea4a5e5fcd54818a9653cc6644b"
)
EXPECTED_HF_REFERENCE_SHA256 = (
    "d3d9894d373f6e2d847cf3ba0da8d698463cba6e3072ba0213cc01b4b70baa88"
)
EXPECTED_RUNNER_SHA256 = (
    "15b4f7f5606a6c889d92aab4837b8f9cf468e6651db1d4763f3161b57d682f96"
)
EXPECTED_PROTOCOL_SHA256 = (
    "9e5c5aa889beecb5d66cd5a1920c9aa86ed3040586ec744cbb069216e20c06ab"
)
EXPECTED_UPDATE_RECIPE_SHA256 = (
    "8e2cea0380d995e44b3a94327ed9116849d3152ae1b721642785a955ccf73dbd"
)
SYSTEM_PROMPT = hf_backend.SYSTEM_PROMPT
SOURCE_ROW_COUNT = 14
GRADIENT_NORM_THRESHOLD = 1.0
LEARNING_RATE = 0.1
CANARY_RECEIPT_SCHEMA = "r13-local-technical-canary-receipt-r1"
CANARY_PASS_STATUS = "PASS_LOCAL_ENGINEERING_CANARY"
CANARY_EVIDENCE_BOUNDARY = "LOCAL_ENGINEERING_CANARY_ONLY_NOT_R13_EVIDENCE"
CANARY_NAMESPACE = "R13_TECHNICAL_CANARY_NOT_A_SCIENTIFIC_CELL_V1"

CANARY_RECEIPT_FIELDS = (
    "schema_version",
    "status",
    "created_at_utc",
    "canary_spec_sha256",
    "backend_sha256",
    "model_inventory_sha256",
    "runtime_reference_sha256",
    "dependency_versions",
    "device_name",
    "device_capability",
    "bf16_supported",
    "trainable_parameter_count",
    "candidate_supervised_token_count_min",
    "candidate_supervised_token_count_max",
    "all_candidate_scores_finite",
    "all_gradients_finite",
    "raw_gradient_norm",
    "raw_gradient_norm_threshold_pass",
    "parameter_changed_after_update",
    "target_read_hash_preserved",
    "reset_hash_restored",
    "peak_gpu_memory_bytes",
    "elapsed_seconds",
    "scientific_source_cells",
    "scientific_target_cells",
    "model_action_counts",
    "evidence_boundary",
)
CANARY_FORBIDDEN_FIELDS = frozenset(
    {
        "ordered_candidate_scores",
        "candidate_probabilities",
        "r13_source_result",
        "r13_target_result",
        "scientific_gate",
        "mechanism_decision",
    }
)

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SOURCE_IDENTITY_RE = re.compile(r"Z7_PLUS([1-5])")
_FORBIDDEN_SOURCE_TOKENS = ("arm", "target", "panel", "replicate")


class R13RealBackendError(RuntimeError):
    """Fail-closed local-backend contract violation."""


class R13LocalTechnicalStop(R13RealBackendError):
    """A non-finite or over-threshold update stopped before mutation."""

    def __init__(self, message: str, diagnostics: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise R13RealBackendError(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise R13RealBackendError(f"cannot hash bound file: {path}") from error
    return digest.hexdigest()


def _reject_pairs(label: str):
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            _require(key not in value, f"duplicate JSON key in {label}: {key}")
            value[key] = item
        return value

    return reject


def _read_strict_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs(label),
            parse_constant=lambda token: (_ for _ in ()).throw(
                R13RealBackendError(
                    f"non-finite JSON constant in {label}: {token}"
                )
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R13RealBackendError(f"invalid strict JSON for {label}: {path}") from error
    _require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _valid_hash(value: Any, label: str) -> str:
    _require(
        isinstance(value, str) and _HASH_RE.fullmatch(value) is not None,
        f"{label} must be a lowercase SHA-256",
    )
    return value


def _is_forbidden_source_name(value: str) -> bool:
    lowered = value.lower()
    return lowered != "lora_targets" and any(
        token in lowered for token in _FORBIDDEN_SOURCE_TOKENS
    )


def _assert_source_spec_target_blind(spec: lazy_adapter.SourceExecutionSpec) -> None:
    def walk(value: Any, location: str) -> None:
        if hasattr(value, "__dataclass_fields__"):
            for field in fields(value):
                _require(
                    not _is_forbidden_source_name(field.name),
                    f"forbidden field entered source projection: {location}.{field.name}",
                )
                walk(getattr(value, field.name), f"{location}.{field.name}")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                _require(isinstance(key, str), f"non-string source key at {location}")
                _require(
                    not _is_forbidden_source_name(key),
                    f"forbidden key entered source projection: {location}.{key}",
                )
                walk(item, f"{location}.{key}")
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")

    walk(spec, "source_spec")


def _source_seed(
    *,
    protocol_sha256: str,
    source_assets_sha256: str,
    stack_id: str,
    source_rule_identity: str,
) -> str:
    raw = json.dumps(
        {
            "protocol_sha256": protocol_sha256,
            "source_assets_sha256": source_assets_sha256,
            "stack_id": stack_id,
            "source_rule_identity": source_rule_identity,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_semantics(
    value: dict[str, Any],
    *,
    path: Path,
    static_contract: lazy_adapter.StaticAdapterContract,
) -> None:
    _require(_sha256_file(path) == EXPECTED_SEMANTICS_SHA256, "local semantics hash drift")
    _require(
        value.get("schema_version")
        == "r13-local-development-execution-semantics-addendum-r1",
        "local semantics schema drift",
    )
    bindings = value.get("parent_bindings")
    _require(isinstance(bindings, dict), "local semantics parent bindings missing")
    expected_bindings = {
        "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
        "update_recipe_sha256": EXPECTED_UPDATE_RECIPE_SHA256,
        "hf_reference_backend_sha256": EXPECTED_HF_REFERENCE_SHA256,
        "runner_core_sha256": EXPECTED_RUNNER_SHA256,
    }
    for key, expected in expected_bindings.items():
        _require(bindings.get(key) == expected, f"local semantics binding drift: {key}")
    _require(
        static_contract.protocol_sha256 == EXPECTED_PROTOCOL_SHA256,
        "static protocol does not match local semantics",
    )
    _require(
        static_contract.update_recipe_sha256 == EXPECTED_UPDATE_RECIPE_SHA256,
        "static update recipe does not match local semantics",
    )
    _require(
        _sha256_file(Path(hf_backend.__file__).resolve()) == EXPECTED_HF_REFERENCE_SHA256,
        "HF reference backend hash drift",
    )
    _require(
        _sha256_file(Path(runner.__file__).resolve()) == EXPECTED_RUNNER_SHA256,
        "runner core hash drift",
    )
    scope = value.get("selected_execution_scope", {})
    _require(
        scope.get("variant") == "REPRODUCIBILITY_8_PROCESS_AB"
        and scope.get("physical_execution")
        == "SERIAL_ONE_GPU_ONE_OS_PROCESS_AT_A_TIME"
        and scope.get("parallel_gpu_processes_allowed") is False,
        "local execution-scope semantics drift",
    )
    gradient = value.get("gradient_accumulation_resolution", {})
    _require(
        gradient.get("selected_semantics") == "R10_OPERATIONAL_MICROBATCH_CONTINUITY"
        and gradient.get("source_rows") == SOURCE_ROW_COUNT
        and gradient.get("backward_calls") == SOURCE_ROW_COUNT
        and gradient.get("logical_update_steps") == 1
        and gradient.get("manual_parameter_steps") == 1
        and gradient.get("optimizer_state") == "none",
        "gradient-accumulation semantics drift",
    )
    norm = value.get("gradient_norm_and_clipping_resolution", {})
    _require(
        norm.get("threshold") == GRADIENT_NORM_THRESHOLD
        and norm.get("if_non_finite")
        == "TECHNICAL_STOP_BEFORE_PARAMETER_MUTATION_NO_SCIENTIFIC_INTERPRETATION"
        and norm.get("if_raw_norm_greater_than_threshold")
        == "TECHNICAL_STOP_BEFORE_PARAMETER_MUTATION_NO_SCIENTIFIC_INTERPRETATION"
        and norm.get("clipping_triggered_on_valid_result") is False
        and norm.get("failed_cell_retry_under_same_manifest") is False,
        "gradient-norm gate semantics drift",
    )
    scoring = value.get("candidate_scoring_resolution", {})
    _require(
        scoring.get("system_prompt") == SYSTEM_PROMPT
        and tuple(scoring.get("ordered_candidates", ()))
        == runner.ORDERED_CANDIDATE_SET
        and scoring.get("prefix_stability_required") is True,
        "candidate-scoring semantics drift",
    )
    model = value.get("model_and_update_resolution", {})
    expected_model = {
        "repository": "HuggingFaceTB/SmolLM2-360M-Instruct",
        "revision": "a10cc1512eabd3dde888204e902eca88bddb4951",
        "local_files_only": True,
        "base_precision": "bfloat16",
        "lora_trainable_precision": "float32",
        "device": "cuda:0",
        "seed": 20260804,
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "lora_rank": 4,
        "lora_alpha": 8,
        "lora_dropout": 0.0,
        "learning_rate": LEARNING_RATE,
    }
    for key, expected in expected_model.items():
        _require(model.get(key) == expected, f"model/update semantics drift: {key}")
    _require(tuple(model.get("lora_targets", ())) == ("q_proj", "v_proj"), "LoRA target drift")
    target = value.get("target_read_resolution", {})
    _require(
        target.get("caller_supplied_target_rows_allowed") is False
        and target.get("gradient_allowed") is False
        and target.get("optimizer_step_allowed") is False
        and target.get("parameter_or_state_mutation_allowed") is False
        and target.get("same_post_update_hash_for_both_arms") is True
        and target.get("verify_trainable_hash_before_and_after_each_arm") is True,
        "target read-only semantics drift",
    )


def _validate_canary_spec(value: dict[str, Any], *, path: Path) -> None:
    _require(_sha256_file(path) == EXPECTED_CANARY_SPEC_SHA256, "technical-canary spec hash drift")
    _require(
        value.get("schema_version") == "r13-local-technical-canary-spec-r1",
        "technical-canary schema drift",
    )
    _require(
        value.get("parent_semantics", {}).get("sha256")
        == EXPECTED_SEMANTICS_SHA256,
        "technical-canary parent-semantics binding drift",
    )
    exclusion = value.get("scientific_cell_exclusion", {})
    _require(
        exclusion.get("uses_r13_source_bundle") is False
        and exclusion.get("uses_r13_matched_target_panel") is False
        and exclusion.get("uses_r10_or_r13_task_id") is False
        and exclusion.get("namespace") == CANARY_NAMESPACE
        and exclusion.get("canary_outcome_may_change_scientific_design_or_gates")
        is False,
        "technical-canary scientific-cell exclusion drift",
    )
    source = value.get("source_canary", {})
    _require(
        source.get("row_count") == SOURCE_ROW_COUNT
        and source.get("ordered_indices") == list(range(SOURCE_ROW_COUNT))
        and source.get("backward_calls") == SOURCE_ROW_COUNT
        and source.get("learning_rate") == LEARNING_RATE
        and source.get("raw_gradient_norm_threshold") == GRADIENT_NORM_THRESHOLD
        and source.get("raw_norm_failure_action") == "STOP_BEFORE_PARAMETER_MUTATION"
        and source.get("manual_parameter_steps_on_pass") == 1,
        "technical-canary update semantics drift",
    )
    _require(
        value.get("expected_action_shape")
        == {
            "tokenizer_loads": 1,
            "model_weight_loads": 1,
            "model_forward_calls": 16,
            "backward_calls": 14,
            "manual_parameter_steps": 1,
            "scientific_source_cells": 0,
            "scientific_target_cells": 0,
        },
        "technical-canary action shape drift",
    )
    _require(
        tuple(value.get("allowed_receipt_fields", ())) == CANARY_RECEIPT_FIELDS,
        "technical-canary receipt allowlist drift",
    )
    _require(
        set(value.get("receipt_forbidden_fields", ())) == CANARY_FORBIDDEN_FIELDS,
        "technical-canary forbidden receipt fields drift",
    )
    _require(
        value.get("candidate_scoring", {}).get(
            "candidate_scores_may_be_written_or_displayed"
        )
        is False
        and value.get("readonly_canary", {}).get("scores_may_be_written_or_displayed")
        is False,
        "technical-canary score-disclosure boundary drift",
    )


@dataclass(frozen=True)
class LocalInitializationPermit:
    """Explicit user-authorized local initialization, not paper custody."""

    authorization_basis: str
    scope: str
    addendum_sha256: str
    canary_spec_sha256: str
    model_inventory_sha256: str
    runtime_reference_sha256: str
    allow_local_model_initialization: bool


@dataclass(frozen=True)
class LocalRuntime:
    dependencies: hf_backend.HFDependencies
    tokenizer: Any
    model: Any
    dependency_versions: Mapping[str, str]
    device_name: str
    device_capability: tuple[int, int]
    bf16_supported: bool


@dataclass(frozen=True)
class _CanaryRow:
    row_id: str
    prompt_text: str
    reward_mask: tuple[int, ...]


SourceBackwardHook = Callable[[Any, tuple[str, ...], LocalRuntime], float]
ScoreHook = Callable[[str, tuple[str, ...], bool, LocalRuntime], tuple[float, ...]]
RuntimeLoader = Callable[[Path], LocalRuntime]
InventoryValidator = Callable[[dict[str, Any], Path], None]


def _default_runtime_loader(model_dir: Path) -> LocalRuntime:
    """Load the exact local HF/PEFT stack; called only after all static gates."""

    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    transformers = importlib.import_module("transformers")
    peft = importlib.import_module("peft")
    dependencies = hf_backend.HFDependencies(
        torch=torch,
        functional=functional,
        auto_model_for_causal_lm=transformers.AutoModelForCausalLM,
        auto_tokenizer=transformers.AutoTokenizer,
        lora_config=peft.LoraConfig,
        task_type=peft.TaskType,
        get_peft_model=peft.get_peft_model,
    )
    _require(torch.cuda.is_available(), "local R13 backend requires CUDA")
    _require(torch.cuda.is_bf16_supported(), "local R13 backend requires CUDA BF16")
    torch.manual_seed(20260804)
    torch.cuda.manual_seed_all(20260804)
    torch.use_deterministic_algorithms(True, warn_only=False)
    torch.cuda.set_device("cuda:0")
    torch.cuda.reset_peak_memory_stats("cuda:0")
    tokenizer = dependencies.auto_tokenizer.from_pretrained(
        model_dir,
        use_fast=True,
        local_files_only=True,
        revision="a10cc1512eabd3dde888204e902eca88bddb4951",
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    model = dependencies.auto_model_for_causal_lm.from_pretrained(
        model_dir,
        torch_dtype=torch.bfloat16,
        local_files_only=True,
        low_cpu_mem_usage=True,
        revision="a10cc1512eabd3dde888204e902eca88bddb4951",
    ).to("cuda:0")
    model.config.use_cache = False
    lora = dependencies.lora_config(
        task_type=dependencies.task_type.CAUSAL_LM,
        inference_mode=False,
        r=4,
        lora_alpha=8,
        lora_dropout=0.0,
        target_modules=["q_proj", "v_proj"],
        bias="none",
    )
    model = dependencies.get_peft_model(model, lora)
    model.eval()
    trainable = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    ]
    _require(bool(trainable), "LoRA initialization exposed no trainable parameters")
    _require(
        all("lora_" in name for name, _ in trainable),
        "a non-LoRA parameter is trainable",
    )
    _require(
        all(parameter.dtype == torch.float32 for _, parameter in trainable),
        "LoRA trainables are not float32",
    )
    capability = torch.cuda.get_device_capability("cuda:0")
    return LocalRuntime(
        dependencies=dependencies,
        tokenizer=tokenizer,
        model=model,
        dependency_versions={
            "torch": str(torch.__version__),
            "transformers": str(transformers.__version__),
            "peft": str(peft.__version__),
        },
        device_name=str(torch.cuda.get_device_name("cuda:0")),
        device_capability=(int(capability[0]), int(capability[1])),
        bf16_supported=bool(torch.cuda.is_bf16_supported()),
    )


class R13RealBackend:
    """Concrete ``runner.R13Backend`` for the frozen local R13 semantics."""

    def __init__(
        self,
        *,
        static_contract: lazy_adapter.StaticAdapterContract,
        semantics_path: Path,
        canary_spec_path: Path,
        model_inventory_path: Path,
        model_dir: Path,
        target_payload_provider: hf_backend.DiskBoundTargetPayloadProvider | None,
        runtime_loader: RuntimeLoader | None = None,
        inventory_validator: InventoryValidator | None = None,
        source_backward_hook: SourceBackwardHook | None = None,
        score_hook: ScoreHook | None = None,
        peak_memory_reader: Callable[[], int] | None = None,
    ) -> None:
        _require(
            target_payload_provider is None
            or type(target_payload_provider)
            is hf_backend.DiskBoundTargetPayloadProvider,
            "target payload provider must be absent or the validated disk provider",
        )
        self.static_contract = static_contract
        self.semantics_path = semantics_path.resolve(strict=True)
        self.canary_spec_path = canary_spec_path.resolve(strict=True)
        self.model_inventory_path = model_inventory_path.resolve(strict=True)
        self.model_dir = model_dir.resolve(strict=True)
        self.target_payload_provider = target_payload_provider
        self._semantics = _read_strict_json(self.semantics_path, "local semantics")
        _validate_semantics(
            self._semantics,
            path=self.semantics_path,
            static_contract=static_contract,
        )
        self._canary_spec = _read_strict_json(
            self.canary_spec_path, "technical-canary spec"
        )
        _validate_canary_spec(self._canary_spec, path=self.canary_spec_path)
        try:
            self._inventory = model_inventory_contract.read_inventory(
                self.model_inventory_path
            )
        except model_inventory_contract.InventoryError as error:
            raise R13RealBackendError("invalid bound model inventory") from error
        self._inventory_sha256 = _sha256_file(self.model_inventory_path)
        _require(
            self._inventory_sha256 == static_contract.model_inventory_sha256,
            "model inventory/static-contract hash mismatch",
        )
        _require(
            self._inventory["repository"] == static_contract.model_repository
            and self._inventory["revision"] == static_contract.model_revision,
            "model inventory identity drift",
        )
        self._runtime_loader = runtime_loader or _default_runtime_loader
        self._inventory_validator = inventory_validator or (
            lambda inventory, directory: model_inventory_contract.validate_inventory(
                inventory, directory
            )
        )
        self._source_backward_hook = source_backward_hook
        self._score_hook = score_hook
        self._peak_memory_reader = peak_memory_reader
        self._test_injections_present = any(
            value is not None
            for value in (
                runtime_loader,
                inventory_validator,
                source_backward_hook,
                score_hook,
                peak_memory_reader,
            )
        )
        self._runtime_state: LocalRuntime | None = None
        self._initial_snapshot: dict[str, Any] | None = None
        self._initial_hash: str | None = None
        self._canary_receipt_sha256: str | None = None
        self._scientific_manifest_sha256: str | None = None
        self._reset_count = 0
        self._update_identity: str | None = None
        self._update_hash: str | None = None
        self._read_phase: str | None = None
        self._read_arms: set[str] = set()
        self._scientific_source_cells = 0
        self._scientific_target_cells = 0
        self._model_forward_calls = 0
        self._backward_calls = 0
        self._manual_parameter_steps = 0

    @property
    def initialized(self) -> bool:
        return self._runtime_state is not None

    @property
    def initial_parameter_hash(self) -> str:
        """Initial float32 LoRA hash; this performs no forward or scientific read."""

        _require(self._initial_hash is not None, "initial parameter hash requested before initialization")
        return self._initial_hash

    @property
    def model_action_counts(self) -> Mapping[str, int]:
        """Score-free local action counters suitable for process receipts."""

        initialized_count = 1 if self.initialized else 0
        return {
            "tokenizer_loads": initialized_count,
            "model_weight_loads": initialized_count,
            "model_forward_calls": self._model_forward_calls,
            "backward_calls": self._backward_calls,
            "manual_parameter_steps": self._manual_parameter_steps,
            "scientific_source_cells": self._scientific_source_cells,
            "scientific_target_cells": self._scientific_target_cells,
        }

    def _runtime(self) -> LocalRuntime:
        _require(self._runtime_state is not None, "local backend is not initialized")
        return self._runtime_state

    def initialize_local(self, permit: LocalInitializationPermit) -> None:
        """Verify exact bytes, then import/load ML dependencies exactly once."""

        _require(type(permit) is LocalInitializationPermit, "local initialization permit type drift")
        _require(self._runtime_state is None, "local backend is already initialized")
        _require(
            permit.authorization_basis
            == "USER_IN_THREAD_EXPLICIT_AUTHORIZATION_FOR_CODEX_TO_RUN_THE_MVP",
            "local initialization authorization basis drift",
        )
        _require(
            permit.scope == "R13_LOCAL_TECHNICAL_CANARY_THEN_HASH_BOUND_MANIFEST",
            "local initialization scope drift",
        )
        _require(permit.allow_local_model_initialization is True, "local model initialization not permitted")
        expected = {
            "addendum_sha256": EXPECTED_SEMANTICS_SHA256,
            "canary_spec_sha256": EXPECTED_CANARY_SPEC_SHA256,
            "model_inventory_sha256": self._inventory_sha256,
            "runtime_reference_sha256": self.static_contract.runtime_environment_reference_sha256,
        }
        for field, expected_value in expected.items():
            _require(
                getattr(permit, field) == expected_value,
                f"local initialization permit binding drift: {field}",
            )
        # Exact model-byte inventory is checked before the first ML import/load.
        self._inventory_validator(self._inventory, self.model_dir)
        runtime_state = self._runtime_loader(self.model_dir)
        _require(type(runtime_state) is LocalRuntime, "runtime loader returned the wrong type")
        _require(runtime_state.bf16_supported is True, "runtime loader did not prove BF16 support")
        _require(
            isinstance(runtime_state.dependency_versions, Mapping)
            and bool(runtime_state.dependency_versions),
            "runtime dependency versions missing",
        )
        self._runtime_state = runtime_state
        self._assert_float32_lora_trainables()
        self._initial_snapshot = hf_backend.snapshot_trainable_reference(runtime_state.model)
        self._initial_hash = hf_backend.sha256_trainable_reference(runtime_state.model)

    def _assert_float32_lora_trainables(self) -> None:
        runtime_state = self._runtime()
        torch = runtime_state.dependencies.torch
        trainable = [
            (name, parameter)
            for name, parameter in runtime_state.model.named_parameters()
            if parameter.requires_grad
        ]
        _require(bool(trainable), "model has no trainable parameters")
        _require(all("lora_" in name for name, _ in trainable), "non-LoRA trainable parameter observed")
        _require(
            all(parameter.dtype == torch.float32 for _, parameter in trainable),
            "trainable LoRA parameter precision is not float32",
        )

    def _trainable(self) -> list[tuple[str, Any]]:
        return sorted(
            (
                (name, parameter)
                for name, parameter in self._runtime().model.named_parameters()
                if parameter.requires_grad
            ),
            key=lambda pair: pair[0],
        )

    def _zero_grad(self) -> None:
        self._runtime().model.zero_grad(set_to_none=True)

    def _restore_initial(self) -> str:
        runtime_state = self._runtime()
        _require(self._initial_snapshot is not None and self._initial_hash is not None, "initial snapshot missing")
        hf_backend.restore_trainable_reference(
            runtime_state.model,
            self._initial_snapshot,
            no_grad_context=runtime_state.dependencies.torch.no_grad,
        )
        self._zero_grad()
        observed = hf_backend.sha256_trainable_reference(runtime_state.model)
        _require(observed == self._initial_hash, "trainable reset hash drift")
        return observed

    def _gradient_statistics(self) -> tuple[bool, float, int]:
        torch = self._runtime().dependencies.torch
        squared: list[float] = []
        count = 0
        for name, parameter in self._trainable():
            gradient = parameter.grad
            _require(gradient is not None, f"missing trainable gradient: {name}")
            _require(gradient.dtype == torch.float32, f"non-float32 trainable gradient: {name}")
            values = gradient.detach().float().cpu().reshape(-1).tolist()
            count += len(values)
            for item in values:
                numeric = float(item)
                if not math.isfinite(numeric):
                    return False, math.nan, count
                squared.append(numeric * numeric)
        _require(count > 0, "gradient gate observed no trainable elements")
        return True, math.sqrt(math.fsum(squared)), count

    def _manual_sgd(self, learning_rate: float) -> None:
        runtime_state = self._runtime()
        with runtime_state.dependencies.torch.no_grad():
            for _, parameter in self._trainable():
                parameter.add_(parameter.grad, alpha=-learning_rate)
        self._manual_parameter_steps += 1

    def _backward_row(
        self,
        row: lazy_adapter.SourceTrainingRowSpec | _CanaryRow,
        candidates: tuple[str, ...],
    ) -> float:
        runtime_state = self._runtime()
        if self._source_backward_hook is not None:
            expected_reward = self._source_backward_hook(row, candidates, runtime_state)
        else:
            torch = runtime_state.dependencies.torch
            scores = hf_backend.candidate_log_scores_reference(
                runtime_state.dependencies,
                runtime_state.model,
                runtime_state.tokenizer,
                row.prompt_text,
                candidates,
                require_grad=True,
            )
            _require(int(scores.numel()) == 7, "source scorer did not return seven scores")
            _require(
                bool(torch.isfinite(scores).all().detach().cpu().item()),
                "source scorer returned a non-finite candidate score",
            )
            probabilities = torch.softmax(scores, dim=0)
            reward = torch.tensor(
                row.reward_mask,
                device=probabilities.device,
                dtype=probabilities.dtype,
            )
            expected = (probabilities * reward).sum()
            loss = -expected / SOURCE_ROW_COUNT
            loss.backward()
            expected_reward = float(expected.detach().float().cpu().item())
        _require(math.isfinite(float(expected_reward)), "non-finite expected reward")
        self._model_forward_calls += 1
        self._backward_calls += 1
        return float(expected_reward)

    def _score(
        self,
        prompt_text: str,
        candidates: tuple[str, ...],
        *,
        require_grad: bool,
    ) -> tuple[float, ...]:
        runtime_state = self._runtime()
        if self._score_hook is not None:
            raw = self._score_hook(prompt_text, candidates, require_grad, runtime_state)
        else:
            tensor = hf_backend.candidate_log_scores_reference(
                runtime_state.dependencies,
                runtime_state.model,
                runtime_state.tokenizer,
                prompt_text,
                candidates,
                require_grad=require_grad,
            )
            raw = tuple(float(item) for item in tensor.detach().float().cpu().tolist())
        scores = tuple(float(item) for item in raw)
        _require(len(scores) == 7, "candidate scorer did not return seven scores")
        _require(all(math.isfinite(item) for item in scores), "candidate scorer returned non-finite values")
        self._model_forward_calls += 1
        return scores

    def _apply_accumulated_update(
        self,
        rows: tuple[lazy_adapter.SourceTrainingRowSpec | _CanaryRow, ...],
        candidates: tuple[str, ...],
        *,
        diagnostic_prefix: str,
    ) -> tuple[dict[str, Any], str, str]:
        _require(len(rows) == SOURCE_ROW_COUNT, "source update must contain fourteen rows")
        before = hf_backend.sha256_trainable_reference(self._runtime().model)
        self._zero_grad()
        forward_before = self._model_forward_calls
        backward_before = self._backward_calls
        step_before = self._manual_parameter_steps
        expected_rewards = [self._backward_row(row, candidates) for row in rows]
        _require(
            self._model_forward_calls - forward_before == SOURCE_ROW_COUNT
            and self._backward_calls - backward_before == SOURCE_ROW_COUNT,
            "source update action count drift",
        )
        finite, raw_norm, trainable_count = self._gradient_statistics()
        diagnostics = {
            f"{diagnostic_prefix}_row_count": SOURCE_ROW_COUNT,
            f"{diagnostic_prefix}_backward_calls": SOURCE_ROW_COUNT,
            "logical_update_steps": 1,
            "manual_parameter_steps": 0,
            "mean_expected_reward": math.fsum(expected_rewards) / SOURCE_ROW_COUNT,
            "raw_gradient_norm": raw_norm,
            "raw_gradient_norm_threshold": GRADIENT_NORM_THRESHOLD,
            "raw_gradient_norm_threshold_pass": finite
            and raw_norm <= GRADIENT_NORM_THRESHOLD,
            "all_gradients_finite": finite,
            "trainable_parameter_count": trainable_count,
            "learning_rate": LEARNING_RATE,
            "clipping_triggered": False,
            "clip_factor": 1.0,
            "mutation_started_before_norm_gate": False,
        }
        if not finite or raw_norm > GRADIENT_NORM_THRESHOLD:
            after_gate = hf_backend.sha256_trainable_reference(self._runtime().model)
            diagnostics["parameter_hash_preserved_before_technical_stop"] = after_gate == before
            self._zero_grad()
            _require(after_gate == before, "parameter mutated before failed norm gate")
            reason = "non-finite accumulated gradient" if not finite else "raw gradient norm exceeds 1.0"
            raise R13LocalTechnicalStop(reason, diagnostics)
        self._manual_sgd(LEARNING_RATE)
        _require(
            self._manual_parameter_steps - step_before == 1,
            "manual SGD step-count drift",
        )
        after = hf_backend.sha256_trainable_reference(self._runtime().model)
        _require(after != before, "manual SGD did not change the trainable hash")
        diagnostics["manual_parameter_steps"] = 1
        diagnostics["realized_update_norm"] = LEARNING_RATE * raw_norm
        diagnostics["parameter_changed"] = True
        self._zero_grad()
        return diagnostics, before, after

    def _canary_rows(self) -> tuple[_CanaryRow, ...]:
        template = self._canary_spec["source_canary"]["prompt_template"]
        rows: list[_CanaryRow] = []
        for index in range(SOURCE_ROW_COUNT):
            gold = (index + 1) % 7
            wrong = (gold + 1) % 7
            rows.append(
                _CanaryRow(
                    row_id=f"CANARY-{index}",
                    prompt_text=template.format(index=index),
                    reward_mask=tuple(
                        1 if candidate_index in (gold, wrong) else 0
                        for candidate_index in range(7)
                    ),
                )
            )
        return tuple(rows)

    def _supervised_token_counts(self, prompts: tuple[str, ...]) -> tuple[int, ...]:
        tokenizer = self._runtime().tokenizer
        return tuple(
            hf_backend.encode_candidate_reference(tokenizer, prompt, candidate).supervised_token_count
            for prompt in prompts
            for candidate in runner.ORDERED_CANDIDATE_SET
        )

    def _peak_memory(self) -> int:
        if self._peak_memory_reader is not None:
            return int(self._peak_memory_reader())
        torch = self._runtime().dependencies.torch
        return int(torch.cuda.max_memory_allocated("cuda:0"))

    def run_technical_canary(self, *, receipt_path: Path) -> Mapping[str, Any]:
        """Run only the frozen non-scientific canary and atomically write its receipt."""

        _require(self.initialized, "technical canary requested before local initialization")
        _require(self._canary_receipt_sha256 is None, "technical canary already completed")
        _require(self._scientific_manifest_sha256 is None, "technical canary requested after manifest activation")
        _require(
            self._scientific_source_cells == 0 and self._scientific_target_cells == 0,
            "technical canary requested after a scientific cell",
        )
        _require(not receipt_path.exists(), "refusing to overwrite a canary receipt")
        started = time.perf_counter()
        initial = self._restore_initial()
        rows = self._canary_rows()
        readonly_prompt = self._canary_spec["readonly_canary"]["prompt"]
        token_counts = self._supervised_token_counts(
            tuple(row.prompt_text for row in rows) + (readonly_prompt,)
        )
        forward_start = self._model_forward_calls
        backward_start = self._backward_calls
        step_start = self._manual_parameter_steps
        read_before_hash = hf_backend.sha256_trainable_reference(self._runtime().model)
        self._score(readonly_prompt, runner.ORDERED_CANDIDATE_SET, require_grad=False)
        read_after_hash = hf_backend.sha256_trainable_reference(self._runtime().model)
        _require(read_before_hash == read_after_hash == initial, "canary pre-read mutated trainables")
        diagnostics, update_before, update_after = self._apply_accumulated_update(
            rows,
            runner.ORDERED_CANDIDATE_SET,
            diagnostic_prefix="canary_source",
        )
        _require(update_before == initial, "canary update did not start from initial hash")
        post_read_before = hf_backend.sha256_trainable_reference(self._runtime().model)
        self._score(readonly_prompt, runner.ORDERED_CANDIDATE_SET, require_grad=False)
        post_read_after = hf_backend.sha256_trainable_reference(self._runtime().model)
        _require(
            post_read_before == post_read_after == update_after,
            "canary read-only scoring mutated trainables",
        )
        restored = self._restore_initial()
        action_counts = {
            "tokenizer_loads": 1,
            "model_weight_loads": 1,
            "model_forward_calls": self._model_forward_calls - forward_start,
            "backward_calls": self._backward_calls - backward_start,
            "manual_parameter_steps": self._manual_parameter_steps - step_start,
        }
        _require(
            action_counts
            == {
                "tokenizer_loads": 1,
                "model_weight_loads": 1,
                "model_forward_calls": 16,
                "backward_calls": 14,
                "manual_parameter_steps": 1,
            },
            "technical-canary model action count drift",
        )
        elapsed = max(time.perf_counter() - started, 1e-12)
        runtime_state = self._runtime()
        receipt: dict[str, Any] = {
            "schema_version": CANARY_RECEIPT_SCHEMA,
            "status": CANARY_PASS_STATUS,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "canary_spec_sha256": EXPECTED_CANARY_SPEC_SHA256,
            "backend_sha256": _sha256_file(Path(__file__).resolve()),
            "model_inventory_sha256": self._inventory_sha256,
            "runtime_reference_sha256": self.static_contract.runtime_environment_reference_sha256,
            "dependency_versions": dict(runtime_state.dependency_versions),
            "device_name": runtime_state.device_name,
            "device_capability": list(runtime_state.device_capability),
            "bf16_supported": runtime_state.bf16_supported,
            "trainable_parameter_count": diagnostics["trainable_parameter_count"],
            "candidate_supervised_token_count_min": min(token_counts),
            "candidate_supervised_token_count_max": max(token_counts),
            "all_candidate_scores_finite": True,
            "all_gradients_finite": diagnostics["all_gradients_finite"],
            "raw_gradient_norm": diagnostics["raw_gradient_norm"],
            "raw_gradient_norm_threshold_pass": diagnostics[
                "raw_gradient_norm_threshold_pass"
            ],
            "parameter_changed_after_update": update_after != update_before,
            "target_read_hash_preserved": read_before_hash == read_after_hash
            and post_read_before == post_read_after,
            "reset_hash_restored": restored == initial,
            "peak_gpu_memory_bytes": self._peak_memory(),
            "elapsed_seconds": elapsed,
            "scientific_source_cells": 0,
            "scientific_target_cells": 0,
            "model_action_counts": action_counts,
            "evidence_boundary": CANARY_EVIDENCE_BOUNDARY,
        }
        _require(set(receipt) == set(CANARY_RECEIPT_FIELDS), "canary receipt field coverage drift")
        _require(not (set(receipt) & CANARY_FORBIDDEN_FIELDS), "forbidden field entered canary receipt")
        _require(receipt["peak_gpu_memory_bytes"] > 0, "canary peak GPU memory is not positive")
        raw = json.dumps(
            receipt,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii") + b"\n"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(receipt_path, flags, 0o600)
        except FileExistsError as error:
            raise R13RealBackendError("refusing to overwrite a canary receipt") from error
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                receipt_path.unlink()
            except OSError:
                pass
            raise
        self._canary_receipt_sha256 = hashlib.sha256(raw).hexdigest()
        return receipt

    def attest_existing_canary_receipt(self, *, receipt_path: Path) -> str:
        """Consume a prior canary receipt in a fresh scientific worker.

        This is a strict byte/field/gate attestation only.  It deliberately
        performs no tokenization, model forward, backward, gradient, or update.
        """

        _require(self.initialized, "canary attestation requested before initialization")
        _require(self._canary_receipt_sha256 is None, "a canary receipt is already bound")
        _require(self._scientific_manifest_sha256 is None, "canary attestation requested after manifest activation")
        _require(
            self._scientific_source_cells == 0 and self._scientific_target_cells == 0,
            "canary attestation requested after a scientific cell",
        )
        actions_before = dict(self.model_action_counts)
        raw = receipt_path.read_bytes()
        receipt = _read_strict_json(receipt_path, "existing technical-canary receipt")
        canonical = json.dumps(
            receipt,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii") + b"\n"
        _require(raw == canonical, "existing canary receipt is not canonical JSON plus one LF")
        _require(set(receipt) == set(CANARY_RECEIPT_FIELDS), "existing canary receipt field coverage drift")
        _require(not (set(receipt) & CANARY_FORBIDDEN_FIELDS), "forbidden field in existing canary receipt")
        _require(receipt["schema_version"] == CANARY_RECEIPT_SCHEMA, "existing canary receipt schema drift")
        _require(receipt["status"] == CANARY_PASS_STATUS, "existing canary receipt did not pass")
        try:
            created = datetime.fromisoformat(receipt["created_at_utc"].replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise R13RealBackendError("invalid existing canary receipt time") from error
        _require(created.tzinfo is not None and created <= datetime.now(timezone.utc), "existing canary receipt time is not past UTC")
        expected_hashes = {
            "canary_spec_sha256": EXPECTED_CANARY_SPEC_SHA256,
            "backend_sha256": _sha256_file(Path(__file__).resolve()),
            "model_inventory_sha256": self._inventory_sha256,
            "runtime_reference_sha256": self.static_contract.runtime_environment_reference_sha256,
        }
        for field, expected in expected_hashes.items():
            _valid_hash(receipt[field], f"existing canary {field}")
            _require(receipt[field] == expected, f"existing canary hash mismatch: {field}")
        versions = receipt["dependency_versions"]
        _require(
            isinstance(versions, dict)
            and bool(versions)
            and all(
                isinstance(name, str)
                and bool(name)
                and isinstance(version, str)
                and bool(version)
                for name, version in versions.items()
            ),
            "existing canary dependency versions missing",
        )
        _require(
            versions == dict(self._runtime().dependency_versions),
            "existing canary dependency versions differ from this worker",
        )
        _require(isinstance(receipt["device_name"], str) and bool(receipt["device_name"].strip()), "existing canary device name missing")
        capability = receipt["device_capability"]
        _require(
            isinstance(capability, list)
            and len(capability) == 2
            and all(type(item) is int and item >= 0 for item in capability),
            "existing canary device capability invalid",
        )
        _require(
            receipt["device_name"] == self._runtime().device_name
            and capability == list(self._runtime().device_capability)
            and receipt["bf16_supported"] == self._runtime().bf16_supported,
            "existing canary device differs from this worker",
        )
        numeric_gates = (
            receipt["bf16_supported"] is True,
            type(receipt["trainable_parameter_count"]) is int
            and receipt["trainable_parameter_count"] > 0,
            type(receipt["candidate_supervised_token_count_min"]) is int
            and receipt["candidate_supervised_token_count_min"] > 0,
            type(receipt["candidate_supervised_token_count_max"]) is int
            and receipt["candidate_supervised_token_count_max"]
            >= receipt["candidate_supervised_token_count_min"],
            receipt["all_candidate_scores_finite"] is True,
            receipt["all_gradients_finite"] is True,
            type(receipt["raw_gradient_norm"]) in (int, float)
            and math.isfinite(float(receipt["raw_gradient_norm"]))
            and 0.0 <= float(receipt["raw_gradient_norm"]) <= GRADIENT_NORM_THRESHOLD,
            receipt["raw_gradient_norm_threshold_pass"] is True,
            receipt["parameter_changed_after_update"] is True,
            receipt["target_read_hash_preserved"] is True,
            receipt["reset_hash_restored"] is True,
            type(receipt["peak_gpu_memory_bytes"]) is int
            and receipt["peak_gpu_memory_bytes"] > 0,
            type(receipt["elapsed_seconds"]) in (int, float)
            and math.isfinite(float(receipt["elapsed_seconds"]))
            and float(receipt["elapsed_seconds"]) > 0,
            receipt["scientific_source_cells"] == 0,
            receipt["scientific_target_cells"] == 0,
            receipt["evidence_boundary"] == CANARY_EVIDENCE_BOUNDARY,
        )
        _require(all(numeric_gates), "existing canary pass gate drift")
        _require(
            receipt["model_action_counts"]
            == {
                "tokenizer_loads": 1,
                "model_weight_loads": 1,
                "model_forward_calls": 16,
                "backward_calls": 14,
                "manual_parameter_steps": 1,
            },
            "existing canary action-count gate drift",
        )
        _require(dict(self.model_action_counts) == actions_before, "canary attestation performed a model action")
        digest = hashlib.sha256(raw).hexdigest()
        self._canary_receipt_sha256 = digest
        return digest

    def activate_scientific_manifest(
        self,
        *,
        manifest_path: Path,
        expected_manifest_sha256: str,
        output_root_was_preflighted_absent_by_coordinator: bool = False,
    ) -> None:
        """Enable scientific calls only after the canary-bound manifest validates."""

        _require(self.initialized, "manifest activation requested before initialization")
        _require(
            self._test_injections_present is False,
            "scientific manifest activation rejects injected test runtime hooks",
        )
        _require(self._canary_receipt_sha256 is not None, "manifest activation requested before canary")
        _require(self._scientific_manifest_sha256 is None, "scientific manifest already active")
        _require(
            type(output_root_was_preflighted_absent_by_coordinator) is bool,
            "coordinator output-root preflight marker must be boolean",
        )
        _require(
            type(self.target_payload_provider)
            is hf_backend.DiskBoundTargetPayloadProvider,
            "scientific manifest activation requires the validated disk target provider",
        )
        _valid_hash(expected_manifest_sha256, "local scientific manifest hash")
        _require(_sha256_file(manifest_path) == expected_manifest_sha256, "local scientific manifest hash drift")
        manifest_contract = importlib.import_module("r13_local_development_manifest")
        manifest = manifest_contract.read_manifest(manifest_path)
        project_root = Path(__file__).resolve().parents[1]
        manifest_contract.verify_manifest_against_filesystem(
            manifest,
            project_root=project_root,
            require_output_root_absent=not output_root_was_preflighted_absent_by_coordinator,
        )
        artifacts = manifest["artifacts"]
        _require(
            artifacts["real_backend"]["sha256"] == _sha256_file(Path(__file__).resolve()),
            "manifest/backend hash mismatch",
        )
        _require(
            artifacts["canary_receipt"]["sha256"] == self._canary_receipt_sha256,
            "manifest/canary receipt hash mismatch",
        )
        _require(
            artifacts["local_execution_semantics_addendum"]["sha256"]
            == EXPECTED_SEMANTICS_SHA256
            and artifacts["local_technical_canary_spec"]["sha256"]
            == EXPECTED_CANARY_SPEC_SHA256,
            "manifest/local semantics binding mismatch",
        )
        self._scientific_manifest_sha256 = expected_manifest_sha256

    def _require_scientific_manifest(self) -> None:
        _require(
            self._scientific_manifest_sha256 is not None,
            "scientific R13 call blocked until a canary-bound local manifest is active",
        )

    def reset_trainable(self) -> None:
        self._require_scientific_manifest()
        if self._read_phase is not None:
            _require(
                self._read_arms == set(runner.ARMS),
                "cannot reset before both target arms read the same parameter hash",
            )
        if self._update_hash is not None:
            _require(
                self._read_arms == set(runner.ARMS),
                "cannot reset an update before both target arms are read",
            )
        self._restore_initial()
        self._reset_count += 1
        self._update_identity = None
        self._update_hash = None
        self._read_phase = None
        self._read_arms = set()

    def parameter_hash(self) -> str:
        self._require_scientific_manifest()
        _require(self._reset_count > 0, "parameter hash requested before reset")
        return hf_backend.sha256_trainable_reference(self._runtime().model)

    def _build_source_spec(self, request: runner.SourceUpdateCall) -> lazy_adapter.SourceExecutionSpec:
        _require(type(request) is runner.SourceUpdateCall, "source request type drift")
        _require(request.stack_id in self.static_contract.ordered_stack_ids, "source stack is not bound")
        _require(request.source_assets_sha256 == self.static_contract.source_assets_sha256, "source assets hash drift")
        _require(request.protocol_sha256 == self.static_contract.protocol_sha256, "source protocol hash drift")
        match = _SOURCE_IDENTITY_RE.fullmatch(request.source_rule_identity)
        _require(match is not None, "source identity is outside Z7_PLUS1..5")
        _require(request.source_rule_identity in self.static_contract.source_identities, "source identity is not bound")
        expected_seed = _source_seed(
            protocol_sha256=request.protocol_sha256,
            source_assets_sha256=request.source_assets_sha256,
            stack_id=request.stack_id,
            source_rule_identity=request.source_rule_identity,
        )
        _require(request.source_seed_material_sha256 == expected_seed, "source seed commitment drift")
        offset = int(match.group(1))
        stack = self.static_contract.source_stack(request.stack_id)
        rows: list[lazy_adapter.SourceTrainingRowSpec] = []
        for row in stack.ordered_rows:
            wrong = row.source_codebook[(row.canonical_z + offset) % 7]
            _require(wrong != row.gold_candidate, f"{row.row_id} wrong candidate equals gold")
            reward_mask = tuple(
                1 if candidate in (row.gold_candidate, wrong) else 0
                for candidate in self.static_contract.ordered_candidate_set
            )
            _require(sum(reward_mask) == 2, f"{row.row_id} reward mass drift")
            rows.append(
                lazy_adapter.SourceTrainingRowSpec(
                    row_id=row.row_id,
                    prompt_text=row.prompt_text,
                    prompt_sha256=row.prompt_sha256,
                    row_bytes_sha256=row.row_bytes_sha256,
                    canonical_z=row.canonical_z,
                    gold_candidate=row.gold_candidate,
                    source_wrong_candidate=wrong,
                    ordered_candidate_set=self.static_contract.ordered_candidate_set,
                    reward_mask=reward_mask,
                )
            )
        update = lazy_adapter.UpdateHyperparameters(
            learning_rate=LEARNING_RATE,
            gradient_clip_norm=GRADIENT_NORM_THRESHOLD,
            lora_rank=4,
            lora_alpha=8,
            lora_dropout=0.0,
            lora_targets=("q_proj", "v_proj"),
            optimizer="manual_sgd_no_state",
            steps=1,
        )
        spec = lazy_adapter.SourceExecutionSpec(
            stack_id=request.stack_id,
            source_rule_identity=request.source_rule_identity,
            source_offset_mod7=offset,
            source_assets_sha256=request.source_assets_sha256,
            source_bundle_sha256=stack.source_bundle_sha256,
            protocol_sha256=request.protocol_sha256,
            source_seed_material_sha256=request.source_seed_material_sha256,
            model_inventory_sha256=self.static_contract.model_inventory_sha256,
            update_recipe_sha256=self.static_contract.update_recipe_sha256,
            update=update,
            ordered_candidate_set=self.static_contract.ordered_candidate_set,
            ordered_source_rows=tuple(rows),
        )
        _assert_source_spec_target_blind(spec)
        return spec

    def update_source(self, request: runner.SourceUpdateCall) -> runner.SourceUpdateResult:
        self._require_scientific_manifest()
        _require(self._reset_count > 0, "source update requested before reset")
        _require(self._update_hash is None, "more than one source update requested after reset")
        _require(not self._read_arms, "source update requested after target read")
        spec = self._build_source_spec(request)
        diagnostics, before, after = self._apply_accumulated_update(
            spec.ordered_source_rows,
            spec.ordered_candidate_set,
            diagnostic_prefix="source",
        )
        diagnostics.update(
            {
                "source_bundle_sha256": spec.source_bundle_sha256,
                "source_seed_material_sha256": spec.source_seed_material_sha256,
                "source_before_parameter_sha256": before,
                "source_after_parameter_sha256": after,
                "source_offset_mod7": spec.source_offset_mod7,
            }
        )
        for key in diagnostics:
            _require(
                not _is_forbidden_source_name(key),
                f"forbidden diagnostic key entered source result: {key}",
            )
        self._update_identity = request.source_rule_identity
        self._update_hash = after
        self._scientific_source_cells += 1
        return runner.SourceUpdateResult(
            optimizer_step_count=1,
            clipping_triggered=False,
            non_finite_observed=False,
            diagnostics=diagnostics,
        )

    def read_target(self, request: runner.TargetReadCall) -> runner.TargetReadResult:
        self._require_scientific_manifest()
        _require(type(request) is runner.TargetReadCall, "target request type drift")
        _require(self._reset_count > 0, "target read requested before reset")
        _require(request.stack_id in self.static_contract.ordered_stack_ids, "target stack is not bound")
        _require(request.replicate_id in ("A", "B"), "target replicate drift")
        _require(request.phase in ("PRE", "POST"), "target phase drift")
        _require(request.arm in runner.ARMS, "target arm drift")
        _require(request.require_grad is False, "target read requested gradients")
        _require(request.optimizer_steps_permitted == 0, "target read permitted optimizer steps")
        _require(request.state_mutation_permitted is False, "target read permitted state mutation")
        _require(request.ordered_candidate_set == runner.ORDERED_CANDIDATE_SET, "target candidate order drift")
        _require(len(request.ordered_row_ids) == 7 and len(set(request.ordered_row_ids)) == 7, "target row order drift")
        _valid_hash(request.target_panel_sha256, "target panel hash")
        _valid_hash(request.source_update_hash, "source update hash")
        if request.phase == "PRE":
            _require(self._update_hash is None, "PRE target read requested after update")
            _require(request.source_rule_identity is None, "PRE target read carried source identity")
            _require(request.source_update_hash == self._initial_hash, "PRE target read hash drift")
        else:
            _require(self._update_hash is not None, "POST target read requested before update")
            _require(request.source_rule_identity == self._update_identity, "POST source identity drift")
            _require(request.source_update_hash == self._update_hash, "POST update hash drift")
        if self._read_phase is None:
            self._read_phase = request.phase
        _require(self._read_phase == request.phase, "mixed PRE/POST target reads after one reset")
        _require(request.arm not in self._read_arms, "target arm read more than once for one state")
        spec = lazy_adapter.TargetReadSpec(
            stack_id=request.stack_id,
            replicate_id=request.replicate_id,
            phase=request.phase,
            arm=request.arm,
            source_rule_identity=request.source_rule_identity,
            source_update_hash=request.source_update_hash,
            target_panel_sha256=request.target_panel_sha256,
            ordered_row_ids=request.ordered_row_ids,
            ordered_candidate_set=request.ordered_candidate_set,
            require_grad=False,
            optimizer_steps_permitted=0,
            state_mutation_permitted=False,
        )
        result = hf_backend.execute_target_readonly_reference(
            spec,
            payload_provider=self.target_payload_provider,
            score_candidates=lambda prompt, candidates, require_grad: self._score(
                prompt,
                candidates,
                require_grad=require_grad,
            ),
            parameter_hash=lambda: hf_backend.sha256_trainable_reference(
                self._runtime().model
            ),
        )
        _require(result.gradient_count == 0, "target read reported a gradient")
        _require(result.optimizer_step_count == 0, "target read reported an optimizer step")
        _require(result.state_mutation_count == 0, "target read reported state mutation")
        _require(result.non_finite_observed is False, "target read reported non-finite score")
        self._read_arms.add(request.arm)
        self._scientific_target_cells += len(result.rows)
        return result


__all__ = [
    "CANARY_RECEIPT_FIELDS",
    "EXPECTED_CANARY_SPEC_SHA256",
    "EXPECTED_SEMANTICS_SHA256",
    "LocalInitializationPermit",
    "LocalRuntime",
    "R13LocalTechnicalStop",
    "R13RealBackend",
    "R13RealBackendError",
    "SCHEMA_VERSION",
]
