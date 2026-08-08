"""Regression tests for the independent native controlled-asset validator."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REAL_ASSETS = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    REAL_ASSETS / "scripts" / "validate_native_controlled_assets.py"
)
BUILDER_PATH = REAL_ASSETS / "scripts" / "build_controlled_assets.py"

SPEC = importlib.util.spec_from_file_location(
    "validate_native_controlled_assets", VALIDATOR_PATH
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def rewrite_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("wb") as handle:
        for row in rows:
            handle.write(validator.canonical_json_bytes(row))


class NativeControlledAssetValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_temp.name) / "fixture"
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILDER_PATH),
                "--output-dir",
                str(cls.fixture),
            ],
            cwd=REAL_ASSETS,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "could not create native test fixture:\n"
                + completed.stdout
                + completed.stderr
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_temp.cleanup()

    def make_copy(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "assets"
        shutil.copytree(self.fixture, root)
        return temporary, root

    def assert_fail_closed(
        self, report: dict, expected_code: str | None = None
    ) -> None:
        self.assertFalse(report["valid"])
        self.assertGreater(report["error_count"], 0)
        codes = {error["code"] for error in report["errors"]}
        self.assertNotIn("internal_validator_error", codes)
        if expected_code is not None:
            self.assertIn(expected_code, codes)

    def test_fresh_repaired_fixture_passes(self) -> None:
        report = validator.validate_native_controlled_assets(self.fixture)
        self.assertTrue(report["valid"], report["errors"][:5])
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["counts"]["prompt_rows_total"], 280)
        self.assertEqual(report["counts"]["candidate_records"], 1960)
        self.assertEqual(report["counts"]["raw_source_files"], 224)
        self.assertEqual(report["counts"]["raw_target_files"], 336)
        self.assertFalse(report["model_execution_performed"])

    def test_prompt_text_solver_matches_all_model_visible_rows(self) -> None:
        rows: list[dict] = []
        for key in ("source", "calibration", "audit"):
            path = self.fixture / validator.FILES[key]
            records = [json.loads(line) for line in path.read_text().splitlines()]
            if key == "source":
                rows.extend(
                    row for bundle in records for row in bundle["rows"]
                )
            else:
                rows.extend(records)
        self.assertEqual(len(rows), 280)
        for row in rows:
            self.assertEqual(
                validator.solve_prompt_text(row["prompt_text"]),
                row["canonical_z"],
                row["row_id"],
            )

    def test_old_rank_rendering_is_rejected_by_visible_prompt_solver(self) -> None:
        old_prompt = (
            "TASK=MARKED_RANK7_V1\n"
            "CANONICAL_CLASS=z in {0,1,2,3,4,5,6}\n"
            "CODEBOOK=z0->K0;z1->K1;z2->K2;z3->K3;z4->K4;z5->K5;z6->K6\n"
            "CANDIDATES=FINAL=K0|FINAL=K1|FINAL=K2|FINAL=K3|FINAL=K4|FINAL=K5|FINAL=K6\n"
            "RULE=z is the number of listed values strictly less than the value marked *.\n"
            "ITEMS=-19,-67,-85,-42,*14,-78,-98\n"
            "ANSWER_FORMAT=Return exactly one candidate and no other text.\n"
        )
        with self.assertRaises(validator.OracleError):
            validator.solve_prompt_text(old_prompt)

    def test_task_input_tamper_fails_oracle_and_bytes(self) -> None:
        temporary, root = self.make_copy()
        try:
            path = root / validator.FILES["source"]
            bundles = [
                json.loads(line) for line in path.read_text().splitlines()
            ]
            row = bundles[0]["rows"][0]
            row["task_input"]["a"] = (row["task_input"]["a"] + 1) % 21
            rewrite_jsonl(path, bundles)
            report = validator.validate_native_controlled_assets(root)
            self.assert_fail_closed(
                report, "deterministic_instance_mismatch"
            )
        finally:
            temporary.cleanup()

    def test_raw_prompt_byte_tamper_fails_exact_bytes(self) -> None:
        temporary, root = self.make_copy()
        try:
            source_path = root / validator.FILES["source"]
            first_bundle = json.loads(
                source_path.read_text().splitlines()[0]
            )
            relpath = first_bundle["rows"][0]["prompt_bytes"]["raw_relpath"]
            prompt_path = root.joinpath(*relpath.split("/"))
            prompt_path.write_bytes(prompt_path.read_bytes() + b"X")
            report = validator.validate_native_controlled_assets(root)
            self.assert_fail_closed(
                report, "raw_prompt_exact_bytes_mismatch"
            )
        finally:
            temporary.cleanup()

    def test_reward_vector_tamper_fails_cross_checks(self) -> None:
        temporary, root = self.make_copy()
        try:
            path = root / validator.FILES["verifier"]
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[0]["arms"][1]["reward_vector"][0] ^= 1
            rewrite_jsonl(path, rows)
            report = validator.validate_native_controlled_assets(root)
            self.assert_fail_closed(report, "verifier_reward_mismatch")
        finally:
            temporary.cleanup()

    def test_audit_exact_byte_tamper_breaks_seal(self) -> None:
        temporary, root = self.make_copy()
        try:
            path = root / validator.FILES["audit"]
            raw = path.read_bytes()
            newline = raw.find(b"\n")
            self.assertGreater(newline, 0)
            path.write_bytes(raw[:newline] + b" " + raw[newline:])
            report = validator.validate_native_controlled_assets(root)
            self.assert_fail_closed(report, "audit_file_hash_mismatch")
        finally:
            temporary.cleanup()

    def test_malformed_json_is_invalid_without_internal_crash(self) -> None:
        temporary, root = self.make_copy()
        try:
            path = root / validator.FILES["mapping"]
            path.write_bytes(b"{not-json}\n")
            report = validator.validate_native_controlled_assets(root)
            self.assert_fail_closed(report, "invalid_jsonl_record")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
