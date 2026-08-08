"""Model-free, fail-closed audit for an R11/R12 execution release.

This module deliberately uses only the Python standard library.  It does not
import an experiment runner, ML framework, tokenizer, or model package.  The
audit is about release custody and dependency closure, not experiment output.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


AUDIT_SCHEMA = "r12-release-closure-audit-r1"
MEMBER_MANIFEST_SCHEMA = "r12-release-member-manifest-r1"
DEFAULT_MEMBER_MANIFEST = "R12_RELEASE_MEMBER_MANIFEST_R1.json"
EXPECTED_MODEL_REVISION = "a10cc1512eabd3dde888204e902eca88bddb4951"
EXPECTED_STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
)
EXPECTED_R11_CELLS = tuple(
    f"{stack}|{arm}|{replicate}"
    for stack in EXPECTED_STACKS
    for arm in ("BUG", "GOLD_ONLY")
    for replicate in ("A", "B")
)
HASH_RE = re.compile(r"[0-9a-f]{64}")
PROHIBITED_ARCHIVE_PARTS = {"__pycache__", ".cache", ".pytest_cache"}


class ReleaseClosureError(RuntimeError):
    """Raised for malformed audit inputs rather than an ordinary failed check."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseClosureError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def strict_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ReleaseClosureError(f"non-finite JSON token in {label}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseClosureError(f"invalid UTF-8 JSON in {label}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseClosureError(f"expected JSON object in {label}")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finding(
    findings: list[dict[str, Any]],
    *,
    code: str,
    passed: bool,
    summary: str,
    evidence: Any,
) -> None:
    findings.append(
        {
            "code": code,
            "status": "PASS" if passed else "FAIL",
            "blocking": not passed,
            "summary": summary,
            "evidence": evidence,
        }
    )


def _safe_member_name(name: str) -> tuple[bool, str]:
    if not name or "\x00" in name:
        return False, "empty_or_nul"
    if "\\" in name:
        return False, "backslash_separator"
    path = PurePosixPath(name)
    if path.is_absolute():
        return False, "absolute_path"
    if any(part in {"", ".", ".."} for part in path.parts):
        return False, "non_canonical_component"
    if path.parts and ":" in path.parts[0]:
        return False, "drive_or_scheme_prefix"
    if path.as_posix() != name.rstrip("/"):
        return False, "non_canonical_posix_path"
    return True, "portable_posix"


def _prohibited_member_reason(name: str) -> str | None:
    lowered = name.lower().rstrip("/")
    parts = {part.lower() for part in PurePosixPath(lowered).parts}
    if lowered.endswith((".pyc", ".pyo")):
        return "compiled_python"
    if parts.intersection(PROHIBITED_ARCHIVE_PARTS):
        return "cache_directory"
    if lowered.endswith((".tmp", ".bak", "~")):
        return "temporary_or_backup"
    return None


def read_archive(archive_path: Path) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ReleaseClosureError(f"archive is not a regular file: {archive_path}")
    members: dict[str, bytes] = {}
    path_issues: list[dict[str, str]] = []
    casefold_names: dict[str, str] = {}
    with zipfile.ZipFile(archive_path, "r") as archive:
        bad_crc = archive.testzip()
        if bad_crc is not None:
            raise ReleaseClosureError(f"ZIP CRC failure: {bad_crc}")
        for info in archive.infolist():
            name = info.filename
            safe, reason = _safe_member_name(name)
            if not safe:
                path_issues.append({"member": name, "reason": reason})
            canonical = name.rstrip("/")
            if info.is_dir():
                continue
            if canonical in members:
                path_issues.append({"member": name, "reason": "duplicate_member"})
                continue
            folded = canonical.casefold()
            if folded in casefold_names and casefold_names[folded] != canonical:
                path_issues.append(
                    {
                        "member": name,
                        "reason": f"casefold_collision_with:{casefold_names[folded]}",
                    }
                )
            casefold_names[folded] = canonical
            members[canonical] = archive.read(info)
    return members, path_issues


def _get_json_member(members: dict[str, bytes], name: str) -> dict[str, Any]:
    if name not in members:
        raise ReleaseClosureError(f"required archive member missing: {name}")
    return strict_json_bytes(members[name], name)


