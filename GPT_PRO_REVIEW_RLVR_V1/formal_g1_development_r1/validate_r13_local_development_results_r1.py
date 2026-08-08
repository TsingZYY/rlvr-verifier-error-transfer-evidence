"""Model-free validator for one sealed local-development R13 A/B batch.

This wrapper deliberately relaxes only production-custody requirements.  It
does not relax the frozen scientific cells, raw trace reconstruction, A/B
equality, target-read immutability, relative gates, or the separately frozen
absolute-effect gate.  The output is local development evidence only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from formal_g1_development_r1 import r13_absolute_effect_gate_addendum_r1 as absolute_gate
from formal_g1_development_r1 import validate_r13_target_alignment_results_r1 as core
from mvp_same_source_v1 import r13_local_development_manifest as local_manifest


SCHEMA_VERSION = "r13-local-development-result-bundle-r1"
STATUS = "SEALED_EIGHT_PROCESS_LOCAL_DEVELOPMENT_BATCH_COMPLETE_PRE_VALIDATION"
REPORT_SCHEMA = "r13-local-development-independent-validation-report-r1"
EVIDENCE_LABEL = "LOCAL_DEVELOPMENT_ONLY_NONCONFIRMATORY"
SELECTED_VARIANT = "REPRODUCIBILITY_8_PROCESS_AB"

BUNDLE_KEYS = {
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

MODEL_ACTION_KEYS = {
    "tokenizer_loads",
    "model_weight_loads",
    "model_forward_calls",
    "backward_calls",
    "manual_parameter_steps",
}

EXPECTED_MODEL_ACTION_COUNTS = {
    "tokenizer_loads": 8,
    "model_weight_loads": 8,
    "model_forward_calls": 1232,
    "backward_calls": 560,
    "manual_parameter_steps": 40,
}

EXECUTION_SEMANTICS_SCHEMA = (
    "r13-local-development-execution-semantics-addendum-r1"
)

JOINT_PASS_LABEL = (
    "R13_LOCAL_DEVELOPMENT_RELATIVE_SWITCH_PLUS_ABSOLUTE_ALIGNED_"
    "WRONG_VS_GOLD_AMPLIFICATION_PASS_ONLY"
)


class R13LocalValidationError(RuntimeError):
    """The local batch is incomplete, drifted, or scientifically invalid."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R13LocalValidationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json(path: Path) -> dict[str, Any]:
    value = core.strict_json_loads(path.read_bytes(), str(path))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _mean(values: Iterable[float], label: str) -> float:
    rows = [float(value) for value in values]
    require(bool(rows) and all(math.isfinite(value) for value in rows), f"{label}: non-finite or empty")
    return sum(rows) / len(rows)


def _exact_positive_int(value: Any, label: str) -> int:
    require(type(value) is int and value > 0, f"{label}: expected positive integer")
    return value


