"""Rebuild and audit all R11 static bindings without any model action."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Sequence

import mvp_static_contract as base
import r11_static_contract as contract


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument("--parent-runner", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--bridge-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def expect_rejected(function, label: str) -> str:
    try:
        function()
    except (contract.R11ContractError, base.ContractError) as error:
        return f"PASS_REJECTED:{type(error).__name__}:{error}"
    raise contract.R11ContractError(f"adversarial case unexpectedly passed: {label}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.output.exists():
        raise contract.R11ContractError(f"refusing to overwrite {args.output}")
    master_path = args.contract_dir / "R11_32_CELL_MASTER_INCLUSION_CONTRACT_R1.json"
    index_path = args.contract_dir / "R11_RELEASE_INDEX_R1.json"
    template_path = args.contract_dir / "R11_AUTHORIZATION_RECEIPT_TEMPLATE_R1.json"
    master = base.read_json(master_path)
    index = base.read_json(index_path)
    contract.validate_master(master)
    master_sha256 = base.sha256_file(master_path)
    if master_sha256 != args.expected_master_sha256:
        raise contract.R11ContractError("external expected master hash mismatch")
    if index["master_inclusion_contract_sha256"] != master_sha256:
        raise contract.R11ContractError("release index/master binding mismatch")
    if index["ordered_cell_ids"] != list(contract.EXPECTED_CELL_IDS):
        raise contract.R11ContractError("release index cell order drift")

    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    checked: list[dict[str, str]] = []
    for selected_cell in contract.EXPECTED_CELL_IDS:
        files = index["cell_files"][selected_cell]
        manifest_path = args.contract_dir / files["manifest"]
        arm_path = args.contract_dir / files["arm_contract"]
        manifest = base.read_json(manifest_path)
        contract.verify_preflight(
            manifest,
            config_path=args.contract_dir / files["config"],
            model_dir=args.model,
            source_path=args.source_bundles,
            target_path=args.target_calibration,
            mapping_path=args.mapping_stacks,
            runner_path=args.runner,
            validator_path=args.validator,
            asset_validation_path=args.asset_validation,
            determinism_addendum_path=args.contract_dir / files["determinism_addendum"],
            audit_seal_path=args.audit_seal,
            require_model_authorization=False,
            expected_manifest_sha256=master["manifest_sha256_by_cell"][selected_cell],
            arm_contract_path=arm_path,
            expected_arm_contract_sha256=master["arm_contract_sha256_by_cell"][selected_cell],
            parent_runner_path=args.parent_runner,
            protocol_path=args.protocol,
            bridge_contract_path=args.bridge_contract,
        )
        checked.append(
            {
                "cell_id": selected_cell,
                "manifest_sha256": base.sha256_file(manifest_path),
                "arm_contract_sha256": base.sha256_file(arm_path),
            }
        )

    first_cell = contract.EXPECTED_CELL_IDS[0]
    first_files = index["cell_files"][first_cell]
    first_manifest = base.read_json(args.contract_dir / first_files["manifest"])
    template_rejection = expect_rejected(
        lambda: contract.verify_authorization_receipt(
            receipt_path=template_path,
            expected_receipt_sha256=base.sha256_file(template_path),
            master_path=master_path,
            selected_cell_id=first_cell,
            selected_manifest_sha256=master["manifest_sha256_by_cell"][first_cell],
            selected_arm_contract_sha256=master["arm_contract_sha256_by_cell"][first_cell],
            manifest=first_manifest,
        ),
        "authorization template",
    )
    swapped = copy.deepcopy(first_manifest)
    swapped["arm"] = "GOLD_ONLY"
    arm_swap_rejection = expect_rejected(
        lambda: contract.validate_manifest_shape(swapped), "manifest arm swap"
    )
    incomplete = copy.deepcopy(master)
    del incomplete["manifest_sha256_by_cell"][contract.EXPECTED_CELL_IDS[-1]]
    missing_cell_rejection = expect_rejected(
        lambda: contract.validate_master(incomplete), "missing master cell"
    )

    with tempfile.TemporaryDirectory() as directory:
        tampered_path = Path(directory) / "arm_contract.json"
        tampered = base.read_json(args.contract_dir / first_files["arm_contract"])
        tampered["model_execution_authorized"] = True
        tampered_path.write_bytes(base.canonical_json_bytes(tampered))
        self_authorization_rejection = expect_rejected(
            lambda: contract.validate_arm_contract(
                tampered,
                expected_parent_runner_sha256=first_manifest["bindings"]["parent_r10_runner_sha256"],
                expected_protocol_sha256=first_manifest["bindings"]["r11_protocol_sha256"],
            ),
            "arm contract self authorization",
        )

    report = {
        "schema_version": "r11-bridge-static-audit-r1",
        "status": "R11_32_CELL_CPU_STATIC_CONTRACT_PASS_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "model_actions_authorized": False,
        "master_inclusion_contract_sha256": master_sha256,
        "process_count": len(checked),
        "cells": checked,
        "adversarial_checks": {
            "authorization_template_rejected": template_rejection,
            "manifest_arm_swap_rejected": arm_swap_rejection,
            "missing_master_cell_rejected": missing_cell_rejection,
            "arm_contract_self_authorization_rejected": self_authorization_rejection,
        },
        "audit_hidden_eligibility": False,
        "audit_label": "CURRENT_AUDIT_EXPOSED_AND_USED_FOR_CPU_STATIC_INTEGRITY_ONLY",
    }
    args.output.write_bytes(base.canonical_json_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
