"""Fail-closed local R13 canary, worker, and sealed-batch coordinator.

Importing this module performs no model action.  Model initialization occurs
only after an explicit ``technical-canary`` or ``worker`` command/function
call.  The coordinator has an exact isolated default worker command; tests and
reviewed launchers may inject an equivalent command builder.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

# ``python -I`` intentionally omits the script directory.  Establish the two
# exact, resolved local import roots before importing any project module.
_MVP_DIR = Path(__file__).resolve(strict=True).parent
_PROJECT_ROOT = _MVP_DIR.parent
if _MVP_DIR.name != "mvp_same_source_v1" or _PROJECT_ROOT.name != "GPT_PRO_REVIEW_RLVR_V1":
    raise RuntimeError("R13 batch runner path identity drift")
for _local_import_root in (str(_PROJECT_ROOT), str(_MVP_DIR)):
    if _local_import_root not in sys.path:
        sys.path.insert(0, _local_import_root)

import r13_hf_backend as hf_backend
import r13_lazy_backend_adapter as lazy_adapter
import r13_local_development_manifest as local_manifest
import r13_model_inventory as model_inventory_contract
import r13_real_backend as real_backend
import r13_runner_core as runner
import r13_runtime_environment as runtime_environment_contract


SCHEMA_VERSION = "r13-local-batch-runner-r1"
BUNDLE_SCHEMA_VERSION = "r13-local-development-result-bundle-r1"
BUNDLE_STATUS = "SEALED_EIGHT_PROCESS_LOCAL_DEVELOPMENT_BATCH_COMPLETE_PRE_VALIDATION"
BUNDLE_EVIDENCE_BOUNDARY = "LOCAL_DEVELOPMENT_ONLY_NONCONFIRMATORY"
BUNDLE_FILENAME = "R13_LOCAL_DEVELOPMENT_RESULT_BUNDLE_R1.json"
TECHNICAL_CANARY_EMPTY_CELLS_SHA256 = hashlib.sha256(
    b"R13_TECHNICAL_CANARY_NO_SOURCE_OR_TARGET_CELLS_V1"
).hexdigest()

BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "run_id",
        "created_at_utc",
        "selected_variant",
        "evidence_boundary",
        "scientific_evidence",
        "formal_experiment",
        "formal_confirmatory",
        "sampled_rlvr",
        "same_empirical_or_policy_fpr",
        "model_execution_performed",
        "intermediate_scientific_results_released",
        "manifest_sha256",
        "bindings",
        "ordered_process_ids",
        "counts",
        "model_action_counts",
        "events",
        "process_results",
    }
)

EXPECTED_WORKER_MODEL_ACTION_COUNTS = {
    "tokenizer_loads": 1,
    "model_weight_loads": 1,
    "model_forward_calls": 154,
    "backward_calls": 70,
    "manual_parameter_steps": 5,
    "scientific_source_cells": 5,
    "scientific_target_cells": 84,
}
EXPECTED_BATCH_MODEL_ACTION_COUNTS = {
    "tokenizer_loads": 8,
    "model_weight_loads": 8,
    "model_forward_calls": 1232,
    "backward_calls": 560,
    "manual_parameter_steps": 40,
}


class R13LocalBatchError(RuntimeError):
    """A local launch, process, custody, or bundle gate failed closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise R13LocalBatchError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise R13LocalBatchError(f"cannot hash file: {path}") from error
    return digest.hexdigest()


def _project_root(path: Path) -> Path:
    root = path.resolve(strict=True)
    _require(root.is_dir(), "project root is not a directory")
    _require(root.name == local_manifest.PROJECT_DIR_NAME, "project root name drift")
    return root


def _artifact_path(project: Path, manifest: Mapping[str, Any], role: str) -> Path:
    record = manifest["artifacts"][role]
    relative = record["path"]
    pure = PurePosixPath(relative)
    _require(
        isinstance(relative, str)
        and relative
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in relative
        and str(pure) == relative,
        f"unsafe artifact path: {role}",
    )
    selected = (project / Path(*pure.parts)).resolve(strict=True)
    try:
        selected.relative_to(project)
    except ValueError as error:
        raise R13LocalBatchError(f"artifact escapes project: {role}") from error
    _require(selected.is_file() and not selected.is_symlink(), f"invalid artifact: {role}")
    _require(selected.stat().st_size == record["byte_length"], f"artifact length drift: {role}")
    _require(sha256_file(selected) == record["sha256"], f"artifact hash drift: {role}")
    return selected


