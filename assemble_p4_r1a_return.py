"""Assemble the deterministic P4-R1A repair return bundle.

This is an out-of-snapshot packaging helper.  It does not modify the staged
project source and is not included among the authorized project additions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


AUTHORIZED_ADDITIONS = (
    "src/sqlite_agent_research/arithmetic_p4_r1_preflight.py",
    "scripts/run_p4_r1_cpu_preflight.py",
    "protocols/p4_r1_cpu_preflight_contract_v1.json",
    "tests/test_arithmetic_p4_r1_preflight.py",
    "tests/fixtures/p4_r1_contract_only_v1/CONTRACT.json",
    "tests/fixtures/p4_r1_contract_only_v1/MAPPING_STACKS.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/SOURCE_BUNDLES.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/TARGET_CALIBRATION.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/TARGET_AUDIT.jsonl",
    "tests/fixtures/p4_r1_contract_only_v1/P3_RULE_TEST_ONLY.json",
    "tests/fixtures/p4_r1_contract_only_v1/INPUT_SHA256SUMS.txt",
)

REUSED_INFRASTRUCTURE = (
    (
        "scripts/build_strict_manifest_zip.py",
        "9c8fbf61ca2d48be87580835a705c20f1d80a33417d7920417901e1b1d8ff1a9",
        "canonical_zip_builder",
    ),
    (
        "scripts/verify_archive_manifest_safely.py",
        "26fd97935cc4c4c7e54e8b4baa996717ddea282802778e6739e93e5ea815626a",
        "nonexecuting_archive_verifier",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def copy_tree(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            copy_file(path, target / path.relative_to(source))


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        "collected": sum(int(suite.attrib.get("tests", "0")) for suite in suites),
        "passed": sum(
            int(suite.attrib.get("tests", "0"))
            - int(suite.attrib.get("failures", "0"))
            - int(suite.attrib.get("errors", "0"))
            - int(suite.attrib.get("skipped", "0"))
            for suite in suites
        ),
        "failed": sum(
            int(suite.attrib.get("failures", "0"))
            + int(suite.attrib.get("errors", "0"))
            for suite in suites
        ),
        "skipped": sum(int(suite.attrib.get("skipped", "0")) for suite in suites),
    }


def media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        return "application/json"
    if suffix == ".xml":
        return "application/xml"
    if suffix == ".py":
        return "text/x-python"
    return "text/plain"


def build_manifest(
    *,
    snapshot_root: Path,
    parent_zip: Path,
    return_root: Path,
    run_a: Path,
    run_b: Path,
    receipts: list[dict[str, object]],
    junit_path: Path,
    scope_report: dict[str, object],
    deterministic_report: dict[str, object],
) -> dict[str, object]:
    changes = []
    for relative in AUTHORIZED_ADDITIONS:
        path = snapshot_root / relative
        changes.append(
            {
                "path": relative,
                "operation": "ADD",
                "before_sha256": None,
                "after_sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    artifacts = []
    for path in sorted(return_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(return_root).as_posix()
        if relative in {
            "P4_R1A_REPAIR_MANIFEST.json",
            "FILE_SHA256SUMS.txt",
        }:
            continue
        artifacts.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "media_type": media_type(path),
            }
        )

    run_a_manifest = run_a / "ARTIFACT_MANIFEST.json"
    run_b_manifest = run_b / "ARTIFACT_MANIFEST.json"
    tests = junit_counts(junit_path)
    tests["junit_sha256"] = sha256_file(junit_path)

    return {
        "schema_version": "p4-r1a-repair-return-v1",
        "task_id": "P4-R1A_CPU_CONTRACT_PREFLIGHT",
        "parent_snapshot": {
            "name": parent_zip.name,
            "sha256": sha256_file(parent_zip),
            "size_bytes": parent_zip.stat().st_size,
            "entries": 145,
            "strict_archive_profile_passed": False,
            "strict_profile_failure": "windows_path_separators",
        },
        "source_control": {
            "git_commit": None,
            "binding_method": "parent_archive_sha256_plus_per_file_sha256",
        },
        "scientific_state": {
            "verdict": "REPAIR_THEN_RUN",
            "experiment_status": "NOT_RUN",
            "g1_source_identity": "NOT_RUN",
            "g2_task_disjoint_leakage": "NOT_RUN",
            "core_gate": "NOT_RUN",
            "short_rlvr": "NOT_RUN",
            "full_rlvr": "NOT_RUN",
        },
        "authorization": {
            "gpu": False,
            "model_or_tokenizer_weights": False,
            "model_forward": False,
            "gradient": False,
            "optimizer_step": False,
            "training": False,
            "core_run": False,
            "short_rlvr": False,
            "full_rlvr": False,
        },
        "synthetic_fixture": {
            "contract_only": True,
            "scientific_evidence": False,
            "acceptance_result": "PASS",
        },
        "changes": changes,
        "existing_files_modified": scope_report["existing_files_modified"],
        "scope_audit_passed": scope_report["passed"],
        "reused_infrastructure": [
            {"path": path, "sha256": digest, "use": use}
            for path, digest, use in REUSED_INFRASTRUCTURE
        ],
        "commands": receipts,
        "tests": tests,
        "deterministic_rebuild": {
            "run_a_manifest_sha256": sha256_file(run_a_manifest),
            "run_b_manifest_sha256": sha256_file(run_b_manifest),
            "byte_identical": deterministic_report["byte_identical"],
        },
        "artifacts": artifacts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", required=True, type=Path)
    parser.add_argument("--parent-zip", required=True, type=Path)
    parser.add_argument("--run-a", required=True, type=Path)
    parser.add_argument("--run-b", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--scope-report", required=True, type=Path)
    parser.add_argument("--deterministic-report", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--return-root", required=True, type=Path)
    args = parser.parse_args()

    snapshot_root = args.snapshot_root.resolve(strict=True)
    parent_zip = args.parent_zip.resolve(strict=True)
    run_a = args.run_a.resolve(strict=True)
    run_b = args.run_b.resolve(strict=True)
    evidence_root = args.evidence_root.resolve(strict=True)
    scope_report_path = args.scope_report.resolve(strict=True)
    deterministic_report_path = args.deterministic_report.resolve(strict=True)
    junit_path = args.junit.resolve(strict=True)
    return_root = args.return_root.resolve()

    if return_root.exists():
        raise FileExistsError(f"refusing to overwrite {return_root}")
    return_root.mkdir(parents=True)

    for relative in AUTHORIZED_ADDITIONS:
        copy_file(
            snapshot_root / relative,
            return_root / "added" / relative,
        )
    copy_tree(run_a, return_root / "evidence" / "run_a")
    copy_tree(run_b, return_root / "evidence" / "run_b")
    copy_tree(evidence_root, return_root / "evidence" / "receipts")

    scope_report = json_load(scope_report_path)
    deterministic_report = json_load(deterministic_report_path)
    receipt_paths = sorted(evidence_root.glob("*_receipt.json"))
    receipts = [json_load(path) for path in receipt_paths]

    manifest = build_manifest(
        snapshot_root=snapshot_root,
        parent_zip=parent_zip,
        return_root=return_root,
        run_a=run_a,
        run_b=run_b,
        receipts=receipts,
        junit_path=junit_path,
        scope_report=scope_report,
        deterministic_report=deterministic_report,
    )
    manifest_path = return_root / "P4_R1A_REPAIR_MANIFEST.json"
    write_json(manifest_path, manifest)

    manifest_lines = []
    for path in sorted(return_root.rglob("*")):
        if (
            not path.is_file()
            or path.relative_to(return_root).as_posix() == "FILE_SHA256SUMS.txt"
        ):
            continue
        manifest_lines.append(
            f"{sha256_file(path)}  {path.relative_to(return_root).as_posix()}"
        )
    (return_root / "FILE_SHA256SUMS.txt").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
