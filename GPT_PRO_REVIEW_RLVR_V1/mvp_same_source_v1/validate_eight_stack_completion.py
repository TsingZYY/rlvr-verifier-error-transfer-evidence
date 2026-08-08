"""Fail-closed CPU-only validator for the exact eight-stack completion set."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Sequence

import mvp_static_contract as contract


SHA256_RE = re.compile(r"[0-9a-f]{64}")
RESULT_ANCHOR_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "mapping_stack_id",
    "master_inclusion_contract_sha256",
    "execution_manifest_sha256",
    "result_a_sha256",
    "result_b_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "result_a_replicate_id",
    "result_b_replicate_id",
    "result_a_run_nonce",
    "result_b_run_nonce",
    "invocation_start_receipt_a_sha256",
    "invocation_start_receipt_b_sha256",
    "custody_requirement",
}
VALIDATION_REPORT_KEYS = {
    "schema_version",
    "validation_status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "mapping_stack_id",
    "result_anchor_sha256",
    "substantive_replicates_identical",
    "errors",
    "stack_summary",
    "norm_evidence_boundary",
    "evidence_label",
}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--result-anchor", type=Path, action="append", required=True)
    parser.add_argument(
        "--expected-result-anchor-sha256", action="append", required=True
    )
    parser.add_argument("--validation-report", type=Path, action="append", required=True)
    parser.add_argument(
        "--expected-validation-report-sha256", action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _distinct_files(paths: list[Path], label: str) -> list[str]:
    errors: list[str] = []
    if len({path.resolve() for path in paths}) != len(paths):
        errors.append(f"duplicate {label} path")
    for left_index, left in enumerate(paths):
        for right in paths[left_index + 1 :]:
            try:
                if os.path.samefile(left, right):
                    errors.append(f"duplicate {label} filesystem object")
            except (FileNotFoundError, OSError):
                errors.append(f"{label} filesystem identity could not be verified")
    return errors


def _finite_float(value: Any) -> float | None:
    """Return a finite float or None without allowing bool/coercion overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _input_failure_report(master_sha256: str, error: BaseException) -> dict[str, Any]:
    """Create canonical, bounded FAIL output for pre-validation input errors."""
    return {
        "schema_version": "same-source-eight-stack-completion-validation-r5",
        "validation_status": "FAIL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.COMPLETION_EVIDENCE_BOUNDARY),
        "master_inclusion_contract_sha256": master_sha256,
        "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
        "exact_stack_count": 8,
        "aggregate": None,
        "errors": [
            "completion input read/parse failure: " + type(error).__name__
        ],
    }


def _print_failure_stdout(error: BaseException, master_sha256: str = "0" * 64) -> None:
    report = _input_failure_report(master_sha256, error)
    print(contract.canonical_json_bytes(report).decode("utf-8"), end="")


def _cwd_bound_output_name(output: Path) -> str:
    """Return a safe leaf name whose parent is exactly the process cwd.

    The validator deliberately does not traverse an output parent pathname.
    Callers that need another output directory must start the validator with
    that directory as its cwd and pass a leaf filename (or the exact absolute
    spelling of cwd plus that leaf).  Relative creation is then anchored to the
    process current-directory object, so swapping a previously checked ancestor
    cannot redirect the final open.
    """
    if output.name in {"", ".", ".."} or ".." in output.parts:
        raise contract.ContractError("output must be a single safe leaf in cwd")
    cwd = Path.cwd()
    if output.is_absolute():
        supplied_parent = Path(os.path.abspath(output.parent))
        if os.path.normcase(str(supplied_parent)) != os.path.normcase(str(cwd)):
            raise contract.ContractError("output parent must be the exact cwd")
    elif output.parent != Path("."):
        raise contract.ContractError("nested relative output path is forbidden")
    if os.path.lexists(output.name):
        raise contract.ContractError("pre-existing output directory entry")
    return output.name


