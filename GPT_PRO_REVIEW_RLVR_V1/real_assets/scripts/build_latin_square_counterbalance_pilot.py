#!/usr/bin/env python3
"""Build and validate a CPU-only Latin-square counterbalance feasibility pilot.

This script does not modify the frozen v5 assets. It reads their existing
model-free rows, treats each row as a paired repeated measure, and derives five
rule-identity blocks. No tokenizer, model, optimizer, forward pass, gradient,
training, RL, or RLVR action is imported or executed.

The pilot answers one narrow question only: can the +1..+5 latent transforms be
counterbalanced so that shared and task-local arms have exactly equal
candidate-aligned reward-vector and ordered gold-to-wrong geometry after
aggregation over the five pre-registered rule blocks?
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
REAL_ASSETS_DIR = SCRIPT_PATH.parents[1]
DEFAULT_INPUT_ROOT = REAL_ASSETS_DIR / "build_v4_a"

SCHEMA_VERSION = "p4-r1-latin-square-counterbalance-pilot-v1"
EXPERIMENT_STATUS = "NOT_RUN"
HUMAN_REVIEW_STATUS = "PENDING_HUMAN_REVIEW"
DESIGN_REVIEW_STATUS = "PENDING_POST_REDESIGN_REVIEW"
AUDIT_ACCESS_MODE = "NONBLIND_LOCKED_AUDIT_REFERENCE"

MODULUS = 7
CANDIDATES = tuple(f"FINAL=K{index}" for index in range(MODULUS))
RULE_OFFSETS = (1, 2, 3, 4, 5)

TASK_MOD7_SUM = "MOD7_SUM_V1"
TASK_DFA7_FINAL = "DFA7_FINAL_V1"
TASK_MARKED_RANK7 = "MARKED_RANK7_V1"
TASK_PAREN_MAX_DEPTH7 = "PAREN_MAX_DEPTH7_V1"
TASK_ORDER = (
    TASK_MOD7_SUM,
    TASK_DFA7_FINAL,
    TASK_MARKED_RANK7,
    TASK_PAREN_MAX_DEPTH7,
)
TASK_SHORT = {
    TASK_MOD7_SUM: "MOD7",
    TASK_DFA7_FINAL: "DFA7",
    TASK_MARKED_RANK7: "RANK7",
    TASK_PAREN_MAX_DEPTH7: "PAREN7",
}

TASK_PAIR_TASKS = {
    "TP1_MOD7SUM_DFA7": (TASK_MOD7_SUM, TASK_DFA7_FINAL),
    "TP2_RANK7_PARENDEPTH7": (TASK_MARKED_RANK7, TASK_PAREN_MAX_DEPTH7),
}
MAPPING_IDS = ("mapping_0", "mapping_1")
MIRROR_ROLES = ("A_TO_B", "B_TO_A")
EXPECTED_MAPPING_STACKS = tuple(
    f"TP{pair_index}-M{mapping_index}-{mirror_role}"
    for pair_index in (1, 2)
    for mapping_index in (0, 1)
    for mirror_role in MIRROR_ROLES
)
EXPECTED_SPLIT_COUNTS = {
    "SOURCE": 112,
    "TARGET_CALIBRATION": 56,
    "TARGET_AUDIT": 112,
}
EXPECTED_ROWS_PER_STACK = {
    "SOURCE": 14,
    "TARGET_CALIBRATION": 7,
    "TARGET_AUDIT": 14,
}

INPUT_FILES = {
    "source": "REAL_SOURCE_BUNDLES_V1.jsonl",
    "calibration": "TARGET_CALIBRATION_REAL_V1.jsonl",
    "audit": "TARGET_AUDIT_REAL_V1.jsonl",
}
OUTPUT_FILES = {
    "assignment": "LATIN_SQUARE_ASSIGNMENT_V1.json",
    "derived": "LATIN_SQUARE_DERIVED_ROWS_V1.jsonl",
    "baseline": "BASELINE_REWARD_VECTOR_PAIR_GEOMETRY_AUDIT_V1.json",
    "geometry": "REWARD_VECTOR_PAIR_GEOMETRY_AUDIT_V1.json",
    "manifest": "LATIN_SQUARE_PILOT_BUILD_MANIFEST_V1.json",
}


class PilotError(RuntimeError):
    """Raised when the CPU-only pilot violates a frozen contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PilotError(f"required input file is missing: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line:
            raise PilotError(f"blank JSONL record: {path}:{line_number}")
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PilotError(
                f"invalid JSONL record: {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise PilotError(
                f"JSONL record is not an object: {path}:{line_number}"
            )
        rows.append(value)
    return rows


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(value))


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("wb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row))


def rule_identity(offset: int) -> str:
    if offset not in RULE_OFFSETS:
        raise PilotError(f"invalid rule offset: {offset}")
    return f"Z7_PLUS{offset}"


def latin_square_blocks() -> list[dict[str, Any]]:
    """Return the frozen 5x5 cyclic Latin square over rule identities."""

    blocks: list[dict[str, Any]] = []
    for block_index, shared_offset in enumerate(RULE_OFFSETS):
        local_offsets = {
            task_id: RULE_OFFSETS[
                (block_index + task_index + 1) % len(RULE_OFFSETS)
            ]
            for task_index, task_id in enumerate(TASK_ORDER)
        }
        blocks.append(
            {
                "rule_block_id": f"RB{block_index}",
                "block_index": block_index,
                "shared_offset_mod7": shared_offset,
                "shared_rule_identity_id": rule_identity(shared_offset),
                "local_offsets_mod7": local_offsets,
                "local_rule_identity_ids": {
                    task_id: rule_identity(offset)
                    for task_id, offset in local_offsets.items()
                },
            }
        )
    validate_latin_square(blocks)
    return blocks


def validate_latin_square(blocks: Sequence[Mapping[str, Any]]) -> None:
    if len(blocks) != len(RULE_OFFSETS):
        raise PilotError("Latin square must contain exactly five rule blocks")
    if [row["rule_block_id"] for row in blocks] != [
        f"RB{index}" for index in range(5)
    ]:
        raise PilotError("rule block identifiers are not canonical")

    for block_index, row in enumerate(blocks):
        if row.get("block_index") != block_index:
            raise PilotError("rule block indices are not canonical")
        shared_offset = row.get("shared_offset_mod7")
        if row.get("shared_rule_identity_id") != rule_identity(shared_offset):
            raise PilotError("shared rule identity does not match its offset")
        local_offsets = row.get("local_offsets_mod7")
        local_identities = row.get("local_rule_identity_ids")
        if not isinstance(local_offsets, Mapping) or set(local_offsets) != set(
            TASK_ORDER
        ):
            raise PilotError("local offset task coverage is not exact")
        if not isinstance(local_identities, Mapping) or set(
            local_identities
        ) != set(TASK_ORDER):
            raise PilotError("local identity task coverage is not exact")
        for task_id in TASK_ORDER:
            if local_identities[task_id] != rule_identity(
                local_offsets[task_id]
            ):
                raise PilotError(
                    f"local rule identity does not match its offset: {task_id}"
                )

    shared_counts = Counter(row["shared_offset_mod7"] for row in blocks)
    expected = Counter({offset: 1 for offset in RULE_OFFSETS})
    if shared_counts != expected:
        raise PilotError("each rule identity must serve shared exactly once")

    for task_id in TASK_ORDER:
        local_counts = Counter(
            row["local_offsets_mod7"][task_id] for row in blocks
        )
        if local_counts != expected:
            raise PilotError(
                f"each rule identity must serve {task_id} local exactly once"
            )
        for row in blocks:
            if (
                row["shared_offset_mod7"]
                == row["local_offsets_mod7"][task_id]
            ):
                raise PilotError(
                    f"shared/local offsets collide in {row['rule_block_id']} "
                    f"for {task_id}"
                )


def load_base_rows(input_root: Path) -> list[dict[str, Any]]:
    source_bundles = read_jsonl(input_root / INPUT_FILES["source"])
    calibration_rows = read_jsonl(input_root / INPUT_FILES["calibration"])
    audit_rows = read_jsonl(input_root / INPUT_FILES["audit"])

    if len(source_bundles) != 8:
        raise PilotError("baseline source must contain exactly eight bundles")
    source_rows: list[dict[str, Any]] = []
    for bundle in source_bundles:
        rows = bundle.get("rows")
        if not isinstance(rows, list) or len(rows) != 14:
            raise PilotError("each baseline source bundle must contain 14 rows")
        source_rows.extend(rows)
    if len(source_rows) != 112:
        raise PilotError("baseline source must contain exactly 112 rows")
    if len(calibration_rows) != 56:
        raise PilotError("baseline calibration must contain exactly 56 rows")
    if len(audit_rows) != 112:
        raise PilotError("baseline audit must contain exactly 112 rows")

    rows = [*source_rows, *calibration_rows, *audit_rows]
    seen_row_ids: set[str] = set()
    for row in rows:
        row_id = row.get("row_id")
        if not isinstance(row_id, str) or row_id in seen_row_ids:
            raise PilotError(f"missing or reused baseline row_id: {row_id}")
        seen_row_ids.add(row_id)
        if row.get("experiment_status") != EXPERIMENT_STATUS:
            raise PilotError(f"baseline row is not NOT_RUN: {row_id}")
        if row.get("scientific_evidence") is not False:
            raise PilotError(f"baseline row claims scientific evidence: {row_id}")
        if row.get("human_review_status") != HUMAN_REVIEW_STATUS:
            raise PilotError(f"baseline row is not pending review: {row_id}")
        if row.get("split_role") not in {
            "SOURCE",
            "TARGET_CALIBRATION",
            "TARGET_AUDIT",
        }:
            raise PilotError(f"invalid split role: {row_id}")
        if row.get("task_id") not in TASK_ORDER:
            raise PilotError(f"unexpected task: {row_id}")
        z = row.get("canonical_z")
        if type(z) is not int or not 0 <= z < MODULUS:
            raise PilotError(f"invalid canonical_z: {row_id}")
        codebook = row.get("codebook")
        latent_to_candidate = (
            codebook.get("latent_to_candidate")
            if isinstance(codebook, dict)
            else None
        )
        if (
            not isinstance(latent_to_candidate, list)
            or len(latent_to_candidate) != MODULUS
            or sorted(latent_to_candidate) != sorted(CANDIDATES)
        ):
            raise PilotError(f"invalid codebook permutation: {row_id}")
        if row.get("gold_candidate") != latent_to_candidate[z]:
            raise PilotError(f"baseline gold/codebook mismatch: {row_id}")
        for arm in ("shared", "local"):
            offset = row.get(f"{arm}_offset_mod7")
            if offset not in RULE_OFFSETS:
                raise PilotError(f"invalid baseline {arm} offset: {row_id}")
            expected_wrong = latent_to_candidate[(z + offset) % MODULUS]
            if row.get(f"{arm}_bug_candidate") != expected_wrong:
                raise PilotError(
                    f"baseline {arm} wrong candidate is not derivable: {row_id}"
                )
            if expected_wrong == row["gold_candidate"]:
                raise PilotError(f"baseline {arm} wrong equals gold: {row_id}")
        if row["shared_bug_candidate"] == row["local_bug_candidate"]:
            raise PilotError(f"baseline shared/local wrong collision: {row_id}")
        for required in (
            "mapping_stack_id",
            "task_pair_id",
            "mapping_id",
            "mirror_role",
            "row_bytes",
            "prompt_bytes",
        ):
            if required not in row:
                raise PilotError(f"baseline row lacks {required}: {row_id}")

    split_counts = Counter(row["split_role"] for row in rows)
    if split_counts != Counter(EXPECTED_SPLIT_COUNTS):
        raise PilotError(
            f"baseline split counts are not exact: {dict(split_counts)}"
        )
    if Counter(row["task_pair_id"] for row in rows) != Counter(
        {pair_id: 140 for pair_id in TASK_PAIR_TASKS}
    ):
        raise PilotError("baseline must contain exactly the two frozen task pairs")
    if Counter(row["mapping_stack_id"] for row in rows) != Counter(
        {stack_id: 35 for stack_id in EXPECTED_MAPPING_STACKS}
    ):
        raise PilotError("baseline must contain exactly the eight frozen stacks")

    for row in rows:
        pair_id = row["task_pair_id"]
        if row["task_id"] not in TASK_PAIR_TASKS[pair_id]:
            raise PilotError(
                f"task does not belong to task pair: {row['row_id']}"
            )
        stack_id = row["mapping_stack_id"]
        if stack_id not in EXPECTED_MAPPING_STACKS:
            raise PilotError(f"unexpected mapping stack: {row['row_id']}")
        pair_index = int(stack_id[2])
        expected_pair = tuple(TASK_PAIR_TASKS)[pair_index - 1]
        expected_mapping = MAPPING_IDS[int(stack_id[5])]
        expected_mirror = stack_id[7:]
        if pair_id != expected_pair:
            raise PilotError(f"stack/task-pair mismatch: {row['row_id']}")
        if row["mapping_id"] != expected_mapping:
            raise PilotError(f"stack/mapping mismatch: {row['row_id']}")
        if row["mirror_role"] != expected_mirror:
            raise PilotError(f"stack/mirror mismatch: {row['row_id']}")
        pair_tasks = TASK_PAIR_TASKS[pair_id]
        source_task = pair_tasks[0] if expected_mirror == "A_TO_B" else pair_tasks[1]
        expected_task = (
            source_task
            if row["split_role"] == "SOURCE"
            else next(task for task in pair_tasks if task != source_task)
        )
        if row["task_id"] != expected_task:
            raise PilotError(f"split/direction task mismatch: {row['row_id']}")

    for split_role, expected_total in EXPECTED_SPLIT_COUNTS.items():
        split_rows = [row for row in rows if row["split_role"] == split_role]
        if len(split_rows) != expected_total:
            raise PilotError(f"unexpected {split_role} row count")
        stack_counts = Counter(row["mapping_stack_id"] for row in split_rows)
        expected_stack_count = EXPECTED_ROWS_PER_STACK[split_role]
        if stack_counts != Counter(
            {
                stack_id: expected_stack_count
                for stack_id in EXPECTED_MAPPING_STACKS
            }
        ):
            raise PilotError(f"{split_role} stack coverage is not exact")
        for stack_id in EXPECTED_MAPPING_STACKS:
            stack_rows = [
                row for row in split_rows if row["mapping_stack_id"] == stack_id
            ]
            expected_per_class = 1 if split_role == "TARGET_CALIBRATION" else 2
            if Counter(row["canonical_z"] for row in stack_rows) != Counter(
                {z: expected_per_class for z in range(MODULUS)}
            ):
                raise PilotError(
                    f"{split_role}/{stack_id} class coverage is not exact"
                )

    source_bundle_stacks = [bundle.get("mapping_stack_id") for bundle in source_bundles]
    if Counter(source_bundle_stacks) != Counter(
        {stack_id: 1 for stack_id in EXPECTED_MAPPING_STACKS}
    ):
        raise PilotError("source bundle stack coverage is not exact")
    for bundle in source_bundles:
        stack_id = bundle["mapping_stack_id"]
        bundle_rows = bundle["rows"]
        if any(row["mapping_stack_id"] != stack_id for row in bundle_rows):
            raise PilotError(f"source bundle contains cross-stack rows: {stack_id}")
    return rows


def candidate_for(row: Mapping[str, Any], latent_z: int) -> str:
    return row["codebook"]["latent_to_candidate"][latent_z % MODULUS]


def reward_vector(gold: str, wrong: str) -> list[int]:
    if gold == wrong:
        raise PilotError("gold and wrong candidates must be distinct")
    accepted = {gold, wrong}
    return [1 if candidate in accepted else 0 for candidate in CANDIDATES]


def derive_rows(
    base_rows: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    derived: list[dict[str, Any]] = []
    for block in blocks:
        for base_row in base_rows:
            z = base_row["canonical_z"]
            task_id = base_row["task_id"]
            shared_offset = block["shared_offset_mod7"]
            local_offset = block["local_offsets_mod7"][task_id]
            shared_wrong = candidate_for(base_row, z + shared_offset)
            local_wrong = candidate_for(base_row, z + local_offset)
            gold = base_row["gold_candidate"]
            if len({gold, shared_wrong, local_wrong}) != 3:
                raise PilotError(
                    "gold/shared/local collision in "
                    f"{block['rule_block_id']}:{base_row['row_id']}"
                )
            derived_row_id = (
                f"{block['rule_block_id']}::{base_row['row_id']}"
            )
            derived.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "experiment_status": EXPERIMENT_STATUS,
                    "scientific_evidence": False,
                    "formal_experiment": False,
                    "run_eligible": False,
                    "design_review_status": DESIGN_REVIEW_STATUS,
                    "human_review_status": HUMAN_REVIEW_STATUS,
                    "audit_access_mode": AUDIT_ACCESS_MODE,
                    "derived_row_id": derived_row_id,
                    "paired_repeated_measure_id": base_row["row_id"],
                    "paired_repeated_measure": True,
                    "base_row_id": base_row["row_id"],
                    "base_row_bytes_sha256": base_row["row_bytes"]["sha256"],
                    "base_prompt_sha256": base_row["prompt_bytes"]["sha256"],
                    "base_mapping_stack_id": base_row["mapping_stack_id"],
                    "design_cell_id": (
                        f"{block['rule_block_id']}::"
                        f"{base_row['mapping_stack_id']}"
                    ),
                    "rule_block_id": block["rule_block_id"],
                    "task_pair_id": base_row["task_pair_id"],
                    "mapping_id": base_row["mapping_id"],
                    "mirror_role": base_row["mirror_role"],
                    "split_role": base_row["split_role"],
                    "task_id": task_id,
                    "canonical_z": z,
                    "candidate_order": list(CANDIDATES),
                    "gold_candidate": gold,
                    "shared_offset_mod7": shared_offset,
                    "shared_rule_identity_id": rule_identity(shared_offset),
                    "shared_wrong_candidate": shared_wrong,
                    "shared_reward_vector": reward_vector(
                        gold, shared_wrong
                    ),
                    "local_offset_mod7": local_offset,
                    "local_rule_identity_id": rule_identity(local_offset),
                    "local_wrong_candidate": local_wrong,
                    "local_reward_vector": reward_vector(gold, local_wrong),
                    "static_online_fpr_shared": "1/6",
                    "static_online_fpr_local": "1/6",
                    "model_dependent_status": {
                        "tokenizer_check": "NOT_RUN",
                        "model_forward": "NOT_RUN",
                        "gradient": "NOT_RUN",
                        "optimizer_step": "NOT_RUN",
                        "training": "NOT_RUN",
                        "rl": "NOT_RUN",
                        "rlvr": "NOT_RUN",
                        "L_D_tau": "NOT_RUN",
                    },
                }
            )
    expected = len(base_rows) * len(blocks)
    if len(derived) != expected:
        raise PilotError("derived row count mismatch")
    return derived


