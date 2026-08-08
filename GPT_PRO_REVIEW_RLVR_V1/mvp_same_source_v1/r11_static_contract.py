from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mvp_static_contract as base
import r11_bridge_contract as bridge


MANIFEST_SCHEMA = "r11-bridge-execution-manifest-r1"
MASTER_SCHEMA = "r11-bridge-32-cell-master-inclusion-r1"
AUTHORIZATION_SCHEMA = "r11-bridge-action-authorization-receipt-r1"
INVOCATION_SCHEMA = "r11-bridge-invocation-start-receipt-r1"
ARM_CONTRACT_SCHEMA = "r11-arm-contract-r1"
AUTHORIZATION_ACTION_ID = "RUN_R11_BUG_GOLD_DEVELOPMENT_BRIDGE_32_PROCESS_R1"
AUTHORIZATION_VERSION = "R11_BUG_GOLD_32_CELL_DEVELOPMENT_R1"
MANIFEST_STATUS = "R11_STATIC_PASS_AUTHORIZATION_PENDING"
MASTER_STATUS = "R11_32_CELL_FROZEN_AUTHORIZATION_PENDING"
ARM_CONTRACT_STATUS = "FROZEN_STATIC_NON_AUTHORIZING"
EXPECTED_STACK_IDS = tuple(base.EXPECTED_STACK_IDS)
EXPECTED_ARMS = tuple(bridge.ARMS)
EXPECTED_REPLICATES = ("A", "B")
EXPECTED_CELL_IDS = tuple(
    f"{stack}|{arm}|{replicate}"
    for stack in EXPECTED_STACK_IDS
    for arm in EXPECTED_ARMS
    for replicate in EXPECTED_REPLICATES
)
ALLOWED_OPERATIONS = [
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "fixed_candidate_r11_bug_gold_development_bridge",
]
FORBIDDEN_OPERATIONS = [
    "audit_row_access",
    "hidden_audit",
    "sampled_rlvr",
    "multi_step_rlvr",
    "formal_or_confirmatory_claim",
    "adaptive_retry",
    "outcome_dependent_rerun",
]
CLAIM_BOUNDARY = [
    "Development screen only; not formal or confirmatory evidence.",
    "BUG versus GOLD_ONLY differs only by the signed reward mask.",
    "Two task pairs are the scientific clusters; 32 processes are not 32 scientific samples.",
    "Calibration-only fixed-candidate optimization is not sampled RLVR or hidden audit evidence.",
    "A pass does not automatically authorize any later stage.",
]
STATIC_AUTHORIZATION = {
    "cpu_static_repair": True,
    "tokenizer_load": False,
    "model_weight_load": False,
    "model_forward": False,
    "gradient": False,
    "optimizer_step": False,
    "fixed_candidate_r11_bug_gold_development_bridge": False,
    "sampled_rlvr": False,
    "hidden_audit": False,
}


class R11ContractError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R11ContractError(message)


def cell_id(stack_id: str, arm: str, replicate_id: str) -> str:
    require(stack_id in EXPECTED_STACK_IDS, f"unknown stack: {stack_id}")
    require(arm in EXPECTED_ARMS, f"unknown arm: {arm}")
    require(replicate_id in EXPECTED_REPLICATES, f"unknown replicate: {replicate_id}")
    return f"{stack_id}|{arm}|{replicate_id}"


def parse_cell_id(value: str) -> tuple[str, str, str]:
    parts = value.split("|")
    require(len(parts) == 3, f"invalid R11 cell id: {value}")
    expected = cell_id(parts[0], parts[1], parts[2])
    require(expected == value, f"non-canonical R11 cell id: {value}")
    return parts[0], parts[1], parts[2]


def validate_hash(value: Any, field: str) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{field} must be lowercase SHA-256",
    )
    return value


