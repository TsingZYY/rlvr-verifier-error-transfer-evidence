"""Fail-closed, dependency-injected backend boundary for the R13 runner.

This is deliberately *not* a model implementation.  Importing this module and
building its static contract read only ordinary JSON/JSONL/source asset bytes.
There is no tokenizer/model loader and there are no forward, backward, gradient,
or optimizer calls.

The public ``static_only`` adapter rejects every ``R13Backend`` operation while
the frozen R13 protocol remains unauthorized.  ``for_synthetic_no_model_test``
exists only to exercise the exact runner/backend interface with an injected fake
runtime whose prohibited-model-action counters must remain identically zero.
That test seam is not an execution authorization or a production backend.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Protocol

import r13_model_inventory as model_inventory_contract
import r13_runner_core as runner
import r13_runtime_environment as runtime_environment_contract
import r13_update_recipe_contract as update_recipe_contract


SCHEMA_VERSION = "r13-lazy-backend-adapter-static-contract-r1"
STATIC_ONLY_BLOCKER = (
    "R13_MODEL_EXECUTION_BLOCKED: protocol authorization, production adapter "
    "binding, full dependency-byte binding, target payload resolution, and a "
    "real execution validation receipt are absent"
)
SYNTHETIC_MODE = "SYNTHETIC_NO_MODEL_TEST_ONLY"
STATIC_MODE = "STATIC_ONLY_FAIL_CLOSED"

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.:|-]{1,200}")
_SOURCE_IDENTITY_RE = re.compile(r"Z7_PLUS([1-5])")
_FORBIDDEN_SOURCE_TOKENS = ("arm", "target", "panel", "replicate")


class LazyBackendError(RuntimeError):
    """A fail-closed static-binding or adapter-state violation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LazyBackendError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_hash(value: Any, label: str) -> str:
    _require(
        isinstance(value, str) and _HASH_RE.fullmatch(value) is not None,
        f"{label} must be a lowercase SHA-256",
    )
    return value


def _is_forbidden_source_name(value: str) -> bool:
    # ``lora_targets`` is the frozen q_proj/v_proj module selector, not target
    # task data.  It is the sole allowlisted lexical collision.
    lowered = value.lower()
    return lowered != "lora_targets" and any(
        token in lowered for token in _FORBIDDEN_SOURCE_TOKENS
    )


