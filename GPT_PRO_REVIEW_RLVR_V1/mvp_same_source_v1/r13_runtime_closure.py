"""Static full-byte runtime closure builder/validator for R13.

This standard-library-only tool inventories explicitly allowed runtime roots.
It never imports ML packages, loads model/tokenizer bytes, or authorizes a run.
Even a complete static byte inventory remains run-ineligible until an external
broker proves a content-addressed, read-only launch plus the native/OS load
graph.  The current mixed R13 environment is therefore a reference input, not
a production closure.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Sequence
import zipfile


SPEC_SCHEMA = "r13-runtime-byte-closure-build-spec-r1"
SPEC_STATUS = "STATIC_BUILD_SPEC_NOT_AUTHORIZATION"
MANIFEST_SCHEMA = "r13-runtime-byte-closure-manifest-r1"
MANIFEST_STATUS = (
    "STATIC_BYTE_INVENTORY_COMPLETE_EXTERNAL_IMMUTABILITY_REQUIRED"
)
EXPECTED_PYTHON_VERSION = "3.10.20"
HASH_RE = re.compile(r"[0-9a-f]{64}")
ROOT_ID_RE = re.compile(r"[a-z][a-z0-9_-]{2,63}")
REQUIRED_LAUNCH_FLAGS = {
    "isolated": 1,
    "ignore_environment": 1,
    "no_user_site": 1,
    "dont_write_bytecode": 1,
    "no_site": 1,
}
FORBIDDEN_DIRECTORY_COMPONENTS = {
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
FORBIDDEN_FILE_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_IMPORT_HOOK_BASENAMES = {
    "sitecustomize.py",
    "usercustomize.py",
}
FORBIDDEN_IMPORT_HOOK_SUFFIXES = {".pth", ".egg-link"}
NATIVE_SUFFIXES = {".dll", ".exe", ".pyd", ".so", ".dylib"}
ML_MODULE_PREFIXES = ("torch", "transformers", "peft", "tokenizers", "accelerate")
FIXED_EXTERNAL_BLOCKERS = (
    "EXTERNAL_CONTENT_ADDRESSED_LAUNCH_RECEIPT_REQUIRED",
    "IMMUTABLE_READ_ONLY_RUNTIME_IMAGE_NOT_ATTESTED",
    "NATIVE_DEPENDENCY_LOAD_GRAPH_NOT_PROVEN_CLOSED",
    "OS_KERNEL_GPU_DRIVER_BYTES_NOT_ATTESTED",
)

SPEC_KEYS = {
    "schema_version",
    "status",
    "run_eligible",
    "model_execution_authorized",
    "expected_python_version",
    "required_launch_flags",
    "roots",
    "interpreter_path",
    "release_entrypoint",
    "ordered_sys_path",
    "working_directory",
    "external_content_addressed_launch_receipt_required",
    "immutable_read_only_mount_required",
    "native_dependency_graph_attestation_required",
    "os_driver_attestation_required",
}
ROOT_SPEC_KEYS = {"root_id", "role", "path"}
FILE_KEYS = {
    "relative_path",
    "size_bytes",
    "sha256",
    "archive_member_count",
    "archive_member_name_commitment_sha256",
    "archive_top_level_import_providers",
}
ROOT_KEYS = {
    "root_id",
    "role",
    "path",
    "directory_count",
    "file_count",
    "total_size_bytes",
    "directories",
    "files",
    "tree_sha256",
    "content_address",
}
PATH_BINDING_KEYS = {"absolute_path", "root_id", "relative_path", "kind"}
PROVIDER_KEYS = {"name", "sys_path_index", "root_id", "source"}
NATIVE_KEYS = {"root_id", "relative_path", "size_bytes", "sha256"}
OBSERVATION_KEYS = {
    "python_version",
    "interpreter_path",
    "launch_flags",
    "ordered_sys_path",
    "working_directory",
    "ml_modules_present",
    "matches_frozen_spec",
}
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "run_eligible",
    "model_execution_authorized",
    "model_execution_performed",
    "model_action_count",
    "build_spec_sha256",
    "expected_python_version",
    "required_launch_flags",
    "roots",
    "interpreter",
    "release_entrypoint",
    "ordered_sys_path",
    "working_directory",
    "top_level_import_providers",
    "native_artifacts",
    "static_byte_inventory_complete",
    "all_allowed_content_bytes_hashed",
    "directory_double_scan_passed",
    "shadow_imports_absent",
    "cwd_import_path_absent",
    "pyc_and_cache_absent",
    "full_dependency_content_hash_bound",
    "external_content_addressed_launch_verified",
    "immutable_read_only_mount_verified",
    "native_dependency_load_graph_verified",
    "os_driver_bytes_attested",
    "current_process_observation",
    "global_content_commitment_sha256",
    "blockers",
}


class RuntimeClosureError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeClosureError(message)


def _assert_json_tree(value: Any, label: str = "root") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        require(math.isfinite(value), f"non-finite value at {label}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_json_tree(child, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        require(all(isinstance(key, str) for key in value), f"non-string key at {label}")
        for key, child in value.items():
            _assert_json_tree(child, f"{label}.{key}")
        return
    raise RuntimeClosureError(f"unsupported JSON value at {label}")


def canonical_json_bytes(value: Any) -> bytes:
    _assert_json_tree(value)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise RuntimeClosureError("value is not canonical JSON") from error
    return (rendered + "\n").encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_reparse(stat_result: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(stat_result, "st_file_attributes", 0) & attribute)


def _identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def require_no_link_ancestors(path: Path, label: str) -> None:
    selected = path if path.is_absolute() else Path.cwd() / path
    cursor = selected
    while True:
        if os.path.lexists(cursor):
            metadata = os.lstat(cursor)
            require(
                not stat.S_ISLNK(metadata.st_mode) and not _is_reparse(metadata),
                f"{label} contains symlink/junction/reparse point: {cursor}",
            )
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def read_file_snapshot(path: Path, label: str) -> tuple[bytes, str, os.stat_result]:
    require_no_link_ancestors(path, label)
    try:
        path_before = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise RuntimeClosureError(f"cannot stat {label}: {path}") from error
    require(
        stat.S_ISREG(path_before.st_mode) and not _is_reparse(path_before),
        f"{label} is not a regular non-reparse file",
    )
    require(path_before.st_nlink == 1, f"{label} is hard-linked")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeClosureError(f"cannot open {label}: {path}") from error
    try:
        opened_before = os.fstat(descriptor)
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise RuntimeClosureError(f"cannot restat {label}: {path}") from error
    require(
        _identity(path_before)
        == _identity(opened_before)
        == _identity(opened_after)
        == _identity(path_after),
        f"{label} changed during same-descriptor snapshot",
    )
    require(
        opened_before.st_nlink == opened_after.st_nlink == path_after.st_nlink == 1,
        f"{label} hardlink count changed during snapshot",
    )
    raw = b"".join(blocks)
    require(len(raw) == opened_after.st_size, f"{label} size/read mismatch")
    return raw, sha256_bytes(raw), opened_after


def strict_json_loads(raw: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeClosureError(f"duplicate key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise RuntimeClosureError(f"non-finite JSON constant in {label}: {token}")

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeClosureError(f"invalid UTF-8 JSON: {label}") from error
    require(isinstance(value, dict), f"{label} must be one JSON object")
    _assert_json_tree(value, label)
    return value


def read_canonical_json(path: Path, label: str) -> dict[str, Any]:
    raw, _, _ = read_file_snapshot(path, label)
    value = strict_json_loads(raw, label)
    require(raw == canonical_json_bytes(value), f"non-canonical JSON: {label}")
    return value


def _absolute(value: Any, label: str, *, must_exist: bool = True) -> Path:
    require(isinstance(value, str) and value, f"{label} is not a path string")
    selected = Path(value)
    require(selected.is_absolute() and ".." not in selected.parts, f"{label} is not canonical absolute")
    require_no_link_ancestors(selected, label)
    try:
        resolved = selected.resolve(strict=must_exist)
    except OSError as error:
        raise RuntimeClosureError(f"cannot resolve {label}: {selected}") from error
    require_no_link_ancestors(resolved, label)
    return resolved


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _owner(path: Path, roots: list[dict[str, Any]], label: str) -> tuple[dict[str, Any], str]:
    matches: list[tuple[dict[str, Any], str]] = []
    for root in roots:
        root_path = Path(root["path"])
        try:
            relative = path.resolve(strict=False).relative_to(root_path)
        except ValueError:
            continue
        matches.append((root, "." if not relative.parts else relative.as_posix()))
    require(len(matches) == 1, f"{label} is not owned by exactly one frozen root")
    return matches[0]


def validate_build_spec(spec: dict[str, Any]) -> None:
    _assert_json_tree(spec, "runtime closure build spec")
    require(set(spec) == SPEC_KEYS, "runtime closure build-spec schema drift")
    require(spec["schema_version"] == SPEC_SCHEMA, "build-spec version drift")
    require(spec["status"] == SPEC_STATUS, "build-spec status drift")
    require(spec["run_eligible"] is False, "build spec self-authorizes execution")
    require(spec["model_execution_authorized"] is False, "build spec authorizes model execution")
    require(spec["expected_python_version"] == EXPECTED_PYTHON_VERSION, "Python version drift")
    require(
        spec["required_launch_flags"] == REQUIRED_LAUNCH_FLAGS
        and all(type(value) is int for value in spec["required_launch_flags"].values()),
        "launch flag contract drift",
    )
    for field in (
        "external_content_addressed_launch_receipt_required",
        "immutable_read_only_mount_required",
        "native_dependency_graph_attestation_required",
        "os_driver_attestation_required",
    ):
        require(spec[field] is True, f"build spec disables {field}")
    roots = spec["roots"]
    require(isinstance(roots, list) and roots, "build spec has no roots")
    ids: set[str] = set()
    paths: list[Path] = []
    for index, root in enumerate(roots):
        require(isinstance(root, dict) and set(root) == ROOT_SPEC_KEYS, f"root {index} schema drift")
        root_id = root["root_id"]
        require(isinstance(root_id, str) and ROOT_ID_RE.fullmatch(root_id) is not None, f"root {index} id drift")
        require(root_id not in ids, "duplicate root id")
        ids.add(root_id)
        require(isinstance(root["role"], str) and root["role"].strip(), f"root {index} role missing")
        selected = _absolute(root["path"], f"root {root_id}")
        require(selected.is_dir(), f"root {root_id} is not a directory")
        require_no_link_ancestors(selected, f"root {root_id}")
        paths.append(selected)
    for left_index, left in enumerate(paths):
        for right in paths[left_index + 1 :]:
            require(not _within(left, right) and not _within(right, left), "runtime roots overlap")
    normalized_roots = [
        {**root, "path": str(path)} for root, path in zip(roots, paths)
    ]
    interpreter = _absolute(spec["interpreter_path"], "interpreter")
    release = _absolute(spec["release_entrypoint"], "release entrypoint")
    require(interpreter.is_file(), "interpreter is not a file")
    require(release.is_file() and release.suffix.casefold() == ".py", "release entrypoint is not Python source")
    _owner(interpreter, normalized_roots, "interpreter")
    release_root, _ = _owner(release, normalized_roots, "release entrypoint")
    require(release_root["role"] == "release_code", "release entrypoint is not in release_code root")
    sys_paths = spec["ordered_sys_path"]
    require(isinstance(sys_paths, list) and sys_paths, "ordered sys.path missing")
    normalized_sys_paths: list[str] = []
    for index, value in enumerate(sys_paths):
        selected = _absolute(value, f"sys.path[{index}]")
        require(selected.is_dir() or selected.is_file(), f"sys.path[{index}] is not importable content")
        _owner(selected, normalized_roots, f"sys.path[{index}]")
        normalized_sys_paths.append(os.path.normcase(str(selected)))
    require(len(set(normalized_sys_paths)) == len(normalized_sys_paths), "duplicate sys.path entry")
    cwd = _absolute(spec["working_directory"], "working directory")
    require(cwd.is_dir(), "working directory is not a directory")
    require(not any(cwd.iterdir()), "working directory must be empty")
    for root in paths:
        require(
            cwd != root and not _within(cwd, root) and not _within(root, cwd),
            "working directory overlaps a frozen root",
        )
    for path in [_absolute(value, "sys.path entry") for value in sys_paths]:
        require(
            cwd != path and not _within(cwd, path) and not _within(path, cwd),
            "working directory overlaps import path",
        )


def _check_relative(relative: str, label: str) -> None:
    pure = PurePosixPath(relative)
    require(relative == pure.as_posix() and not pure.is_absolute() and ".." not in pure.parts, f"non-canonical relative path: {label}")
    require(all(":" not in part for part in pure.parts), f"alternate stream/device path forbidden: {label}")
    folded = {part.casefold() for part in pure.parts}
    require(not (folded & FORBIDDEN_DIRECTORY_COMPONENTS), f"cache directory forbidden: {label}")
    name = pure.name.casefold()
    require(PurePosixPath(name).suffix not in FORBIDDEN_FILE_SUFFIXES, f"bytecode forbidden: {label}")
    require(name not in FORBIDDEN_IMPORT_HOOK_BASENAMES, f"implicit import hook forbidden: {label}")
    require(not any(name.endswith(suffix) for suffix in FORBIDDEN_IMPORT_HOOK_SUFFIXES), f"path injection file forbidden: {label}")


def _tree_metadata(root: Path) -> dict[str, tuple[str, tuple[int, int, int, int]]]:
    result: dict[str, tuple[str, tuple[int, int, int, int]]] = {}
    seen_folded: set[str] = set()
    require_no_link_ancestors(root, "runtime root")
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_names.sort(key=str.casefold)
        file_names.sort(key=str.casefold)
        current_path = Path(current)
        for name, expected_kind in [
            *((name, "directory") for name in directory_names),
            *((name, "file") for name in file_names),
        ]:
            path = current_path / name
            metadata = os.lstat(path)
            require(not stat.S_ISLNK(metadata.st_mode) and not _is_reparse(metadata), f"symlink/junction/reparse entry forbidden: {path}")
            if expected_kind == "directory":
                require(stat.S_ISDIR(metadata.st_mode), f"non-directory encountered: {path}")
            else:
                require(stat.S_ISREG(metadata.st_mode), f"non-regular file encountered: {path}")
            relative = path.relative_to(root).as_posix()
            _check_relative(relative, relative)
            key = relative.casefold()
            require(key not in seen_folded, f"case-insensitive path collision: {relative}")
            seen_folded.add(key)
            result[relative] = (expected_kind, _identity(metadata))
    return result


def _archive_record(raw: bytes, relative: str) -> tuple[int, str | None, list[str]]:
    if PurePosixPath(relative).suffix.casefold() != ".zip":
        return 0, None, []
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise RuntimeClosureError(f"invalid import zip: {relative}") from error
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    provider_forms: dict[str, tuple[str, set[str]]] = {}
    with archive:
        for info in archive.infolist():
            name = info.filename
            require("\\" not in name, f"non-POSIX zip member: {relative}!{name}")
            pure = PurePosixPath(name)
            require(not pure.is_absolute() and ".." not in pure.parts and name == pure.as_posix(), f"escaping zip member: {relative}!{name}")
            if info.is_dir():
                continue
            _check_relative(name, f"{relative}!{name}")
            folded = name.casefold()
            require(folded not in seen, f"duplicate/case-colliding zip member: {relative}!{name}")
            seen.add(folded)
            mode = (info.external_attr >> 16) & 0xFFFF
            require(not stat.S_ISLNK(mode), f"zip symlink forbidden: {relative}!{name}")
            members.append({"name": name, "size_bytes": info.file_size, "crc32": info.CRC})
            first = pure.parts[0]
            if len(pure.parts) == 1 and pure.suffix.casefold() == ".py":
                provider = pure.stem
                form = pure.name
            elif len(pure.parts) > 1 and first.isidentifier():
                provider = first
                form = first + "/"
            else:
                continue
            if provider.isidentifier():
                folded_provider = provider.casefold()
                display, forms = provider_forms.setdefault(
                    folded_provider, (provider, set())
                )
                forms.add(form.casefold())
                require(
                    len(forms) == 1,
                    f"ambiguous provider inside import zip: {relative}!{display}",
                )
    providers = sorted((display for display, _ in provider_forms.values()), key=str.casefold)
    return len(members), sha256_bytes(canonical_json_bytes(members)), providers


def _scan_root(root_spec: dict[str, Any]) -> dict[str, Any]:
    root = Path(root_spec["path"]).resolve(strict=True)
    before = _tree_metadata(root)
    directories = sorted(
        (path for path, (kind, _) in before.items() if kind == "directory"),
        key=str.casefold,
    )
    files: list[dict[str, Any]] = []
    for relative in sorted(
        (path for path, (kind, _) in before.items() if kind == "file"),
        key=str.casefold,
    ):
        raw, digest, metadata = read_file_snapshot(root / Path(relative), f"runtime file {relative}")
        require(_identity(metadata) == before[relative][1], f"runtime file changed after enumeration: {relative}")
        member_count, member_commitment, providers = _archive_record(raw, relative)
        files.append(
            {
                "relative_path": relative,
                "size_bytes": len(raw),
                "sha256": digest,
                "archive_member_count": member_count,
                "archive_member_name_commitment_sha256": member_commitment,
                "archive_top_level_import_providers": providers,
            }
        )
    after = _tree_metadata(root)
    require(before == after, f"runtime root changed during directory double scan: {root}")
    tree_payload = {
        "root_id": root_spec["root_id"],
        "role": root_spec["role"],
        "directories": directories,
        "files": files,
    }
    tree_sha256 = sha256_bytes(canonical_json_bytes(tree_payload))
    return {
        "root_id": root_spec["root_id"],
        "role": root_spec["role"],
        "path": str(root),
        "directory_count": len(directories),
        "file_count": len(files),
        "total_size_bytes": sum(record["size_bytes"] for record in files),
        "directories": directories,
        "files": files,
        "tree_sha256": tree_sha256,
        "content_address": f"sha256:{tree_sha256}",
    }


def _path_binding(path: Path, roots: list[dict[str, Any]], label: str) -> dict[str, Any]:
    root, relative = _owner(path, roots, label)
    kind = "directory" if path.is_dir() else "file"
    return {
        "absolute_path": str(path.resolve(strict=True)),
        "root_id": root["root_id"],
        "relative_path": relative,
        "kind": kind,
    }


def _providers_for_directory(binding: dict[str, Any], root: dict[str, Any]) -> list[tuple[str, str]]:
    base = PurePosixPath(binding["relative_path"])
    prefix = () if binding["relative_path"] == "." else base.parts
    sources: dict[str, tuple[str, dict[str, str]]] = {}
    for relative in root["directories"]:
        parts = PurePosixPath(relative).parts
        if len(parts) != len(prefix) + 1 or parts[: len(prefix)] != prefix:
            continue
        provider = parts[-1]
        if provider.isidentifier():
            sources.setdefault(provider.casefold(), (provider, {}))[1]["directory"] = relative + "/"
    for record in root["files"]:
        parts = PurePosixPath(record["relative_path"]).parts
        if len(parts) <= len(prefix) or parts[: len(prefix)] != prefix:
            continue
        remainder = parts[len(prefix) :]
        first = remainder[0]
        provider: str | None = None
        if len(remainder) == 1:
            suffix = PurePosixPath(first).suffix.casefold()
            if suffix == ".py":
                provider = PurePosixPath(first).stem
            elif suffix in {".pyd", ".so"}:
                provider = first.split(".", 1)[0]
        elif first.isidentifier():
            provider = first
        if provider is not None and provider.isidentifier():
            form = "module" if len(remainder) == 1 else "directory"
            display, forms = sources.setdefault(provider.casefold(), (provider, {}))
            forms.setdefault(form, record["relative_path"])
    output: list[tuple[str, str]] = []
    for _, (provider, forms) in sorted(sources.items()):
        require(len(forms) == 1, f"ambiguous provider within import directory: {provider}")
        output.append((provider, next(iter(forms.values()))))
    return output


def _provider_records(
    sys_path_bindings: list[dict[str, Any]], roots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    roots_by_id = {root["root_id"]: root for root in roots}
    global_providers: dict[str, list[tuple[int, str, str, str]]] = {}
    for index, binding in enumerate(sys_path_bindings):
        root = roots_by_id[binding["root_id"]]
        if binding["kind"] == "directory":
            providers = _providers_for_directory(binding, root)
        else:
            selected = next(
                record
                for record in root["files"]
                if record["relative_path"] == binding["relative_path"]
            )
            providers = [
                (provider, f"{binding['relative_path']}!{provider}")
                for provider in selected["archive_top_level_import_providers"]
            ]
        seen_here: set[str] = set()
        for provider, source in providers:
            folded = provider.casefold()
            require(folded not in seen_here, f"ambiguous provider within sys.path[{index}]: {provider}")
            seen_here.add(folded)
            global_providers.setdefault(folded, []).append(
                (index, provider, binding["root_id"], source)
            )
    records: list[dict[str, Any]] = []
    for folded, occurrences in sorted(global_providers.items()):
        require(len(occurrences) == 1, f"shadow import provider across sys.path: {folded}")
        index, provider, root_id, source = occurrences[0]
        records.append(
            {
                "name": provider,
                "sys_path_index": index,
                "root_id": root_id,
                "source": source,
            }
        )
    return records


def _native_records(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "root_id": root["root_id"],
                "relative_path": record["relative_path"],
                "size_bytes": record["size_bytes"],
                "sha256": record["sha256"],
            }
            for root in roots
            for record in root["files"]
            if PurePosixPath(record["relative_path"]).suffix.casefold()
            in NATIVE_SUFFIXES
        ],
        key=lambda value: (value["root_id"], value["relative_path"].casefold()),
    )


def _process_observation(spec: dict[str, Any]) -> dict[str, Any]:
    flags = {
        "isolated": sys.flags.isolated,
        "ignore_environment": sys.flags.ignore_environment,
        "no_user_site": sys.flags.no_user_site,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "no_site": sys.flags.no_site,
    }
    version = ".".join(str(part) for part in sys.version_info[:3])
    interpreter = str(Path(sys.executable).resolve(strict=False))
    paths = [str(Path(value).resolve(strict=False)) for value in sys.path if value]
    cwd = str(Path.cwd().resolve())
    ml_present = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in ML_MODULE_PREFIXES)
    )
    expected_paths = [str(Path(value).resolve(strict=False)) for value in spec["ordered_sys_path"]]
    matches = (
        version == spec["expected_python_version"]
        and os.path.normcase(interpreter)
        == os.path.normcase(str(Path(spec["interpreter_path"]).resolve(strict=False)))
        and flags == REQUIRED_LAUNCH_FLAGS
        and [os.path.normcase(value) for value in paths]
        == [os.path.normcase(value) for value in expected_paths]
        and os.path.normcase(cwd)
        == os.path.normcase(str(Path(spec["working_directory"]).resolve(strict=False)))
        and not ml_present
    )
    return {
        "python_version": version,
        "interpreter_path": interpreter,
        "launch_flags": flags,
        "ordered_sys_path": paths,
        "working_directory": cwd,
        "ml_modules_present": ml_present,
        "matches_frozen_spec": matches,
    }


def _blockers(observation: dict[str, Any]) -> list[str]:
    values = list(FIXED_EXTERNAL_BLOCKERS)
    if not observation["matches_frozen_spec"]:
        values.append("CURRENT_PROCESS_DOES_NOT_MATCH_FROZEN_LAUNCH_SPEC")
    return values


def build_manifest(spec: dict[str, Any]) -> dict[str, Any]:
    validate_build_spec(spec)
    normalized_roots = [
        {**root, "path": str(Path(root["path"]).resolve(strict=True))}
        for root in spec["roots"]
    ]
    roots = [_scan_root(root) for root in normalized_roots]
    root_specs = [
        {"root_id": root["root_id"], "role": root["role"], "path": root["path"]}
        for root in roots
    ]
    interpreter = _path_binding(
        Path(spec["interpreter_path"]).resolve(strict=True), root_specs, "interpreter"
    )
    release = _path_binding(
        Path(spec["release_entrypoint"]).resolve(strict=True), root_specs, "release entrypoint"
    )
    sys_paths = [
        _path_binding(Path(value).resolve(strict=True), root_specs, f"sys.path[{index}]")
        for index, value in enumerate(spec["ordered_sys_path"])
    ]
    providers = _provider_records(sys_paths, roots)
    native = _native_records(roots)
    observation = _process_observation(spec)
    commitment_payload = {
        "roots": [
            {
                "root_id": root["root_id"],
                "role": root["role"],
                "tree_sha256": root["tree_sha256"],
            }
            for root in roots
        ],
        "interpreter": interpreter,
        "release_entrypoint": release,
        "ordered_sys_path": sys_paths,
        "top_level_import_providers": providers,
        "native_artifacts": native,
    }
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "status": MANIFEST_STATUS,
        "run_eligible": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "model_action_count": 0,
        "build_spec_sha256": sha256_bytes(canonical_json_bytes(spec)),
        "expected_python_version": spec["expected_python_version"],
        "required_launch_flags": REQUIRED_LAUNCH_FLAGS,
        "roots": roots,
        "interpreter": interpreter,
        "release_entrypoint": release,
        "ordered_sys_path": sys_paths,
        "working_directory": str(Path(spec["working_directory"]).resolve(strict=True)),
        "top_level_import_providers": providers,
        "native_artifacts": native,
        "static_byte_inventory_complete": True,
        "all_allowed_content_bytes_hashed": True,
        "directory_double_scan_passed": True,
        "shadow_imports_absent": True,
        "cwd_import_path_absent": True,
        "pyc_and_cache_absent": True,
        "full_dependency_content_hash_bound": False,
        "external_content_addressed_launch_verified": False,
        "immutable_read_only_mount_verified": False,
        "native_dependency_load_graph_verified": False,
        "os_driver_bytes_attested": False,
        "current_process_observation": observation,
        "global_content_commitment_sha256": sha256_bytes(
            canonical_json_bytes(commitment_payload)
        ),
        "blockers": _blockers(observation),
    }
    validate_manifest(manifest)
    return manifest


def _validate_hash(value: Any, label: str) -> str:
    require(isinstance(value, str) and HASH_RE.fullmatch(value) is not None, f"invalid SHA-256: {label}")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    require(set(manifest) == MANIFEST_KEYS, "runtime closure manifest schema drift")
    require(manifest["schema_version"] == MANIFEST_SCHEMA, "manifest version drift")
    require(manifest["status"] == MANIFEST_STATUS, "manifest status drift")
    expected_false = (
        "run_eligible",
        "model_execution_authorized",
        "model_execution_performed",
        "full_dependency_content_hash_bound",
        "external_content_addressed_launch_verified",
        "immutable_read_only_mount_verified",
        "native_dependency_load_graph_verified",
        "os_driver_bytes_attested",
    )
    for field in expected_false:
        require(manifest[field] is False, f"manifest overclaims {field}")
    require(manifest["model_action_count"] == 0 and type(manifest["model_action_count"]) is int, "model action count drift")
    for field in (
        "static_byte_inventory_complete",
        "all_allowed_content_bytes_hashed",
        "directory_double_scan_passed",
        "shadow_imports_absent",
        "cwd_import_path_absent",
        "pyc_and_cache_absent",
    ):
        require(manifest[field] is True, f"static closure flag failed: {field}")
    _validate_hash(manifest["build_spec_sha256"], "build spec")
    require(manifest["expected_python_version"] == EXPECTED_PYTHON_VERSION, "manifest Python version drift")
    require(
        manifest["required_launch_flags"] == REQUIRED_LAUNCH_FLAGS
        and all(type(value) is int for value in manifest["required_launch_flags"].values()),
        "manifest launch flags drift",
    )
    roots = manifest["roots"]
    require(isinstance(roots, list) and roots, "manifest roots missing")
    root_ids: set[str] = set()
    known_files: dict[tuple[str, str], dict[str, Any]] = {}
    for root in roots:
        require(isinstance(root, dict) and set(root) == ROOT_KEYS, "manifest root schema drift")
        root_id = root["root_id"]
        require(isinstance(root_id, str) and ROOT_ID_RE.fullmatch(root_id) is not None and root_id not in root_ids, "manifest root id drift")
        root_ids.add(root_id)
        require(isinstance(root["role"], str) and root["role"].strip(), "manifest root role missing")
        require(Path(root["path"]).is_absolute(), "manifest root path not absolute")
        directories = root["directories"]
        files = root["files"]
        require(isinstance(directories, list) and directories == sorted(directories, key=str.casefold), "directory order drift")
        require(len(directories) == len({item.casefold() for item in directories}), "directory collision")
        for relative in directories:
            _check_relative(relative, relative)
        require(isinstance(files, list) and [row["relative_path"] for row in files] == sorted((row["relative_path"] for row in files), key=str.casefold), "file order drift")
        for record in files:
            require(isinstance(record, dict) and set(record) == FILE_KEYS, "file record schema drift")
            relative = record["relative_path"]
            _check_relative(relative, relative)
            require(type(record["size_bytes"]) is int and record["size_bytes"] >= 0, "file size drift")
            _validate_hash(record["sha256"], relative)
            require(type(record["archive_member_count"]) is int and record["archive_member_count"] >= 0, "archive count drift")
            if record["archive_member_count"] == 0:
                require(record["archive_member_name_commitment_sha256"] is None and record["archive_top_level_import_providers"] == [], "non-archive metadata drift")
            else:
                _validate_hash(record["archive_member_name_commitment_sha256"], "archive member commitment")
                require(isinstance(record["archive_top_level_import_providers"], list), "archive providers drift")
            key = (root_id, relative)
            require(key not in known_files, "duplicate manifest file")
            known_files[key] = record
        require(root["directory_count"] == len(directories), "directory count drift")
        require(root["file_count"] == len(files), "file count drift")
        require(root["total_size_bytes"] == sum(row["size_bytes"] for row in files), "root byte count drift")
        expected_tree = sha256_bytes(
            canonical_json_bytes(
                {"root_id": root_id, "role": root["role"], "directories": directories, "files": files}
            )
        )
        require(root["tree_sha256"] == expected_tree and root["content_address"] == f"sha256:{expected_tree}", "root tree commitment drift")
    for label in ("interpreter", "release_entrypoint"):
        binding = manifest[label]
        require(isinstance(binding, dict) and set(binding) == PATH_BINDING_KEYS, f"{label} binding drift")
        require(binding["kind"] == "file" and (binding["root_id"], binding["relative_path"]) in known_files, f"{label} is not inventoried")
    sys_paths = manifest["ordered_sys_path"]
    require(isinstance(sys_paths, list) and sys_paths, "manifest sys.path missing")
    for binding in sys_paths:
        require(isinstance(binding, dict) and set(binding) == PATH_BINDING_KEYS, "sys.path binding drift")
        require(binding["root_id"] in root_ids and binding["kind"] in {"file", "directory"}, "sys.path owner/kind drift")
        if binding["kind"] == "file":
            require((binding["root_id"], binding["relative_path"]) in known_files, "sys.path file not inventoried")
    providers = manifest["top_level_import_providers"]
    require(isinstance(providers, list), "provider inventory missing")
    names: set[str] = set()
    for provider in providers:
        require(isinstance(provider, dict) and set(provider) == PROVIDER_KEYS, "provider record drift")
        folded = provider["name"].casefold()
        require(provider["name"].isidentifier() and folded not in names, "shadow/invalid provider")
        names.add(folded)
        require(type(provider["sys_path_index"]) is int and 0 <= provider["sys_path_index"] < len(sys_paths), "provider sys.path index drift")
        require(provider["root_id"] == sys_paths[provider["sys_path_index"]]["root_id"], "provider root drift")
    require(providers == _provider_records(sys_paths, roots), "provider inventory is not derivable from frozen roots")
    native = manifest["native_artifacts"]
    require(isinstance(native, list), "native inventory missing")
    for record in native:
        require(isinstance(record, dict) and set(record) == NATIVE_KEYS, "native record drift")
        source = known_files.get((record["root_id"], record["relative_path"]))
        require(source is not None and PurePosixPath(record["relative_path"]).suffix.casefold() in NATIVE_SUFFIXES, "native source drift")
        require(record["size_bytes"] == source["size_bytes"] and record["sha256"] == source["sha256"], "native binding drift")
    require(native == _native_records(roots), "native inventory is incomplete or reordered")
    observation = manifest["current_process_observation"]
    require(isinstance(observation, dict) and set(observation) == OBSERVATION_KEYS, "process observation drift")
    require(isinstance(observation["matches_frozen_spec"], bool), "process match flag drift")
    require(isinstance(observation["ml_modules_present"], list), "ML module observation drift")
    require(manifest["blockers"] == _blockers(observation), "manifest blocker drift")
    commitment_payload = {
        "roots": [
            {"root_id": root["root_id"], "role": root["role"], "tree_sha256": root["tree_sha256"]}
            for root in roots
        ],
        "interpreter": manifest["interpreter"],
        "release_entrypoint": manifest["release_entrypoint"],
        "ordered_sys_path": sys_paths,
        "top_level_import_providers": providers,
        "native_artifacts": native,
    }
    require(manifest["global_content_commitment_sha256"] == sha256_bytes(canonical_json_bytes(commitment_payload)), "global content commitment drift")
    require(isinstance(manifest["working_directory"], str) and Path(manifest["working_directory"]).is_absolute(), "working directory drift")


def verify_manifest_against_filesystem(manifest: dict[str, Any], spec: dict[str, Any]) -> None:
    validate_manifest(manifest)
    validate_build_spec(spec)
    require(manifest["build_spec_sha256"] == sha256_bytes(canonical_json_bytes(spec)), "manifest/build-spec mismatch")
    rebuilt = build_manifest(spec)
    require(canonical_json_bytes(manifest) == canonical_json_bytes(rebuilt), "runtime closure differs from current filesystem/process")


def atomic_write_new(path: Path, data: bytes) -> None:
    require(path.is_absolute(), "output path must be absolute")
    require(not path.exists(), "refusing to overwrite runtime closure output")
    require_no_link_ancestors(path.parent, "runtime closure output parent")
    path.parent.mkdir(parents=True, exist_ok=True)
    require_no_link_ancestors(path.parent, "runtime closure output parent")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--spec", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    spec = read_canonical_json(args.spec.resolve(), "runtime closure build spec")
    if args.command == "build":
        output = args.output.resolve(strict=False)
        for root in spec.get("roots", []):
            if isinstance(root, dict) and isinstance(root.get("path"), str):
                require(not _within(output, Path(root["path"])), "output must be outside inventoried roots")
        manifest = build_manifest(spec)
        data = canonical_json_bytes(manifest)
        atomic_write_new(output, data)
        print(json.dumps({"status": MANIFEST_STATUS, "manifest_sha256": sha256_bytes(data), "run_eligible": False, "model_actions": 0}, sort_keys=True))
        return 0
    manifest = read_canonical_json(args.manifest.resolve(), "runtime closure manifest")
    verify_manifest_against_filesystem(manifest, spec)
    print(json.dumps({"status": "STATIC_RUNTIME_CLOSURE_MATCHES_RUN_ELIGIBLE_FALSE", "run_eligible": False, "model_actions": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