def _output_root(project: Path, manifest: Mapping[str, Any]) -> Path:
    workspace = project.parent
    relative = manifest["output"]["root"]
    pure = PurePosixPath(relative)
    _require(
        isinstance(relative, str)
        and relative.startswith(f"{project.name}/{local_manifest.OUTPUT_ROOT_PREFIX}")
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in relative
        and str(pure) == relative,
        "unsafe manifest output root",
    )
    selected = (workspace / Path(*pure.parts)).resolve()
    try:
        selected.relative_to(project)
    except ValueError as error:
        raise R13LocalBatchError("manifest output root escapes project") from error
    return selected


def _load_manifest(
    project: Path,
    manifest_path: Path,
    expected_manifest_sha256: str,
    *,
    require_output_root_absent: bool,
    manifest_reader: Callable[[Path], dict[str, Any]],
    manifest_verifier: Callable[..., None],
) -> dict[str, Any]:
    manifest_file = manifest_path.resolve(strict=True)
    _require(
        sha256_file(manifest_file) == expected_manifest_sha256,
        "local development manifest hash drift",
    )
    manifest = manifest_reader(manifest_file)
    manifest_verifier(
        manifest,
        project_root=project,
        require_output_root_absent=require_output_root_absent,
    )
    return manifest


def _initialization_permit(
    contract: lazy_adapter.StaticAdapterContract,
    model_inventory_sha256: str,
) -> real_backend.LocalInitializationPermit:
    return real_backend.LocalInitializationPermit(
        authorization_basis="USER_IN_THREAD_EXPLICIT_AUTHORIZATION_FOR_CODEX_TO_RUN_THE_MVP",
        scope="R13_LOCAL_TECHNICAL_CANARY_THEN_HASH_BOUND_MANIFEST",
        addendum_sha256=real_backend.EXPECTED_SEMANTICS_SHA256,
        canary_spec_sha256=real_backend.EXPECTED_CANARY_SPEC_SHA256,
        model_inventory_sha256=model_inventory_sha256,
        runtime_reference_sha256=contract.runtime_environment_reference_sha256,
        allow_local_model_initialization=True,
    )


def build_technical_canary_contract(
    *,
    protocol_path: Path,
    update_recipe_path: Path,
    model_inventory_path: Path,
    runtime_reference_path: Path,
) -> lazy_adapter.StaticAdapterContract:
    """Build a valid static identity with zero R13 source or target cells."""

    _require(
        sha256_file(protocol_path) == real_backend.EXPECTED_PROTOCOL_SHA256,
        "technical-canary protocol hash drift",
    )
    _require(
        sha256_file(update_recipe_path) == real_backend.EXPECTED_UPDATE_RECIPE_SHA256,
        "technical-canary update-recipe hash drift",
    )
    inventory = model_inventory_contract.read_inventory(model_inventory_path)
    runtime_environment_contract.read_environment(runtime_reference_path)
    return lazy_adapter.StaticAdapterContract(
        schema_version=lazy_adapter.SCHEMA_VERSION,
        status="TECHNICAL_CANARY_STATIC_IDENTITY_NO_R13_CELLS",
        protocol_sha256=real_backend.EXPECTED_PROTOCOL_SHA256,
        update_recipe_sha256=real_backend.EXPECTED_UPDATE_RECIPE_SHA256,
        source_assets_sha256=TECHNICAL_CANARY_EMPTY_CELLS_SHA256,
        model_inventory_sha256=sha256_file(model_inventory_path),
        runtime_environment_reference_sha256=sha256_file(runtime_reference_path),
        model_repository=inventory["repository"],
        model_revision=inventory["revision"],
        ordered_stack_ids=(),
        source_identities=(),
        ordered_candidate_set=runner.ORDERED_CANDIDATE_SET,
        source_stacks=(),
        model_execution_authorized=False,
        production_backend_bound=False,
        full_dependency_content_hash_bound=False,
    )


