"""R12 custody wrapper for one frozen R11 bridge process.

The single-use claim is durably created before ``run_bridge`` can reach model
load.  The inner R11 result is written only to a private temporary directory;
the final output is created exactly once after all R12 postconditions pass.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import math
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

import r11_static_contract as r11_contract
import r12_invocation_custody as custody


_RUN_GUARD = threading.Lock()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one R11 bridge cell through the R12 single-use custody gate."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--determinism-addendum", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--replicate-id", choices=("A", "B"), required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--invocation-start-receipt", type=Path, required=True)
    parser.add_argument("--expected-invocation-start-receipt-sha256", required=True)
    parser.add_argument("--arm-contract", type=Path, required=True)
    parser.add_argument("--expected-arm-contract-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--invocation-plan", type=Path, required=True)
    parser.add_argument("--expected-invocation-plan-sha256", required=True)
    parser.add_argument("--signed-launch-authorization", type=Path, required=True)
    parser.add_argument("--expected-signed-launch-authorization-sha256", required=True)
    parser.add_argument("--expected-master-sha256", required=True)
    parser.add_argument("--claim-ledger-root", type=Path, required=True)
    return parser


def assert_finite(value: Any, path: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise custody.R12CustodyError(f"non-finite result value at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")


def _validate_inner_result(
    result: dict[str, Any],
    *,
    args: argparse.Namespace,
    claim: custody.HashedJson,
) -> None:
    manifest = custody.read_hashed_json(
        args.execution_manifest, expected_sha256=args.expected_manifest_sha256
    )
    authorization = custody.read_hashed_json(
        args.authorization_receipt,
        expected_sha256=args.expected_authorization_receipt_sha256,
    )
    stack, arm, replicate = r11_contract.parse_cell_id(claim.value["cell_id"])
    custody.require(
        result.get("bridge_schema_version") == "r11-same-source-bridge-result-r1",
        "inner result is not an R11 bridge result",
    )
    custody.require(result.get("mapping_stack_id") == stack, "inner result stack mismatch")
    custody.require(result.get("arm") == arm, "inner result arm mismatch")
    custody.require(result.get("replicate_id") == replicate, "inner result replicate mismatch")
    custody.require(result.get("run_nonce") == claim.value["run_nonce"], "inner result nonce mismatch")
    custody.require(
        result.get("execution_manifest_sha256") == manifest.sha256,
        "inner result manifest mismatch",
    )
    custody.require(
        result.get("r11_arm_contract_sha256") == claim.value["arm_contract_sha256"],
        "inner result arm-contract mismatch",
    )
    custody.require(
        result.get("authorization_receipt_sha256") == authorization.sha256,
        "inner result authorization hash mismatch",
    )
    custody.require(
        result.get("authorization_id") == authorization.value["authorization_id"],
        "inner result authorization id mismatch",
    )
    custody.require(
        result.get("invocation_start_receipt_sha256")
        == claim.value["invocation_receipt_sha256"],
        "inner result invocation hash mismatch",
    )
    custody.require(
        result.get("development_screen_only") is True
        and result.get("formal_confirmatory") is False,
        "inner result evidence boundary drift",
    )


def run_custodied(
    args: argparse.Namespace,
    *,
    now=None,
    inner_runner: Callable[[argparse.Namespace], dict[str, Any]] | None = None,
    trusted_signer_policy: custody.TrustedSignerPolicy | None = None,
) -> tuple[dict[str, Any], custody.HashedJson]:
    if not _RUN_GUARD.acquire(blocking=False):
        raise custody.R12CustodyError("R12 runner is non-reentrant within one process")
    try:
        custody.require(
            threading.active_count() == 1,
            "R12 runner requires a dedicated single-thread Python process",
        )
        custody.require_no_symlink_ancestors(args.output, "final result output")
        final_output = custody.canonical_path(args.output)
        custody.require(
            not final_output.exists(), f"refusing to overwrite final result: {final_output}"
        )
        manifest = custody.read_hashed_json(
            args.execution_manifest, expected_sha256=args.expected_manifest_sha256
        )
        selected_cell = str(manifest.value["bridge_cell_id"])
        claim = custody.claim_invocation_once(
            invocation_receipt_path=args.invocation_start_receipt,
            expected_invocation_sha256=args.expected_invocation_start_receipt_sha256,
            invocation_plan_path=args.invocation_plan,
            expected_invocation_plan_sha256=args.expected_invocation_plan_sha256,
            signed_launch_path=args.signed_launch_authorization,
            expected_signed_launch_sha256=(
                args.expected_signed_launch_authorization_sha256
            ),
            trusted_signer_policy=trusted_signer_policy,
            ledger_root=args.claim_ledger_root,
            expected_result_output_path=final_output,
            expected_run_nonce=args.run_nonce,
            expected_arm=str(manifest.value["arm"]),
            master_path=args.master_inclusion_contract,
            expected_master_sha256=args.expected_master_sha256,
            authorization_path=args.authorization_receipt,
            expected_authorization_sha256=args.expected_authorization_receipt_sha256,
            manifest_path=args.execution_manifest,
            expected_manifest_sha256=args.expected_manifest_sha256,
            arm_contract_path=args.arm_contract,
            expected_arm_contract_sha256=args.expected_arm_contract_sha256,
            selected_cell_id=selected_cell,
            now=now,
        )

        final_output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".r12-private-", dir=final_output.parent
        ) as private_directory:
            inner_args = copy.copy(args)
            inner_args.output = Path(private_directory) / "inner-r11-result.json"
            selected_runner = inner_runner
            if selected_runner is None:
                r11_runner = importlib.import_module("run_same_source_bridge_r11")
                selected_runner = r11_runner.run_bridge
            result = selected_runner(inner_args)
            _validate_inner_result(result, args=args, claim=claim)
            result["r12_custody_schema_version"] = "r12-custodied-r11-bridge-result-r1"
            result["r12_invocation_plan_sha256"] = args.expected_invocation_plan_sha256
            result["r12_consumption_claim_sha256"] = claim.sha256
            result["r12_consumption_claim_path"] = str(claim.path)
            result["r12_signed_launch_authorization_sha256"] = claim.value[
                "signed_launch_authorization_sha256"
            ]
            result["r12_signed_launch_message_sha256"] = claim.value[
                "signed_launch_message_sha256"
            ]
            result["r12_trusted_signer_id"] = claim.value["trusted_signer_id"]
            result["r12_trusted_signer_key_fingerprint_sha256"] = claim.value[
                "trusted_signer_key_fingerprint_sha256"
            ]
            result["r12_custody_runner_sha256"] = custody.sha256_file_snapshot(
                Path(__file__).resolve()
            )
            result["r12_final_output_path"] = str(final_output)
            result["r12_claimed_before_model_load"] = True
            assert_finite(result)
            final_snapshot = custody.atomic_create_json(final_output, result)
        return result, final_snapshot
    finally:
        _RUN_GUARD.release()


def main() -> None:
    args = build_parser().parse_args()
    result, final_snapshot = run_custodied(args)
    print(
        json.dumps(
            {
                "run_status": result["run_status"],
                "cell_id": custody.read_hashed_json(args.invocation_plan).value["cell_id"],
                "r12_consumption_claim_sha256": result[
                    "r12_consumption_claim_sha256"
                ],
                "final_result_sha256": final_snapshot.sha256,
                "output": str(final_snapshot.path),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
