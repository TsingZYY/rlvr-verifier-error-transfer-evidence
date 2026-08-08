"""Import-safe Hugging Face implementation reference for R13.

This module records the parts of the SmolLM2/PEFT backend that are uniquely
determined by the frozen R10 implementation and R13 contracts.  It deliberately
does not make R13 executable: the current protocol denies every model action,
a local-development execution addendum is not bound, and two source-update
details conflict across the frozen prose and reference implementation.

No ML package is imported at module import time.  The only dependency-import
function checks the execution gates *before* calling ``importlib``.  With the
current R1 plan it therefore always fails before tokenizer/model load, forward,
gradient, or optimizer work.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Protocol

import r13_lazy_backend_adapter as lazy_adapter
import r13_runner_core as runner


SCHEMA_VERSION = "r13-hf-backend-reference-plan-r1"
PARENT_R10_RUNNER_SHA256 = "439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d"
SYSTEM_PROMPT = (
    "Follow the task specification exactly. Return exactly one listed candidate "
    "and no other text."
)
CURRENT_EXECUTION_BLOCKER = (
    "R13_HF_EXECUTION_BLOCKED: current protocol denies tokenizer/model/forward/"
    "gradient/optimizer actions; a local development addendum, trusted target "
    "payload resolution, local runtime invocation, and source-update operational "
    "semantics are not fully frozen"
)

UNRESOLVED_BLOCKERS = (
    "GRADIENT_CALL_SHAPE_CONFLICT: R13 prose says one backward pass over 14 row "
    "contributions; the exact R10 reference runner calls backward once per row",
    "CLIPPING_STOP_POINT_CONFLICT: R13 requires a technical stop on realized "
    "clipping; the R10 reference clips and applies the parameter update",
)

LOCAL_ADDENDUM_BINDINGS_REQUIRED = (
    "bind the exact r13_hf_backend.py hash and choose both source-update semantics",
    "bind an internal constructor over load_bound_matched_panel_bundle and its exact hash",
    "bind the local model inventory, runtime reference, CUDA determinism settings, and invocation",
)


class HFBackendError(RuntimeError):
    """A fail-closed HF reference-plan or execution-boundary violation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HFBackendError(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class HFReferencePlan:
    schema_version: str
    status: str
    model_repository: str
    model_revision: str
    model_local_path_reference_only: str
    precision: str
    device_reference_only: str
    system_prompt: str
    ordered_candidate_set: tuple[str, ...]
    parent_r10_runner_sha256_reference_only: str
    protocol_sha256: str
    model_inventory_sha256: str
    update_recipe_sha256: str
    seed: int
    model_execution_authorized: bool
    local_development_addendum_bound: bool
    local_target_loader_bound: bool
    local_runtime_invocation_bound: bool
    production_backend_bound: bool
    full_dependency_content_hash_bound: bool
    unresolved_blockers: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEncoding:
    input_ids: tuple[int, ...]
    supervised_mask: tuple[bool, ...]
    supervised_token_count: int


@dataclass(frozen=True)
class BoundTargetRow:
    row_id: str
    prompt_text: str
    prompt_sha256: str


@dataclass(frozen=True)
class BoundTargetPayload:
    stack_id: str
    arm: str
    target_panel_sha256: str
    manifest_sha256: str
    allowlist_receipt_sha256: str
    ordered_rows: tuple[BoundTargetRow, ...]


@dataclass(frozen=True)
class SourceUpdateExecutionPlan:
    stack_id: str
    source_rule_identity: str
    ordered_row_ids: tuple[str, ...]
    ordered_candidate_set: tuple[str, ...]
    ordered_reward_masks: tuple[tuple[int, ...], ...]
    loss: str
    learning_rate: float
    gradient_clip_norm: float
    optimizer: str
    executable: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class HFDependencies:
    torch: Any
    functional: Any
    auto_model_for_causal_lm: Any
    auto_tokenizer: Any
    lora_config: Any
    task_type: Any
    get_peft_model: Any


class TargetPayloadProvider(Protocol):
    def resolve(self, request: lazy_adapter.TargetReadSpec) -> BoundTargetPayload: ...


class CandidateScoreProvider(Protocol):
    def __call__(
        self,
        prompt_text: str,
        ordered_candidates: tuple[str, ...],
        *,
        require_grad: bool,
    ) -> tuple[float, ...]: ...


class DiskBoundTargetPayloadProvider:
    """Payload index produced only by the validated manifest/raw-byte loader."""

    _CONSTRUCTION_TOKEN = object()

    def __init__(
        self,
        payloads: tuple[BoundTargetPayload, ...],
        *,
        construction_token: object,
    ) -> None:
        _require(
            construction_token is self._CONSTRUCTION_TOKEN,
            "disk-bound target provider must be created by its validating loader",
        )
        self._payloads = payloads

    def panel_payload(self, stack_id: str, arm: str) -> BoundTargetPayload:
        matches = [
            payload
            for payload in self._payloads
            if payload.stack_id == stack_id and payload.arm == arm
        ]
        _require(len(matches) == 1, "no uniquely bound target payload")
        return matches[0]

    def resolve(self, request: lazy_adapter.TargetReadSpec) -> BoundTargetPayload:
        payload = self.panel_payload(request.stack_id, request.arm)
        _require(
            payload.target_panel_sha256 == request.target_panel_sha256,
            "target request does not match the disk-bound panel hash",
        )
        _require(
            tuple(row.row_id for row in payload.ordered_rows) == request.ordered_row_ids,
            "target request does not match disk-bound row order",
        )
        return payload


def load_disk_bound_target_payload_provider(
    *,
    validator_path: Path,
    expected_validator_sha256: str,
    protocol_path: Path,
    expected_protocol_sha256: str,
    bundle_root: Path,
    expected_manifest_sha256: str,
    expected_allowlist_receipt_sha256: str,
) -> DiskBoundTargetPayloadProvider:
    """Load real target prompts through the existing independent hash-chain validator."""

    for value, label in (
        (expected_validator_sha256, "validator hash"),
        (expected_protocol_sha256, "protocol hash"),
        (expected_manifest_sha256, "matched-panel manifest hash"),
        (expected_allowlist_receipt_sha256, "allowlist receipt hash"),
    ):
        _require(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value),
            f"{label} must be lowercase SHA-256",
        )
    _require(_sha256_file(validator_path) == expected_validator_sha256, "validator byte hash drift")
    _require(_sha256_file(protocol_path) == expected_protocol_sha256, "protocol byte hash drift")
    module_name = f"_r13_bound_result_validator_{expected_validator_sha256[:16]}"
    module_spec = importlib.util.spec_from_file_location(module_name, validator_path)
    _require(
        module_spec is not None and module_spec.loader is not None,
        "cannot construct validator module spec",
    )
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    protocol = module.strict_json_loads(protocol_path.read_bytes(), "bound R13 protocol")
    module.validate_protocol(protocol, synthetic_test_mode=False)
    loaded = module.load_bound_matched_panel_bundle(
        bundle_root,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_allowlist_receipt_sha256=expected_allowlist_receipt_sha256,
    )
    panels, panel_hashes = module.validate_matched_panels(loaded.records, protocol)
    payloads: list[BoundTargetPayload] = []
    for stack_id in runner.STACK_IDS:
        for arm in runner.ARMS:
            records = panels[(stack_id, arm)]
            payloads.append(
                BoundTargetPayload(
                    stack_id=stack_id,
                    arm=arm,
                    target_panel_sha256=panel_hashes[(stack_id, arm)],
                    manifest_sha256=loaded.manifest_sha256,
                    allowlist_receipt_sha256=loaded.allowlist_receipt_sha256,
                    ordered_rows=tuple(
                        BoundTargetRow(
                            row_id=record.row_id,
                            prompt_text=record.prompt_bytes.decode("utf-8"),
                            prompt_sha256=record.prompt_sha256,
                        )
                        for record in records
                    ),
                )
            )
    _require(len(payloads) == 8, "disk-bound target payload coverage drift")
    return DiskBoundTargetPayloadProvider(
        tuple(payloads),
        construction_token=DiskBoundTargetPayloadProvider._CONSTRUCTION_TOKEN,
    )


