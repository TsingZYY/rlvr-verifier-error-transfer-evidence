"""Regression tests for the isolated CPU-only counterbalance pilot."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


REAL_ASSETS = Path(__file__).resolve().parents[1]
PILOT_PATH = (
    REAL_ASSETS / "scripts" / "build_latin_square_counterbalance_pilot.py"
)
BASELINE_ROOT = REAL_ASSETS / "build_v4_a"

SPEC = importlib.util.spec_from_file_location(
    "build_latin_square_counterbalance_pilot", PILOT_PATH
)
assert SPEC is not None and SPEC.loader is not None
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def rewrite_artifact_binding(output: Path, filename: str) -> None:
    manifest_path = output / pilot.OUTPUT_FILES["manifest"]
    manifest = json.loads(manifest_path.read_bytes())
    artifact_path = output / filename
    manifest["artifact_hashes"][filename] = {
        "sha256": pilot.sha256_file(artifact_path),
        "byte_length": artifact_path.stat().st_size,
    }
    pilot.write_json(manifest_path, manifest)


class LatinSquareCounterbalancePilotTests(unittest.TestCase):
    def test_frozen_cyclic_latin_square_balances_every_role(self) -> None:
        blocks = pilot.latin_square_blocks()
        self.assertEqual(len(blocks), 5)
        expected = set(pilot.RULE_OFFSETS)
        self.assertEqual(
            {row["shared_offset_mod7"] for row in blocks},
            expected,
        )
        for task_id in pilot.TASK_ORDER:
            self.assertEqual(
                {row["local_offsets_mod7"][task_id] for row in blocks},
                expected,
            )
            self.assertTrue(
                all(
                    row["shared_offset_mod7"]
                    != row["local_offsets_mod7"][task_id]
                    for row in blocks
                )
            )

    def test_build_passes_static_geometry_but_remains_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            result = pilot.build_pilot(BASELINE_ROOT, output)
            self.assertTrue(result["valid"])
            self.assertEqual(result["base_row_count"], 280)
            self.assertEqual(result["derived_row_count"], 1400)
            self.assertEqual(result["geometry_record_count"], 33)
            self.assertEqual(result["failed_geometry_record_count"], 0)
            self.assertGreater(
                result["baseline_failed_geometry_record_count"], 0
            )
            self.assertEqual(
                result["cpu_design_gate_result"],
                "PASS_COUNTERBALANCE_FEASIBILITY_ONLY",
            )
            self.assertEqual(result["experiment_status"], "NOT_RUN")
            self.assertFalse(result["scientific_evidence"])
            self.assertFalse(result["run_eligible"])
            self.assertFalse(result["model_execution_performed"])

            geometry = json.loads(
                (
                    output
                    / pilot.OUTPUT_FILES["geometry"]
                ).read_bytes()
            )
            self.assertTrue(
                geometry["per_base_row_rule_identity_counterbalance_pass"]
            )
            self.assertTrue(geometry["rule_identity_role_matrix_pass"])
            self.assertTrue(
                geometry["all_required_static_geometry_checks_pass"]
            )
            self.assertEqual(
                geometry["failed_geometry_record_count"], 0
            )
            self.assertTrue(
                all(
                    row["candidate_aligned_vector_counter_equal"]
                    and row["gold_wrong_pair_counter_equal"]
                    for row in geometry["geometry_records"]
                )
            )

    def test_two_independent_builds_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            pilot.build_pilot(BASELINE_ROOT, first)
            pilot.build_pilot(BASELINE_ROOT, second)
            self.assertEqual(tree_bytes(first), tree_bytes(second))

    def test_tampered_derived_rows_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            pilot.build_pilot(BASELINE_ROOT, output)
            derived_path = output / pilot.OUTPUT_FILES["derived"]
            lines = derived_path.read_bytes().splitlines()
            first = json.loads(lines[0])
            first["local_reward_vector"][0] ^= 1
            lines[0] = pilot.canonical_json_bytes(first).rstrip(b"\n")
            derived_path.write_bytes(b"\n".join(lines) + b"\n")
            with self.assertRaises(pilot.PilotError):
                pilot.validate_output(output, BASELINE_ROOT)

    def test_tampered_reward_vector_fails_after_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            pilot.build_pilot(BASELINE_ROOT, output)
            derived_path = output / pilot.OUTPUT_FILES["derived"]
            rows = pilot.read_jsonl(derived_path)
            rows[0]["shared_reward_vector"] = rows[0]["local_reward_vector"]
            pilot.write_jsonl(derived_path, rows)
            rewrite_artifact_binding(output, pilot.OUTPUT_FILES["derived"])
            with self.assertRaisesRegex(
                pilot.PilotError, "candidate-bound|provenance"
            ):
                pilot.validate_output(output, BASELINE_ROOT)

    def test_empty_manifest_artifact_inventory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            pilot.build_pilot(BASELINE_ROOT, output)
            manifest_path = output / pilot.OUTPUT_FILES["manifest"]
            manifest = json.loads(manifest_path.read_bytes())
            manifest["artifact_hashes"] = {}
            pilot.write_json(manifest_path, manifest)
            with self.assertRaisesRegex(pilot.PilotError, "inventory"):
                pilot.validate_output(output, BASELINE_ROOT)

    def test_tampered_rule_identity_fails_after_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            pilot.build_pilot(BASELINE_ROOT, output)
            assignment_path = output / pilot.OUTPUT_FILES["assignment"]
            assignment = json.loads(assignment_path.read_bytes())
            assignment["rule_blocks"][0]["shared_rule_identity_id"] = (
                "Z7_PLUS5"
            )
            pilot.write_json(assignment_path, assignment)
            rewrite_artifact_binding(output, pilot.OUTPUT_FILES["assignment"])
            with self.assertRaisesRegex(pilot.PilotError, "frozen assignment"):
                pilot.validate_output(output, BASELINE_ROOT)

    def test_missing_split_scope_cannot_pass_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            pilot.build_pilot(BASELINE_ROOT, output)
            rows = pilot.read_jsonl(output / pilot.OUTPUT_FILES["derived"])
            for row in rows:
                row["split_role"] = "SOURCE"
            with self.assertRaisesRegex(pilot.PilotError, "split coverage"):
                pilot.build_geometry_audit(rows, pilot.latin_square_blocks())

    def test_task_pair_relabeling_in_input_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root = Path(temporary) / "input"
            input_root.mkdir()
            for filename in pilot.INPUT_FILES.values():
                shutil.copyfile(BASELINE_ROOT / filename, input_root / filename)
            source_path = input_root / pilot.INPUT_FILES["source"]
            bundles = pilot.read_jsonl(source_path)
            bundles[0]["task_pair_id"] = "FAKE_PAIR"
            for row in bundles[0]["rows"]:
                row["task_pair_id"] = "FAKE_PAIR"
            pilot.write_jsonl(source_path, bundles)
            with self.assertRaisesRegex(pilot.PilotError, "frozen task pairs"):
                pilot.load_base_rows(input_root)

    def test_self_declared_baseline_wrong_candidate_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root = Path(temporary) / "input"
            input_root.mkdir()
            for filename in pilot.INPUT_FILES.values():
                shutil.copyfile(BASELINE_ROOT / filename, input_root / filename)
            source_path = input_root / pilot.INPUT_FILES["source"]
            bundles = pilot.read_jsonl(source_path)
            first = bundles[0]["rows"][0]
            first["shared_bug_candidate"] = first["gold_candidate"]
            pilot.write_jsonl(source_path, bundles)
            with self.assertRaisesRegex(pilot.PilotError, "not derivable"):
                pilot.load_base_rows(input_root)

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            output.mkdir()
            marker = output / "user-file.txt"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaises(pilot.PilotError):
                pilot.build_pilot(BASELINE_ROOT, output)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