def derive_model_action_counts(
    process_results: Any,
    execution_semantics: Mapping[str, Any],
) -> dict[str, int]:
    """Derive the frozen action schedule from validated process-result shapes.

    This is deliberately called only after every process has passed the official
    core validator.  It closes the local wrapper's schedule arithmetic without
    pretending that a result JSON file is independent hardware telemetry.
    """

    require(
        isinstance(execution_semantics, Mapping)
        and execution_semantics.get("schema_version") == EXECUTION_SEMANTICS_SCHEMA,
        "execution semantics schema drift",
    )
    scope = execution_semantics.get("selected_execution_scope")
    accumulation = execution_semantics.get("gradient_accumulation_resolution")
    require(isinstance(scope, Mapping), "execution semantics scope missing")
    require(isinstance(accumulation, Mapping), "gradient accumulation semantics missing")
    require(
        scope.get("variant") == SELECTED_VARIANT
        and scope.get("technical_replicates") == ["A", "B"]
        and scope.get("os_processes") == 8
        and scope.get("technical_update_executions") == 40
        and scope.get("total_target_identity_metric_cells") == 576,
        "execution semantics eight-process A/B scope drift",
    )

    source_rows = _exact_positive_int(accumulation.get("source_rows"), "source rows")
    backward_calls_per_update = _exact_positive_int(
        accumulation.get("backward_calls"), "backward calls per update"
    )
    manual_steps_per_update = _exact_positive_int(
        accumulation.get("manual_parameter_steps"), "manual steps per update"
    )
    require(
        source_rows == 14
        and accumulation.get("candidate_panel_size") == 7
        and backward_calls_per_update == 14
        and accumulation.get("logical_update_steps") == 1
        and manual_steps_per_update == 1
        and accumulation.get("adaptive_microbatching") is False,
        "gradient accumulation semantics drift",
    )

    require(isinstance(process_results, list), "process results must be a list")
    process_count = len(process_results)
    source_update_count = 0
    target_readout_count = 0
    target_row_forward_count = 0
    for process_index, process in enumerate(process_results):
        label = f"process_results[{process_index}]"
        require(isinstance(process, Mapping), f"{label}: expected object")
        pre_readouts = process.get("pre_target_readouts")
        executions = process.get("source_update_executions")
        require(isinstance(pre_readouts, list), f"{label}: pre readouts missing")
        require(isinstance(executions, list), f"{label}: source executions missing")
        source_update_count += len(executions)
        readouts = list(pre_readouts)
        for execution_index, execution in enumerate(executions):
            require(
                isinstance(execution, Mapping)
                and isinstance(execution.get("readouts"), list),
                f"{label}.source_update_executions[{execution_index}]: readouts missing",
            )
            readouts.extend(execution["readouts"])
        target_readout_count += len(readouts)
        for readout_index, readout in enumerate(readouts):
            require(
                isinstance(readout, Mapping)
                and isinstance(readout.get("row_traces"), list),
                f"{label}.readouts[{readout_index}]: row traces missing",
            )
            target_row_forward_count += len(readout["row_traces"])

    require(process_count == scope["os_processes"], "derived OS-process count drift")
    require(
        source_update_count == scope["technical_update_executions"],
        "derived technical-update count drift",
    )
    target_identity_cells = target_readout_count * len(core.TARGET_OFFSETS)
    require(
        target_identity_cells == scope["total_target_identity_metric_cells"],
        "derived target-identity metric-cell count drift",
    )
    return {
        "tokenizer_loads": process_count,
        "model_weight_loads": process_count,
        "model_forward_calls": (
            source_update_count * source_rows + target_row_forward_count
        ),
        "backward_calls": source_update_count * backward_calls_per_update,
        "manual_parameter_steps": source_update_count * manual_steps_per_update,
    }