def build_reference_plan(
    static_contract: lazy_adapter.StaticAdapterContract,
    *,
    parent_r10_runner_path: Path,
) -> HFReferencePlan:
    """Bind the reference implementation without reading a model directory."""

    _require(
        _sha256_file(parent_r10_runner_path) == PARENT_R10_RUNNER_SHA256,
        "parent R10 runner hash drift",
    )
    _require(static_contract.model_execution_authorized is False, "R1 authorization flag drift")
    _require(static_contract.production_backend_bound is False, "R1 backend flag drift")
    _require(
        static_contract.full_dependency_content_hash_bound is False,
        "R1 dependency binding flag drift",
    )
    return HFReferencePlan(
        schema_version=SCHEMA_VERSION,
        status="REFERENCE_IMPLEMENTATION_STATIC_ONLY_MODEL_NOT_LOADED",
        model_repository=static_contract.model_repository,
        model_revision=static_contract.model_revision,
        model_local_path_reference_only="models/SmolLM2-360M-Instruct-a10cc151",
        precision="base_bfloat16_lora_float32",
        device_reference_only="cuda",
        system_prompt=SYSTEM_PROMPT,
        ordered_candidate_set=static_contract.ordered_candidate_set,
        parent_r10_runner_sha256_reference_only=PARENT_R10_RUNNER_SHA256,
        protocol_sha256=static_contract.protocol_sha256,
        model_inventory_sha256=static_contract.model_inventory_sha256,
        update_recipe_sha256=static_contract.update_recipe_sha256,
        seed=20260804,
        model_execution_authorized=False,
        local_development_addendum_bound=False,
        local_target_loader_bound=False,
        local_runtime_invocation_bound=False,
        production_backend_bound=False,
        full_dependency_content_hash_bound=False,
        unresolved_blockers=UNRESOLVED_BLOCKERS,
    )


