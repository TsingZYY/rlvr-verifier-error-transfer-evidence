#!/usr/bin/env python3
"""Fail-closed static validator for the controlled RLVR real-asset bundle.

This module intentionally uses only the Python standard library.  It validates
the pre-model asset contract approved in
``07_GPT_PRO_TASK_PAIR_VERDICT.md``; it never imports, loads, or executes a
model.  Any missing, malformed, inconsistent, or unverifiable required fact is
reported as an error and makes the command exit non-zero.

The record hash used by this contract is:

    sha256(canonical_json(record without ``record_sha256``))

where canonical JSON is UTF-8, sorted by key, has no insignificant whitespace,
and is emitted with ``ensure_ascii=False``.

The audit hash chain is:

    chain[0] = sha256(genesis_ascii || record_hash[0]_ascii)
    chain[i] = sha256(chain[i-1]_ascii || record_hash[i]_ascii)

The default genesis is 64 ASCII zeroes and must be stated in the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPORT_SCHEMA_VERSION = "controlled-assets-validation-report-v1"
PROTOCOL_FILE = "P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json"
MAPPING_FILE = "REAL_MAPPING_STACKS_V1.jsonl"
SOURCE_FILE = "REAL_SOURCE_BUNDLES_V1.jsonl"
REWARD_FILE = "VERIFIER_REWARD_MANIFEST_V1.jsonl"
G1_FILE = "G1_OPPORTUNITY_AUDIT_V1.json"
CALIBRATION_FILE = "TARGET_CALIBRATION_REAL_V1.jsonl"
AUDIT_FILE = "TARGET_AUDIT_REAL_V1.jsonl"
SEAL_FILE = "TARGET_AUDIT_SEAL_RECEIPT.json"
PREREG_FILE = "RANDOMIZATION_MODEL_ENV_PREREG_V1.json"

REQUIRED_FILES = (
    PROTOCOL_FILE,
    MAPPING_FILE,
    SOURCE_FILE,
    REWARD_FILE,
    G1_FILE,
    CALIBRATION_FILE,
    AUDIT_FILE,
    SEAL_FILE,
    PREREG_FILE,
)

CANDIDATES = tuple(f"FINAL=K{i}" for i in range(7))
ARMS = (
    "clean",
    "shared_leaky",
    "local_leaky",
    "shared_frozen_probe_control",
    "local_frozen_probe_control",
)

PAIR_DEFINITIONS = {
    "task_pair_1": ("MOD7_SUM_V1", "DFA7_FINAL_V1"),
    "task_pair_2": (
        "MARKED_RANK7_V1",
        "PAREN_MAX_DEPTH7_V1",
    ),
}

LOCAL_SHIFT_BY_TASK = {
    "MOD7_SUM_V1": 2,
    "DFA7_FINAL_V1": 3,
    "MARKED_RANK7_V1": 4,
    "PAREN_MAX_DEPTH7_V1": 5,
}

EXPECTED_CODEBOOKS = {
    "mapping_0": {
        "A": tuple(range(7)),
        "B": tuple((2 * z + 1) % 7 for z in range(7)),
    },
    "mapping_1": {
        "A": tuple((3 * z + 2) % 7 for z in range(7)),
        "B": tuple((5 * z + 4) % 7 for z in range(7)),
    },
}

EXPECTED_STACK_IDS = tuple(
    f"TP{pair_number}-M{mapping_number}-{direction}"
    for pair_number in (1, 2)
    for mapping_number in (0, 1)
    for direction in ("A_TO_B", "B_TO_A")
)

EXPECTED_STACK_META = {}
for _pair_number, (_pair_id, (_task_a, _task_b)) in enumerate(
    PAIR_DEFINITIONS.items(), start=1
):
    for _mapping_number in (0, 1):
        for _direction in ("A_TO_B", "B_TO_A"):
            _source_task, _target_task = (
                (_task_a, _task_b)
                if _direction == "A_TO_B"
                else (_task_b, _task_a)
            )
            EXPECTED_STACK_META[
                f"TP{_pair_number}-M{_mapping_number}-{_direction}"
            ] = {
                "pair_id": _pair_id,
                "mapping_id": f"mapping_{_mapping_number}",
                "direction": _direction,
                "source_task_id": _source_task,
                "target_task_id": _target_task,
                "task_a": _task_a,
                "task_b": _task_b,
            }

ROW_SPLITS = {
    "source": (SOURCE_FILE, 14, 2),
    "target_calibration": (CALIBRATION_FILE, 7, 1),
    "target_audit": (AUDIT_FILE, 14, 2),
}

MODEL_STATUS_KEYS = {
    "experiment_status",
    "g1_status",
    "g2_status",
    "core_gate_status",
    "core_status",
    "scientific_go_stop_status",
    "scientific_verdict_status",
    "model_dependent_status",
}

CPU_ONLY_TRUE_AUTHORIZATIONS = {
    "construct_cpu_only_task_rows_candidate_panels_manifests_and_seals",
    "cpu_schema_hash_lineage_overlap_and_bijection_checks",
    "cpu_clean_oracle_and_verifier_fpr_checks_after_human_review",
    "human_review_required",
}

FORBIDDEN_PROVENANCE_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:h[\s_-]*id|sqlite|synthetic)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
PROVENANCE_KEY_RE = re.compile(
    r"(?:source|provenance|origin|dataset|path|generator|oracle|lineage)",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DFA7_TRANSITIONS = {
    "x": (2, 5, 1, 6, 0, 3, 4),
    "y": (4, 0, 6, 2, 5, 1, 3),
}


class DuplicateKeyError(ValueError):
    """Raised when JSON contains a duplicate object key."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def record_sha256(record: Mapping[str, Any]) -> str:
    payload = dict(record)
    payload.pop("record_sha256", None)
    return sha256_bytes(canonical_json_bytes(payload))