def audit_member_manifest(
    members: dict[str, bytes], manifest_member: str, findings: list[dict[str, Any]]
) -> None:
    if manifest_member not in members:
        _finding(
            findings,
            code="MEMBER_MANIFEST_PRESENT_AND_EXACT",
            passed=False,
            summary="A complete hash manifest for archive members is required.",
            evidence={"missing_member": manifest_member},
        )
        return
    manifest = strict_json_bytes(members[manifest_member], manifest_member)
    declared = manifest.get("member_sha256_by_path")
    schema_ok = (
        set(manifest) == {"schema_version", "member_sha256_by_path"}
        and manifest.get("schema_version") == MEMBER_MANIFEST_SCHEMA
        and isinstance(declared, dict)
    )
    expected_names = set(members).difference({manifest_member})
    declared_names = set(declared) if isinstance(declared, dict) else set()
    hashes_ok = schema_ok and declared_names == expected_names
    mismatches: list[str] = []
    if hashes_ok:
        for name in sorted(expected_names):
            expected = declared[name]
            if not isinstance(expected, str) or HASH_RE.fullmatch(expected) is None:
                mismatches.append(name)
            elif expected != sha256_bytes(members[name]):
                mismatches.append(name)
    passed = bool(schema_ok and hashes_ok and not mismatches)
    _finding(
        findings,
        code="MEMBER_MANIFEST_PRESENT_AND_EXACT",
        passed=passed,
        summary="Member manifest must bind every non-self ZIP member and no extras.",
        evidence={
            "manifest_member": manifest_member,
            "schema_ok": schema_ok,
            "missing": sorted(expected_names.difference(declared_names)),
            "extra": sorted(declared_names.difference(expected_names)),
            "hash_mismatches": mismatches,
        },
    )


def _local_module_aliases(source_roots: Iterable[Path]) -> dict[str, list[Path]]:
    aliases: dict[str, list[Path]] = {}
    for root in source_roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in candidates:
            if path.suffix != ".py" or "__pycache__" in path.parts:
                continue
            names = {path.stem}
            if path.name == "__init__.py":
                names.add(path.parent.name)
            for name in names:
                aliases.setdefault(name, []).append(path.resolve())
    return aliases


def _imports_from_source(data: bytes, label: str) -> set[str]:
    try:
        tree = ast.parse(data.decode("utf-8"), filename=label)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ReleaseClosureError(f"cannot parse Python source {label}: {error}") from error
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def audit_source_closure(
    members: dict[str, bytes], source_roots: Sequence[Path], findings: list[dict[str, Any]]
) -> None:
    local = _local_module_aliases(source_roots)
    included_sources = {
        PurePosixPath(name).stem
        for name in members
        if name.lower().endswith(".py")
    }
    included_compiled = {
        PurePosixPath(name).name.split(".", 1)[0]
        for name in members
        if name.lower().endswith((".pyc", ".pyo"))
    }
    imported_by: dict[str, list[str]] = {}
    for name, data in members.items():
        if not name.lower().endswith(".py"):
            continue
        for module in _imports_from_source(data, name):
            imported_by.setdefault(module, []).append(name)
    missing: dict[str, Any] = {}
    for module in sorted(imported_by):
        if module in local and module not in included_sources:
            missing[module] = {
                "imported_by": sorted(imported_by[module]),
                "canonical_sources": sorted(str(path) for path in local[module]),
                "compiled_member_present": module in included_compiled,
            }
    _finding(
        findings,
        code="LOCAL_SOURCE_DEPENDENCY_CLOSURE",
        passed=not missing,
        summary="Every imported project-local module must be present as source, never only bytecode.",
        evidence={"missing_local_modules": missing},
    )


def _member_parent(member: str) -> PurePosixPath:
    return PurePosixPath(member).parent


def _join_member(parent: PurePosixPath, relative: Any, label: str) -> str:
    if not isinstance(relative, str) or PurePosixPath(relative).name != relative:
        raise ReleaseClosureError(f"{label} must be a bare member filename")
    return (parent / relative).as_posix()


