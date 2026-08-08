"""CPU-only immutable contract for the repaired same-source diagnostic.

This module intentionally uses only the Python standard library.  It can be
imported and executed before any tokenizer, model, torch, or CUDA operation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import platform
import re
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

REVIEW_ROOT = Path(__file__).resolve().parents[1]
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from commitment_core import canonical_json_bytes, rows_commitment, sha256_bytes


MANIFEST_SCHEMA = "same-source-diagnostic-execution-manifest-r5"
MASTER_CONTRACT_SCHEMA = "same-source-eight-stack-inclusion-contract-r5"
CONFIG_SCHEMA = "same-source-diagnostic-mvp-config-v1"
ADDENDUM_SCHEMA = "same-source-diagnostic-mvp-determinism-addendum-r5"
AUTHORIZATION_SCHEMA = "same-source-action-authorization-receipt-r5"
AUTHORIZATION_VERSION = "R5_EXACT_EIGHT_STACK_DIAGNOSTIC_V1"
AUTHORIZATION_ACTION_ID = "REPAIRED_EIGHT_STACK_FIXED_CANDIDATE_DIAGNOSTIC_R5"
INVOCATION_RECEIPT_SCHEMA = "same-source-invocation-start-receipt-r5"
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
EVIDENCE_BOUNDARY = {
    "same_fpr_evidence": "NOT_SAME_FPR_EVIDENCE",
    "rlvr_mode": "NOT_SAMPLED_RLVR",
    "data_scope": "CALIBRATION_ONLY",
    "audit_status": "NOT_HIDDEN_AUDIT",
    "formal_g1_status": "NOT_FORMAL_G1",
    "scientific_evidence_status": "SCIENTIFIC_EVIDENCE_FALSE",
}
COMPLETION_EVIDENCE_BOUNDARY = {
    **EVIDENCE_BOUNDARY,
    "old_r2_status": "OLD_R2_EXCLUDED",
}
NORM_EVIDENCE_BOUNDARY = {
    "validation_status": "RUNNER_REPORTED_INTERNAL_CONSISTENCY_ONLY",
    "independent_validation_input": "NOT_AN_INDEPENDENT_VALIDATION_INPUT",
    "used_for_diagnostic_gates": False,
    "used_for_scientific_explanation": False,
}
EXPECTED_STACK_IDS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP1-M1-A_TO_B",
    "TP1-M1-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
    "TP2-M1-A_TO_B",
    "TP2-M1-B_TO_A",
)
EXPECTED_OFFSETS = [1, 2, 3, 4, 5]
TOKENIZER_FILES = (
    "merges.txt",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)
MODEL_ROOT_FILES = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    *TOKENIZER_FILES,
)
FROZEN_MODEL = {
    "repository": "HuggingFaceTB/SmolLM2-360M-Instruct",
    "revision": "a10cc1512eabd3dde888204e902eca88bddb4951",
    "local_path": "models/SmolLM2-360M-Instruct-a10cc151",
    "weight_file": "model.safetensors",
    "precision": "base_bfloat16_lora_float32",
}
FROZEN_RUNTIME = {
    "python": "3.10.20",
    "torch": "2.8.0+cu128",
    "transformers": "4.55.4",
    "peft": "0.17.1",
    "accelerate": "1.10.1",
    "device": "cuda",
}
FROZEN_SCORING = {
    "candidate_set": [f"FINAL=K{i}" for i in range(7)],
    "candidate_score": "sum causal log probability of assistant response tokens including the common assistant terminator",
    "candidate_policy": "softmax over the seven candidate scores",
    "target_metric": "mean over target calibration rows of log P(candidate(z+q)) minus log P(gold candidate(z))",
}
FROZEN_UPDATE = {
    "target_rule_blind_source_update": True,
    "reset_to_identical_lora_initialization_per_source_rule": True,
    "reward": "1 for gold and source-rule wrong candidate; 0 for the other five candidates",
    "objective": "exact expected reward under the normalized fixed-candidate policy",
    "steps": 1,
    "optimizer": "manual_sgd_no_state",
    "learning_rate": 0.1,
    "gradient_clip_norm": 1.0,
    "lora_rank": 4,
    "lora_alpha": 8,
    "lora_dropout": 0.0,
    "lora_targets": ["q_proj", "v_proj"],
    "seed": 20260804,
}
FROZEN_GATES = {
    "mean_source_wrong_probability_mass_min": 0.00001,
    "mean_source_relative_advantage_min": 0.01,
    "candidate_supervised_token_count_range_max": 1,
    "all_five_source_rules_must_pass": True,
    "non_finite_value_action": "STOP",
}
FROZEN_ESTIMAND = {
    "cell_effect": "post target metric minus pre target metric",
    "within_update_diagonal_excess": "effect(r,r) minus mean over q != r of effect(r,q)",
    "stack_summary": "equal mean over the five source-rule diagonal excess values",
}
FROZEN_CLAIM_BOUNDARY = [
    "Not a formal run and not confirmatory evidence.",
    "The eight-stack fixed-candidate screen is diagnostic development evidence, not a formal G1 result.",
    "Fixed-candidate exact expected-reward optimization is an RLVR surrogate, not sampled RLVR.",
    "Calibration-only readouts are development data and cannot be reported as hidden audit performance.",
    "A pass only justifies the next frozen G1 and sampled-RLVR bridge experiments.",
]
EXPECTED_CONFIG_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "run_label",
    "purpose",
    "model",
    "runtime",
    "data",
    "scoring",
    "update",
    "diagnostic_gates",
    "estimand",
    "claim_boundary",
    "evidence_boundary",
}
EXPECTED_SECTION_KEYS = {
    "model": {"repository", "revision", "local_path", "weight_file", "precision"},
    "runtime": {"python", "torch", "transformers", "peft", "accelerate", "device"},
    "data": {
        "mapping_stack_id",
        "source_split",
        "target_split",
        "audit_access",
        "source_rule_offsets_mod7",
        "target_rule_offsets_mod7",
    },
    "scoring": {"candidate_set", "candidate_score", "candidate_policy", "target_metric"},
    "update": {
        "target_rule_blind_source_update",
        "reset_to_identical_lora_initialization_per_source_rule",
        "reward",
        "objective",
        "steps",
        "optimizer",
        "learning_rate",
        "gradient_clip_norm",
        "lora_rank",
        "lora_alpha",
        "lora_dropout",
        "lora_targets",
        "seed",
    },
    "diagnostic_gates": {
        "mean_source_wrong_probability_mass_min",
        "mean_source_relative_advantage_min",
        "candidate_supervised_token_count_range_max",
        "all_five_source_rules_must_pass",
        "non_finite_value_action",
    },
    "estimand": {"cell_effect", "within_update_diagonal_excess", "stack_summary"},
}


class ContractError(ValueError):
    """Raised when a frozen CPU/static invariant is violated."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain one JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ContractError(f"{path}:{number} is not an object")
        rows.append(value)
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def validate_config(config: dict[str, Any]) -> None:
    _require(set(config) == EXPECTED_CONFIG_KEYS, "config top-level schema drift")
    _require(config["schema_version"] == CONFIG_SCHEMA, "wrong config schema")
    _require(config["status"] == "FROZEN_BEFORE_MODEL_FORWARD", "config is not frozen")
    _require(config["scientific_evidence"] is False, "scientific status washing")
    _require(config["formal_experiment"] is False, "formal status washing")
    _require(config["run_label"] == "DIAGNOSTIC_MVP_NOT_FORMAL", "wrong run label")
    _require(
        config["purpose"]
        == "Low-cost same-source causal-chain smoke test before a formal G1/RLVR run.",
        "purpose drift",
    )
    for section, keys in EXPECTED_SECTION_KEYS.items():
        value = config.get(section)
        _require(isinstance(value, dict) and set(value) == keys, f"{section} schema drift")
    data = config["data"]
    _require(data["mapping_stack_id"] in EXPECTED_STACK_IDS, "mapping stack is not frozen")
    _require(data["source_split"] == "SOURCE", "source split drift")
    _require(data["target_split"] == "TARGET_CALIBRATION", "target split drift")
    _require(data["audit_access"] == "FORBIDDEN", "audit access must be forbidden")
    _require(data["source_rule_offsets_mod7"] == EXPECTED_OFFSETS, "source offsets drift")
    _require(data["target_rule_offsets_mod7"] == EXPECTED_OFFSETS, "target offsets drift")
    _require(
        data["source_rule_offsets_mod7"] == data["target_rule_offsets_mod7"],
        "source/target offset mismatch",
    )
    _require(config["model"] == FROZEN_MODEL, "model contract drift")
    _require(config["runtime"] == FROZEN_RUNTIME, "runtime contract drift")
    _require(config["scoring"] == FROZEN_SCORING, "scoring contract drift")
    _require(config["update"] == FROZEN_UPDATE, "update contract drift")
    _require(config["diagnostic_gates"] == FROZEN_GATES, "gate contract drift")
    _require(config["estimand"] == FROZEN_ESTIMAND, "estimand contract drift")
    _require(config["claim_boundary"] == FROZEN_CLAIM_BOUNDARY, "claim boundary drift")
    _require(config["evidence_boundary"] == EVIDENCE_BOUNDARY, "evidence boundary drift")


