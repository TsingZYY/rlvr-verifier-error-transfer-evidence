"""Build and validate the semantic model inventory required by R13.

This module only reads bytes and metadata.  It never imports an ML framework,
loads a tokenizer, deserializes weights, or performs a model action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "r13-semantic-model-inventory-r1"
STATUS = "STATIC_BYTES_BOUND_MODEL_NOT_LOADED"
REPOSITORY = "HuggingFaceTB/SmolLM2-360M-Instruct"
REVISION = "a10cc1512eabd3dde888204e902eca88bddb4951"
EXPECTED_FILES = (
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)
HASH_RE = re.compile(r"[0-9a-f]{64}")


class InventoryError(ValueError):
    """Raised when the local model snapshot is not exactly bindable."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _strict_object(data: bytes, label: str) -> dict[str, Any]:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InventoryError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise InventoryError(f"non-finite JSON constant in {label}: {token}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InventoryError(f"invalid UTF-8 JSON in {label}") from error
    require(isinstance(value, dict), f"{label} must be one JSON object")
    return value


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _relative_files(model_dir: Path) -> tuple[list[str], list[str]]:
    semantic_or_extra: list[str] = []
    cache_files: list[str] = []
    for path in sorted(model_dir.rglob("*"), key=lambda item: item.as_posix()):
        require(not _is_reparse_or_symlink(path), f"symlink or junction forbidden: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(model_dir).as_posix()
        if relative == ".cache" or relative.startswith(".cache/"):
            cache_files.append(relative)
        else:
            semantic_or_extra.append(relative)
    return semantic_or_extra, cache_files


def build_inventory(model_dir: Path) -> dict[str, Any]:
    selected = model_dir.resolve(strict=True)
    require(selected.is_dir(), "model path is not a directory")
    require(not _is_reparse_or_symlink(selected), "model directory cannot be a link")
    semantic, cache_files = _relative_files(selected)
    require(
        semantic == list(EXPECTED_FILES),
        f"semantic model member drift: expected {list(EXPECTED_FILES)}, observed {semantic}",
    )
    files = []
    for relative in EXPECTED_FILES:
        path = selected / relative
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    value = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "repository": REPOSITORY,
        "revision": REVISION,
        "semantic_files": files,
        "semantic_file_count": len(files),
        "ignored_cache_policy": {
            "root": ".cache/",
            "reason": "download metadata is not read by the frozen local model loader",
            "observed_file_count": len(cache_files),
            "observed_paths": cache_files,
            "included_in_semantic_hashes": False,
        },
        "model_loaded": False,
        "tokenizer_loaded": False,
        "model_forward_performed": False,
    }
    validate_inventory(value)
    return value


INVENTORY_KEYS = {
    "schema_version",
    "status",
    "repository",
    "revision",
    "semantic_files",
    "semantic_file_count",
    "ignored_cache_policy",
    "model_loaded",
    "tokenizer_loaded",
    "model_forward_performed",
}
FILE_KEYS = {"path", "size_bytes", "sha256"}
CACHE_KEYS = {
    "root",
    "reason",
    "observed_file_count",
    "observed_paths",
    "included_in_semantic_hashes",
}


def validate_inventory(value: dict[str, Any], model_dir: Path | None = None) -> None:
    require(set(value) == INVENTORY_KEYS, "inventory schema drift")
    require(value["schema_version"] == SCHEMA, "inventory version drift")
    require(value["status"] == STATUS, "inventory status drift")
    require(value["repository"] == REPOSITORY, "repository drift")
    require(value["revision"] == REVISION, "revision drift")
    require(value["model_loaded"] is False, "inventory cannot claim a model load")
    require(value["tokenizer_loaded"] is False, "inventory cannot claim a tokenizer load")
    require(
        value["model_forward_performed"] is False,
        "inventory cannot claim a model forward",
    )
    files = value["semantic_files"]
    require(isinstance(files, list), "semantic_files must be a list")
    require(value["semantic_file_count"] == len(EXPECTED_FILES), "file count drift")
    require(len(files) == len(EXPECTED_FILES), "semantic file coverage drift")
    observed_paths: list[str] = []
    for entry in files:
        require(isinstance(entry, dict) and set(entry) == FILE_KEYS, "file entry drift")
        path = entry["path"]
        require(isinstance(path, str), "file path must be a string")
        require("\\" not in path and not path.startswith("/"), "non-portable model path")
        require(".." not in Path(path).parts, "parent traversal in model path")
        observed_paths.append(path)
        require(
            isinstance(entry["size_bytes"], int) and entry["size_bytes"] >= 0,
            "invalid file size",
        )
        require(
            isinstance(entry["sha256"], str)
            and HASH_RE.fullmatch(entry["sha256"]) is not None,
            "invalid file SHA-256",
        )
    require(observed_paths == list(EXPECTED_FILES), "semantic file order or membership drift")
    cache = value["ignored_cache_policy"]
    require(isinstance(cache, dict) and set(cache) == CACHE_KEYS, "cache policy drift")
    require(cache["root"] == ".cache/", "cache root drift")
    require(cache["included_in_semantic_hashes"] is False, "cache entered semantic hashes")
    paths = cache["observed_paths"]
    require(isinstance(paths, list), "cache paths must be a list")
    require(cache["observed_file_count"] == len(paths), "cache count drift")
    require(
        paths == sorted(paths) and len(paths) == len(set(paths)),
        "cache paths are not ordered and unique",
    )
    require(
        all(isinstance(path, str) and path.startswith(".cache/") for path in paths),
        "non-cache path recorded as ignored cache",
    )
    if model_dir is not None:
        current = build_inventory(model_dir)
        require(
            current["semantic_files"] == files,
            "current semantic model bytes differ from inventory",
        )
        require(
            current["ignored_cache_policy"] == cache,
            "current ignored-cache inventory differs from bound inventory",
        )


def read_inventory(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    value = _strict_object(raw, str(path))
    validate_inventory(value)
    require(raw == canonical_json_bytes(value), "inventory is not canonical JSON")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require(not args.output.exists(), f"refusing to overwrite {args.output}")
    value = build_inventory(args.model_dir)
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
                "semantic_file_count": len(EXPECTED_FILES),
                "inventory_sha256": sha256_file(args.output),
                "model_loaded": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
