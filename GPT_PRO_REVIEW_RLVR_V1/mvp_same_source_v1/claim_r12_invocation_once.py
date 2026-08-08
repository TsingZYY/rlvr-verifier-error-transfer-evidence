"""Atomically consume one R11 invocation receipt in an R12 claim ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import r12_invocation_custody as custody


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--arm", choices=("BUG", "GOLD_ONLY"), required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--invocation-receipt", type=Path, required=True)
    parser.add_argument("--expected-invocation-receipt-sha256", required=True)
    parser.add_argument("--invocation-plan", type=Path, required=True)
    parser.add_argument("--expected-invocation-plan-sha256", required=True)
    parser.add_argument("--signed-launch-authorization", type=Path, required=True)
    parser.add_argument("--expected-signed-launch-authorization-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--arm-contract", type=Path, required=True)
    parser.add_argument("--expected-arm-contract-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--expected-result-output", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    claim = custody.claim_invocation_once(
        invocation_receipt_path=args.invocation_receipt,
        expected_invocation_sha256=args.expected_invocation_receipt_sha256,
        invocation_plan_path=args.invocation_plan,
        expected_invocation_plan_sha256=args.expected_invocation_plan_sha256,
        signed_launch_path=args.signed_launch_authorization,
        expected_signed_launch_sha256=(
            args.expected_signed_launch_authorization_sha256
        ),
        # Deliberately fail closed: no CLI argument can install a trust root.
        trusted_signer_policy=None,
        ledger_root=args.ledger_root,
        expected_result_output_path=args.expected_result_output,
        expected_run_nonce=args.run_nonce,
        expected_arm=args.arm,
        master_path=args.master_inclusion_contract,
        expected_master_sha256=args.expected_master_sha256,
        authorization_path=args.authorization_receipt,
        expected_authorization_sha256=args.expected_authorization_receipt_sha256,
        manifest_path=args.manifest,
        expected_manifest_sha256=args.expected_manifest_sha256,
        arm_contract_path=args.arm_contract,
        expected_arm_contract_sha256=args.expected_arm_contract_sha256,
        selected_cell_id=args.cell_id,
    )
    print(
        json.dumps(
            {
                "status": claim.value["status"],
                "cell_id": claim.value["cell_id"],
                "run_nonce": claim.value["run_nonce"],
                "expected_result_output": claim.value["expected_result_output_path"],
                "claim_sha256": claim.sha256,
                "claim_path": str(claim.path),
                "model_execution_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