BackendFactory = Callable[..., Any]


def run_technical_canary(
    *,
    project_root: Path,
    model_dir: Path,
    receipt_path: Path,
    backend_factory: BackendFactory = real_backend.R13RealBackend,
) -> Mapping[str, Any]:
    project = _project_root(project_root)
    formal = project / "formal_g1_development_r1"
    protocol_path = formal / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json"
    update_recipe_path = formal / "R13_UPDATE_RECIPE_CONTRACT_R1.json"
    inventory_path = formal / "R13_MODEL_INVENTORY_R1.json"
    runtime_path = formal / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json"
    contract = build_technical_canary_contract(
        protocol_path=protocol_path,
        update_recipe_path=update_recipe_path,
        model_inventory_path=inventory_path,
        runtime_reference_path=runtime_path,
    )
    backend = backend_factory(
        static_contract=contract,
        semantics_path=formal / "R13_LOCAL_DEVELOPMENT_EXECUTION_SEMANTICS_ADDENDUM_R1.json",
        canary_spec_path=formal / "R13_LOCAL_TECHNICAL_CANARY_SPEC_R1.json",
        model_inventory_path=inventory_path,
        model_dir=model_dir,
        target_payload_provider=None,
    )
    backend.initialize_local(_initialization_permit(contract, contract.model_inventory_sha256))
    return backend.run_technical_canary(receipt_path=receipt_path)


@dataclass(frozen=True)
class WorkerStaticContext:
    static_contract: lazy_adapter.StaticAdapterContract
    target_payload_provider: Any
    target_panels_by_stack: Mapping[str, tuple[runner.TargetPanelBinding, ...]]
    bindings: Mapping[str, str]


def _load_validator_module(path: Path, expected_sha256: str) -> Any:
    _require(sha256_file(path) == expected_sha256, "result validator hash drift")
    name = f"_r13_local_batch_validator_{expected_sha256[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    _require(spec is not None and spec.loader is not None, "cannot load bound validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def build_worker_static_context(
    *, project_root: Path, manifest_path: Path, manifest: Mapping[str, Any]
) -> WorkerStaticContext:
    project = _project_root(project_root)
    protocol_path = _artifact_path(project, manifest, "protocol")
    recipe_path = _artifact_path(project, manifest, "update_recipe")
    source_path = _artifact_path(project, manifest, "source_bundle")
    inventory_path = _artifact_path(project, manifest, "model_inventory")
    runtime_path = _artifact_path(project, manifest, "runtime_reference")
    validator_path = _artifact_path(project, manifest, "result_validator")
    panel_manifest_path = _artifact_path(project, manifest, "matched_panel_manifest")
    allowlist_path = _artifact_path(project, manifest, "matched_panel_allowlist")
    contract = lazy_adapter.bind_static_contract(
        protocol_path=protocol_path,
        update_recipe_path=recipe_path,
        source_bundles_path=source_path,
        model_inventory_path=inventory_path,
        runtime_environment_path=runtime_path,
    )
    asset_root = panel_manifest_path.parent
    provider = hf_backend.load_disk_bound_target_payload_provider(
        validator_path=validator_path,
        expected_validator_sha256=manifest["artifacts"]["result_validator"]["sha256"],
        protocol_path=protocol_path,
        expected_protocol_sha256=manifest["artifacts"]["protocol"]["sha256"],
        bundle_root=asset_root,
        expected_manifest_sha256=manifest["artifacts"]["matched_panel_manifest"]["sha256"],
        expected_allowlist_receipt_sha256=manifest["artifacts"]["matched_panel_allowlist"]["sha256"],
    )
    validator = _load_validator_module(
        validator_path, manifest["artifacts"]["result_validator"]["sha256"]
    )
    protocol = validator.strict_json_loads(protocol_path.read_bytes(), "bound R13 protocol")
    validator.validate_protocol(protocol, synthetic_test_mode=False)
    loaded = validator.load_bound_matched_panel_bundle(
        asset_root,
        expected_manifest_sha256=manifest["artifacts"]["matched_panel_manifest"]["sha256"],
        expected_allowlist_receipt_sha256=manifest["artifacts"]["matched_panel_allowlist"]["sha256"],
    )
    panels, panel_hashes = validator.validate_matched_panels(loaded.records, protocol)
    canonical_by_record = {
        (record.stack_id, record.arm, record.canonical_z): canonical
        for record, canonical in zip(loaded.records, loaded.canonical_instance_ids)
    }
    target_panels: dict[str, tuple[runner.TargetPanelBinding, ...]] = {}
    for stack_id in runner.STACK_IDS:
        stack_panels: list[runner.TargetPanelBinding] = []
        for arm in runner.ARMS:
            records = panels[(stack_id, arm)]
            payload = provider.panel_payload(stack_id, arm)
            _require(payload.target_panel_sha256 == panel_hashes[(stack_id, arm)], "provider/panel hash drift")
            _require(
                tuple(row.row_id for row in payload.ordered_rows)
                == tuple(record.row_id for record in records),
                "provider/panel row-order drift",
            )
            stack_panels.append(
                runner.TargetPanelBinding(
                    arm=arm,
                    target_panel_sha256=panel_hashes[(stack_id, arm)],
                    ordered_rows=tuple(
                        runner.TargetRowBinding(
                            canonical_instance_id=canonical_by_record[
                                (record.stack_id, record.arm, record.canonical_z)
                            ],
                            row_id=record.row_id,
                            panel_row_sha256=record.row_sha256,
                        )
                        for record in records
                    ),
                )
            )
        target_panels[stack_id] = tuple(stack_panels)
    human_template = (
        asset_root / "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json"
    )
    _require(human_template.is_file(), "human-review template missing")
    bindings = {
        "protocol_sha256": manifest["artifacts"]["protocol"]["sha256"],
        "panel_bundle_sha256": manifest["artifacts"]["matched_panel_manifest"]["sha256"],
        "allowlist_receipt_sha256": manifest["artifacts"]["matched_panel_allowlist"]["sha256"],
        "human_review_receipt_sha256": sha256_file(human_template),
        "authorization_sha256": sha256_file(manifest_path),
        "runner_sha256": manifest["artifacts"]["runner"]["sha256"],
        "validator_sha256": manifest["artifacts"]["result_validator"]["sha256"],
        "model_inventory_sha256": manifest["artifacts"]["model_inventory"]["sha256"],
        "source_assets_sha256": manifest["artifacts"]["source_bundle"]["sha256"],
    }
    _require(set(bindings) == set(runner.BINDING_KEYS), "worker binding coverage drift")
    return WorkerStaticContext(contract, provider, target_panels, bindings)


