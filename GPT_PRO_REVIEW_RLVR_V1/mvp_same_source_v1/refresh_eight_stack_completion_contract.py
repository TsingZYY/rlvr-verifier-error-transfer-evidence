"""Refresh only the completion-validator hash chain without model actions.

The eight configs, determinism addenda, and execution manifests are copied
byte-for-byte from an already frozen contract.  A new master, authorization
template, and reference-only hash record are then built against the current
completion validator.  No model directory is read and no model framework is
imported.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-contract-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output_dir.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output_dir}")
    if not args.source_contract_dir.is_dir() or args.source_contract_dir.is_symlink():
        raise contract.ContractError("source contract directory is invalid")
    args.output_dir.mkdir(parents=True)

    configs: dict[str, Path] = {}
    manifests: dict[str, Path] = {}
    for index, stack_id in enumerate(contract.EXPECTED_STACK_IDS, 1):
        prefix = f"{index:02d}_"
        source_config = next(args.source_contract_dir.glob(prefix + "*_config.json"))
        source_addendum = next(
            args.source_contract_dir.glob(prefix + "*_determinism.json")
        )
        source_manifest = next(args.source_contract_dir.glob(prefix + "*_manifest.json"))
        target_config = args.output_dir / source_config.name
        target_addendum = args.output_dir / source_addendum.name
        target_manifest = args.output_dir / source_manifest.name
        for source, target in (
            (source_config, target_config),
            (source_addendum, target_addendum),
            (source_manifest, target_manifest),
        ):
            if not source.is_file() or source.is_symlink():
                raise contract.ContractError(f"unsafe source contract file: {source}")
            shutil.copyfile(source, target)
        configs[stack_id] = target_config
        manifests[stack_id] = target_manifest

    master = contract.build_master_inclusion_contract(
        configs_by_stack=configs,
        manifests_by_stack=manifests,
    )
    contract.validate_master_inclusion_contract(master)
    master_path = args.output_dir / "EIGHT_STACK_MASTER_INCLUSION_CONTRACT_R5.json"
    master_path.write_bytes(contract.canonical_json_bytes(master))

    template_receipt = {
        "schema_version": contract.AUTHORIZATION_SCHEMA,
        "status": "NOT_AUTHORIZED_TEMPLATE_ONLY",
        "action_id": contract.AUTHORIZATION_ACTION_ID,
        "authorization_id": "NOT_SET_RANDOM_UUIDV4",
        "issued_at_utc": "NOT_SET_RFC3339_UTC",
        "master_inclusion_contract_sha256": contract.sha256_file(master_path),
        "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
        "config_sha256_by_stack": master["config_sha256_by_stack"],
        "manifest_sha256_by_stack": master["manifest_sha256_by_stack"],
        "runner_sha256": master["shared_runner_sha256"],
        "validator_sha256": master["shared_validator_sha256"],
        "completion_validator_sha256": master[
            "shared_completion_validator_sha256"
        ],
        "static_contract_sha256": master["shared_static_contract_sha256"],
        "model_recursive_inventory_sha256": master[
            "shared_model_recursive_inventory_sha256"
        ],
        "allowed_operations": [],
        "forbidden_operations": [
            "tokenizer_load",
            "model_weight_load",
            "model_forward",
            "gradient",
            "optimizer_step",
            "audit_row_access",
            "sampled_rlvr",
        ],
        "version": contract.AUTHORIZATION_VERSION,
        "expires_at_utc": "NOT_SET",
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
    }
    receipt_path = args.output_dir / "AUTHORIZATION_RECEIPT_TEMPLATE_R5.json"
    receipt_path.write_bytes(contract.canonical_json_bytes(template_receipt))

    reference_hashes = {
        "schema_version": "same-source-reference-hashes-r5",
        "status": "REFERENCE_ONLY_COPY_TO_INDEPENDENT_CUSTODY_BEFORE_AUTHORIZATION",
        "trusted_external_anchor": False,
        "master_inclusion_contract_sha256": contract.sha256_file(master_path),
        "config_sha256_by_stack": master["config_sha256_by_stack"],
        "manifest_sha256_by_stack": master["manifest_sha256_by_stack"],
        "authorization_receipt_template_sha256": contract.sha256_file(receipt_path),
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
    }
    reference_path = args.output_dir / "REFERENCE_HASHES_NOT_EXTERNAL_ANCHOR_R5.json"
    reference_path.write_bytes(contract.canonical_json_bytes(reference_hashes))

    print(
        json.dumps(
            {
                "status": master["status"],
                "stack_count": len(manifests),
                "master_sha256": contract.sha256_file(master_path),
                "completion_validator_sha256": master[
                    "shared_completion_validator_sha256"
                ],
                "model_actions_allowed": False,
                "output_dir": str(args.output_dir.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
