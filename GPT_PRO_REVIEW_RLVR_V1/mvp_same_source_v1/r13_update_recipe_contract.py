"""Freeze the exact source-update semantics inherited by R13 from R10."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "r13-source-update-recipe-contract-r1"
STATUS = "STATIC_SEMANTICS_BOUND_PRODUCTION_BACKEND_NOT_BOUND_MODEL_NOT_RUN"
STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
IDENTITIES = (1, 2, 3, 4, 5)
CANDIDATES = tuple(f"FINAL=K{index}" for index in range(7))
EXPECTED_UPDATE = {
    "gradient_clip_norm": 1.0,
    "learning_rate": 0.1,
    "lora_alpha": 8,
    "lora_dropout": 0.0,
    "lora_rank": 4,
    "lora_targets": ["q_proj", "v_proj"],
    "objective": "exact expected reward under the normalized fixed-candidate policy",
    "optimizer": "manual_sgd_no_state",
    "reset_to_identical_lora_initialization_per_source_rule": True,
    "reward": "1 for gold and source-rule wrong candidate; 0 for the other five candidates",
    "seed": 20260804,
    "steps": 1,
    "target_rule_blind_source_update": True,
}
EXPECTED_SCORING = {
    "candidate_policy": "softmax over the seven candidate scores",
    "candidate_score": "sum causal log probability of assistant response tokens including the common assistant terminator",
    "candidate_set": list(CANDIDATES),
    "target_metric": "mean over target calibration rows of log P(candidate(z+q)) minus log P(gold candidate(z))",
}
EXPECTED_MATHEMATICAL_UPDATE_SEMANTICS = {
    "per_row_policy": "p=softmax(seven candidate log-probability scores)",
    "per_row_reward": "reward(gold)=1; reward(candidate at latent offset r)=1; all others=0",
    "loss": "negative mean over 14 source rows of sum_c p(c)*reward(c)",
    "gradient": "one backward pass accumulated over the 14 row contributions",
    "clipping": "global trainable-gradient L2 norm clipped to 1.0",
    "parameter_step": "theta := theta - 0.1 * clipped_gradient",
    "optimizer_state": "none",
}
EXPECTED_SOURCE_UPDATE_INPUT_ALLOWLIST = [
    "bound model bytes and initial trainable snapshot",
    "bound stack source rows and their prompts/codebooks/gold labels",
    "ordered candidate set",
    "source identity r",
    "this update recipe and deterministic seed",
]
EXPECTED_SOURCE_UPDATE_FORBIDDEN_INPUTS = [
    "target arm",
    "target panel rows or prompts",
    "target codebook",
    "target scores or outcomes",
    "target evaluation order",
    "replicate label",
]
HASH_RE = re.compile(r"[0-9a-f]{64}")


class UpdateRecipeError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UpdateRecipeError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_hash(value: Any, field: str) -> str:
    require(isinstance(value, str) and HASH_RE.fullmatch(value) is not None, f"{field} must be SHA-256")
    return value


def strict_json(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            require(key not in value, f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                UpdateRecipeError(f"non-finite JSON in {path}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateRecipeError(f"invalid JSON: {path}") from error
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def validate_parent_config(value: dict[str, Any], expected_stack: str) -> None:
    require(value.get("schema_version") == "same-source-diagnostic-mvp-config-v1", "parent config version drift")
    require(value.get("status") == "FROZEN_BEFORE_MODEL_FORWARD", "parent config status drift")
    require(value.get("scientific_evidence") is False, "parent config overstates evidence")
    require(value.get("formal_experiment") is False, "parent config overstates formality")
    data = value.get("data")
    require(isinstance(data, dict), "parent data contract missing")
    require(data.get("mapping_stack_id") == expected_stack, "parent stack mismatch")
    require(data.get("source_split") == "SOURCE", "parent source split drift")
    require(data.get("target_split") == "TARGET_CALIBRATION", "parent target split drift")
    require(data.get("audit_access") == "FORBIDDEN", "audit access drift")
    require(data.get("source_rule_offsets_mod7") == list(IDENTITIES), "source identity drift")
    require(value.get("update") == EXPECTED_UPDATE, "parent update recipe drift")
    require(value.get("scoring") == EXPECTED_SCORING, "parent scoring recipe drift")


def build_contract(
    *,
    config_paths_by_stack: dict[str, Path],
    protocol_path: Path,
    source_bundles_path: Path,
    mapping_stacks_path: Path,
    model_inventory_path: Path,
    runtime_environment_path: Path,
    parent_runner_path: Path,
) -> dict[str, Any]:
    require(set(config_paths_by_stack) == set(STACKS), "parent config stack coverage drift")
    config_hashes: dict[str, str] = {}
    for stack in STACKS:
        path = config_paths_by_stack[stack]
        validate_parent_config(strict_json(path), stack)
        config_hashes[stack] = sha256_file(path)
    value = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "protocol_sha256": sha256_file(protocol_path),
        "source_bundles_sha256": sha256_file(source_bundles_path),
        "mapping_stacks_sha256": sha256_file(mapping_stacks_path),
        "model_inventory_sha256": sha256_file(model_inventory_path),
        "runtime_environment_reference_sha256": sha256_file(runtime_environment_path),
        "parent_r10_config_sha256_by_stack": config_hashes,
        "parent_r10_runner_sha256_reference_only": sha256_file(parent_runner_path),
        "ordered_stack_ids": list(STACKS),
        "source_identities": list(IDENTITIES),
        "source_rows_per_stack": 14,
        "candidate_set": list(CANDIDATES),
        "candidate_scoring": dict(EXPECTED_SCORING),
        "update": dict(EXPECTED_UPDATE),
        "mathematical_update_semantics": dict(EXPECTED_MATHEMATICAL_UPDATE_SEMANTICS),
        "source_update_input_allowlist": list(EXPECTED_SOURCE_UPDATE_INPUT_ALLOWLIST),
        "source_update_forbidden_inputs": list(EXPECTED_SOURCE_UPDATE_FORBIDDEN_INPUTS),
        "same_update_target_readout_contract": {
            "updates_per_stack_and_replicate": 5,
            "one_update_per_identity": True,
            "both_target_arms_share_exact_parameter_hash": True,
            "target_readout_has_no_gradient_or_state_mutation": True,
            "reset_to_same_initial_snapshot_before_each_identity": True,
        },
        "parent_runner_is_production_backend": False,
        "production_backend_adapter_bound": False,
        "full_dependency_content_hash_bound": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "tokenizer_loaded_by_this_contract": False,
        "model_weight_loaded_by_this_contract": False,
    }
    validate_contract(value)
    return value


TOP_KEYS = {
    "schema_version", "status", "protocol_sha256", "source_bundles_sha256",
    "mapping_stacks_sha256", "model_inventory_sha256",
    "runtime_environment_reference_sha256", "parent_r10_config_sha256_by_stack",
    "parent_r10_runner_sha256_reference_only", "ordered_stack_ids", "source_identities",
    "source_rows_per_stack", "candidate_set", "candidate_scoring", "update",
    "mathematical_update_semantics", "source_update_input_allowlist",
    "source_update_forbidden_inputs", "same_update_target_readout_contract",
    "parent_runner_is_production_backend", "production_backend_adapter_bound",
    "full_dependency_content_hash_bound", "model_execution_authorized",
    "model_execution_performed", "tokenizer_loaded_by_this_contract",
    "model_weight_loaded_by_this_contract",
}


def validate_contract(value: dict[str, Any]) -> None:
    require(set(value) == TOP_KEYS, "update contract schema drift")
    require(value["schema_version"] == SCHEMA, "update contract version drift")
    require(value["status"] == STATUS, "update contract status drift")
    for field in (
        "protocol_sha256", "source_bundles_sha256", "mapping_stacks_sha256",
        "model_inventory_sha256", "runtime_environment_reference_sha256",
        "parent_r10_runner_sha256_reference_only",
    ):
        validate_hash(value[field], field)
    configs = value["parent_r10_config_sha256_by_stack"]
    require(isinstance(configs, dict) and list(configs) == list(STACKS), "config hash coverage/order drift")
    for stack, digest in configs.items():
        validate_hash(digest, f"config hash {stack}")
    require(value["ordered_stack_ids"] == list(STACKS), "update stack drift")
    require(value["source_identities"] == list(IDENTITIES), "update identity drift")
    require(value["source_rows_per_stack"] == 14, "source row count drift")
    require(value["candidate_set"] == list(CANDIDATES), "candidate set drift")
    require(value["candidate_scoring"] == EXPECTED_SCORING, "candidate scoring drift")
    require(value["update"] == EXPECTED_UPDATE, "update recipe drift")
    require(
        value["mathematical_update_semantics"]
        == EXPECTED_MATHEMATICAL_UPDATE_SEMANTICS,
        "mathematical update semantics drift",
    )
    allowlist = value["source_update_input_allowlist"]
    forbidden = value["source_update_forbidden_inputs"]
    require(
        allowlist == EXPECTED_SOURCE_UPDATE_INPUT_ALLOWLIST,
        "source input allowlist drift",
    )
    require(
        forbidden == EXPECTED_SOURCE_UPDATE_FORBIDDEN_INPUTS,
        "source forbidden-input contract drift",
    )
    same = value["same_update_target_readout_contract"]
    require(same == {
        "updates_per_stack_and_replicate": 5,
        "one_update_per_identity": True,
        "both_target_arms_share_exact_parameter_hash": True,
        "target_readout_has_no_gradient_or_state_mutation": True,
        "reset_to_same_initial_snapshot_before_each_identity": True,
    }, "same-update readout contract drift")
    for field in (
        "parent_runner_is_production_backend", "production_backend_adapter_bound",
        "full_dependency_content_hash_bound", "model_execution_authorized",
        "model_execution_performed", "tokenizer_loaded_by_this_contract",
        "model_weight_loaded_by_this_contract",
    ):
        require(value[field] is False, f"update contract {field} must remain false")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--runtime-environment", type=Path, required=True)
    parser.add_argument("--parent-runner", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _configs(config_dir: Path) -> dict[str, Path]:
    paths = sorted(config_dir.glob("*_config.json"))
    result: dict[str, Path] = {}
    for path in paths:
        value = strict_json(path)
        stack = value.get("data", {}).get("mapping_stack_id")
        if stack in STACKS:
            require(stack not in result, f"duplicate parent config for {stack}")
            result[stack] = path
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require(not args.output.exists(), f"refusing to overwrite {args.output}")
    value = build_contract(
        config_paths_by_stack=_configs(args.config_dir),
        protocol_path=args.protocol,
        source_bundles_path=args.source_bundles,
        mapping_stacks_path=args.mapping_stacks,
        model_inventory_path=args.model_inventory,
        runtime_environment_path=args.runtime_environment,
        parent_runner_path=args.parent_runner,
    )
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
    print(json.dumps({
        "status": STATUS,
        "contract_sha256": sha256_file(args.output),
        "production_backend_adapter_bound": False,
        "model_execution_performed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
