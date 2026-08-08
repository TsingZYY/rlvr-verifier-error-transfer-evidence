from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mvp_static_contract as base
import r11_bridge_contract as bridge
import run_same_source_bridge_r11 as runner


class R11RunnerAdapterTests(unittest.TestCase):
    def test_runner_routes_parent_custody_calls_through_r11_contract(self) -> None:
        release = Path(__file__).resolve().with_name("frozen_r11_bridge_r1")
        index = base.read_json(release / "R11_RELEASE_INDEX_R1.json")
        selected_cell = index["ordered_cell_ids"][0]
        files = index["cell_files"][selected_cell]
        config_path = release / files["config"]
        manifest_path = release / files["manifest"]
        arm_path = release / files["arm_contract"]
        manifest_sha256 = base.sha256_file(manifest_path)
        arm_sha256 = base.sha256_file(arm_path)
        original_parent_preflight = runner.parent.static_contract.verify_preflight
        original_parent_invocation = (
            runner.parent.static_contract.verify_invocation_start_receipt
        )
        captured: dict[str, dict] = {}

        def fake_r11_preflight(manifest, **kwargs):
            captured["preflight"] = {"manifest": manifest, **kwargs}

        def fake_r11_invocation(**kwargs):
            captured["invocation"] = kwargs
            return {"status": "FROZEN_BEFORE_MODEL_LOAD"}

        def fake_parent_run(args):
            manifest = base.read_json(args.execution_manifest)
            runner.parent.static_contract.verify_preflight(
                manifest,
                config_path=args.config,
                model_dir=args.model,
                source_path=args.source_bundles,
                target_path=args.target_calibration,
                mapping_path=args.mapping_stacks,
                runner_path=Path(runner.parent.__file__).resolve(),
                validator_path=args.validator,
                asset_validation_path=args.asset_validation,
                determinism_addendum_path=args.determinism_addendum,
                audit_seal_path=args.audit_seal,
                require_model_authorization=True,
                expected_manifest_sha256=args.expected_manifest_sha256,
                master_inclusion_contract_path=args.master_inclusion_contract,
                authorization_receipt_path=args.authorization_receipt,
                expected_authorization_receipt_sha256=args.expected_authorization_receipt_sha256,
            )
            runner.parent.static_contract.verify_invocation_start_receipt(
                receipt_path=args.invocation_start_receipt,
                expected_receipt_sha256=args.expected_invocation_start_receipt_sha256,
                replicate_id=args.replicate_id,
                run_nonce=args.run_nonce,
                mapping_stack_id=manifest["selected_mapping_stack_id"],
                execution_manifest_sha256=args.expected_manifest_sha256,
                authorization_receipt_sha256=args.expected_authorization_receipt_sha256,
                authorization_id="00000000-0000-4000-8000-000000000001",
            )
            return {
                "run_status": "COMPLETE",
                "diagnostic_gate_status": "PASS",
                "stack_summary": {},
                "runtime_seconds": 0.0,
                "peak_gpu_memory_gib": 0.0,
                "source_updates": [
                    {
                        "source_rule_identity": identity,
                        "arm": "BUG",
                        "reward_mask_sha256": "a" * 64,
                        "clipping_not_triggered": True,
                    }
                    for identity in bridge.EXPECTED_IDENTITIES
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            output = temp / "result.json"
            args = argparse.Namespace(
                config=config_path,
                model=temp / "model",
                source_bundles=temp / "source.jsonl",
                target_calibration=temp / "target.jsonl",
                mapping_stacks=temp / "mapping.jsonl",
                execution_manifest=manifest_path,
                validator=temp / "validator.py",
                asset_validation=temp / "validation.json",
                determinism_addendum=release / files["determinism_addendum"],
                audit_seal=temp / "audit.json",
                expected_manifest_sha256=manifest_sha256,
                master_inclusion_contract=release
                / "R11_32_CELL_MASTER_INCLUSION_CONTRACT_R1.json",
                authorization_receipt=temp / "authorization.json",
                expected_authorization_receipt_sha256="b" * 64,
                replicate_id="A",
                run_nonce="00000000-0000-4000-8000-000000000001",
                invocation_start_receipt=temp / "invocation.json",
                expected_invocation_start_receipt_sha256="c" * 64,
                arm_contract=arm_path,
                expected_arm_contract_sha256=arm_sha256,
                output=output,
            )
            with mock.patch.object(runner.parent, "run", side_effect=fake_parent_run), mock.patch.object(
                runner.r11_custody, "verify_preflight", side_effect=fake_r11_preflight
            ), mock.patch.object(
                runner.r11_custody,
                "verify_invocation_start_receipt",
                side_effect=fake_r11_invocation,
            ):
                result = runner.run_bridge(args)

        self.assertEqual(result["arm"], "BUG")
        self.assertTrue(captured["preflight"]["require_model_authorization"])
        self.assertEqual(captured["preflight"]["arm_contract_path"], arm_path)
        self.assertEqual(captured["invocation"]["arm"], "BUG")
        self.assertEqual(captured["invocation"]["arm_contract_sha256"], arm_sha256)
        self.assertIs(
            runner.parent.static_contract.verify_preflight, original_parent_preflight
        )
        self.assertIs(
            runner.parent.static_contract.verify_invocation_start_receipt,
            original_parent_invocation,
        )


if __name__ == "__main__":
    unittest.main()
