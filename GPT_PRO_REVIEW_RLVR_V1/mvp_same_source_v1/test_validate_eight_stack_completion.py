"""Mutation tests for the exact R5 eight-stack completion validator."""

from __future__ import annotations

import copy
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "validate_eight_stack_completion", HERE / "validate_eight_stack_completion.py"
)
assert SPEC is not None and SPEC.loader is not None
completion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(completion)
contract = completion.contract


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def master_fixture() -> dict:
    return {
        "schema_version": contract.MASTER_CONTRACT_SCHEMA,
        "status": "FROZEN_EIGHT_STACK_SCREEN_AUTHORIZATION_PENDING",
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
        "model_execution_performed": False,
        "ordered_stack_ids": list(contract.EXPECTED_STACK_IDS),
        "required_stack_count": 8,
        "config_sha256_by_stack": {
            stack: f"{index + 1:064x}"
            for index, stack in enumerate(contract.EXPECTED_STACK_IDS)
        },
        "manifest_sha256_by_stack": {
            stack: f"{index + 11:064x}"
            for index, stack in enumerate(contract.EXPECTED_STACK_IDS)
        },
        "shared_runner_sha256": "a" * 64,
        "shared_validator_sha256": "b" * 64,
        "shared_completion_validator_sha256": "9" * 64,
        "shared_static_contract_sha256": "c" * 64,
        "shared_model_recursive_inventory_sha256": "d" * 64,
        "inclusion_rule": "RUN_ALL_EIGHT_STACKS_WITHOUT_SELECTION_OR_DELETION",
        "exclusion_rule": "NO_STACK_IDENTITY_THRESHOLD_OR_HYPERPARAMETER_ADAPTATION",
        "claim_boundary": list(contract.FROZEN_CLAIM_BOUNDARY),
        "authorization": {
            "user_authorization_received": False,
            "model_actions_allowed": False,
            "sampled_rlvr_allowed": False,
        },
    }