def worker_result_filename(process_index: int, stack_id: str, replicate_id: str) -> str:
    _require(1 <= process_index <= 8, "worker process index outside 1..8")
    _require(stack_id in runner.STACK_IDS, "worker stack id drift")
    _require(replicate_id in ("A", "B"), "worker replicate id drift")
    safe_stack = stack_id.lower().replace("-", "_")
    return f"{process_index:02d}_{safe_stack}_{replicate_id.lower()}_process.json"


ContextBuilder = Callable[..., WorkerStaticContext]


def run_worker(
    *,
    project_root: Path,
    manifest_path: Path,
    expected_manifest_sha256: str,
    process_id: str,
    model_dir: Path,
    backend_factory: BackendFactory = real_backend.R13RealBackend,
    context_builder: ContextBuilder = build_worker_static_context,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    manifest_reader: Callable[[Path], dict[str, Any]] = local_manifest.read_manifest,
    manifest_verifier: Callable[..., None] = local_manifest.verify_manifest_against_filesystem,
    result_writer: Callable[[Path, Mapping[str, Any]], None] = runner.write_result_strict,
) -> Mapping[str, Any]:
    project = _project_root(project_root)
    manifest_file = manifest_path.resolve(strict=True)
    manifest = _load_manifest(
        project,
        manifest_file,
        expected_manifest_sha256,
        require_output_root_absent=False,
        manifest_reader=manifest_reader,
        manifest_verifier=manifest_verifier,
    )
    _require(process_id in manifest["ordered_process_ids"], "worker process id is not manifest-bound")
    process_index = manifest["ordered_process_ids"].index(process_id) + 1
    stack_id, replicate_id = process_id.split("|")
    output_root = _output_root(project, manifest)
    _require(output_root.exists() and output_root.is_dir(), "coordinator output root is absent")
    _require(not output_root.is_symlink(), "coordinator output root is a link")
    result_path = output_root / worker_result_filename(process_index, stack_id, replicate_id)
    _require(not result_path.exists(), "worker result already exists; model initialization blocked")
    context = context_builder(
        project_root=project, manifest_path=manifest_file, manifest=manifest
    )
    formal = project / "formal_g1_development_r1"
    inventory_path = _artifact_path(project, manifest, "model_inventory")
    backend = backend_factory(
        static_contract=context.static_contract,
        semantics_path=_artifact_path(project, manifest, "local_execution_semantics_addendum"),
        canary_spec_path=_artifact_path(project, manifest, "local_technical_canary_spec"),
        model_inventory_path=inventory_path,
        model_dir=model_dir,
        target_payload_provider=context.target_payload_provider,
    )
    backend.initialize_local(
        _initialization_permit(
            context.static_contract, manifest["artifacts"]["model_inventory"]["sha256"]
        )
    )
    canary_path = _artifact_path(project, manifest, "canary_receipt")
    observed_canary_hash = backend.attest_existing_canary_receipt(receipt_path=canary_path)
    _require(
        observed_canary_hash == manifest["artifacts"]["canary_receipt"]["sha256"],
        "worker canary hash drift",
    )
    backend.activate_scientific_manifest(
        manifest_path=manifest_file,
        expected_manifest_sha256=expected_manifest_sha256,
        output_root_was_preflighted_absent_by_coordinator=True,
    )
    contract = runner.R13RunContract(
        run_id=f"{manifest['run_id']}|{process_index:02d}|{stack_id}|{replicate_id}",
        bundle_run_id=manifest["run_id"],
        replicate_id=replicate_id,
        stack_id=stack_id,
        bindings=dict(context.bindings),
        initial_parameter_hash=backend.initial_parameter_hash,
        ordered_candidate_set=runner.ORDERED_CANDIDATE_SET,
        target_panels=context.target_panels_by_stack[stack_id],
    )
    result = runner.run_stack_process(contract, backend, clock=clock)
    _require(
        dict(backend.model_action_counts) == EXPECTED_WORKER_MODEL_ACTION_COUNTS,
        "worker model action count drift",
    )
    result_writer(result_path, result)
    return result


