#!/usr/bin/env python3
"""Package the five CPU-only P4-R1 pre-review deliverables deterministically.

This script is model-free and standard-library-only.  It reads one validated
native build, creates three byte-reproducible ZIP_STORED bundles, copies the
frozen protocol bytes, and writes a final PRE_REVIEW preregistration that binds
the protocol and ZIP hashes.  It never changes its input build and refuses to
overwrite an existing deliverable.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
REAL_ASSETS_DIR = SCRIPT_PATH.parents[1]

PROTOCOL = "P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json"
SOURCE_ZIP = "REAL_SOURCE_STACK_BUNDLE_V1.zip"
VERIFIER_ZIP = "VERIFIER_G1_CPU_BUNDLE_V1.zip"
TARGET_ZIP = "TARGET_G2_REAL_BUNDLE_V1.zip"
PREREG = "RANDOMIZATION_MODEL_ENV_PREREG_V1.json"

DELIVERABLES = (PROTOCOL, SOURCE_ZIP, VERIFIER_ZIP, TARGET_ZIP, PREREG)
FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)
READ_ONLY_FILE_MODE = 0o100444

SOURCE_FILES = (
    "REAL_MAPPING_STACKS_V1.jsonl",
    "REAL_SOURCE_BUNDLES_V1.jsonl",
    "SOURCE_MACHINE_LABEL_MANIFEST_V1.jsonl",
)
VERIFIER_FILES = (
    "VERIFIER_REWARD_MANIFEST_V1.jsonl",
    "G1_OPPORTUNITY_AUDIT_V1.json",
)
TARGET_FILES = (
    "TARGET_CALIBRATION_REAL_V1.jsonl",
    "TARGET_AUDIT_REAL_V1.jsonl",
    "TARGET_CALIBRATION_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "TARGET_AUDIT_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
    "TARGET_AUDIT_SEAL_RECEIPT.json",
)
BOUND_ARTIFACT_FILES = (
    "REAL_MAPPING_STACKS_V1.jsonl",
    "REAL_SOURCE_BUNDLES_V1.jsonl",
    "TARGET_CALIBRATION_REAL_V1.jsonl",
    "TARGET_AUDIT_REAL_V1.jsonl",
    "VERIFIER_REWARD_MANIFEST_V1.jsonl",
    "SOURCE_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "TARGET_CALIBRATION_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "TARGET_AUDIT_MACHINE_LABEL_MANIFEST_V1.jsonl",
    PROTOCOL,
    "G1_OPPORTUNITY_AUDIT_V1.json",
    "TARGET_AUDIT_SEAL_RECEIPT.json",
    PREREG,
    "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
)

SOURCE_MANIFEST = "REAL_SOURCE_STACK_BUNDLE_MANIFEST_V1.json"
VERIFIER_MANIFEST = "VERIFIER_G1_CPU_BUNDLE_MANIFEST_V1.json"
TARGET_MANIFEST = "TARGET_G2_REAL_BUNDLE_MANIFEST_V1.json"
NATIVE_BUILD_MANIFEST = "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json"
NATIVE_PREREG_MEMBER = (
    "provenance/NATIVE_RANDOMIZATION_MODEL_ENV_PREREG_V1.json"
)
NATIVE_BUILD_MANIFEST_MEMBER = (
    "provenance/NATIVE_CONTROLLED_ASSET_BUILD_MANIFEST_V1.json"
)


class PackageError(RuntimeError):
    """Raised when deterministic packaging cannot proceed safely."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def parse_json_bytes(value: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageError(f"cannot read JSON {label}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PackageError(f"expected JSON object: {label}")
    return parsed


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise PackageError(f"cannot read JSON {path}: {exc}") from exc
    return parse_json_bytes(value, str(path))