def _raw_e_cell_count(
    derived: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> int:
    count = 0
    for stack in core.STACKS:
        require(set(derived[stack]) == {"A", "B"}, f"{stack}: A/B coverage drift")
        for replicate in ("A", "B"):
            raw_e = derived[stack][replicate]["E"]
            absolute_gate.validate_raw_e_matrix(raw_e)
            count += sum(
                len(raw_e[arm][source_identity])
                for arm in core.ARMS
                for source_identity in range(1, 6)
            )
    return count


def expected_bindings(
    *,
    project_root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, str]:
    artifacts = manifest["artifacts"]
    human_template = (
        project_root
        / "formal_g1_development_r1"
        / "r13_assets_r1"
        / "R13_MATCHED_TARGET_PANEL_HUMAN_REVIEW_RECEIPT_TEMPLATE_R1.json"
    )
    require(human_template.is_file(), "local human-review template binding missing")
    return {
        "protocol_sha256": artifacts["protocol"]["sha256"],
        "panel_bundle_sha256": artifacts["matched_panel_manifest"]["sha256"],
        "allowlist_receipt_sha256": artifacts["matched_panel_allowlist"]["sha256"],
        "human_review_receipt_sha256": sha256_file(human_template),
        "authorization_sha256": sha256_file(manifest_path),
        "runner_sha256": artifacts["runner"]["sha256"],
        "validator_sha256": artifacts["result_validator"]["sha256"],
        "model_inventory_sha256": artifacts["model_inventory"]["sha256"],
        "source_assets_sha256": artifacts["source_bundle"]["sha256"],
    }


def _absolute_report(
    derived: Mapping[str, Mapping[str, Mapping[str, Any]]],
    protocol: Mapping[str, Any],
    *,
    addendum_sha256: str,
    expected_raw_e_cells: int,
) -> dict[str, Any]:
    raw_e_cells = _raw_e_cell_count(derived)
    require(
        raw_e_cells == expected_raw_e_cells == 480,
        "absolute-effect raw-E cell count drift",
    )
    by_stack: dict[str, Any] = {}
    for stack in core.STACKS:
        by_replicate = {
            replicate: derived[stack][replicate]["E"]
            for replicate in ("A", "B")
        }
        averaged_e = absolute_gate.average_technical_raw_e(by_replicate)
        q_by_arm = {
            arm: core.q_alignment(protocol, stack, arm)
            for arm in core.ARMS
        }
        anchors = absolute_gate.derive_absolute_anchors(averaged_e, q_by_arm)
        averaged_f = {
            r: _mean(
                (derived[stack][replicate]["F"][r] for replicate in ("A", "B")),
                f"{stack}.F.r{r}",
            )
            for r in range(1, 6)
        }
        relative_mean = _mean(averaged_f.values(), f"{stack}.mean_F")
        aligned_mean = _mean(
            anchors["A_aligned_by_source_identity"].values(),
            f"{stack}.mean_A_aligned",
        )
        original_mean = _mean(
            anchors["A_original_by_source_identity"].values(),
            f"{stack}.mean_A_original",
        )
        by_stack[stack] = {
            "mean_relative_interface_F": relative_mean,
            "mean_A_original": original_mean,
            "mean_A_aligned": aligned_mean,
            "mean_D_absolute": aligned_mean - original_mean,
            "A_original_by_source_identity": {
                f"Z7_PLUS{r}": anchors["A_original_by_source_identity"][r]
                for r in range(1, 6)
            },
            "A_aligned_by_source_identity": {
                f"Z7_PLUS{r}": anchors["A_aligned_by_source_identity"][r]
                for r in range(1, 6)
            },
            "D_absolute_by_source_identity": {
                f"Z7_PLUS{r}": anchors["D_absolute_by_source_identity"][r]
                for r in range(1, 6)
            },
            "classification": absolute_gate.classify_stack(relative_mean, aligned_mean),
            "absolute_directional_gate": (
                "PASS" if aligned_mean > absolute_gate.DIRECTIONAL_EPSILON else "FAIL"
            ),
        }

    task_pairs = {
        pair: [stack for stack in core.STACKS if stack.startswith(pair)]
        for pair in ("TP1", "TP2")
    }
    by_task_pair = {
        pair: {
            key: _mean((by_stack[stack][key] for stack in stacks), f"{pair}.{key}")
            for key in ("mean_A_original", "mean_A_aligned", "mean_D_absolute")
        }
        for pair, stacks in task_pairs.items()
    }
    overall = {
        key: _mean((by_stack[stack][key] for stack in core.STACKS), f"overall.{key}")
        for key in ("mean_A_original", "mean_A_aligned", "mean_D_absolute")
    }
    all_absolute = all(
        by_stack[stack]["absolute_directional_gate"] == "PASS"
        for stack in core.STACKS
    )
    return {
        "addendum_sha256": addendum_sha256,
        "raw_E_cells_before_technical_averaging": raw_e_cells,
        "technical_replicates": 2,
        "a_b_checked_before_technical_averaging": True,
        "all_four_stack_absolute_gate_pass": all_absolute,
        "by_stack": by_stack,
        "by_task_pair": by_task_pair,
        "descriptive_overall": overall,
    }


def validate_local_bundle(
    bundle: Mapping[str, Any],
    *,
    project_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    project = project_root.resolve()
    manifest_file = manifest_path.resolve()
    manifest = local_manifest.read_manifest(manifest_file)
    local_manifest.validate_manifest(manifest)
    local_manifest.verify_manifest_against_filesystem(
        manifest,
        project_root=project,
        require_output_root_absent=False,
    )

    require(isinstance(bundle, Mapping) and set(bundle) == BUNDLE_KEYS, "local bundle schema drift")
    core.assert_finite(bundle, "local bundle")
    require(bundle["schema_version"] == SCHEMA_VERSION, "local bundle version drift")
    require(bundle["status"] == STATUS, "local bundle status drift")
    require(bundle["run_id"] == manifest["run_id"], "local bundle run id mismatch")
    core.valid_uuid4(bundle["run_id"], "local bundle run id")
    created_at = core.parse_utc(bundle["created_at_utc"], "local bundle creation time")
    frozen_at = core.parse_utc(manifest["frozen_at_utc"], "local manifest freeze time")
    require(frozen_at <= created_at, "local bundle predates its manifest")
    require(bundle["selected_variant"] == SELECTED_VARIANT, "local bundle variant drift")
    require(bundle["evidence_boundary"] == EVIDENCE_LABEL, "local evidence boundary drift")
    require(bundle["scientific_evidence"] is False, "local bundle claims scientific evidence")
    require(bundle["formal_experiment"] is False, "local bundle claims formal experiment")
    require(bundle["formal_confirmatory"] is False, "local bundle claims confirmatory status")
    require(bundle["sampled_rlvr"] is False, "local bundle claims sampled RLVR")
    require(bundle["same_empirical_or_policy_fpr"] is False, "local bundle claims empirical FPR")
    require(bundle["model_execution_performed"] is True, "local bundle hides model execution")
    require(
        bundle["intermediate_scientific_results_released"] is False,
        "sealed batch released intermediate results",
    )
    require(bundle["manifest_sha256"] == sha256_file(manifest_file), "manifest hash mismatch")

    bindings = expected_bindings(
        project_root=project,
        manifest_path=manifest_file,
        manifest=manifest,
    )
    core.validate_bindings(bundle["bindings"], bindings, "local bundle.bindings")
    ordered_process_ids = core.expected_process_ids(SELECTED_VARIANT)
    require(bundle["ordered_process_ids"] == list(ordered_process_ids), "process order drift")
    require(bundle["counts"] == core.expected_total_counts(SELECTED_VARIANT), "total count drift")
    require(
        isinstance(bundle["model_action_counts"], Mapping)
        and set(bundle["model_action_counts"]) == MODEL_ACTION_KEYS
        and all(
            type(bundle["model_action_counts"][key]) is int
            and bundle["model_action_counts"][key] >= 0
            for key in MODEL_ACTION_KEYS
        ),
        "model action count schema drift",
    )

    protocol_path = project / manifest["artifacts"]["protocol"]["path"]
    protocol = strict_json(protocol_path)
    core.validate_protocol(protocol, synthetic_test_mode=False)
    semantics_record = manifest["artifacts"]["local_execution_semantics_addendum"]
    semantics_path = project / semantics_record["path"]
    execution_semantics = strict_json(semantics_path)
    require(
        sha256_file(semantics_path) == semantics_record["sha256"],
        "execution semantics hash mismatch",
    )
    absolute_record = manifest["artifacts"]["absolute_effect_addendum"]
    absolute_path = project / absolute_record["path"]
    absolute_addendum = strict_json(absolute_path)
    require(
        sha256_file(absolute_path) == absolute_record["sha256"],
        "absolute-effect addendum hash mismatch",
    )
    absolute_gate.validate_addendum(absolute_addendum)
    absolute_count_contract = absolute_addendum["count_contract"][SELECTED_VARIANT]
    allowlist_path = project / manifest["artifacts"]["matched_panel_allowlist"]["path"]
    allowlist = strict_json(allowlist_path)
    core.validate_allowlist_receipt(
        allowlist,
        expected_protocol_sha256=bindings["protocol_sha256"],
        expected_panel_bundle_sha256=bindings["panel_bundle_sha256"],
        synthetic_test_mode=False,
    )
    asset_root = project / "formal_g1_development_r1" / "r13_assets_r1"
    loaded = core.load_bound_matched_panel_bundle(
        asset_root,
        expected_manifest_sha256=bindings["panel_bundle_sha256"],
        expected_allowlist_receipt_sha256=bindings["allowlist_receipt_sha256"],
    )
    require(loaded.allowlist_receipt == allowlist, "loaded allowlist receipt drift")
    panels, panel_hashes = core.validate_matched_panels(loaded.records, protocol)

    process_results = bundle["process_results"]
    require(
        isinstance(process_results, list) and len(process_results) == len(ordered_process_ids),
        "process result coverage drift",
    )
    derived: dict[str, dict[str, dict[str, Any]]] = {stack: {} for stack in core.STACKS}
    global_event_ids: set[str] = set()
    global_process_run_ids: set[str] = set()
    flattened_events: list[dict[str, Any]] = []
    authorization_window = (frozen_at, created_at + timedelta(seconds=1))
    for process_id, process in zip(ordered_process_ids, process_results):
        item = core.validate_process(
            process,
            expected_process_id=process_id,
            expected_bundle_run_id=bundle["run_id"],
            expected_bindings=bindings,
            protocol=protocol,
            panels=panels,
            panel_hashes=panel_hashes,
            authorization_window=authorization_window,
            result_created_at=created_at,
            global_event_ids=global_event_ids,
            global_process_run_ids=global_process_run_ids,
        )
        derived[item["stack_id"]][item["replicate_id"]] = item
        flattened_events.extend(item["events"])

    require(
        len(
            {
                item["initial_parameter_hash"]
                for by_replicate in derived.values()
                for item in by_replicate.values()
            }
        )
        == 1,
        "global initial trainable snapshot hash mismatch",
    )
    require(bundle["events"] == flattened_events, "top-level execution ledger mismatch")
    require(len(global_process_run_ids) == 8, "process run-id cardinality drift")
    require(len(global_event_ids) == 40, "source-update event cardinality drift")

    derived_action_counts = derive_model_action_counts(
        process_results,
        execution_semantics,
    )
    require(
        derived_action_counts == EXPECTED_MODEL_ACTION_COUNTS,
        "shape-derived model action contract drift",
    )
    require(
        dict(bundle["model_action_counts"]) == derived_action_counts,
        "reported model action counts do not match validated process shapes",
    )

    relative = core.summarize_results(
        derived,
        selected_variant=SELECTED_VARIANT,
        synthetic_fixture=False,
        model_execution_performed=True,
    )
    absolute = _absolute_report(
        derived,
        protocol,
        addendum_sha256=absolute_record["sha256"],
        expected_raw_e_cells=absolute_count_contract[
            "raw_E_cells_before_technical_averaging"
        ],
    )
    joint_pass = (
        relative["decision"] == "R13_DEVELOPMENT_SURFACE_ALIGNMENT_SWITCH_PASS_ONLY"
        and absolute["all_four_stack_absolute_gate_pass"]
    )
    if joint_pass:
        decision = JOINT_PASS_LABEL
    elif relative["decision"] != "R13_DEVELOPMENT_SURFACE_ALIGNMENT_SWITCH_PASS_ONLY":
        decision = relative["decision"]
    elif any(
        item["classification"] == "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION"
        for item in absolute["by_stack"].values()
    ):
        decision = "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION"
    else:
        decision = "ABSOLUTE_ALIGNED_WRONG_VS_GOLD_DIRECTIONAL_GATE_FAILURE"

    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "COMPLETE_VALIDATED_LOCAL_DEVELOPMENT_BATCH",
        "decision": decision,
        "technical_pass": True,
        "joint_relative_and_absolute_gate_pass": joint_pass,
        "selected_variant": SELECTED_VARIANT,
        "model_execution_performed": True,
        "shape_derived_expected_model_action_counts": derived_action_counts,
        "reported_model_action_counts_match_shape": True,
        "model_action_counts_are_external_telemetry": False,
        "relative_interface_report": relative,
        "absolute_effect_report": absolute,
        "development_evidence": True,
        "scientific_evidence": False,
        "evidence_boundary": EVIDENCE_LABEL,
        "formal_confirmatory": False,
        "inferential_p_value": None,
        "sampled_rlvr": False,
        "same_empirical_or_policy_fpr": False,
        "human_review_completed": False,
        "paper_custody": False,
        "production_custody": False,
        "automatic_progression": False,
        "maximum_positive_claim": (
            "In four frozen outcome-aware M0 affine development stacks, one fixed "
            "source-only update changed target wrong-vs-gold log-odds differently "
            "under two frozen target codebook interfaces and passed the aligned "
            "absolute-direction gate."
        ),
        "forbidden_claims": [
            "shared semantic identity caused transfer",
            "same empirical or policy-weighted false-positive rate",
            "sampled or multi-step RLVR transfer",
            "population task generalization",
            "formal or confirmatory inference",
        ],
    }
    core.assert_finite(report, "local validation report")
    return report


def atomic_write_new(path: Path, value: Mapping[str, Any]) -> None:
    require(not path.exists(), f"refusing to overwrite validation report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = core.canonical_json_bytes(value) + b"\n"
    with path.open("xb") as handle:
        handle.write(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    bundle = strict_json(args.bundle.resolve())
    report = validate_local_bundle(
        bundle,
        project_root=args.project_root.resolve(),
        manifest_path=args.manifest.resolve(),
    )
    atomic_write_new(args.output.resolve(), report)
    print(json.dumps({"status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