@dataclass(frozen=True)
class WorkerLaunch:
    process_index: int
    process_id: str
    stack_id: str
    replicate_id: str
    project_root: Path
    manifest_path: Path
    manifest_sha256: str
    output_root: Path
    result_path: Path


CommandBuilder = Callable[[WorkerLaunch], Sequence[str]]
SubprocessRunner = Callable[..., Any]


def _default_worker_command_builder(launch: WorkerLaunch) -> Sequence[str]:
    workspace = launch.project_root.parent
    expected_python = (workspace / ".mvp-venv" / "Scripts" / "python.exe").resolve(
        strict=True
    )
    observed_python = Path(sys.executable).resolve(strict=True)
    _require(observed_python == expected_python, "coordinate Python is not the exact .mvp-venv executable")
    model_dir = (
        workspace / "models" / "SmolLM2-360M-Instruct-a10cc151"
    ).resolve(strict=True)
    _require(model_dir.is_dir() and not model_dir.is_symlink(), "fixed local model directory invalid")
    runner_path = Path(__file__).resolve(strict=True)
    runner_argument = runner_path.relative_to(workspace).as_posix()
    project_argument = launch.project_root.relative_to(workspace).as_posix()
    manifest_argument = launch.manifest_path.relative_to(workspace).as_posix()
    model_argument = model_dir.relative_to(workspace).as_posix()
    return [
        str(expected_python),
        "-I",
        "-B",
        runner_argument,
        "worker",
        "--project-root",
        project_argument,
        "--manifest",
        manifest_argument,
        "--manifest-sha256",
        launch.manifest_sha256,
        "--process-id",
        launch.process_id,
        "--model-dir",
        model_argument,
    ]


