"""Observe and revalidate the isolated Python environment intended for R13.

Invoke with ``python -I -B``.  This utility imports only Python standard-library
modules and package metadata.  It does not import torch/transformers, load a
tokenizer, read model weights, or authorize an experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
import sysconfig
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "r13-observed-isolated-runtime-environment-r1"
STATUS = "REFERENCE_ONLY_EXTERNAL_BINDING_REQUIRED_MODEL_NOT_LOADED"
EXPECTED_PYTHON = "3.10.20"
EXPECTED_DISTRIBUTIONS = {
    "accelerate": ("1.10.1", "accelerate"),
    "huggingface-hub": ("0.36.2", "huggingface_hub"),
    "numpy": ("1.26.4", "numpy"),
    "peft": ("0.17.1", "peft"),
    "safetensors": ("0.8.0", "safetensors"),
    "tokenizers": ("0.21.4", "tokenizers"),
    "torch": ("2.8.0+cu128", "torch"),
    "transformers": ("4.55.4", "transformers"),
}
HASH_RE = re.compile(r"[0-9a-f]{64}")


class RuntimeEnvironmentError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeEnvironmentError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    selected = path.resolve(strict=True)
    require(selected.is_file() and not selected.is_symlink(), f"not a regular file: {path}")
    return {
        "path": str(selected),
        "size_bytes": selected.stat().st_size,
        "sha256": sha256_file(selected),
    }


def _dist_info_path(distribution: importlib.metadata.Distribution) -> Path:
    selected = getattr(distribution, "_path", None)
    require(selected is not None, "distribution exposes no dist-info path")
    path = Path(selected).resolve(strict=True)
    require(path.is_dir() and path.name.endswith((".dist-info", ".egg-info")), "invalid dist-info path")
    return path


def distribution_record(name: str, version: str, module: str) -> dict[str, Any]:
    distribution = importlib.metadata.distribution(name)
    require(distribution.version == version, f"{name} version drift")
    dist_info = _dist_info_path(distribution)
    metadata_files: dict[str, dict[str, Any]] = {}
    for filename in ("METADATA", "RECORD", "WHEEL", "INSTALLER"):
        path = dist_info / filename
        if path.is_file():
            metadata_files[filename] = file_record(path)
    require("METADATA" in metadata_files and "RECORD" in metadata_files, f"{name} metadata closure incomplete")
    spec = importlib.util.find_spec(module)
    require(spec is not None and spec.origin is not None, f"cannot resolve module {module}")
    origin = Path(spec.origin).resolve(strict=True)
    require(origin.is_file() and not origin.is_symlink(), f"module origin is not regular: {module}")
    return {
        "distribution": name,
        "version": version,
        "top_level_module": module,
        "module_origin": file_record(origin),
        "dist_info_path": str(dist_info),
        "metadata_files": metadata_files,
        "full_distribution_content_hash_bound": False,
        "full_content_binding_blocker": "RECORD_AND_ENTRYPOINT_ONLY_NOT_ALL_INSTALLED_BYTES",
    }


def _interpreter_files() -> list[dict[str, Any]]:
    candidates = [Path(sys.executable)]
    version_dll = Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    if version_dll.is_file():
        candidates.append(version_dll)
    pyvenv = Path(sys.prefix) / "pyvenv.cfg"
    if pyvenv.is_file():
        candidates.append(pyvenv)
    return [file_record(path) for path in candidates]


def _flags() -> dict[str, Any]:
    return {
        "isolated": sys.flags.isolated,
        "ignore_environment": sys.flags.ignore_environment,
        "no_user_site": sys.flags.no_user_site,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "safe_path_flag_available": hasattr(sys.flags, "safe_path"),
        "safe_path": getattr(sys.flags, "safe_path", None),
    }


def build_environment() -> dict[str, Any]:
    flags = _flags()
    require(flags == {
        "isolated": 1,
        "ignore_environment": 1,
        "no_user_site": 1,
        "dont_write_bytecode": 1,
        "safe_path_flag_available": False,
        "safe_path": None,
    }, "R13 runtime audit must be invoked with python -I -B")
    version = ".".join(str(part) for part in sys.version_info[:3])
    require(version == EXPECTED_PYTHON, "Python runtime version drift")
    paths = [str(Path(item).resolve(strict=False)) for item in sys.path]
    require("" not in sys.path, "empty current-directory sys.path entry detected")
    cwd = os.path.normcase(str(Path.cwd().resolve()))
    require(
        all(os.path.normcase(path) != cwd for path in paths),
        "current working directory is importable before trusted release insertion",
    )
    distributions = [
        distribution_record(name, version, module)
        for name, (version, module) in sorted(EXPECTED_DISTRIBUTIONS.items())
    ]
    value = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "python_version": EXPECTED_PYTHON,
        "python_implementation": sys.implementation.name,
        "platform_tag": sysconfig.get_platform(),
        "sys_prefix": str(Path(sys.prefix).resolve()),
        "sys_base_prefix": str(Path(sys.base_prefix).resolve()),
        "interpreter_files": _interpreter_files(),
        "isolated_flags": flags,
        "ordered_sys_path_before_release_insertion": paths,
        "current_working_directory_excluded": True,
        "release_code_inserted": False,
        "distributions": distributions,
        "full_dependency_content_hash_bound": False,
        "full_dependency_binding_blocker": "HASH_ALL_RUNTIME_DISTRIBUTION_FILES_OR_BUILD_CLEAN_IMMUTABLE_IMAGE",
        "tokenizer_loaded": False,
        "model_weight_loaded": False,
        "model_forward_performed": False,
        "gradient_performed": False,
        "optimizer_step_performed": False,
        "model_execution_authorized": False,
    }
    validate_environment(value)
    return value


TOP_KEYS = {
    "schema_version",
    "status",
    "python_version",
    "python_implementation",
    "platform_tag",
    "sys_prefix",
    "sys_base_prefix",
    "interpreter_files",
    "isolated_flags",
    "ordered_sys_path_before_release_insertion",
    "current_working_directory_excluded",
    "release_code_inserted",
    "distributions",
    "full_dependency_content_hash_bound",
    "full_dependency_binding_blocker",
    "tokenizer_loaded",
    "model_weight_loaded",
    "model_forward_performed",
    "gradient_performed",
    "optimizer_step_performed",
    "model_execution_authorized",
}
FILE_KEYS = {"path", "size_bytes", "sha256"}
DIST_KEYS = {
    "distribution",
    "version",
    "top_level_module",
    "module_origin",
    "dist_info_path",
    "metadata_files",
    "full_distribution_content_hash_bound",
    "full_content_binding_blocker",
}


def _validate_file_record(value: Any, label: str) -> None:
    require(isinstance(value, dict) and set(value) == FILE_KEYS, f"{label} record drift")
    require(isinstance(value["path"], str) and Path(value["path"]).is_absolute(), f"{label} path drift")
    require(isinstance(value["size_bytes"], int) and value["size_bytes"] >= 0, f"{label} size drift")
    require(isinstance(value["sha256"], str) and HASH_RE.fullmatch(value["sha256"]) is not None, f"{label} hash drift")


def validate_environment(value: dict[str, Any]) -> None:
    require(set(value) == TOP_KEYS, "runtime environment schema drift")
    require(value["schema_version"] == SCHEMA, "runtime environment version drift")
    require(value["status"] == STATUS, "runtime environment status drift")
    require(value["python_version"] == EXPECTED_PYTHON, "runtime Python drift")
    require(value["python_implementation"] == "cpython", "runtime implementation drift")
    require(isinstance(value["platform_tag"], str) and value["platform_tag"], "missing platform tag")
    require(Path(value["sys_prefix"]).is_absolute(), "sys_prefix is not absolute")
    require(Path(value["sys_base_prefix"]).is_absolute(), "sys_base_prefix is not absolute")
    files = value["interpreter_files"]
    require(isinstance(files, list) and len(files) >= 2, "interpreter file closure incomplete")
    for index, record in enumerate(files):
        _validate_file_record(record, f"interpreter file {index}")
    require(value["isolated_flags"] == {
        "isolated": 1,
        "ignore_environment": 1,
        "no_user_site": 1,
        "dont_write_bytecode": 1,
        "safe_path_flag_available": False,
        "safe_path": None,
    }, "isolated flags drift")
    paths = value["ordered_sys_path_before_release_insertion"]
    require(isinstance(paths, list) and paths and len(paths) == len(set(paths)), "sys.path is empty or duplicated")
    require(all(isinstance(path, str) and Path(path).is_absolute() for path in paths), "non-absolute sys.path member")
    expected_flags = {
        "current_working_directory_excluded": True,
        "release_code_inserted": False,
        "full_dependency_content_hash_bound": False,
        "tokenizer_loaded": False,
        "model_weight_loaded": False,
        "model_forward_performed": False,
        "gradient_performed": False,
        "optimizer_step_performed": False,
        "model_execution_authorized": False,
    }
    for field, expected in expected_flags.items():
        require(value[field] is expected, f"runtime {field} drift")
    require(value["full_dependency_binding_blocker"] == "HASH_ALL_RUNTIME_DISTRIBUTION_FILES_OR_BUILD_CLEAN_IMMUTABLE_IMAGE", "dependency blocker drift")
    records = value["distributions"]
    require(isinstance(records, list) and len(records) == len(EXPECTED_DISTRIBUTIONS), "distribution coverage drift")
    observed_names = []
    for record in records:
        require(isinstance(record, dict) and set(record) == DIST_KEYS, "distribution record drift")
        name = record["distribution"]
        observed_names.append(name)
        require(name in EXPECTED_DISTRIBUTIONS, "unexpected distribution")
        version, module = EXPECTED_DISTRIBUTIONS[name]
        require(record["version"] == version, f"{name} version drift")
        require(record["top_level_module"] == module, f"{name} module drift")
        _validate_file_record(record["module_origin"], f"{name} module origin")
        require(Path(record["dist_info_path"]).is_absolute(), f"{name} dist-info path drift")
        metadata_files = record["metadata_files"]
        require(isinstance(metadata_files, dict) and {"METADATA", "RECORD"}.issubset(metadata_files), f"{name} metadata coverage drift")
        for filename, file_value in metadata_files.items():
            require(filename in {"METADATA", "RECORD", "WHEEL", "INSTALLER"}, f"{name} metadata member drift")
            _validate_file_record(file_value, f"{name}/{filename}")
        require(record["full_distribution_content_hash_bound"] is False, f"{name} overstates content binding")
        require(record["full_content_binding_blocker"] == "RECORD_AND_ENTRYPOINT_ONLY_NOT_ALL_INSTALLED_BYTES", f"{name} blocker drift")
    require(observed_names == sorted(EXPECTED_DISTRIBUTIONS), "distribution order or membership drift")


def read_environment(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=lambda pairs: _reject_duplicate_pairs(pairs),
            parse_constant=lambda token: (_ for _ in ()).throw(
                RuntimeEnvironmentError(f"non-finite JSON constant: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeEnvironmentError("invalid runtime environment JSON") from error
    require(isinstance(value, dict), "runtime environment must be an object")
    validate_environment(value)
    require(raw == canonical_json_bytes(value), "runtime environment JSON is not canonical")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeEnvironmentError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def compare_current(expected: dict[str, Any]) -> None:
    validate_environment(expected)
    observed = build_environment()
    require(observed == expected, "current isolated runtime differs from bound environment")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    require((args.output is None) != (args.verify is None), "choose exactly one of --output or --verify")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verify is not None:
        value = read_environment(args.verify)
        compare_current(value)
        print(json.dumps({"status": "CURRENT_RUNTIME_MATCHES_REFERENCE", "model_loaded": False}, sort_keys=True))
        return 0
    require(args.output is not None and not args.output.exists(), "refusing to overwrite runtime output")
    value = build_environment()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(args.output, flags, 0o600)
    try:
        data = canonical_json_bytes(value)
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(
        json.dumps(
            {
                "status": STATUS,
                "environment_sha256": sha256_file(args.output),
                "full_dependency_content_hash_bound": False,
                "model_loaded": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
