"""Audit a completion-only contract refresh without touching model bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-contract-dir", type=Path, required=True)
    parser.add_argument("--refreshed-contract-dir", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output}")
    source_master_path = (
        args.source_contract_dir / "EIGHT_STACK_MASTER_INCLUSION_CONTRACT_R5.json"
    )
    refreshed_master_path = (
        args.refreshed_contract_dir
        / "EIGHT_STACK_MASTER_INCLUSION_CONTRACT_R5.json"
    )
    source_master = contract.read_json(source_master_path)
    refreshed_master = contract.read_json(refreshed_master_path)
    contract.validate_master_inclusion_contract(source_master)
    contract.validate_master_inclusion_contract(refreshed_master)
    refreshed_master_sha = contract.sha256_file(refreshed_master_path)
    if refreshed_master_sha != args.expected_master_sha256:
        raise contract.ContractError("external expected refreshed master hash mismatch")

    unchanged_files: list[str] = []
    for index in range(1, 9):
        prefix = f"{index:02d}_"
        for suffix in ("_config.json", "_determinism.json", "_manifest.json"):
            source = next(args.source_contract_dir.glob(prefix + "*" + suffix))
            refreshed = args.refreshed_contract_dir / source.name
            if source.read_bytes() != refreshed.read_bytes():
                raise contract.ContractError(
                    f"execution contract changed during completion-only refresh: {source.name}"
                )
            unchanged_files.append(source.name)

    normalized_source = dict(source_master)
    normalized_refreshed = dict(refreshed_master)
    normalized_source.pop("shared_completion_validator_sha256")
    normalized_refreshed.pop("shared_completion_validator_sha256")
    if normalized_source != normalized_refreshed:
        raise contract.ContractError("master changed beyond completion-validator hash")
    current_completion_hash = contract.sha256_file(
        Path(__file__).resolve().with_name("validate_eight_stack_completion.py")
    )
    if (
        refreshed_master["shared_completion_validator_sha256"]
        != current_completion_hash
    ):
        raise contract.ContractError("refreshed master/current completion hash mismatch")

    template_path = (
        args.refreshed_contract_dir / "AUTHORIZATION_RECEIPT_TEMPLATE_R5.json"
    )
    first_stack = contract.EXPECTED_STACK_IDS[0]
    first_manifest_path = next(args.refreshed_contract_dir.glob("01_*_manifest.json"))
    template_rejected = False
    try:
        contract.verify_authorization_receipt(
            receipt_path=template_path,
            master_contract_path=refreshed_master_path,
            selected_stack_id=first_stack,
            selected_manifest_sha256=refreshed_master["manifest_sha256_by_stack"][
                first_stack
            ],
            manifest=contract.read_json(first_manifest_path),
            expected_authorization_receipt_sha256=contract.sha256_file(template_path),
        )
    except contract.ContractError:
        template_rejected = True
    if not template_rejected:
        raise contract.ContractError("authorization template unexpectedly passed")

    report = {
        "schema_version": "same-source-completion-only-contract-refresh-audit-v1",
        "status": "CPU_STATIC_COMPLETION_REFRESH_PASS",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "model_actions_authorized": False,
        "model_bytes_read_during_refresh": False,
        "source_master_sha256": contract.sha256_file(source_master_path),
        "refreshed_master_sha256": refreshed_master_sha,
        "completion_validator_sha256": current_completion_hash,
        "unchanged_execution_contract_file_count": len(unchanged_files),
        "unchanged_execution_contract_files": sorted(unchanged_files),
        "master_changed_only_in_completion_validator_hash": True,
        "authorization_template_rejected": template_rejected,
    }
    args.output.write_bytes(contract.canonical_json_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