def encode_candidate_reference(
    tokenizer: Any,
    prompt_text: str,
    candidate: str,
) -> CandidateEncoding:
    """Reproduce the hash-bound R10 assistant-token and terminator boundary."""

    _require(isinstance(prompt_text, str), "prompt must be text")
    _require(isinstance(candidate, str) and candidate, "candidate must be nonempty text")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt_text},
    ]
    prefix_text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    full_text = tokenizer.apply_chat_template(
        messages + [{"role": "assistant", "content": candidate}],
        tokenize=False,
        add_generation_prompt=False,
    )
    prefix_ids = [
        int(value)
        for value in tokenizer(prefix_text, add_special_tokens=False)["input_ids"]
    ]
    full_ids = [
        int(value)
        for value in tokenizer(full_text, add_special_tokens=False)["input_ids"]
    ]
    _require(full_ids[: len(prefix_ids)] == prefix_ids, "chat-template prefix is not token-prefix stable")
    _require(len(full_ids) > len(prefix_ids), "candidate produced no supervised tokens")
    mask = tuple(False for _ in prefix_ids) + tuple(
        True for _ in range(len(full_ids) - len(prefix_ids))
    )
    return CandidateEncoding(
        input_ids=tuple(full_ids),
        supervised_mask=mask,
        supervised_token_count=sum(mask),
    )


def sha256_trainable_reference(model: Any) -> str:
    """Exact R10 name/shape/float32-byte hash for trainable parameters."""

    digest = hashlib.sha256()
    observed = 0
    for name, parameter in sorted(model.named_parameters(), key=lambda pair: pair[0]):
        if not parameter.requires_grad:
            continue
        observed += 1
        digest.update(name.encode("utf-8"))
        value = parameter.detach().float().cpu().contiguous()
        digest.update(str(tuple(value.shape)).encode("ascii"))
        digest.update(value.numpy().tobytes(order="C"))
    _require(observed > 0, "model exposes no trainable parameters")
    return digest.hexdigest()


