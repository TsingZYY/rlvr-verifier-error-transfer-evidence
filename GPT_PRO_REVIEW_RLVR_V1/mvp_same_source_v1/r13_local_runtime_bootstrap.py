"""Fail-closed local-development bootstrap for the existing R13 hybrid runtime.

This module is deliberately not a production launcher.  It must be started by
the frozen Python 3.10 interpreter with ``-I -S -B``.  Before any optional
static-module dispatch it replaces ``sys.path``, fixes the offline/determinism
environment, revalidates the frozen runtime reference and critical package
metadata without importing those packages, and checks a minimum free-GPU
memory gate with ``nvidia-smi``.

No model-capable module is dispatchable in this revision.  The receipt remains
non-authorizing and explicitly describes the hybrid venv/conda evidence limit.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
from typing import Any, Iterator, Sequence


SCHEMA = "r13-local-development-runtime-preflight-receipt-r1"
STATUS = "HYBRID_HASH_BOUND_LOCAL_DEVELOPMENT_NOT_CLEAN_IMAGE"
REFERENCE_SCHEMA = "r13-observed-isolated-runtime-environment-r1"
REFERENCE_STATUS = "REFERENCE_ONLY_EXTERNAL_BINDING_REQUIRED_MODEL_NOT_LOADED"
REFERENCE_FILENAME = "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json"
REFERENCE_SHA256 = "b8ad8e6c397d88a339537f36c04cecd42782b5f29ea89361a2129b246111f6f2"
EXPECTED_PYTHON = "3.10.20"
DEFAULT_MIN_FREE_GPU_MIB = 5500
HASH_RE = re.compile(r"[0-9a-f]{64}")
COMPUTE_CAPABILITY_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")

FIXED_ENVIRONMENT = {
    "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
    "CUDA_VISIBLE_DEVICES": "0",
    "HF_HUB_OFFLINE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "TRANSFORMERS_OFFLINE": "1",
}

FORBIDDEN_IMPORT_ROOTS = {
    "accelerate",
    "huggingface_hub",
    "numpy",
    "peft",
    "safetensors",
    "sentencepiece",
    "tokenizers",
    "torch",
    "transformers",
}

# These files are standard-library-only and contain no model implementation.
# Expanding this set requires a new reviewed bootstrap revision.
STATIC_DISPATCH_ALLOWLIST = {
    "r13_lazy_backend_adapter",
    "r13_model_inventory",
    "r13_runner_core",
    "r13_runtime_environment",
    "r13_update_recipe_contract",
}

PROHIBITED_ACTION_KEYS = {
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "sampled_rlvr",
    "hidden_audit",
}

EXPECTED_DISTRIBUTION_NAMES = [
    "accelerate",
    "huggingface-hub",
    "numpy",
    "peft",
    "safetensors",
    "tokenizers",
    "torch",
    "transformers",
]

FIXED_BLOCKERS = [
    "CURRENT_RUNTIME_IS_HYBRID_VENV_EXTERNAL_CONDA",
    "FULL_DEPENDENCY_CONTENT_HASH_NOT_BOUND",
    "NOT_A_CLEAN_IMMUTABLE_IMAGE",
    "LOCAL_DEVELOPMENT_PREFLIGHT_NOT_MODEL_AUTHORIZATION",
    "MODEL_CAPABLE_DISPATCH_DISABLED",
    "GPU_OBSERVATION_IS_EPHEMERAL_PREFLIGHT_ONLY",
    "PRE_PYTHON_NATIVE_LOADER_STATE_NOT_ATTESTED",
]


class LocalRuntimeBootstrapError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LocalRuntimeBootstrapError(message)


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
    raise LocalRuntimeBootstrapError(f"unsupported JSON value at {label}")


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
        raise LocalRuntimeBootstrapError("value is not canonical JSON") from error
    return (rendered + "\n").encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def strict_json_loads(raw: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LocalRuntimeBootstrapError(f"duplicate key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise LocalRuntimeBootstrapError(f"non-finite JSON constant in {label}: {token}")

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalRuntimeBootstrapError(f"invalid UTF-8 JSON: {label}") from error
    require(isinstance(value, dict), f"{label} must be one JSON object")
    _assert_json_tree(value, label)
    return value


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(str(Path(left).resolve(strict=False))) == os.path.normcase(
        str(Path(right).resolve(strict=False))
    )


def _within(child: Path | str, parent: Path | str) -> bool:
    try:
        Path(child).resolve(strict=False).relative_to(Path(parent).resolve(strict=False))
    except ValueError:
        return False
    return True


def expected_reference_path(project_path: Path) -> Path:
    return (
        project_path.resolve(strict=True).parent
        / "formal_g1_development_r1"
        / REFERENCE_FILENAME
    ).resolve(strict=True)


def read_runtime_reference(path: Path, project_path: Path) -> dict[str, Any]:
    selected = path.resolve(strict=True)
    require(
        _same_path(selected, expected_reference_path(project_path)),
        "runtime reference is not the fixed R13 reference",
    )
    raw = selected.read_bytes()
    require(sha256_bytes(raw) == REFERENCE_SHA256, "runtime reference hash drift")
    value = strict_json_loads(raw, "runtime reference")
    require(raw == canonical_json_bytes(value), "runtime reference is not canonical JSON")
    require(value.get("schema_version") == REFERENCE_SCHEMA, "runtime reference schema drift")
    require(value.get("status") == REFERENCE_STATUS, "runtime reference status drift")
    require(value.get("python_version") == EXPECTED_PYTHON, "runtime reference Python drift")
    require(value.get("python_implementation") == "cpython", "runtime implementation drift")
    require(value.get("full_dependency_content_hash_bound") is False, "reference overclaims dependency closure")
    require(value.get("model_execution_authorized") is False, "reference authorizes model execution")
    for field in (
        "tokenizer_loaded",
        "model_weight_loaded",
        "model_forward_performed",
        "gradient_performed",
        "optimizer_step_performed",
    ):
        require(value.get(field) is False, f"reference reports prohibited action: {field}")
    require(
        isinstance(value.get("sys_prefix"), str)
        and isinstance(value.get("sys_base_prefix"), str)
        and not _same_path(value["sys_prefix"], value["sys_base_prefix"]),
        "reference is not the expected hybrid venv/base layout",
    )
    require(isinstance(value.get("interpreter_files"), list), "reference interpreter files missing")
    require(isinstance(value.get("distributions"), list), "reference distributions missing")
    return value


def validate_startup_flags() -> dict[str, int]:
    flags = {
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "ignore_environment": sys.flags.ignore_environment,
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
        "no_user_site": sys.flags.no_user_site,
    }
    expected = {
        "dont_write_bytecode": 1,
        "ignore_environment": 1,
        "isolated": 1,
        "no_site": 1,
        "no_user_site": 1,
    }
    require(flags == expected, "bootstrap must be launched with python -I -S -B")
    version = ".".join(str(part) for part in sys.version_info[:3])
    require(version == EXPECTED_PYTHON, "bootstrap Python version drift")
    require(sys.implementation.name == "cpython", "bootstrap requires CPython")
    for name in ("site", "sitecustomize", "usercustomize", "_distutils_hack"):
        require(name not in sys.modules, f"startup hook module was already imported: {name}")
    require(not loaded_forbidden_modules(), "ML package imported before bootstrap preflight")
    return flags


def freeze_environment() -> dict[str, str]:
    for key, value in FIXED_ENVIRONMENT.items():
        os.environ[key] = value
    require(
        {key: os.environ.get(key) for key in FIXED_ENVIRONMENT} == FIXED_ENVIRONMENT,
        "cannot fix local-development environment variables",
    )
    return dict(FIXED_ENVIRONMENT)


def configure_import_machinery() -> dict[str, Any]:
    expected_meta = [
        ("_frozen_importlib", "BuiltinImporter"),
        ("_frozen_importlib", "FrozenImporter"),
        ("_frozen_importlib_external", "PathFinder"),
    ]
    observed_meta = [
        (
            getattr(finder, "__module__", type(finder).__module__),
            getattr(finder, "__qualname__", type(finder).__qualname__),
        )
        for finder in sys.meta_path
    ]
    require(observed_meta == expected_meta, "non-default meta_path finder present before bootstrap")
    hook_records = [
        (
            getattr(hook, "__module__", type(hook).__module__),
            getattr(hook, "__qualname__", type(hook).__qualname__),
        )
        for hook in sys.path_hooks
    ]
    require(
        len(hook_records) == 2
        and hook_records[0] == ("zipimport", "zipimporter")
        and hook_records[1][0] == "_frozen_importlib_external"
        and hook_records[1][1].endswith("path_hook_for_FileFinder"),
        "non-default path hook present before bootstrap",
    )
    file_finder_hook = sys.path_hooks[1]
    sys.meta_path[:] = [
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    ]
    # The effective import roots are directories only; disabling zipimport
    # removes the stale/nonexistent python310.zip path class completely.
    sys.path_hooks[:] = [file_finder_hook]
    sys.path_importer_cache.clear()
    importlib.invalidate_caches()
    return {
        "meta_path": ["BuiltinImporter", "FrozenImporter", "PathFinder"],
        "path_hooks": ["FileFinder"],
        "zip_import_disabled": True,
    }


def runtime_paths(reference: dict[str, Any], project_path: Path) -> dict[str, Path]:
    overlay = Path(reference["sys_prefix"]).resolve(strict=True)
    base = Path(reference["sys_base_prefix"]).resolve(strict=True)
    project = project_path.resolve(strict=True)
    require(project.is_dir(), "fixed project path is not a directory")
    expected_project = Path(__file__).resolve(strict=True).parent
    require(_same_path(project, expected_project), "project path is not the bootstrap directory")
    executable = Path(sys.executable).resolve(strict=True)
    require(_same_path(executable, overlay / "Scripts" / "python.exe"), "wrong hybrid venv interpreter")
    require(_same_path(sys.base_prefix, base), "base prefix differs from runtime reference")
    require(_same_path(sys.base_exec_prefix, base), "base exec-prefix differs from runtime reference")
    # On CPython 3.10, -S intentionally leaves sys.prefix equal to base_prefix.
    require(_same_path(sys.prefix, base), "unexpected sys.prefix under Python 3.10 -S")
    require(_same_path(sys.exec_prefix, base), "unexpected sys.exec_prefix under Python 3.10 -S")
    # Reconstruct the venv-visible prefix without invoking site.py.  This occurs
    # before any third-party package is imported.
    sys.prefix = str(overlay)
    sys.exec_prefix = str(overlay)
    require(_same_path(sys.prefix, overlay) and _same_path(sys.exec_prefix, overlay), "cannot rebuild overlay prefix")
    paths = {
        "base_dlls": (base / "DLLs").resolve(strict=True),
        "base_lib": (base / "Lib").resolve(strict=True),
        "overlay_site_packages": (overlay / "Lib" / "site-packages").resolve(strict=True),
        "base_site_packages": (base / "Lib" / "site-packages").resolve(strict=True),
        "project": project,
    }
    require(all(path.is_dir() for path in paths.values()), "one or more runtime paths are not directories")
    normalized = [os.path.normcase(str(path)) for path in paths.values()]
    require(len(normalized) == len(set(normalized)), "runtime paths are duplicated")
    return paths


def rebuild_sys_path(paths: dict[str, Path], working_directory: Path | None = None) -> list[str]:
    require(
        list(paths) == [
            "base_dlls",
            "base_lib",
            "overlay_site_packages",
            "base_site_packages",
            "project",
        ],
        "runtime path ordering drift",
    )
    ordered = [str(path.resolve(strict=True)) for path in paths.values()]
    cwd = (working_directory or Path.cwd()).resolve(strict=True)
    require(
        all(not _same_path(cwd, path) for path in ordered),
        "current working directory is an effective import root",
    )
    sys.path[:] = ordered
    sys.path_importer_cache.clear()
    importlib.invalidate_caches()
    require("" not in sys.path and sys.path == ordered, "failed to replace sys.path exactly")
    return ordered


def validate_interpreter_files(reference: dict[str, Any]) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for index, expected in enumerate(reference["interpreter_files"]):
        require(isinstance(expected, dict), f"interpreter record {index} is not an object")
        record = file_record(Path(expected["path"]))
        require(record == expected, f"interpreter file {index} differs from runtime reference")
        observed.append(record)
    require(
        any(_same_path(record["path"], sys.executable) for record in observed),
        "current interpreter is absent from runtime reference",
    )
    return observed


def _distribution_path(distribution: importlib.metadata.Distribution) -> Path:
    selected = getattr(distribution, "_path", None)
    require(selected is not None, "distribution exposes no dist-info path")
    path = Path(selected).resolve(strict=True)
    require(path.is_dir() and path.name.endswith((".dist-info", ".egg-info")), "invalid dist-info path")
    return path


def validate_distribution_origins(reference: dict[str, Any]) -> list[dict[str, Any]]:
    records = reference["distributions"]
    require(isinstance(records, list) and records, "runtime distribution records missing")
    observed: list[dict[str, Any]] = []
    names: set[str] = set()
    search_roots = [
        str((Path(reference["sys_prefix"]) / "Lib" / "site-packages").resolve(strict=True)),
        str((Path(reference["sys_base_prefix"]) / "Lib" / "site-packages").resolve(strict=True)),
    ]
    all_distributions = list(importlib.metadata.distributions(path=search_roots))
    for expected in records:
        require(isinstance(expected, dict), "runtime distribution record is not an object")
        name = expected.get("distribution")
        module = expected.get("top_level_module")
        version = expected.get("version")
        require(isinstance(name, str) and name not in names, "duplicate/invalid distribution")
        require(isinstance(module, str) and module, f"{name} module missing")
        require(isinstance(version, str) and version, f"{name} version missing")
        names.add(name)
        normalized_name = re.sub(r"[-_.]+", "-", name).casefold()
        matching = [
            candidate
            for candidate in all_distributions
            if re.sub(
                r"[-_.]+",
                "-",
                str(candidate.metadata.get("Name", "")),
            ).casefold()
            == normalized_name
        ]
        require(len(matching) == 1, f"{name} is duplicated or missing across hybrid roots")
        distribution = importlib.metadata.distribution(name)
        require(distribution.version == version, f"{name} version drift")
        dist_info = _distribution_path(distribution)
        require(_same_path(dist_info, expected["dist_info_path"]), f"{name} dist-info origin drift")
        spec = importlib.util.find_spec(module)
        require(spec is not None and spec.origin is not None, f"cannot resolve {module}")
        origin = Path(spec.origin).resolve(strict=True)
        origin_record = file_record(origin)
        require(origin_record == expected["module_origin"], f"{name} module origin/hash drift")
        metadata_records: dict[str, dict[str, Any]] = {}
        for filename, expected_file in sorted(expected["metadata_files"].items()):
            record = file_record(dist_info / filename)
            require(record == expected_file, f"{name}/{filename} hash drift")
            metadata_records[filename] = record
        observed.append(
            {
                "distribution": name,
                "version": version,
                "top_level_module": module,
                "module_origin": origin_record,
                "dist_info_path": str(dist_info),
                "metadata_files": metadata_records,
            }
        )
    require(not loaded_forbidden_modules(), "metadata inspection imported an ML package")
    return observed


def startup_hook_records(paths: dict[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("overlay_site_packages", "base_site_packages"):
        root = paths[key]
        candidates = sorted(
            (
                path
                for path in root.iterdir()
                if path.is_file()
                and (
                    path.name.casefold().endswith((".pth", "._pth"))
                    or path.name.casefold() in {"sitecustomize.py", "usercustomize.py"}
                )
            ),
            key=lambda path: path.name.casefold(),
        )
        for path in candidates:
            record = file_record(path)
            record["runtime_root"] = key
            records.append(record)
    return records


def loaded_forbidden_modules() -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS
    )


class _ForbiddenImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: Any = None,
    ) -> None:
        del path, target
        root = fullname.split(".", 1)[0]
        if root in FORBIDDEN_IMPORT_ROOTS:
            raise LocalRuntimeBootstrapError(
                f"model/tokenizer-capable import is disabled in this bootstrap revision: {fullname}"
            )
        return None


@contextmanager
def block_forbidden_imports() -> Iterator[None]:
    require(not loaded_forbidden_modules(), "forbidden module already loaded before dispatch")
    blocker = _ForbiddenImportBlocker()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        require(blocker in sys.meta_path, "forbidden import blocker was removed during dispatch")
        sys.meta_path.remove(blocker)
        require(not loaded_forbidden_modules(), "forbidden module loaded during dispatch")


def _system_nvidia_smi() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(strict=True)
    expected = (system_root / "System32" / "nvidia-smi.exe").resolve(strict=True)
    located = shutil.which("nvidia-smi")
    require(located is not None and _same_path(located, expected), "nvidia-smi is absent or PATH-shadowed")
    return expected


def parse_gpu_query(output: str, minimum_free_mib: int) -> dict[str, Any]:
    require(type(minimum_free_mib) is int and minimum_free_mib >= 0, "invalid GPU free-memory threshold")
    require(len(output) <= 16384, "nvidia-smi output is unexpectedly large")
    rows = list(csv.reader(io.StringIO(output), skipinitialspace=True))
    selected: list[list[str]] = []
    for row in rows:
        if len(row) != 7:
            continue
        if row[0].strip() == "0":
            selected.append([value.strip() for value in row])
    require(len(selected) == 1, "nvidia-smi did not report exactly one physical GPU index 0")
    index, name, driver, total, free, used, compute = selected[0]
    try:
        total_mib = int(total)
        free_mib = int(free)
        used_mib = int(used)
    except ValueError as error:
        raise LocalRuntimeBootstrapError("nvidia-smi memory fields are not integers") from error
    require(total_mib > 0 and 0 <= free_mib <= total_mib, "invalid GPU memory observation")
    require(0 <= used_mib <= total_mib, "invalid GPU used-memory observation")
    require(COMPUTE_CAPABILITY_RE.fullmatch(compute) is not None, "invalid GPU compute capability")
    require(free_mib >= minimum_free_mib, "GPU free-memory gate failed")
    return {
        "index": int(index),
        "name": name,
        "driver_version": driver,
        "memory_total_mib": total_mib,
        "memory_free_mib": free_mib,
        "memory_used_mib": used_mib,
        "compute_capability": compute,
        "minimum_free_mib": minimum_free_mib,
        "free_memory_gate_passed": True,
        "observation_scope": "EPHEMERAL_PREFLIGHT_ONLY_NOT_EXECUTION_OR_PRODUCTION_ATTESTATION",
        "resource_reserved": False,
    }


def probe_gpu(minimum_free_mib: int) -> dict[str, Any]:
    tool = _system_nvidia_smi()
    command = [
        str(tool),
        "--query-gpu=index,name,driver_version,memory.total,memory.free,memory.used,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise LocalRuntimeBootstrapError("nvidia-smi preflight failed") from error
    require(completed.returncode == 0 and not completed.stderr.strip(), "nvidia-smi returned an error")
    observed = parse_gpu_query(completed.stdout, minimum_free_mib)
    observed["nvidia_smi"] = file_record(tool)
    return observed


def resolve_dispatch(module_name: str | None, project_path: Path) -> dict[str, Any]:
    allowed = sorted(STATIC_DISPATCH_ALLOWLIST)
    if module_name is None:
        return {
            "requested": False,
            "module": None,
            "module_file": None,
            "allowed_static_modules": allowed,
            "model_capable_dispatch_enabled": False,
        }
    require(module_name in STATIC_DISPATCH_ALLOWLIST, "dispatch module is not in the static allowlist")
    require(module_name.isidentifier(), "dispatch module name is invalid")
    module_path = (project_path / f"{module_name}.py").resolve(strict=True)
    require(module_path.parent == project_path.resolve(strict=True), "dispatch module escapes project path")
    return {
        "requested": True,
        "module": module_name,
        "module_file": file_record(module_path),
        "allowed_static_modules": allowed,
        "model_capable_dispatch_enabled": False,
    }


def build_preflight_receipt(
    *,
    reference_path: Path,
    project_path: Path,
    minimum_free_gpu_mib: int,
    dispatch_module: str | None,
) -> dict[str, Any]:
    flags = validate_startup_flags()
    project = project_path.resolve(strict=True)
    reference = read_runtime_reference(reference_path, project)
    initial_prefix = str(Path(sys.prefix).resolve(strict=True))
    initial_exec_prefix = str(Path(sys.exec_prefix).resolve(strict=True))
    paths = runtime_paths(reference, project)
    environment = freeze_environment()
    import_machinery = configure_import_machinery()
    effective_sys_path = rebuild_sys_path(paths)
    interpreter_files = validate_interpreter_files(reference)
    distributions = validate_distribution_origins(reference)
    hook_files = startup_hook_records(paths)
    gpu = probe_gpu(minimum_free_gpu_mib)
    dispatch = resolve_dispatch(dispatch_module, project)
    receipt = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_boundary": {
            "scope": "LOCAL_DEVELOPMENT_STATIC_PREFLIGHT_ONLY",
            "scientific_evidence": False,
            "formal_experiment": False,
            "production_runtime": False,
        },
        "run_eligible": False,
        "model_execution_authorized": False,
        "model_action_count": 0,
        "release_authorization_compatible": False,
        "pre_python_native_loader_state_attested": False,
        "static_preflight_passed": True,
        "static_module_dispatch_ready": True,
        "hash_binding_scope": "REFERENCE_INTERPRETER_CRITICAL_PACKAGE_METADATA_ORIGINS_BOOTSTRAP_AND_DISPATCH_ONLY",
        "full_dependency_content_hash_bound": False,
        "runtime_reference": {
            "path": str(reference_path.resolve(strict=True)),
            "sha256": REFERENCE_SHA256,
            "schema_version": reference["schema_version"],
            "status": reference["status"],
        },
        "bootstrap": file_record(Path(__file__)),
        "python": {
            "version": EXPECTED_PYTHON,
            "implementation": sys.implementation.name,
            "executable": file_record(Path(sys.executable)),
            "referenced_overlay_prefix": str(Path(reference["sys_prefix"]).resolve(strict=True)),
            "base_prefix": str(Path(reference["sys_base_prefix"]).resolve(strict=True)),
            "initial_prefix_under_no_site": initial_prefix,
            "initial_exec_prefix_under_no_site": initial_exec_prefix,
            "rebuilt_prefix": str(Path(sys.prefix).resolve(strict=True)),
            "rebuilt_exec_prefix": str(Path(sys.exec_prefix).resolve(strict=True)),
            "launch_flags": flags,
            "interpreter_files": interpreter_files,
        },
        "effective_sys_path": effective_sys_path,
        "working_directory": {
            "path": str(Path.cwd().resolve(strict=True)),
            "excluded_from_effective_sys_path": True,
        },
        "environment": environment,
        "startup_isolation": {
            "site_module_imported": False,
            "pth_execution_disabled_by_no_site": True,
            "startup_hook_modules_absent": True,
            "forbidden_modules_absent_before_dispatch": True,
            "hook_files_present_but_not_executed": hook_files,
            "import_machinery": import_machinery,
        },
        "distributions": distributions,
        "gpu": gpu,
        "dispatch": dispatch,
        "prohibited_actions": {key: False for key in sorted(PROHIBITED_ACTION_KEYS)},
        "blockers": list(FIXED_BLOCKERS),
    }
    validate_receipt(receipt)
    return receipt


RECEIPT_KEYS = {
    "schema_version",
    "status",
    "created_at_utc",
    "evidence_boundary",
    "run_eligible",
    "model_execution_authorized",
    "model_action_count",
    "release_authorization_compatible",
    "pre_python_native_loader_state_attested",
    "static_preflight_passed",
    "static_module_dispatch_ready",
    "hash_binding_scope",
    "full_dependency_content_hash_bound",
    "runtime_reference",
    "bootstrap",
    "python",
    "effective_sys_path",
    "working_directory",
    "environment",
    "startup_isolation",
    "distributions",
    "gpu",
    "dispatch",
    "prohibited_actions",
    "blockers",
}


def _validate_hash(value: Any, label: str) -> None:
    require(isinstance(value, str) and HASH_RE.fullmatch(value) is not None, f"invalid SHA-256: {label}")


def _validate_file_record(value: Any, label: str) -> None:
    require(isinstance(value, dict) and set(value) == {"path", "size_bytes", "sha256"}, f"{label} record drift")
    require(isinstance(value["path"], str) and Path(value["path"]).is_absolute(), f"{label} path drift")
    require(type(value["size_bytes"]) is int and value["size_bytes"] >= 0, f"{label} size drift")
    _validate_hash(value["sha256"], label)


def validate_receipt(receipt: dict[str, Any]) -> None:
    require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, "preflight receipt schema drift")
    require(receipt["schema_version"] == SCHEMA, "preflight receipt version drift")
    require(receipt["status"] == STATUS, "preflight receipt status drift")
    require(isinstance(receipt["created_at_utc"], str) and receipt["created_at_utc"], "preflight timestamp missing")
    boundary = receipt["evidence_boundary"]
    require(
        boundary
        == {
            "scope": "LOCAL_DEVELOPMENT_STATIC_PREFLIGHT_ONLY",
            "scientific_evidence": False,
            "formal_experiment": False,
            "production_runtime": False,
        },
        "evidence boundary drift",
    )
    require(receipt["run_eligible"] is False, "receipt self-authorizes a run")
    require(receipt["model_execution_authorized"] is False, "receipt authorizes model execution")
    require(type(receipt["model_action_count"]) is int and receipt["model_action_count"] == 0, "model action count drift")
    require(receipt["release_authorization_compatible"] is False, "receipt claims release authorization compatibility")
    require(receipt["pre_python_native_loader_state_attested"] is False, "receipt overclaims pre-Python native loader state")
    require(receipt["static_preflight_passed"] is True, "static preflight flag drift")
    require(receipt["static_module_dispatch_ready"] is True, "static dispatch flag drift")
    require(
        receipt["hash_binding_scope"]
        == "REFERENCE_INTERPRETER_CRITICAL_PACKAGE_METADATA_ORIGINS_BOOTSTRAP_AND_DISPATCH_ONLY",
        "hash-binding scope drift",
    )
    require(receipt["full_dependency_content_hash_bound"] is False, "receipt overclaims dependency closure")
    reference = receipt["runtime_reference"]
    require(
        isinstance(reference, dict)
        and set(reference) == {"path", "sha256", "schema_version", "status"},
        "runtime reference receipt drift",
    )
    require(reference["schema_version"] == REFERENCE_SCHEMA and reference["status"] == REFERENCE_STATUS, "runtime reference binding drift")
    require(reference["sha256"] == REFERENCE_SHA256, "runtime reference receipt hash drift")
    require(isinstance(reference["path"], str) and Path(reference["path"]).is_absolute(), "runtime reference path drift")
    _validate_file_record(receipt["bootstrap"], "bootstrap")
    python = receipt["python"]
    require(
        isinstance(python, dict)
        and set(python)
        == {
            "version",
            "implementation",
            "executable",
            "referenced_overlay_prefix",
            "base_prefix",
            "initial_prefix_under_no_site",
            "initial_exec_prefix_under_no_site",
            "rebuilt_prefix",
            "rebuilt_exec_prefix",
            "launch_flags",
            "interpreter_files",
        }
        and python["version"] == EXPECTED_PYTHON,
        "receipt Python drift",
    )
    require(python["implementation"] == "cpython", "receipt implementation drift")
    _validate_file_record(python["executable"], "Python executable")
    require(isinstance(python["interpreter_files"], list) and python["interpreter_files"], "interpreter files missing")
    for index, record in enumerate(python["interpreter_files"]):
        _validate_file_record(record, f"interpreter file {index}")
    expected_flags = {
        "dont_write_bytecode": 1,
        "ignore_environment": 1,
        "isolated": 1,
        "no_site": 1,
        "no_user_site": 1,
    }
    require(python["launch_flags"] == expected_flags, "receipt launch flag drift")
    require(_same_path(python["initial_prefix_under_no_site"], python["base_prefix"]), "initial -S prefix drift")
    require(_same_path(python["initial_exec_prefix_under_no_site"], python["base_prefix"]), "initial -S exec-prefix drift")
    require(_same_path(python["rebuilt_prefix"], python["referenced_overlay_prefix"]), "rebuilt overlay prefix drift")
    require(_same_path(python["rebuilt_exec_prefix"], python["referenced_overlay_prefix"]), "rebuilt overlay exec-prefix drift")
    paths = receipt["effective_sys_path"]
    require(isinstance(paths, list) and len(paths) == 5 and len(paths) == len(set(paths)), "effective sys.path drift")
    require(all(isinstance(path, str) and Path(path).is_absolute() for path in paths), "non-absolute effective sys.path")
    working = receipt["working_directory"]
    require(isinstance(working, dict) and working.get("excluded_from_effective_sys_path") is True, "working-directory exclusion drift")
    require(all(not _same_path(working["path"], path) for path in paths), "working directory entered effective sys.path")
    require(receipt["environment"] == FIXED_ENVIRONMENT, "fixed environment drift")
    isolation = receipt["startup_isolation"]
    require(isolation["site_module_imported"] is False, "site was imported")
    require(isolation["pth_execution_disabled_by_no_site"] is True, "pth execution is not disabled")
    require(isolation["startup_hook_modules_absent"] is True, "startup hook module drift")
    require(isolation["forbidden_modules_absent_before_dispatch"] is True, "forbidden module preloaded")
    require(
        isolation["import_machinery"]
        == {
            "meta_path": ["BuiltinImporter", "FrozenImporter", "PathFinder"],
            "path_hooks": ["FileFinder"],
            "zip_import_disabled": True,
        },
        "import machinery receipt drift",
    )
    hooks = isolation["hook_files_present_but_not_executed"]
    require(isinstance(hooks, list), "startup hook file inventory drift")
    for record in hooks:
        require(isinstance(record, dict) and record.get("runtime_root") in {"overlay_site_packages", "base_site_packages"}, "hook root drift")
        _validate_file_record({key: record[key] for key in ("path", "size_bytes", "sha256")}, "startup hook")
    distributions = receipt["distributions"]
    require(isinstance(distributions, list) and distributions, "distribution receipt missing")
    require(
        [record.get("distribution") for record in distributions]
        == EXPECTED_DISTRIBUTION_NAMES,
        "distribution receipt membership/order drift",
    )
    for record in distributions:
        require(
            isinstance(record, dict)
            and set(record)
            == {
                "distribution",
                "version",
                "top_level_module",
                "module_origin",
                "dist_info_path",
                "metadata_files",
            }
            and isinstance(record.get("distribution"), str),
            "distribution receipt drift",
        )
        _validate_file_record(record["module_origin"], f"{record['distribution']} origin")
        require(isinstance(record["metadata_files"], dict) and record["metadata_files"], "distribution metadata missing")
        for name, file_value in record["metadata_files"].items():
            _validate_file_record(file_value, f"{record['distribution']}/{name}")
    gpu = receipt["gpu"]
    require(isinstance(gpu, dict) and gpu.get("free_memory_gate_passed") is True, "GPU gate receipt drift")
    require(type(gpu.get("memory_free_mib")) is int and gpu["memory_free_mib"] >= gpu["minimum_free_mib"], "GPU free-memory receipt drift")
    require(gpu.get("observation_scope") == "EPHEMERAL_PREFLIGHT_ONLY_NOT_EXECUTION_OR_PRODUCTION_ATTESTATION", "GPU evidence scope drift")
    require(gpu.get("resource_reserved") is False, "GPU observation overclaims reservation")
    _validate_file_record(gpu["nvidia_smi"], "nvidia-smi")
    dispatch = receipt["dispatch"]
    require(isinstance(dispatch, dict) and dispatch.get("model_capable_dispatch_enabled") is False, "model-capable dispatch enabled")
    require(dispatch.get("allowed_static_modules") == sorted(STATIC_DISPATCH_ALLOWLIST), "dispatch allowlist drift")
    if dispatch.get("requested") is True:
        require(dispatch.get("module") in STATIC_DISPATCH_ALLOWLIST, "requested dispatch is not allowed")
        _validate_file_record(dispatch.get("module_file"), "dispatch module")
    else:
        require(dispatch.get("requested") is False and dispatch.get("module") is None and dispatch.get("module_file") is None, "empty dispatch drift")
    prohibited = receipt["prohibited_actions"]
    require(set(prohibited) == PROHIBITED_ACTION_KEYS and all(value is False for value in prohibited.values()), "prohibited action receipt drift")
    require(receipt["blockers"] == FIXED_BLOCKERS, "local-development blockers drift")


def read_preflight_receipt(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.resolve(strict=True).read_bytes()
    receipt = strict_json_loads(raw, "preflight receipt")
    require(raw == canonical_json_bytes(receipt), "preflight receipt is not canonical JSON")
    validate_receipt(receipt)
    return receipt, raw


def verify_recorded_files(receipt: dict[str, Any]) -> None:
    validate_receipt(receipt)

    def replay(record: dict[str, Any], label: str) -> None:
        require(file_record(Path(record["path"])) == record, f"recorded file changed: {label}")

    reference = receipt["runtime_reference"]
    reference_path = Path(reference["path"])
    require(sha256_file(reference_path) == reference["sha256"], "recorded runtime reference changed")
    replay(receipt["bootstrap"], "bootstrap")
    replay(receipt["python"]["executable"], "Python executable")
    for index, record in enumerate(receipt["python"]["interpreter_files"]):
        replay(record, f"interpreter file {index}")
    for distribution in receipt["distributions"]:
        replay(distribution["module_origin"], f"{distribution['distribution']} origin")
        for name, record in distribution["metadata_files"].items():
            replay(record, f"{distribution['distribution']}/{name}")
    replay(receipt["gpu"]["nvidia_smi"], "nvidia-smi")
    if receipt["dispatch"]["module_file"] is not None:
        replay(receipt["dispatch"]["module_file"], "dispatch module")


def atomic_write_new(path: Path, data: bytes) -> None:
    selected = path.resolve(strict=False)
    require(selected.is_absolute() and not selected.exists(), "refusing to overwrite preflight receipt")
    selected.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(selected, flags, 0o600)
    try:
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_controlled_dispatch(
    receipt: dict[str, Any],
    dispatch_args: Sequence[str],
) -> int:
    validate_receipt(receipt)
    dispatch = receipt["dispatch"]
    require(dispatch["requested"] is True, "no static module dispatch was requested")
    module = dispatch["module"]
    require(module in STATIC_DISPATCH_ALLOWLIST, "dispatch module left the static allowlist")
    module_path = Path(dispatch["module_file"]["path"]).resolve(strict=True)
    require(module not in sys.modules, "dispatch module was already present in sys.modules")
    validate_startup_flags()
    require(sys.path == receipt["effective_sys_path"], "dispatch sys.path drift")
    require(
        _same_path(Path.cwd(), receipt["working_directory"]["path"]),
        "dispatch working directory drift",
    )
    require(
        {key: os.environ.get(key) for key in FIXED_ENVIRONMENT}
        == FIXED_ENVIRONMENT,
        "dispatch environment drift",
    )
    require(file_record(module_path) == dispatch["module_file"], "dispatch module changed after preflight")
    previous_argv = sys.argv[:]
    exit_code = 0
    try:
        sys.argv = [str(module_path), *dispatch_args]
        with block_forbidden_imports():
            try:
                runpy.run_path(str(module_path), run_name="__main__")
            except SystemExit as error:
                require(error.code is None or type(error.code) is int, "static dispatch returned a non-integer exit code")
                exit_code = 0 if error.code is None else error.code
    finally:
        sys.argv = previous_argv
    require(not loaded_forbidden_modules(), "forbidden module present after static dispatch")
    return exit_code


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-reference", type=Path)
    parser.add_argument("--project-path", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--verify-receipt", type=Path)
    parser.add_argument("--min-free-gpu-mib", type=int, default=DEFAULT_MIN_FREE_GPU_MIB)
    parser.add_argument("--dispatch-module", choices=sorted(STATIC_DISPATCH_ALLOWLIST))
    parser.add_argument("dispatch_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verify_receipt is not None:
        require(
            args.runtime_reference is None
            and args.project_path is None
            and args.receipt is None
            and args.dispatch_module is None
            and not args.dispatch_args,
            "receipt verification cannot be combined with preflight/dispatch arguments",
        )
        validate_startup_flags()
        receipt, raw = read_preflight_receipt(args.verify_receipt)
        verify_recorded_files(receipt)
        print(
            json.dumps(
                {
                    "status": "STATIC_RECEIPT_STRUCTURE_AND_RECORDED_FILES_MATCH",
                    "receipt_sha256": sha256_bytes(raw),
                    "gpu_observation_replayed": False,
                    "model_execution_authorized": False,
                    "model_action_count": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    require(
        args.runtime_reference is not None
        and args.project_path is not None
        and args.receipt is not None,
        "preflight requires --runtime-reference, --project-path, and --receipt",
    )
    dispatch_args = list(args.dispatch_args)
    if dispatch_args[:1] == ["--"]:
        dispatch_args = dispatch_args[1:]
    receipt = build_preflight_receipt(
        reference_path=args.runtime_reference,
        project_path=args.project_path,
        minimum_free_gpu_mib=args.min_free_gpu_mib,
        dispatch_module=args.dispatch_module,
    )
    data = canonical_json_bytes(receipt)
    output = args.receipt.resolve(strict=False)
    require(
        all(not _within(output, root) for root in receipt["effective_sys_path"]),
        "preflight receipt output must be outside effective import roots",
    )
    atomic_write_new(output, data)
    print(
        json.dumps(
            {
                "status": STATUS,
                "receipt_sha256": sha256_bytes(data),
                "static_preflight_passed": True,
                "model_execution_authorized": False,
                "model_action_count": 0,
                "full_dependency_content_hash_bound": False,
                "dispatch_module": args.dispatch_module,
            },
            sort_keys=True,
        )
    )
    if args.dispatch_module is None:
        return 0
    return execute_controlled_dispatch(receipt, dispatch_args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LocalRuntimeBootstrapError as error:
        print(json.dumps({"status": "LOCAL_RUNTIME_PREFLIGHT_FAILED", "error": str(error)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
