"""Freeze the non-authorizing 32-process R11 BUG/GOLD_ONLY bridge release."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Sequence

import mvp_static_contract as base
import r11_static_contract as contract


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--r10-contract-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--completion-validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--parent-runner", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--bridge-contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def stack_prefix(index: int, stack_id: str) -> str:
    return f"{index:02d}_{stack_id.lower().replace('-', '_')}"


def cell_prefix(index: int, stack_id: str, arm: str, replicate: str) -> str:
    return f"{stack_prefix(index, stack_id)}__{arm.lower()}_{replicate.lower()}"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.output_dir.exists():
        raise contract.R11ContractError(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    parent_runner_sha256 = base.sha256_file(args.parent_runner)
    protocol_sha256 = base.sha256_file(args.protocol)
    configs: dict[str, Path] = {}
    manifests: dict[str, Path] = {}
    arm_contracts: dict[str, Path] = {}
    cell_files: dict[str, dict[str, str]] = {}

    for index, stack_id in enumerate(contract.EXPECTED_STACK_IDS, 1):
        source_prefix = f"{index:02d}_"
        source_config = next(args.r10_contract_dir.glob(source_prefix + "*_config.json"))
        source_addendum = next(
            args.r10_contract_dir.glob(source_prefix + "*_determinism.json")
        )
        prefix = stack_prefix(index, stack_id)
        config_path = args.output_dir / f"{prefix}_config.json"
        addendum_path = args.output_dir / f"{prefix}_determinism.json"
        shutil.copyfile(source_config, config_path)
        shutil.copyfile(source_addendum, addendum_path)
        config = base.read_json(config_path)
        base.validate_config(config)
        if config["data"]["mapping_stack_id"] != stack_id:
            raise contract.R11ContractError(f"R10 config stack mismatch: {stack_id}")
        configs[stack_id] = config_path

        for arm in contract.EXPECTED_ARMS:
            for replicate in contract.EXPECTED_REPLICATES:
                selected_cell = contract.cell_id(stack_id, arm, replicate)
                selected_prefix = cell_prefix(index, stack_id, arm, replicate)
                arm_path = args.output_dir / f"{selected_prefix}_arm_contract.json"
                manifest_path = args.output_dir / f"{selected_prefix}_manifest.json"
                arm_value = contract.build_arm_contract(
                    stack_id=stack_id,
                    arm=arm,
                    replicate_id=replicate,
                    parent_runner_sha256=parent_runner_sha256,
                    protocol_sha256=protocol_sha256,
                )
                arm_path.write_bytes(base.canonical_json_bytes(arm_value))
                manifest = contract.build_manifest(
                    config_path=config_path,
                    model_dir=args.model,
                    source_path=args.source_bundles,
                    target_path=args.target_calibration,
                    mapping_path=args.mapping_stacks,
                    runner_path=args.runner,
                    validator_path=args.validator,
                    asset_validation_path=args.asset_validation,
                    determinism_addendum_path=addendum_path,
                    audit_seal_path=args.audit_seal,
                    arm_contract_path=arm_path,
                    parent_runner_path=args.parent_runner,
                    protocol_path=args.protocol,
                    bridge_contract_path=args.bridge_contract,
                )
                manifest_path.write_bytes(base.canonical_json_bytes(manifest))
                manifests[selected_cell] = manifest_path
                arm_contracts[selected_cell] = arm_path
                cell_files[selected_cell] = {
                    "config": config_path.name,
                    "determinism_addendum": addendum_path.name,
                    "arm_contract": arm_path.name,
                    "manifest": manifest_path.name,
                }

    master = contract.build_master(
        configs_by_stack=configs,
        manifests_by_cell=manifests,
        arm_contracts_by_cell=arm_contracts,
        completion_validator_path=args.completion_validator,
    )
    master_path = args.output_dir / "R11_32_CELL_MASTER_INCLUSION_CONTRACT_R1.json"
    master_path.write_bytes(base.canonical_json_bytes(master))
    master_sha256 = base.sha256_file(master_path)

    template = contract.build_authorization_template(master, master_sha256)
    template_path = args.output_dir / "R11_AUTHORIZATION_RECEIPT_TEMPLATE_R1.json"
    template_path.write_bytes(base.canonical_json_bytes(template))

    index = {
        "schema_version": "r11-bridge-release-index-r1",
        "status": "FROZEN_STATIC_NON_AUTHORIZING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "model_actions_authorized": False,
        "ordered_cell_ids": list(contract.EXPECTED_CELL_IDS),
        "cell_files": cell_files,
        "master_inclusion_contract": master_path.name,
        "master_inclusion_contract_sha256": master_sha256,
        "authorization_receipt_template": template_path.name,
        "authorization_receipt_template_sha256": base.sha256_file(template_path),
    }
    index_path = args.output_dir / "R11_RELEASE_INDEX_R1.json"
    index_path.write_bytes(base.canonical_json_bytes(index))
    anchors = {
        "schema_version": "r11-bridge-reference-hashes-r1",
        "status": "REFERENCE_ONLY_COPY_TO_INDEPENDENT_CUSTODY_BEFORE_AUTHORIZATION",
        "trusted_external_anchor": False,
        "master_inclusion_contract_sha256": master_sha256,
        "release_index_sha256": base.sha256_file(index_path),
        "authorization_receipt_template_sha256": base.sha256_file(template_path),
        "manifest_sha256_by_cell": master["manifest_sha256_by_cell"],
        "arm_contract_sha256_by_cell": master["arm_contract_sha256_by_cell"],
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
    }
    anchor_path = args.output_dir / "R11_REFERENCE_HASHES_NOT_EXTERNAL_ANCHOR_R1.json"
    anchor_path.write_bytes(base.canonical_json_bytes(anchors))
    print(
        json.dumps(
            {
                "status": master["status"],
                "process_count": len(manifests),
                "master_sha256": master_sha256,
                "model_actions_authorized": False,
                "model_execution_performed": False,
                "output_dir": str(args.output_dir.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