def snapshot_trainable_reference(model: Any) -> dict[str, Any]:
    snapshot = {
        name: parameter.detach().float().cpu().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }
    _require(bool(snapshot), "model exposes no trainable parameters")
    return snapshot


def restore_trainable_reference(
    model: Any,
    snapshot: Mapping[str, Any],
    *,
    no_grad_context: Callable[[], Any],
) -> None:
    observed = {
        name for name, parameter in model.named_parameters() if parameter.requires_grad
    }
    _require(observed == set(snapshot), "trainable parameter names changed after initialization")
    with no_grad_context():
        for name, parameter in model.named_parameters():
            if parameter.requires_grad:
                parameter.copy_(snapshot[name].to(parameter.device, parameter.dtype))


def build_source_update_execution_plan(
    spec: lazy_adapter.SourceExecutionSpec,
) -> SourceUpdateExecutionPlan:
    """Freeze all determined source math while refusing the ambiguous execution."""

    _require(type(spec) is lazy_adapter.SourceExecutionSpec, "source execution spec type drift")
    _require(len(spec.ordered_source_rows) == 14, "source update must contain fourteen rows")
    _require(spec.ordered_candidate_set == runner.ORDERED_CANDIDATE_SET, "candidate order drift")
    _require(spec.update.learning_rate == 0.1, "learning-rate drift")
    _require(spec.update.gradient_clip_norm == 1.0, "gradient clip drift")
    _require(spec.update.optimizer == "manual_sgd_no_state", "optimizer drift")
    masks: list[tuple[int, ...]] = []
    row_ids: list[str] = []
    for row in spec.ordered_source_rows:
        _require(len(row.reward_mask) == 7 and sum(row.reward_mask) == 2, "reward mask drift")
        _require(set(row.reward_mask).issubset({0, 1}), "reward mask is not binary")
        row_ids.append(row.row_id)
        masks.append(row.reward_mask)
    _require(len(set(row_ids)) == 14, "source row ids are duplicated")
    return SourceUpdateExecutionPlan(
        stack_id=spec.stack_id,
        source_rule_identity=spec.source_rule_identity,
        ordered_row_ids=tuple(row_ids),
        ordered_candidate_set=spec.ordered_candidate_set,
        ordered_reward_masks=tuple(masks),
        loss="negative mean over 14 rows of sum_c softmax(score)_c * reward_c",
        learning_rate=spec.update.learning_rate,
        gradient_clip_norm=spec.update.gradient_clip_norm,
        optimizer=spec.update.optimizer,
        executable=False,
        blockers=UNRESOLVED_BLOCKERS[:2],
    )


