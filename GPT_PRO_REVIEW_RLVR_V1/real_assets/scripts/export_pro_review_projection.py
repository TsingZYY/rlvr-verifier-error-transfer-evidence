#!/usr/bin/env python3
"""Export upload-compatible Markdown projections of the CPU asset release.

ChatGPT's file uploader may reject JSON and ZIP files.  This utility preserves
the exact UTF-8/ASCII bytes of the most review-relevant files in Markdown,
records the original file/member length and SHA-256 for every section, and
labels the only sampled section explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Iterable, Sequence


PROTOCOL = "P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json"
PREREG = "RANDOMIZATION_MODEL_ENV_PREREG_V1.json"
SOURCE_ZIP = "REAL_SOURCE_STACK_BUNDLE_V1.zip"
VERIFIER_ZIP = "VERIFIER_G1_CPU_BUNDLE_V1.zip"
TARGET_ZIP = "TARGET_G2_REAL_BUNDLE_V1.zip"
DELIVERABLES = (PROTOCOL, SOURCE_ZIP, VERIFIER_ZIP, TARGET_ZIP, PREREG)

PROJECTION_A = "PRO_UPLOAD_A_CPU_PROTOCOL_AND_PROVENANCE_V5.md"
PROJECTION_B = "PRO_UPLOAD_B_CPU_ROWS_AND_REWARD_SAMPLE_V5.md"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_text(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-UTF-8 review member {label}: {exc}") from exc


def exact_section(
    *,
    label: str,
    value: bytes,
    language: str,
) -> str:
    text = decode_text(value, label)
    if not text.endswith("\n"):
        raise ValueError(f"review text is not LF-terminated: {label}")
    return (
        f"## Exact complete content: `{label}`\n\n"
        f"- completeness: `FULL_EXACT_BYTES`\n"
        f"- byte_length: `{len(value)}`\n"
        f"- sha256: `{sha256(value)}`\n\n"
        f"~~~~{language}\n{text}~~~~\n\n"
    )


def zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        raise ValueError(f"missing ZIP member: {name}") from exc


def verify_zip(archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        if archive.testzip() is not None:
            raise ValueError(f"ZIP CRC failure: {archive_path}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate ZIP member: {archive_path}")


def release_manifest(release_root: Path) -> dict[str, object]:
    actual = {path.name for path in release_root.iterdir()}
    if actual != set(DELIVERABLES):
        raise ValueError(
            "release root must contain exactly the five frozen deliverables"
        )
    result: dict[str, object] = {}
    for filename in DELIVERABLES:
        value = (release_root / filename).read_bytes()
        result[filename] = {
            "byte_length": len(value),
            "sha256": sha256(value),
        }
    return result


def select_reward_rows(value: bytes) -> tuple[bytes, dict[str, object]]:
    lines = value.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise ValueError("reward manifest is not non-empty LF JSONL")
    selected: list[bytes] = []
    seen: set[tuple[str, str]] = set()
    selected_ids: list[str] = []
    for line in lines:
        row = json.loads(line)
        key = (row["mapping_stack_id"], row["split_role"])
        if key in seen:
            continue
        seen.add(key)
        selected.append(line)
        selected_ids.append(row["row_id"])
    expected = 8 * 3
    if len(selected) != expected:
        raise ValueError(
            f"expected {expected} stack-by-split reward samples, got "
            f"{len(selected)}"
        )
    return b"".join(selected), {
        "sampling_rule": (
            "first canonical input-order row for every "
            "(mapping_stack_id, split_role)"
        ),
        "full_row_count": len(lines),
        "sample_row_count": len(selected),
        "selected_row_ids": selected_ids,
        "full_member_byte_length": len(value),
        "full_member_sha256": sha256(value),
        "sample_bytes_sha256": sha256(b"".join(selected)),
    }


def json_block(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )


def write_projection_a(
    release_root: Path,
    output_path: Path,
    manifest: dict[str, object],
) -> None:
    sections: list[str] = [
        "# GPT Pro CPU asset audit projection A: protocol and provenance\n\n",
        "This is an upload-compatible review projection, not a scientific "
        "deliverable. ChatGPT rejected direct JSON/ZIP uploads. Every section "
        "below contains complete exact UTF-8 bytes and binds them to the "
        "original deliverable or ZIP member by byte length and SHA-256. The "
        "five original deliverables remain the authority.\n\n",
        "## Frozen outer deliverables\n\n",
        "~~~~json\n",
        json_block(manifest),
        "\n~~~~\n\n",
    ]
    sections.append(
        exact_section(
            label=PROTOCOL,
            value=(release_root / PROTOCOL).read_bytes(),
            language="json",
        )
    )
    sections.append(
        exact_section(
            label=PREREG,
            value=(release_root / PREREG).read_bytes(),
            language="json",
        )
    )

    selections = {
        SOURCE_ZIP: (
            "REAL_SOURCE_STACK_BUNDLE_MANIFEST_V1.json",
            "REAL_MAPPING_STACKS_V1.jsonl",
            "src/controlled_tasks.py",
            "provenance/NATIVE_CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
            "provenance/NATIVE_RANDOMIZATION_MODEL_ENV_PREREG_V1.json",
        ),
        VERIFIER_ZIP: (
            "VERIFIER_G1_CPU_BUNDLE_MANIFEST_V1.json",
            "G1_OPPORTUNITY_AUDIT_V1.json",
            "CPU_VALIDATION_REPORT_V1.json",
        ),
        TARGET_ZIP: (
            "TARGET_G2_REAL_BUNDLE_MANIFEST_V1.json",
            "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
            "TARGET_AUDIT_SEAL_RECEIPT.json",
        ),
    }
    for zip_name, members in selections.items():
        sections.append(f"# Exact members from `{zip_name}`\n\n")
        with zipfile.ZipFile(release_root / zip_name, "r") as archive:
            for member in members:
                language = (
                    "python"
                    if member.endswith(".py")
                    else "jsonl"
                    if member.endswith(".jsonl")
                    else "json"
                )
                sections.append(
                    exact_section(
                        label=f"{zip_name}!{member}",
                        value=zip_member(archive, member),
                        language=language,
                    )
                )
    output_path.write_text("".join(sections), encoding="utf-8", newline="\n")


def write_projection_b(
    release_root: Path,
    output_path: Path,
    manifest: dict[str, object],
) -> None:
    sections: list[str] = [
        "# GPT Pro CPU asset audit projection B: rows and reward sample\n\n",
        "This is an upload-compatible review projection, not a scientific "
        "deliverable. It contains every source bundle row, every target "
        "calibration row, every sealed target audit row, and a deterministic "
        "24-row verifier sample covering every mapping stack and split. The "
        "full verifier member is bound by SHA-256 and was exhaustively checked "
        "locally; it is the only sampled section here.\n\n",
        "## Frozen outer deliverables\n\n",
        "~~~~json\n",
        json_block(manifest),
        "\n~~~~\n\n",
    ]

    complete_members = (
        (
            SOURCE_ZIP,
            "REAL_SOURCE_BUNDLES_V1.jsonl",
        ),
        (
            TARGET_ZIP,
            "TARGET_CALIBRATION_REAL_V1.jsonl",
        ),
        (
            TARGET_ZIP,
            "TARGET_AUDIT_REAL_V1.jsonl",
        ),
    )
    for zip_name, member in complete_members:
        with zipfile.ZipFile(release_root / zip_name, "r") as archive:
            sections.append(
                exact_section(
                    label=f"{zip_name}!{member}",
                    value=zip_member(archive, member),
                    language="jsonl",
                )
            )

    with zipfile.ZipFile(release_root / VERIFIER_ZIP, "r") as archive:
        full_reward = zip_member(archive, "VERIFIER_REWARD_MANIFEST_V1.jsonl")
    sample, receipt = select_reward_rows(full_reward)
    sections.extend(
        (
            "## Deterministic verifier reward sample\n\n",
            "- completeness: `STRATIFIED_SAMPLE_ONLY`\n",
            "- omitted rows remain bound by the full member SHA-256 and by the "
            "bundle manifest in projection A\n\n",
            "~~~~json\n",
            json_block(receipt),
            "\n~~~~\n\n",
            f"~~~~jsonl\n{decode_text(sample, 'reward sample')}~~~~\n\n",
        )
    )
    output_path.write_text("".join(sections), encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    release_root = args.release_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for zip_name in (SOURCE_ZIP, VERIFIER_ZIP, TARGET_ZIP):
        verify_zip(release_root / zip_name)
    manifest = release_manifest(release_root)
    outputs = (
        output_dir / PROJECTION_A,
        output_dir / PROJECTION_B,
    )
    for path in outputs:
        if path.exists():
            raise ValueError(f"refusing to overwrite projection: {path}")
    write_projection_a(release_root, outputs[0], manifest)
    write_projection_b(release_root, outputs[1], manifest)
    result = {
        path.name: {
            "byte_length": path.stat().st_size,
            "sha256": sha256(path.read_bytes()),
        }
        for path in outputs
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