def validate_evidence_boundary(value: Any, *, completion: bool = False) -> None:
    expected = COMPLETION_EVIDENCE_BOUNDARY if completion else EVIDENCE_BOUNDARY
    _require(value == expected, "exact evidence-boundary labels required")


def _parse_rfc3339_utc(value: Any, field: str) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z"), f"{field} must be RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ContractError(f"{field} must be RFC3339 UTC") from error
    _require(parsed.tzinfo == timezone.utc, f"{field} must be UTC")
    return parsed


def _model_file_manifest(model_dir: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    _require(model_dir.is_dir() and not model_dir.is_symlink(), "model directory is invalid")
    files: list[dict[str, Any]] = []
    for path in sorted(model_dir.rglob("*"), key=lambda item: item.relative_to(model_dir).as_posix()):
        _require(not path.is_symlink(), f"model inventory contains symlink: {path}")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(model_dir).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    observed = {row["path"] for row in files}
    missing = set(MODEL_ROOT_FILES).difference(observed)
    _require(not missing, f"model directory missing files: {sorted(missing)}")
    _require(
        model_dir.name == Path(config["model"]["local_path"]).name,
        "config model local_path differs from CLI model directory",
    )
    weight_file = config["model"]["weight_file"]
    _require(weight_file in observed, "configured weight file is missing")
    indexes = [row["path"] for row in files if row["path"].endswith(".index.json")]
    if indexes:
        for relative in indexes:
            index = read_json(model_dir / Path(relative))
            weight_map = index.get("weight_map")
            _require(isinstance(weight_map, dict) and weight_map, "weight index is invalid")
            shards = {str(value) for value in weight_map.values()}
            _require(shards.issubset(observed), "weight index references missing shard")
    return files


def _chat_template_hash(model_dir: Path) -> str:
    tokenizer_config = read_json(model_dir / "tokenizer_config.json")
    template = tokenizer_config.get("chat_template")
    _require(isinstance(template, str) and template, "tokenizer chat template is missing")
    return sha256_bytes(template.encode("utf-8"))


def _runtime_observed() -> dict[str, str]:
    def version(distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return "MISSING"

    return {
        "python": platform.python_version(),
        "torch": version("torch"),
        "transformers": version("transformers"),
        "peft": version("peft"),
        "accelerate": version("accelerate"),
        "platform": platform.platform(),
    }


def _stack_ids(mapping_path: Path) -> list[str]:
    rows = read_jsonl(mapping_path)
    ids = sorted(str(row.get("mapping_stack_id", row.get("stack_id", ""))) for row in rows)
    _require(ids == sorted(EXPECTED_STACK_IDS), "mapping stack coverage is not frozen 8/8")
    return ids


def validate_stack_membership(
    source_path: Path,
    target_path: Path,
    mapping_path: Path,
) -> dict[str, dict[str, str]]:
    """Validate immutable source/target membership without trusting split labels alone."""

    stack_ids = _stack_ids(mapping_path)
    bundles = read_jsonl(source_path)
    _require(len(bundles) == 8, "source bundle count is not 8")
    source_by_stack: dict[str, list[dict[str, Any]]] = {}
    for bundle in bundles:
        stack_id = str(bundle.get("mapping_stack_id", ""))
        rows = bundle.get("rows")
        _require(stack_id in stack_ids and stack_id not in source_by_stack, "source stack reuse")
        _require(isinstance(rows, list) and len(rows) == 14, f"{stack_id}: source rows not 14")
        _require(
            all(
                isinstance(row, dict)
                and row.get("mapping_stack_id") == stack_id
                and row.get("split_role") == "SOURCE"
                for row in rows
            ),
            f"{stack_id}: source membership mismatch",
        )
        source_by_stack[stack_id] = rows
    _require(sorted(source_by_stack) == stack_ids, "source stack coverage is not 8/8")

    targets = read_jsonl(target_path)
    target_by_stack: dict[str, list[dict[str, Any]]] = {stack_id: [] for stack_id in stack_ids}
    for row in targets:
        stack_id = str(row.get("mapping_stack_id", ""))
        _require(stack_id in target_by_stack, "unknown target stack")
        _require(row.get("split_role") == "TARGET_CALIBRATION", "relabeled target row")
        target_by_stack[stack_id].append(row)
    _require(
        all(len(rows) == 7 for rows in target_by_stack.values()),
        "target membership is not exactly 7 rows per stack",
    )

    source_row_ids = {
        str(row.get("row_id")) for rows in source_by_stack.values() for row in rows
    }
    target_row_ids = {
        str(row.get("row_id")) for rows in target_by_stack.values() for row in rows
    }
    source_lineages = {
        str(row.get("lineage_id")) for rows in source_by_stack.values() for row in rows
    }
    target_lineages = {
        str(row.get("lineage_id")) for rows in target_by_stack.values() for row in rows
    }
    _require(len(source_row_ids) == 112 and len(target_row_ids) == 56, "row id reuse")
    _require(source_row_ids.isdisjoint(target_row_ids), "source/target row overlap")
    _require(source_lineages.isdisjoint(target_lineages), "source/target lineage overlap")
    return {
        stack_id: {
            "source_rows_commitment": rows_commitment(source_by_stack[stack_id]),
            "target_rows_commitment": rows_commitment(target_by_stack[stack_id]),
            "source_trace_semantics": [
                row_trace_semantics(row) for row in source_by_stack[stack_id]
            ],
            "target_trace_semantics": [
                row_trace_semantics(row) for row in target_by_stack[stack_id]
            ],
        }
        for stack_id in stack_ids
    }


def row_trace_semantics(row: dict[str, Any]) -> dict[str, Any]:
    latent_to_candidate = row.get("codebook", {}).get("latent_to_candidate")
    _require(
        isinstance(latent_to_candidate, list)
        and len(latent_to_candidate) == 7
        and len(set(latent_to_candidate)) == 7,
        "row codebook is invalid",
    )
    z = row.get("canonical_z")
    _require(type(z) is int and 0 <= z < 7, "row canonical_z is invalid")
    expected_gold = latent_to_candidate[z]
    _require(row.get("gold_candidate") == expected_gold, "row gold candidate mismatch")
    return {
        "row_id": str(row.get("row_id")),
        "gold_candidate": expected_gold,
        "offset_candidates": {
            f"Z7_PLUS{offset}": latent_to_candidate[(z + offset) % 7]
            for offset in EXPECTED_OFFSETS
        },
    }


def build_manifest(
    *,
    config_path: Path,
    model_dir: Path,
    source_path: Path,
    target_path: Path,
    mapping_path: Path,
    runner_path: Path,
    validator_path: Path,
    asset_validation_path: Path,
    determinism_addendum_path: Path,
    audit_seal_path: Path,
    action_authorization: bool,
) -> dict[str, Any]:
    _require(action_authorization is False, "static manifest cannot grant model authorization")
    config = read_json(config_path)
    validate_config(config)
    validation = read_json(asset_validation_path)
    _require(validation.get("valid") is True and validation.get("error_count") == 0, "asset validation failed")
    metrics = validation.get("metrics", {})
    _require(metrics.get("prompt_visible_semantics_mismatches") == 0, "visible prompt mismatch")
    _require(metrics.get("prompt_visible_parse_errors") == 0, "visible prompt parse error")
    asset_root = source_path.resolve().parent
    _require(target_path.resolve().parent == asset_root, "source/target asset roots differ")
    _require(mapping_path.resolve().parent == asset_root, "mapping asset root differs")
    _require(validation.get("portable_root") == ".", "validation portability marker missing")
    critical_hashes = validation.get("critical_asset_sha256")
    _require(isinstance(critical_hashes, dict), "validation critical hash map missing")
    for path in (source_path, target_path, mapping_path, audit_seal_path):
        _require(
            critical_hashes.get(path.name) == sha256_file(path),
            f"validation/asset hash mismatch: {path.name}",
        )
    addendum = read_json(determinism_addendum_path)
    _require(
        addendum.get("schema_version") == ADDENDUM_SCHEMA
        and addendum.get("status") == "FROZEN_BEFORE_AUTHORIZED_REPLICATES",
        "determinism addendum drift",
    )
    _require(
        addendum.get("base_config_sha256") == sha256_file(config_path),
        "determinism addendum/config binding mismatch",
    )
    _require(addendum.get("scientific_evidence") is False, "addendum status washing")
    validate_evidence_boundary(addendum.get("evidence_boundary"))
    seal = read_json(audit_seal_path)
    _require(seal.get("seal_status") == "SEALED_PRE_MODEL_ACTION", "audit seal is invalid")

    model_files = _model_file_manifest(model_dir, config)
    observed_runtime = _runtime_observed()
    for key in ("python", "torch", "transformers", "peft", "accelerate"):
        _require(
            observed_runtime[key] == config["runtime"][key],
            f"runtime version mismatch: {key}",
        )
    membership = validate_stack_membership(source_path, target_path, mapping_path)
    bindings = {
        "config_sha256": sha256_file(config_path),
        "source_bundles_sha256": sha256_file(source_path),
        "target_calibration_sha256": sha256_file(target_path),
        "mapping_stacks_sha256": sha256_file(mapping_path),
        "runner_sha256": sha256_file(runner_path),
        "validator_sha256": sha256_file(validator_path),
        "static_contract_sha256": sha256_file(Path(__file__).resolve()),
        "asset_validation_sha256": sha256_file(asset_validation_path),
        "determinism_addendum_sha256": sha256_file(determinism_addendum_path),
        "audit_seal_receipt_sha256": sha256_file(audit_seal_path),
        "audit_file_sha256_from_seal": seal.get("audit_file_sha256"),
        "model_recursive_inventory": model_files,
        "model_recursive_inventory_sha256": sha256_bytes(
            canonical_json_bytes(model_files)
        ),
        "chat_template_sha256": _chat_template_hash(model_dir),
    }
    return {
        "schema_version": MANIFEST_SCHEMA,
        "status": "CPU_STATIC_REPAIR_PASS_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "mapping_stack_ids": _stack_ids(mapping_path),
        "selected_mapping_stack_id": config["data"]["mapping_stack_id"],
        "stack_membership_commitments": membership,
        "source_rule_offsets_mod7": list(config["data"]["source_rule_offsets_mod7"]),
        "target_rule_offsets_mod7": list(config["data"]["target_rule_offsets_mod7"]),
        "bindings": bindings,
        "runtime_expected": dict(config["runtime"]),
        "runtime_observed_at_manifest_build": observed_runtime,
        "determinism_environment": {"CUBLAS_WORKSPACE_CONFIG": ":4096:8"},
        "audit_contract": {
            "audit_content_accessed_during_cpu_asset_validation": True,
            "current_audit_hidden_eligibility": False,
            "runner_audit_row_access": "FORBIDDEN",
            "bound_by_preexisting_seal": True,
            "formal_hidden_audit_custody_complete": False,
        },
        "authorization": {
            "cpu_static_repair": True,
            "tokenizer_load": action_authorization,
            "model_weight_load": action_authorization,
            "model_forward": action_authorization,
            "gradient": action_authorization,
            "optimizer_step": action_authorization,
            "fixed_candidate_eight_stack_diagnostic": action_authorization,
            "sampled_rlvr": False,
        },
    }


def build_master_inclusion_contract(
    *,
    configs_by_stack: dict[str, Path],
    manifests_by_stack: dict[str, Path],
) -> dict[str, Any]:
    """Bind the exact eight-stack screen while excluding adaptive selection."""

    _require(
        set(configs_by_stack) == set(EXPECTED_STACK_IDS),
        "master contract config coverage is not exact 8/8",
    )
    _require(
        set(manifests_by_stack) == set(EXPECTED_STACK_IDS),
        "master contract manifest coverage is not exact 8/8",
    )
    config_hashes: dict[str, str] = {}
    manifest_hashes: dict[str, str] = {}
    reference_config: dict[str, Any] | None = None
    shared_manifest_bindings: dict[str, Any] | None = None
    for stack_id in EXPECTED_STACK_IDS:
        config = read_json(configs_by_stack[stack_id])
        validate_config(config)
        _require(
            config["data"]["mapping_stack_id"] == stack_id,
            f"config stack binding mismatch: {stack_id}",
        )
        normalized_config = json.loads(json.dumps(config))
        normalized_config["data"]["mapping_stack_id"] = "<STACK_ID>"
        if reference_config is None:
            reference_config = normalized_config
        else:
            _require(
                normalized_config == reference_config,
                f"config differs beyond mapping_stack_id: {stack_id}",
            )
        manifest = read_json(manifests_by_stack[stack_id])
        _require(manifest.get("schema_version") == MANIFEST_SCHEMA, "master manifest schema drift")
        _require(manifest.get("scientific_evidence") is False, "master manifest status washing")
        _require(manifest.get("formal_experiment") is False, "master manifest formal washing")
        validate_evidence_boundary(manifest.get("evidence_boundary"))
        _require(
            manifest.get("selected_mapping_stack_id") == stack_id,
            f"manifest stack binding mismatch: {stack_id}",
        )
        _require(
            manifest.get("bindings", {}).get("config_sha256")
            == sha256_file(configs_by_stack[stack_id]),
            f"manifest/config hash mismatch: {stack_id}",
        )
        authorization = manifest.get("authorization", {})
        _require(
            authorization
            == {
                "cpu_static_repair": True,
                "tokenizer_load": False,
                "model_weight_load": False,
                "model_forward": False,
                "gradient": False,
                "optimizer_step": False,
                "fixed_candidate_eight_stack_diagnostic": False,
                "sampled_rlvr": False,
            },
            f"manifest self-authorization detected: {stack_id}",
        )
        normalized_bindings = dict(manifest["bindings"])
        normalized_bindings.pop("config_sha256")
        normalized_bindings.pop("determinism_addendum_sha256")
        if shared_manifest_bindings is None:
            shared_manifest_bindings = normalized_bindings
        else:
            _require(
                normalized_bindings == shared_manifest_bindings,
                f"shared manifest bindings drift: {stack_id}",
            )
        config_hashes[stack_id] = sha256_file(configs_by_stack[stack_id])
        manifest_hashes[stack_id] = sha256_file(manifests_by_stack[stack_id])
    assert shared_manifest_bindings is not None
    return {
        "schema_version": MASTER_CONTRACT_SCHEMA,
        "status": "FROZEN_EIGHT_STACK_SCREEN_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_stack_ids": list(EXPECTED_STACK_IDS),
        "required_stack_count": 8,
        "config_sha256_by_stack": config_hashes,
        "manifest_sha256_by_stack": manifest_hashes,
        "shared_runner_sha256": shared_manifest_bindings["runner_sha256"],
        "shared_validator_sha256": shared_manifest_bindings["validator_sha256"],
        "shared_completion_validator_sha256": sha256_file(
            Path(__file__).resolve().with_name("validate_eight_stack_completion.py")
        ),
        "shared_static_contract_sha256": shared_manifest_bindings[
            "static_contract_sha256"
        ],
        "shared_model_recursive_inventory_sha256": shared_manifest_bindings[
            "model_recursive_inventory_sha256"
        ],
        "inclusion_rule": "RUN_ALL_EIGHT_STACKS_WITHOUT_SELECTION_OR_DELETION",
        "exclusion_rule": "NO_STACK_IDENTITY_THRESHOLD_OR_HYPERPARAMETER_ADAPTATION",
        "claim_boundary": list(FROZEN_CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
        },
    }


def validate_master_inclusion_contract(master: dict[str, Any]) -> None:
    required_keys = {
        "schema_version",
        "status",
        "scientific_evidence",
        "formal_experiment",
        "evidence_boundary",
        "model_execution_performed",
        "ordered_stack_ids",
        "required_stack_count",
        "config_sha256_by_stack",
        "manifest_sha256_by_stack",
        "shared_runner_sha256",
        "shared_validator_sha256",
        "shared_completion_validator_sha256",
        "shared_static_contract_sha256",
        "shared_model_recursive_inventory_sha256",
        "inclusion_rule",
        "exclusion_rule",
        "claim_boundary",
        "authorization",
    }
    _require(set(master) == required_keys, "master inclusion contract schema drift")
    _require(master["schema_version"] == MASTER_CONTRACT_SCHEMA, "wrong master contract schema")
    _require(
        master["status"] == "FROZEN_EIGHT_STACK_SCREEN_AUTHORIZATION_PENDING",
        "master contract status drift",
    )
    _require(master["scientific_evidence"] is False, "master scientific status washing")
    _require(master["formal_experiment"] is False, "master formal status washing")
    validate_evidence_boundary(master["evidence_boundary"])
    _require(master["model_execution_performed"] is False, "master execution status washing")
    _require(master["ordered_stack_ids"] == list(EXPECTED_STACK_IDS), "master stack order drift")
    _require(master["required_stack_count"] == 8, "master stack count drift")
    for field in ("config_sha256_by_stack", "manifest_sha256_by_stack"):
        values = master[field]
        _require(isinstance(values, dict) and set(values) == set(EXPECTED_STACK_IDS), f"{field} coverage drift")
        _require(
            all(
                isinstance(value, str)
                and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                for value in values.values()
            ),
            f"{field} hash format drift",
        )
    for field in (
        "shared_runner_sha256",
        "shared_validator_sha256",
        "shared_completion_validator_sha256",
        "shared_static_contract_sha256",
        "shared_model_recursive_inventory_sha256",
    ):
        _require(
            isinstance(master[field], str)
            and re.fullmatch(r"[0-9a-f]{64}", master[field]) is not None,
            f"{field} hash format drift",
        )
    _require(
        master["inclusion_rule"] == "RUN_ALL_EIGHT_STACKS_WITHOUT_SELECTION_OR_DELETION",
        "master inclusion rule drift",
    )
    _require(
        master["exclusion_rule"] == "NO_STACK_IDENTITY_THRESHOLD_OR_HYPERPARAMETER_ADAPTATION",
        "master exclusion rule drift",
    )
    _require(master["claim_boundary"] == FROZEN_CLAIM_BOUNDARY, "master claim boundary drift")
    _require(
        master["authorization"]
        == {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
        },
        "master contract cannot self-authorize",
    )


def verify_preflight(
    manifest: dict[str, Any],
    *,
    config_path: Path,
    model_dir: Path,
    source_path: Path,
    target_path: Path,
    mapping_path: Path,
    runner_path: Path,
    validator_path: Path,
    asset_validation_path: Path,
    determinism_addendum_path: Path,
    audit_seal_path: Path,
    require_model_authorization: bool,
    expected_manifest_sha256: str,
    master_inclusion_contract_path: Path | None = None,
    authorization_receipt_path: Path | None = None,
    expected_authorization_receipt_sha256: str | None = None,
) -> None:
    _require(manifest.get("schema_version") == MANIFEST_SCHEMA, "manifest schema drift")
    _require(manifest.get("scientific_evidence") is False, "manifest status washing")
    validate_evidence_boundary(manifest.get("evidence_boundary"))
    config = read_json(config_path)
    validate_config(config)
    expected = build_manifest(
        config_path=config_path,
        model_dir=model_dir,
        source_path=source_path,
        target_path=target_path,
        mapping_path=mapping_path,
        runner_path=runner_path,
        validator_path=validator_path,
        asset_validation_path=asset_validation_path,
        determinism_addendum_path=determinism_addendum_path,
        audit_seal_path=audit_seal_path,
        action_authorization=False,
    )
    _require(manifest == expected, "execution manifest or bound assets drifted")
    manifest_sha256 = sha256_bytes(canonical_json_bytes(manifest))
    _require(
        expected_manifest_sha256 == manifest_sha256,
        "external expected manifest hash mismatch",
    )
    _require(os.environ.get("CUBLAS_WORKSPACE_CONFIG") == ":4096:8", "CUBLAS determinism env missing")
    if require_model_authorization:
        _require(
            master_inclusion_contract_path is not None
            and authorization_receipt_path is not None,
            "external authorization receipt missing",
        )
        _require(
            isinstance(expected_authorization_receipt_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_authorization_receipt_sha256)
            is not None,
            "external authorization receipt SHA-256 required",
        )
        verify_authorization_receipt(
            receipt_path=authorization_receipt_path,
            master_contract_path=master_inclusion_contract_path,
            selected_stack_id=manifest["selected_mapping_stack_id"],
            selected_manifest_sha256=manifest_sha256,
            manifest=manifest,
            expected_authorization_receipt_sha256=expected_authorization_receipt_sha256,
        )


def verify_authorization_receipt(
    *,
    receipt_path: Path,
    master_contract_path: Path,
    selected_stack_id: str,
    selected_manifest_sha256: str,
    manifest: dict[str, Any],
    expected_authorization_receipt_sha256: str,
) -> None:
    _require(
        sha256_file(receipt_path) == expected_authorization_receipt_sha256,
        "external authorization receipt hash mismatch",
    )
    receipt = read_json(receipt_path)
    master = read_json(master_contract_path)
    validate_master_inclusion_contract(master)
    required_receipt_keys = {
        "schema_version",
        "status",
        "action_id",
        "authorization_id",
        "issued_at_utc",
        "master_inclusion_contract_sha256",
        "ordered_stack_ids",
        "config_sha256_by_stack",
        "manifest_sha256_by_stack",
        "runner_sha256",
        "validator_sha256",
        "completion_validator_sha256",
        "static_contract_sha256",
        "model_recursive_inventory_sha256",
        "allowed_operations",
        "forbidden_operations",
        "version",
        "expires_at_utc",
        "evidence_boundary",
    }
    _require(set(receipt) == required_receipt_keys, "authorization receipt schema drift")
    _require(
        receipt["schema_version"] == AUTHORIZATION_SCHEMA
        and receipt["status"] == "AUTHORIZED_BY_USER"
        and receipt["action_id"] == AUTHORIZATION_ACTION_ID,
        "authorization receipt does not authorize the exact action",
    )
    _require(receipt["version"] == AUTHORIZATION_VERSION, "authorization version drift")
    _require(
        isinstance(receipt["authorization_id"], str)
        and UUID4_RE.fullmatch(receipt["authorization_id"]) is not None,
        "authorization_id must be a random UUIDv4",
    )
    issued = _parse_rfc3339_utc(receipt["issued_at_utc"], "issued_at_utc")
    expires = _parse_rfc3339_utc(receipt["expires_at_utc"], "expires_at_utc")
    now = datetime.now(timezone.utc)
    _require(issued <= now < expires, "authorization receipt is not currently valid")
    _require(expires > issued, "authorization expiry must follow issuance")
    validate_evidence_boundary(receipt["evidence_boundary"])
    master_sha256 = sha256_file(master_contract_path)
    _require(
        receipt["master_inclusion_contract_sha256"] == master_sha256,
        "authorization/master contract binding mismatch",
    )
    _require(receipt["ordered_stack_ids"] == list(EXPECTED_STACK_IDS), "authorization stack order drift")
    _require(
        receipt["config_sha256_by_stack"] == master["config_sha256_by_stack"]
        and receipt["manifest_sha256_by_stack"]
        == master["manifest_sha256_by_stack"],
        "authorization/master eight-stack map mismatch",
    )
    _require(
        receipt["manifest_sha256_by_stack"].get(selected_stack_id)
        == selected_manifest_sha256,
        "selected manifest is not externally authorized",
    )
    _require(
        receipt["config_sha256_by_stack"].get(selected_stack_id)
        == manifest["bindings"]["config_sha256"],
        "selected config is not externally authorized",
    )
    bindings = manifest["bindings"]
    for receipt_key, binding_key in (
        ("runner_sha256", "runner_sha256"),
        ("validator_sha256", "validator_sha256"),
        ("static_contract_sha256", "static_contract_sha256"),
        ("model_recursive_inventory_sha256", "model_recursive_inventory_sha256"),
    ):
        _require(
            receipt[receipt_key] == bindings[binding_key],
            f"authorization binding mismatch: {receipt_key}",
        )
    _require(
        receipt["runner_sha256"] == master["shared_runner_sha256"]
        and receipt["validator_sha256"] == master["shared_validator_sha256"]
        and receipt["completion_validator_sha256"]
        == master["shared_completion_validator_sha256"]
        and receipt["static_contract_sha256"]
        == master["shared_static_contract_sha256"]
        and receipt["model_recursive_inventory_sha256"]
        == master["shared_model_recursive_inventory_sha256"],
        "authorization/master shared binding mismatch",
    )
    _require(
        receipt["allowed_operations"]
        == [
            "tokenizer_load",
            "model_weight_load",
            "model_forward",
            "gradient",
            "optimizer_step",
            "fixed_candidate_eight_stack_diagnostic",
        ],
        "authorization allowed operations drift",
    )
    _require(
        receipt["forbidden_operations"]
        == [
            "audit_row_access",
            "sampled_rlvr",
            "hyperparameter_adaptation",
            "threshold_adaptation",
            "stack_or_identity_deletion",
        ],
        "authorization forbidden operations drift",
    )


def verify_invocation_start_receipt(
    *,
    receipt_path: Path,
    expected_receipt_sha256: str,
    replicate_id: str,
    run_nonce: str,
    mapping_stack_id: str,
    execution_manifest_sha256: str,
    authorization_receipt_sha256: str,
    authorization_id: str,
) -> dict[str, Any]:
    _require(replicate_id in {"A", "B"}, "replicate_id must be A or B")
    _require(UUID4_RE.fullmatch(run_nonce) is not None, "run_nonce must be a random UUIDv4")
    _require(
        re.fullmatch(r"[0-9a-f]{64}", expected_receipt_sha256) is not None,
        "external invocation receipt SHA-256 required",
    )
    _require(
        sha256_file(receipt_path) == expected_receipt_sha256,
        "external invocation receipt hash mismatch",
    )
    receipt = read_json(receipt_path)
    required = {
        "schema_version",
        "status",
        "evidence_boundary",
        "replicate_id",
        "run_nonce",
        "mapping_stack_id",
        "execution_manifest_sha256",
        "authorization_receipt_sha256",
        "authorization_id",
        "started_at_utc",
        "custody_requirement",
    }
    _require(set(receipt) == required, "invocation start receipt schema drift")
    _require(
        receipt["schema_version"] == INVOCATION_RECEIPT_SCHEMA
        and receipt["status"] == "FROZEN_BEFORE_MODEL_LOAD",
        "invocation start receipt status drift",
    )
    validate_evidence_boundary(receipt["evidence_boundary"])
    _require(receipt["replicate_id"] == replicate_id, "invocation replicate_id mismatch")
    _require(receipt["run_nonce"] == run_nonce, "invocation run_nonce mismatch")
    _require(receipt["mapping_stack_id"] == mapping_stack_id, "invocation stack mismatch")
    _require(
        receipt["execution_manifest_sha256"] == execution_manifest_sha256,
        "invocation manifest binding mismatch",
    )
    _require(
        receipt["authorization_receipt_sha256"] == authorization_receipt_sha256,
        "invocation authorization hash mismatch",
    )
    _require(receipt["authorization_id"] == authorization_id, "invocation authorization_id mismatch")
    _parse_rfc3339_utc(receipt["started_at_utc"], "started_at_utc")
    _require(
        receipt["custody_requirement"]
        == "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
        "invocation custody contract drift",
    )
    return receipt


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a CPU-only immutable MVP execution manifest")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--determinism-addendum", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output.exists():
        raise ContractError(f"refusing to overwrite {args.output}")
    manifest = build_manifest(
        config_path=args.config,
        model_dir=args.model,
        source_path=args.source_bundles,
        target_path=args.target_calibration,
        mapping_path=args.mapping_stacks,
        runner_path=args.runner,
        validator_path=args.validator,
        asset_validation_path=args.asset_validation,
        determinism_addendum_path=args.determinism_addendum,
        audit_seal_path=args.audit_seal,
        action_authorization=False,
    )
    args.output.write_bytes(canonical_json_bytes(manifest))
    print(json.dumps({"status": manifest["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