def validate_jsonl_strict_bytes(value: bytes, label: str) -> None:
    lines = value.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise PackageError(f"JSONL must be non-empty and LF-terminated: {label}")
    for index, line in enumerate(lines, start=1):
        try:
            row = json.loads(
                line.decode("ascii"),
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            raise PackageError(
                f"invalid strict JSONL at {label}:{index}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise PackageError(f"JSONL row must be an object: {label}:{index}")
        if canonical_json_bytes(row) != line:
            raise PackageError(
                f"JSONL row is not canonical ASCII/LF: {label}:{index}"
            )


def validate_jsonl_strict(path: Path) -> None:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise PackageError(f"cannot read JSONL {path}: {exc}") from exc
    validate_jsonl_strict_bytes(value, str(path))


def require_file(path: Path) -> None:
    if not path.is_file():
        raise PackageError(f"required file is missing: {path}")


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def paths_overlap(first: Path, second: Path) -> bool:
    return path_is_within(first, second) or path_is_within(second, first)


def reject_link_or_reparse(path: Path) -> None:
    is_junction = getattr(os.path, "isjunction", lambda _: False)
    if path.is_symlink() or is_junction(path):
        raise PackageError(f"links and reparse-point junctions are forbidden: {path}")
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise PackageError(f"cannot stat input path {path}: {exc}") from exc
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise PackageError(f"non-regular input path is forbidden: {path}")


def capture_build_bytes(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise PackageError(f"native build root is not a directory: {root}")
    root = root.resolve()
    reject_link_or_reparse(root)
    snapshot: dict[str, bytes] = {}
    paths = sorted(
        root.rglob("*"),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    )
    for path in paths:
        reject_link_or_reparse(path)
        resolved = path.resolve()
        if not path_is_within(resolved, root):
            raise PackageError(f"input path resolves outside build root: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        require_safe_member_path(relative)
        if relative in snapshot:
            raise PackageError(f"duplicate build snapshot path: {relative}")
        value = path.read_bytes()
        reject_link_or_reparse(path)
        snapshot[relative] = value
    return snapshot


def snapshot_build_tree(root: Path) -> list[dict[str, Any]]:
    snapshot = capture_build_bytes(root)
    return [
        {
            "path": relative,
            "byte_length": len(value),
            "sha256": sha256_bytes(value),
        }
        for relative, value in sorted(snapshot.items())
    ]


def materialize_byte_snapshot(
    snapshot: Mapping[str, bytes],
    destination: Path,
) -> None:
    if os.path.lexists(destination):
        raise PackageError(f"snapshot destination already exists: {destination}")
    destination.mkdir(parents=True)
    for relative, value in sorted(
        snapshot.items(), key=lambda item: item[0].encode("ascii")
    ):
        require_safe_member_path(relative)
        path = destination.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)


def require_safe_member_path(member_path: str) -> None:
    pure = PurePosixPath(member_path)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        not member_path
        or member_path == "."
        or "\\" in member_path
        or pure.is_absolute()
        or member_path.endswith("/")
        or pure.as_posix() != member_path
        or ":" in member_path
        or ".." in pure.parts
        or any(not part for part in pure.parts)
        or not member_path.isascii()
        or any(
            part in {".", ".."}
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in reserved
            or any(ord(character) < 32 for character in part)
            for part in pure.parts
        )
    ):
        raise PackageError(f"unsafe or non-ASCII ZIP member path: {member_path!r}")


def require_all_leaves(value: Any, expected: Any, path: str) -> None:
    if isinstance(value, dict):
        if not value:
            raise PackageError(f"{path} must not be empty")
        for key, child in value.items():
            require_all_leaves(child, expected, f"{path}.{key}")
        return
    if isinstance(value, list):
        if not value:
            raise PackageError(f"{path} must not be empty")
        for index, child in enumerate(value):
            require_all_leaves(child, expected, f"{path}[{index}]")
        return
    if value != expected:
        raise PackageError(f"{path} must be {expected!r}, got {value!r}")


def status_guard(value: Any, path: str = "$") -> None:
    """Fail if any nested status claims human/model/scientific completion."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            lowered = key.lower()
            if lowered == "human_review_status" and child != (
                "PENDING_HUMAN_REVIEW"
            ):
                raise PackageError(
                    f"{child_path} must remain PENDING_HUMAN_REVIEW"
                )
            if lowered == "experiment_status" and child != "NOT_RUN":
                raise PackageError(f"{child_path} must remain NOT_RUN")
            if lowered in {
                "g1_status",
                "g2_status",
                "core_status",
                "scientific_verdict_status",
            } and child != "NOT_RUN":
                raise PackageError(f"{child_path} must remain NOT_RUN")
            if lowered == "overall_g1_pass" and child is not None:
                raise PackageError(f"{child_path} must remain null")
            if lowered == "scientific_evidence" and child is not False:
                raise PackageError(f"{child_path} must remain false")
            if lowered in {"run_eligible", "run_eligibility"} and child is not False:
                raise PackageError(f"{child_path} must remain false")
            if lowered == "formal_experiment" and child is not False:
                raise PackageError(f"{child_path} must remain false")
            if lowered == "human_review_receipt" and child is not None:
                raise PackageError(f"{child_path} must remain null")
            if lowered == "machine_label_status" and child != (
                "MACHINE_COMPUTED_PENDING_HUMAN_REVIEW"
            ):
                raise PackageError(
                    f"{child_path} must remain machine-computed pending human review"
                )
            if lowered in {
                "current_parameter_update",
                "parameter_update_authorized",
                "scientific_evaluation_eligible",
                "selection_eligible",
                "calibration_eligible",
            } and child is not False:
                raise PackageError(f"{child_path} must remain false")
            if lowered in {
                "model_dependent_status",
                "model_dependent_checks",
            }:
                require_all_leaves(child, "NOT_RUN", child_path)
                continue
            status_guard(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            status_guard(child, f"{path}[{index}]")


def denied_model_actions(value: Mapping[str, Any], label: str) -> None:
    authorization = value.get("authorization")
    if not isinstance(authorization, dict):
        raise PackageError(f"{label}.authorization is missing")
    denied = (
        "tokenizer_load",
        "gpu",
        "model_weight_load",
        "model_inference",
        "model_generation",
        "model_forward",
        "backward",
        "gradient",
        "optimizer",
        "optimizer_step",
        "parameter_update",
        "training",
        "rl",
        "rlvr",
        "one_stack_methodology_pilot",
        "eight_stack_screen",
        "scientific_go_stop_decision",
    )
    for key in denied:
        if authorization.get(key) is not False:
            raise PackageError(f"{label}.authorization.{key} must be false")


def verify_build_bindings(build_root: Path) -> dict[str, Any]:
    manifest_path = build_root / "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json"
    require_file(manifest_path)
    manifest = read_json(manifest_path)
    status_guard(manifest, "build_manifest")
    if manifest.get("formal_experiment") is not False:
        raise PackageError("build manifest must state formal_experiment=false")
    if manifest.get("total_rows") != {
        "source": 112,
        "target_calibration": 56,
        "target_audit": 112,
    }:
        raise PackageError("build manifest row totals are not 112/56/112")
    if manifest.get("total_candidate_records") != 1960:
        raise PackageError("build manifest must bind 1,960 candidate records")

    artifact_hashes = manifest.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise PackageError("build manifest artifact_hashes is missing")
    if set(artifact_hashes) != set(BOUND_ARTIFACT_FILES):
        raise PackageError(
            "build manifest artifact_hashes must have the exact frozen file set"
        )
    for filename, binding in artifact_hashes.items():
        require_safe_member_path(filename)
        if PurePosixPath(filename).parent != PurePosixPath("."):
            raise PackageError(
                f"artifact binding must name a root file: {filename}"
            )
        path = build_root / filename
        require_file(path)
        if path.suffix == ".jsonl":
            validate_jsonl_strict(path)
        elif path.suffix == ".json":
            read_json(path)
        if not isinstance(binding, dict):
            raise PackageError(f"invalid artifact binding: {filename}")
        if binding.get("sha256") != sha256_file(path):
            raise PackageError(f"artifact SHA-256 mismatch: {filename}")
        if binding.get("byte_length") != path.stat().st_size:
            raise PackageError(f"artifact byte length mismatch: {filename}")

    code_hashes = manifest.get("code_hashes")
    if not isinstance(code_hashes, dict):
        raise PackageError("build manifest code_hashes is missing")
    source_bindings = (
        (
            REAL_ASSETS_DIR / "scripts" / "build_controlled_assets.py",
            "builder_implementation_sha256",
        ),
        (
            REAL_ASSETS_DIR / "src" / "controlled_tasks.py",
            "task_and_verifier_implementation_sha256",
        ),
    )
    for path, key in source_bindings:
        require_file(path)
        if code_hashes.get(key) != sha256_file(path):
            raise PackageError(f"code SHA-256 mismatch: {path.name}")
    return manifest


def snapshot_file(
    snapshot: Mapping[str, bytes],
    relative: str,
) -> bytes:
    require_safe_member_path(relative)
    try:
        return snapshot[relative]
    except KeyError as exc:
        raise PackageError(
            f"required file is missing from frozen build snapshot: {relative}"
        ) from exc


def verify_snapshot_build_bindings(
    build_snapshot: Mapping[str, bytes],
    tooling_bytes: Mapping[str, bytes],
) -> dict[str, Any]:
    manifest_bytes = snapshot_file(build_snapshot, NATIVE_BUILD_MANIFEST)
    manifest = parse_json_bytes(
        manifest_bytes,
        f"snapshot:{NATIVE_BUILD_MANIFEST}",
    )
    status_guard(manifest, "build_manifest")
    if manifest.get("formal_experiment") is not False:
        raise PackageError("build manifest must state formal_experiment=false")
    if manifest.get("total_rows") != {
        "source": 112,
        "target_calibration": 56,
        "target_audit": 112,
    }:
        raise PackageError("build manifest row totals are not 112/56/112")
    if manifest.get("total_candidate_records") != 1960:
        raise PackageError("build manifest must bind 1,960 candidate records")

    artifact_hashes = manifest.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise PackageError("build manifest artifact_hashes is missing")
    if set(artifact_hashes) != set(BOUND_ARTIFACT_FILES):
        raise PackageError(
            "build manifest artifact_hashes must have the exact frozen file set"
        )
    for filename, binding in artifact_hashes.items():
        require_safe_member_path(filename)
        if PurePosixPath(filename).parent != PurePosixPath("."):
            raise PackageError(
                f"artifact binding must name a root file: {filename}"
            )
        value = snapshot_file(build_snapshot, filename)
        if filename.endswith(".jsonl"):
            validate_jsonl_strict_bytes(value, f"snapshot:{filename}")
        elif filename.endswith(".json"):
            parse_json_bytes(value, f"snapshot:{filename}")
        if not isinstance(binding, dict):
            raise PackageError(f"invalid artifact binding: {filename}")
        if binding.get("sha256") != sha256_bytes(value):
            raise PackageError(f"artifact SHA-256 mismatch: {filename}")
        if binding.get("byte_length") != len(value):
            raise PackageError(f"artifact byte length mismatch: {filename}")

    code_hashes = manifest.get("code_hashes")
    if not isinstance(code_hashes, dict):
        raise PackageError("build manifest code_hashes is missing")
    expected_code_hashes = {
        "builder_implementation_sha256": sha256_bytes(
            tooling_bytes["builder"]
        ),
        "task_and_verifier_implementation_sha256": sha256_bytes(
            tooling_bytes["controlled_tasks"]
        ),
    }
    for key, expected in expected_code_hashes.items():
        if code_hashes.get(key) != expected:
            raise PackageError(f"code SHA-256 mismatch: {key}")
    return manifest


def run_fresh_native_validation(
    *,
    build_root: Path,
    native_validator: Path,
) -> dict[str, Any]:
    require_file(native_validator)
    spec = importlib.util.spec_from_file_location(
        "p4_r1_native_validator_for_packaging",
        native_validator,
    )
    if spec is None or spec.loader is None:
        raise PackageError("cannot load the native validator implementation")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        report = module.validate_native_controlled_assets(build_root)
    except Exception as exc:
        raise PackageError(f"fresh native validation crashed: {exc}") from exc
    if not isinstance(report, dict):
        raise PackageError("fresh native validator did not return an object")
    return report


def portable_validation_report(
    path: Path,
    *,
    build_root: Path | None = None,
    native_validator: Path | None = None,
    reported_build_root: Path | None = None,
    native_build_manifest_bytes: bytes | None = None,
    native_validator_bytes: bytes | None = None,
) -> bytes:
    report = read_json(path)
    if build_root is not None:
        build_root = build_root.resolve()
        expected_report_root = (
            reported_build_root.resolve()
            if reported_build_root is not None
            else build_root
        )
        reported_root = report.get("root")
        if (
            not isinstance(reported_root, str)
            or Path(reported_root).resolve() != expected_report_root
        ):
            raise PackageError(
                "validation report root does not match the packaged build"
            )
        if native_validator is None:
            raise PackageError("native validator path is required")
        fresh_report = run_fresh_native_validation(
            build_root=build_root,
            native_validator=native_validator,
        )
        fresh_root = fresh_report.get("root")
        if (
            not isinstance(fresh_root, str)
            or Path(fresh_root).resolve() != build_root
        ):
            raise PackageError(
                "fresh validation report root does not match its snapshot"
            )
        report_comparable = copy.deepcopy(report)
        fresh_comparable = copy.deepcopy(fresh_report)
        report_comparable["root"] = "<BOUND_BUILD_ROOT>"
        fresh_comparable["root"] = "<BOUND_BUILD_ROOT>"
        if report_comparable != fresh_comparable:
            raise PackageError(
                "provided validation report does not equal a fresh validation "
                "of the packaged build"
            )
    valid = report.get("valid", report.get("machine_valid"))
    if report.get("schema_version") != (
        "native-controlled-assets-validation-report-v1"
    ):
        raise PackageError("validation report schema version is not exact")
    if (
        valid is not True
        or report.get("error_count") != 0
        or report.get("errors") != []
    ):
        raise PackageError("validation report is not a clean machine PASS")
    if report.get("model_execution_performed") is not False:
        raise PackageError("validation report must state no model execution")

    expected_counts = {
        "candidate_records": 1960,
        "machine_label_rows": 280,
        "prompt_rows_total": 280,
        "raw_source_files": 224,
        "raw_target_files": 336,
        "source_rows": 112,
        "target_audit_rows": 112,
        "target_calibration_rows": 56,
        "verifier_rows": 280,
    }
    if report.get("counts") != expected_counts:
        raise PackageError("validation report counts are incomplete or incorrect")
    expected_metrics = {
        "local_online_fpr": {"denominator": 672, "numerator": 112},
        "shared_online_fpr": {"denominator": 672, "numerator": 112},
    }
    if report.get("metrics") != expected_metrics:
        raise PackageError("validation report FPR metrics are not exact")

    portable = {
        "schema_version": report["schema_version"],
        "root": ".",
        "valid": True,
        "error_count": 0,
        "errors": [],
        "counts": expected_counts,
        "metrics": expected_metrics,
        "model_execution_performed": False,
    }
    portable["machine_validation_status"] = "PASS_MACHINE_CHECK"
    portable["machine_valid"] = True
    portable["human_review_status"] = "PENDING_HUMAN_REVIEW"
    portable["experiment_status"] = "NOT_RUN"
    portable["model_dependent_status"] = "NOT_RUN"
    portable["g1_status"] = "NOT_RUN"
    portable["g2_status"] = "NOT_RUN"
    portable["core_status"] = "NOT_RUN"
    portable["scientific_verdict_status"] = "NOT_RUN"
    portable["scientific_evidence"] = False
    portable["run_eligible"] = False
    portable["scientific_claims_supported"] = []
    if build_root is not None and native_validator is not None:
        manifest_value = (
            native_build_manifest_bytes
            if native_build_manifest_bytes is not None
            else (build_root / NATIVE_BUILD_MANIFEST).read_bytes()
        )
        validator_value = (
            native_validator_bytes
            if native_validator_bytes is not None
            else native_validator.read_bytes()
        )
        portable["validation_binding"] = {
            "native_build_manifest_sha256": sha256_bytes(manifest_value),
            "native_validator_sha256": sha256_bytes(validator_value),
        }
    status_guard(portable, "portable_validation_report")
    return canonical_json_bytes(portable)


def collect_tree_members(
    root: Path,
    member_prefix: str,
) -> dict[str, bytes]:
    require_safe_member_path(member_prefix)
    if not root.is_dir():
        raise PackageError(f"required directory is missing: {root}")
    root = root.resolve()
    reject_link_or_reparse(root)
    members: dict[str, bytes] = {}
    candidates: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        reject_link_or_reparse(path)
        resolved = path.resolve()
        if not path_is_within(resolved, root):
            raise PackageError(f"tree member resolves outside input root: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        member_path = f"{member_prefix}/{relative}"
        require_safe_member_path(member_path)
        if member_path.endswith(".row.json"):
            validate_jsonl_strict(path)
        candidates.append((member_path, path))
    for member_path, path in sorted(
        candidates, key=lambda item: item[0].encode("ascii")
    ):
        members[member_path] = path.read_bytes()
    return members


def collect_snapshot_tree_members(
    snapshot: Mapping[str, bytes],
    snapshot_prefix: str,
    member_prefix: str,
) -> dict[str, bytes]:
    require_safe_member_path(snapshot_prefix)
    require_safe_member_path(member_prefix)
    prefix = f"{snapshot_prefix}/"
    members: dict[str, bytes] = {}
    for relative, value in sorted(
        snapshot.items(), key=lambda item: item[0].encode("ascii")
    ):
        if not relative.startswith(prefix):
            continue
        suffix = relative[len(prefix) :]
        member_path = f"{member_prefix}/{suffix}"
        require_safe_member_path(member_path)
        if member_path.endswith(".row.json"):
            validate_jsonl_strict_bytes(
                value,
                f"snapshot:{relative}",
            )
        members[member_path] = value
    if not members:
        raise PackageError(
            f"frozen build snapshot has no members under {snapshot_prefix}"
        )
    return members


def build_bundle_manifest(
    *,
    bundle_id: str,
    protocol_sha256: str,
    prior_bundle_hashes: Mapping[str, str],
    members: Mapping[str, bytes],
    scope: str,
    native_build_manifest_sha256: str,
    native_prereg_sha256: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "p4-r1-deterministic-cpu-bundle-manifest-v1",
            "bundle_id": bundle_id,
            "scope": scope,
            "protocol_sha256": protocol_sha256,
            "native_build_provenance": {
                "native_build_manifest_member": (
                    NATIVE_BUILD_MANIFEST_MEMBER
                ),
                "native_build_manifest_sha256": (
                    native_build_manifest_sha256
                ),
                "native_prereg_member": NATIVE_PREREG_MEMBER,
                "native_prereg_sha256": native_prereg_sha256,
                "final_release_prereg_is_distinct": True,
            },
            "prior_bundle_hashes": dict(sorted(prior_bundle_hashes.items())),
            "zip_contract": {
                "compression": "ZIP_STORED",
                "member_order": "ascending_POSIX_path_ASCII_bytes",
                "timestamp": "1980-01-01T00:00:00",
                "external_mode_octal": "100444",
                "directory_entries": False,
                "extra_fields": False,
                "archive_comment": False,
            },
            "members_excluding_this_manifest": [
                {
                    "path": path,
                    "byte_length": len(value),
                    "sha256": sha256_bytes(value),
                }
                for path, value in sorted(
                    members.items(), key=lambda item: item[0].encode("ascii")
                )
            ],
            "experiment_status": "NOT_RUN",
            "human_review_status": "PENDING_HUMAN_REVIEW",
            "model_dependent_status": "NOT_RUN",
            "scientific_evidence": False,
            "run_eligible": False,
        }
    )


def write_deterministic_zip(path: Path, members: Mapping[str, bytes]) -> None:
    for member_path in members:
        require_safe_member_path(member_path)
    with zipfile.ZipFile(
        path,
        mode="x",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        archive.comment = b""
        for member_path, value in sorted(
            members.items(), key=lambda item: item[0].encode("ascii")
        ):
            info = zipfile.ZipInfo(member_path, FIXED_ZIP_DATETIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = READ_ONLY_FILE_MODE << 16
            info.extra = b""
            info.comment = b""
            info.flag_bits = 0x800
            archive.writestr(info, value)


def verify_zip_readback(path: Path, members: Mapping[str, bytes]) -> None:
    expected_names = sorted(members, key=lambda value: value.encode("ascii"))
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            if archive.comment != b"":
                raise PackageError(f"ZIP archive comment is not empty: {path}")
            if [info.filename for info in infos] != expected_names:
                raise PackageError(f"ZIP member order or inventory differs: {path}")
            for info in infos:
                if (
                    info.is_dir()
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.date_time != FIXED_ZIP_DATETIME
                    or info.extra != b""
                    or info.comment != b""
                    or (info.external_attr >> 16) != READ_ONLY_FILE_MODE
                    or archive.read(info.filename) != members[info.filename]
                ):
                    raise PackageError(
                        f"ZIP readback contract failed: {path}!{info.filename}"
                    )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise PackageError(f"cannot verify ZIP readback {path}: {exc}") from exc


def file_members(
    build_root: Path | Mapping[str, bytes],
    filenames: Sequence[str],
) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for filename in filenames:
        require_safe_member_path(filename)
        if isinstance(build_root, Mapping):
            members[filename] = snapshot_file(build_root, filename)
        else:
            path = build_root / filename
            require_file(path)
            members[filename] = path.read_bytes()
    return members


def package_deliverables(
    *,
    build_root: Path,
    output_dir: Path,
    validation_report_path: Path,
) -> dict[str, Any]:
    build_root = build_root.resolve()
    output_dir = output_dir.resolve()
    validation_report_path = validation_report_path.resolve()
    if paths_overlap(output_dir, build_root):
        raise PackageError(
            "output directory and native build root must not overlap"
        )
    if path_is_within(validation_report_path, output_dir):
        raise PackageError(
            "validation report must not be located in the output directory"
        )
    if path_is_within(validation_report_path, build_root):
        raise PackageError(
            "validation report must not be stored inside the native build"
        )
    if os.path.lexists(output_dir):
        raise PackageError(
            "output directory must not already exist; refusing dirty or "
            "partially published output"
        )

    native_validator = (
        REAL_ASSETS_DIR / "scripts" / "validate_native_controlled_assets.py"
    )
    native_tests = REAL_ASSETS_DIR / "tests" / "test_native_controlled_assets.py"
    controlled_tasks_path = REAL_ASSETS_DIR / "src" / "controlled_tasks.py"
    builder_path = REAL_ASSETS_DIR / "scripts" / "build_controlled_assets.py"
    tooling_paths = {
        "controlled_tasks": controlled_tasks_path,
        "builder": builder_path,
        "native_validator": native_validator,
        "native_tests": native_tests,
        "packager": SCRIPT_PATH,
    }
    for path in tooling_paths.values():
        require_file(path)
        reject_link_or_reparse(path)

    # Capture every untrusted input exactly once.  From this point forward,
    # all safety decisions and bundle bytes use these immutable bytes rather
    # than re-reading the mutable native-build paths.
    build_snapshot = capture_build_bytes(build_root)
    tooling_bytes = {
        key: path.read_bytes() for key, path in tooling_paths.items()
    }
    build_manifest = verify_snapshot_build_bindings(
        build_snapshot,
        tooling_bytes,
    )

    protocol_bytes = snapshot_file(build_snapshot, PROTOCOL)
    protocol = parse_json_bytes(protocol_bytes, f"snapshot:{PROTOCOL}")
    status_guard(protocol, "protocol")
    denied_model_actions(protocol, "protocol")
    protocol_sha256 = sha256_bytes(protocol_bytes)

    native_prereg_bytes = snapshot_file(build_snapshot, PREREG)
    base_prereg = parse_json_bytes(
        native_prereg_bytes,
        f"snapshot:{PREREG}",
    )
    status_guard(base_prereg, "base_prereg")
    denied_model_actions(base_prereg, "base_prereg")
    native_build_manifest_bytes = snapshot_file(
        build_snapshot,
        NATIVE_BUILD_MANIFEST,
    )
    native_build_manifest_sha256 = sha256_bytes(
        native_build_manifest_bytes
    )
    native_prereg_sha256 = sha256_bytes(native_prereg_bytes)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    snapshot_work_root = Path(
        tempfile.mkdtemp(
            prefix=".p4-r1-input-snapshot-",
            dir=str(output_dir.parent),
        )
    ).resolve()
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".p4-r1-release-staging-",
            dir=str(output_dir.parent),
        )
    ).resolve()
    published = False
    try:
        snapshot_assets_root = snapshot_work_root / "real_assets"
        workspace_snapshot = {
            **{
                f"native_build/{relative}": value
                for relative, value in build_snapshot.items()
            },
            "scripts/build_controlled_assets.py": tooling_bytes["builder"],
            "scripts/validate_native_controlled_assets.py": tooling_bytes[
                "native_validator"
            ],
            "src/controlled_tasks.py": tooling_bytes["controlled_tasks"],
        }
        materialize_byte_snapshot(
            workspace_snapshot,
            snapshot_assets_root,
        )
        snapshot_build_root = snapshot_assets_root / "native_build"
        snapshot_validator = (
            snapshot_assets_root
            / "scripts"
            / "validate_native_controlled_assets.py"
        )
        portable_report = portable_validation_report(
            validation_report_path,
            build_root=snapshot_build_root,
            native_validator=snapshot_validator,
            reported_build_root=build_root,
            native_build_manifest_bytes=native_build_manifest_bytes,
            native_validator_bytes=tooling_bytes["native_validator"],
        )

        provenance_members = {
            NATIVE_BUILD_MANIFEST_MEMBER: native_build_manifest_bytes,
            NATIVE_PREREG_MEMBER: native_prereg_bytes,
        }
        source_members = file_members(build_snapshot, SOURCE_FILES)
        source_members.update(
            collect_snapshot_tree_members(
                build_snapshot,
                "raw_source_bytes",
                "raw_source_bytes",
            )
        )
        source_members.update(provenance_members)
        source_members["src/controlled_tasks.py"] = tooling_bytes[
            "controlled_tasks"
        ]
        source_members["scripts/build_controlled_assets.py"] = tooling_bytes[
            "builder"
        ]
        source_members[SOURCE_MANIFEST] = build_bundle_manifest(
            bundle_id="REAL_SOURCE_STACK_BUNDLE_V1",
            protocol_sha256=protocol_sha256,
            prior_bundle_hashes={},
            members=source_members,
            scope=(
                "8 mapping stacks and 112 source rows; "
                "machine labels pending human review"
            ),
            native_build_manifest_sha256=native_build_manifest_sha256,
            native_prereg_sha256=native_prereg_sha256,
        )

        verifier_members = file_members(build_snapshot, VERIFIER_FILES)
        verifier_members.update(provenance_members)
        verifier_members["CPU_VALIDATION_REPORT_V1.json"] = portable_report
        verifier_members["src/controlled_tasks.py"] = tooling_bytes[
            "controlled_tasks"
        ]
        verifier_members[
            "scripts/validate_native_controlled_assets.py"
        ] = tooling_bytes["native_validator"]
        verifier_members[
            "tests/test_native_controlled_assets.py"
        ] = tooling_bytes["native_tests"]
        target_members = file_members(build_snapshot, TARGET_FILES)
        target_members.update(provenance_members)
        target_members.update(
            collect_snapshot_tree_members(
                build_snapshot,
                "raw_target_bytes",
                "raw_target_bytes",
            )
        )

        (staging_root / PROTOCOL).write_bytes(protocol_bytes)

        source_zip_path = staging_root / SOURCE_ZIP
        write_deterministic_zip(source_zip_path, source_members)
        verify_zip_readback(source_zip_path, source_members)
        source_zip_sha256 = sha256_file(source_zip_path)

        verifier_members[VERIFIER_MANIFEST] = build_bundle_manifest(
            bundle_id="VERIFIER_G1_CPU_BUNDLE_V1",
            protocol_sha256=protocol_sha256,
            prior_bundle_hashes={SOURCE_ZIP: source_zip_sha256},
            members=verifier_members,
            scope=(
                "CPU verifier geometry and static G1 checks only; "
                "model-dependent G1 remains NOT_RUN"
            ),
            native_build_manifest_sha256=native_build_manifest_sha256,
            native_prereg_sha256=native_prereg_sha256,
        )
        verifier_zip_path = staging_root / VERIFIER_ZIP
        write_deterministic_zip(verifier_zip_path, verifier_members)
        verify_zip_readback(verifier_zip_path, verifier_members)
        verifier_zip_sha256 = sha256_file(verifier_zip_path)

        target_members[TARGET_MANIFEST] = build_bundle_manifest(
            bundle_id="TARGET_G2_REAL_BUNDLE_V1",
            protocol_sha256=protocol_sha256,
            prior_bundle_hashes={
                SOURCE_ZIP: source_zip_sha256,
                VERIFIER_ZIP: verifier_zip_sha256,
            },
            members=target_members,
            scope=(
                "56 target calibration rows plus 112 byte-sealed audit rows; "
                "G2 and all model scoring remain NOT_RUN"
            ),
            native_build_manifest_sha256=native_build_manifest_sha256,
            native_prereg_sha256=native_prereg_sha256,
        )
        target_zip_path = staging_root / TARGET_ZIP
        write_deterministic_zip(target_zip_path, target_members)
        verify_zip_readback(target_zip_path, target_members)
        target_zip_sha256 = sha256_file(target_zip_path)

        final_prereg = copy.deepcopy(base_prereg)
        final_prereg["native_build_provenance"] = {
            "native_build_manifest_original_filename": (
                NATIVE_BUILD_MANIFEST
            ),
            "native_build_manifest_bundle_member": (
                NATIVE_BUILD_MANIFEST_MEMBER
            ),
            "native_build_manifest_sha256": (
                native_build_manifest_sha256
            ),
            "native_prereg_original_filename": PREREG,
            "native_prereg_bundle_member": NATIVE_PREREG_MEMBER,
            "native_prereg_sha256": native_prereg_sha256,
            "final_release_prereg_is_distinct": True,
        }
        final_prereg["asset_bindings"] = {
            PROTOCOL: {
                "byte_length": len(protocol_bytes),
                "sha256": protocol_sha256,
            },
            SOURCE_ZIP: {
                "byte_length": source_zip_path.stat().st_size,
                "sha256": source_zip_sha256,
            },
            VERIFIER_ZIP: {
                "byte_length": verifier_zip_path.stat().st_size,
                "sha256": verifier_zip_sha256,
            },
            TARGET_ZIP: {
                "byte_length": target_zip_path.stat().st_size,
                "sha256": target_zip_sha256,
            },
        }
        final_prereg["release_chain"] = [
            f"H0=sha256({PROTOCOL})",
            f"Zs=sha256({SOURCE_ZIP}); source manifest binds H0",
            (
                f"Zv=sha256({VERIFIER_ZIP}); verifier manifest binds "
                "H0 and Zs"
            ),
            (
                f"Zt=sha256({TARGET_ZIP}); target manifest binds "
                "H0, Zs, and Zv"
            ),
            "this preregistration binds H0, Zs, Zv, and Zt",
        ]
        final_prereg["release_tooling"] = {
            "scripts/package_cpu_deliverables.py": sha256_bytes(
                tooling_bytes["packager"]
            ),
            "scripts/validate_native_controlled_assets.py": sha256_bytes(
                tooling_bytes["native_validator"]
            ),
            "validation_report_sha256": sha256_bytes(portable_report),
        }
        final_prereg["release_status"] = (
            "CPU_ASSETS_PACKAGED_PENDING_HUMAN_AND_PRO_REVIEW"
        )
        status_guard(final_prereg, "final_prereg")
        denied_model_actions(final_prereg, "final_prereg")
        (staging_root / PREREG).write_bytes(
            canonical_json_bytes(final_prereg)
        )

        hashes = {
            filename: {
                "byte_length": (staging_root / filename).stat().st_size,
                "sha256": sha256_file(staging_root / filename),
            }
            for filename in DELIVERABLES
        }
        if {
            path.name for path in staging_root.iterdir()
        } != set(DELIVERABLES):
            raise PackageError(
                "staging directory does not contain exactly five deliverables"
            )
        if os.path.lexists(output_dir):
            raise PackageError("output directory appeared before atomic publish")
        os.replace(staging_root, output_dir)
        published = True
    finally:
        if not published and staging_root.exists():
            resolved_stage = staging_root.resolve()
            if (
                resolved_stage.parent != output_dir.parent
                or not resolved_stage.name.startswith(
                    ".p4-r1-release-staging-"
                )
            ):
                raise PackageError(
                    "refusing to clean an unexpected staging directory"
                )
            shutil.rmtree(resolved_stage)
        if snapshot_work_root.exists():
            resolved_snapshot = snapshot_work_root.resolve()
            if (
                resolved_snapshot.parent != output_dir.parent
                or not resolved_snapshot.name.startswith(
                    ".p4-r1-input-snapshot-"
                )
            ):
                raise PackageError(
                    "refusing to clean an unexpected input snapshot directory"
                )
            shutil.rmtree(resolved_snapshot)

    return {
        "status": "PACKAGED_CPU_ASSETS_PENDING_HUMAN_AND_PRO_REVIEW",
        "output_dir": str(output_dir),
        "deliverable_count": 5,
        "deliverables": hashes,
        "native_build_manifest_sha256": native_build_manifest_sha256,
        "experiment_status": "NOT_RUN",
        "human_review_status": "PENDING_HUMAN_REVIEW",
        "model_execution_performed": False,
        "run_eligible": False,
        "scientific_evidence": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = package_deliverables(
            build_root=args.build_root,
            output_dir=args.output_dir,
            validation_report_path=args.validation_report,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "PACKAGE_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "model_execution_performed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