def execute_target_readonly_reference(
    request: lazy_adapter.TargetReadSpec,
    *,
    payload_provider: TargetPayloadProvider,
    score_candidates: CandidateScoreProvider,
    parameter_hash: Callable[[], str],
) -> runner.TargetReadResult:
    """Execute the fully determined target no-grad/read-only orchestration.

    This helper is provenance-neutral.  A production release must supply a
    provider constructed internally from ``load_bound_matched_panel_bundle``;
    a caller-created provider is not production evidence.
    """

    _require(type(request) is lazy_adapter.TargetReadSpec, "target read spec type drift")
    _require(request.require_grad is False, "target read requested gradients")
    _require(request.optimizer_steps_permitted == 0, "target read permitted optimizer work")
    _require(request.state_mutation_permitted is False, "target read permitted mutation")
    _require(request.ordered_candidate_set == runner.ORDERED_CANDIDATE_SET, "candidate order drift")
    before = parameter_hash()
    _require(before == request.source_update_hash, "parameter hash drift before target read")
    payload = payload_provider.resolve(request)
    _require(type(payload) is BoundTargetPayload, "target provider returned an unbound payload type")
    _require(payload.stack_id == request.stack_id, "target payload stack drift")
    _require(payload.arm == request.arm, "target payload arm drift")
    _require(payload.target_panel_sha256 == request.target_panel_sha256, "target panel hash drift")
    _require(len(payload.ordered_rows) == 7, "target payload must contain seven rows")
    _require(
        tuple(row.row_id for row in payload.ordered_rows) == request.ordered_row_ids,
        "target payload row order drift",
    )
    rows: list[runner.RawCandidateRow] = []
    for row in payload.ordered_rows:
        _require(
            hashlib.sha256(row.prompt_text.encode("utf-8")).hexdigest()
            == row.prompt_sha256,
            f"target prompt hash drift: {row.row_id}",
        )
        scores = score_candidates(
            row.prompt_text,
            request.ordered_candidate_set,
            require_grad=False,
        )
        _require(len(scores) == 7, f"target score count drift: {row.row_id}")
        numeric = tuple(float(score) for score in scores)
        _require(all(math.isfinite(score) for score in numeric), f"non-finite target score: {row.row_id}")
        rows.append(
            runner.RawCandidateRow(
                row_id=row.row_id,
                ordered_candidate_scores=numeric,
            )
        )
    after = parameter_hash()
    _require(after == before == request.source_update_hash, "target read mutated trainable parameters")
    return runner.TargetReadResult(
        rows=tuple(rows),
        gradient_count=0,
        optimizer_step_count=0,
        state_mutation_count=0,
        non_finite_observed=False,
    )


def candidate_log_scores_reference(
    dependencies: HFDependencies,
    model: Any,
    tokenizer: Any,
    prompt_text: str,
    ordered_candidates: tuple[str, ...],
    *,
    require_grad: bool,
) -> Any:
    """R10 reference scoring code, parameterized by lazily supplied modules."""

    torch = dependencies.torch
    functional = dependencies.functional
    encoded = [
        encode_candidate_reference(tokenizer, prompt_text, candidate)
        for candidate in ordered_candidates
    ]
    max_length = max(len(item.input_ids) for item in encoded)
    pad_id = tokenizer.pad_token_id
    _require(pad_id is not None, "tokenizer has no pad token")
    input_ids = torch.full(
        (len(encoded), max_length),
        int(pad_id),
        dtype=torch.long,
        device=model.device,
    )
    attention_mask = torch.zeros_like(input_ids)
    supervised_mask = torch.zeros_like(input_ids, dtype=torch.bool)
    for index, item in enumerate(encoded):
        size = len(item.input_ids)
        input_ids[index, :size] = torch.tensor(item.input_ids, device=model.device)
        attention_mask[index, :size] = 1
        supervised_mask[index, :size] = torch.tensor(
            item.supervised_mask,
            device=model.device,
        )
    context = nullcontext() if require_grad else torch.no_grad()
    with context, torch.autocast(device_type="cuda", dtype=torch.bfloat16):
        output = model(input_ids=input_ids, attention_mask=attention_mask, use_cache=False)
        shifted_logits = output.logits[:, :-1, :].float()
        shifted_targets = input_ids[:, 1:].unsqueeze(-1)
        selected = functional.log_softmax(shifted_logits, dim=-1).gather(
            -1,
            shifted_targets,
        ).squeeze(-1)
        return (selected * supervised_mask[:, 1:]).sum(dim=1)


def _require_future_release_authorization(plan: HFReferencePlan) -> None:
    _require(plan.model_execution_authorized is True, CURRENT_EXECUTION_BLOCKER)
    _require(plan.local_development_addendum_bound is True, CURRENT_EXECUTION_BLOCKER)
    _require(plan.local_target_loader_bound is True, CURRENT_EXECUTION_BLOCKER)
    _require(plan.local_runtime_invocation_bound is True, CURRENT_EXECUTION_BLOCKER)
    _require(not plan.unresolved_blockers, CURRENT_EXECUTION_BLOCKER)