class EightStackCompletionTests(unittest.TestCase):
    def validate(
        self,
        *,
        missing_last: bool = False,
        duplicate_stack: bool = False,
        old_r2: bool = False,
        mutation: str | None = None,
    ) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master = master_fixture()
            master_path = root / "master.json"
            master_path.write_bytes(contract.canonical_json_bytes(master))
            master_sha = contract.sha256_file(master_path)
            anchors: list[dict] = []
            reports: list[dict] = []
            anchor_paths: list[Path] = []
            report_paths: list[Path] = []
            anchor_hashes: list[str] = []
            report_hashes: list[str] = []
            stacks = list(contract.EXPECTED_STACK_IDS)
            if missing_last:
                stacks = stacks[:-1]
            for index, stack in enumerate(stacks):
                effective_stack = stacks[0] if duplicate_stack and index == 1 else stack
                anchor = {
                    "schema_version": (
                        "same-source-result-pair-anchor-r2"
                        if old_r2 and index == 0
                        else "same-source-result-pair-anchor-r5"
                    ),
                    "status": "FROZEN_POST_RUN_PRE_VALIDATION",
                    "scientific_evidence": False,
                    "formal_experiment": False,
                    "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
                    "mapping_stack_id": effective_stack,
                    "master_inclusion_contract_sha256": master_sha,
                    "execution_manifest_sha256": master[
                        "manifest_sha256_by_stack"
                    ][effective_stack],
                    "result_a_sha256": f"{index * 2 + 101:064x}",
                    "result_b_sha256": f"{index * 2 + 102:064x}",
                    "authorization_receipt_sha256": "e" * 64,
                    "authorization_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "result_a_replicate_id": "A",
                    "result_b_replicate_id": "B",
                    "result_a_run_nonce": f"{index + 1:08x}-0000-4000-8000-000000000001",
                    "result_b_run_nonce": f"{index + 1:08x}-0000-4000-8000-000000000002",
                    "invocation_start_receipt_a_sha256": f"{index * 2 + 201:064x}",
                    "invocation_start_receipt_b_sha256": f"{index * 2 + 202:064x}",
                    "custody_requirement": "STORE_OUTSIDE_BOTH_RESULT_OUTPUT_DIRECTORIES_AND_RECORD_SHA256_EXTERNALLY",
                }
                anchor_path = root / f"anchor-{index}.json"
                anchor_path.write_bytes(contract.canonical_json_bytes(anchor))
                anchor_hash = contract.sha256_file(anchor_path)
                report = {
                    "schema_version": "same-source-diagnostic-mvp-validation-r5",
                    "validation_status": "PASS",
                    "scientific_evidence": False,
                    "formal_experiment": False,
                    "evidence_boundary": dict(contract.EVIDENCE_BOUNDARY),
                    "mapping_stack_id": effective_stack,
                    "result_anchor_sha256": anchor_hash,
                    "substantive_replicates_identical": True,
                    "errors": [],
                    "stack_summary": {
                        "mean_diagonal_excess": float(index) / 100.0,
                        "positive_diagonal_excess_count": 5,
                        "source_rule_count": 5,
                    },
                    "norm_evidence_boundary": dict(contract.NORM_EVIDENCE_BOUNDARY),
                    "evidence_label": "CPU_STATIC_REPAIR_VALIDATOR_ONLY_R5",
                }
                report_path = root / f"report-{index}.json"
                report_path.write_bytes(contract.canonical_json_bytes(report))
                anchors.append(anchor)
                reports.append(report)
                anchor_paths.append(anchor_path)
                report_paths.append(report_path)
                anchor_hashes.append(anchor_hash)
                report_hashes.append(contract.sha256_file(report_path))
            if mutation == "duplicate_nonce_cross_stack":
                anchors[1]["result_a_run_nonce"] = anchors[0]["result_a_run_nonce"]
            elif mutation == "malformed_result_hash":
                anchors[0]["result_a_sha256"] = "not-a-sha256"
            elif mutation == "malformed_invocation_hash":
                anchors[0]["invocation_start_receipt_a_sha256"] = "also-not-a-sha256"
            elif mutation == "malformed_authorization_hash":
                for anchor in anchors:
                    anchor["authorization_receipt_sha256"] = "not-a-sha256"
            elif mutation == "malformed_authorization_id":
                for anchor in anchors:
                    anchor["authorization_id"] = "not-a-uuid"
            elif mutation == "malformed_nonce":
                anchors[0]["result_a_run_nonce"] = "not-a-uuid"
            elif mutation == "count_above_five":
                reports[0]["stack_summary"]["positive_diagonal_excess_count"] = 999
            elif mutation == "count_negative":
                reports[0]["stack_summary"]["positive_diagonal_excess_count"] = -1
            elif mutation == "count_fractional":
                reports[0]["stack_summary"]["positive_diagonal_excess_count"] = 3.7
            elif mutation == "count_boolean":
                reports[0]["stack_summary"]["positive_diagonal_excess_count"] = True
            elif mutation == "fail_report":
                reports[0]["validation_status"] = "FAIL"
                reports[0]["errors"] = ["synthetic failure"]
                reports[0]["stack_summary"]["mean_diagonal_excess"] = 999.0
                reports[0]["stack_summary"]["positive_diagonal_excess_count"] = 5
            elif mutation == "large_finite_means":
                for report in reports:
                    report["stack_summary"]["mean_diagonal_excess"] = 1e308
            elif mutation == "huge_integer_mean":
                reports[0]["stack_summary"]["mean_diagonal_excess"] = 10**1000
            elif mutation is not None:
                raise AssertionError(f"unknown mutation: {mutation}")

            anchor_hashes = []
            report_hashes = []
            for index, (anchor, report) in enumerate(zip(anchors, reports)):
                anchor_paths[index].write_bytes(contract.canonical_json_bytes(anchor))
                anchor_hash = contract.sha256_file(anchor_paths[index])
                anchor_hashes.append(anchor_hash)
                report["result_anchor_sha256"] = anchor_hash
                report_paths[index].write_bytes(contract.canonical_json_bytes(report))
                report_hashes.append(contract.sha256_file(report_paths[index]))

            return completion.validate_completion(
                master=master,
                master_sha256=master_sha,
                expected_master_sha256=master_sha,
                anchor_paths=anchor_paths,
                anchors=anchors,
                anchor_sha256=anchor_hashes,
                expected_anchor_sha256=list(anchor_hashes),
                report_paths=report_paths,
                reports=reports,
                report_sha256=report_hashes,
                expected_report_sha256=list(report_hashes),
            )

    def test_valid_exact_eight_stack_completion_passes(self) -> None:
        report = self.validate()
        self.assertEqual(report["validation_status"], "PASS", report["errors"])
        self.assertEqual(report["evidence_boundary"]["old_r2_status"], "OLD_R2_EXCLUDED")
        self.assertEqual(
            report["aggregate"]["equal_mean_over_eight_stack_mean_diagonal_excess"],
            0.035,
        )
        self.assertEqual(report["aggregate"]["total_positive_diagonal_excess_count"], 40)

    def test_missing_one_of_eight_stacks_rejected(self) -> None:
        report = self.validate(missing_last=True)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("exact 8/8" in error or "exactly 8" in error for error in report["errors"]))

    def test_duplicate_stack_in_completion_rejected(self) -> None:
        report = self.validate(duplicate_stack=True)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("duplicate stack" in error for error in report["errors"]))

    def test_old_r2_result_rejected(self) -> None:
        report = self.validate(old_r2=True)
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("old or unknown" in error for error in report["errors"]))
        self.assertIsNone(report["aggregate"])

    def test_duplicate_nonce_across_different_stacks_rejected(self) -> None:
        report = self.validate(mutation="duplicate_nonce_cross_stack")
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertTrue(any("globally distinct" in error for error in report["errors"]))
        self.assertIsNone(report["aggregate"])

    def test_completion_internal_identifier_formats_required(self) -> None:
        for mutation in (
            "malformed_result_hash",
            "malformed_invocation_hash",
            "malformed_authorization_hash",
            "malformed_authorization_id",
            "malformed_nonce",
        ):
            with self.subTest(mutation=mutation):
                report = self.validate(mutation=mutation)
                self.assertEqual(report["validation_status"], "FAIL")
                self.assertTrue(any("malformed" in error for error in report["errors"]))
                self.assertIsNone(report["aggregate"])

    def test_positive_diagonal_excess_count_domain_required(self) -> None:
        for mutation in (
            "count_above_five",
            "count_negative",
            "count_fractional",
            "count_boolean",
        ):
            with self.subTest(mutation=mutation):
                report = self.validate(mutation=mutation)
                self.assertEqual(report["validation_status"], "FAIL")
                self.assertTrue(any("aggregate inputs" in error for error in report["errors"]))
                self.assertIsNone(report["aggregate"])

    def test_fail_report_produces_null_aggregate(self) -> None:
        report = self.validate(mutation="fail_report")
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertIsNone(report["aggregate"])

    def test_large_finite_stack_means_cannot_produce_infinite_aggregate(self) -> None:
        report = self.validate(mutation="large_finite_means")
        self.assertEqual(report["validation_status"], "PASS", report["errors"])
        aggregate_mean = report["aggregate"][
            "equal_mean_over_eight_stack_mean_diagonal_excess"
        ]
        self.assertTrue(math.isfinite(aggregate_mean))
        self.assertEqual(aggregate_mean, 1e308)

    def test_huge_integer_mean_returns_fail_with_null_aggregate(self) -> None:
        report = self.validate(mutation="huge_integer_mean")
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertIsNone(report["aggregate"])
        self.assertTrue(any("aggregate inputs" in error for error in report["errors"]))

    def test_numeric_conversion_overflow_is_caught_fail_closed(self) -> None:
        self.assertIsNone(completion._finite_float(10**1000))
        report = self.validate(mutation="huge_integer_mean")
        self.assertEqual(report["validation_status"], "FAIL")
        self.assertIsNone(report["aggregate"])

    def test_aggregate_mean_is_explicitly_finite_before_pass(self) -> None:
        report = self.validate(mutation="large_finite_means")
        self.assertEqual(report["validation_status"], "PASS")
        self.assertTrue(
            math.isfinite(
                report["aggregate"][
                    "equal_mean_over_eight_stack_mean_diagonal_excess"
                ]
            )
        )

    def test_fail_report_is_canonical_json_serializable(self) -> None:
        report = self.validate(mutation="huge_integer_mean")
        encoded = contract.canonical_json_bytes(report)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["validation_status"], "FAIL")
        self.assertIsNone(decoded["aggregate"])

    def test_cli_emits_valid_fail_json_for_huge_numeric_input(self) -> None:
        failure = self.validate(mutation="huge_integer_mean")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.json"
            master_path.write_text("{}\n", encoding="utf-8")
            anchor_paths: list[Path] = []
            report_paths: list[Path] = []
            for index in range(8):
                anchor_path = root / f"anchor-{index}.json"
                report_path = root / f"report-{index}.json"
                anchor_path.write_text("{}\n", encoding="utf-8")
                report_path.write_text("{}\n", encoding="utf-8")
                anchor_paths.append(anchor_path)
                report_paths.append(report_path)
            output = root / "completion.json"
            argv = [
                "--master-inclusion-contract",
                str(master_path),
                "--expected-master-sha256",
                "0" * 64,
            ]
            for path in anchor_paths:
                argv.extend(("--result-anchor", str(path)))
            for _ in anchor_paths:
                argv.extend(("--expected-result-anchor-sha256", "1" * 64))
            for path in report_paths:
                argv.extend(("--validation-report", str(path)))
            for _ in report_paths:
                argv.extend(("--expected-validation-report-sha256", "2" * 64))
            argv.extend(("--output", str(output)))
            with mock.patch.object(
                completion, "validate_completion", return_value=failure
            ), working_directory(root):
                exit_code = completion.main(argv)
            self.assertEqual(exit_code, 1)
            decoded = json.loads(output.read_bytes())
            self.assertEqual(decoded["validation_status"], "FAIL")
            self.assertIsNone(decoded["aggregate"])

    def test_cli_json_integer_parser_limit_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.json"
            master_path.write_text("{}\n", encoding="utf-8")
            anchor_paths: list[Path] = []
            report_paths: list[Path] = []
            for index in range(8):
                anchor_path = root / f"anchor-{index}.json"
                report_path = root / f"report-{index}.json"
                anchor_path.write_text("{}\n", encoding="utf-8")
                report_path.write_text("{}\n", encoding="utf-8")
                anchor_paths.append(anchor_path)
                report_paths.append(report_path)
            report_paths[0].write_text(
                '{"mean_diagonal_excess":' + "9" * 10000 + "}\n",
                encoding="utf-8",
            )
            output = root / "completion.json"
            argv = [
                "--master-inclusion-contract",
                str(master_path),
                "--expected-master-sha256",
                "0" * 64,
            ]
            for path in anchor_paths:
                argv.extend(("--result-anchor", str(path)))
            for _ in anchor_paths:
                argv.extend(("--expected-result-anchor-sha256", "1" * 64))
            for path in report_paths:
                argv.extend(("--validation-report", str(path)))
            for _ in report_paths:
                argv.extend(("--expected-validation-report-sha256", "2" * 64))
            argv.extend(("--output", str(output)))
            with working_directory(root):
                exit_code = completion.main(argv)
            self.assertEqual(exit_code, 1)
            decoded = json.loads(output.read_bytes())
            self.assertEqual(decoded["validation_status"], "FAIL")
            self.assertIsNone(decoded["aggregate"])
            self.assertTrue(
                any("read/parse failure" in error for error in decoded["errors"])
            )

    def test_cli_other_input_read_parse_errors_are_fail_closed(self) -> None:
        for case, payload in (
            ("malformed_json", b"{\n"),
            ("invalid_utf8", b"\xff\xfe"),
            ("non_object_root", b"[]\n"),
            ("missing_file", None),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                master_path = root / "master.json"
                master_path.write_text("{}\n", encoding="utf-8")
                anchor_paths: list[Path] = []
                report_paths: list[Path] = []
                for index in range(8):
                    anchor_path = root / f"anchor-{index}.json"
                    report_path = root / f"report-{index}.json"
                    anchor_path.write_text("{}\n", encoding="utf-8")
                    if index != 0 or payload is not None:
                        report_path.write_bytes(payload if index == 0 else b"{}\n")
                    anchor_paths.append(anchor_path)
                    report_paths.append(report_path)
                output = root / "completion.json"
                argv = [
                    "--master-inclusion-contract",
                    str(master_path),
                    "--expected-master-sha256",
                    "0" * 64,
                ]
                for path in anchor_paths:
                    argv.extend(("--result-anchor", str(path)))
                for _ in anchor_paths:
                    argv.extend(("--expected-result-anchor-sha256", "1" * 64))
                for path in report_paths:
                    argv.extend(("--validation-report", str(path)))
                for _ in report_paths:
                    argv.extend(("--expected-validation-report-sha256", "2" * 64))
                argv.extend(("--output", str(output)))
                with working_directory(root):
                    self.assertEqual(completion.main(argv), 1)
                decoded = json.loads(output.read_bytes())
                self.assertEqual(decoded["validation_status"], "FAIL")
                self.assertIsNone(decoded["aggregate"])
                self.assertTrue(
                    any("read/parse failure" in error for error in decoded["errors"])
                )

    def test_cli_preexisting_output_is_preserved_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "completion.json"
            sentinel = b"DO-NOT-OVERWRITE\n"
            output.write_bytes(sentinel)
            stream = io.StringIO()
            with redirect_stdout(stream), working_directory(root):
                exit_code = completion.main(
                    [
                        "--master-inclusion-contract",
                        str(root / "unused-master.json"),
                        "--expected-master-sha256",
                        "0" * 64,
                        "--result-anchor",
                        str(root / "unused-anchor.json"),
                        "--expected-result-anchor-sha256",
                        "1" * 64,
                        "--validation-report",
                        str(root / "unused-report.json"),
                        "--expected-validation-report-sha256",
                        "2" * 64,
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_bytes(), sentinel)
            decoded = json.loads(stream.getvalue())
            self.assertEqual(decoded["validation_status"], "FAIL")
            self.assertIsNone(decoded["aggregate"])

    def test_cli_broken_output_symlink_is_rejected_without_creating_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.json"
            output = root / "completion-link.json"
            try:
                os.symlink(victim, output)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            self.assertTrue(os.path.lexists(output))
            self.assertFalse(output.exists())
            stream = io.StringIO()
            with redirect_stdout(stream), working_directory(root):
                exit_code = completion.main(
                    [
                        "--master-inclusion-contract",
                        str(root / "unused-master.json"),
                        "--expected-master-sha256",
                        "0" * 64,
                        "--result-anchor",
                        str(root / "unused-anchor.json"),
                        "--expected-result-anchor-sha256",
                        "1" * 64,
                        "--validation-report",
                        str(root / "unused-report.json"),
                        "--expected-validation-report-sha256",
                        "2" * 64,
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertTrue(os.path.lexists(output))
            self.assertTrue(output.is_symlink())
            self.assertFalse(victim.exists())
            decoded = json.loads(stream.getvalue())
            self.assertEqual(decoded["validation_status"], "FAIL")
            self.assertIsNone(decoded["aggregate"])

    def test_cli_symlinked_output_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            try:
                os.symlink(real_parent, linked_parent, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlink creation unavailable: {error}")
            output = linked_parent / "completion.json"
            stream = io.StringIO()
            with redirect_stdout(stream), working_directory(root):
                exit_code = completion.main(
                    [
                        "--master-inclusion-contract",
                        str(root / "unused-master.json"),
                        "--expected-master-sha256",
                        "0" * 64,
                        "--result-anchor",
                        str(root / "unused-anchor.json"),
                        "--expected-result-anchor-sha256",
                        "1" * 64,
                        "--validation-report",
                        str(root / "unused-report.json"),
                        "--expected-validation-report-sha256",
                        "2" * 64,
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertFalse((real_parent / "completion.json").exists())
            decoded = json.loads(stream.getvalue())
            self.assertEqual(decoded["validation_status"], "FAIL")
            self.assertIsNone(decoded["aggregate"])

    def test_cli_rejects_non_cwd_and_nested_output_paths_without_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            cases = (
                outside / "completion.json",
                Path("nested") / "completion.json",
            )
            for output in cases:
                with self.subTest(output=str(output)):
                    stream = io.StringIO()
                    with redirect_stdout(stream), working_directory(root), mock.patch.object(
                        completion.os, "open", side_effect=AssertionError("open called")
                    ):
                        exit_code = completion.main(
                            [
                                "--master-inclusion-contract",
                                str(root / "unused-master.json"),
                                "--expected-master-sha256",
                                "0" * 64,
                                "--result-anchor",
                                str(root / "unused-anchor.json"),
                                "--expected-result-anchor-sha256",
                                "1" * 64,
                                "--validation-report",
                                str(root / "unused-report.json"),
                                "--expected-validation-report-sha256",
                                "2" * 64,
                                "--output",
                                str(output),
                            ]
                        )
                    self.assertEqual(exit_code, 1)
                    self.assertFalse((outside / "completion.json").exists())
                    self.assertFalse((root / "nested" / "completion.json").exists())
                    decoded = json.loads(stream.getvalue())
                    self.assertEqual(decoded["validation_status"], "FAIL")
                    self.assertIsNone(decoded["aggregate"])


if __name__ == "__main__":
    unittest.main()