def validate_completion(
    *,
    master: dict[str, Any],
    master_sha256: str,
    expected_master_sha256: str,
    anchor_paths: list[Path],
    anchors: list[dict[str, Any]],
    anchor_sha256: list[str],
    expected_anchor_sha256: list[str],
    report_paths: list[Path],
    reports: list[dict[str, Any]],
    report_sha256: list[str],
    expected_report_sha256: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        contract.validate_master_inclusion_contract(master)
    except contract.ContractError as error:
        errors.append(f"master inclusion contract invalid: {error}")
    if master_sha256 != expected_master_sha256:
        errors.append("external expected master hash mismatch")
    for label, paths, objects, actual, expected in (
        ("result anchor", anchor_paths, anchors, anchor_sha256, expected_anchor_sha256),
        ("validation report", report_paths, reports, report_sha256, expected_report_sha256),
    ):
        if not all(len(values) == 8 for values in (paths, objects, actual, expected)):
            errors.append(f"{label} count must be exactly 8")
        errors.extend(_distinct_files(paths, label))
        if len(set(actual)) != len(actual):
            errors.append(f"duplicate {label} hash")
        for index, (actual_hash, expected_hash) in enumerate(zip(actual, expected), 1):
            if SHA256_RE.fullmatch(expected_hash) is None or actual_hash != expected_hash:
                errors.append(f"external expected {label} hash mismatch at position {index}")

    anchors_by_stack: dict[str, tuple[dict[str, Any], str]] = {}
    for anchor, anchor_hash in zip(anchors, anchor_sha256):
        stack = str(anchor.get("mapping_stack_id", ""))
        if stack in anchors_by_stack:
            errors.append(f"duplicate stack in result anchors: {stack}")
        anchors_by_stack[stack] = (anchor, anchor_hash)
        if set(anchor) != RESULT_ANCHOR_KEYS:
            errors.append(f"result anchor schema drift: {stack}")
        if anchor.get("schema_version") != "same-source-result-pair-anchor-r5":
            errors.append(f"old or unknown result anchor rejected: {stack}")
        if anchor.get("status") != "FROZEN_POST_RUN_PRE_VALIDATION":
            errors.append(f"result anchor status drift: {stack}")
        if anchor.get("evidence_boundary") != contract.EVIDENCE_BOUNDARY:
            errors.append(f"result anchor exact evidence-boundary labels required: {stack}")
        if anchor.get("scientific_evidence") is not False or anchor.get(
            "formal_experiment"
        ) is not False:
            errors.append(f"result anchor status washing: {stack}")
        if anchor.get("master_inclusion_contract_sha256") != master_sha256:
            errors.append(f"result anchor/master binding mismatch: {stack}")
        if anchor.get("execution_manifest_sha256") != master.get(
            "manifest_sha256_by_stack", {}
        ).get(stack):
            errors.append(f"result anchor/manifest binding mismatch: {stack}")
        for field in (
            "master_inclusion_contract_sha256",
            "execution_manifest_sha256",
            "result_a_sha256",
            "result_b_sha256",
            "authorization_receipt_sha256",
            "invocation_start_receipt_a_sha256",
            "invocation_start_receipt_b_sha256",
        ):
            value = anchor.get(field)
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                errors.append(f"malformed SHA-256 field in result anchor: {stack}/{field}")
        for field in ("authorization_id", "result_a_run_nonce", "result_b_run_nonce"):
            value = anchor.get(field)
            if not isinstance(value, str) or contract.UUID4_RE.fullmatch(value) is None:
                errors.append(f"malformed UUIDv4 field in result anchor: {stack}/{field}")
        if anchor.get("result_a_replicate_id") != "A" or anchor.get(
            "result_b_replicate_id"
        ) != "B":
            errors.append(f"result anchor replica identity mismatch: {stack}")
        if anchor.get("result_a_run_nonce") == anchor.get("result_b_run_nonce"):
            errors.append(f"duplicate replica nonce in result anchor: {stack}")
        if anchor.get("result_a_sha256") == anchor.get("result_b_sha256"):
            errors.append(f"duplicate replica result hash in result anchor: {stack}")
        if anchor.get("invocation_start_receipt_a_sha256") == anchor.get(
            "invocation_start_receipt_b_sha256"
        ):
            errors.append(f"duplicate invocation receipt hash in result anchor: {stack}")
        if anchor.get("custody_requirement") != (
            "STORE_OUTSIDE_BOTH_RESULT_OUTPUT_DIRECTORIES_AND_RECORD_SHA256_EXTERNALLY"
        ):
            errors.append(f"result anchor custody contract drift: {stack}")

    reports_by_stack: dict[str, dict[str, Any]] = {}
    for report in reports:
        stack = str(report.get("mapping_stack_id", ""))
        if stack in reports_by_stack:
            errors.append(f"duplicate stack in validation reports: {stack}")
        reports_by_stack[stack] = report
        if set(report) != VALIDATION_REPORT_KEYS:
            errors.append(f"validation report schema drift: {stack}")
        if report.get("schema_version") != "same-source-diagnostic-mvp-validation-r5":
            errors.append(f"old R2 or unknown validation report rejected: {stack}")
        if report.get("validation_status") != "PASS":
            errors.append(f"validation report is not PASS: {stack}")
        if report.get("errors") != []:
            errors.append(f"validation report contains errors: {stack}")
        if report.get("evidence_boundary") != contract.EVIDENCE_BOUNDARY:
            errors.append(f"validation exact evidence-boundary labels required: {stack}")
        if report.get("scientific_evidence") is not False or report.get(
            "formal_experiment"
        ) is not False:
            errors.append(f"validation report status washing: {stack}")
        if report.get("substantive_replicates_identical") is not True:
            errors.append(f"validation report substantive equality failed: {stack}")
        if report.get("norm_evidence_boundary") != contract.NORM_EVIDENCE_BOUNDARY:
            errors.append(f"validation norm evidence boundary drift: {stack}")
        if report.get("evidence_label") != "CPU_STATIC_REPAIR_VALIDATOR_ONLY_R5":
            errors.append(f"validation evidence label drift: {stack}")
        anchor_pair = anchors_by_stack.get(stack)
        if anchor_pair is None or report.get("result_anchor_sha256") != anchor_pair[1]:
            errors.append(f"validation/result-anchor binding mismatch: {stack}")

    expected_stacks = set(contract.EXPECTED_STACK_IDS)
    if set(anchors_by_stack) != expected_stacks:
        errors.append("result anchor stack set is not exact 8/8")
    if set(reports_by_stack) != expected_stacks:
        errors.append("validation report stack set is not exact 8/8")
    all_result_hashes = [
        anchor.get(field)
        for anchor in anchors
        for field in ("result_a_sha256", "result_b_sha256")
    ]
    if len(all_result_hashes) != 16 or len(set(all_result_hashes)) != 16:
        errors.append("completion requires 16 distinct replica result hashes")
    all_invocation_hashes = [
        anchor.get(field)
        for anchor in anchors
        for field in (
            "invocation_start_receipt_a_sha256",
            "invocation_start_receipt_b_sha256",
        )
    ]
    if len(all_invocation_hashes) != 16 or len(set(all_invocation_hashes)) != 16:
        errors.append("completion requires 16 distinct invocation receipt hashes")
    if len({anchor.get("authorization_receipt_sha256") for anchor in anchors}) != 1:
        errors.append("completion authorization receipt hash is not shared")
    if len({anchor.get("authorization_id") for anchor in anchors}) != 1:
        errors.append("completion authorization_id is not shared")
    all_run_nonces = [
        anchor.get(field)
        for anchor in anchors
        for field in ("result_a_run_nonce", "result_b_run_nonce")
    ]
    if len(all_run_nonces) != 16 or len(set(all_run_nonces)) != 16:
        errors.append("completion requires 16 globally distinct UUIDv4 run nonces")

    summaries = [
        reports_by_stack[stack].get("stack_summary")
        for stack in contract.EXPECTED_STACK_IDS
        if stack in reports_by_stack
    ]
    numeric_means: list[float] = []
    summaries_valid = len(summaries) == 8
    if summaries_valid:
        for value in summaries:
            valid_shape = isinstance(value, dict) and set(value) == {
                "mean_diagonal_excess",
                "positive_diagonal_excess_count",
                "source_rule_count",
            }
            if not valid_shape:
                summaries_valid = False
                break
            mean = _finite_float(value["mean_diagonal_excess"])
            count = value["positive_diagonal_excess_count"]
            source_count = value["source_rule_count"]
            if (
                mean is None
                or type(count) is not int
                or not 0 <= count <= 5
                or type(source_count) is not int
                or source_count != 5
            ):
                summaries_valid = False
                break
            numeric_means.append(mean)
    if not summaries_valid:
        errors.append("eight-stack aggregate inputs are invalid")
    aggregate: dict[str, Any] | None = None
    if not errors and summaries_valid:
        try:
            # Divide before summing: the average of finite inputs must not
            # overflow merely because the naive intermediate sum does.
            aggregate_mean = sum(value / 8.0 for value in numeric_means)
            if not math.isfinite(aggregate_mean):
                raise ValueError("non-finite aggregate mean")
            aggregate = {
                "equal_mean_over_eight_stack_mean_diagonal_excess": aggregate_mean,
                "total_positive_diagonal_excess_count": sum(
                    value["positive_diagonal_excess_count"] for value in summaries
                ),
                "stack_count": 8,
                "source_rule_count_per_stack": 5,
            }
        except (OverflowError, TypeError, ValueError):
            errors.append("eight-stack aggregate numeric overflow or non-finite result")
            aggregate = None

    return {
        "schema_version": "same-source-eight-stack-completion-validation-r5",
        "validation_status": "PASS" if not errors else "FAIL",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.COMPLETION_EVIDENCE_BOUNDARY),
        "master_inclusion_contract_sha256": master_sha256,
        "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
        "exact_stack_count": 8,
        "aggregate": aggregate,
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output_name = _cwd_bound_output_name(args.output)
    except (OSError, ValueError, TypeError, contract.ContractError) as error:
        _print_failure_stdout(error)
        return 1
    master_sha256 = "0" * 64
    try:
        master_sha256 = contract.sha256_file(args.master_inclusion_contract)
        anchor_sha256 = [contract.sha256_file(path) for path in args.result_anchor]
        report_sha256 = [contract.sha256_file(path) for path in args.validation_report]
        master = contract.read_json(args.master_inclusion_contract)
        anchors = [contract.read_json(path) for path in args.result_anchor]
        reports = [contract.read_json(path) for path in args.validation_report]
        report = validate_completion(
            master=master,
            master_sha256=master_sha256,
            expected_master_sha256=args.expected_master_sha256,
            anchor_paths=args.result_anchor,
            anchors=anchors,
            anchor_sha256=anchor_sha256,
            expected_anchor_sha256=args.expected_result_anchor_sha256,
            report_paths=args.validation_report,
            reports=reports,
            report_sha256=report_sha256,
            expected_report_sha256=args.expected_validation_report_sha256,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        report = _input_failure_report(master_sha256, error)
    encoded_report = contract.canonical_json_bytes(report)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output_name, flags, 0o444)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded_report)
    except (OSError, ValueError, TypeError) as error:
        _print_failure_stdout(error, master_sha256)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["validation_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