class Validator:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[dict[str, str]] = []
        self.counts: dict[str, Any] = {
            "mapping_stacks": 0,
            "source_rows": 0,
            "target_calibration_rows": 0,
            "target_audit_rows": 0,
            "prompt_rows_total": 0,
            "manifest_candidate_records": 0,
        }
        self.metrics: dict[str, Any] = {
            "shared_online_fpr": None,
            "local_online_fpr": None,
        }

    def error(self, code: str, path: str, message: str) -> None:
        self.errors.append({"code": code, "path": path, "message": message})

    def _read_json(self, filename: str) -> Any | None:
        path = self.root / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.error("file_read_error", filename, str(exc))
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.error("invalid_utf8", filename, str(exc))
            return None
        try:
            return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
            self.error("invalid_json", filename, str(exc))
            return None

    def _read_jsonl(self, filename: str) -> list[dict[str, Any]] | None:
        path = self.root / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.error("file_read_error", filename, str(exc))
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.error("invalid_utf8", filename, str(exc))
            return None

        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                self.error(
                    "blank_jsonl_line",
                    f"{filename}:{line_number}",
                    "blank lines are forbidden in canonical JSONL",
                )
                continue
            try:
                value = json.loads(
                    line, object_pairs_hook=_reject_duplicate_keys
                )
            except (json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
                self.error(
                    "invalid_jsonl",
                    f"{filename}:{line_number}",
                    str(exc),
                )
                continue
            if not isinstance(value, dict):
                self.error(
                    "jsonl_record_not_object",
                    f"{filename}:{line_number}",
                    "each JSONL record must be an object",
                )
                continue
            rows.append(value)
        return rows

    def _require_fields(
        self, value: Any, required: Iterable[str], path: str
    ) -> bool:
        if not isinstance(value, dict):
            self.error("expected_object", path, "expected a JSON object")
            return False
        missing = sorted(set(required) - set(value))
        if missing:
            self.error(
                "missing_fields",
                path,
                f"missing required fields: {', '.join(missing)}",
            )
            return False
        return True

    def _check_status_and_authorization(self, value: Any, path: str) -> None:
        """Recursively protect human/model status and authorization boundaries."""

        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                key_lower = key.lower()

                if key == "human_review_status":
                    if child != "PENDING_HUMAN_REVIEW":
                        self.error(
                            "human_review_not_pending",
                            child_path,
                            "human review must remain PENDING_HUMAN_REVIEW; "
                            "machine or AI checks cannot set PASS",
                        )
                elif "human" in key_lower and "status" in key_lower:
                    if isinstance(child, str) and "PASS" in child.upper():
                        self.error(
                            "human_review_claims_pass",
                            child_path,
                            "human-review status must not claim PASS",
                        )

                if key in MODEL_STATUS_KEYS and child != "NOT_RUN":
                    self.error(
                        "model_dependent_not_not_run",
                        child_path,
                        "model-dependent quantities and gates must equal NOT_RUN",
                    )

                if key == "authorization":
                    self._check_authorization_tree(child, child_path)
                elif key_lower.endswith("_authorized") and child is not False:
                    self.error(
                        "authorization_not_false",
                        child_path,
                        "authorization flags must be the JSON boolean false",
                    )

                self._check_status_and_authorization(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._check_status_and_authorization(
                    child, f"{path}[{index}]"
                )

    def _check_authorization_tree(
        self, value: Any, path: str, leaf_name: str | None = None
    ) -> None:
        if isinstance(value, dict):
            if not value:
                self.error(
                    "authorization_empty",
                    path,
                    "authorization mapping must enumerate its denied actions",
            )
            for key, child in value.items():
                self._check_authorization_tree(
                    child, f"{path}.{key}", leaf_name=key
                )
            return
        if isinstance(value, list):
            if not value:
                self.error(
                    "authorization_empty",
                    path,
                    "authorization list must not be empty",
            )
            for index, child in enumerate(value):
                self._check_authorization_tree(
                    child, f"{path}[{index}]", leaf_name=leaf_name
                )
            return
        expected = True if leaf_name in CPU_ONLY_TRUE_AUTHORIZATIONS else False
        if value is not expected:
            self.error(
                "authorization_not_false",
                path,
                (
                    "only the four explicit CPU construction/check and human "
                    "review permissions may be true; every model/run "
                    "authorization must be the JSON boolean false"
                ),
            )

    def _check_forbidden_provenance(self, value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if PROVENANCE_KEY_RE.search(key):
                    for text in _flatten_strings(child):
                        if FORBIDDEN_PROVENANCE_RE.search(text):
                            self.error(
                                "forbidden_legacy_or_synthetic_source",
                                child_path,
                                "old H-ID, SQLite, and synthetic sources are "
                                "forbidden for these scientific rows",
                            )
                            break
                self._check_forbidden_provenance(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._check_forbidden_provenance(
                    child, f"{path}[{index}]"
                )

    def _validate_protocol(self, protocol: Any) -> None:
        path = PROTOCOL_FILE
        if not self._require_fields(
            protocol,
            (
                "schema_version",
                "protocol_status",
                "experiment_status",
                "scientific_evidence",
                "run_eligible",
                "human_review_status",
                "authorization",
                "tasks",
                "task_pairs",
                "mappings",
                "mapping_stacks",
            ),
            path,
        ):
            return
        assert isinstance(protocol, dict)
        expected_scalar = {
            "schema_version": "p4-r1-real-protocol-v2-pre-review",
            "protocol_status": "PRE_REVIEW",
            "experiment_status": "NOT_RUN",
            "scientific_evidence": False,
            "run_eligible": False,
            "human_review_status": "PENDING_HUMAN_REVIEW",
        }
        for key, expected in expected_scalar.items():
            if protocol.get(key) != expected:
                self.error(
                    "protocol_value_mismatch",
                    f"{path}.{key}",
                    f"expected {expected!r}, got {protocol.get(key)!r}",
                )

        expected_tasks = [
            (
                "MOD7_SUM_V1",
                "task_pair_1",
                "A",
                "z=(a+b) mod 7",
                2,
            ),
            (
                "DFA7_FINAL_V1",
                "task_pair_1",
                "B",
                "z=encoded_final_state_after_full_input",
                3,
            ),
            (
                "MARKED_RANK7_V1",
                "task_pair_2",
                "A",
                "z=zero_based_rank_of_marked_item",
                4,
            ),
            (
                "PAREN_MAX_DEPTH7_V1",
                "task_pair_2",
                "B",
                "z=maximum_nesting_depth-1, with maximum_nesting_depth in 1..7",
                5,
            ),
        ]
        tasks = protocol.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 4:
            self.error(
                "protocol_task_count",
                f"{path}.tasks",
                "protocol must declare exactly the four controlled tasks",
            )
        else:
            for index, expected_task in enumerate(expected_tasks):
                task_id, pair_id, role, formula, shift = expected_task
                task = tasks[index]
                if not isinstance(task, dict):
                    self.error(
                        "protocol_task_not_object",
                        f"{path}.tasks[{index}]",
                        "task declaration must be an object",
                    )
                    continue
                expected_task_fields = {
                    "task_id": task_id,
                    "task_pair_id": pair_id,
                    "task_role": role,
                    "latent_answer_formula": formula,
                    "local_shift_mod7": shift,
                }
                for key, expected_value in expected_task_fields.items():
                    if task.get(key) != expected_value:
                        self.error(
                            "protocol_task_mismatch",
                            f"{path}.tasks[{index}].{key}",
                            f"expected {expected_value!r}, "
                            f"got {task.get(key)!r}",
                        )

        declared_stacks = protocol.get("mapping_stacks")
        declared_stack_ids = (
            [item.get("stack_id") for item in declared_stacks]
            if isinstance(declared_stacks, list)
            and all(isinstance(item, dict) for item in declared_stacks)
            else None
        )
        if declared_stack_ids != list(EXPECTED_STACK_IDS):
            self.error(
                "protocol_stack_order",
                f"{path}.mapping_stacks",
                "protocol must declare the eight exact stacks in frozen order",
            )

        authorization = protocol.get("authorization")
        required_denials = {
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
        }
        if not isinstance(authorization, dict):
            self.error(
                "protocol_authorization_not_object",
                f"{path}.authorization",
                "authorization must explicitly enumerate denied actions",
            )
        else:
            missing = sorted(required_denials - set(authorization))
            if missing:
                self.error(
                    "protocol_authorization_missing",
                    f"{path}.authorization",
                    f"missing explicit authorization denials: "
                    f"{', '.join(missing)}",
                )

    def _validate_mappings(
        self, rows: list[dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]]:
        if rows is None:
            return {}
        self.counts["mapping_stacks"] = len(rows)
        if len(rows) != 8:
            self.error(
                "mapping_stack_count",
                MAPPING_FILE,
                f"expected exactly 8 mapping stacks, got {len(rows)}",
            )

        observed_order = [row.get("stack_id") for row in rows]
        if observed_order != list(EXPECTED_STACK_IDS):
            self.error(
                "mapping_stack_order",
                MAPPING_FILE,
                "stack IDs and JSONL order must exactly equal "
                + ", ".join(EXPECTED_STACK_IDS),
            )

        by_stack: dict[str, dict[str, Any]] = {}
        required = (
            "stack_id",
            "pair_id",
            "mapping_id",
            "mirror_id",
            "direction",
            "source_task_id",
            "target_task_id",
            "codebook",
        )
        for index, row in enumerate(rows, start=1):
            path = f"{MAPPING_FILE}:{index}"
            if not self._require_fields(row, required, path):
                continue
            stack_id = row["stack_id"]
            if not isinstance(stack_id, str):
                self.error(
                    "invalid_stack_id", f"{path}.stack_id", "must be a string"
                )
                continue
            if stack_id in by_stack:
                self.error(
                    "duplicate_stack_id",
                    f"{path}.stack_id",
                    f"duplicate stack_id {stack_id!r}",
                )
                continue
            by_stack[stack_id] = row
            expected = EXPECTED_STACK_META.get(stack_id)
            if expected is None:
                self.error(
                    "unknown_stack_id",
                    f"{path}.stack_id",
                    f"unexpected stack_id {stack_id!r}",
                )
                continue

            for key in (
                "pair_id",
                "mapping_id",
                "direction",
                "source_task_id",
                "target_task_id",
            ):
                if row.get(key) != expected[key]:
                    self.error(
                        "mapping_metadata_mismatch",
                        f"{path}.{key}",
                        f"expected {expected[key]!r}, got {row.get(key)!r}",
                    )

            mirror_id = row.get("mirror_id")
            valid_mirror_ids = {
                expected["direction"],
                "mirror_0"
                if expected["direction"] == "A_TO_B"
                else "mirror_1",
            }
            if mirror_id not in valid_mirror_ids:
                self.error(
                    "mirror_direction_mismatch",
                    f"{path}.mirror_id",
                    f"mirror_id must identify {expected['direction']}",
                )

            normalized_codebook = _normalize_codebook(
                row.get("codebook"), expected["task_a"], expected["task_b"]
            )
            if normalized_codebook != EXPECTED_CODEBOOKS[expected["mapping_id"]]:
                self.error(
                    "codebook_mismatch",
                    f"{path}.codebook",
                    "codebook does not implement the exact preregistered "
                    f"{expected['mapping_id']} permutations",
                )
            else:
                row["_normalized_codebook"] = normalized_codebook

            self._check_forbidden_provenance(row, path)
            self._check_status_and_authorization(row, path)

        observed_pairs = {row.get("pair_id") for row in rows}
        observed_mappings = {row.get("mapping_id") for row in rows}
        observed_directions = {row.get("direction") for row in rows}
        observed_tasks = {
            task
            for row in rows
            for task in (
                row.get("source_task_id"),
                row.get("target_task_id"),
            )
        }
        if observed_pairs != set(PAIR_DEFINITIONS):
            self.error(
                "pair_set_mismatch",
                MAPPING_FILE,
                "must contain exactly the two preregistered task pairs",
            )
        if observed_mappings != set(EXPECTED_CODEBOOKS):
            self.error(
                "mapping_set_mismatch",
                MAPPING_FILE,
                "must contain exactly mapping_0 and mapping_1",
            )
        if observed_directions != {"A_TO_B", "B_TO_A"}:
            self.error(
                "direction_set_mismatch",
                MAPPING_FILE,
                "must contain exactly A_TO_B and B_TO_A",
            )
        if observed_tasks != set(LOCAL_SHIFT_BY_TASK):
            self.error(
                "task_set_mismatch",
                MAPPING_FILE,
                "must contain exactly the four preregistered tasks",
            )
        return by_stack

    def _validate_rows(
        self,
        rows: list[dict[str, Any]] | None,
        split: str,
        mappings: Mapping[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if rows is None:
            return []
        filename, per_stack, per_class = ROW_SPLITS[split]
        self.counts[f"{split}_rows"] = len(rows)
        expected_total = 8 * per_stack
        if len(rows) != expected_total:
            self.error(
                "row_count",
                filename,
                f"expected exactly {expected_total} rows "
                f"({per_stack} per stack), got {len(rows)}",
            )

        required = (
            "stack_id",
            "pair_id",
            "mapping_id",
            "direction",
            "task_id",
            "split",
            "row_id",
            "seed_namespace",
            "row_seed",
            "generator_id",
            "oracle_id",
            "task_input",
            "prompt",
            "prompt_sha256",
            "canonical_class",
            "candidates",
            "candidate_sha256s",
            "gold_index",
            "shared_wrong_index",
            "local_wrong_index",
            "reward_vectors",
            "lineage_id",
            "record_sha256",
            "model_dependent_status",
            "human_review_status",
        )
        stack_counts: Counter[str] = Counter()
        class_counts: Counter[tuple[str, int]] = Counter()

        for index, row in enumerate(rows, start=1):
            path = f"{filename}:{index}"
            if not self._require_fields(row, required, path):
                continue

            stack_id = row.get("stack_id")
            stack = mappings.get(stack_id)
            if stack is None:
                self.error(
                    "row_unknown_stack",
                    f"{path}.stack_id",
                    f"row references unknown stack {stack_id!r}",
                )
                continue
            expected = EXPECTED_STACK_META[stack_id]
            stack_counts[stack_id] += 1

            if row.get("split") != split:
                self.error(
                    "split_mismatch",
                    f"{path}.split",
                    f"expected {split!r}, got {row.get('split')!r}",
                )
            for key in ("pair_id", "mapping_id", "direction"):
                if row.get(key) != stack.get(key):
                    self.error(
                        "row_stack_metadata_mismatch",
                        f"{path}.{key}",
                        f"does not match stack declaration {stack.get(key)!r}",
                    )

            expected_task = (
                expected["source_task_id"]
                if split == "source"
                else expected["target_task_id"]
            )
            if row.get("task_id") != expected_task:
                self.error(
                    "row_task_mismatch",
                    f"{path}.task_id",
                    f"{split} row must use task {expected_task!r}",
                )

            z = row.get("canonical_class")
            if isinstance(z, bool) or not isinstance(z, int) or not 0 <= z < 7:
                self.error(
                    "invalid_canonical_class",
                    f"{path}.canonical_class",
                    "canonical_class must be an integer in [0, 6]",
                )
                continue
            class_counts[(stack_id, z)] += 1
            computed_z = self._validate_task_input(
                row.get("task_id"), row.get("task_input"), path
            )
            if computed_z is not None and computed_z != z:
                self.error(
                    "oracle_class_mismatch",
                    f"{path}.canonical_class",
                    f"independent oracle computes z={computed_z}, "
                    f"but row declares z={z}",
                )

            candidates = row.get("candidates")
            if candidates != list(CANDIDATES):
                self.error(
                    "candidate_order_mismatch",
                    f"{path}.candidates",
                    "candidates must be exactly FINAL=K0 through FINAL=K6 "
                    "in that order",
                )
            expected_candidate_hashes = [
                sha256_bytes(candidate.encode("utf-8"))
                for candidate in CANDIDATES
            ]
            if row.get("candidate_sha256s") != expected_candidate_hashes:
                self.error(
                    "candidate_hash_mismatch",
                    f"{path}.candidate_sha256s",
                    "candidate hashes must match the exact candidate bytes",
                )

            prompt = row.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                self.error(
                    "invalid_prompt",
                    f"{path}.prompt",
                    "prompt must be a non-empty string",
                )
            elif row.get("prompt_sha256") != sha256_bytes(
                prompt.encode("utf-8")
            ):
                self.error(
                    "prompt_hash_mismatch",
                    f"{path}.prompt_sha256",
                    "prompt_sha256 does not match the UTF-8 prompt bytes",
                )

            codebook = stack.get("_normalized_codebook")
            if isinstance(codebook, dict):
                role = (
                    "A"
                    if row.get("task_id") == expected["task_a"]
                    else "B"
                )
                mapping = codebook[role]
                task_id = row.get("task_id")
                local_shift = LOCAL_SHIFT_BY_TASK.get(task_id)
                if local_shift is None:
                    self.error(
                        "unknown_task_local_rule",
                        f"{path}.task_id",
                        "no preregistered local transform for this task",
                    )
                else:
                    gold_index = mapping[z]
                    shared_index = mapping[(z + 1) % 7]
                    local_index = mapping[(z + local_shift) % 7]
                    expected_indices = {
                        "gold_index": gold_index,
                        "shared_wrong_index": shared_index,
                        "local_wrong_index": local_index,
                    }
                    for key, expected_index in expected_indices.items():
                        if row.get(key) != expected_index:
                            self.error(
                                "candidate_role_index_mismatch",
                                f"{path}.{key}",
                                f"expected {expected_index}, "
                                f"got {row.get(key)!r}",
                            )
                    observed_indices = {
                        _hashable(row.get("gold_index")),
                        _hashable(row.get("shared_wrong_index")),
                        _hashable(row.get("local_wrong_index")),
                    }
                    if len(observed_indices) != 3:
                        self.error(
                            "candidate_roles_not_distinct",
                            path,
                            "declared gold, shared wrong, and local wrong "
                            "indices must be pairwise distinct",
                        )
                    if len({gold_index, shared_index, local_index}) != 3:
                        self.error(
                            "candidate_roles_not_distinct",
                            path,
                            "gold, shared wrong, and local wrong must be "
                            "pairwise distinct",
                        )
                    self._validate_reward_vectors(
                        row.get("reward_vectors"),
                        gold_index,
                        shared_index,
                        local_index,
                        f"{path}.reward_vectors",
                    )

            if row.get("record_sha256") != record_sha256(row):
                self.error(
                    "record_hash_mismatch",
                    f"{path}.record_sha256",
                    "record_sha256 does not match canonical row content",
                )

            self._check_forbidden_provenance(row, path)
            self._check_status_and_authorization(row, path)

        for stack_id in EXPECTED_STACK_IDS:
            if stack_counts[stack_id] != per_stack:
                self.error(
                    "per_stack_row_count",
                    filename,
                    f"{stack_id} has {stack_counts[stack_id]} rows; "
                    f"expected {per_stack}",
                )
            for z in range(7):
                if class_counts[(stack_id, z)] != per_class:
                    self.error(
                        "class_balance",
                        filename,
                        f"{stack_id} class {z} has "
                        f"{class_counts[(stack_id, z)]} rows; "
                        f"expected {per_class}",
                    )
        return rows

    def _validate_task_input(
        self, task_id: Any, task_input: Any, path: str
    ) -> int | None:
        input_path = f"{path}.task_input"
        if not isinstance(task_input, dict):
            self.error(
                "task_input_not_object",
                input_path,
                "task_input must be an object for independent oracle checking",
            )
            return None

        if task_id == "MOD7_SUM_V1":
            if set(task_input) != {"a", "b"}:
                self.error(
                    "mod7_input_schema",
                    input_path,
                    "MOD7_SUM_V1 input must contain exactly integer fields a,b",
                )
                return None
            a, b = task_input.get("a"), task_input.get("b")
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 20
                for value in (a, b)
            ):
                self.error(
                    "mod7_input_range",
                    input_path,
                    "a and b must each be integers in [0, 20]",
                )
                return None
            return (a + b) % 7

        if task_id == "DFA7_FINAL_V1":
            if set(task_input) != {
                "start_state",
                "sequence",
                "transition_table",
            }:
                self.error(
                    "dfa_input_schema",
                    input_path,
                    "DFA7 input must contain exactly start_state, sequence, "
                    "and transition_table",
                )
                return None
            start = task_input.get("start_state")
            sequence = task_input.get("sequence")
            table = _normalize_dfa_table(task_input.get("transition_table"))
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or not 0 <= start < 7
            ):
                self.error(
                    "dfa_start_state",
                    f"{input_path}.start_state",
                    "start_state must be an integer in [0, 6]",
                )
                return None
            if isinstance(sequence, str):
                symbols = list(sequence)
            elif isinstance(sequence, list) and all(
                isinstance(symbol, str) for symbol in sequence
            ):
                symbols = sequence
            else:
                symbols = []
            if not 2 <= len(symbols) <= 6 or any(
                symbol not in {"x", "y"} for symbol in symbols
            ):
                self.error(
                    "dfa_sequence",
                    f"{input_path}.sequence",
                    "sequence must contain only lowercase x/y and have "
                    "length 2..6",
                )
                return None
            if table != DFA7_TRANSITIONS:
                self.error(
                    "dfa_transition_table",
                    f"{input_path}.transition_table",
                    "transition table must exactly equal the preregistered "
                    "fixed x/y table",
                )
                return None
            state = start
            for symbol in symbols:
                state = table[symbol][state]
            return state

        if task_id == "MARKED_RANK7_V1":
            if set(task_input) != {"values", "marked_index"}:
                self.error(
                    "rank_input_schema",
                    input_path,
                    "MARKED_RANK7 input must contain exactly values and "
                    "marked_index",
                )
                return None
            values = task_input.get("values")
            marked_index = task_input.get("marked_index")
            if (
                not isinstance(values, list)
                or len(values) != 7
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in values
                )
                or len(set(values)) != 7
            ):
                self.error(
                    "rank_values",
                    f"{input_path}.values",
                    "values must contain seven distinct integers",
                )
                return None
            if (
                isinstance(marked_index, bool)
                or not isinstance(marked_index, int)
                or not 0 <= marked_index < 7
            ):
                self.error(
                    "rank_marked_index",
                    f"{input_path}.marked_index",
                    "marked_index must be an integer in [0, 6]",
                )
                return None
            marked_value = values[marked_index]
            return sum(value < marked_value for value in values)

        if task_id == "PAREN_MAX_DEPTH7_V1":
            if set(task_input) != {"parentheses"}:
                self.error(
                    "paren_input_schema",
                    input_path,
                    "PAREN_MAX_DEPTH7 input must contain exactly parentheses",
                )
                return None
            text = task_input.get("parentheses")
            if (
                not isinstance(text, str)
                or not text
                or any(character not in "()" for character in text)
            ):
                self.error(
                    "paren_character_set",
                    f"{input_path}.parentheses",
                    "parentheses must be a non-empty string containing only "
                    "'(' and ')'",
                )
                return None
            depth = 0
            maximum_depth = 0
            for character in text:
                depth += 1 if character == "(" else -1
                if depth < 0:
                    self.error(
                        "paren_not_balanced",
                        f"{input_path}.parentheses",
                        "prefix depth fell below zero",
                    )
                    return None
                maximum_depth = max(maximum_depth, depth)
            if depth != 0:
                self.error(
                    "paren_not_balanced",
                    f"{input_path}.parentheses",
                    "final parenthesis depth must be zero",
                )
                return None
            if not 1 <= maximum_depth <= 7:
                self.error(
                    "paren_depth_range",
                    f"{input_path}.parentheses",
                    "maximum depth must be in [1, 7]",
                )
                return None
            return maximum_depth - 1

        self.error(
            "unknown_task_id",
            f"{path}.task_id",
            f"unknown controlled task {task_id!r}",
        )
        return None

    def _validate_reward_vectors(
        self,
        reward_vectors: Any,
        gold_index: int,
        shared_index: int,
        local_index: int,
        path: str,
    ) -> None:
        if not isinstance(reward_vectors, dict):
            self.error(
                "reward_vectors_not_object",
                path,
                "reward_vectors must be an object",
            )
            return
        if set(reward_vectors) != set(ARMS):
            self.error(
                "reward_arm_set_mismatch",
                path,
                "reward_vectors must contain exactly the five preregistered arms",
            )
            return

        clean = [0] * 7
        clean[gold_index] = 1
        shared = clean.copy()
        shared[shared_index] = 1
        local = clean.copy()
        local[local_index] = 1
        expected = {
            "clean": clean,
            "shared_leaky": shared,
            "local_leaky": local,
            "shared_frozen_probe_control": shared,
            "local_frozen_probe_control": local,
        }
        for arm, vector in expected.items():
            if reward_vectors.get(arm) != vector:
                self.error(
                    "reward_vector_mismatch",
                    f"{path}.{arm}",
                    f"expected {vector!r}, got {reward_vectors.get(arm)!r}",
                )

        for arm, wrong_index in (
            ("shared_leaky", shared_index),
            ("local_leaky", local_index),
        ):
            vector = reward_vectors.get(arm)
            if isinstance(vector, list) and len(vector) == 7:
                accepted_wrong = sum(
                    value == 1
                    for index, value in enumerate(vector)
                    if index != gold_index
                )
                if (
                    accepted_wrong != 1
                    or vector[wrong_index] != 1
                    or not math.isclose(
                        accepted_wrong / 6, 1 / 6, rel_tol=0, abs_tol=0
                    )
                ):
                    self.error(
                        "fpr_not_one_sixth",
                        f"{path}.{arm}",
                        "accepted wrong candidates / six wrong candidates "
                        "must equal exactly 1/6",
                    )

    def _validate_global_disjointness(
        self, rows: Sequence[dict[str, Any]]
    ) -> None:
        unique_fields = (
            "row_id",
            "row_seed",
            "prompt",
            "prompt_sha256",
            "lineage_id",
        )
        for field in unique_fields:
            occurrences: defaultdict[Any, list[str]] = defaultdict(list)
            for row in rows:
                value = row.get(field)
                if value is not None:
                    occurrences[_hashable(value)].append(
                        f"{row.get('stack_id')}/{row.get('split')}/"
                        f"{row.get('row_id')}"
                    )
            for value, locations in occurrences.items():
                if len(locations) > 1:
                    self.error(
                        "cross_stack_split_overlap",
                        field,
                        f"{field} value {value!r} occurs {len(locations)} "
                        "times across rows",
                    )

        namespace_groups: defaultdict[Any, set[tuple[Any, Any]]] = defaultdict(
            set
        )
        observed_groups: set[tuple[Any, Any]] = set()
        for row in rows:
            group = (row.get("stack_id"), row.get("split"))
            observed_groups.add(group)
            namespace_groups[_hashable(row.get("seed_namespace"))].add(group)
        for namespace, groups in namespace_groups.items():
            if len(groups) > 1:
                self.error(
                    "seed_namespace_overlap",
                    "seed_namespace",
                    f"seed namespace {namespace!r} is shared by "
                    f"{len(groups)} stack/split groups",
                )
        if len(namespace_groups) < len(observed_groups):
            self.error(
                "seed_namespace_count",
                "seed_namespace",
                "each stack/split group must have a disjoint seed namespace",
            )

        generator_tasks: defaultdict[Any, set[Any]] = defaultdict(set)
        oracle_tasks: defaultdict[Any, set[Any]] = defaultdict(set)
        for row in rows:
            generator_tasks[_hashable(row.get("generator_id"))].add(
                row.get("task_id")
            )
            oracle_tasks[_hashable(row.get("oracle_id"))].add(
                row.get("task_id")
            )
        for identifier, tasks in generator_tasks.items():
            if len(tasks) > 1:
                self.error(
                    "generator_shared_across_tasks",
                    "generator_id",
                    f"generator {identifier!r} is shared by tasks {sorted(tasks)}",
                )
        for identifier, tasks in oracle_tasks.items():
            if len(tasks) > 1:
                self.error(
                    "oracle_shared_across_tasks",
                    "oracle_id",
                    f"oracle {identifier!r} is shared by tasks {sorted(tasks)}",
                )

    def _validate_reward_manifest(
        self,
        manifest: list[dict[str, Any]] | None,
        rows: Sequence[dict[str, Any]],
    ) -> None:
        if manifest is None:
            return
        rows_by_id: dict[Any, dict[str, Any]] = {}
        for row in rows:
            row_id = row.get("row_id")
            if row_id in rows_by_id:
                continue
            rows_by_id[row_id] = row

        observed: dict[tuple[Any, int], dict[str, Any]] = {}
        candidate_count = 0
        for line_number, record in enumerate(manifest, start=1):
            path = f"{REWARD_FILE}:{line_number}"
            self._check_forbidden_provenance(record, path)
            self._check_status_and_authorization(record, path)
            row_id = record.get("row_id")
            row = rows_by_id.get(row_id)
            if row is None:
                self.error(
                    "manifest_unknown_row",
                    f"{path}.row_id",
                    f"manifest references unknown row {row_id!r}",
                )
                continue

            candidates = _expand_manifest_record(record)
            if candidates is None:
                self.error(
                    "manifest_shape",
                    path,
                    "record must be candidate-level or contain seven nested "
                    "candidate records",
                )
                continue
            if "candidate_index" not in record and len(candidates) != 7:
                self.error(
                    "manifest_prompt_candidate_count",
                    path,
                    f"prompt-level manifest record must contain 7 candidates, "
                    f"got {len(candidates)}",
                )

            for candidate in candidates:
                candidate_count += 1
                candidate_index = candidate.get("candidate_index")
                candidate_path = (
                    f"{path}.candidate[{candidate_index!r}]"
                )
                if (
                    isinstance(candidate_index, bool)
                    or not isinstance(candidate_index, int)
                    or not 0 <= candidate_index < 7
                ):
                    self.error(
                        "manifest_candidate_index",
                        candidate_path,
                        "candidate_index must be an integer in [0, 6]",
                    )
                    continue
                key = (row_id, candidate_index)
                if key in observed:
                    self.error(
                        "manifest_duplicate_candidate",
                        candidate_path,
                        f"duplicate manifest candidate {key!r}",
                    )
                    continue
                observed[key] = candidate

                expected_text = row["candidates"][candidate_index]
                text = candidate.get("candidate_text", candidate.get("candidate"))
                if text != expected_text:
                    self.error(
                        "manifest_candidate_text_mismatch",
                        f"{candidate_path}.candidate_text",
                        f"expected {expected_text!r}, got {text!r}",
                    )
                expected_hash = row["candidate_sha256s"][candidate_index]
                observed_hash = candidate.get(
                    "candidate_sha256", candidate.get("sha256")
                )
                if observed_hash != expected_hash:
                    self.error(
                        "manifest_candidate_hash_mismatch",
                        f"{candidate_path}.candidate_sha256",
                        "manifest candidate hash does not match row asset",
                    )

                rewards = candidate.get(
                    "reward_by_arm", candidate.get("rewards")
                )
                if not isinstance(rewards, dict):
                    self.error(
                        "manifest_rewards_missing",
                        f"{candidate_path}.reward_by_arm",
                        "candidate-level reward_by_arm mapping is required",
                    )
                else:
                    expected_rewards = {
                        arm: row["reward_vectors"][arm][candidate_index]
                        for arm in ARMS
                    }
                    if rewards != expected_rewards:
                        self.error(
                            "manifest_rewards_mismatch",
                            f"{candidate_path}.reward_by_arm",
                            f"expected {expected_rewards!r}, got {rewards!r}",
                        )

        expected_keys = {
            (row_id, candidate_index)
            for row_id in rows_by_id
            for candidate_index in range(7)
        }
        observed_keys = set(observed)
        missing = expected_keys - observed_keys
        extra = observed_keys - expected_keys
        if missing:
            self.error(
                "manifest_candidates_missing",
                REWARD_FILE,
                f"missing {len(missing)} of the required candidate records",
            )
        if extra:
            self.error(
                "manifest_candidates_extra",
                REWARD_FILE,
                f"contains {len(extra)} unexpected candidate records",
            )
        self.counts["manifest_candidate_records"] = candidate_count
        if candidate_count != 1960:
            self.error(
                "manifest_candidate_total",
                REWARD_FILE,
                f"expected exactly 1,960 candidate records, got "
                f"{candidate_count}",
            )

    def _validate_g1(self, g1: Any, rows: Sequence[dict[str, Any]]) -> None:
        if not self._require_fields(
            g1,
            (
                "schema_version",
                "g1_status",
                "model_dependent_status",
                "human_review_status",
                "shared_online_fpr",
                "local_online_fpr",
                "authorization",
            ),
            G1_FILE,
        ):
            return
        assert isinstance(g1, dict)
        if g1.get("schema_version") != "g1-opportunity-audit-v1":
            self.error(
                "g1_schema_version",
                f"{G1_FILE}.schema_version",
                "expected g1-opportunity-audit-v1",
            )
        if not _ratio_is_one_sixth(g1.get("shared_online_fpr")):
            self.error(
                "g1_shared_fpr",
                f"{G1_FILE}.shared_online_fpr",
                "reported shared online FPR must be exactly 1/6",
            )
        if not _ratio_is_one_sixth(g1.get("local_online_fpr")):
            self.error(
                "g1_local_fpr",
                f"{G1_FILE}.local_online_fpr",
                "reported local online FPR must be exactly 1/6",
            )

        shared_wrong = 0
        local_wrong = 0
        total_wrong = 0
        for row in rows:
            gold = row.get("gold_index")
            rewards = row.get("reward_vectors")
            if (
                not isinstance(gold, int)
                or not isinstance(rewards, dict)
                or not all(
                    isinstance(rewards.get(arm), list)
                    and len(rewards[arm]) == 7
                    for arm in ("shared_leaky", "local_leaky")
                )
            ):
                continue
            total_wrong += 6
            shared_wrong += sum(
                rewards["shared_leaky"][i] == 1
                for i in range(7)
                if i != gold
            )
            local_wrong += sum(
                rewards["local_leaky"][i] == 1
                for i in range(7)
                if i != gold
            )
        if total_wrong:
            self.metrics["shared_online_fpr"] = {
                "numerator": shared_wrong,
                "denominator": total_wrong,
            }
            self.metrics["local_online_fpr"] = {
                "numerator": local_wrong,
                "denominator": total_wrong,
            }
            if shared_wrong * 6 != total_wrong:
                self.error(
                    "computed_shared_fpr",
                    SOURCE_FILE,
                    f"computed shared FPR is {shared_wrong}/{total_wrong}, "
                    "not 1/6",
                )
            if local_wrong * 6 != total_wrong:
                self.error(
                    "computed_local_fpr",
                    SOURCE_FILE,
                    f"computed local FPR is {local_wrong}/{total_wrong}, "
                    "not 1/6",
                )

    def _validate_audit_seal(
        self, receipt: Any, audit_rows: Sequence[dict[str, Any]]
    ) -> None:
        required = (
            "schema_version",
            "seal_status",
            "audit_filename",
            "audit_file_sha256",
            "audit_record_count",
            "hash_algorithm",
            "chain_formula",
            "hash_chain_genesis",
            "record_hashes",
            "hash_chain",
            "hash_chain_head",
            "sealed_before_model_action",
            "audit_used_for_design",
            "audit_used_for_matching",
            "audit_used_for_tuning",
            "audit_used_for_debug",
            "human_review_status",
            "model_dependent_status",
            "authorization",
        )
        if not self._require_fields(receipt, required, SEAL_FILE):
            return
        assert isinstance(receipt, dict)
        expected_scalars = {
            "schema_version": "target-audit-seal-receipt-v1",
            "seal_status": "SEALED_PRE_MODEL_ACTION",
            "audit_filename": AUDIT_FILE,
            "audit_record_count": 112,
            "hash_algorithm": "sha256",
            "chain_formula": (
                "sha256(previous_chain_sha256 || record_sha256)"
            ),
            "sealed_before_model_action": True,
            "audit_used_for_design": False,
            "audit_used_for_matching": False,
            "audit_used_for_tuning": False,
            "audit_used_for_debug": False,
            "human_review_status": "PENDING_HUMAN_REVIEW",
            "model_dependent_status": "NOT_RUN",
        }
        for key, expected in expected_scalars.items():
            if receipt.get(key) != expected:
                self.error(
                    "seal_value_mismatch",
                    f"{SEAL_FILE}.{key}",
                    f"expected {expected!r}, got {receipt.get(key)!r}",
                )

        try:
            audit_bytes = (self.root / AUDIT_FILE).read_bytes()
        except OSError as exc:
            self.error("file_read_error", AUDIT_FILE, str(exc))
            return
        expected_file_hash = sha256_bytes(audit_bytes)
        if receipt.get("audit_file_sha256") != expected_file_hash:
            self.error(
                "audit_file_hash_mismatch",
                f"{SEAL_FILE}.audit_file_sha256",
                "receipt does not match the exact audit JSONL bytes",
            )
        if receipt.get("audit_record_count") != len(audit_rows):
            self.error(
                "audit_seal_count_mismatch",
                f"{SEAL_FILE}.audit_record_count",
                f"receipt count does not match {len(audit_rows)} parsed rows",
            )

        expected_record_hashes = [
            row.get("record_sha256") for row in audit_rows
        ]
        if receipt.get("record_hashes") != expected_record_hashes:
            self.error(
                "audit_record_hash_list_mismatch",
                f"{SEAL_FILE}.record_hashes",
                "record hash list must match audit JSONL order exactly",
            )

        genesis = receipt.get("hash_chain_genesis")
        if not isinstance(genesis, str) or not SHA256_RE.fullmatch(genesis):
            self.error(
                "audit_chain_genesis_invalid",
                f"{SEAL_FILE}.hash_chain_genesis",
                "genesis must be exactly 64 lowercase hexadecimal characters",
            )
            return
        expected_chain: list[str] = []
        previous = genesis
        for row_hash in expected_record_hashes:
            if not isinstance(row_hash, str) or not SHA256_RE.fullmatch(
                row_hash
            ):
                self.error(
                    "audit_record_hash_invalid",
                    AUDIT_FILE,
                    "audit record hashes must be lowercase SHA-256 strings",
                )
                return
            previous = sha256_bytes((previous + row_hash).encode("ascii"))
            expected_chain.append(previous)
        if receipt.get("hash_chain") != expected_chain:
            self.error(
                "audit_hash_chain_mismatch",
                f"{SEAL_FILE}.hash_chain",
                "hash chain does not match the ordered audit record hashes",
            )
        expected_head = expected_chain[-1] if expected_chain else genesis
        if receipt.get("hash_chain_head") != expected_head:
            self.error(
                "audit_chain_head_mismatch",
                f"{SEAL_FILE}.hash_chain_head",
                "hash_chain_head does not match the recomputed chain",
            )

    def _validate_prereg(self, prereg: Any) -> None:
        required = (
            "schema_version",
            "preregistration_status",
            "experiment_status",
            "model_dependent_status",
            "human_review_status",
            "authorization",
        )
        if not self._require_fields(prereg, required, PREREG_FILE):
            return
        assert isinstance(prereg, dict)
        expected = {
            "schema_version": "randomization-model-env-prereg-v1",
            "preregistration_status": "PRE_REVIEW",
            "experiment_status": "NOT_RUN",
            "model_dependent_status": "NOT_RUN",
            "human_review_status": "PENDING_HUMAN_REVIEW",
        }
        for key, expected_value in expected.items():
            if prereg.get(key) != expected_value:
                self.error(
                    "prereg_value_mismatch",
                    f"{PREREG_FILE}.{key}",
                    f"expected {expected_value!r}, got {prereg.get(key)!r}",
                )

    def run(self) -> dict[str, Any]:
        if not self.root.exists() or not self.root.is_dir():
            self.error(
                "root_not_directory",
                str(self.root),
                "asset root does not exist or is not a directory",
            )
            return self.report()

        for filename in REQUIRED_FILES:
            path = self.root / filename
            if not path.is_file():
                self.error(
                    "missing_required_file",
                    filename,
                    "required real-asset file is missing",
                )

        protocol = (
            self._read_json(PROTOCOL_FILE)
            if (self.root / PROTOCOL_FILE).is_file()
            else None
        )
        mapping_rows = (
            self._read_jsonl(MAPPING_FILE)
            if (self.root / MAPPING_FILE).is_file()
            else None
        )
        source_rows = (
            self._read_jsonl(SOURCE_FILE)
            if (self.root / SOURCE_FILE).is_file()
            else None
        )
        calibration_rows = (
            self._read_jsonl(CALIBRATION_FILE)
            if (self.root / CALIBRATION_FILE).is_file()
            else None
        )
        audit_rows = (
            self._read_jsonl(AUDIT_FILE)
            if (self.root / AUDIT_FILE).is_file()
            else None
        )
        reward_manifest = (
            self._read_jsonl(REWARD_FILE)
            if (self.root / REWARD_FILE).is_file()
            else None
        )
        g1 = (
            self._read_json(G1_FILE)
            if (self.root / G1_FILE).is_file()
            else None
        )
        receipt = (
            self._read_json(SEAL_FILE)
            if (self.root / SEAL_FILE).is_file()
            else None
        )
        prereg = (
            self._read_json(PREREG_FILE)
            if (self.root / PREREG_FILE).is_file()
            else None
        )

        if protocol is not None:
            self._validate_protocol(protocol)
            self._check_status_and_authorization(protocol, PROTOCOL_FILE)
            self._check_forbidden_provenance(protocol, PROTOCOL_FILE)

        mappings = self._validate_mappings(mapping_rows)
        all_rows: list[dict[str, Any]] = []
        for rows, split in (
            (source_rows, "source"),
            (calibration_rows, "target_calibration"),
            (audit_rows, "target_audit"),
        ):
            validated = self._validate_rows(rows, split, mappings)
            all_rows.extend(validated)
        self.counts["prompt_rows_total"] = len(all_rows)
        if len(all_rows) != 280:
            self.error(
                "prompt_row_total",
                "rows",
                f"expected exactly 280 prompt rows, got {len(all_rows)}",
            )
        self._validate_global_disjointness(all_rows)

        self._validate_reward_manifest(reward_manifest, all_rows)

        if g1 is not None:
            self._validate_g1(g1, source_rows or [])
            self._check_status_and_authorization(g1, G1_FILE)
            self._check_forbidden_provenance(g1, G1_FILE)
        if receipt is not None:
            self._validate_audit_seal(receipt, audit_rows or [])
            self._check_status_and_authorization(receipt, SEAL_FILE)
            self._check_forbidden_provenance(receipt, SEAL_FILE)
        if prereg is not None:
            self._validate_prereg(prereg)
            self._check_status_and_authorization(prereg, PREREG_FILE)
            self._check_forbidden_provenance(prereg, PREREG_FILE)

        return self.report()

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "root": str(self.root.resolve()),
            "valid": not self.errors,
            "error_count": len(self.errors),
            "errors": self.errors,
            "counts": self.counts,
            "metrics": self.metrics,
            "model_execution_performed": False,
        }


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _flatten_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_strings(child)


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return canonical_json_bytes(value).decode("utf-8", errors="replace")
    return value


def _label_to_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value < 7:
        return value
    if isinstance(value, str):
        match = re.fullmatch(r"(?:FINAL=)?K([0-6])", value)
        if match:
            return int(match.group(1))
    return None


def _normalize_codebook(
    value: Any, task_a: str, task_b: str
) -> dict[str, tuple[int, ...]] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, tuple[int, ...]] = {}
    for role, task_id in (("A", task_a), ("B", task_b)):
        raw = value.get(role, value.get(task_id))
        if isinstance(raw, dict):
            try:
                raw = [raw[str(z)] for z in range(7)]
            except KeyError:
                try:
                    raw = [raw[z] for z in range(7)]
                except KeyError:
                    return None
        if not isinstance(raw, list) or len(raw) != 7:
            return None
        converted = tuple(_label_to_index(item) for item in raw)
        if any(item is None for item in converted):
            return None
        normalized[role] = converted  # type: ignore[assignment]
    return normalized


def _normalize_dfa_table(value: Any) -> dict[str, tuple[int, ...]] | None:
    if isinstance(value, dict):
        if set(value) != {"x", "y"}:
            return None
        result: dict[str, tuple[int, ...]] = {}
        for symbol in ("x", "y"):
            row = value.get(symbol)
            if (
                not isinstance(row, list)
                or len(row) != 7
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, int)
                    or not 0 <= item < 7
                    for item in row
                )
            ):
                return None
            result[symbol] = tuple(row)
        return result
    if (
        isinstance(value, list)
        and len(value) == 7
        and all(isinstance(row, dict) for row in value)
    ):
        ordered: list[dict[str, Any] | None] = [None] * 7
        for row in value:
            state = row.get("state")
            if (
                isinstance(state, bool)
                or not isinstance(state, int)
                or not 0 <= state < 7
                or ordered[state] is not None
            ):
                return None
            ordered[state] = row
        if any(row is None for row in ordered):
            return None
        result = {}
        for symbol in ("x", "y"):
            values = [
                row.get(symbol) for row in ordered if row is not None
            ]
            if any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item < 7
                for item in values
            ):
                return None
            result[symbol] = tuple(values)
        return result
    return None


