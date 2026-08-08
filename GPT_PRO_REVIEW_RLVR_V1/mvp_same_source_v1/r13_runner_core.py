from __future__ import annotations

"""Pure, dependency-injected orchestration core for the R13 pilot.

This module intentionally imports only the Python standard library.  It does not
know how to locate, load, score, differentiate, or update a model.  A future
runtime adapter must implement :class:`R13Backend`; the tests use only a fake
backend.  The narrow update call is deliberately unable to carry target-arm or
target-panel inputs.
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Protocol, Sequence


SCHEMA_VERSION = "r13-target-blind-runner-core-result-r1"
AB_RECEIPT_SCHEMA_VERSION = "r13-ab-reproducibility-receipt-r1"
COLLECTION_RECEIPT_SCHEMA_VERSION = "r13-process-collection-receipt-r1"

STACK_IDS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
ARM_ORDER_BY_REPLICATE = {"A": ARMS, "B": tuple(reversed(ARMS))}
SOURCE_IDENTITIES = tuple(f"Z7_PLUS{offset}" for offset in range(1, 6))
ORDERED_CANDIDATE_SET = tuple(f"FINAL=K{index}" for index in range(7))

BINDING_KEYS = frozenset(
    {
        "protocol_sha256",
        "panel_bundle_sha256",
        "allowlist_receipt_sha256",
        "human_review_receipt_sha256",
        "authorization_sha256",
        "runner_sha256",
        "validator_sha256",
        "model_inventory_sha256",
        "source_assets_sha256",
    }
)

SOURCE_UPDATE_CALL_FIELDS = (
    "stack_id",
    "source_rule_identity",
    "source_assets_sha256",
    "protocol_sha256",
    "source_seed_material_sha256",
)

EXPECTED_PROCESS_COUNTS = {
    "technical_processes": 1,
    "fresh_reset_calls": 6,
    "source_update_calls": 5,
    "technical_update_executions": 5,
    "unique_design_source_updates": 5,
    "target_read_calls": 12,
    "pre_target_vectors": 2,
    "post_target_vectors": 10,
    "pre_target_identity_reads": 12,
    "post_target_identity_reads": 60,
    "total_target_identity_metric_cells": 72,
    "raw_target_row_traces": 84,
    "raw_candidate_scores": 588,
    "parameter_hash_observations": 35,
    "event_count": 25,
}

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.:|-]{1,200}")
_FORBIDDEN_UPDATE_FIELD_TOKENS = ("arm", "target", "panel", "replicate")


class R13RunnerError(RuntimeError):
    """Fail-closed contract violation in the static orchestration core."""


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TargetRowBinding:
    canonical_instance_id: str
    row_id: str
    panel_row_sha256: str


@dataclass(frozen=True)
class TargetPanelBinding:
    arm: str
    target_panel_sha256: str
    ordered_rows: tuple[TargetRowBinding, ...]


@dataclass(frozen=True)
class R13RunContract:
    run_id: str
    bundle_run_id: str
    replicate_id: str
    stack_id: str
    bindings: Mapping[str, str]
    initial_parameter_hash: str
    ordered_candidate_set: tuple[str, ...]
    target_panels: tuple[TargetPanelBinding, ...]


@dataclass(frozen=True)
class SourceUpdateCall:
    """The complete and deliberately target-blind source-update input."""

    stack_id: str
    source_rule_identity: str
    source_assets_sha256: str
    protocol_sha256: str
    source_seed_material_sha256: str


@dataclass(frozen=True)
class SourceUpdateResult:
    optimizer_step_count: int
    clipping_triggered: bool
    non_finite_observed: bool
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class TargetReadCall:
    stack_id: str
    replicate_id: str
    phase: str
    arm: str
    source_rule_identity: str | None
    source_update_hash: str
    target_panel_sha256: str
    ordered_row_ids: tuple[str, ...]
    ordered_candidate_set: tuple[str, ...]
    require_grad: bool = False
    optimizer_steps_permitted: int = 0
    state_mutation_permitted: bool = False


@dataclass(frozen=True)
class RawCandidateRow:
    row_id: str
    ordered_candidate_scores: tuple[float, ...]


@dataclass(frozen=True)
class TargetReadResult:
    rows: tuple[RawCandidateRow, ...]
    gradient_count: int
    optimizer_step_count: int
    state_mutation_count: int
    non_finite_observed: bool


class R13Backend(Protocol):
    """Minimal runtime surface.  No concrete model dependency is imported here."""

    def reset_trainable(self) -> None: ...

    def parameter_hash(self) -> str: ...

    def update_source(self, request: SourceUpdateCall) -> SourceUpdateResult: ...

    def read_target(self, request: TargetReadCall) -> TargetReadResult: ...


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise R13RunnerError(message)


def validate_sha256(value: Any, field: str) -> str:
    _require(
        isinstance(value, str) and _HASH_RE.fullmatch(value) is not None,
        f"{field} must be a lowercase SHA-256",
    )
    return value


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_json_value(value: Any, path: str = "root") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        _require(math.isfinite(value), f"non-finite JSON number at {path}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _require(isinstance(key, str), f"non-string JSON key at {path}")
            _validate_json_value(child, f"{path}.{key}")
        return
    raise R13RunnerError(f"non-JSON value at {path}: {type(value).__name__}")


def strict_json_dumps(value: Any) -> str:
    """Serialize RFC-8259 JSON; NaN/Infinity and non-JSON types are rejected."""

    return canonical_json_bytes(value).decode("utf-8") + "\n"


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise R13RunnerError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise R13RunnerError(f"non-finite JSON constant: {value}")


def strict_json_loads(text: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise R13RunnerError(f"invalid strict JSON: {error}") from error
    _require(isinstance(value, dict), "strict JSON result must be an object")
    _validate_json_value(value)
    return value


def write_result_strict(path: Path, result: Mapping[str, Any]) -> None:
    """Exclusive-create a strict JSON result; an existing file is never replaced."""

    payload = strict_json_dumps(dict(result))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as error:
        raise R13RunnerError(f"refusing to overwrite existing result: {path}") from error


def format_utc_z(value: datetime) -> str:
    _require(isinstance(value, datetime), "clock must return datetime")
    _require(value.tzinfo is not None, "clock datetime must be timezone-aware")
    _require(value.utcoffset() is not None, "clock datetime has invalid timezone")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc_z(value: Any, field: str) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z"), f"{field} must end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise R13RunnerError(f"{field} must be RFC3339 UTC") from error
    _require(parsed.tzinfo == timezone.utc, f"{field} must be UTC")
    return parsed


def _validate_contract(contract: R13RunContract) -> dict[str, TargetPanelBinding]:
    _require(isinstance(contract, R13RunContract), "contract has wrong type")
    _require(
        isinstance(contract.run_id, str) and _SAFE_ID_RE.fullmatch(contract.run_id),
        "run_id has invalid format",
    )
    _require(
        isinstance(contract.bundle_run_id, str)
        and _SAFE_ID_RE.fullmatch(contract.bundle_run_id),
        "bundle_run_id has invalid format",
    )
    _require(contract.replicate_id in ARM_ORDER_BY_REPLICATE, "replicate_id must be A or B")
    _require(contract.stack_id in STACK_IDS, "stack_id is outside the frozen four M0 stacks")
    _require(set(contract.bindings) == BINDING_KEYS, "binding key set drift")
    for key in sorted(BINDING_KEYS):
        validate_sha256(contract.bindings[key], f"bindings.{key}")
    validate_sha256(contract.initial_parameter_hash, "initial_parameter_hash")
    _require(
        tuple(contract.ordered_candidate_set) == ORDERED_CANDIDATE_SET,
        "ordered candidate set must be FINAL=K0..FINAL=K6",
    )
    _require(len(contract.target_panels) == 2, "exactly two target panels are required")
    panel_by_arm: dict[str, TargetPanelBinding] = {}
    for panel in contract.target_panels:
        _require(isinstance(panel, TargetPanelBinding), "target panel has wrong type")
        _require(panel.arm in ARMS and panel.arm not in panel_by_arm, "target arm coverage drift")
        validate_sha256(panel.target_panel_sha256, f"target panel {panel.arm}")
        _require(
            panel.target_panel_sha256 != contract.bindings["source_assets_sha256"],
            "source and target asset commitments must differ",
        )
        _require(len(panel.ordered_rows) == 7, f"{panel.arm} must bind seven rows")
        canonical_instance_ids: list[str] = []
        row_ids: list[str] = []
        for row in panel.ordered_rows:
            _require(isinstance(row, TargetRowBinding), "target row binding has wrong type")
            _require(
                isinstance(row.canonical_instance_id, str)
                and _SAFE_ID_RE.fullmatch(row.canonical_instance_id),
                f"invalid canonical_instance_id in {panel.arm}",
            )
            _require(
                isinstance(row.row_id, str) and _SAFE_ID_RE.fullmatch(row.row_id),
                f"invalid row_id in {panel.arm}",
            )
            validate_sha256(row.panel_row_sha256, f"{panel.arm}.{row.row_id}.panel_row_sha256")
            canonical_instance_ids.append(row.canonical_instance_id)
            row_ids.append(row.row_id)
        _require(
            len(set(canonical_instance_ids)) == 7,
            f"{panel.arm} canonical instance ids must be unique",
        )
        _require(len(set(row_ids)) == 7, f"{panel.arm} row ids must be unique")
        panel_by_arm[panel.arm] = panel
    _require(set(panel_by_arm) == set(ARMS), "both frozen target arms are required")
    _require(
        [row.canonical_instance_id for row in panel_by_arm[ARMS[0]].ordered_rows]
        == [row.canonical_instance_id for row in panel_by_arm[ARMS[1]].ordered_rows],
        "H0/H1 must bind the same seven canonical rows in the same order",
    )
    _require(
        panel_by_arm[ARMS[0]].target_panel_sha256
        != panel_by_arm[ARMS[1]].target_panel_sha256,
        "H0/H1 target panel commitments must differ",
    )
    return panel_by_arm


def _source_seed_material(contract: R13RunContract, source_identity: str) -> str:
    """A/B-stable seed commitment with no target or evaluation-order input."""

    return sha256_json(
        {
            "protocol_sha256": contract.bindings["protocol_sha256"],
            "source_assets_sha256": contract.bindings["source_assets_sha256"],
            "stack_id": contract.stack_id,
            "source_rule_identity": source_identity,
        }
    )


def _source_update_call(contract: R13RunContract, source_identity: str) -> SourceUpdateCall:
    request = SourceUpdateCall(
        stack_id=contract.stack_id,
        source_rule_identity=source_identity,
        source_assets_sha256=contract.bindings["source_assets_sha256"],
        protocol_sha256=contract.bindings["protocol_sha256"],
        source_seed_material_sha256=_source_seed_material(contract, source_identity),
    )
    observed_fields = tuple(field.name for field in fields(request))
    _require(observed_fields == SOURCE_UPDATE_CALL_FIELDS, "source-update call field set drift")
    for name in observed_fields:
        lowered = name.lower()
        _require(
            not any(token in lowered for token in _FORBIDDEN_UPDATE_FIELD_TOKENS),
            f"target/order field leaked into source update input: {name}",
        )
    return request


def _validate_backend_diagnostics(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "backend update diagnostics must be a mapping")
    diagnostics = dict(value)
    for key in diagnostics:
        _require(isinstance(key, str), "backend diagnostic keys must be strings")
        lowered = key.lower()
        _require(
            not any(token in lowered for token in ("arm", "target", "panel")),
            f"target information leaked into update diagnostics: {key}",
        )
    _validate_json_value(diagnostics, "backend_update_diagnostics")
    return diagnostics


def _backend_update_receipt(
    update_result: SourceUpdateResult, diagnostics: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "optimizer_step_count": update_result.optimizer_step_count,
        "clipping_triggered": update_result.clipping_triggered,
        "non_finite_observed": update_result.non_finite_observed,
        "diagnostics": dict(diagnostics),
    }


def _validate_backend_update_receipt(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "backend update receipt must be an object")
    _require(
        set(value)
        == {
            "optimizer_step_count",
            "clipping_triggered",
            "non_finite_observed",
            "diagnostics",
        },
        "backend update receipt schema drift",
    )
    _require(value["optimizer_step_count"] == 1, "backend receipt optimizer step drift")
    _require(value["clipping_triggered"] is False, "backend receipt clipping drift")
    _require(value["non_finite_observed"] is False, "backend receipt non-finite drift")
    _validate_backend_diagnostics(value["diagnostics"])
    return value


def run_stack_process(
    contract: R13RunContract,
    backend: R13Backend,
    *,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """Execute one injected (replicate, stack) process under the frozen R13 shape.

    The core makes 5 source-update calls: exactly one for every (k, s, r).  Each
    update is followed by both target readouts without reset or another update.
    """

    panel_by_arm = _validate_contract(contract)
    arm_order = ARM_ORDER_BY_REPLICATE[contract.replicate_id]
    events: list[dict[str, Any]] = []
    pre_readouts: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    counters = {
        "fresh_reset_calls": 0,
        "source_update_calls": 0,
        "target_read_calls": 0,
        "parameter_hash_observations": 0,
    }
    last_event_time: datetime | None = None
    readout_ordinal = 0

    def emit(event_type: str, **details: Any) -> str:
        nonlocal last_event_time
        at = clock()
        timestamp = format_utc_z(at)
        parsed = parse_utc_z(timestamp, "event.at_utc")
        if last_event_time is not None:
            _require(parsed >= last_event_time, "event clock moved backwards")
        last_event_time = parsed
        index = len(events) + 1
        event_id = f"{contract.run_id}|event|{index:04d}"
        event = {
            "event_index": index,
            "event_id": event_id,
            "event_type": event_type,
            "at_utc": timestamp,
            **details,
        }
        _validate_json_value(event, f"events[{index - 1}]")
        events.append(event)
        return event_id

    def observe_parameter_hash(label: str) -> str:
        counters["parameter_hash_observations"] += 1
        value = backend.parameter_hash()
        return validate_sha256(value, label)

    def fresh_reset(*, phase: str, execution_key: str | None) -> str:
        backend.reset_trainable()
        counters["fresh_reset_calls"] += 1
        observed = observe_parameter_hash(f"{phase}.initial_hash_after_reset")
        _require(
            observed == contract.initial_parameter_hash,
            f"fresh reset failed before {phase}",
        )
        emit(
            "FRESH_RESET_VERIFIED",
            phase=phase,
            execution_key=execution_key,
            parameter_hash=observed,
        )
        return observed

    def read_target(
        *,
        phase: str,
        arm: str,
        source_identity: str | None,
        source_update_hash: str,
        execution_key: str | None,
    ) -> dict[str, Any]:
        nonlocal readout_ordinal
        panel = panel_by_arm[arm]
        before = observe_parameter_hash(f"{phase}.{arm}.parameter_hash_before")
        _require(before == source_update_hash, f"unexpected parameter state before {phase} {arm}")
        request = TargetReadCall(
            stack_id=contract.stack_id,
            replicate_id=contract.replicate_id,
            phase=phase,
            arm=arm,
            source_rule_identity=source_identity,
            source_update_hash=source_update_hash,
            target_panel_sha256=panel.target_panel_sha256,
            ordered_row_ids=tuple(row.row_id for row in panel.ordered_rows),
            ordered_candidate_set=tuple(contract.ordered_candidate_set),
        )
        _require(request.require_grad is False, "target readout must forbid gradients")
        _require(request.optimizer_steps_permitted == 0, "target readout must forbid optimizer steps")
        _require(request.state_mutation_permitted is False, "target readout must forbid state mutation")
        observed = backend.read_target(request)
        counters["target_read_calls"] += 1
        _require(isinstance(observed, TargetReadResult), "backend returned wrong target-read type")
        _require(type(observed.gradient_count) is int and observed.gradient_count == 0, "target readout reported a gradient")
        _require(type(observed.optimizer_step_count) is int and observed.optimizer_step_count == 0, "target readout reported an optimizer step")
        _require(type(observed.state_mutation_count) is int and observed.state_mutation_count == 0, "target readout reported state mutation")
        _require(observed.non_finite_observed is False, "target readout reported non-finite computation")
        after = observe_parameter_hash(f"{phase}.{arm}.parameter_hash_after")
        _require(after == before == source_update_hash, f"target readout mutated parameters for {phase} {arm}")
        _require(len(observed.rows) == 7, f"{phase} {arm} must return seven target rows")
        row_traces: list[dict[str, Any]] = []
        for row_index, (raw_row, bound_row) in enumerate(zip(observed.rows, panel.ordered_rows)):
            _require(isinstance(raw_row, RawCandidateRow), "backend returned wrong raw-row type")
            _require(raw_row.row_id == bound_row.row_id, f"target row order/id drift at {phase} {arm} row {row_index}")
            _require(len(raw_row.ordered_candidate_scores) == 7, f"{phase} {arm} row {row_index} must contain seven raw scores")
            scores: list[float] = []
            for score_index, score in enumerate(raw_row.ordered_candidate_scores):
                _require(type(score) in (int, float), f"raw score has invalid type at {phase} {arm} row {row_index} candidate {score_index}")
                numeric = float(score)
                _require(math.isfinite(numeric), f"non-finite raw score at {phase} {arm} row {row_index} candidate {score_index}")
                scores.append(numeric)
            row_traces.append(
                {
                    "row_id": bound_row.row_id,
                    "panel_row_sha256": bound_row.panel_row_sha256,
                    "state": "PRE_TARGET" if phase == "PRE" else "POST_TARGET",
                    "arm": arm,
                    "source_rule_identity": source_identity,
                    "source_update_hash": source_update_hash,
                    "ordered_candidate_scores": scores,
                }
            )
        readout_ordinal += 1
        readout = {
            "readout_ordinal": readout_ordinal,
            "phase": phase,
            "arm": arm,
            "source_rule_identity": source_identity,
            "target_panel_sha256": panel.target_panel_sha256,
            "parameter_hash_before": before,
            "parameter_hash_after": after,
            "row_traces": row_traces,
        }
        emit(
            f"{phase}_TARGET_READOUT_VERIFIED",
            execution_key=execution_key,
            readout_ordinal=readout_ordinal,
            arm=arm,
            source_rule_identity=source_identity,
            parameter_hash=source_update_hash,
        )
        return readout

    emit(
        "RUN_STARTED",
        replicate_id=contract.replicate_id,
        stack_id=contract.stack_id,
        arm_evaluation_order=list(arm_order),
    )
    fresh_reset(phase="PRE", execution_key=None)
    for arm in arm_order:
        pre_readouts.append(
            read_target(
                phase="PRE",
                arm=arm,
                source_identity=None,
                source_update_hash=contract.initial_parameter_hash,
                execution_key=None,
            )
        )

    update_hashes: list[str] = []
    for execution_ordinal, source_identity in enumerate(SOURCE_IDENTITIES, 1):
        execution_key = f"{contract.replicate_id}|{contract.stack_id}|{source_identity}"
        initial_after_reset = fresh_reset(phase="POST", execution_key=execution_key)
        request = _source_update_call(contract, source_identity)
        request_payload = asdict(request)
        request_sha256 = sha256_json(request_payload)
        update_result = backend.update_source(request)
        counters["source_update_calls"] += 1
        _require(isinstance(update_result, SourceUpdateResult), "backend returned wrong source-update type")
        _require(type(update_result.optimizer_step_count) is int and update_result.optimizer_step_count == 1, "source update must report exactly one optimizer step")
        _require(update_result.clipping_triggered is False, "source update reported realized clipping")
        _require(update_result.non_finite_observed is False, "source update reported non-finite computation")
        diagnostics = _validate_backend_diagnostics(update_result.diagnostics)
        update_hash = observe_parameter_hash(f"{execution_key}.update_parameter_hash")
        _require(update_hash != initial_after_reset, f"source update did not change parameters for {execution_key}")
        update_hashes.append(update_hash)
        execution_event_id = emit(
            "SOURCE_UPDATE_VERIFIED",
            execution_key=execution_key,
            execution_ordinal=execution_ordinal,
            source_rule_identity=source_identity,
            source_update_call_sha256=request_sha256,
            update_parameter_hash=update_hash,
            optimizer_step_count=1,
        )
        readouts = [
            read_target(
                phase="POST",
                arm=arm,
                source_identity=source_identity,
                source_update_hash=update_hash,
                execution_key=execution_key,
            )
            for arm in arm_order
        ]
        executions.append(
            {
                "execution_event_id": execution_event_id,
                "execution_ordinal": execution_ordinal,
                "execution_key": execution_key,
                "source_rule_identity": source_identity,
                "initial_parameter_hash_after_reset": initial_after_reset,
                "source_update_call_sha256": request_sha256,
                "update_parameter_hash": update_hash,
                "optimizer_step_count": 1,
                "readout_gradient_count": 0,
                "readout_optimizer_step_count": 0,
                "readout_state_mutation_count": 0,
                "backend_update_receipt": _backend_update_receipt(
                    update_result, diagnostics
                ),
                "readouts": readouts,
            }
        )

    _require(len(set(update_hashes)) == 5, "five source identities did not yield five distinct update hashes")
    counts = {
        "technical_processes": 1,
        "fresh_reset_calls": counters["fresh_reset_calls"],
        "source_update_calls": counters["source_update_calls"],
        "technical_update_executions": len(executions),
        "unique_design_source_updates": len({item["source_rule_identity"] for item in executions}),
        "target_read_calls": counters["target_read_calls"],
        "pre_target_vectors": len(pre_readouts),
        "post_target_vectors": sum(len(item["readouts"]) for item in executions),
        "pre_target_identity_reads": len(pre_readouts) * 6,
        "post_target_identity_reads": sum(len(item["readouts"]) for item in executions) * 6,
        "total_target_identity_metric_cells": (len(pre_readouts) + sum(len(item["readouts"]) for item in executions)) * 6,
        "raw_target_row_traces": sum(len(readout["row_traces"]) for readout in pre_readouts) + sum(len(readout["row_traces"]) for item in executions for readout in item["readouts"]),
        "raw_candidate_scores": sum(len(trace["ordered_candidate_scores"]) for readout in pre_readouts for trace in readout["row_traces"]) + sum(len(trace["ordered_candidate_scores"]) for item in executions for readout in item["readouts"] for trace in readout["row_traces"]),
        "parameter_hash_observations": counters["parameter_hash_observations"],
        "event_count": len(events) + 1,
    }
    _require(counts == EXPECTED_PROCESS_COUNTS, f"process count closure failed: {counts!r}")
    emit("RUN_COMPLETED", counts=counts)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "R13_STATIC_CORE_TRACE_COMPLETE_NOT_SCIENTIFIC_RESULT",
        "run_id": contract.run_id,
        "bundle_run_id": contract.bundle_run_id,
        "created_at_utc": events[-1]["at_utc"],
        "static_core_only": True,
        "model_execution_authorized_by_core": False,
        "scientific_evidence": False,
        "formal_experiment": False,
        "development_only": True,
        "replicate_id": contract.replicate_id,
        "stack_id": contract.stack_id,
        "arm_evaluation_order": list(arm_order),
        "bindings": dict(contract.bindings),
        "ordered_candidate_set": list(contract.ordered_candidate_set),
        "initial_parameter_hash": contract.initial_parameter_hash,
        "pre_target_readouts": pre_readouts,
        "source_update_executions": executions,
        "counts": counts,
        "events": events,
    }
    validate_runner_result(result)
    strict_json_dumps(result)
    return result


_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "run_id",
        "bundle_run_id",
        "created_at_utc",
        "static_core_only",
        "model_execution_authorized_by_core",
        "scientific_evidence",
        "formal_experiment",
        "development_only",
        "replicate_id",
        "stack_id",
        "arm_evaluation_order",
        "bindings",
        "ordered_candidate_set",
        "initial_parameter_hash",
        "pre_target_readouts",
        "source_update_executions",
        "counts",
        "events",
    }
)

_READOUT_KEYS = frozenset(
    {
        "readout_ordinal",
        "phase",
        "arm",
        "source_rule_identity",
        "target_panel_sha256",
        "parameter_hash_before",
        "parameter_hash_after",
        "row_traces",
    }
)

_TRACE_KEYS = frozenset(
    {
        "row_id",
        "panel_row_sha256",
        "state",
        "arm",
        "source_rule_identity",
        "source_update_hash",
        "ordered_candidate_scores",
    }
)

_EXECUTION_KEYS = frozenset(
    {
        "execution_event_id",
        "execution_ordinal",
        "execution_key",
        "source_rule_identity",
        "initial_parameter_hash_after_reset",
        "source_update_call_sha256",
        "update_parameter_hash",
        "optimizer_step_count",
        "readout_gradient_count",
        "readout_optimizer_step_count",
        "readout_state_mutation_count",
        "backend_update_receipt",
        "readouts",
    }
)


def _validate_readout(
    readout: Any,
    *,
    expected_ordinal: int,
    expected_phase: str,
    expected_arm: str,
    expected_identity: str | None,
    expected_hash: str,
) -> None:
    _require(isinstance(readout, dict) and set(readout) == _READOUT_KEYS, "readout schema drift")
    _require(readout["readout_ordinal"] == expected_ordinal, "readout ordinal drift")
    _require(readout["phase"] == expected_phase, "readout phase drift")
    _require(readout["arm"] == expected_arm, "readout arm order drift")
    _require(readout["source_rule_identity"] == expected_identity, "readout source identity drift")
    validate_sha256(readout["target_panel_sha256"], "readout.target_panel_sha256")
    _require(readout["parameter_hash_before"] == expected_hash, "readout before hash drift")
    _require(readout["parameter_hash_after"] == expected_hash, "readout after hash drift")
    traces = readout["row_traces"]
    _require(isinstance(traces, list) and len(traces) == 7, "readout must contain seven row traces")
    row_ids: list[str] = []
    for trace in traces:
        _require(isinstance(trace, dict) and set(trace) == _TRACE_KEYS, "row trace schema drift")
        _require(trace["state"] == f"{expected_phase}_TARGET", "row trace state drift")
        _require(trace["arm"] == expected_arm, "row trace arm drift")
        _require(trace["source_rule_identity"] == expected_identity, "row trace source identity drift")
        _require(trace["source_update_hash"] == expected_hash, "row trace update hash drift")
        validate_sha256(trace["panel_row_sha256"], "row_trace.panel_row_sha256")
        _require(isinstance(trace["row_id"], str), "row trace row_id must be a string")
        row_ids.append(trace["row_id"])
        scores = trace["ordered_candidate_scores"]
        _require(isinstance(scores, list) and len(scores) == 7, "row trace must contain seven candidate scores")
        for score in scores:
            _require(type(score) in (int, float) and math.isfinite(float(score)), "row trace score is not finite numeric")
    _require(len(set(row_ids)) == 7, "row trace ids must be unique")


def validate_runner_result(result: Mapping[str, Any]) -> None:
    """Validate the exact runner-produced schema without trusting its counts."""

    _require(isinstance(result, Mapping), "runner result must be a mapping")
    value = dict(result)
    _require(set(value) == _TOP_LEVEL_KEYS, "runner result top-level schema drift")
    _require(value["schema_version"] == SCHEMA_VERSION, "runner schema version drift")
    _require(value["status"] == "R13_STATIC_CORE_TRACE_COMPLETE_NOT_SCIENTIFIC_RESULT", "runner status drift")
    for field, expected in (
        ("static_core_only", True),
        ("model_execution_authorized_by_core", False),
        ("scientific_evidence", False),
        ("formal_experiment", False),
        ("development_only", True),
    ):
        _require(value[field] is expected, f"{field} boundary drift")
    _require(isinstance(value["run_id"], str) and _SAFE_ID_RE.fullmatch(value["run_id"]), "result run_id drift")
    _require(
        isinstance(value["bundle_run_id"], str)
        and _SAFE_ID_RE.fullmatch(value["bundle_run_id"]),
        "result bundle_run_id drift",
    )
    parse_utc_z(value["created_at_utc"], "created_at_utc")
    replicate = value["replicate_id"]
    stack = value["stack_id"]
    _require(replicate in ARM_ORDER_BY_REPLICATE, "result replicate_id drift")
    _require(stack in STACK_IDS, "result stack_id drift")
    arm_order = list(ARM_ORDER_BY_REPLICATE[replicate])
    _require(value["arm_evaluation_order"] == arm_order, "result arm evaluation order drift")
    bindings = value["bindings"]
    _require(isinstance(bindings, dict) and set(bindings) == BINDING_KEYS, "result bindings drift")
    for key in BINDING_KEYS:
        validate_sha256(bindings[key], f"bindings.{key}")
    _require(value["ordered_candidate_set"] == list(ORDERED_CANDIDATE_SET), "result candidate order drift")
    initial_hash = validate_sha256(value["initial_parameter_hash"], "initial_parameter_hash")

    pre = value["pre_target_readouts"]
    _require(isinstance(pre, list) and len(pre) == 2, "pre readout coverage drift")
    readout_ordinal = 0
    for arm, readout in zip(arm_order, pre):
        readout_ordinal += 1
        _validate_readout(
            readout,
            expected_ordinal=readout_ordinal,
            expected_phase="PRE",
            expected_arm=arm,
            expected_identity=None,
            expected_hash=initial_hash,
        )

    executions = value["source_update_executions"]
    _require(isinstance(executions, list) and len(executions) == 5, "source execution coverage drift")
    update_hashes: list[str] = []
    for ordinal, (identity, execution) in enumerate(zip(SOURCE_IDENTITIES, executions), 1):
        _require(isinstance(execution, dict) and set(execution) == _EXECUTION_KEYS, "source execution schema drift")
        expected_key = f"{replicate}|{stack}|{identity}"
        _require(execution["execution_ordinal"] == ordinal, "source execution ordinal drift")
        _require(execution["execution_key"] == expected_key, "source execution key drift")
        _require(execution["source_rule_identity"] == identity, "source identity order drift")
        _require(execution["initial_parameter_hash_after_reset"] == initial_hash, "fresh reset hash drift")
        validate_sha256(execution["source_update_call_sha256"], "source_update_call_sha256")
        update_hash = validate_sha256(execution["update_parameter_hash"], "update_parameter_hash")
        _require(update_hash != initial_hash, "source update equals initial parameters")
        update_hashes.append(update_hash)
        _require(execution["optimizer_step_count"] == 1, "optimizer step count drift")
        _require(execution["readout_gradient_count"] == 0, "readout gradient count drift")
        _require(execution["readout_optimizer_step_count"] == 0, "readout optimizer count drift")
        _require(execution["readout_state_mutation_count"] == 0, "readout mutation count drift")
        _validate_backend_update_receipt(execution["backend_update_receipt"])
        readouts = execution["readouts"]
        _require(isinstance(readouts, list) and len(readouts) == 2, "post readout coverage drift")
        for arm, readout in zip(arm_order, readouts):
            readout_ordinal += 1
            _validate_readout(
                readout,
                expected_ordinal=readout_ordinal,
                expected_phase="POST",
                expected_arm=arm,
                expected_identity=identity,
                expected_hash=update_hash,
            )
    _require(len(set(update_hashes)) == 5, "source update hashes are not unique")

    counts = value["counts"]
    _require(counts == EXPECTED_PROCESS_COUNTS, "runner count receipt drift")
    events = value["events"]
    _require(isinstance(events, list) and len(events) == EXPECTED_PROCESS_COUNTS["event_count"], "event count drift")
    previous_time: datetime | None = None
    for index, event in enumerate(events, 1):
        _require(isinstance(event, dict), "event must be an object")
        _require(event.get("event_index") == index, "event index drift")
        _require(event.get("event_id") == f"{value['run_id']}|event|{index:04d}", "event id drift")
        _require(isinstance(event.get("event_type"), str), "event type missing")
        observed_time = parse_utc_z(event.get("at_utc"), f"events[{index - 1}].at_utc")
        if previous_time is not None:
            _require(observed_time >= previous_time, "event timestamps moved backwards")
        previous_time = observed_time
    _require(events[0]["event_type"] == "RUN_STARTED", "first event must start run")
    _require(events[-1]["event_type"] == "RUN_COMPLETED", "last event must complete run")
    _require(value["created_at_utc"] == events[-1]["at_utc"], "created_at_utc must equal completion event")
    update_event_ids = {event["event_id"] for event in events if event["event_type"] == "SOURCE_UPDATE_VERIFIED"}
    _require(
        update_event_ids == {execution["execution_event_id"] for execution in executions},
        "source executions do not bind exact update events",
    )
    _validate_json_value(value)


def _readout_map(result: Mapping[str, Any]) -> dict[tuple[str, str | None, str], dict[str, Any]]:
    output: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for readout in result["pre_target_readouts"]:
        output[(readout["phase"], None, readout["arm"])] = readout
    for execution in result["source_update_executions"]:
        for readout in execution["readouts"]:
            output[(readout["phase"], execution["source_rule_identity"], readout["arm"])] = readout
    return output


def verify_ab_reproducibility(
    result_a: Mapping[str, Any],
    result_b: Mapping[str, Any],
    *,
    absolute_tolerance: float = 1e-12,
) -> dict[str, Any]:
    """Verify A/B equality after indexing away the deliberately reversed arm order."""

    _require(type(absolute_tolerance) in (int, float) and math.isfinite(float(absolute_tolerance)), "A/B tolerance must be finite")
    _require(float(absolute_tolerance) >= 0.0, "A/B tolerance must be non-negative")
    validate_runner_result(result_a)
    validate_runner_result(result_b)
    _require(result_a["replicate_id"] == "A" and result_b["replicate_id"] == "B", "A/B results must be supplied in A, B order")
    _require(result_a["stack_id"] == result_b["stack_id"], "A/B stack mismatch")
    _require(result_a["bundle_run_id"] == result_b["bundle_run_id"], "A/B bundle run mismatch")
    _require(result_a["bindings"] == result_b["bindings"], "A/B binding mismatch")
    _require(result_a["initial_parameter_hash"] == result_b["initial_parameter_hash"], "A/B initial parameter mismatch")
    _require(result_a["ordered_candidate_set"] == result_b["ordered_candidate_set"], "A/B candidate order mismatch")
    executions_a = {item["source_rule_identity"]: item for item in result_a["source_update_executions"]}
    executions_b = {item["source_rule_identity"]: item for item in result_b["source_update_executions"]}
    for identity in SOURCE_IDENTITIES:
        _require(executions_a[identity]["source_update_call_sha256"] == executions_b[identity]["source_update_call_sha256"], f"A/B source update input mismatch for {identity}")
        _require(executions_a[identity]["update_parameter_hash"] == executions_b[identity]["update_parameter_hash"], f"A/B source update hash mismatch for {identity}")

    readouts_a = _readout_map(result_a)
    readouts_b = _readout_map(result_b)
    _require(set(readouts_a) == set(readouts_b), "A/B readout coverage mismatch")
    max_abs_difference = 0.0
    score_count = 0
    for key in sorted(readouts_a, key=lambda item: (item[0], item[1] or "", item[2])):
        left = readouts_a[key]
        right = readouts_b[key]
        _require(left["target_panel_sha256"] == right["target_panel_sha256"], f"A/B target panel mismatch at {key}")
        _require(left["parameter_hash_before"] == right["parameter_hash_before"], f"A/B readout parameter mismatch at {key}")
        _require(left["parameter_hash_after"] == right["parameter_hash_after"], f"A/B post-read parameter mismatch at {key}")
        for left_row, right_row in zip(left["row_traces"], right["row_traces"]):
            _require(left_row["row_id"] == right_row["row_id"], f"A/B row mismatch at {key}")
            _require(left_row["panel_row_sha256"] == right_row["panel_row_sha256"], f"A/B row commitment mismatch at {key}")
            for left_score, right_score in zip(left_row["ordered_candidate_scores"], right_row["ordered_candidate_scores"]):
                difference = abs(float(left_score) - float(right_score))
                max_abs_difference = max(max_abs_difference, difference)
                score_count += 1
    _require(max_abs_difference <= float(absolute_tolerance), f"A/B raw candidate scores differ by {max_abs_difference}, tolerance {absolute_tolerance}")
    receipt = {
        "schema_version": AB_RECEIPT_SCHEMA_VERSION,
        "status": "PASS_AB_REVERSED_ORDER_REPRODUCIBILITY",
        "stack_id": result_a["stack_id"],
        "replicate_order": ["A", "B"],
        "arm_evaluation_order_by_replicate": {
            "A": list(ARM_ORDER_BY_REPLICATE["A"]),
            "B": list(ARM_ORDER_BY_REPLICATE["B"]),
        },
        "source_update_call_hashes_match": True,
        "source_update_parameter_hashes_match": True,
        "compared_readout_vectors": len(readouts_a),
        "compared_raw_candidate_scores": score_count,
        "technical_absolute_tolerance": float(absolute_tolerance),
        "maximum_absolute_raw_score_difference": max_abs_difference,
        "scientific_evidence": False,
    }
    _validate_json_value(receipt)
    return receipt


def validate_process_collection(
    results: Sequence[Mapping[str, Any]],
    *,
    variant: str,
    absolute_tolerance: float = 1e-12,
) -> dict[str, Any]:
    """Close exact 4-process A or 8-process A/B coverage and aggregate counts."""

    variant_replicates = {
        "MINIMAL_4_PROCESS_A": ("A",),
        "REPRODUCIBILITY_8_PROCESS_AB": ("A", "B"),
    }
    _require(variant in variant_replicates, "unsupported R13 execution variant")
    replicates = variant_replicates[variant]
    expected_keys = {(replicate, stack) for stack in STACK_IDS for replicate in replicates}
    observed: dict[tuple[str, str], Mapping[str, Any]] = {}
    run_ids: set[str] = set()
    shared_bundle_run_id: str | None = None
    shared_bindings: dict[str, str] | None = None
    for result in results:
        validate_runner_result(result)
        key = (result["replicate_id"], result["stack_id"])
        _require(key in expected_keys, f"unexpected process result: {key}")
        _require(key not in observed, f"duplicate process result: {key}")
        _require(result["run_id"] not in run_ids, "duplicate run_id in collection")
        run_ids.add(result["run_id"])
        observed[key] = result
        if shared_bundle_run_id is None:
            shared_bundle_run_id = result["bundle_run_id"]
        else:
            _require(
                result["bundle_run_id"] == shared_bundle_run_id,
                "collection bundle_run_id drift",
            )
        if shared_bindings is None:
            shared_bindings = dict(result["bindings"])
        else:
            _require(result["bindings"] == shared_bindings, "collection binding drift")
    _require(set(observed) == expected_keys, "process collection coverage is incomplete")

    ab_receipts: list[dict[str, Any]] = []
    if replicates == ("A", "B"):
        for stack in STACK_IDS:
            ab_receipts.append(
                verify_ab_reproducibility(
                    observed[("A", stack)],
                    observed[("B", stack)],
                    absolute_tolerance=absolute_tolerance,
                )
            )
    process_count = len(expected_keys)
    technical_updates = process_count * 5
    aggregate_counts = {
        "os_processes": process_count,
        "unique_design_source_updates": len(STACK_IDS) * len(SOURCE_IDENTITIES),
        "technical_update_executions": technical_updates,
        "fresh_reset_calls": process_count * EXPECTED_PROCESS_COUNTS["fresh_reset_calls"],
        "source_update_calls": technical_updates,
        "target_read_calls": process_count * EXPECTED_PROCESS_COUNTS["target_read_calls"],
        "pre_target_vectors": process_count * 2,
        "post_target_vectors": technical_updates * 2,
        "pre_target_identity_reads": process_count * 2 * 6,
        "post_target_identity_reads": technical_updates * 2 * 6,
        "total_target_identity_metric_cells": process_count * 2 * 6 + technical_updates * 2 * 6,
        "raw_target_row_traces": process_count * EXPECTED_PROCESS_COUNTS["raw_target_row_traces"],
        "raw_candidate_scores": process_count * EXPECTED_PROCESS_COUNTS["raw_candidate_scores"],
        "event_count": process_count * EXPECTED_PROCESS_COUNTS["event_count"],
    }
    expected_protocol_counts = {
        "MINIMAL_4_PROCESS_A": {
            "os_processes": 4,
            "unique_design_source_updates": 20,
            "technical_update_executions": 20,
            "pre_target_identity_reads": 48,
            "post_target_identity_reads": 240,
            "total_target_identity_metric_cells": 288,
        },
        "REPRODUCIBILITY_8_PROCESS_AB": {
            "os_processes": 8,
            "unique_design_source_updates": 20,
            "technical_update_executions": 40,
            "pre_target_identity_reads": 96,
            "post_target_identity_reads": 480,
            "total_target_identity_metric_cells": 576,
        },
    }[variant]
    for key, expected in expected_protocol_counts.items():
        _require(aggregate_counts[key] == expected, f"collection protocol count mismatch: {key}")
    receipt = {
        "schema_version": COLLECTION_RECEIPT_SCHEMA_VERSION,
        "status": "PASS_R13_STATIC_PROCESS_COLLECTION_SHAPE_ONLY",
        "variant": variant,
        "bundle_run_id": shared_bundle_run_id,
        "ordered_stack_ids": list(STACK_IDS),
        "ordered_replicates": list(replicates),
        "bindings": shared_bindings,
        "counts": aggregate_counts,
        "ab_reproducibility_receipts": ab_receipts,
        "static_core_only": True,
        "model_execution_authorized_by_core": False,
        "scientific_evidence": False,
    }
    _validate_json_value(receipt)
    return receipt


__all__ = [
    "AB_RECEIPT_SCHEMA_VERSION",
    "ARMS",
    "ARM_ORDER_BY_REPLICATE",
    "BINDING_KEYS",
    "COLLECTION_RECEIPT_SCHEMA_VERSION",
    "EXPECTED_PROCESS_COUNTS",
    "ORDERED_CANDIDATE_SET",
    "R13Backend",
    "R13RunContract",
    "R13RunnerError",
    "RawCandidateRow",
    "SCHEMA_VERSION",
    "SOURCE_IDENTITIES",
    "SOURCE_UPDATE_CALL_FIELDS",
    "STACK_IDS",
    "SourceUpdateCall",
    "SourceUpdateResult",
    "TargetPanelBinding",
    "TargetReadCall",
    "TargetReadResult",
    "TargetRowBinding",
    "canonical_json_bytes",
    "format_utc_z",
    "parse_utc_z",
    "run_stack_process",
    "sha256_json",
    "strict_json_dumps",
    "strict_json_loads",
    "validate_process_collection",
    "validate_runner_result",
    "verify_ab_reproducibility",
    "write_result_strict",
]