def audit_cell_bindings(
    members: dict[str, bytes],
    index_member: str,
    master_member: str,
    findings: list[dict[str, Any]],
) -> None:
    try:
        index = _get_json_member(members, index_member)
        master = _get_json_member(members, master_member)
        index_parent = _member_parent(index_member)
        ordered = index.get("ordered_cell_ids")
        cell_files = index.get("cell_files")
        manifest_hashes = master.get("manifest_sha256_by_cell")
        arm_hashes = master.get("arm_contract_sha256_by_cell")
        if not all(
            isinstance(value, dict)
            for value in (cell_files, manifest_hashes, arm_hashes)
        ):
            raise ReleaseClosureError("cell/hash maps must be JSON objects")
        if ordered != list(EXPECTED_R11_CELLS):
            raise ReleaseClosureError("release index ordered cells are not canonical 32/32")
        expected = set(EXPECTED_R11_CELLS)
        for label, mapping in (
            ("index.cell_files", cell_files),
            ("master.manifest_sha256_by_cell", manifest_hashes),
            ("master.arm_contract_sha256_by_cell", arm_hashes),
        ):
            if set(mapping) != expected:
                raise ReleaseClosureError(f"{label} coverage is not canonical 32/32")
        if master.get("ordered_cell_ids") != list(EXPECTED_R11_CELLS):
            raise ReleaseClosureError("master ordered cells are not canonical 32/32")
        if index.get("master_inclusion_contract_sha256") != sha256_bytes(
            members[master_member]
        ):
            raise ReleaseClosureError("release index does not bind the master bytes")

        used_manifest_members: set[str] = set()
        used_arm_members: set[str] = set()
        errors: list[str] = []
        for cell in EXPECTED_R11_CELLS:
            files = cell_files[cell]
            if not isinstance(files, dict):
                errors.append(f"{cell}:file_map_not_object")
                continue
            try:
                manifest_member = _join_member(index_parent, files.get("manifest"), f"{cell}.manifest")
                arm_member = _join_member(index_parent, files.get("arm_contract"), f"{cell}.arm_contract")
                config_member = _join_member(index_parent, files.get("config"), f"{cell}.config")
            except ReleaseClosureError as error:
                errors.append(f"{cell}:{error}")
                continue
            if manifest_member in used_manifest_members:
                errors.append(f"{cell}:manifest_alias:{manifest_member}")
            if arm_member in used_arm_members:
                errors.append(f"{cell}:arm_alias:{arm_member}")
            used_manifest_members.add(manifest_member)
            used_arm_members.add(arm_member)
            if any(name not in members for name in (manifest_member, arm_member, config_member)):
                errors.append(f"{cell}:referenced_member_missing")
                continue
            manifest = strict_json_bytes(members[manifest_member], manifest_member)
            arm = strict_json_bytes(members[arm_member], arm_member)
            config = strict_json_bytes(members[config_member], config_member)
            stack, expected_arm, replicate = cell.split("|")
            if manifest.get("bridge_cell_id") != cell:
                errors.append(f"{cell}:manifest_internal_cell_mismatch")
            if arm.get("cell_id") != cell:
                errors.append(f"{cell}:arm_internal_cell_mismatch")
            if manifest.get("selected_mapping_stack_id") != stack:
                errors.append(f"{cell}:manifest_stack_mismatch")
            if manifest.get("arm") != expected_arm or manifest.get("replicate_id") != replicate:
                errors.append(f"{cell}:manifest_arm_or_replicate_mismatch")
            if arm.get("mapping_stack_id") != stack or arm.get("arm") != expected_arm or arm.get("replicate_id") != replicate:
                errors.append(f"{cell}:arm_key_content_mismatch")
            if config.get("data", {}).get("mapping_stack_id") != stack:
                errors.append(f"{cell}:config_stack_mismatch")
            manifest_sha = sha256_bytes(members[manifest_member])
            arm_sha = sha256_bytes(members[arm_member])
            if manifest_hashes.get(cell) != manifest_sha:
                errors.append(f"{cell}:master_manifest_hash_mismatch")
            if arm_hashes.get(cell) != arm_sha:
                errors.append(f"{cell}:master_arm_hash_mismatch")
            if manifest.get("bindings", {}).get("r11_arm_contract_sha256") != arm_sha:
                errors.append(f"{cell}:manifest_arm_hash_mismatch")
        _finding(
            findings,
            code="CELL_KEY_CONTENT_AND_UNIQUENESS_BINDING",
            passed=not errors,
            summary="Every release key must bind a unique manifest/arm file whose internal cell is the same key.",
            evidence={"checked_cells": len(EXPECTED_R11_CELLS), "errors": errors},
        )
    except ReleaseClosureError as error:
        _finding(
            findings,
            code="CELL_KEY_CONTENT_AND_UNIQUENESS_BINDING",
            passed=False,
            summary="Cell alias/key-content audit could not establish canonical 32/32 custody.",
            evidence={"error": str(error)},
        )


