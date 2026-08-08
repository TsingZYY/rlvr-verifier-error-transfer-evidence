"""Freeze one exact R11 invocation receipt through the R12 custody checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import r12_invocation_custody as custody


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--arm-contract", type=Path, required=True)
    parser.add_argument("--expected-arm-contract-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--signed-launch-authorization", type=Path, required=True)
    parser.add_argument("--expected-signed-launch-authorization-sha256", required=True)
    parser.add_argument("--expected-result-output", type=Path, required=True)
    parser.add_argument("--claim-ledger-root", type=Path, required=True)
    parser.add_argument("--started-at-utc")
    parser.add_argument("--r11-receipt-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="R12 freeze-plan output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    frozen = custody.freeze_r12_invocation(
        receipt_output_path=args.r11_receipt_output,
        plan_output_path=args.output,
        expected_result_output_path=args.expected_result_output,
        claim_ledger_root=args.claim_ledger_root,
        run_nonce=args.run_nonce,
        signed_launch_path=args.signed_launch_authorization,
        expected_signed_launch_sha256=(
            args.expected_signed_launch_authorization_sha256
        ),
        # Deliberately fail closed until an embedding operator provisions a
        # trusted verifier/key outside caller-controlled CLI inputs.
        trusted_signer_policy=None,
        master_path=args.master_inclusion_contract,
        expected_master_sha256=args.expected_master_sha256,
        authorization_path=args.authorization_receipt,
        expected_authorization_sha256=args.expected_authorization_receipt_sha256,
        manifest_path=args.manifest,
        expected_manifest_sha256=args.expected_manifest_sha256,
        arm_contract_path=args.arm_contract,
        expected_arm_contract_sha256=args.expected_arm_contract_sha256,
        selected_cell_id=args.cell_id,
        started_at_utc=args.started_at_utc,
    )
    print(
        json.dumps(
            {
                "status": "R11_INVOCATION_FROZEN_R12_CLAIM_PENDING",
                "cell_id": frozen.receipt.value["cell_id"],
                "run_nonce": frozen.receipt.value["run_nonce"],
                "invocation_receipt_sha256": frozen.receipt.sha256,
                "receipt_output": str(frozen.receipt.path),
                "invocation_plan_sha256": frozen.plan.sha256,
                "plan_output": str(frozen.plan.path),
                "expected_result_output": str(
                    custody.canonical_path(args.expected_result_output)
                ),
                "model_execution_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
