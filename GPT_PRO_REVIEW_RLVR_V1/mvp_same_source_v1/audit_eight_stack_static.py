"""Recompute the frozen eight-stack CPU/static contract without model actions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-dir", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output}")
    master_path = (
        args.contract_dir / "EIGHT_STACK_MASTER_INCLUSION_CONTRACT_R5.json"
    )
    master = contract.read_json(master_path)
    contract.validate_master_inclusion_contract(master)
    master_sha = contract.sha256_file(master_path)
    if master_sha != args.expected_master_sha256:
        raise contract.ContractError("external expected master hash mismatch")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    checked: list[dict[str, str]] = []
    for index, stack_id in enumerate(contract.EXPECTED_STACK_IDS, 1):
        prefix = f"{index:02d}_"
        config_path = next(args.contract_dir.glob(prefix + "*_config.json"))
        addendum_path = next(args.contract_dir.glob(prefix + "*_determinism.json"))
        manifest_path = next(args.contract_dir.glob(prefix + "*_manifest.json"))
        manifest = contract.read_json(manifest_path)
        expected_manifest_sha = master["manifest_sha256_by_stack"][stack_id]
        contract.verify_preflight(
            manifest,
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
            require_model_authorization=False,
            expected_manifest_sha256=expected_manifest_sha,
        )
        checked.append(
            {
                "mapping_stack_id": stack_id,
                "config_sha256": contract.sha256_file(config_path),
                "manifest_sha256": contract.sha256_file(manifest_path),
            }
        )
    template_rejected = False
    try:
        contract.verify_authorization_receipt(
            receipt_path=args.contract_dir / "AUTHORIZATION_RECEIPT_TEMPLATE_R5.json",
            master_contract_path=master_path,
            selected_stack_id=checked[0]["mapping_stack_id"],
            selected_manifest_sha256=checked[0]["manifest_sha256"],
            manifest=contract.read_json(
                next(args.contract_dir.glob("01_*_manifest.json"))
            ),
            expected_authorization_receipt_sha256=contract.sha256_file(
                args.contract_dir / "AUTHORIZATION_RECEIPT_TEMPLATE_R5.json"
            ),
        )
    except contract.ContractError:
        template_rejected = True
    if not template_rejected:
        raise contract.ContractError("authorization template unexpectedly passed")
    report = {
        "schema_version": "same-source-eight-stack-static-audit-r5",
        "status": "CPU_STATIC_ASSET_AND_EXECUTION_CONTRACT_PASS",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "model_actions_authorized": False,
        "master_inclusion_contract_sha256": master_sha,
        "stack_count": len(checked),
        "stacks": checked,
        "authorization_template_rejected": template_rejected,
        "audit_hidden_eligibility": False,
        "audit_label": "CURRENT_AUDIT_EXPOSED_AND_USED_FOR_CPU_STATIC_INTEGRITY_ONLY",
    }
    args.output.write_bytes(contract.canonical_json_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
