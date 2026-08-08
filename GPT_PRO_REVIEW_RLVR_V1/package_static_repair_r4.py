"""Create a deterministic, portable GPT Pro static-repair review packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import zipfile


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "GPT_PRO_STATIC_REPAIR_PACKET_R4.zip"
FIXED_TIME = (2026, 8, 4, 0, 0, 0)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def add_file(members: dict[str, bytes], relative: str) -> None:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"missing or unsafe packet member: {relative}")
    normalized = Path(relative).as_posix()
    if normalized.startswith("/") or ".." in Path(normalized).parts:
        raise RuntimeError(f"unsafe packet path: {relative}")
    members[normalized] = path.read_bytes()


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite {OUTPUT}")
    members: dict[str, bytes] = {}
    for relative in (
        "commitment_core.py",
        "test_portable_packet_r4.py",
        "25_PRO_REVIEWER_5_STATIC_REPAIR_AUDIT_RAW_RESPONSE.md",
        "26_CPU_STATIC_REPAIR_REPORT_R4.md",
        "27_GPT_PRO_STATIC_REAUDIT_REQUEST_R4.md",
        "package_static_repair_r4.py",
        "mvp_same_source_v1/MVP_CONFIG_V1.json",
        "mvp_same_source_v1/MVP_DETERMINISM_ADDENDUM_R2.json",
        "mvp_same_source_v1/mvp_static_contract.py",
        "mvp_same_source_v1/run_same_source_mvp.py",
        "mvp_same_source_v1/validate_mvp_replicates_r3.py",
        "mvp_same_source_v1/freeze_eight_stack_contract.py",
        "mvp_same_source_v1/freeze_result_anchor.py",
        "mvp_same_source_v1/audit_eight_stack_static.py",
        "mvp_same_source_v1/test_mvp_static_contract.py",
        "mvp_same_source_v1/test_validate_mvp_replicates_r3.py",
        "mvp_same_source_v1/EIGHT_STACK_STATIC_AUDIT_R4_V2.json",
        "real_assets/P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json",
        "real_assets/src/controlled_tasks.py",
        "real_assets/scripts/build_controlled_assets.py",
        "real_assets/scripts/validate_native_controlled_assets.py",
        "real_assets/tests/test_native_controlled_assets.py",
        "real_assets/VALIDATION_R4_PORTABLE_A.json",
        "real_assets/VALIDATION_R4_PORTABLE_B.json",
        "real_assets/BUILD_R4_DETERMINISM.json",
        "real_assets/build_r4_a/CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
        "real_assets/build_r4_a/REAL_MAPPING_STACKS_V1.jsonl",
        "real_assets/build_r4_a/REAL_SOURCE_BUNDLES_V1.jsonl",
        "real_assets/build_r4_a/TARGET_CALIBRATION_REAL_V1.jsonl",
        "real_assets/build_r4_a/TARGET_AUDIT_SEAL_RECEIPT.json",
    ):
        add_file(members, relative)
    frozen = ROOT / "mvp_same_source_v1" / "frozen_eight_stack_r4_v2"
    for path in sorted(frozen.iterdir(), key=lambda value: value.name):
        add_file(
            members,
            "mvp_same_source_v1/frozen_eight_stack_r4_v2/" + path.name,
        )
    payload_manifest = {
        "schema_version": "gpt-pro-static-repair-packet-manifest-r4",
        "status": "CPU_STATIC_REPAIR_CANDIDATE_AWAITING_PRO_REAUDIT",
        "scientific_evidence": False,
        "formal_experiment": False,
        "model_execution_performed": False,
        "model_actions_authorized": False,
        "real_model_artifact_included": False,
        "real_model_omission_reason": "727 MB local model is represented by the recursive exact inventory in every manifest; portable tests use a synthetic inventory and no model framework.",
        "current_audit_rows_included": False,
        "current_audit_eligibility": "NOT_ELIGIBLE_AS_HIDDEN_EVALUATION",
        "payload_file_count": len(members),
        "payload": {
            name: {"size": len(value), "sha256": sha256_bytes(value)}
            for name, value in sorted(members.items())
        },
    }
    members["PACKET_MANIFEST_R4.json"] = (
        json.dumps(payload_manifest, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in sorted(members.items()):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, value)
    with zipfile.ZipFile(OUTPUT, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("packet CRC validation failed")
        if archive.namelist() != sorted(members):
            raise RuntimeError("packet member ordering drift")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": sha256_bytes(OUTPUT.read_bytes()),
                "member_count": len(members),
                "size": OUTPUT.stat().st_size,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