def parse_rfc3339_utc(value: Any, field: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{field} must be RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise R11ContractError(f"{field} is not RFC3339 UTC") from error
    require(parsed.tzinfo is not None, f"{field} lacks timezone")
    return parsed.astimezone(timezone.utc)


ARM_CONTRACT_KEYS = {
    "schema_version",
    "status",
    "cell_id",
    "arm",
    "reward_specification",
    "unique_permitted_treatment_difference",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "mapping_stack_id",
    "replicate_id",
    "model_execution_authorized",
}


def validate_arm_contract(
    contract: dict[str, Any],
    *,
    expected_parent_runner_sha256: str,
    expected_protocol_sha256: str,
) -> str:
    require(set(contract) == ARM_CONTRACT_KEYS, "R11 arm contract schema drift")
    require(contract["schema_version"] == ARM_CONTRACT_SCHEMA, "arm contract version drift")
    require(contract["status"] == ARM_CONTRACT_STATUS, "arm contract is not frozen")
    require(contract["model_execution_authorized"] is False, "arm contract self-authorization detected")
    arm = str(contract["arm"])
    stack = str(contract["mapping_stack_id"])
    replicate = str(contract["replicate_id"])
    expected_cell = cell_id(stack, arm, replicate)
    require(contract["cell_id"] == expected_cell, "arm contract cell binding mismatch")
    require(
        contract["reward_specification"] == bridge.expected_reward_specification(arm),
        "arm reward specification drift",
    )
    require(
        contract["unique_permitted_treatment_difference"] == "reward_mask",
        "treatment difference is not frozen to reward mask",
    )
    require(
        contract["parent_r10_runner_sha256"] == expected_parent_runner_sha256,
        "arm contract parent runner mismatch",
    )
    require(
        contract["r11_protocol_sha256"] == expected_protocol_sha256,
        "arm contract protocol mismatch",
    )
    return expected_cell


def build_arm_contract(
    *,
    stack_id: str,
    arm: str,
    replicate_id: str,
    parent_runner_sha256: str,
    protocol_sha256: str,
) -> dict[str, Any]:
    validate_hash(parent_runner_sha256, "parent_runner_sha256")
    validate_hash(protocol_sha256, "protocol_sha256")
    contract = {
        "schema_version": ARM_CONTRACT_SCHEMA,
        "status": ARM_CONTRACT_STATUS,
        "cell_id": cell_id(stack_id, arm, replicate_id),
        "arm": arm,
        "reward_specification": bridge.expected_reward_specification(arm),
        "unique_permitted_treatment_difference": "reward_mask",
        "parent_r10_runner_sha256": parent_runner_sha256,
        "r11_protocol_sha256": protocol_sha256,
        "mapping_stack_id": stack_id,
        "replicate_id": replicate_id,
        "model_execution_authorized": False,
    }
    validate_arm_contract(
        contract,
        expected_parent_runner_sha256=parent_runner_sha256,
        expected_protocol_sha256=protocol_sha256,
    )
    return contract


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
    arm_contract_path: Path,
    parent_runner_path: Path,
    protocol_path: Path,
    bridge_contract_path: Path,
) -> dict[str, Any]:
    parent_runner_sha256 = base.sha256_file(parent_runner_path)
    protocol_sha256 = base.sha256_file(protocol_path)
    arm_contract = bridge.read_json(arm_contract_path)
    selected_cell = validate_arm_contract(
        arm_contract,
        expected_parent_runner_sha256=parent_runner_sha256,
        expected_protocol_sha256=protocol_sha256,
    )
    inherited = base.build_manifest(
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
    require(
        inherited["selected_mapping_stack_id"] == arm_contract["mapping_stack_id"],
        "config/arm-contract stack mismatch",
    )
    inherited["schema_version"] = MANIFEST_SCHEMA
    inherited["status"] = MANIFEST_STATUS
    inherited["bridge_cell_id"] = selected_cell
    inherited["arm"] = arm_contract["arm"]
    inherited["replicate_id"] = arm_contract["replicate_id"]
    inherited["development_screen_only"] = True
    inherited["formal_confirmatory"] = False
    inherited["unique_permitted_treatment_difference"] = "reward_mask"
    inherited["bindings"]["static_contract_sha256"] = base.sha256_file(Path(__file__).resolve())
    inherited["bindings"].update(
        {
            "parent_r10_runner_sha256": parent_runner_sha256,
            "r11_protocol_sha256": protocol_sha256,
            "r11_arm_contract_sha256": base.sha256_file(arm_contract_path),
            "r11_bridge_contract_sha256": base.sha256_file(bridge_contract_path),
        }
    )
    inherited["authorization"] = dict(STATIC_AUTHORIZATION)
    validate_manifest_shape(inherited)
    return inherited


MANIFEST_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "model_execution_performed",
    "mapping_stack_ids",
    "selected_mapping_stack_id",
    "stack_membership_commitments",
    "source_rule_offsets_mod7",
    "target_rule_offsets_mod7",
    "bindings",
    "runtime_expected",
    "runtime_observed_at_manifest_build",
    "determinism_environment",
    "audit_contract",
    "authorization",
    "bridge_cell_id",
    "arm",
    "replicate_id",
    "development_screen_only",
    "formal_confirmatory",
    "unique_permitted_treatment_difference",
}
MANIFEST_BINDING_KEYS = {
    "asset_validation_sha256",
    "audit_file_sha256_from_seal",
    "audit_seal_receipt_sha256",
    "chat_template_sha256",
    "config_sha256",
    "determinism_addendum_sha256",
    "mapping_stacks_sha256",
    "model_recursive_inventory",
    "model_recursive_inventory_sha256",
    "runner_sha256",
    "source_bundles_sha256",
    "static_contract_sha256",
    "target_calibration_sha256",
    "validator_sha256",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "r11_arm_contract_sha256",
    "r11_bridge_contract_sha256",
}


