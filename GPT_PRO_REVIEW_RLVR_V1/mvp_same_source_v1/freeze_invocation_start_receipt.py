"""Freeze one externally anchored replica invocation before any model load."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replicate-id", choices=("A", "B"), required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output}")
    manifest = contract.read_json(args.manifest)
    manifest_sha = contract.sha256_file(args.manifest)
    if manifest_sha != args.expected_manifest_sha256:
        raise contract.ContractError("external expected manifest hash mismatch")
    if contract.sha256_file(args.master_inclusion_contract) != args.expected_master_sha256:
        raise contract.ContractError("external expected master hash mismatch")
    contract.verify_authorization_receipt(
        receipt_path=args.authorization_receipt,
        master_contract_path=args.master_inclusion_contract,
        selected_stack_id=manifest["selected_mapping_stack_id"],
        selected_manifest_sha256=manifest_sha,
        manifest=manifest,
        expected_authorization_receipt_sha256=args.expected_authorization_receipt_sha256,
    )
    authorization = contract.read_json(args.authorization_receipt)
    receipt = {
        "schema_version": contract.INVOCATION_RECEIPT_SCHEMA,
        "status": "FROZEN_BEFORE_MODEL_LOAD",
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        "replicate_id": args.replicate_id,
        "run_nonce": args.run_nonce,
        "mapping_stack_id": manifest["selected_mapping_stack_id"],
        "execution_manifest_sha256": manifest_sha,
        "authorization_receipt_sha256": args.expected_authorization_receipt_sha256,
        "authorization_id": authorization["authorization_id"],
        "started_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "custody_requirement": "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(contract.canonical_json_bytes(receipt))
    print(
        json.dumps(
            {
                "replicate_id": args.replicate_id,
                "run_nonce": args.run_nonce,
                "invocation_start_receipt_sha256": contract.sha256_file(args.output),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
