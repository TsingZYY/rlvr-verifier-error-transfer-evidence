"""Unit tests for deterministic, CPU-only deliverable packaging helpers."""

from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest


PACKAGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "package_cpu_deliverables.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "package_cpu_deliverables_under_test",
    PACKAGER_PATH,
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
packager = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(packager)

REAL_ASSETS_DIR = Path(__file__).resolve().parents[1]
REAL_BUILD_V4_A = REAL_ASSETS_DIR / "build_v4_a"
REAL_BUILD_V4_B = REAL_ASSETS_DIR / "build_v4_b"
NATIVE_VALIDATOR_PATH = (
    REAL_ASSETS_DIR / "scripts" / "validate_native_controlled_assets.py"
)
EXPECTED_COUNTS = {
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
EXPECTED_METRICS = {
    "local_online_fpr": {"denominator": 672, "numerator": 112},
    "shared_online_fpr": {"denominator": 672, "numerator": 112},
}


def _write_report(path: Path, **overrides: Any) -> None:
    report: dict[str, Any] = {
        "schema_version": "native-controlled-assets-validation-report-v1",
        "root": r"C:\machine-a\build",
        "valid": True,
        "error_count": 0,
        "errors": [],
        "counts": EXPECTED_COUNTS,
        "metrics": EXPECTED_METRICS,
        "model_execution_performed": False,
    }
    report.update(overrides)
    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def copied_real_builds(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Path]:
    fixture_root = tmp_path_factory.mktemp("package-real-build-v4")
    build_a = fixture_root / "build-a"
    build_b = fixture_root / "build-b"
    shutil.copytree(REAL_BUILD_V4_A, build_a)
    shutil.copytree(REAL_BUILD_V4_B, build_b)

    report_a_path = fixture_root / "validation-a.json"
    report_b_path = fixture_root / "validation-b.json"
    report_a = packager.run_fresh_native_validation(
        build_root=build_a,
        native_validator=NATIVE_VALIDATOR_PATH,
    )
    report_b = packager.run_fresh_native_validation(
        build_root=build_b,
        native_validator=NATIVE_VALIDATOR_PATH,
    )
    _write_json(report_a_path, report_a)
    _write_json(report_b_path, report_b)

    return {
        "root": fixture_root,
        "build_a": build_a,
        "build_b": build_b,
        "report_a": report_a_path,
        "report_b": report_b_path,
    }


def test_write_deterministic_zip_is_byte_reproducible_and_canonical(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.zip"
    second_path = tmp_path / "second.zip"
    members = {
        "z-last.txt": b"last\n",
        "a-first.txt": b"first\n",
        "nested/middle.bin": bytes(range(32)),
    }

    packager.write_deterministic_zip(first_path, members)
    packager.write_deterministic_zip(
        second_path,
        dict(reversed(tuple(members.items()))),
    )

    assert first_path.read_bytes() == second_path.read_bytes()

    expected_order = sorted(members, key=lambda path: path.encode("ascii"))
    with zipfile.ZipFile(first_path, "r") as archive:
        assert archive.comment == b""
        assert archive.namelist() == expected_order
        assert archive.testzip() is None

        for info in archive.infolist():
            assert info.date_time == packager.FIXED_ZIP_DATETIME
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.compress_size == info.file_size
            assert info.extra == b""
            assert info.comment == b""
            assert info.create_system == 3
            assert info.external_attr >> 16 == packager.READ_ONLY_FILE_MODE
            assert not info.is_dir()
            assert archive.read(info) == members[info.filename]


@pytest.mark.parametrize(
    "member_path",
    (
        "",
        "../escape.txt",
        "safe/../../escape.txt",
        "/absolute.txt",
        r"windows\separator.txt",
        "non-ascii-\N{LATIN SMALL LETTER E WITH ACUTE}.txt",
        "C:/escape.txt",
        "CON",
        "a:b",
        "a/./b",
        "a//b",
        "trailing/",
    ),
)
def test_write_deterministic_zip_rejects_unsafe_members(
    tmp_path: Path,
    member_path: str,
) -> None:
    output_path = tmp_path / "unsafe.zip"

    with pytest.raises(packager.PackageError, match="unsafe or non-ASCII"):
        packager.write_deterministic_zip(
            output_path,
            {member_path: b"must not be written"},
        )

    assert not output_path.exists()


def test_require_safe_member_path_rejects_current_directory() -> None:
    with pytest.raises(packager.PackageError, match="unsafe or non-ASCII"):
        packager.require_safe_member_path(".")


def test_portable_validation_report_normalizes_roots_and_freezes_statuses(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "validation.json"
    _write_report(report_path)

    encoded = packager.portable_validation_report(report_path)
    portable = json.loads(encoded)

    assert encoded.endswith(b"\n")
    assert all(byte < 128 for byte in encoded)
    assert portable["root"] == "."
    assert portable["schema_version"] == (
        "native-controlled-assets-validation-report-v1"
    )
    assert portable["counts"] == EXPECTED_COUNTS
    assert portable["metrics"] == EXPECTED_METRICS
    assert portable["valid"] is True
    assert portable["errors"] == []
    assert portable["machine_validation_status"] == "PASS_MACHINE_CHECK"
    assert portable["machine_valid"] is True
    assert portable["error_count"] == 0
    assert portable["human_review_status"] == "PENDING_HUMAN_REVIEW"
    assert portable["experiment_status"] == "NOT_RUN"
    assert portable["model_execution_performed"] is False
    assert portable["model_dependent_status"] == "NOT_RUN"
    assert portable["g1_status"] == "NOT_RUN"
    assert portable["g2_status"] == "NOT_RUN"
    assert portable["core_status"] == "NOT_RUN"
    assert portable["scientific_verdict_status"] == "NOT_RUN"
    assert portable["scientific_evidence"] is False
    assert portable["run_eligible"] is False
    assert portable["scientific_claims_supported"] == []


@pytest.mark.parametrize(
    "overrides, error_match",
    (
        (
            {"valid": False, "model_execution_performed": False},
            "not a clean machine PASS",
        ),
        (
            {"valid": True, "model_execution_performed": True},
            "must state no model execution",
        ),
    ),
)
def test_portable_validation_report_rejects_invalid_or_model_executed_reports(
    tmp_path: Path,
    overrides: dict[str, Any],
    error_match: str,
) -> None:
    report_path = tmp_path / "validation.json"
    _write_report(report_path, **overrides)

    with pytest.raises(packager.PackageError, match=error_match):
        packager.portable_validation_report(report_path)


@pytest.mark.parametrize(
    "guarded_value, error_match",
    (
        ({"run_eligibility": True}, "must remain false"),
        (
            {"model_dependent_status": {"scoring": "COMPLETE"}},
            "must be 'NOT_RUN'",
        ),
        ({"g1_status": "PASS"}, "must remain NOT_RUN"),
        ({"overall_g1_pass": True}, "must remain null"),
    ),
)
def test_status_guard_rejects_scientific_or_model_completion_claims(
    guarded_value: dict[str, Any],
    error_match: str,
) -> None:
    with pytest.raises(packager.PackageError, match=error_match):
        packager.status_guard(guarded_value)


@pytest.mark.parametrize(
    "raw_json, error_match",
    (
        ('{"valid":true,"valid":false}\n', "duplicate JSON key"),
        ('{"metric":NaN}\n', "non-finite JSON constant"),
    ),
)
def test_read_json_rejects_duplicate_keys_and_non_finite_numbers(
    tmp_path: Path,
    raw_json: str,
    error_match: str,
) -> None:
    path = tmp_path / "hostile.json"
    path.write_text(raw_json, encoding="utf-8")

    with pytest.raises(packager.PackageError, match=error_match):
        packager.read_json(path)


def test_package_rejects_validation_report_from_another_build(
    copied_real_builds: dict[str, Path],
) -> None:
    output_dir = copied_real_builds["root"] / "cross-build-output"

    with pytest.raises(
        packager.PackageError,
        match="validation report root does not match",
    ):
        packager.package_deliverables(
            build_root=copied_real_builds["build_a"],
            output_dir=output_dir,
            validation_report_path=copied_real_builds["report_b"],
        )

    assert not output_dir.exists()


def test_package_rejects_output_nested_inside_native_build(
    copied_real_builds: dict[str, Path],
) -> None:
    output_dir = copied_real_builds["build_a"] / "nested-release"

    with pytest.raises(packager.PackageError, match="must not overlap"):
        packager.package_deliverables(
            build_root=copied_real_builds["build_a"],
            output_dir=output_dir,
            validation_report_path=copied_real_builds["report_a"],
        )

    assert not output_dir.exists()


def test_package_rejects_dirty_output_without_publishing_any_deliverable(
    copied_real_builds: dict[str, Path],
) -> None:
    output_dir = copied_real_builds["root"] / "dirty-output"
    output_dir.mkdir()
    sentinel = output_dir / "preexisting.txt"
    sentinel.write_bytes(b"do not overwrite\n")

    with pytest.raises(
        packager.PackageError,
        match="output directory must not already exist",
    ):
        packager.package_deliverables(
            build_root=copied_real_builds["build_a"],
            output_dir=output_dir,
            validation_report_path=copied_real_builds["report_a"],
        )

    assert sentinel.read_bytes() == b"do not overwrite\n"
    assert {path.name for path in output_dir.iterdir()} == {sentinel.name}
    assert not any(
        (output_dir / filename).exists()
        for filename in packager.DELIVERABLES
    )


def test_package_never_publishes_transiently_tampered_member_bytes(
    copied_real_builds: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = copied_real_builds["build_b"]
    victim_path = build_root / "REAL_MAPPING_STACKS_V1.jsonl"
    original_bytes = victim_path.read_bytes()
    first_row = original_bytes.splitlines(keepends=True)[0]
    tampered_bytes = original_bytes + first_row
    assert tampered_bytes != original_bytes

    output_dir = copied_real_builds["root"] / "toctou-output"
    original_file_members = packager.file_members
    tamper_triggered = False

    def file_members_during_transient_tamper(
        member_root: Path,
        filenames: tuple[str, ...],
    ) -> dict[str, bytes]:
        nonlocal tamper_triggered
        if (
            not tamper_triggered
            and "REAL_MAPPING_STACKS_V1.jsonl" in filenames
        ):
            tamper_triggered = True
            victim_path.write_bytes(tampered_bytes)
            try:
                return original_file_members(member_root, filenames)
            finally:
                victim_path.write_bytes(original_bytes)
        return original_file_members(member_root, filenames)

    monkeypatch.setattr(
        packager,
        "file_members",
        file_members_during_transient_tamper,
    )

    package_error: packager.PackageError | None = None
    try:
        try:
            packager.package_deliverables(
                build_root=build_root,
                output_dir=output_dir,
                validation_report_path=copied_real_builds["report_b"],
            )
        except packager.PackageError as exc:
            package_error = exc
    finally:
        victim_path.write_bytes(original_bytes)

    assert tamper_triggered
    assert victim_path.read_bytes() == original_bytes
    if not output_dir.exists():
        assert package_error is not None
        return

    with zipfile.ZipFile(output_dir / packager.SOURCE_ZIP, "r") as archive:
        bundled_bytes = archive.read("REAL_MAPPING_STACKS_V1.jsonl")
    assert bundled_bytes == original_bytes
    assert bundled_bytes != tampered_bytes


def test_package_real_build_v4_end_to_end_succeeds_atomically(
    copied_real_builds: dict[str, Path],
) -> None:
    output_dir = copied_real_builds["root"] / "valid-output"

    result = packager.package_deliverables(
        build_root=copied_real_builds["build_a"],
        output_dir=output_dir,
        validation_report_path=copied_real_builds["report_a"],
    )

    assert result["status"] == (
        "PACKAGED_CPU_ASSETS_PENDING_HUMAN_AND_PRO_REVIEW"
    )
    assert result["deliverable_count"] == 5
    assert result["experiment_status"] == "NOT_RUN"
    assert result["human_review_status"] == "PENDING_HUMAN_REVIEW"
    assert result["model_execution_performed"] is False
    assert result["run_eligible"] is False
    assert result["scientific_evidence"] is False
    assert {path.name for path in output_dir.iterdir()} == set(
        packager.DELIVERABLES
    )

    for filename, binding in result["deliverables"].items():
        path = output_dir / filename
        assert binding["byte_length"] == path.stat().st_size
        assert binding["sha256"] == packager.sha256_file(path)

    portable_report_bytes: bytes | None = None
    with zipfile.ZipFile(output_dir / packager.VERIFIER_ZIP, "r") as archive:
        portable_report_bytes = archive.read("CPU_VALIDATION_REPORT_V1.json")
    portable_report = json.loads(portable_report_bytes)
    assert portable_report["root"] == "."
    assert portable_report["counts"] == EXPECTED_COUNTS
    assert portable_report["metrics"] == EXPECTED_METRICS
    assert portable_report["model_execution_performed"] is False