def _expand_manifest_record(
    record: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    if "candidate_index" in record:
        return [dict(record)]

    nested = record.get("candidate_records")
    if nested is None:
        nested = record.get("candidates")
    if not isinstance(nested, list):
        return None

    if all(isinstance(item, dict) for item in nested):
        expanded = []
        for index, item in enumerate(nested):
            candidate = dict(item)
            candidate.setdefault("candidate_index", index)
            expanded.append(candidate)
        return expanded

    if all(isinstance(item, str) for item in nested):
        hashes = record.get("candidate_sha256s")
        vectors = record.get("reward_vectors")
        if (
            not isinstance(hashes, list)
            or not isinstance(vectors, dict)
            or not all(
                isinstance(vectors.get(arm), list)
                and len(vectors[arm]) == len(nested)
                for arm in ARMS
            )
        ):
            return None
        return [
            {
                "candidate_index": index,
                "candidate_text": text,
                "candidate_sha256": hashes[index]
                if index < len(hashes)
                else None,
                "reward_by_arm": {
                    arm: vectors[arm][index] for arm in ARMS
                },
            }
            for index, text in enumerate(nested)
        ]
    return None


def _ratio_is_one_sixth(value: Any) -> bool:
    if value == "1/6":
        return True
    if isinstance(value, dict):
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        return (
            isinstance(numerator, int)
            and not isinstance(numerator, bool)
            and isinstance(denominator, int)
            and not isinstance(denominator, bool)
            and denominator != 0
            and numerator * 6 == denominator
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and math.isclose(
            float(value), 1 / 6, rel_tol=0, abs_tol=1e-15
        )
    return False


def validate_controlled_assets(root: str | Path) -> dict[str, Any]:
    """Validate *root* and return a JSON-serializable report."""

    try:
        return Validator(Path(root)).run()
    except Exception as exc:  # pragma: no cover - last-resort fail-closed guard
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "root": str(Path(root).resolve()),
            "valid": False,
            "error_count": 1,
            "errors": [
                {
                    "code": "internal_validator_error",
                    "path": str(root),
                    "message": f"{type(exc).__name__}: {exc}",
                }
            ],
            "counts": {},
            "metrics": {},
            "model_execution_performed": False,
        }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed static validation of the controlled RLVR real assets"
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="real_assets directory (default: parent of scripts/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional path for the same JSON report printed to stdout",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate_controlled_assets(args.root)
    rendered = json.dumps(
        report, ensure_ascii=False, sort_keys=True, indent=2
    )
    print(rendered)
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            failure = {
                "schema_version": REPORT_SCHEMA_VERSION,
                "root": str(Path(args.root).resolve()),
                "valid": False,
                "error_count": 1,
                "errors": [
                    {
                        "code": "report_write_error",
                        "path": str(args.output),
                        "message": str(exc),
                    }
                ],
                "counts": report.get("counts", {}),
                "metrics": report.get("metrics", {}),
                "model_execution_performed": False,
            }
            print(
                json.dumps(
                    failure, ensure_ascii=False, sort_keys=True, indent=2
                ),
                file=sys.stderr,
            )
            return 2
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