def _nested_model_revision(value: dict[str, Any]) -> Any:
    # R10/R11 named this block model_and_update_contract; R12 and the frozen
    # per-stack configs use model.  Do not recursively accept an arbitrary
    # provenance note containing the word "revision" as the execution binding.
    observed: list[Any] = []
    for key in ("model", "model_and_update_contract", "model_contract"):
        model = value.get(key)
        if isinstance(model, dict) and "revision" in model:
            observed.append(model.get("revision"))
    if len(observed) == 1:
        return observed[0]
    return None


def audit_revisions_and_manifests(
    members: dict[str, bytes],
    protocol_member: str,
    expected_revision: str,
    findings: list[dict[str, Any]],
) -> None:
    protocol_revision: Any = None
    if protocol_member in members:
        protocol_revision = _nested_model_revision(
            strict_json_bytes(members[protocol_member], protocol_member)
        )
    config_revisions: dict[str, Any] = {}
    platform_members: list[str] = []
    inventory_pollution: dict[str, list[str]] = {}
    for name, data in members.items():
        lowered = name.lower()
        if lowered.endswith("_config.json"):
            config_revisions[name] = _nested_model_revision(strict_json_bytes(data, name))
        if lowered.endswith("_manifest.json"):
            value = strict_json_bytes(data, name)
            observed = value.get("runtime_observed_at_manifest_build")
            if isinstance(observed, dict) and "platform" in observed:
                platform_members.append(name)
            inventory = value.get("bindings", {}).get("model_recursive_inventory")
            bad: list[str] = []
            if isinstance(inventory, list):
                for item in inventory:
                    path = item.get("path") if isinstance(item, dict) else None
                    if isinstance(path, str) and _prohibited_member_reason(path):
                        bad.append(path)
            if bad:
                inventory_pollution[name] = sorted(set(bad))

    revisions = {value for value in config_revisions.values()}
    revision_ok = (
        protocol_revision == expected_revision
        and revisions == {expected_revision}
        and bool(config_revisions)
    )
    _finding(
        findings,
        code="MODEL_REVISION_SINGLE_SOURCE_OF_TRUTH",
        passed=revision_ok,
        summary="Protocol and every frozen config must bind the verified exact model revision.",
        evidence={
            "expected_revision": expected_revision,
            "protocol_member": protocol_member,
            "protocol_revision": protocol_revision,
            "config_revision_values": sorted(str(value) for value in revisions),
            "config_count": len(config_revisions),
        },
    )
    _finding(
        findings,
        code="CANONICAL_MANIFEST_PLATFORM_INDEPENDENT",
        passed=not platform_members,
        summary="Host platform observations must be a separate attestation, not canonical manifest content.",
        evidence={"members_with_platform_field": platform_members},
    )
    _finding(
        findings,
        code="MODEL_INVENTORY_SEMANTIC_WHITELIST",
        passed=not inventory_pollution,
        summary="Model inventory must exclude download/cache metadata and other non-semantic files.",
        evidence={"polluted_manifests": inventory_pollution},
    )


def _contains_next_glob(source: bytes, label: str) -> list[int]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=label)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ReleaseClosureError(f"cannot parse freezer source {label}: {error}") from error
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "next"
        ):
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr in {"glob", "rglob"}
            for child in ast.walk(node)
        ):
            lines.append(node.lineno)
    return sorted(lines)


