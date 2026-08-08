from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from r13_matched_panels_generate_r1 import (
    ARMS,
    STACKS,
    canonical_json_bytes,
    generate,
    strict_json_loads,
)
from r13_matched_panels_validate_r1 import (
    MACHINE_RECEIPT_NAME,
    MANIFEST_NAME,
    PANEL_NAME,
    TEMPLATE_NAME,
    ValidationError,
    validate_bundle,
    write_machine_receipt,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    return strict_json_loads(path.read_bytes())


def write_pretty_json(path: Path, value: dict) -> None:
    path.write_bytes(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        ).encode("ascii")
        + b"\n"
    )


def read_panel_rows(bundle: Path) -> list[dict]:
    return [strict_json_loads(line) for line in (bundle / PANEL_NAME).read_bytes().splitlines()]


def write_panel_rows(bundle: Path, rows: list[dict]) -> None:
    (bundle / PANEL_NAME).write_bytes(
        b"".join(canonical_json_bytes(row, newline=True) for row in rows)
    )


def rebind_bundle(bundle: Path) -> None:
    manifest_path = bundle / MANIFEST_NAME
    template_path = bundle / TEMPLATE_NAME
    panel_path = bundle / PANEL_NAME
    manifest = read_json(manifest_path)
    panel_bytes = panel_path.read_bytes()
    manifest["panel_jsonl"] = {
        "relpath": PANEL_NAME,
        "byte_length": len(panel_bytes),
        "sha256": sha256_bytes(panel_bytes),
    }
    inventory = manifest["raw_file_inventory"]
    for entry in inventory:
        data = bundle.joinpath(*entry["relpath"].split("/")).read_bytes()
        entry["byte_length"] = len(data)
        entry["sha256"] = sha256_bytes(data)
    manifest["raw_inventory_commitment_sha256"] = sha256_bytes(
        canonical_json_bytes(inventory)
    )
    write_pretty_json(manifest_path, manifest)

    template = read_json(template_path)
    template["asset_bindings"] = {
        "manifest_relpath": MANIFEST_NAME,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "panel_jsonl_relpath": PANEL_NAME,
        "panel_jsonl_sha256": sha256_bytes(panel_bytes),
        "raw_inventory_commitment_sha256": manifest[
            "raw_inventory_commitment_sha256"
        ],
    }
    write_pretty_json(template_path, template)


def mutate_raw_row(bundle: Path, stack: str, arm: str, z: int, mutation) -> None:
    rows = read_panel_rows(bundle)
    wrapper = next(
        row
        for row in rows
        if row["mapping_stack_id"] == stack
        and row["arm"] == arm
        and row["canonical_z"] == z
    )
    row_path = bundle.joinpath(*wrapper["generated"]["row_relpath"].split("/"))
    raw_row = strict_json_loads(row_path.read_bytes())
    mutation(raw_row)
    row_bytes = canonical_json_bytes(raw_row, newline=True)
    row_path.write_bytes(row_bytes)
    wrapper["generated"]["row_sha256"] = sha256_bytes(row_bytes)
    write_panel_rows(bundle, rows)
    rebind_bundle(bundle)


def mutate_prompt(bundle: Path, stack: str, arm: str, z: int, mutation) -> None:
    rows = read_panel_rows(bundle)
    wrapper = next(
        row
        for row in rows
        if row["mapping_stack_id"] == stack
        and row["arm"] == arm
        and row["canonical_z"] == z
    )
    prompt_path = bundle.joinpath(*wrapper["generated"]["prompt_relpath"].split("/"))
    prompt = mutation(prompt_path.read_bytes())
    prompt_path.write_bytes(prompt)
    wrapper["generated"]["prompt_sha256"] = sha256_bytes(prompt)
    row_path = bundle.joinpath(*wrapper["generated"]["row_relpath"].split("/"))
    raw_row = strict_json_loads(row_path.read_bytes())
    raw_row["prompt_text"] = prompt.decode("ascii")
    row_bytes = canonical_json_bytes(raw_row, newline=True)
    row_path.write_bytes(row_bytes)
    wrapper["generated"]["row_sha256"] = sha256_bytes(row_bytes)
    write_panel_rows(bundle, rows)
    rebind_bundle(bundle)