def validate_manifest_shape(manifest: dict[str, Any]) -> str:
    require(set(manifest) == MANIFEST_KEYS, "R11 execution manifest schema drift")
    require(manifest["schema_version"] == MANIFEST_SCHEMA, "manifest version drift")
    require(manifest["status"] == MANIFEST_STATUS, "manifest status drift")
    require(manifest["scientific_evidence"] is False, "manifest scientific status washing")
    require(manifest["formal_experiment"] is False, "manifest formal status washing")
    base.validate_evidence_boundary(manifest["evidence_boundary"])
    require(manifest["model_execution_performed"] is False, "manifest execution status washing")
    require(manifest["development_screen_only"] is True, "manifest development boundary missing")
    require(manifest["formal_confirmatory"] is False, "manifest confirmatory status washing")
    require(
        manifest["unique_permitted_treatment_difference"] == "reward_mask",
        "manifest treatment-difference drift",
    )
    expected_cell = cell_id(
        str(manifest["selected_mapping_stack_id"]),
        str(manifest["arm"]),
        str(manifest["replicate_id"]),
    )
    require(manifest["bridge_cell_id"] == expected_cell, "manifest cell binding mismatch")
    require(manifest["authorization"] == STATIC_AUTHORIZATION, "manifest self-authorization detected")
    bindings = manifest.get("bindings")
    require(isinstance(bindings, dict), "manifest bindings missing")
    require(set(bindings) == MANIFEST_BINDING_KEYS, "manifest binding schema drift")
    require(
        isinstance(bindings["model_recursive_inventory"], list),
        "manifest model inventory missing",
    )
    for field in (
        "config_sha256",
        "runner_sha256",
        "validator_sha256",
        "static_contract_sha256",
        "model_recursive_inventory_sha256",
        "parent_r10_runner_sha256",
        "r11_protocol_sha256",
        "r11_arm_contract_sha256",
        "r11_bridge_contract_sha256",
    ):
        validate_hash(bindings.get(field), f"bindings.{field}")
    return expected_cell


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
    arm_contract_path: Path,
    expected_arm_contract_sha256: str,
    parent_runner_path: Path,
    protocol_path: Path,
    bridge_contract_path: Path,
) -> None:
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
        arm_contract_path=arm_contract_path,
        parent_runner_path=parent_runner_path,
        protocol_path=protocol_path,
        bridge_contract_path=bridge_contract_path,
    )
    require(manifest == expected, "R11 execution manifest or bound assets drifted")
    manifest_hash = base.sha256_bytes(base.canonical_json_bytes(manifest))
    require(manifest_hash == expected_manifest_sha256, "external expected manifest hash mismatch")
    require(base.sha256_file(arm_contract_path) == expected_arm_contract_sha256, "arm contract hash mismatch")
    require(os.environ.get("CUBLAS_WORKSPACE_CONFIG") == ":4096:8", "CUBLAS determinism env missing")
    if require_model_authorization:
        require(
            master_inclusion_contract_path is not None
            and authorization_receipt_path is not None
            and expected_authorization_receipt_sha256 is not None,
            "external R11 authorization receipt missing",
        )
        verify_authorization_receipt(
            receipt_path=authorization_receipt_path,
            expected_receipt_sha256=expected_authorization_receipt_sha256,
            master_path=master_inclusion_contract_path,
            selected_cell_id=manifest["bridge_cell_id"],
            selected_manifest_sha256=manifest_hash,
            selected_arm_contract_sha256=expected_arm_contract_sha256,
            manifest=manifest,
        )