def audit_exact_globs(
    members: dict[str, bytes],
    freezer_member: str,
    r10_contract_dir: Path | None,
    findings: list[dict[str, Any]],
) -> None:
    unsafe_lines: list[int] = []
    if freezer_member in members:
        unsafe_lines = _contains_next_glob(members[freezer_member], freezer_member)
    _finding(
        findings,
        code="FREEZER_REQUIRES_EXACT_SINGLE_GLOB_MATCH",
        passed=freezer_member in members and not unsafe_lines,
        summary="Freezer code must count glob results and reject zero or multiple matches before selection.",
        evidence={"freezer_member": freezer_member, "unsafe_next_glob_lines": unsafe_lines},
    )
    if r10_contract_dir is None:
        _finding(
            findings,
            code="CURRENT_R10_GLOB_CARDINALITY",
            passed=False,
            summary="The current freezer input directory must be supplied for exact cardinality audit.",
            evidence={"r10_contract_dir": None},
        )
        return
    counts: dict[str, int] = {}
    if r10_contract_dir.is_dir() and not r10_contract_dir.is_symlink():
        for index in range(1, 9):
            for suffix in ("_config.json", "_determinism.json"):
                pattern = f"{index:02d}_*{suffix}"
                counts[pattern] = len(list(r10_contract_dir.glob(pattern)))
    passed = len(counts) == 16 and all(count == 1 for count in counts.values())
    _finding(
        findings,
        code="CURRENT_R10_GLOB_CARDINALITY",
        passed=passed,
        summary="Every source config/addendum glob must have exactly one current match.",
        evidence={"r10_contract_dir": str(r10_contract_dir), "match_counts": counts},
    )


def audit_release(
    *,
    archive_path: Path,
    source_roots: Sequence[Path],
    index_member: str,
    master_member: str,
    protocol_member: str,
    freezer_member: str,
    member_manifest: str = DEFAULT_MEMBER_MANIFEST,
    expected_model_revision: str = EXPECTED_MODEL_REVISION,
    r10_contract_dir: Path | None = None,
) -> dict[str, Any]:
    members, path_issues = read_archive(archive_path)
    findings: list[dict[str, Any]] = []
    _finding(
        findings,
        code="ZIP_PORTABLE_CANONICAL_PATHS",
        passed=not path_issues,
        summary="ZIP member names must be unique canonical POSIX-relative paths.",
        evidence={
            "member_count": len(members),
            "backslash_member_count": sum("\\" in name for name in members),
            "posix_subpath_member_count": sum("/" in name for name in members),
            "issues": path_issues,
        },
    )
    prohibited = {
        name: reason
        for name in sorted(members)
        if (reason := _prohibited_member_reason(name)) is not None
    }
    _finding(
        findings,
        code="NO_BYTECODE_OR_CACHE_POLLUTION",
        passed=not prohibited,
        summary="Release archives must contain source, never bytecode, cache, temp, or backup artifacts.",
        evidence={"prohibited_members": prohibited},
    )
    audit_member_manifest(members, member_manifest, findings)
    audit_source_closure(members, source_roots, findings)
    audit_cell_bindings(members, index_member, master_member, findings)
    audit_revisions_and_manifests(
        members, protocol_member, expected_model_revision, findings
    )
    audit_exact_globs(members, freezer_member, r10_contract_dir, findings)
    failed = [item["code"] for item in findings if item["status"] == "FAIL"]
    return {
        "schema_version": AUDIT_SCHEMA,
        "status": "PASS_RELEASE_CLOSURE" if not failed else "BLOCKED_RELEASE_CLOSURE",
        "run_eligible": not failed,
        "model_execution_performed": False,
        "model_or_tokenizer_imported": False,
        "archive": str(archive_path.resolve()),
        "archive_sha256": sha256_file(archive_path),
        "source_roots": [str(path.resolve()) for path in source_roots],
        "failed_checks": failed,
        "findings": findings,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, action="append", required=True)
    parser.add_argument("--index-member", required=True)
    parser.add_argument("--master-member", required=True)
    parser.add_argument("--protocol-member", required=True)
    parser.add_argument("--freezer-member", required=True)
    parser.add_argument("--member-manifest", default=DEFAULT_MEMBER_MANIFEST)
    parser.add_argument("--expected-model-revision", default=EXPECTED_MODEL_REVISION)
    parser.add_argument("--r10-contract-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_release(
        archive_path=args.archive,
        source_roots=args.source_root,
        index_member=args.index_member,
        master_member=args.master_member,
        protocol_member=args.protocol_member,
        freezer_member=args.freezer_member,
        member_manifest=args.member_manifest,
        expected_model_revision=args.expected_model_revision,
        r10_contract_dir=args.r10_contract_dir,
    )
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        if args.output.exists():
            raise ReleaseClosureError(f"refusing to overwrite audit output: {args.output}")
        args.output.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0 if report["run_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
