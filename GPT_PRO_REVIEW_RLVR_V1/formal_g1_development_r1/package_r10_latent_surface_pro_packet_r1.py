"""Build a deterministic, model-free GPT Pro review packet."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
FORMAL = Path(__file__).resolve().parent
OUTPUT = FORMAL / "R10_LATENT_SURFACE_PRO_PACKET_R1.zip"
RESULTS = PROJECT / "authorized_runs" / "r10_devcal_20260804T130238Z" / "results"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def add_file(files: dict[str, Path], member: str, path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing packet input: {path}")
    if "\\" in member or member.startswith("/") or ".." in Path(member).parts:
        raise RuntimeError(f"unsafe member path: {member}")
    if member in files:
        raise RuntimeError(f"duplicate member path: {member}")
    files[member] = path


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite existing packet: {OUTPUT}")
    files: dict[str, Path] = {}
    for name in (
        "R10_LATENT_SURFACE_PRO_REVIEW_REQUEST_R1.md",
        "R10_LATENT_VS_SURFACE_DIAGNOSTIC_PROTOCOL_R1.json",
        "analyze_r10_latent_vs_surface_r1.py",
        "test_analyze_r10_latent_vs_surface_r1.py",
        "R10_LATENT_VS_SURFACE_DIAGNOSTIC_RESULT_R1.json",
        "R12_IDENTITY_SWITCH_PILOT_PROTOCOL_DRAFT_R3.json",
        "R12_MINIMUM_PILOT_IMPLEMENTATION_AUDIT_R1.md",
    ):
        add_file(files, f"formal_g1_development_r1/{name}", FORMAL / name)
    add_file(
        files,
        "real_assets/build_r4_a/REAL_MAPPING_STACKS_V1.jsonl",
        PROJECT / "real_assets" / "build_r4_a" / "REAL_MAPPING_STACKS_V1.jsonl",
    )
    add_file(
        files,
        "real_assets/build_r4_a/TARGET_CALIBRATION_REAL_V1.jsonl",
        PROJECT / "real_assets" / "build_r4_a" / "TARGET_CALIBRATION_REAL_V1.jsonl",
    )
    result_dirs = sorted(path for path in RESULTS.iterdir() if path.is_dir())
    if len(result_dirs) != 8:
        raise RuntimeError(f"expected eight result directories, found {len(result_dirs)}")
    for result_dir in result_dirs:
        for replicate in ("A", "B"):
            path = result_dir / replicate / "result.json"
            add_file(
                files,
                f"authorized_runs/r10_devcal_20260804T130238Z/results/{result_dir.name}/{replicate}/result.json",
                path,
            )

    manifest = {
        "schema_version": "r10-latent-surface-pro-packet-manifest-r1",
        "model_files_included": False,
        "member_count_excluding_manifest": len(files),
        "members": {
            member: {
                "sha256": sha256(path.read_bytes()),
                "size_bytes": len(path.read_bytes()),
            }
            for member, path in sorted(files.items())
        },
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    timestamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for member, path in sorted(files.items()):
            info = zipfile.ZipInfo(member, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo("PACKET_MEMBER_MANIFEST_R1.json", timestamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest_bytes, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    with zipfile.ZipFile(OUTPUT, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
        names = archive.namelist()
        if len(names) != len(set(names)) or any("\\" in name for name in names):
            raise RuntimeError("non-portable or duplicate ZIP members")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": sha256(OUTPUT.read_bytes()),
                "member_count": len(files) + 1,
                "model_files_included": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