def _reject_pairs(label: str):
    def reject(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    return reject


def _parse_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs(label),
            parse_constant=lambda token: (_ for _ in ()).throw(
                LazyBackendError(f"non-finite JSON constant in {label}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LazyBackendError(f"invalid UTF-8 JSON in {label}") from error
    _require(isinstance(value, dict), f"{label} must be one JSON object")
    return value


def _read_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    return raw, _parse_json_bytes(raw, str(path))


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _bound_member(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str) and relative, "source byte path is missing")
    rel = Path(relative)
    _require(
        not rel.is_absolute() and ".." not in rel.parts,
        "source byte path escapes its asset root",
    )
    current = root
    for part in rel.parts:
        current = current / part
        _require(not _is_link(current), f"source byte path contains a link: {relative}")
    selected = current.resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    _require(
        os.path.commonpath((str(root_resolved), str(selected))) == str(root_resolved),
        "source byte path resolves outside its asset root",
    )
    _require(selected.is_file(), f"source byte member is not a file: {relative}")
    return selected


@dataclass(frozen=True)
class BoundSourceRow:
    """The complete source-row projection allowed to reach an update runtime."""

    row_id: str
    prompt_text: str
    prompt_sha256: str
    row_bytes_sha256: str
    canonical_z: int
    gold_candidate: str
    source_codebook: tuple[str, ...]


@dataclass(frozen=True)
class SourceStackBinding:
    stack_id: str
    source_bundle_sha256: str
    source_codebook: tuple[str, ...]
    ordered_rows: tuple[BoundSourceRow, ...]


@dataclass(frozen=True)
class StaticAdapterContract:
    schema_version: str
    status: str
    protocol_sha256: str
    update_recipe_sha256: str
    source_assets_sha256: str
    model_inventory_sha256: str
    runtime_environment_reference_sha256: str
    model_repository: str
    model_revision: str
    ordered_stack_ids: tuple[str, ...]
    source_identities: tuple[str, ...]
    ordered_candidate_set: tuple[str, ...]
    source_stacks: tuple[SourceStackBinding, ...]
    model_execution_authorized: bool
    production_backend_bound: bool
    full_dependency_content_hash_bound: bool

    def source_stack(self, stack_id: str) -> SourceStackBinding:
        matches = [stack for stack in self.source_stacks if stack.stack_id == stack_id]
        _require(len(matches) == 1, f"no uniquely bound source stack: {stack_id}")
        return matches[0]


@dataclass(frozen=True)
class UpdateHyperparameters:
    learning_rate: float
    gradient_clip_norm: float
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    lora_targets: tuple[str, ...]
    optimizer: str
    steps: int


@dataclass(frozen=True)
class SourceTrainingRowSpec:
    row_id: str
    prompt_text: str
    prompt_sha256: str
    row_bytes_sha256: str
    canonical_z: int
    gold_candidate: str
    source_wrong_candidate: str
    ordered_candidate_set: tuple[str, ...]
    reward_mask: tuple[int, ...]


@dataclass(frozen=True)
class SourceExecutionSpec:
    """Exact source-only input given to an eventual low-level implementation."""

    stack_id: str
    source_rule_identity: str
    source_offset_mod7: int
    source_assets_sha256: str
    source_bundle_sha256: str
    protocol_sha256: str
    source_seed_material_sha256: str
    model_inventory_sha256: str
    update_recipe_sha256: str
    update: UpdateHyperparameters
    ordered_candidate_set: tuple[str, ...]
    ordered_source_rows: tuple[SourceTrainingRowSpec, ...]


@dataclass(frozen=True)
class TargetReadSpec:
    """Read-only target interface; target payload resolution is not implemented."""

    stack_id: str
    replicate_id: str
    phase: str
    arm: str
    source_rule_identity: str | None
    source_update_hash: str
    target_panel_sha256: str
    ordered_row_ids: tuple[str, ...]
    ordered_candidate_set: tuple[str, ...]
    require_grad: bool
    optimizer_steps_permitted: int
    state_mutation_permitted: bool


@dataclass(frozen=True)
class ProhibitedModelActionCounts:
    tokenizer_loads: int = 0
    model_weight_loads: int = 0
    model_forwards: int = 0
    gradient_calls: int = 0
    optimizer_steps: int = 0


class SyntheticNoModelRuntime(Protocol):
    """Test seam only.  A real ML runtime cannot be authorized through it."""

    def prohibited_model_action_counts(self) -> ProhibitedModelActionCounts: ...

    def reset_trainable(self) -> None: ...

    def parameter_hash(self) -> str: ...

    def update_source(self, spec: SourceExecutionSpec) -> runner.SourceUpdateResult: ...

    def read_target(self, spec: TargetReadSpec) -> runner.TargetReadResult: ...


def _validate_protocol(value: dict[str, Any]) -> None:
    _require(
        value.get("schema_version")
        == "r13-fixed-update-target-alignment-pilot-draft-r1",
        "protocol schema drift",
    )
    _require(
        value.get("status") == "DRAFT_STATIC_DESIGN_NOT_AUTHORIZED_NO_MODEL_RUN",
        "protocol status drift",
    )
    _require(value.get("run_eligible") is False, "protocol unexpectedly became run eligible")
    _require(
        value.get("model_execution_authorized") is False,
        "protocol unexpectedly authorizes model execution",
    )
    model = value.get("model_contract")
    _require(isinstance(model, dict), "protocol model contract missing")
    _require(model.get("repository") == model_inventory_contract.REPOSITORY, "model repository drift")
    _require(model.get("revision") == model_inventory_contract.REVISION, "model revision drift")
    _require(model.get("precision") == "base_bfloat16_lora_float32", "model precision drift")
    fixed = value.get("fixed_source_stacks")
    _require(isinstance(fixed, dict), "protocol fixed source stacks missing")
    _require(
        fixed.get("ordered_stack_ids") == list(update_recipe_contract.STACKS),
        "protocol source stack order drift",
    )
    source_update = value.get("fixed_source_update_contract")
    _require(isinstance(source_update, dict), "protocol source-update contract missing")
    _require(
        source_update.get("source_identities") == list(update_recipe_contract.IDENTITIES),
        "protocol source identities drift",
    )
    _require(source_update.get("candidate_panel_size") == 7, "protocol candidate count drift")
    authorization = value.get("authorization")
    _require(isinstance(authorization, dict), "protocol authorization missing")
    _require(authorization.get("cpu_static_design_and_tests") is True, "static tests not authorized")
    for field in (
        "tokenizer_load",
        "model_weight_load",
        "model_forward",
        "gradient",
        "optimizer_step",
        "run_r13_target_alignment_pilot",
    ):
        _require(authorization.get(field) is False, f"protocol authorization drift: {field}")


def _validate_codebook(value: Any, candidates: tuple[str, ...], label: str) -> tuple[str, ...]:
    _require(isinstance(value, dict), f"{label} source codebook missing")
    latent = value.get("latent_to_candidate")
    _require(
        isinstance(latent, list)
        and len(latent) == 7
        and len(set(latent)) == 7
        and set(latent) == set(candidates),
        f"{label} source codebook drift",
    )
    return tuple(latent)


def _project_source_row(
    row: Any,
    *,
    stack_id: str,
    candidates: tuple[str, ...],
    codebook: tuple[str, ...],
    asset_root: Path,
) -> BoundSourceRow:
    _require(isinstance(row, dict), f"{stack_id} source row is not an object")
    _require(row.get("mapping_stack_id") == stack_id, f"{stack_id} source row stack drift")
    _require(row.get("split_role") == "SOURCE", f"{stack_id} non-source row entered source bundle")
    _require(
        row.get("panel_role") == "SOURCE_UPDATE_INPUT",
        f"{stack_id} source row role drift",
    )
    _require(row.get("candidate_order") == list(candidates), f"{stack_id} candidate order drift")
    row_id = row.get("row_id")
    _require(
        isinstance(row_id, str) and _SAFE_ID_RE.fullmatch(row_id) is not None,
        f"{stack_id} invalid source row id",
    )
    canonical_z = row.get("canonical_z")
    _require(type(canonical_z) is int and 0 <= canonical_z < 7, f"{row_id} invalid canonical z")
    gold = row.get("gold_candidate")
    _require(gold == codebook[canonical_z], f"{row_id} gold/codebook mismatch")
    _require(row.get("codebook", {}).get("latent_to_candidate") == list(codebook), f"{row_id} row codebook drift")
    prompt_text = row.get("prompt_text")
    _require(isinstance(prompt_text, str), f"{row_id} prompt text missing")
    try:
        prompt_raw = prompt_text.encode("ascii")
    except UnicodeEncodeError as error:
        raise LazyBackendError(f"{row_id} prompt is not ASCII") from error
    prompt_record = row.get("prompt_bytes")
    row_record = row.get("row_bytes")
    _require(isinstance(prompt_record, dict), f"{row_id} prompt byte binding missing")
    _require(isinstance(row_record, dict), f"{row_id} row byte binding missing")
    prompt_hash = _validate_hash(prompt_record.get("sha256"), f"{row_id} prompt hash")
    row_hash = _validate_hash(row_record.get("sha256"), f"{row_id} row hash")
    _require(_sha256_bytes(prompt_raw) == prompt_hash, f"{row_id} embedded prompt hash drift")
    _require(prompt_record.get("byte_length") == len(prompt_raw), f"{row_id} prompt length drift")
    prompt_path = _bound_member(asset_root, prompt_record.get("raw_relpath"))
    row_path = _bound_member(asset_root, row_record.get("raw_relpath"))
    _require(prompt_path.read_bytes() == prompt_raw, f"{row_id} raw prompt bytes drift")
    _require(_sha256_file(row_path) == row_hash, f"{row_id} raw row bytes drift")
    return BoundSourceRow(
        row_id=row_id,
        prompt_text=prompt_text,
        prompt_sha256=prompt_hash,
        row_bytes_sha256=row_hash,
        canonical_z=canonical_z,
        gold_candidate=gold,
        source_codebook=codebook,
    )


def _load_source_stacks(
    path: Path,
    *,
    expected_sha256: str,
    candidates: tuple[str, ...],
) -> tuple[SourceStackBinding, ...]:
    raw = path.read_bytes()
    _require(_sha256_bytes(raw) == expected_sha256, "source bundle file hash drift")
    lines = raw.splitlines()
    _require(lines and all(line for line in lines), "source bundle JSONL contains a blank line")
    by_stack: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(lines, 1):
        value = _parse_json_bytes(line, f"{path}:{index}")
        stack_id = value.get("mapping_stack_id")
        _require(isinstance(stack_id, str), f"{path}:{index} stack id missing")
        _require(stack_id not in by_stack, f"duplicate source stack: {stack_id}")
        by_stack[stack_id] = value
    _require(
        set(update_recipe_contract.STACKS).issubset(by_stack),
        "source bundle is missing a frozen M0 stack",
    )
    asset_root = path.parent.resolve(strict=True)
    result: list[SourceStackBinding] = []
    for stack_id in update_recipe_contract.STACKS:
        value = by_stack[stack_id]
        _require(
            value.get("schema_version") == "p4-r1-real-source-bundle-v1",
            f"{stack_id} source bundle schema drift",
        )
        _require(value.get("source_row_count") == 14, f"{stack_id} source row count drift")
        _require(value.get("candidate_order") == list(candidates), f"{stack_id} candidate order drift")
        codebook = _validate_codebook(value.get("source_codebook"), candidates, stack_id)
        rows_value = value.get("rows")
        _require(isinstance(rows_value, list) and len(rows_value) == 14, f"{stack_id} rows drift")
        rows = tuple(
            _project_source_row(
                row,
                stack_id=stack_id,
                candidates=candidates,
                codebook=codebook,
                asset_root=asset_root,
            )
            for row in rows_value
        )
        _require(len({row.row_id for row in rows}) == 14, f"{stack_id} duplicate row ids")
        class_counts = {z: sum(row.canonical_z == z for row in rows) for z in range(7)}
        _require(class_counts == {z: 2 for z in range(7)}, f"{stack_id} class balance drift")
        result.append(
            SourceStackBinding(
                stack_id=stack_id,
                source_bundle_sha256=_validate_hash(
                    value.get("source_bundle_sha256"),
                    f"{stack_id} source bundle hash",
                ),
                source_codebook=codebook,
                ordered_rows=rows,
            )
        )
    return tuple(result)


def bind_static_contract(
    *,
    protocol_path: Path,
    update_recipe_path: Path,
    source_bundles_path: Path,
    model_inventory_path: Path,
    runtime_environment_path: Path,
) -> StaticAdapterContract:
    """Bind static assets without accepting or touching a model directory."""

    protocol_raw, protocol = _read_json(protocol_path)
    _validate_protocol(protocol)
    recipe_raw, recipe = _read_json(update_recipe_path)
    try:
        update_recipe_contract.validate_contract(recipe)
    except update_recipe_contract.UpdateRecipeError as error:
        raise LazyBackendError(f"invalid update recipe contract: {error}") from error
    protocol_hash = _sha256_bytes(protocol_raw)
    _require(recipe["protocol_sha256"] == protocol_hash, "recipe/protocol hash mismatch")

    try:
        inventory = model_inventory_contract.read_inventory(model_inventory_path)
    except model_inventory_contract.InventoryError as error:
        raise LazyBackendError(f"invalid model inventory: {error}") from error
    inventory_hash = _sha256_file(model_inventory_path)
    _require(recipe["model_inventory_sha256"] == inventory_hash, "recipe/model inventory hash mismatch")
    _require(
        inventory["repository"] == protocol["model_contract"]["repository"],
        "protocol/inventory repository mismatch",
    )
    _require(
        inventory["revision"] == protocol["model_contract"]["revision"],
        "protocol/inventory revision mismatch",
    )

    try:
        environment = runtime_environment_contract.read_environment(runtime_environment_path)
    except runtime_environment_contract.RuntimeEnvironmentError as error:
        raise LazyBackendError(f"invalid runtime environment reference: {error}") from error
    environment_hash = _sha256_file(runtime_environment_path)
    _require(
        recipe["runtime_environment_reference_sha256"] == environment_hash,
        "recipe/runtime environment hash mismatch",
    )
    _require(environment["model_execution_authorized"] is False, "runtime reference authorizes execution")

    candidates = tuple(recipe["candidate_set"])
    source_stacks = _load_source_stacks(
        source_bundles_path,
        expected_sha256=recipe["source_bundles_sha256"],
        candidates=candidates,
    )
    return StaticAdapterContract(
        schema_version=SCHEMA_VERSION,
        status="STATIC_ASSETS_BOUND_REAL_EXECUTION_FAIL_CLOSED",
        protocol_sha256=protocol_hash,
        update_recipe_sha256=_sha256_bytes(recipe_raw),
        source_assets_sha256=recipe["source_bundles_sha256"],
        model_inventory_sha256=inventory_hash,
        runtime_environment_reference_sha256=environment_hash,
        model_repository=inventory["repository"],
        model_revision=inventory["revision"],
        ordered_stack_ids=tuple(recipe["ordered_stack_ids"]),
        source_identities=tuple(f"Z7_PLUS{offset}" for offset in recipe["source_identities"]),
        ordered_candidate_set=candidates,
        source_stacks=source_stacks,
        model_execution_authorized=False,
        production_backend_bound=False,
        full_dependency_content_hash_bound=False,
    )


def _source_seed(
    *,
    protocol_sha256: str,
    source_assets_sha256: str,
    stack_id: str,
    source_rule_identity: str,
) -> str:
    payload = {
        "protocol_sha256": protocol_sha256,
        "source_assets_sha256": source_assets_sha256,
        "stack_id": stack_id,
        "source_rule_identity": source_rule_identity,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(raw)


def _update_hyperparameters() -> UpdateHyperparameters:
    value = update_recipe_contract.EXPECTED_UPDATE
    return UpdateHyperparameters(
        learning_rate=value["learning_rate"],
        gradient_clip_norm=value["gradient_clip_norm"],
        lora_rank=value["lora_rank"],
        lora_alpha=value["lora_alpha"],
        lora_dropout=value["lora_dropout"],
        lora_targets=tuple(value["lora_targets"]),
        optimizer=value["optimizer"],
        steps=value["steps"],
    )


def _assert_source_spec_is_target_blind(spec: SourceExecutionSpec) -> None:
    def walk(value: Any, location: str) -> None:
        if hasattr(value, "__dataclass_fields__"):
            for field in fields(value):
                lowered = field.name.lower()
                _require(
                    not _is_forbidden_source_name(lowered),
                    f"forbidden field entered source spec at {location}.{field.name}",
                )
                walk(getattr(value, field.name), f"{location}.{field.name}")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                _require(isinstance(key, str), f"non-string source-spec key at {location}")
                lowered = key.lower()
                _require(
                    not _is_forbidden_source_name(lowered),
                    f"forbidden key entered source spec at {location}.{key}",
                )
                walk(item, f"{location}.{key}")
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")

    walk(spec, "source_spec")


class LazyR13BackendAdapter:
    """R13Backend-shaped adapter with no real-execution enabling path in R1."""

    def __init__(
        self,
        contract: StaticAdapterContract,
        *,
        mode: str,
        runtime: SyntheticNoModelRuntime | None,
    ) -> None:
        _require(contract.model_execution_authorized is False, "R1 contract must remain unauthorized")
        _require(contract.production_backend_bound is False, "R1 production backend flag drift")
        _require(
            contract.full_dependency_content_hash_bound is False,
            "R1 dependency binding flag drift",
        )
        _require(mode in (STATIC_MODE, SYNTHETIC_MODE), "unknown adapter mode")
        _require((mode == SYNTHETIC_MODE) == (runtime is not None), "runtime/mode mismatch")
        self.contract = contract
        self.mode = mode
        self._injected_runtime = runtime
        self._initial_parameter_hash: str | None = None
        self._reset_count = 0
        self._update_identity: str | None = None
        self._update_hash: str | None = None
        self._read_phase: str | None = None
        self._read_arms: set[str] = set()
        if runtime is not None:
            self._assert_zero_model_actions()

    @classmethod
    def static_only(cls, contract: StaticAdapterContract) -> "LazyR13BackendAdapter":
        return cls(contract, mode=STATIC_MODE, runtime=None)

    @classmethod
    def for_synthetic_no_model_test(
        cls,
        contract: StaticAdapterContract,
        runtime: SyntheticNoModelRuntime,
    ) -> "LazyR13BackendAdapter":
        """Create the fake-only seam; this method cannot authorize real execution."""

        return cls(contract, mode=SYNTHETIC_MODE, runtime=runtime)

    def _runtime(self) -> SyntheticNoModelRuntime:
        _require(self.mode == SYNTHETIC_MODE, STATIC_ONLY_BLOCKER)
        _require(self._injected_runtime is not None, STATIC_ONLY_BLOCKER)
        self._assert_zero_model_actions()
        return self._injected_runtime

    def _assert_zero_model_actions(self) -> None:
        _require(self._injected_runtime is not None, STATIC_ONLY_BLOCKER)
        counts = self._injected_runtime.prohibited_model_action_counts()
        _require(
            type(counts) is ProhibitedModelActionCounts,
            "synthetic runtime returned the wrong action-count type",
        )
        _require(counts == ProhibitedModelActionCounts(), "synthetic runtime performed a prohibited model action")

    def _runtime_hash(self) -> str:
        runtime = self._runtime()
        value = runtime.parameter_hash()
        self._assert_zero_model_actions()
        return _validate_hash(value, "runtime parameter hash")

    def reset_trainable(self) -> None:
        runtime = self._runtime()
        if self._read_arms:
            _require(
                self._read_arms == set(runner.ARMS),
                "cannot reset before both target interfaces read the same state",
            )
        if self._update_hash is not None:
            _require(
                self._read_arms == set(runner.ARMS),
                "cannot reset an update before both target interfaces are read",
            )
        runtime.reset_trainable()
        self._assert_zero_model_actions()
        observed = self._runtime_hash()
        if self._initial_parameter_hash is None:
            self._initial_parameter_hash = observed
        else:
            _require(observed == self._initial_parameter_hash, "trainable reset hash drift")
        self._reset_count += 1
        self._update_identity = None
        self._update_hash = None
        self._read_phase = None
        self._read_arms = set()

    def parameter_hash(self) -> str:
        self._runtime()
        _require(self._reset_count > 0, "parameter hash requested before reset")
        return self._runtime_hash()

    def _build_source_spec(self, request: runner.SourceUpdateCall) -> SourceExecutionSpec:
        _require(type(request) is runner.SourceUpdateCall, "source update request type drift")
        _require(request.stack_id in self.contract.ordered_stack_ids, "source stack is not bound")
        _require(request.source_assets_sha256 == self.contract.source_assets_sha256, "source asset hash drift")
        _require(request.protocol_sha256 == self.contract.protocol_sha256, "source protocol hash drift")
        match = _SOURCE_IDENTITY_RE.fullmatch(request.source_rule_identity)
        _require(match is not None, "source identity is outside Z7_PLUS1..5")
        _require(request.source_rule_identity in self.contract.source_identities, "source identity is not bound")
        expected_seed = _source_seed(
            protocol_sha256=request.protocol_sha256,
            source_assets_sha256=request.source_assets_sha256,
            stack_id=request.stack_id,
            source_rule_identity=request.source_rule_identity,
        )
        _require(request.source_seed_material_sha256 == expected_seed, "source seed commitment drift")
        offset = int(match.group(1))
        stack = self.contract.source_stack(request.stack_id)
        rows: list[SourceTrainingRowSpec] = []
        for row in stack.ordered_rows:
            wrong = row.source_codebook[(row.canonical_z + offset) % 7]
            _require(wrong != row.gold_candidate, f"{row.row_id} source wrong candidate equals gold")
            reward_mask = tuple(
                1 if candidate in (row.gold_candidate, wrong) else 0
                for candidate in self.contract.ordered_candidate_set
            )
            _require(sum(reward_mask) == 2, f"{row.row_id} reward mask mass drift")
            rows.append(
                SourceTrainingRowSpec(
                    row_id=row.row_id,
                    prompt_text=row.prompt_text,
                    prompt_sha256=row.prompt_sha256,
                    row_bytes_sha256=row.row_bytes_sha256,
                    canonical_z=row.canonical_z,
                    gold_candidate=row.gold_candidate,
                    source_wrong_candidate=wrong,
                    ordered_candidate_set=self.contract.ordered_candidate_set,
                    reward_mask=reward_mask,
                )
            )
        spec = SourceExecutionSpec(
            stack_id=request.stack_id,
            source_rule_identity=request.source_rule_identity,
            source_offset_mod7=offset,
            source_assets_sha256=request.source_assets_sha256,
            source_bundle_sha256=stack.source_bundle_sha256,
            protocol_sha256=request.protocol_sha256,
            source_seed_material_sha256=request.source_seed_material_sha256,
            model_inventory_sha256=self.contract.model_inventory_sha256,
            update_recipe_sha256=self.contract.update_recipe_sha256,
            update=_update_hyperparameters(),
            ordered_candidate_set=self.contract.ordered_candidate_set,
            ordered_source_rows=tuple(rows),
        )
        _assert_source_spec_is_target_blind(spec)
        return spec

    def update_source(self, request: runner.SourceUpdateCall) -> runner.SourceUpdateResult:
        runtime = self._runtime()
        _require(self._reset_count > 0, "source update requested before reset")
        _require(self._update_hash is None, "more than one source update requested after one reset")
        _require(not self._read_arms, "source update requested after a target read without reset")
        spec = self._build_source_spec(request)
        before = self._runtime_hash()
        result = runtime.update_source(spec)
        self._assert_zero_model_actions()
        _require(type(result) is runner.SourceUpdateResult, "runtime update result type drift")
        _require(type(result.optimizer_step_count) is int and result.optimizer_step_count == 1, "synthetic update must report one interface step")
        _require(result.clipping_triggered is False, "synthetic update reported clipping")
        _require(result.non_finite_observed is False, "synthetic update reported non-finite arithmetic")
        _require(isinstance(result.diagnostics, Mapping), "synthetic update diagnostics type drift")
        for key in result.diagnostics:
            _require(isinstance(key, str), "synthetic diagnostic key is not a string")
            lowered = key.lower()
            _require(
                not _is_forbidden_source_name(lowered),
                f"forbidden source diagnostic key: {key}",
            )
        after = self._runtime_hash()
        _require(after != before, "synthetic source update did not change its fake parameter hash")
        self._update_identity = request.source_rule_identity
        self._update_hash = after
        return result

    def read_target(self, request: runner.TargetReadCall) -> runner.TargetReadResult:
        runtime = self._runtime()
        _require(type(request) is runner.TargetReadCall, "target read request type drift")
        _require(self._reset_count > 0, "target read requested before reset")
        _require(request.stack_id in self.contract.ordered_stack_ids, "target read stack is not bound")
        _require(request.phase in ("PRE", "POST"), "target read phase drift")
        _require(request.arm in runner.ARMS, "target arm drift")
        _require(request.require_grad is False, "target read requested gradients")
        _require(request.optimizer_steps_permitted == 0, "target read permitted optimizer steps")
        _require(request.state_mutation_permitted is False, "target read permitted state mutation")
        _require(request.ordered_candidate_set == self.contract.ordered_candidate_set, "target candidate order drift")
        _require(len(request.ordered_row_ids) == 7, "target read must bind seven rows")
        _require(len(set(request.ordered_row_ids)) == 7, "target row ids are duplicated")
        _validate_hash(request.target_panel_sha256, "target panel hash")
        _validate_hash(request.source_update_hash, "target source-update hash")
        if request.phase == "PRE":
            _require(self._update_hash is None, "PRE read requested after an update")
            _require(request.source_rule_identity is None, "PRE read carried a source identity")
            _require(
                request.source_update_hash == self._initial_parameter_hash,
                "PRE read did not bind the reset parameter hash",
            )
        else:
            _require(self._update_hash is not None, "POST read requested before an update")
            _require(request.source_rule_identity == self._update_identity, "POST source identity drift")
            _require(request.source_update_hash == self._update_hash, "POST update hash drift")
        if self._read_phase is None:
            self._read_phase = request.phase
        _require(self._read_phase == request.phase, "mixed PRE/POST reads within one reset")
        _require(request.arm not in self._read_arms, "target interface read more than once for one state")
        before = self._runtime_hash()
        _require(before == request.source_update_hash, "runtime state differs before target read")
        spec = TargetReadSpec(
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
        result = runtime.read_target(spec)
        self._assert_zero_model_actions()
        _require(type(result) is runner.TargetReadResult, "runtime target result type drift")
        _require(type(result.gradient_count) is int and result.gradient_count == 0, "target runtime reported a gradient")
        _require(type(result.optimizer_step_count) is int and result.optimizer_step_count == 0, "target runtime reported an optimizer step")
        _require(type(result.state_mutation_count) is int and result.state_mutation_count == 0, "target runtime reported state mutation")
        _require(result.non_finite_observed is False, "target runtime reported non-finite arithmetic")
        _require(len(result.rows) == 7, "target runtime row count drift")
        for expected_id, row in zip(request.ordered_row_ids, result.rows):
            _require(type(row) is runner.RawCandidateRow, "target runtime row type drift")
            _require(row.row_id == expected_id, "target runtime row order drift")
            _require(len(row.ordered_candidate_scores) == 7, "target runtime candidate count drift")
            _require(
                all(type(score) in (int, float) and math.isfinite(float(score)) for score in row.ordered_candidate_scores),
                "target runtime returned a non-finite or non-numeric score",
            )
        after = self._runtime_hash()
        _require(after == before == request.source_update_hash, "target runtime mutated fake parameters")
        self._read_arms.add(request.arm)
        return result


__all__ = [
    "BoundSourceRow",
    "LazyBackendError",
    "LazyR13BackendAdapter",
    "ProhibitedModelActionCounts",
    "SCHEMA_VERSION",
    "STATIC_ONLY_BLOCKER",
    "SourceExecutionSpec",
    "SourceStackBinding",
    "SourceTrainingRowSpec",
    "StaticAdapterContract",
    "SyntheticNoModelRuntime",
    "TargetReadSpec",
    "UpdateHyperparameters",
    "bind_static_contract",
]