class R13MatchedPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory(prefix="r13-matched-panels-tests-")
        cls.root = Path(cls._temp.name)
        cls.base = cls.root / "base"
        cls.generation = generate(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def copy_bundle(self, name: str) -> Path:
        destination = self.root / name
        shutil.copytree(self.base, destination)
        return destination

    def test_positive_bundle_closes_exact_counts_and_is_fail_closed(self) -> None:
        report = validate_bundle(self.base)
        self.assertEqual(report["verdict"], "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY")
        self.assertEqual(report["validated_pairs"], 28)
        self.assertEqual(report["validated_rows"], 56)
        self.assertEqual(report["validated_raw_files"], 112)
        self.assertFalse(report["run_eligible"])
        self.assertFalse(report["model_execution_authorized"])
        self.assertFalse(report["model_execution_performed"])
        self.assertEqual(report["model_actions"], 0)
        self.assertFalse(report["scientific_evidence"])
        self.assertFalse(report["human_review_completed"])

    def test_generation_is_deterministic(self) -> None:
        second = self.root / "deterministic-second"
        generated = generate(second)
        for field in (
            "panel_jsonl_sha256",
            "manifest_sha256",
            "receipt_template_sha256",
            "raw_inventory_commitment_sha256",
        ):
            self.assertEqual(generated[field], self.generation[field])
        first_manifest = read_json(self.base / MANIFEST_NAME)
        second_manifest = read_json(second / MANIFEST_NAME)
        self.assertEqual(first_manifest, second_manifest)

    def test_every_h0_raw_file_is_exact_original_reuse(self) -> None:
        rows = read_panel_rows(self.base)
        h0_rows = [row for row in rows if row["arm"] == ARMS[0]]
        self.assertEqual(len(h0_rows), 28)
        source_root = (
            Path(__file__).resolve().parent.parent
            / "real_assets"
            / "build_v5_repair_a"
        )
        for wrapper in h0_rows:
            generated = wrapper["generated"]
            source = wrapper["source_original"]
            prompt = self.base.joinpath(*generated["prompt_relpath"].split("/")).read_bytes()
            source_prompt = source_root.joinpath(*source["prompt_relpath"].split("/")).read_bytes()
            row = self.base.joinpath(*generated["row_relpath"].split("/")).read_bytes()
            source_row = source_root.joinpath(*source["row_relpath"].split("/")).read_bytes()
            self.assertEqual(prompt, source_prompt)
            self.assertEqual(row, source_row)

    def test_q_tables_are_distinct_for_every_stack_and_r(self) -> None:
        rows = read_panel_rows(self.base)
        for stack in STACKS:
            by_arm = {
                arm: next(
                    row
                    for row in rows
                    if row["mapping_stack_id"] == stack
                    and row["arm"] == arm
                    and row["canonical_z"] == 0
                )
                for arm in ARMS
            }
            for r in range(1, 6):
                q0 = by_arm[ARMS[0]]["q_surface_by_r"][str(r)]
                q1 = by_arm[ARMS[1]]["q_surface_by_r"][str(r)]
                self.assertEqual(len({r, q0, q1}), 3)

    def test_machine_receipt_is_separate_and_not_human_review(self) -> None:
        bundle = self.copy_bundle("machine-receipt")
        report = validate_bundle(bundle)
        path = write_machine_receipt(bundle, report)
        self.assertEqual(path.name, MACHINE_RECEIPT_NAME)
        receipt = read_json(path)
        self.assertEqual(receipt["verdict"], "PASS_STATIC_MATCHED_PANEL_ALLOWLIST_ONLY")
        self.assertFalse(receipt["human_review_completed"])
        self.assertFalse(receipt["model_execution_authorized"])
        self.assertEqual(validate_bundle(bundle), report)

    def test_tampered_machine_receipt_fails_recomputation(self) -> None:
        bundle = self.copy_bundle("machine-receipt-tamper")
        report = validate_bundle(bundle)
        path = write_machine_receipt(bundle, report)
        receipt = read_json(path)
        receipt["validated_pairs"] = 27
        write_pretty_json(path, receipt)
        with self.assertRaisesRegex(ValidationError, "does not match recomputed report"):
            validate_bundle(bundle)

    def test_duplicate_panel_key_fails_after_hashes_are_rebound(self) -> None:
        bundle = self.copy_bundle("duplicate")
        rows = read_panel_rows(bundle)
        rows[1] = copy.deepcopy(rows[0])
        write_panel_rows(bundle, rows)
        rebind_bundle(bundle)
        with self.assertRaisesRegex(ValidationError, "duplicate panel key"):
            validate_bundle(bundle)

    def test_reordered_panel_rows_fail_after_hashes_are_rebound(self) -> None:
        bundle = self.copy_bundle("reordered")
        rows = read_panel_rows(bundle)
        rows[0], rows[1] = rows[1], rows[0]
        write_panel_rows(bundle, rows)
        rebind_bundle(bundle)
        with self.assertRaisesRegex(ValidationError, "order mismatch"):
            validate_bundle(bundle)

    def test_non_allowlisted_raw_row_field_fails_after_full_rebinding(self) -> None:
        bundle = self.copy_bundle("nonallowlisted")
        mutate_raw_row(
            bundle,
            STACKS[0],
            ARMS[1],
            0,
            lambda row: row.__setitem__("task_id", "TAMPERED_TASK"),
        )
        with self.assertRaisesRegex(ValidationError, "non-allowlisted raw-row differences"):
            validate_bundle(bundle)

    def test_duplicate_candidate_fails_after_full_rebinding(self) -> None:
        bundle = self.copy_bundle("candidate-duplicate")

        def duplicate_candidate(row: dict) -> None:
            row["candidate_order"][0] = row["candidate_order"][1]

        mutate_raw_row(bundle, STACKS[0], ARMS[1], 0, duplicate_candidate)
        with self.assertRaisesRegex(ValidationError, "candidate_order"):
            validate_bundle(bundle)

    def test_non_codebook_prompt_edit_fails_after_full_rebinding(self) -> None:
        bundle = self.copy_bundle("prompt-tamper")

        def tamper(prompt: bytes) -> bytes:
            return prompt.replace(b"ANSWER_FORMAT=", b"ANSWER_FORMAT_TAMPERED=", 1)

        mutate_prompt(bundle, STACKS[0], ARMS[1], 0, tamper)
        with self.assertRaisesRegex(ValidationError, "non-allowlisted prompt line"):
            validate_bundle(bundle)

    def test_h0_byte_edit_fails_even_after_full_rebinding(self) -> None:
        bundle = self.copy_bundle("h0-tamper")

        def tamper(prompt: bytes) -> bytes:
            return prompt.replace(b"ANSWER_FORMAT=", b"ANSWER_FORMAT_TAMPERED=", 1)

        mutate_prompt(bundle, STACKS[0], ARMS[0], 0, tamper)
        with self.assertRaisesRegex(ValidationError, "H0 is not byte-exact source reuse"):
            validate_bundle(bundle)

    def test_q_table_tamper_fails_after_panel_rebinding(self) -> None:
        bundle = self.copy_bundle("q-tamper")
        rows = read_panel_rows(bundle)
        rows[0]["q_surface_by_r"]["1"] = 6
        write_panel_rows(bundle, rows)
        rebind_bundle(bundle)
        with self.assertRaisesRegex(ValidationError, "q table mismatch"):
            validate_bundle(bundle)

    def test_duplicate_json_key_fails_closed(self) -> None:
        bundle = self.copy_bundle("duplicate-json-key")
        path = bundle / MANIFEST_NAME
        data = path.read_bytes()
        path.write_bytes(data.replace(b"{\n", b'{\n  "run_eligible": false,\n', 1))
        with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
            validate_bundle(bundle)

    def test_nonfinite_json_constant_fails_closed(self) -> None:
        bundle = self.copy_bundle("nonfinite-json")
        path = bundle / MANIFEST_NAME
        data = path.read_bytes()
        path.write_bytes(data.replace(b'"run_eligible": false', b'"run_eligible": NaN', 1))
        with self.assertRaisesRegex(ValidationError, "non-finite JSON constant"):
            validate_bundle(bundle)

    def test_human_review_template_cannot_contain_fake_signature(self) -> None:
        bundle = self.copy_bundle("fake-signature")
        template = read_json(bundle / TEMPLATE_NAME)
        template["signature"] = "not-a-real-signature"
        write_pretty_json(bundle / TEMPLATE_NAME, template)
        with self.assertRaisesRegex(ValidationError, "forged review/signature"):
            validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
