"""Tests for the fail-closed controlled-assets validator."""

from __future__ import annotations

import importlib.util
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, Callable


VALIDATOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_controlled_assets.py"
)
SPEC = importlib.util.spec_from_file_location(
    "controlled_asset_validator", VALIDATOR_PATH
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _make_protocol() -> dict[str, Any]:
    tasks = [
        {
            "task_id": "MOD7_SUM_V1",
            "task_pair_id": "task_pair_1",
            "task_role": "A",
            "latent_answer_formula": "z=(a+b) mod 7",
            "local_shift_mod7": 2,
        },
        {
            "task_id": "DFA7_FINAL_V1",
            "task_pair_id": "task_pair_1",
            "task_role": "B",
            "latent_answer_formula": (
                "z=encoded_final_state_after_full_input"
            ),
            "local_shift_mod7": 3,
        },
        {
            "task_id": "MARKED_RANK7_V1",
            "task_pair_id": "task_pair_2",
            "task_role": "A",
            "latent_answer_formula": "z=zero_based_rank_of_marked_item",
            "local_shift_mod7": 4,
        },
        {
            "task_id": "PAREN_MAX_DEPTH7_V1",
            "task_pair_id": "task_pair_2",
            "task_role": "B",
            "latent_answer_formula": (
                "z=maximum_nesting_depth-1, with "
                "maximum_nesting_depth in 1..7"
            ),
            "local_shift_mod7": 5,
        },
    ]
    denied = {
        key: False
        for key in (
            "gpu",
            "model_weight_load",
            "tokenizer_load",
            "model_inference",
            "model_generation",
            "model_forward",
            "backward",
            "gradient",
            "optimizer",
            "optimizer_step",
            "parameter_update",
            "training",
            "rl",
            "rlvr",
            "one_stack_methodology_pilot",
            "eight_stack_screen",
            "scientific_go_stop_decision",
        )
    }
    return {
        "schema_version": "p4-r1-real-protocol-v2-pre-review",
        "protocol_status": "PRE_REVIEW",
        "experiment_status": "NOT_RUN",
        "scientific_evidence": False,
        "run_eligible": False,
        "human_review_status": "PENDING_HUMAN_REVIEW",
        "tasks": tasks,
        "task_pairs": [
            {"task_pair_id": "task_pair_1"},
            {"task_pair_id": "task_pair_2"},
        ],
        "mappings": [
            {"mapping_id": "mapping_0"},
            {"mapping_id": "mapping_1"},
        ],
        "mapping_stacks": [
            {"stack_id": stack_id, "status": "NOT_RUN"}
            for stack_id in validator.EXPECTED_STACK_IDS
        ],
        "authorization": denied,
    }


def _make_mappings() -> list[dict[str, Any]]:
    rows = []
    for stack_id in validator.EXPECTED_STACK_IDS:
        meta = validator.EXPECTED_STACK_META[stack_id]
        codebook = validator.EXPECTED_CODEBOOKS[meta["mapping_id"]]
        rows.append(
            {
                "stack_id": stack_id,
                "pair_id": meta["pair_id"],
                "mapping_id": meta["mapping_id"],
                "mirror_id": meta["direction"],
                "direction": meta["direction"],
                "source_task_id": meta["source_task_id"],
                "target_task_id": meta["target_task_id"],
                "codebook": {
                    "A": [f"K{value}" for value in codebook["A"]],
                    "B": [f"K{value}" for value in codebook["B"]],
                },
            }
        )
    return rows


def _dfa_result(start: int, sequence: str) -> int:
    state = start
    for symbol in sequence:
        state = validator.DFA7_TRANSITIONS[symbol][state]
    return state


def _task_input(
    task_id: str,
    z: int,
    occurrence: int,
) -> dict[str, Any]:
    if task_id == "MOD7_SUM_V1":
        matches = [
            (a, b)
            for a in range(21)
            for b in range(21)
            if (a + b) % 7 == z
        ]
        a, b = matches[occurrence]
        return {"a": a, "b": b}

    if task_id == "DFA7_FINAL_V1":
        matches = []
        for length in range(2, 7):
            for symbols in itertools.product("xy", repeat=length):
                sequence = "".join(symbols)
                for start in range(7):
                    if _dfa_result(start, sequence) == z:
                        matches.append((start, sequence))
        start, sequence = matches[occurrence]
        return {
            "start_state": start,
            "sequence": sequence,
            "transition_table": {
                symbol: list(row)
                for symbol, row in validator.DFA7_TRANSITIONS.items()
            },
        }

    if task_id == "MARKED_RANK7_V1":
        base = 100 * occurrence
        values = [base + offset for offset in (4, 1, 6, 0, 5, 2, 3)]
        marked_value = base + z
        return {
            "values": values,
            "marked_index": values.index(marked_value),
        }

    if task_id == "PAREN_MAX_DEPTH7_V1":
        depth = z + 1
        return {
            "parentheses": "()" * (occurrence + 1) + "(" * depth + ")" * depth
        }

    raise AssertionError(task_id)


def _make_rows(
    mappings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    task_occurrences: Counter[tuple[str, int]] = Counter()
    seed = 1
    by_split: dict[str, list[dict[str, Any]]] = {
        "source": [],
        "target_calibration": [],
        "target_audit": [],
    }
    candidates = list(validator.CANDIDATES)
    candidate_hashes = [
        validator.sha256_bytes(value.encode("utf-8"))
        for value in candidates
    ]
    mapping_by_stack = {row["stack_id"]: row for row in mappings}

    for split, (_, _per_stack, per_class) in validator.ROW_SPLITS.items():
        for stack_id in validator.EXPECTED_STACK_IDS:
            stack = mapping_by_stack[stack_id]
            meta = validator.EXPECTED_STACK_META[stack_id]
            task_id = (
                meta["source_task_id"]
                if split == "source"
                else meta["target_task_id"]
            )
            role = "A" if task_id == meta["task_a"] else "B"
            codebook = validator.EXPECTED_CODEBOOKS[meta["mapping_id"]][role]
            for z in range(7):
                for repeat in range(per_class):
                    occurrence = task_occurrences[(task_id, z)]
                    task_occurrences[(task_id, z)] += 1
                    task_input = _task_input(task_id, z, occurrence)
                    row_id = (
                        f"{stack_id}:{split}:z{z}:repeat{repeat}"
                    )
                    prompt = (
                        f"TASK={task_id}\nROW={row_id}\nINPUT="
                        + json.dumps(
                            task_input,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\nCANDIDATES="
                        + "|".join(candidates)
                        + "\n"
                    )
                    gold_index = codebook[z]
                    shared_index = codebook[(z + 1) % 7]
                    local_shift = validator.LOCAL_SHIFT_BY_TASK[task_id]
                    local_index = codebook[(z + local_shift) % 7]
                    clean = [0] * 7
                    clean[gold_index] = 1
                    shared = clean.copy()
                    shared[shared_index] = 1
                    local = clean.copy()
                    local[local_index] = 1
                    row = {
                        "stack_id": stack_id,
                        "pair_id": meta["pair_id"],
                        "mapping_id": meta["mapping_id"],
                        "direction": meta["direction"],
                        "task_id": task_id,
                        "split": split,
                        "row_id": row_id,
                        "seed_namespace": f"{stack_id}:{split}",
                        "row_seed": seed,
                        "generator_id": f"controlled_generator:{task_id}",
                        "oracle_id": f"independent_oracle:{task_id}",
                        "task_input": task_input,
                        "prompt": prompt,
                        "prompt_sha256": validator.sha256_bytes(
                            prompt.encode("utf-8")
                        ),
                        "canonical_class": z,
                        "candidates": candidates,
                        "candidate_sha256s": candidate_hashes,
                        "gold_index": gold_index,
                        "shared_wrong_index": shared_index,
                        "local_wrong_index": local_index,
                        "reward_vectors": {
                            "clean": clean,
                            "shared_leaky": shared,
                            "local_leaky": local,
                            "shared_frozen_probe_control": shared,
                            "local_frozen_probe_control": local,
                        },
                        "lineage_id": f"lineage:{row_id}",
                        "model_dependent_status": "NOT_RUN",
                        "human_review_status": "PENDING_HUMAN_REVIEW",
                    }
                    row["record_sha256"] = validator.record_sha256(row)
                    by_split[split].append(row)
                    seed += 1
    return by_split


def _make_manifest(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        for candidate_index, candidate_text in enumerate(row["candidates"]):
            result.append(
                {
                    "row_id": row["row_id"],
                    "stack_id": row["stack_id"],
                    "split": row["split"],
                    "candidate_index": candidate_index,
                    "candidate_text": candidate_text,
                    "candidate_sha256": row["candidate_sha256s"][
                        candidate_index
                    ],
                    "reward_by_arm": {
                        arm: row["reward_vectors"][arm][candidate_index]
                        for arm in validator.ARMS
                    },
                    "model_dependent_status": "NOT_RUN",
                }
            )
    return result


def _make_seal(root: Path, audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    record_hashes = [row["record_sha256"] for row in audit_rows]
    genesis = "0" * 64
    previous = genesis
    chain = []
    for row_hash in record_hashes:
        previous = validator.sha256_bytes(
            (previous + row_hash).encode("ascii")
        )
        chain.append(previous)
    return {
        "schema_version": "target-audit-seal-receipt-v1",
        "seal_status": "SEALED_PRE_MODEL_ACTION",
        "audit_filename": validator.AUDIT_FILE,
        "audit_file_sha256": validator.sha256_bytes(
            (root / validator.AUDIT_FILE).read_bytes()
        ),
        "audit_record_count": len(audit_rows),
        "hash_algorithm": "sha256",
        "chain_formula": (
            "sha256(previous_chain_sha256 || record_sha256)"
        ),
        "hash_chain_genesis": genesis,
        "record_hashes": record_hashes,
        "hash_chain": chain,
        "hash_chain_head": chain[-1],
        "sealed_before_model_action": True,
        "audit_used_for_design": False,
        "audit_used_for_matching": False,
        "audit_used_for_tuning": False,
        "audit_used_for_debug": False,
        "human_review_status": "PENDING_HUMAN_REVIEW",
        "model_dependent_status": "NOT_RUN",
        "authorization": {"model_forward": False},
    }


def build_valid_fixture(root: Path) -> None:
    mappings = _make_mappings()
    split_rows = _make_rows(mappings)
    all_rows = (
        split_rows["source"]
        + split_rows["target_calibration"]
        + split_rows["target_audit"]
    )
    _write_json(root / validator.PROTOCOL_FILE, _make_protocol())
    _write_jsonl(root / validator.MAPPING_FILE, mappings)
    _write_jsonl(root / validator.SOURCE_FILE, split_rows["source"])
    _write_jsonl(
        root / validator.CALIBRATION_FILE,
        split_rows["target_calibration"],
    )
    _write_jsonl(root / validator.AUDIT_FILE, split_rows["target_audit"])
    _write_jsonl(root / validator.REWARD_FILE, _make_manifest(all_rows))
    _write_json(
        root / validator.G1_FILE,
        {
            "schema_version": "g1-opportunity-audit-v1",
            "g1_status": "NOT_RUN",
            "model_dependent_status": "NOT_RUN",
            "human_review_status": "PENDING_HUMAN_REVIEW",
            "shared_online_fpr": {"numerator": 1, "denominator": 6},
            "local_online_fpr": "1/6",
            "authorization": {"model_forward": False},
        },
    )
    _write_json(
        root / validator.SEAL_FILE,
        _make_seal(root, split_rows["target_audit"]),
    )
    _write_json(
        root / validator.PREREG_FILE,
        {
            "schema_version": "randomization-model-env-prereg-v1",
            "preregistration_status": "PRE_REVIEW",
            "experiment_status": "NOT_RUN",
            "model_dependent_status": "NOT_RUN",
            "human_review_status": "PENDING_HUMAN_REVIEW",
            "authorization": {
                "gpu": False,
                "model_weight_load": False,
                "model_forward": False,
                "gradient": False,
                "optimizer": False,
            },
        },
    )


def _mutate_first(
    path: Path,
    predicate: Callable[[dict[str, Any]], bool],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    rows = _read_jsonl(path)
    for row in rows:
        if predicate(row):
            mutation(row)
            row["record_sha256"] = validator.record_sha256(row)
            break
    else:
        raise AssertionError("no matching row to mutate")
    _write_jsonl(path, rows)


def _error_codes(report: dict[str, Any]) -> set[str]:
    return {error["code"] for error in report["errors"]}


class ControlledAssetValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        build_valid_fixture(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_valid_fixture_passes_with_exact_totals(self) -> None:
        report = validator.validate_controlled_assets(self.root)
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["counts"]["mapping_stacks"], 8)
        self.assertEqual(report["counts"]["source_rows"], 112)
        self.assertEqual(report["counts"]["target_calibration_rows"], 56)
        self.assertEqual(report["counts"]["target_audit_rows"], 112)
        self.assertEqual(report["counts"]["prompt_rows_total"], 280)
        self.assertEqual(
            report["counts"]["manifest_candidate_records"], 1960
        )
        self.assertFalse(report["model_execution_performed"])

    def test_missing_or_unbalanced_rows_fail_closed(self) -> None:
        rows = _read_jsonl(self.root / validator.SOURCE_FILE)
        rows.pop()
        _write_jsonl(self.root / validator.SOURCE_FILE, rows)
        report = validator.validate_controlled_assets(self.root)
        self.assertFalse(report["valid"])
        self.assertTrue(
            {"row_count", "per_stack_row_count", "class_balance"}
            <= _error_codes(report)
        )

    def test_candidate_order_and_role_distinctness_are_enforced(self) -> None:
        def mutation(row: dict[str, Any]) -> None:
            row["candidates"][0], row["candidates"][1] = (
                row["candidates"][1],
                row["candidates"][0],
            )
            row["local_wrong_index"] = row["shared_wrong_index"]

        _mutate_first(
            self.root / validator.SOURCE_FILE,
            lambda _row: True,
            mutation,
        )
        report = validator.validate_controlled_assets(self.root)
        codes = _error_codes(report)
        self.assertIn("candidate_order_mismatch", codes)
        self.assertIn("candidate_roles_not_distinct", codes)

    def test_reward_vector_and_one_sixth_fpr_are_enforced(self) -> None:
        def mutation(row: dict[str, Any]) -> None:
            wrong = next(
                index
                for index in range(7)
                if index
                not in {row["gold_index"], row["shared_wrong_index"]}
            )
            row["reward_vectors"]["shared_leaky"][wrong] = 1

        _mutate_first(
            self.root / validator.SOURCE_FILE,
            lambda _row: True,
            mutation,
        )
        report = validator.validate_controlled_assets(self.root)
        self.assertFalse(report["valid"])
        self.assertTrue(
            {"reward_vector_mismatch", "fpr_not_one_sixth"}
            <= _error_codes(report)
        )

    def test_exact_task_oracles_reject_old_expansions_and_depth_error(
        self,
    ) -> None:
        path = self.root / validator.SOURCE_FILE

        _mutate_first(
            path,
            lambda row: row["task_id"] == "MOD7_SUM_V1",
            lambda row: row.__setitem__(
                "task_input", {"numbers": [1, 2, 3, 4, 5, 6]}
            ),
        )
        _mutate_first(
            path,
            lambda row: row["task_id"] == "DFA7_FINAL_V1",
            lambda row: row["task_input"].__setitem__(
                "sequence", ["X", "Y", "Z"]
            ),
        )
        _mutate_first(
            path,
            lambda row: (
                row["task_id"] == "PAREN_MAX_DEPTH7_V1"
                and row["canonical_class"] == 1
            ),
            lambda row: row.__setitem__(
                "task_input", {"parentheses": "()"}
            ),
        )
        report = validator.validate_controlled_assets(self.root)
        codes = _error_codes(report)
        self.assertIn("mod7_input_schema", codes)
        self.assertIn("dfa_sequence", codes)
        self.assertIn("oracle_class_mismatch", codes)

    def test_cross_stack_split_overlap_is_rejected(self) -> None:
        rows = _read_jsonl(self.root / validator.CALIBRATION_FILE)
        source = _read_jsonl(self.root / validator.SOURCE_FILE)[0]
        rows[0]["row_seed"] = source["row_seed"]
        rows[0]["lineage_id"] = source["lineage_id"]
        rows[0]["prompt"] = source["prompt"]
        rows[0]["prompt_sha256"] = source["prompt_sha256"]
        rows[0]["record_sha256"] = validator.record_sha256(rows[0])
        _write_jsonl(self.root / validator.CALIBRATION_FILE, rows)
        report = validator.validate_controlled_assets(self.root)
        self.assertIn("cross_stack_split_overlap", _error_codes(report))

    def test_manifest_must_cover_all_1960_candidate_records(self) -> None:
        rows = _read_jsonl(self.root / validator.REWARD_FILE)
        rows.pop()
        _write_jsonl(self.root / validator.REWARD_FILE, rows)
        report = validator.validate_controlled_assets(self.root)
        codes = _error_codes(report)
        self.assertIn("manifest_candidates_missing", codes)
        self.assertIn("manifest_candidate_total", codes)

    def test_audit_byte_hash_record_hashes_and_chain_are_enforced(self) -> None:
        rows = _read_jsonl(self.root / validator.AUDIT_FILE)
        rows[0]["prompt"] += " "
        _write_jsonl(self.root / validator.AUDIT_FILE, rows)
        report = validator.validate_controlled_assets(self.root)
        codes = _error_codes(report)
        self.assertIn("record_hash_mismatch", codes)
        self.assertIn("audit_file_hash_mismatch", codes)

        receipt = json.loads(
            (self.root / validator.SEAL_FILE).read_text(encoding="utf-8")
        )
        receipt["hash_chain_head"] = "f" * 64
        _write_json(self.root / validator.SEAL_FILE, receipt)
        report = validator.validate_controlled_assets(self.root)
        self.assertIn("audit_chain_head_mismatch", _error_codes(report))

    def test_forbidden_source_human_pass_model_run_and_authorization_fail(
        self,
    ) -> None:
        def mutation(row: dict[str, Any]) -> None:
            row["generator_id"] = "old_sqlite_synthetic_h-id_generator"
            row["human_review_status"] = "PASS"
            row["model_dependent_status"] = "COMPLETE"

        _mutate_first(
            self.root / validator.SOURCE_FILE,
            lambda _row: True,
            mutation,
        )
        protocol = json.loads(
            (self.root / validator.PROTOCOL_FILE).read_text(encoding="utf-8")
        )
        protocol["authorization"]["model_forward"] = True
        _write_json(self.root / validator.PROTOCOL_FILE, protocol)
        report = validator.validate_controlled_assets(self.root)
        codes = _error_codes(report)
        self.assertIn("forbidden_legacy_or_synthetic_source", codes)
        self.assertIn("human_review_not_pending", codes)
        self.assertIn("model_dependent_not_not_run", codes)
        self.assertIn("authorization_not_false", codes)

    def test_cli_prints_json_and_exits_nonzero_on_any_violation(self) -> None:
        (self.root / validator.REWARD_FILE).unlink()
        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(completed.stdout)
        self.assertFalse(report["valid"])
        self.assertIn("missing_required_file", _error_codes(report))
        self.assertFalse(report["model_execution_performed"])


if __name__ == "__main__":
    unittest.main()