MASTER_KEYS = {
    "schema_version",
    "status",
    "scientific_evidence",
    "formal_experiment",
    "evidence_boundary",
    "model_execution_performed",
    "ordered_cell_ids",
    "required_process_count",
    "required_unique_arm_specific_design_cells",
    "required_technical_update_executions",
    "required_target_identity_evaluation_cells",
    "config_sha256_by_stack",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "shared_runner_sha256",
    "shared_validator_sha256",
    "shared_completion_validator_sha256",
    "shared_static_contract_sha256",
    "shared_bridge_contract_sha256",
    "shared_parent_r10_runner_sha256",
    "shared_r11_protocol_sha256",
    "shared_model_recursive_inventory_sha256",
    "inclusion_rule",
    "exclusion_rule",
    "claim_boundary",
    "authorization",
}


def validate_master(master: dict[str, Any]) -> None:
    require(set(master) == MASTER_KEYS, "R11 master inclusion schema drift")
    require(master["schema_version"] == MASTER_SCHEMA, "master version drift")
    require(master["status"] == MASTER_STATUS, "master status drift")
    require(master["scientific_evidence"] is False, "master scientific status washing")
    require(master["formal_experiment"] is False, "master formal status washing")
    base.validate_evidence_boundary(master["evidence_boundary"])
    require(master["model_execution_performed"] is False, "master execution status washing")
    require(master["ordered_cell_ids"] == list(EXPECTED_CELL_IDS), "master cell order drift")
    require(master["required_process_count"] == 32, "master process count drift")
    require(master["required_unique_arm_specific_design_cells"] == 80, "master design-cell count drift")
    require(master["required_technical_update_executions"] == 160, "master update count drift")
    require(master["required_target_identity_evaluation_cells"] == 800, "master target-cell count drift")
    require(
        set(master["config_sha256_by_stack"]) == set(EXPECTED_STACK_IDS),
        "master config coverage drift",
    )
    for stack, value in master["config_sha256_by_stack"].items():
        validate_hash(value, f"config_sha256_by_stack.{stack}")
    for field in ("manifest_sha256_by_cell", "arm_contract_sha256_by_cell"):
        require(set(master[field]) == set(EXPECTED_CELL_IDS), f"{field} coverage drift")
        for key, value in master[field].items():
            validate_hash(value, f"{field}.{key}")
    for field in (
        "shared_runner_sha256",
        "shared_validator_sha256",
        "shared_completion_validator_sha256",
        "shared_static_contract_sha256",
        "shared_bridge_contract_sha256",
        "shared_parent_r10_runner_sha256",
        "shared_r11_protocol_sha256",
        "shared_model_recursive_inventory_sha256",
    ):
        validate_hash(master[field], field)
    require(
        master["inclusion_rule"] == "RUN_ALL_32_STACK_ARM_REPLICATE_CELLS_WITHOUT_SELECTION",
        "master inclusion rule drift",
    )
    require(
        master["exclusion_rule"]
        == "NO_CELL_IDENTITY_THRESHOLD_HYPERPARAMETER_RETRY_OR_OUTCOME_ADAPTATION",
        "master exclusion rule drift",
    )
    require(master["claim_boundary"] == CLAIM_BOUNDARY, "master claim boundary drift")
    require(
        master["authorization"]
        == {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
        "master self-authorization detected",
    )


def build_master(
    *,
    configs_by_stack: dict[str, Path],
    manifests_by_cell: dict[str, Path],
    arm_contracts_by_cell: dict[str, Path],
    completion_validator_path: Path,
) -> dict[str, Any]:
    require(set(configs_by_stack) == set(EXPECTED_STACK_IDS), "master config coverage is not 8/8")
    require(set(manifests_by_cell) == set(EXPECTED_CELL_IDS), "master manifest coverage is not 32/32")
    require(set(arm_contracts_by_cell) == set(EXPECTED_CELL_IDS), "master arm-contract coverage is not 32/32")
    config_hashes = {stack: base.sha256_file(configs_by_stack[stack]) for stack in EXPECTED_STACK_IDS}
    manifest_hashes: dict[str, str] = {}
    contract_hashes: dict[str, str] = {}
    shared: dict[str, str] | None = None
    protocol_sha256: str | None = None
    parent_runner_sha256: str | None = None
    for selected_cell in EXPECTED_CELL_IDS:
        stack, arm, replicate = parse_cell_id(selected_cell)
        manifest = bridge.read_json(manifests_by_cell[selected_cell])
        require(validate_manifest_shape(manifest) == selected_cell, f"manifest cell drift: {selected_cell}")
        require(manifest["selected_mapping_stack_id"] == stack, f"manifest stack drift: {selected_cell}")
        require(manifest["arm"] == arm and manifest["replicate_id"] == replicate, f"manifest arm/replicate drift: {selected_cell}")
        require(manifest["bindings"]["config_sha256"] == config_hashes[stack], f"manifest/config mismatch: {selected_cell}")
        contract_path = arm_contracts_by_cell[selected_cell]
        contract = bridge.read_json(contract_path)
        validate_arm_contract(
            contract,
            expected_parent_runner_sha256=manifest["bindings"]["parent_r10_runner_sha256"],
            expected_protocol_sha256=manifest["bindings"]["r11_protocol_sha256"],
        )
        require(contract["cell_id"] == selected_cell, f"arm contract cell drift: {selected_cell}")
        require(
            manifest["bindings"]["r11_arm_contract_sha256"] == base.sha256_file(contract_path),
            f"manifest/arm-contract mismatch: {selected_cell}",
        )
        normalized = {
            field: manifest["bindings"][field]
            for field in (
                "runner_sha256",
                "validator_sha256",
                "static_contract_sha256",
                "r11_bridge_contract_sha256",
                "parent_r10_runner_sha256",
                "r11_protocol_sha256",
                "model_recursive_inventory_sha256",
            )
        }
        if shared is None:
            shared = normalized
            protocol_sha256 = normalized["r11_protocol_sha256"]
            parent_runner_sha256 = normalized["parent_r10_runner_sha256"]
        else:
            require(normalized == shared, f"shared binding drift: {selected_cell}")
        manifest_hashes[selected_cell] = base.sha256_file(manifests_by_cell[selected_cell])
        contract_hashes[selected_cell] = base.sha256_file(contract_path)
    assert shared is not None and protocol_sha256 is not None and parent_runner_sha256 is not None
    master = {
        "schema_version": MASTER_SCHEMA,
        "status": MASTER_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_cell_ids": list(EXPECTED_CELL_IDS),
        "required_process_count": 32,
        "required_unique_arm_specific_design_cells": 80,
        "required_technical_update_executions": 160,
        "required_target_identity_evaluation_cells": 800,
        "config_sha256_by_stack": config_hashes,
        "manifest_sha256_by_cell": manifest_hashes,
        "arm_contract_sha256_by_cell": contract_hashes,
        "shared_runner_sha256": shared["runner_sha256"],
        "shared_validator_sha256": shared["validator_sha256"],
        "shared_completion_validator_sha256": base.sha256_file(completion_validator_path),
        "shared_static_contract_sha256": shared["static_contract_sha256"],
        "shared_bridge_contract_sha256": shared["r11_bridge_contract_sha256"],
        "shared_parent_r10_runner_sha256": parent_runner_sha256,
        "shared_r11_protocol_sha256": protocol_sha256,
        "shared_model_recursive_inventory_sha256": shared["model_recursive_inventory_sha256"],
        "inclusion_rule": "RUN_ALL_32_STACK_ARM_REPLICATE_CELLS_WITHOUT_SELECTION",
        "exclusion_rule": "NO_CELL_IDENTITY_THRESHOLD_HYPERPARAMETER_RETRY_OR_OUTCOME_ADAPTATION",
        "claim_boundary": list(CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
            "hidden_audit_allowed": False,
        },
    }
    validate_master(master)
    return master


AUTHORIZATION_KEYS = {
    "schema_version",
    "status",
    "action_id",
    "version",
    "authorization_id",
    "issued_at_utc",
    "expires_at_utc",
    "master_inclusion_contract_sha256",
    "ordered_cell_ids",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "runner_sha256",
    "validator_sha256",
    "completion_validator_sha256",
    "static_contract_sha256",
    "bridge_contract_sha256",
    "parent_r10_runner_sha256",
    "r11_protocol_sha256",
    "model_recursive_inventory_sha256",
    "allowed_operations",
    "forbidden_operations",
    "model_execution_authorized",
    "evidence_boundary",
}


def build_authorization_template(master: dict[str, Any], master_sha256: str) -> dict[str, Any]:
    validate_master(master)
    validate_hash(master_sha256, "master_sha256")
    return {
        "schema_version": AUTHORIZATION_SCHEMA,
        "status": "NOT_AUTHORIZED_TEMPLATE_ONLY",
        "action_id": AUTHORIZATION_ACTION_ID,
        "version": AUTHORIZATION_VERSION,
        "authorization_id": "NOT_SET_RANDOM_UUIDV4",
        "issued_at_utc": "NOT_SET_RFC3339_UTC",
        "expires_at_utc": "NOT_SET_RFC3339_UTC",
        "master_inclusion_contract_sha256": master_sha256,
        "ordered_cell_ids": list(EXPECTED_CELL_IDS),
        "manifest_sha256_by_cell": dict(master["manifest_sha256_by_cell"]),
        "arm_contract_sha256_by_cell": dict(master["arm_contract_sha256_by_cell"]),
        "runner_sha256": master["shared_runner_sha256"],
        "validator_sha256": master["shared_validator_sha256"],
        "completion_validator_sha256": master["shared_completion_validator_sha256"],
        "static_contract_sha256": master["shared_static_contract_sha256"],
        "bridge_contract_sha256": master["shared_bridge_contract_sha256"],
        "parent_r10_runner_sha256": master["shared_parent_r10_runner_sha256"],
        "r11_protocol_sha256": master["shared_r11_protocol_sha256"],
        "model_recursive_inventory_sha256": master["shared_model_recursive_inventory_sha256"],
        "allowed_operations": [],
        "forbidden_operations": [
            "tokenizer_load",
            "model_weight_load",
            "model_forward",
            "gradient",
            "optimizer_step",
            *FORBIDDEN_OPERATIONS,
        ],
        "model_execution_authorized": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
    }


def verify_authorization_receipt(
    *,
    receipt_path: Path,
    expected_receipt_sha256: str,
    master_path: Path,
    selected_cell_id: str,
    selected_manifest_sha256: str,
    selected_arm_contract_sha256: str,
    manifest: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_hash(expected_receipt_sha256, "expected authorization receipt hash")
    require(base.sha256_file(receipt_path) == expected_receipt_sha256, "authorization receipt hash mismatch")
    receipt = bridge.read_json(receipt_path)
    master = bridge.read_json(master_path)
    validate_master(master)
    require(set(receipt) == AUTHORIZATION_KEYS, "R11 authorization receipt schema drift")
    require(receipt["schema_version"] == AUTHORIZATION_SCHEMA, "authorization version drift")
    require(receipt["status"] == "AUTHORIZED_BY_USER", "receipt is not user-authorized")
    require(receipt["action_id"] == AUTHORIZATION_ACTION_ID, "authorization action drift")
    require(receipt["version"] == AUTHORIZATION_VERSION, "authorization contract version drift")
    require(
        isinstance(receipt["authorization_id"], str)
        and base.UUID4_RE.fullmatch(receipt["authorization_id"]) is not None,
        "authorization_id must be UUIDv4",
    )
    issued = parse_rfc3339_utc(receipt["issued_at_utc"], "issued_at_utc")
    expires = parse_rfc3339_utc(receipt["expires_at_utc"], "expires_at_utc")
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    require(issued <= current < expires and expires > issued, "authorization receipt is expired or not yet valid")
    require(base.sha256_file(master_path) == receipt["master_inclusion_contract_sha256"], "authorization/master hash mismatch")
    require(receipt["ordered_cell_ids"] == list(EXPECTED_CELL_IDS), "authorization cell order drift")
    require(receipt["manifest_sha256_by_cell"] == master["manifest_sha256_by_cell"], "authorization manifest map drift")
    require(receipt["arm_contract_sha256_by_cell"] == master["arm_contract_sha256_by_cell"], "authorization arm-contract map drift")
    require(selected_cell_id in EXPECTED_CELL_IDS, "selected cell is not frozen")
    require(receipt["manifest_sha256_by_cell"][selected_cell_id] == selected_manifest_sha256, "selected manifest is not authorized")
    require(receipt["arm_contract_sha256_by_cell"][selected_cell_id] == selected_arm_contract_sha256, "selected arm contract is not authorized")
    shared_fields = {
        "runner_sha256": "shared_runner_sha256",
        "validator_sha256": "shared_validator_sha256",
        "completion_validator_sha256": "shared_completion_validator_sha256",
        "static_contract_sha256": "shared_static_contract_sha256",
        "bridge_contract_sha256": "shared_bridge_contract_sha256",
        "parent_r10_runner_sha256": "shared_parent_r10_runner_sha256",
        "r11_protocol_sha256": "shared_r11_protocol_sha256",
        "model_recursive_inventory_sha256": "shared_model_recursive_inventory_sha256",
    }
    for receipt_field, master_field in shared_fields.items():
        require(receipt[receipt_field] == master[master_field], f"authorization shared binding mismatch: {receipt_field}")
    bindings = manifest["bindings"]
    for receipt_field, binding_field in (
        ("runner_sha256", "runner_sha256"),
        ("validator_sha256", "validator_sha256"),
        ("static_contract_sha256", "static_contract_sha256"),
        ("bridge_contract_sha256", "r11_bridge_contract_sha256"),
        ("parent_r10_runner_sha256", "parent_r10_runner_sha256"),
        ("r11_protocol_sha256", "r11_protocol_sha256"),
        ("model_recursive_inventory_sha256", "model_recursive_inventory_sha256"),
    ):
        require(receipt[receipt_field] == bindings[binding_field], f"authorization/manifest mismatch: {receipt_field}")
    require(receipt["allowed_operations"] == ALLOWED_OPERATIONS, "allowed operations drift")
    require(receipt["forbidden_operations"] == FORBIDDEN_OPERATIONS, "forbidden operations drift")
    require(receipt["model_execution_authorized"] is True, "receipt keeps model execution disabled")
    base.validate_evidence_boundary(receipt["evidence_boundary"])
    return receipt


INVOCATION_KEYS = {
    "schema_version",
    "status",
    "evidence_boundary",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "started_at_utc",
    "custody_requirement",
}


def verify_invocation_start_receipt(
    *,
    receipt_path: Path,
    expected_receipt_sha256: str,
    replicate_id: str,
    run_nonce: str,
    mapping_stack_id: str,
    arm: str,
    execution_manifest_sha256: str,
    arm_contract_sha256: str,
    authorization_receipt_sha256: str,
    authorization_id: str,
) -> dict[str, Any]:
    selected_cell = cell_id(mapping_stack_id, arm, replicate_id)
    require(base.UUID4_RE.fullmatch(run_nonce) is not None, "run_nonce must be UUIDv4")
    validate_hash(execution_manifest_sha256, "execution_manifest_sha256")
    validate_hash(arm_contract_sha256, "arm_contract_sha256")
    validate_hash(authorization_receipt_sha256, "authorization_receipt_sha256")
    validate_hash(expected_receipt_sha256, "expected invocation receipt hash")
    require(base.sha256_file(receipt_path) == expected_receipt_sha256, "invocation receipt hash mismatch")
    receipt = bridge.read_json(receipt_path)
    require(set(receipt) == INVOCATION_KEYS, "R11 invocation receipt schema drift")
    require(receipt["schema_version"] == INVOCATION_SCHEMA, "invocation version drift")
    require(receipt["status"] == "FROZEN_BEFORE_MODEL_LOAD", "invocation status drift")
    base.validate_evidence_boundary(receipt["evidence_boundary"])
    require(receipt["cell_id"] == selected_cell, "invocation cell mismatch")
    require(receipt["mapping_stack_id"] == mapping_stack_id, "invocation stack mismatch")
    require(receipt["arm"] == arm, "invocation arm mismatch")
    require(receipt["replicate_id"] == replicate_id, "invocation replicate mismatch")
    require(receipt["run_nonce"] == run_nonce, "invocation nonce mismatch")
    require(receipt["execution_manifest_sha256"] == execution_manifest_sha256, "invocation manifest mismatch")
    require(receipt["arm_contract_sha256"] == arm_contract_sha256, "invocation arm-contract mismatch")
    require(receipt["authorization_receipt_sha256"] == authorization_receipt_sha256, "invocation authorization hash mismatch")
    require(receipt["authorization_id"] == authorization_id, "invocation authorization id mismatch")
    parse_rfc3339_utc(receipt["started_at_utc"], "started_at_utc")
    require(
        receipt["custody_requirement"]
        == "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
        "invocation custody requirement drift",
    )
    return receipt