def _lazy_import_hf_dependencies(plan: HFReferencePlan) -> HFDependencies:
    """Import ML modules only after a future release satisfies every gate."""

    _require_future_release_authorization(plan)
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    transformers = importlib.import_module("transformers")
    peft = importlib.import_module("peft")
    return HFDependencies(
        torch=torch,
        functional=functional,
        auto_model_for_causal_lm=transformers.AutoModelForCausalLM,
        auto_tokenizer=transformers.AutoTokenizer,
        lora_config=peft.LoraConfig,
        task_type=peft.TaskType,
        get_peft_model=peft.get_peft_model,
    )


def _initialize_hf_reference_after_future_authorization(
    plan: HFReferencePlan,
    *,
    model_dir: Path,
) -> tuple[HFDependencies, Any, Any, dict[str, Any], str]:
    """Future release hook; unreachable for the current R1 plan."""

    dependencies = _lazy_import_hf_dependencies(plan)
    torch = dependencies.torch
    torch.manual_seed(plan.seed)
    torch.cuda.manual_seed_all(plan.seed)
    torch.use_deterministic_algorithms(True, warn_only=False)
    tokenizer = dependencies.auto_tokenizer.from_pretrained(
        model_dir,
        use_fast=True,
        local_files_only=True,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    model = dependencies.auto_model_for_causal_lm.from_pretrained(
        model_dir,
        torch_dtype=torch.bfloat16,
        local_files_only=True,
        low_cpu_mem_usage=True,
    ).to("cuda")
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
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    _require(bool(trainable), "LoRA initialization has no trainable parameters")
    _require(
        all(parameter.dtype == torch.float32 for parameter in trainable),
        "LoRA trainables are not float32 under the frozen precision contract",
    )
    snapshot = snapshot_trainable_reference(model)
    return dependencies, tokenizer, model, snapshot, sha256_trainable_reference(model)


class R13HFBackend:
    """R13Backend-shaped current façade; every operation is intentionally blocked."""

    def __init__(self, plan: HFReferencePlan) -> None:
        self.plan = plan

    def initialize(self, *, model_dir: Path) -> None:
        _require_future_release_authorization(self.plan)
        # A future release must own and bind the returned objects.  The current
        # plan can never reach this call.
        _initialize_hf_reference_after_future_authorization(self.plan, model_dir=model_dir)

    def reset_trainable(self) -> None:
        raise HFBackendError(CURRENT_EXECUTION_BLOCKER)

    def parameter_hash(self) -> str:
        raise HFBackendError(CURRENT_EXECUTION_BLOCKER)

    def update_source(self, request: runner.SourceUpdateCall) -> runner.SourceUpdateResult:
        raise HFBackendError(CURRENT_EXECUTION_BLOCKER)

    def read_target(self, request: runner.TargetReadCall) -> runner.TargetReadResult:
        raise HFBackendError(CURRENT_EXECUTION_BLOCKER)


__all__ = [
    "BoundTargetPayload",
    "BoundTargetRow",
    "CURRENT_EXECUTION_BLOCKER",
    "CandidateEncoding",
    "DiskBoundTargetPayloadProvider",
    "HFBackendError",
    "HFReferencePlan",
    "LOCAL_ADDENDUM_BINDINGS_REQUIRED",
    "PARENT_R10_RUNNER_SHA256",
    "R13HFBackend",
    "SCHEMA_VERSION",
    "SYSTEM_PROMPT",
    "SourceUpdateExecutionPlan",
    "UNRESOLVED_BLOCKERS",
    "build_reference_plan",
    "build_source_update_execution_plan",
    "candidate_log_scores_reference",
    "encode_candidate_reference",
    "execute_target_readonly_reference",
    "load_disk_bound_target_payload_provider",
    "restore_trainable_reference",
    "sha256_trainable_reference",
    "snapshot_trainable_reference",
]
