"""Freeze two completed result files into an independently-custodied receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Sequence

import mvp_static_contract as contract


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-a", type=Path, required=True)
    parser.add_argument("--result-b", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.output.exists():
        raise contract.ContractError(f"refusing to overwrite {args.output}")
    if args.result_a.resolve() == args.result_b.resolve():
        raise contract.ContractError("replica A and B resolve to the same result path")
    if os.path.samefile(args.result_a, args.result_b):
        raise contract.ContractError("replica A and B are the same filesystem object")
    output_parent = args.output.resolve().parent
    for result in (args.result_a, args.result_b):
        result_parent = result.resolve().parent
        if _is_within(output_parent, result_parent):
            raise contract.ContractError(
                "result anchor must be stored outside both result output directories"
            )
    manifest = contract.read_json(args.manifest)
    result_a = contract.read_json(args.result_a)
    result_b = contract.read_json(args.result_b)
    if result_a.get("schema_version") != "same-source-diagnostic-mvp-result-r5" or result_b.get(
        "schema_version"
    ) != "same-source-diagnostic-mvp-result-r5":
        raise contract.ContractError("old or unknown result schema rejected")
    for label, result in (("A", result_a), ("B", result_b)):
        contract.validate_evidence_boundary(result.get("evidence_boundary"))
        if contract.UUID4_RE.fullmatch(str(result.get("run_nonce", ""))) is None:
            raise contract.ContractError(f"replica {label} run_nonce is not UUIDv4")
        if contract.UUID4_RE.fullmatch(str(result.get("authorization_id", ""))) is None:
            raise contract.ContractError(f"replica {label} authorization_id is not UUIDv4")
        for field in (
            "invocation_start_receipt_sha256",
            "authorization_receipt_sha256",
        ):
            value = result.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise contract.ContractError(f"replica {label} {field} is malformed")
    if result_a.get("replicate_id") != "A" or result_b.get("replicate_id") != "B":
        raise contract.ContractError("replicate identities must be ordered A then B")
    if result_a.get("run_nonce") == result_b.get("run_nonce"):
        raise contract.ContractError("replica run nonces must be distinct")
    if result_a.get("invocation_start_receipt_sha256") == result_b.get(
        "invocation_start_receipt_sha256"
    ):
        raise contract.ContractError("replica invocation receipts must be distinct")
    if result_a.get("authorization_receipt_sha256") != result_b.get(
        "authorization_receipt_sha256"
    ) or result_a.get("authorization_id") != result_b.get("authorization_id"):
        raise contract.ContractError("replica authorization binding mismatch")
    master = contract.read_json(args.master_inclusion_contract)
    contract.validate_master_inclusion_contract(master)
    stack_id = manifest.get("selected_mapping_stack_id")
    manifest_sha = contract.sha256_file(args.manifest)
    if master["manifest_sha256_by_stack"].get(stack_id) != manifest_sha:
        raise contract.ContractError("selected manifest is not in the master contract")
    anchor = {
        "schema_version": "same-source-result-pair-anchor-r5",
        "status": "FROZEN_POST_RUN_PRE_VALIDATION",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        "mapping_stack_id": stack_id,
        "master_inclusion_contract_sha256": contract.sha256_file(
            args.master_inclusion_contract
        ),
        "execution_manifest_sha256": manifest_sha,
        "result_a_sha256": contract.sha256_file(args.result_a),
        "result_b_sha256": contract.sha256_file(args.result_b),
        "authorization_receipt_sha256": result_a["authorization_receipt_sha256"],
        "authorization_id": result_a["authorization_id"],
        "result_a_replicate_id": result_a["replicate_id"],
        "result_b_replicate_id": result_b["replicate_id"],
        "result_a_run_nonce": result_a["run_nonce"],
        "result_b_run_nonce": result_b["run_nonce"],
        "invocation_start_receipt_a_sha256": result_a[
            "invocation_start_receipt_sha256"
        ],
        "invocation_start_receipt_b_sha256": result_b[
            "invocation_start_receipt_sha256"
        ],
        "custody_requirement": "STORE_OUTSIDE_BOTH_RESULT_OUTPUT_DIRECTORIES_AND_RECORD_SHA256_EXTERNALLY",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(contract.canonical_json_bytes(anchor))
    print(
        json.dumps(
            {
                "status": anchor["status"],
                "anchor_sha256": contract.sha256_file(args.output),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
