"""Create eight CPU/static manifests and one fail-closed inclusion contract.

This tool never imports a model framework and never grants authorization.  It
refuses to overwrite an existing output directory so the resulting hashes can
be copied to independent custody before any model action.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-config", type=Path, required=True)
    parser.add_argument("--template-addendum", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output_dir.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    template_config = contract.read_json(args.template_config)
    template_addendum = contract.read_json(args.template_addendum)
    configs: dict[str, Path] = {}
    manifests: dict[str, Path] = {}

    for index, stack_id in enumerate(contract.EXPECTED_STACK_IDS, 1):
        slug = stack_id.lower().replace("-", "_")
        config_path = args.output_dir / f"{index:02d}_{slug}_config.json"
        addendum_path = args.output_dir / f"{index:02d}_{slug}_determinism.json"
        manifest_path = args.output_dir / f"{index:02d}_{slug}_manifest.json"

        config = copy.deepcopy(template_config)
        config["data"]["mapping_stack_id"] = stack_id
        contract.validate_config(config)
        config_path.write_bytes(contract.canonical_json_bytes(config))

        addendum = copy.deepcopy(template_addendum)
        addendum["base_config"] = config_path.name
        addendum["base_config_sha256"] = contract.sha256_file(config_path)
        addendum_path.write_bytes(contract.canonical_json_bytes(addendum))

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
            action_authorization=False,
        )
        manifest_path.write_bytes(contract.canonical_json_bytes(manifest))
        configs[stack_id] = config_path
        manifests[stack_id] = manifest_path

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

    anchors = {
        "schema_version": "same-source-reference-hashes-r5",
        "status": "REFERENCE_ONLY_COPY_TO_INDEPENDENT_CUSTODY_BEFORE_AUTHORIZATION",
        "trusted_external_anchor": False,
        "master_inclusion_contract_sha256": contract.sha256_file(master_path),
        "config_sha256_by_stack": master["config_sha256_by_stack"],
        "manifest_sha256_by_stack": master["manifest_sha256_by_stack"],
        "authorization_receipt_template_sha256": contract.sha256_file(
            receipt_path
        ),
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
    }
    anchor_path = args.output_dir / "REFERENCE_HASHES_NOT_EXTERNAL_ANCHOR_R5.json"
    anchor_path.write_bytes(contract.canonical_json_bytes(anchors))
    print(
        json.dumps(
            {
                "status": master["status"],
                "stack_count": len(manifests),
                "master_sha256": contract.sha256_file(master_path),
                "model_actions_allowed": False,
                "output_dir": str(args.output_dir.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