def _read_canonical_process(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise R13LocalBatchError(f"worker result is not UTF-8: {path.name}") from error
    value = runner.strict_json_loads(text)
    _require(raw == runner.strict_json_dumps(value).encode("utf-8"), f"non-canonical worker result: {path.name}")
    runner.validate_runner_result(value)
    return value


def _bundle_event_ledger(
    process_ids: Sequence[str], process_results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for process_id, process in zip(process_ids, process_results):
        for execution in process["source_update_executions"]:
            ledger.append(
                {
                    "execution_event_id": execution["execution_event_id"],
                    "process_id": process_id,
                    "execution_key": execution["execution_key"],
                    "source_rule_identity": execution["source_rule_identity"],
                    "update_parameter_hash": execution["update_parameter_hash"],
                }
            )
    _require(len(ledger) == 40, "batch source-update ledger count drift")
    return ledger


def coordinate(
    *,
    project_root: Path,
    manifest_path: Path,
    expected_manifest_sha256: str,
    command_builder: CommandBuilder | None = None,
    subprocess_runner: SubprocessRunner = subprocess.run,
    progress_writer: Callable[[str], None] = print,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    manifest_reader: Callable[[Path], dict[str, Any]] = local_manifest.read_manifest,
    manifest_verifier: Callable[..., None] = local_manifest.verify_manifest_against_filesystem,
    bundle_writer: Callable[[Path, Mapping[str, Any]], None] = runner.write_result_strict,
) -> Mapping[str, Any]:
    """Launch eight fresh OS workers serially, then assemble one unclassified bundle."""

    effective_command_builder = command_builder or _default_worker_command_builder
    _require(callable(effective_command_builder), "coordinate command_builder is not callable")
    project = _project_root(project_root)
    manifest_file = manifest_path.resolve(strict=True)
    manifest = _load_manifest(
        project,
        manifest_file,
        expected_manifest_sha256,
        require_output_root_absent=True,
        manifest_reader=manifest_reader,
        manifest_verifier=manifest_verifier,
    )
    process_ids = tuple(manifest["ordered_process_ids"])
    _require(process_ids == tuple(local_manifest.ORDERED_PROCESS_IDS), "coordinate process order drift")
    output_root = _output_root(project, manifest)
    _require(not output_root.exists(), "BLOCKED_OUTPUT_ROOT_ALREADY_EXISTS")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    _require(not output_root.parent.is_symlink(), "output parent is a link")
    try:
        os.mkdir(output_root, 0o700)
    except FileExistsError as error:
        raise R13LocalBatchError("BLOCKED_OUTPUT_ROOT_ALREADY_EXISTS") from error

    launches: list[WorkerLaunch] = []
    for index, process_id in enumerate(process_ids, 1):
        stack_id, replicate_id = process_id.split("|")
        result_path = output_root / worker_result_filename(index, stack_id, replicate_id)
        launches.append(
            WorkerLaunch(
                process_index=index,
                process_id=process_id,
                stack_id=stack_id,
                replicate_id=replicate_id,
                project_root=project,
                manifest_path=manifest_file,
                manifest_sha256=expected_manifest_sha256,
                output_root=output_root,
                result_path=result_path,
            )
        )

    for launch in launches:
        _require(not launch.result_path.exists(), "prefrozen worker result path already exists")
        command = effective_command_builder(launch)
        _require(
            isinstance(command, (list, tuple))
            and bool(command)
            and all(isinstance(item, str) and item for item in command),
            "command_builder returned an invalid argv",
        )
        progress_writer(
            f"R13_LOCAL_BATCH {launch.process_index}/8 START {launch.process_id}"
        )
        completed = subprocess_runner(
            list(command),
            cwd=str(project.parent),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _require(type(completed.returncode) is int, "worker return code is invalid")
        _require(
            completed.returncode == 0,
            f"worker failed closed: {launch.process_id}; returncode={completed.returncode}",
        )
        _require(
            launch.result_path.is_file() and not launch.result_path.is_symlink(),
            f"worker result missing: {launch.process_id}",
        )
        progress_writer(
            f"R13_LOCAL_BATCH {launch.process_index}/8 COMPLETE {launch.process_id}"
        )

    expected_names = {launch.result_path.name for launch in launches}
    observed_children = {child.name for child in output_root.iterdir()}
    _require(observed_children == expected_names, "unexpected output appeared during sealed batch")

    process_results = [_read_canonical_process(launch.result_path) for launch in launches]
    collection = runner.validate_process_collection(
        process_results, variant=local_manifest.SELECTED_VARIANT
    )
    _require(collection["bundle_run_id"] == manifest["run_id"], "collection run id drift")
    _require(collection["counts"]["os_processes"] == 8, "collection process count drift")
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "status": BUNDLE_STATUS,
        "run_id": manifest["run_id"],
        "created_at_utc": runner.format_utc_z(clock()),
        "selected_variant": local_manifest.SELECTED_VARIANT,
        "evidence_boundary": BUNDLE_EVIDENCE_BOUNDARY,
        "scientific_evidence": False,
        "formal_experiment": False,
        "formal_confirmatory": False,
        "sampled_rlvr": False,
        "same_empirical_or_policy_fpr": False,
        "model_execution_performed": True,
        "intermediate_scientific_results_released": False,
        "manifest_sha256": expected_manifest_sha256,
        "bindings": dict(process_results[0]["bindings"]),
        "ordered_process_ids": list(process_ids),
        "counts": dict(manifest["process_plan"]["counts"]),
        "model_action_counts": dict(EXPECTED_BATCH_MODEL_ACTION_COUNTS),
        "events": _bundle_event_ledger(process_ids, process_results),
        "process_results": process_results,
    }
    _require(set(bundle) == set(BUNDLE_KEYS), "local result bundle key drift")
    _require(
        "classification" not in json.dumps(bundle, ensure_ascii=True).lower(),
        "classification leaked into pre-validation bundle",
    )
    bundle_path = output_root / BUNDLE_FILENAME
    _require(not bundle_path.exists(), "local result bundle already exists")
    bundle_writer(bundle_path, bundle)
    return bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode")
    canary = subparsers.add_parser("technical-canary")
    canary.add_argument("--project-root", type=Path, required=True)
    canary.add_argument("--model-dir", type=Path, required=True)
    canary.add_argument("--receipt", type=Path, required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--project-root", type=Path, required=True)
    worker.add_argument("--manifest", type=Path, required=True)
    worker.add_argument("--manifest-sha256", required=True)
    worker.add_argument("--process-id", required=True)
    worker.add_argument("--model-dir", type=Path, required=True)
    coordinate_parser = subparsers.add_parser("coordinate")
    coordinate_parser.add_argument("--manifest", type=Path, required=True)
    return parser


def _require_cli_isolation() -> None:
    _require(sys.flags.isolated == 1, "CLI requires Python isolated mode (-I)")
    _require(
        sys.flags.dont_write_bytecode == 1,
        "CLI requires bytecode writes disabled (-B or PYTHONDONTWRITEBYTECODE=1)",
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.mode is None:
        parser.print_help()
        return 0
    if args.mode == "technical-canary":
        _require_cli_isolation()
        run_technical_canary(
            project_root=args.project_root,
            model_dir=args.model_dir,
            receipt_path=args.receipt,
        )
        return 0
    if args.mode == "worker":
        _require_cli_isolation()
        run_worker(
            project_root=args.project_root,
            manifest_path=args.manifest,
            expected_manifest_sha256=args.manifest_sha256,
            process_id=args.process_id,
            model_dir=args.model_dir,
        )
        return 0
    _require_cli_isolation()
    manifest_path = args.manifest.resolve(strict=True)
    coordinate(
        project_root=_PROJECT_ROOT,
        manifest_path=manifest_path,
        expected_manifest_sha256=sha256_file(manifest_path),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (R13LocalBatchError, OSError, ValueError) as error:
        print(f"R13_LOCAL_BATCH_BLOCKED: {error}", file=sys.stderr)
        raise SystemExit(2)


__all__ = [
    "BUNDLE_FILENAME",
    "BUNDLE_KEYS",
    "EXPECTED_BATCH_MODEL_ACTION_COUNTS",
    "EXPECTED_WORKER_MODEL_ACTION_COUNTS",
    "R13LocalBatchError",
    "WorkerLaunch",
    "WorkerStaticContext",
    "build_technical_canary_contract",
    "build_worker_static_context",
    "coordinate",
    "main",
    "run_technical_canary",
    "run_worker",
    "worker_result_filename",
]