def vector_key(row: Mapping[str, Any], arm: str) -> str:
    vector = row[f"{arm}_reward_vector"]
    if (
        not isinstance(vector, list)
        or len(vector) != MODULUS
        or any(value not in {0, 1} for value in vector)
        or sum(vector) != 2
    ):
        raise PilotError(
            f"invalid {arm} reward vector: {row.get('derived_row_id')}"
        )
    return "".join(str(value) for value in vector)


def pair_key(row: Mapping[str, Any], arm: str) -> str:
    return (
        f"{row['gold_candidate']}->{row[f'{arm}_wrong_candidate']}"
    )


def counter_diff(
    first: Counter[str], second: Counter[str]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for key in sorted(set(first) | set(second)):
        if first[key] != second[key]:
            result[key] = {
                "shared": first[key],
                "local": second[key],
                "delta": first[key] - second[key],
            }
    return result


def geometry_record(
    rows: Sequence[Mapping[str, Any]],
    *,
    split_role: str,
    scope: str,
    scope_id: str,
) -> dict[str, Any]:
    shared_vectors = Counter(vector_key(row, "shared") for row in rows)
    local_vectors = Counter(vector_key(row, "local") for row in rows)
    shared_pairs = Counter(pair_key(row, "shared") for row in rows)
    local_pairs = Counter(pair_key(row, "local") for row in rows)
    vector_equal = shared_vectors == local_vectors
    pair_equal = shared_pairs == local_pairs
    return {
        "split_role": split_role,
        "comparison_scope": scope,
        "scope_id": scope_id,
        "derived_row_count": len(rows),
        "candidate_aligned_vector_counter_shared": dict(
            sorted(shared_vectors.items())
        ),
        "candidate_aligned_vector_counter_local": dict(
            sorted(local_vectors.items())
        ),
        "candidate_aligned_vector_counter_diff": counter_diff(
            shared_vectors, local_vectors
        ),
        "gold_wrong_pair_counter_shared": dict(sorted(shared_pairs.items())),
        "gold_wrong_pair_counter_local": dict(sorted(local_pairs.items())),
        "gold_wrong_pair_counter_diff": counter_diff(
            shared_pairs, local_pairs
        ),
        "candidate_aligned_vector_counter_equal": vector_equal,
        "gold_wrong_pair_counter_equal": pair_equal,
        "result": (
            "PASS_EXACT_COUNTERBALANCE"
            if vector_equal and pair_equal
            else "FAIL_EXACT_COUNTERBALANCE"
        ),
    }


def build_geometry_audit(
    derived_rows: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_latin_square(blocks)
    expected_derived = 280 * 5
    if len(derived_rows) != expected_derived:
        raise PilotError(
            f"expected {expected_derived} derived rows, got "
            f"{len(derived_rows)}"
        )

    if len({row.get("derived_row_id") for row in derived_rows}) != expected_derived:
        raise PilotError("derived row identifiers are missing or reused")
    block_by_id = {block["rule_block_id"]: block for block in blocks}
    by_base_row: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in derived_rows:
        block_id = row.get("rule_block_id")
        if block_id not in block_by_id:
            raise PilotError("derived row references an unknown rule block")
        block = block_by_id[block_id]
        task_id = row.get("task_id")
        if task_id not in TASK_ORDER:
            raise PilotError("derived row references an unknown task")
        base_row_id = row.get("base_row_id")
        if row.get("derived_row_id") != f"{block_id}::{base_row_id}":
            raise PilotError("derived row identifier is not canonical")
        if row.get("paired_repeated_measure_id") != base_row_id:
            raise PilotError("paired repeated-measure identity is not canonical")
        if row.get("paired_repeated_measure") is not True:
            raise PilotError("derived row is not marked as a paired repeat")
        if row.get("shared_offset_mod7") != block["shared_offset_mod7"]:
            raise PilotError("derived shared offset does not match rule block")
        if row.get("local_offset_mod7") != block["local_offsets_mod7"][task_id]:
            raise PilotError("derived local offset does not match rule block")
        if row.get("shared_rule_identity_id") != block[
            "shared_rule_identity_id"
        ]:
            raise PilotError("derived shared identity does not match rule block")
        if row.get("local_rule_identity_id") != block[
            "local_rule_identity_ids"
        ][task_id]:
            raise PilotError("derived local identity does not match rule block")
        gold = row.get("gold_candidate")
        shared_wrong = row.get("shared_wrong_candidate")
        local_wrong = row.get("local_wrong_candidate")
        if row.get("shared_reward_vector") != reward_vector(gold, shared_wrong):
            raise PilotError("shared reward vector is not candidate-bound")
        if row.get("local_reward_vector") != reward_vector(gold, local_wrong):
            raise PilotError("local reward vector is not candidate-bound")
        if row.get("design_cell_id") != (
            f"{block_id}::{row.get('base_mapping_stack_id')}"
        ):
            raise PilotError("derived design cell identifier is not canonical")
        by_base_row[row["base_row_id"]].append(row)
    if len(by_base_row) != 280:
        raise PilotError("expected exactly 280 paired base rows")
    expected_offsets = Counter({offset: 1 for offset in RULE_OFFSETS})
    per_base_row_pass = True
    for rows in by_base_row.values():
        if len(rows) != 5:
            per_base_row_pass = False
            break
        if Counter(row["shared_offset_mod7"] for row in rows) != expected_offsets:
            per_base_row_pass = False
            break
        if Counter(row["local_offset_mod7"] for row in rows) != expected_offsets:
            per_base_row_pass = False
            break
        if Counter(row["rule_block_id"] for row in rows) != Counter(
            {f"RB{index}": 1 for index in range(5)}
        ):
            per_base_row_pass = False
            break

    split_counts = Counter(row["split_role"] for row in derived_rows)
    if split_counts != Counter(
        {split: count * 5 for split, count in EXPECTED_SPLIT_COUNTS.items()}
    ):
        raise PilotError("derived split coverage is not exact")
    pair_counts = Counter(
        (row["split_role"], row["task_pair_id"]) for row in derived_rows
    )
    expected_pair_counts = Counter(
        {
            (split, pair_id): (count // 2) * 5
            for split, count in EXPECTED_SPLIT_COUNTS.items()
            for pair_id in TASK_PAIR_TASKS
        }
    )
    if pair_counts != expected_pair_counts:
        raise PilotError("derived task-pair coverage is not exact")
    stack_counts = Counter(
        (row["split_role"], row["base_mapping_stack_id"])
        for row in derived_rows
    )
    expected_stack_counts = Counter(
        {
            (split, stack_id): rows_per_stack * 5
            for split, rows_per_stack in EXPECTED_ROWS_PER_STACK.items()
            for stack_id in EXPECTED_MAPPING_STACKS
        }
    )
    if stack_counts != expected_stack_counts:
        raise PilotError("derived mapping-stack coverage is not exact")

    records: list[dict[str, Any]] = []
    for split_role in ("SOURCE", "TARGET_CALIBRATION", "TARGET_AUDIT"):
        split_rows = [
            row for row in derived_rows if row["split_role"] == split_role
        ]
        records.append(
            geometry_record(
                split_rows,
                split_role=split_role,
                scope="GLOBAL_SPLIT",
                scope_id=split_role,
            )
        )
        for task_pair_id in TASK_PAIR_TASKS:
            records.append(
                geometry_record(
                    [
                        row
                        for row in split_rows
                        if row["task_pair_id"] == task_pair_id
                    ],
                    split_role=split_role,
                    scope="TASK_PAIR",
                    scope_id=task_pair_id,
                )
            )
        for stack_id in EXPECTED_MAPPING_STACKS:
            records.append(
                geometry_record(
                    [
                        row
                        for row in split_rows
                        if row["base_mapping_stack_id"] == stack_id
                    ],
                    split_role=split_role,
                    scope="BASE_MAPPING_STACK_ACROSS_RULE_BLOCKS",
                    scope_id=stack_id,
                )
            )

    failed = [record for record in records if not record["result"].startswith("PASS")]
    role_matrix = {
        rule_identity(offset): {
            "shared_block_count": sum(
                block["shared_offset_mod7"] == offset for block in blocks
            ),
            "local_block_count_by_task": {
                task_id: sum(
                    block["local_offsets_mod7"][task_id] == offset
                    for block in blocks
                )
                for task_id in TASK_ORDER
            },
        }
        for offset in RULE_OFFSETS
    }
    role_matrix_pass = all(
        row["shared_block_count"] == 1
        and all(count == 1 for count in row["local_block_count_by_task"].values())
        for row in role_matrix.values()
    )
    all_pass = not failed and per_base_row_pass and role_matrix_pass
    return {
        "schema_version": "reward-vector-pair-geometry-audit-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "run_eligible": False,
        "design_review_status": DESIGN_REVIEW_STATUS,
        "human_review_status": HUMAN_REVIEW_STATUS,
        "audit_access_mode": AUDIT_ACCESS_MODE,
        "static_reward_vector_definition": (
            "tuple(reward_by_arm[candidate] for candidate in "
            "[FINAL=K0,...,FINAL=K6])"
        ),
        "static_reward_vector_balance_scope": [
            "GLOBAL_SPLIT",
            "TASK_PAIR",
            "BASE_MAPPING_STACK_ACROSS_RULE_BLOCKS",
        ],
        "base_row_count": len(by_base_row),
        "rule_block_count": len(blocks),
        "derived_row_count": len(derived_rows),
        "paired_repeated_measure_design": True,
        "per_base_row_rule_identity_counterbalance_pass": per_base_row_pass,
        "rule_identity_role_matrix": role_matrix,
        "rule_identity_role_matrix_pass": role_matrix_pass,
        "geometry_records": records,
        "geometry_record_count": len(records),
        "failed_geometry_record_count": len(failed),
        "all_required_static_geometry_checks_pass": all_pass,
        "cpu_design_gate_result": (
            "PASS_COUNTERBALANCE_FEASIBILITY_ONLY"
            if all_pass
            else "FAIL_COUNTERBALANCE_FEASIBILITY"
        ),
        "scientific_boundary": (
            "A pass proves only deterministic static counterbalance feasibility. "
            "It does not authorize a model run or establish model-conditioned "
            "exchangeability, transfer, L, D, tau, or a causal result."
        ),
        "model_dependent_status": {
            "tokenizer_check": "NOT_RUN",
            "base_logits": "NOT_RUN",
            "model_forward": "NOT_RUN",
            "gradient": "NOT_RUN",
            "optimizer_step": "NOT_RUN",
            "training": "NOT_RUN",
            "rl": "NOT_RUN",
            "rlvr": "NOT_RUN",
            "L_D_tau": "NOT_RUN",
        },
    }


def build_baseline_audit(
    base_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Recompute the rejected v5 geometry without trusting its G1 flag."""

    records: list[dict[str, Any]] = []
    for split_role in ("SOURCE", "TARGET_CALIBRATION", "TARGET_AUDIT"):
        split_rows = [row for row in base_rows if row["split_role"] == split_role]
        synthetic_rows = [
            {
                **row,
                "shared_reward_vector": reward_vector(
                    row["gold_candidate"],
                    candidate_for(
                        row,
                        row["canonical_z"] + row["shared_offset_mod7"],
                    ),
                ),
                "local_reward_vector": reward_vector(
                    row["gold_candidate"],
                    candidate_for(
                        row,
                        row["canonical_z"] + row["local_offset_mod7"],
                    ),
                ),
                "shared_wrong_candidate": candidate_for(
                    row,
                    row["canonical_z"] + row["shared_offset_mod7"],
                ),
                "local_wrong_candidate": candidate_for(
                    row,
                    row["canonical_z"] + row["local_offset_mod7"],
                ),
            }
            for row in split_rows
        ]
        records.append(
            geometry_record(
                synthetic_rows,
                split_role=split_role,
                scope="GLOBAL_SPLIT",
                scope_id=split_role,
            )
        )
        for stack_id in sorted(
            {row["mapping_stack_id"] for row in split_rows}
        ):
            records.append(
                geometry_record(
                    [
                        row
                        for row in synthetic_rows
                        if row["mapping_stack_id"] == stack_id
                    ],
                    split_role=split_role,
                    scope="BASE_MAPPING_STACK",
                    scope_id=stack_id,
                )
            )
    failures = [
        record for record in records if record["result"].startswith("FAIL")
    ]
    return {
        "schema_version": "baseline-reward-vector-pair-geometry-audit-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "baseline_release_status": "REJECTED_CPU_DESIGN_BASELINE",
        "base_row_count": len(base_rows),
        "geometry_records": records,
        "geometry_record_count": len(records),
        "failed_geometry_record_count": len(failures),
        "baseline_exact_geometry_pass": not failures,
        "expected_result": "FAIL",
        "expected_failure_observed": bool(failures),
    }


def assignment_artifact(
    blocks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": "latin-square-rule-assignment-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "run_eligible": False,
        "design_review_status": DESIGN_REVIEW_STATUS,
        "human_review_status": HUMAN_REVIEW_STATUS,
        "audit_access_mode": AUDIT_ACCESS_MODE,
        "rule_identity_offsets_mod7": list(RULE_OFFSETS),
        "roles": ["SHARED", *[f"LOCAL_{TASK_SHORT[task]}" for task in TASK_ORDER]],
        "rule_blocks": list(blocks),
        "assignment_method": "FROZEN_5X5_CYCLIC_LATIN_SQUARE",
        "estimand_plan": {
            "within_rule_identity": (
                "compare shared versus task-local effects while holding the "
                "latent transform identity fixed"
            ),
            "across_rule_identity": (
                "average the five pre-registered within-identity contrasts"
            ),
            "primary_independence_cluster": "TASK_PAIR",
            "base_mapping_stacks": (
                "crossed repeated screen cells, not independent scientific repeats"
            ),
        },
        "authorization": {
            "tokenizer": False,
            "model_weights": False,
            "model_forward": False,
            "gradient": False,
            "optimizer_step": False,
            "training": False,
            "rl": False,
            "rlvr": False,
        },
    }


def validate_output(output_dir: Path, input_root: Path) -> dict[str, Any]:
    input_root = input_root.resolve()
    if not input_root.is_dir():
        raise PilotError(f"input root is not a directory: {input_root}")
    manifest_path = output_dir / OUTPUT_FILES["manifest"]
    if not manifest_path.is_file():
        raise PilotError("pilot build manifest is missing")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("experiment_status") != EXPERIMENT_STATUS:
        raise PilotError("pilot manifest status was washed")
    if manifest.get("run_eligible") is not False:
        raise PilotError("pilot manifest incorrectly permits a run")
    artifact_hashes = manifest.get("artifact_hashes")
    expected_artifacts = {
        OUTPUT_FILES["assignment"],
        OUTPUT_FILES["derived"],
        OUTPUT_FILES["baseline"],
        OUTPUT_FILES["geometry"],
    }
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != (
        expected_artifacts
    ):
        raise PilotError("pilot artifact inventory is not exact")
    for filename, binding in artifact_hashes.items():
        path = output_dir / filename
        if not path.is_file():
            raise PilotError(f"pilot artifact is missing: {filename}")
        if not isinstance(binding, dict) or set(binding) != {
            "sha256",
            "byte_length",
        }:
            raise PilotError(f"pilot artifact binding is malformed: {filename}")
        if path.stat().st_size != binding["byte_length"]:
            raise PilotError(f"pilot artifact byte length mismatch: {filename}")
        if sha256_file(path) != binding["sha256"]:
            raise PilotError(f"pilot artifact hash mismatch: {filename}")
    input_hashes = manifest.get("input_file_hashes")
    if not isinstance(input_hashes, dict) or set(input_hashes) != set(
        INPUT_FILES.values()
    ):
        raise PilotError("pilot input inventory is not exact")
    for filename, binding in input_hashes.items():
        path = input_root / filename
        if not path.is_file():
            raise PilotError(f"pilot input is missing: {filename}")
        expected_binding = {
            "sha256": sha256_file(path),
            "byte_length": path.stat().st_size,
        }
        if binding != expected_binding:
            raise PilotError(f"pilot input provenance mismatch: {filename}")

    expected_implementation = {
        "relpath": "scripts/build_latin_square_counterbalance_pilot.py",
        "sha256": sha256_file(SCRIPT_PATH),
        "stdlib_only": True,
        "cpu_only": True,
    }
    if manifest.get("implementation") != expected_implementation:
        raise PilotError("pilot implementation provenance mismatch")

    canonical_blocks = latin_square_blocks()
    expected_assignment = assignment_artifact(canonical_blocks)
    assignment = json.loads(
        (output_dir / OUTPUT_FILES["assignment"]).read_bytes()
    )
    if canonical_json_bytes(assignment) != canonical_json_bytes(
        expected_assignment
    ):
        raise PilotError("stored assignment is not the frozen assignment")
    blocks = assignment["rule_blocks"]

    base_rows = load_base_rows(input_root)
    expected_derived_rows = derive_rows(base_rows, canonical_blocks)
    derived_rows = read_jsonl(output_dir / OUTPUT_FILES["derived"])
    for row in derived_rows:
        if row.get("experiment_status") != EXPERIMENT_STATUS:
            raise PilotError("derived row status was washed")
        if row.get("scientific_evidence") is not False:
            raise PilotError("derived row claims scientific evidence")
        if row.get("run_eligible") is not False:
            raise PilotError("derived row incorrectly permits a run")
    if derived_rows != expected_derived_rows:
        raise PilotError("derived rows do not match frozen input provenance")
    geometry_path = output_dir / OUTPUT_FILES["geometry"]
    geometry = json.loads(geometry_path.read_bytes())
    expected_geometry = build_geometry_audit(expected_derived_rows, blocks)
    if canonical_json_bytes(geometry) != canonical_json_bytes(
        expected_geometry
    ):
        raise PilotError(
            "stored geometry audit does not match derived-row recomputation"
        )
    baseline = json.loads(
        (output_dir / OUTPUT_FILES["baseline"]).read_bytes()
    )
    expected_baseline = build_baseline_audit(base_rows)
    if canonical_json_bytes(baseline) != canonical_json_bytes(
        expected_baseline
    ):
        raise PilotError("stored baseline audit was not recomputed from input")
    if geometry.get("all_required_static_geometry_checks_pass") is not True:
        raise PilotError("counterbalanced geometry did not pass")
    if geometry.get("failed_geometry_record_count") != 0:
        raise PilotError("counterbalanced geometry contains failed scopes")
    if baseline.get("expected_failure_observed") is not True:
        raise PilotError("rejected baseline unexpectedly passed geometry")
    if manifest.get("design_counts") != {
        "base_rows": 280,
        "rule_blocks": 5,
        "base_mapping_stacks": 8,
        "derived_rows": 1400,
        "formal_future_cells": 40,
    }:
        raise PilotError("pilot manifest design counts are not exact")
    if manifest.get("cpu_design_gate_result") != geometry.get(
        "cpu_design_gate_result"
    ):
        raise PilotError("pilot manifest/geometry gate mismatch")
    if manifest.get("baseline_expected_failure_observed") is not True:
        raise PilotError("pilot manifest baseline status was washed")
    return {
        "valid": True,
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "run_eligible": False,
        "base_row_count": geometry["base_row_count"],
        "derived_row_count": geometry["derived_row_count"],
        "geometry_record_count": geometry["geometry_record_count"],
        "failed_geometry_record_count": 0,
        "baseline_failed_geometry_record_count": (
            baseline["failed_geometry_record_count"]
        ),
        "cpu_design_gate_result": geometry["cpu_design_gate_result"],
        "model_execution_performed": False,
    }


def build_pilot(input_root: Path, output_dir: Path) -> dict[str, Any]:
    input_root = input_root.resolve()
    output_dir = output_dir.resolve()
    if not input_root.is_dir():
        raise PilotError(f"input root is not a directory: {input_root}")
    if output_dir.exists():
        raise PilotError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    staging: Path | None = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.staging-",
            dir=output_dir.parent,
        )
    )
    try:
        blocks = latin_square_blocks()
        base_rows = load_base_rows(input_root)
        derived_rows = derive_rows(base_rows, blocks)
        assignment = assignment_artifact(blocks)
        baseline = build_baseline_audit(base_rows)
        geometry = build_geometry_audit(derived_rows, blocks)

        write_json(staging / OUTPUT_FILES["assignment"], assignment)
        write_jsonl(staging / OUTPUT_FILES["derived"], derived_rows)
        write_json(staging / OUTPUT_FILES["baseline"], baseline)
        write_json(staging / OUTPUT_FILES["geometry"], geometry)

        artifact_hashes = {
            filename: {
                "sha256": sha256_file(staging / filename),
                "byte_length": (staging / filename).stat().st_size,
            }
            for filename in (
                OUTPUT_FILES["assignment"],
                OUTPUT_FILES["derived"],
                OUTPUT_FILES["baseline"],
                OUTPUT_FILES["geometry"],
            )
        }
        manifest = {
            "schema_version": "latin-square-pilot-build-manifest-v1",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "formal_experiment": False,
            "run_eligible": False,
            "design_review_status": DESIGN_REVIEW_STATUS,
            "human_review_status": HUMAN_REVIEW_STATUS,
            "audit_access_mode": AUDIT_ACCESS_MODE,
            "input_root_role": "REJECTED_V5_NATIVE_BASELINE_REFERENCE",
            "input_file_hashes": {
                filename: {
                    "sha256": sha256_file(input_root / filename),
                    "byte_length": (input_root / filename).stat().st_size,
                }
                for filename in INPUT_FILES.values()
            },
            "implementation": {
                "relpath": (
                    "scripts/build_latin_square_counterbalance_pilot.py"
                ),
                "sha256": sha256_file(SCRIPT_PATH),
                "stdlib_only": True,
                "cpu_only": True,
            },
            "design_counts": {
                "base_rows": len(base_rows),
                "rule_blocks": len(blocks),
                "base_mapping_stacks": 8,
                "derived_rows": len(derived_rows),
                "formal_future_cells": 5 * 8,
            },
            "artifact_hashes": artifact_hashes,
            "cpu_design_gate_result": geometry["cpu_design_gate_result"],
            "baseline_expected_failure_observed": (
                baseline["expected_failure_observed"]
            ),
            "model_dependent_status": {
                "tokenizer_check": "NOT_RUN",
                "model_forward": "NOT_RUN",
                "gradient": "NOT_RUN",
                "optimizer_step": "NOT_RUN",
                "training": "NOT_RUN",
                "rl": "NOT_RUN",
                "rlvr": "NOT_RUN",
                "L_D_tau": "NOT_RUN",
            },
            "remaining_blockers": [
                "PRODUCTION_PIPELINE_NOT_YET_MIGRATED_TO_40_CELLS",
                "PENDING_POST_REDESIGN_REVIEW",
                "PENDING_HUMAN_REVIEW",
                "G1_MODEL_DEPENDENT_DEFINITIONS_AND_THRESHOLDS_NOT_FROZEN",
                "HIDDEN_AUDIT_NOT_REGENERATED_AFTER_BLOCKING_FIELDS_FREEZE",
                "NO_MODEL_RUN_AUTHORIZATION",
            ],
        }
        write_json(staging / OUTPUT_FILES["manifest"], manifest)
        os.replace(staging, output_dir)
        staging = None
        return validate_output(output_dir, input_root)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        type=Path,
        default=DEFAULT_INPUT_ROOT,
        help="Rejected v5 native build used only as frozen CPU row input",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New output directory; existing targets are rejected",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing --output-dir without rebuilding",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.validate_only:
        result = validate_output(
            args.output_dir.resolve(),
            args.input_root.resolve(),
        )
    else:
        result = build_pilot(
            args.input_root.resolve(),
            args.output_dir.resolve(),
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
