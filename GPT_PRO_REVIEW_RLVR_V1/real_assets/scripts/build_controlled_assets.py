#!/usr/bin/env python3
"""Build deterministic CPU-only P4-R1 controlled assets.

The builder is intentionally model-free and standard-library-only.  It does
not import a tokenizer, ML framework, model, optimizer, or GPU library.  It
does not run automatically on import.

Default output is the containing ``real_assets`` directory.  Existing target
files are never overwritten.  Every machine-generated label remains
``PENDING_HUMAN_REVIEW`` and every model-dependent measurement remains
``NOT_RUN``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
REAL_ASSETS_DIR = SCRIPT_PATH.parents[1]
REVIEW_ROOT = SCRIPT_PATH.parents[2]
SRC_DIR = REAL_ASSETS_DIR / "src"
PROTOCOL_FILENAME = "P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json"
PROTOCOL_PATH = REAL_ASSETS_DIR / PROTOCOL_FILENAME
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from commitment_core import (  # noqa: E402
    canonical_json_bytes as _canonical_json_bytes,
    sha256_bytes as _sha256_bytes,
)

from controlled_tasks import (  # noqa: E402
    CANDIDATES,
    LOCAL_OFFSETS,
    LOCAL_RULE_IDS,
    SHARED_OFFSET,
    SHARED_RULE_ID,
    TASK_DFA7_FINAL,
    TASK_MARKED_RANK7,
    TASK_MOD7_SUM,
    TASK_PAREN_MAX_DEPTH7,
    Codebook,
    ControlledTaskError,
    evaluate_instance,
    generate_instance,
    label_triplet,
    make_codebook,
    render_prompt,
    task_spec_record,
    verifier_arm_records,
)


SCHEMA_VERSION = "p4-r1-controlled-real-assets-v1"
GENERATOR_ID = "P4_R1_CONTROLLED_ASSET_BUILDER_V1"
DEFAULT_SEED_ROOT = "P4_R1_CONTROLLED_REAL_V1_2026_07_30"
EXPERIMENT_STATUS = "NOT_RUN"
HUMAN_REVIEW_STATUS = "PENDING_HUMAN_REVIEW"
MACHINE_LABEL_STATUS = "MACHINE_COMPUTED_PENDING_HUMAN_REVIEW"

ARMS = (
    "clean",
    "shared_leaky",
    "local_leaky",
    "shared_frozen_probe_control",
    "local_frozen_probe_control",
)

TASK_PAIRS = (
    {
        "task_pair_id": "TP1_MOD7SUM_DFA7",
        "stack_prefix": "TP1",
        "task_a": TASK_MOD7_SUM,
        "task_b": TASK_DFA7_FINAL,
    },
    {
        "task_pair_id": "TP2_RANK7_PARENDEPTH7",
        "stack_prefix": "TP2",
        "task_a": TASK_MARKED_RANK7,
        "task_b": TASK_PAREN_MAX_DEPTH7,
    },
)

# Exact codebooks supplied for the two mappings.  A/B identify the task slot
# inside a pair and are not changed by source/target direction.
MAPPING_DEFINITIONS = (
    {
        "mapping_id": "mapping_0",
        "A": {"multiplier": 1, "intercept": 0},
        "B": {"multiplier": 2, "intercept": 1},
    },
    {
        "mapping_id": "mapping_1",
        "A": {"multiplier": 3, "intercept": 2},
        "B": {"multiplier": 5, "intercept": 4},
    },
)

DIRECTIONS = (
    {
        "mirror_role": "A_TO_B",
        "source_slot": "A",
        "target_slot": "B",
    },
    {
        "mirror_role": "B_TO_A",
        "source_slot": "B",
        "target_slot": "A",
    },
)

SOURCE_REPETITIONS_PER_CLASS = 2
TARGET_CALIBRATION_REPETITIONS_PER_CLASS = 1
TARGET_AUDIT_REPETITIONS_PER_CLASS = 2

PRIMARY_JSONL_FILES = (
    "REAL_MAPPING_STACKS_V1.jsonl",
    "REAL_SOURCE_BUNDLES_V1.jsonl",
    "TARGET_CALIBRATION_REAL_V1.jsonl",
    "TARGET_AUDIT_REAL_V1.jsonl",
    "VERIFIER_REWARD_MANIFEST_V1.jsonl",
)

MACHINE_LABEL_FILES = (
    "SOURCE_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "TARGET_CALIBRATION_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "TARGET_AUDIT_MACHINE_LABEL_MANIFEST_V1.jsonl",
)

OTHER_OUTPUT_FILES = (
    PROTOCOL_FILENAME,
    "G1_OPPORTUNITY_AUDIT_V1.json",
    "TARGET_AUDIT_SEAL_RECEIPT.json",
    "RANDOMIZATION_MODEL_ENV_PREREG_V1.json",
    "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
    "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
)

OUTPUT_DIRECTORIES = ("raw_source_bytes", "raw_target_bytes")
ALL_OUTPUT_NAMES = (
    *PRIMARY_JSONL_FILES,
    *MACHINE_LABEL_FILES,
    *OTHER_OUTPUT_FILES,
    *OUTPUT_DIRECTORIES,
)


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("wb") as handle:
        for row in rows:
            handle.write(_canonical_json_bytes(row))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(_canonical_json_bytes(value))


def _model_dependent_not_run() -> dict[str, str]:
    return {
        "tokenizer_check": "NOT_RUN",
        "base_logits": "NOT_RUN",
        "initial_wrong_target_reachability": "NOT_RUN",
        "model_conditioned_log_probability": "NOT_RUN",
        "relative_advantage": "NOT_RUN",
        "candidate_exposure_or_selection_distribution": "NOT_RUN",
        "pre_post_target_log_probabilities": "NOT_RUN",
        "L_D_tau": "NOT_RUN",
        "G1_G2_core_scientific_decision": "NOT_RUN",
        "model_forward": "NOT_RUN",
        "gradient": "NOT_RUN",
        "optimizer_step": "NOT_RUN",
        "training": "NOT_RUN",
        "rl": "NOT_RUN",
        "rlvr": "NOT_RUN",
    }


def _review_fields() -> dict[str, Any]:
    return {
        "machine_label_status": MACHINE_LABEL_STATUS,
        "human_review_status": HUMAN_REVIEW_STATUS,
        "human_review_receipt": None,
    }


def _task_for_slot(task_pair: Mapping[str, str], slot: str) -> str:
    if slot == "A":
        return task_pair["task_a"]
    if slot == "B":
        return task_pair["task_b"]
    raise ControlledTaskError(f"invalid task slot: {slot}")


def _codebook_for(
    mapping: Mapping[str, Any],
    slot: str,
) -> Codebook:
    parameters = mapping[slot]
    return make_codebook(
        mapping_id=mapping["mapping_id"],
        task_slot=slot,
        multiplier=parameters["multiplier"],
        intercept=parameters["intercept"],
    )


def _stack_id(
    stack_prefix: str,
    mapping_id: str,
    mirror_role: str,
) -> str:
    mapping_compact = {
        "mapping_0": "M0",
        "mapping_1": "M1",
    }.get(mapping_id)
    if mapping_compact is None:
        raise ControlledTaskError(f"unknown mapping_id: {mapping_id}")
    return f"{stack_prefix}-{mapping_compact}-{mirror_role}"


def _stack_blueprints() -> list[dict[str, Any]]:
    blueprints: list[dict[str, Any]] = []
    for task_pair in TASK_PAIRS:
        for mapping in MAPPING_DEFINITIONS:
            mirror_ids = {
                direction["mirror_role"]: _stack_id(
                    task_pair["stack_prefix"],
                    mapping["mapping_id"],
                    direction["mirror_role"],
                )
                for direction in DIRECTIONS
            }
            for direction in DIRECTIONS:
                source_slot = direction["source_slot"]
                target_slot = direction["target_slot"]
                counterpart_role = (
                    "B_TO_A"
                    if direction["mirror_role"] == "A_TO_B"
                    else "A_TO_B"
                )
                blueprints.append(
                    {
                        "mapping_stack_id": mirror_ids[direction["mirror_role"]],
                        "task_pair_id": task_pair["task_pair_id"],
                        "mapping_id": mapping["mapping_id"],
                        "mirror_role": direction["mirror_role"],
                        "mirror_counterpart_stack_id": mirror_ids[counterpart_role],
                        "task_a": task_pair["task_a"],
                        "task_b": task_pair["task_b"],
                        "source_slot": source_slot,
                        "target_slot": target_slot,
                        "source_task_id": _task_for_slot(task_pair, source_slot),
                        "target_task_id": _task_for_slot(task_pair, target_slot),
                        "source_codebook": _codebook_for(mapping, source_slot),
                        "target_codebook": _codebook_for(mapping, target_slot),
                    }
                )
    if len(blueprints) != 8:
        raise ControlledTaskError("exactly eight mapping-stack blueprints are required")
    return blueprints


class UniquenessLedger:
    """Fail closed on row, prompt, lineage, or seed-namespace reuse."""

    def __init__(self) -> None:
        self.seed_namespaces: set[str] = set()
        self.lineage_ids: set[str] = set()
        self.task_input_sha256: set[str] = set()
        self.prompt_sha256: set[str] = set()
        self.row_bytes_sha256: set[str] = set()
        self.raw_relpaths: set[str] = set()

    def prompt_is_used(self, digest: str) -> bool:
        return digest in self.prompt_sha256

    def task_input_is_used(self, digest: str) -> bool:
        return digest in self.task_input_sha256

    def register(
        self,
        *,
        seed_namespace: str,
        lineage_id: str,
        task_input_sha256: str,
        prompt_sha256: str,
        row_bytes_sha256: str,
        raw_prompt_relpath: str,
        raw_row_relpath: str,
    ) -> None:
        checks = (
            ("seed_namespace", seed_namespace, self.seed_namespaces),
            ("lineage_id", lineage_id, self.lineage_ids),
            ("task_input_sha256", task_input_sha256, self.task_input_sha256),
            ("prompt_sha256", prompt_sha256, self.prompt_sha256),
            ("row_bytes_sha256", row_bytes_sha256, self.row_bytes_sha256),
            ("raw_prompt_relpath", raw_prompt_relpath, self.raw_relpaths),
            ("raw_row_relpath", raw_row_relpath, self.raw_relpaths),
        )
        for label, value, used in checks:
            if value in used:
                raise ControlledTaskError(f"{label} reuse detected: {value}")
        for _, value, used in checks:
            used.add(value)

    def as_counts(self) -> dict[str, int]:
        return {
            "unique_seed_namespaces": len(self.seed_namespaces),
            "unique_lineage_ids": len(self.lineage_ids),
            "unique_task_input_sha256": len(self.task_input_sha256),
            "unique_prompt_sha256": len(self.prompt_sha256),
            "unique_row_bytes_sha256": len(self.row_bytes_sha256),
            "unique_raw_relpaths": len(self.raw_relpaths),
        }


def _row_prefix(split_role: str) -> str:
    return {
        "SOURCE": "src",
        "TARGET_CALIBRATION": "cal",
        "TARGET_AUDIT": "audit",
    }[split_role]


def _raw_root_and_subdir(
    split_role: str,
    mapping_stack_id: str,
) -> tuple[str, str]:
    if split_role == "SOURCE":
        return "raw_source_bytes", mapping_stack_id
    if split_role == "TARGET_CALIBRATION":
        return "raw_target_bytes", f"{mapping_stack_id}/calibration"
    if split_role == "TARGET_AUDIT":
        return "raw_target_bytes", f"{mapping_stack_id}/audit"
    raise ControlledTaskError(f"unknown split role: {split_role}")


def _make_row(
    *,
    staging_root: Path,
    ledger: UniquenessLedger,
    seed_root: str,
    stack: Mapping[str, Any],
    split_role: str,
    task_id: str,
    task_slot: str,
    codebook: Codebook,
    z: int,
    replicate_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_namespace = (
        f"{seed_root}/{stack['mapping_stack_id']}/{split_role}/"
        f"{task_id}/z{z}/rep{replicate_index}"
    )
    for generation_attempt in range(1024):
        seed_namespace = f"{base_namespace}/attempt{generation_attempt}"
        instance = generate_instance(task_id, z, seed_namespace)
        task_input_sha256 = _sha256_bytes(_canonical_json_bytes(instance))
        prompt_bytes = render_prompt(task_id, instance, codebook)
        prompt_sha256 = _sha256_bytes(prompt_bytes)
        if (
            not ledger.task_input_is_used(task_input_sha256)
            and not ledger.prompt_is_used(prompt_sha256)
        ):
            break
    else:
        raise ControlledTaskError(
            f"failed to create a unique prompt for {base_namespace}"
        )

    computed_z, machine_trace = evaluate_instance(task_id, instance)
    if computed_z != z:
        raise ControlledTaskError("machine-label recomputation changed canonical z")

    seed_sha256 = _sha256_bytes(seed_namespace.encode("ascii"))
    lineage_id = _sha256_bytes(
        ("P4_R1_LINEAGE_V1|" + seed_namespace).encode("ascii")
    )
    row_id = (
        f"{_row_prefix(split_role)}-{stack['mapping_stack_id']}-"
        f"z{z}-r{replicate_index}-{lineage_id[:12]}"
    )
    labels = label_triplet(task_id, z, codebook)
    candidate_records = [
        {
            "candidate_index": candidate_index,
            "candidate": candidate,
            "is_gold": candidate == labels["gold_candidate"],
            "is_shared_wrong": candidate == labels["shared_bug_candidate"],
            "is_task_local_wrong": candidate == labels["local_bug_candidate"],
        }
        for candidate_index, candidate in enumerate(CANDIDATES)
    ]

    split_contract: dict[str, Any]
    if split_role == "SOURCE":
        split_contract = {
            "panel_role": "SOURCE_UPDATE_INPUT",
            "selection_eligible": False,
            "scientific_evaluation_eligible": False,
            "eligibility_blocker": (
                "PENDING_HUMAN_REVIEW_AND_MODEL_DEPENDENT_G1_G2"
            ),
        }
    elif split_role == "TARGET_CALIBRATION":
        split_contract = {
            "panel_role": "CALIBRATION_ONLY",
            "selection_eligible": False,
            "scientific_evaluation_eligible": False,
            "eligibility_blocker": (
                "PENDING_HUMAN_REVIEW_AND_MODEL_DEPENDENT_G1_G2"
            ),
        }
    elif split_role == "TARGET_AUDIT":
        split_contract = {
            "panel_role": "SEALED_AUDIT_INTENDED",
            "seal_status": "BOUND_BY_TARGET_AUDIT_SEAL_RECEIPT",
            "selection_eligible": False,
            "calibration_eligible": False,
            "scientific_evaluation_eligible": False,
            "eligibility_blocker": (
                "PENDING_HUMAN_REVIEW_AND_MODEL_AUTHORIZATION"
            ),
        }
    else:
        raise ControlledTaskError(f"unknown split role: {split_role}")

    semantic_row = {
        "schema_version": SCHEMA_VERSION,
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "mapping_stack_id": stack["mapping_stack_id"],
        "task_pair_id": stack["task_pair_id"],
        "mapping_id": stack["mapping_id"],
        "mirror_role": stack["mirror_role"],
        "split_role": split_role,
        "row_id": row_id,
        "task_id": task_id,
        "task_slot": task_slot,
        "canonical_z": z,
        "replicate_index_within_class": replicate_index,
        "seed_namespace": seed_namespace,
        "seed_namespace_sha256": seed_sha256,
        "generation_attempt": generation_attempt,
        "lineage_id": lineage_id,
        "task_input": instance,
        "task_input_sha256": task_input_sha256,
        "codebook": codebook.as_record(),
        "candidate_order": list(CANDIDATES),
        "candidate_records": candidate_records,
        **labels,
        "prompt_text": prompt_bytes.decode("ascii"),
        **split_contract,
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
    }
    row_bytes = _canonical_json_bytes(semantic_row)
    row_bytes_sha256 = _sha256_bytes(row_bytes)

    raw_root, raw_subdir = _raw_root_and_subdir(
        split_role,
        stack["mapping_stack_id"],
    )
    short_name = (
        f"{_row_prefix(split_role)}-z{z}-r{replicate_index}-"
        f"{lineage_id[:12]}"
    )
    raw_prompt_relpath = (
        f"{raw_root}/{raw_subdir}/{short_name}.prompt.txt"
    )
    raw_row_relpath = f"{raw_root}/{raw_subdir}/{short_name}.row.json"

    raw_prompt_path = staging_root / Path(raw_prompt_relpath)
    raw_row_path = staging_root / Path(raw_row_relpath)
    raw_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    raw_prompt_path.write_bytes(prompt_bytes)
    raw_row_path.write_bytes(row_bytes)

    ledger.register(
        seed_namespace=seed_namespace,
        lineage_id=lineage_id,
        task_input_sha256=task_input_sha256,
        prompt_sha256=prompt_sha256,
        row_bytes_sha256=row_bytes_sha256,
        raw_prompt_relpath=raw_prompt_relpath,
        raw_row_relpath=raw_row_relpath,
    )

    published_row = {
        **semantic_row,
        "prompt_bytes": {
            "encoding": "ascii",
            "newline": "LF",
            "byte_length": len(prompt_bytes),
            "sha256": prompt_sha256,
            "raw_relpath": raw_prompt_relpath,
        },
        "row_bytes": {
            "encoding": "canonical-json-ascii",
            "newline": "LF",
            "byte_length": len(row_bytes),
            "sha256": row_bytes_sha256,
            "raw_relpath": raw_row_relpath,
        },
    }
    machine_label_row = {
        "schema_version": "p4-r1-machine-label-manifest-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "mapping_stack_id": stack["mapping_stack_id"],
        "task_pair_id": stack["task_pair_id"],
        "mapping_id": stack["mapping_id"],
        "mirror_role": stack["mirror_role"],
        "split_role": split_role,
        "row_id": row_id,
        "task_id": task_id,
        "task_slot": task_slot,
        "lineage_id": lineage_id,
        "task_input_sha256": task_input_sha256,
        "prompt_sha256": prompt_sha256,
        "row_bytes_sha256": row_bytes_sha256,
        "canonical_z": z,
        "gold_candidate": labels["gold_candidate"],
        "shared_bug_z": labels["shared_bug_z"],
        "shared_bug_candidate": labels["shared_bug_candidate"],
        "shared_rule_id": SHARED_RULE_ID,
        "local_bug_z": labels["local_bug_z"],
        "local_bug_candidate": labels["local_bug_candidate"],
        "local_rule_id": LOCAL_RULE_IDS[task_id],
        "shared_offset_mod7": SHARED_OFFSET,
        "local_offset_mod7": LOCAL_OFFSETS[task_id],
        "machine_label_trace": machine_trace,
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
    }
    return published_row, machine_label_row


def _class_count(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(row["canonical_z"] for row in rows)
    return {str(z): counts[z] for z in range(7)}


def _panel_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    material = {
        "ordered_row_ids": [row["row_id"] for row in rows],
        "ordered_row_bytes_sha256": [
            row["row_bytes"]["sha256"] for row in rows
        ],
        "ordered_prompt_sha256": [
            row["prompt_bytes"]["sha256"] for row in rows
        ],
    }
    return _sha256_bytes(_canonical_json_bytes(material))


def _source_bundle(
    stack: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(rows) != 14 or _class_count(rows) != {
        str(z): 2 for z in range(7)
    }:
        raise ControlledTaskError("source bundle must contain two rows per class")
    bundle_material = {
        "mapping_stack_id": stack["mapping_stack_id"],
        "source_task_id": stack["source_task_id"],
        "ordered_row_ids": [row["row_id"] for row in rows],
        "ordered_row_bytes_sha256": [
            row["row_bytes"]["sha256"] for row in rows
        ],
        "ordered_prompt_sha256": [
            row["prompt_bytes"]["sha256"] for row in rows
        ],
        "candidate_order": list(CANDIDATES),
    }
    source_bundle_sha256 = _sha256_bytes(
        _canonical_json_bytes(bundle_material)
    )
    return {
        "schema_version": "p4-r1-real-source-bundle-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "mapping_stack_id": stack["mapping_stack_id"],
        "task_pair_id": stack["task_pair_id"],
        "mapping_id": stack["mapping_id"],
        "mirror_role": stack["mirror_role"],
        "source_task_id": stack["source_task_id"],
        "source_task_slot": stack["source_slot"],
        "source_codebook": stack["source_codebook"].as_record(),
        "source_row_count": len(rows),
        "class_counts": _class_count(rows),
        "candidate_order": list(CANDIDATES),
        "source_bundle_sha256": source_bundle_sha256,
        "arm_source_bundle_binding": {
            arm_id: source_bundle_sha256 for arm_id in ARMS
        },
        "arm_source_identity_exact": True,
        "rows": list(rows),
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
    }


def _verifier_manifest_row(
    *,
    stack: Mapping[str, Any],
    row: Mapping[str, Any],
    verifier_implementation_relpath: str,
    verifier_implementation_sha256: str,
    task_contract_sha256: str,
) -> dict[str, Any]:
    codebook = (
        stack["source_codebook"]
        if row["split_role"] == "SOURCE"
        else stack["target_codebook"]
    )
    arms = verifier_arm_records(
        row["task_id"],
        row["canonical_z"],
        codebook,
    )
    arm_by_id = {arm["arm_id"]: arm for arm in arms}
    candidate_records = [
        {
            "candidate_index": candidate_index,
            "candidate": candidate,
            "candidate_text": candidate,
            "candidate_sha256": _sha256_bytes(candidate.encode("ascii")),
            "reward_by_arm": {
                arm_id: arm_by_id[arm_id]["reward_vector"][candidate_index]
                for arm_id in ARMS
            },
        }
        for candidate_index, candidate in enumerate(CANDIDATES)
    ]
    return {
        "schema_version": "p4-r1-verifier-reward-manifest-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "mapping_stack_id": stack["mapping_stack_id"],
        "task_pair_id": stack["task_pair_id"],
        "mapping_id": stack["mapping_id"],
        "mirror_role": stack["mirror_role"],
        "row_id": row["row_id"],
        "split_role": row["split_role"],
        "row_bytes_sha256": row["row_bytes"]["sha256"],
        "prompt_sha256": row["prompt_bytes"]["sha256"],
        "task_id": row["task_id"],
        "canonical_z": row["canonical_z"],
        "candidate_order": list(CANDIDATES),
        "gold_candidate": row["gold_candidate"],
        "shared_bug_candidate": row["shared_bug_candidate"],
        "local_bug_candidate": row["local_bug_candidate"],
        "verifier_id": "CONTROLLED_MOD7_EXACT_VERIFIER_V1",
        "verifier_contract": (
            "clean accepts gold only; shared accepts gold and latent z+1; "
            "local accepts gold and the task-fixed latent offset; transformations "
            "occur before the task-slot codebook"
        ),
        "verifier_implementation_relpath": verifier_implementation_relpath,
        "verifier_implementation_sha256": verifier_implementation_sha256,
        "task_contract_sha256": task_contract_sha256,
        "arms": arms,
        "candidate_record_count": len(candidate_records),
        "candidate_records": candidate_records,
        "model_free_enumeration_scope": (
            "exact reward vectors, online FPR as accepted-wrong/all-wrong, "
            "accepted-wrong count, positive count, and fixed candidate exposure; "
            "not base logits, reachability, model-conditioned log probability, "
            "relative advantage, or pre/post outcomes"
        ),
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
    }


def _mapping_stack_record(
    *,
    stack: Mapping[str, Any],
    seed_root: str,
    source_bundle: Mapping[str, Any],
    calibration_rows: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    task_contract_sha256: str,
    verifier_implementation_sha256: str,
    builder_implementation_sha256: str,
) -> dict[str, Any]:
    if len(calibration_rows) != 7 or _class_count(calibration_rows) != {
        str(z): 1 for z in range(7)
    }:
        raise ControlledTaskError(
            "target calibration must contain one row per class"
        )
    if len(audit_rows) != 14 or _class_count(audit_rows) != {
        str(z): 2 for z in range(7)
    }:
        raise ControlledTaskError("target audit must contain two rows per class")
    return {
        "schema_version": "p4-r1-real-mapping-stack-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "mapping_stack_id": stack["mapping_stack_id"],
        "task_pair_id": stack["task_pair_id"],
        "mapping_id": stack["mapping_id"],
        "mirror_role": stack["mirror_role"],
        "mirror_counterpart_stack_id": stack["mirror_counterpart_stack_id"],
        "task_a": stack["task_a"],
        "task_b": stack["task_b"],
        "source_task_id": stack["source_task_id"],
        "source_task_slot": stack["source_slot"],
        "target_task_id": stack["target_task_id"],
        "target_task_slot": stack["target_slot"],
        "source_codebook": stack["source_codebook"].as_record(),
        "target_codebook": stack["target_codebook"].as_record(),
        "candidate_order": list(CANDIDATES),
        "shared_error_rule": {
            "rule_id": SHARED_RULE_ID,
            "space": "canonical_latent_z",
            "formula": "(z+1) mod 7",
            "offset_mod7": SHARED_OFFSET,
            "cross_task_constant": True,
        },
        "local_error_rules": {
            task_id: {
                "rule_id": LOCAL_RULE_IDS[task_id],
                "space": "canonical_latent_z",
                "formula": f"(z+{offset}) mod 7",
                "offset_mod7": offset,
                "task_fixed": True,
                "mapping_independent": True,
                "direction_independent": True,
            }
            for task_id, offset in LOCAL_OFFSETS.items()
        },
        "row_counts": {
            "source": len(source_bundle["rows"]),
            "target_calibration": len(calibration_rows),
            "target_audit": len(audit_rows),
        },
        "class_counts": {
            "source": _class_count(source_bundle["rows"]),
            "target_calibration": _class_count(calibration_rows),
            "target_audit": _class_count(audit_rows),
        },
        "source_bundle_sha256": source_bundle["source_bundle_sha256"],
        "target_calibration_panel_sha256": _panel_digest(calibration_rows),
        "target_audit_panel_sha256": _panel_digest(audit_rows),
        "target_audit_seal_status": (
            "BOUND_BY_TARGET_AUDIT_SEAL_RECEIPT"
        ),
        "target_audit_selection_eligible": False,
        "seed_namespace_roots": {
            split_role: (
                f"{seed_root}/{stack['mapping_stack_id']}/{split_role}/"
            )
            for split_role in (
                "SOURCE",
                "TARGET_CALIBRATION",
                "TARGET_AUDIT",
            )
        },
        "zero_reuse_contract": {
            "seed_namespace": "EXACT_ZERO_REUSE_REQUIRED",
            "lineage_id": "EXACT_ZERO_REUSE_REQUIRED",
            "prompt_bytes_sha256": "EXACT_ZERO_REUSE_REQUIRED",
            "row_bytes_sha256": "EXACT_ZERO_REUSE_REQUIRED",
            "scope": "all stacks, splits, and tasks",
        },
        "task_contract_sha256": task_contract_sha256,
        "verifier_implementation_sha256": verifier_implementation_sha256,
        "builder_implementation_sha256": builder_implementation_sha256,
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
    }


def _validate_stack_mirrors(
    mapping_stack_rows: Sequence[Mapping[str, Any]],
) -> None:
    by_id = {row["mapping_stack_id"]: row for row in mapping_stack_rows}
    if len(by_id) != 8:
        raise ControlledTaskError("mapping stack IDs must be eight unique values")
    for row in mapping_stack_rows:
        counterpart = by_id.get(row["mirror_counterpart_stack_id"])
        if counterpart is None:
            raise ControlledTaskError("mirror counterpart is missing")
        if counterpart["mirror_counterpart_stack_id"] != row["mapping_stack_id"]:
            raise ControlledTaskError("mirror counterpart relation is not symmetric")
        if counterpart["task_pair_id"] != row["task_pair_id"]:
            raise ControlledTaskError("mirror task-pair mismatch")
        if counterpart["mapping_id"] != row["mapping_id"]:
            raise ControlledTaskError("mirror mapping mismatch")
        if counterpart["source_task_id"] != row["target_task_id"]:
            raise ControlledTaskError("mirror did not reverse source and target")
        if counterpart["target_task_id"] != row["source_task_id"]:
            raise ControlledTaskError("mirror did not reverse target and source")


def _validate_cross_artifact_contract(
    *,
    mapping_stack_rows: Sequence[Mapping[str, Any]],
    source_bundles: Sequence[Mapping[str, Any]],
    target_calibration_rows: Sequence[Mapping[str, Any]],
    target_audit_rows: Sequence[Mapping[str, Any]],
    verifier_rows: Sequence[Mapping[str, Any]],
    source_machine_labels: Sequence[Mapping[str, Any]],
    calibration_machine_labels: Sequence[Mapping[str, Any]],
    audit_machine_labels: Sequence[Mapping[str, Any]],
    ledger: UniquenessLedger,
) -> None:
    _validate_stack_mirrors(mapping_stack_rows)
    if len(source_bundles) != 8:
        raise ControlledTaskError("exactly eight source bundles are required")
    if len(target_calibration_rows) != 8 * 7:
        raise ControlledTaskError("expected 56 target calibration rows")
    if len(target_audit_rows) != 8 * 14:
        raise ControlledTaskError("expected 112 target audit rows")
    if len(verifier_rows) != 8 * (14 + 7 + 14):
        raise ControlledTaskError(
            "expected 280 verifier/reward rows (1,960 candidate records)"
        )
    if len(source_machine_labels) != 8 * 14:
        raise ControlledTaskError("expected 112 source machine labels")
    if len(calibration_machine_labels) != 8 * 7:
        raise ControlledTaskError("expected 56 calibration machine labels")
    if len(audit_machine_labels) != 8 * 14:
        raise ControlledTaskError("expected 112 audit machine labels")

    expected_total_rows = 8 * (14 + 7 + 14)
    counts = ledger.as_counts()
    for key in (
        "unique_seed_namespaces",
        "unique_lineage_ids",
        "unique_task_input_sha256",
        "unique_prompt_sha256",
        "unique_row_bytes_sha256",
    ):
        if counts[key] != expected_total_rows:
            raise ControlledTaskError(
                f"{key} must equal {expected_total_rows}, got {counts[key]}"
            )
    if counts["unique_raw_relpaths"] != 2 * expected_total_rows:
        raise ControlledTaskError("each row requires unique prompt and row byte paths")

    stack_ids = {row["mapping_stack_id"] for row in mapping_stack_rows}
    for collection in (
        source_bundles,
        target_calibration_rows,
        target_audit_rows,
        verifier_rows,
    ):
        if {row["mapping_stack_id"] for row in collection} != stack_ids:
            raise ControlledTaskError("an artifact is missing one or more stacks")

    for bundle in source_bundles:
        hashes = set(bundle["arm_source_bundle_binding"].values())
        if hashes != {bundle["source_bundle_sha256"]}:
            raise ControlledTaskError("arm source-bundle identity is not exact")


def _authorization_record() -> dict[str, bool]:
    return {
        "construct_cpu_only_assets": True,
        "cpu_schema_hash_lineage_overlap_checks": True,
        "cpu_verifier_reward_vector_fpr_checks": True,
        "create_audit_seal_receipt": True,
        "tokenizer_load": False,
        "gpu": False,
        "model_weight_load": False,
        "model_inference": False,
        "model_generation": False,
        "model_forward": False,
        "backward": False,
        "gradient": False,
        "optimizer": False,
        "optimizer_step": False,
        "parameter_update": False,
        "training": False,
        "rl": False,
        "rlvr": False,
        "one_stack_methodology_pilot": False,
        "eight_stack_screen": False,
        "scientific_go_stop_decision": False,
    }


def _g1_cpu_audit(
    *,
    source_bundles: Sequence[Mapping[str, Any]],
    verifier_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_rows = [
        row
        for bundle in source_bundles
        for row in bundle["rows"]
    ]
    source_verifier_rows = [
        row for row in verifier_rows if row["split_role"] == "SOURCE"
    ]
    if len(source_rows) != 112 or len(source_verifier_rows) != 112:
        raise ControlledTaskError(
            "G1 CPU audit requires exactly 112 source rows and manifests"
        )

    accepted_wrong = {"shared_leaky": 0, "local_leaky": 0}
    total_wrong = len(source_verifier_rows) * 6
    shared_labels: Counter[str] = Counter()
    local_labels: Counter[str] = Counter()
    for row in source_verifier_rows:
        arms = {arm["arm_id"]: arm for arm in row["arms"]}
        for arm_id in accepted_wrong:
            accepted_wrong[arm_id] += arms[arm_id][
                "wrong_positive_count"
            ]
        shared_labels[row["shared_bug_candidate"]] += 1
        local_labels[row["local_bug_candidate"]] += 1

    expected_accepted_wrong = len(source_verifier_rows)
    if accepted_wrong != {
        "shared_leaky": expected_accepted_wrong,
        "local_leaky": expected_accepted_wrong,
    }:
        raise ControlledTaskError("G1 accepted-wrong count is not one per row")
    if shared_labels != local_labels:
        raise ControlledTaskError(
            "shared/local wrong-label multisets are not exactly balanced"
        )

    return {
        "schema_version": "g1-opportunity-audit-v1",
        "g1_status": "NOT_RUN",
        "cpu_static_contract_status": "PASS_MACHINE_CHECK",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "source_row_count": len(source_rows),
        "source_candidate_record_count": len(source_rows) * len(CANDIDATES),
        "candidate_order": list(CANDIDATES),
        "candidate_panel_identical_between_arms": True,
        "source_prompt_identity_between_arms": True,
        "reward_vector_multiset_balance": "PASS_MACHINE_CHECK",
        "wrong_label_multiset_balance": {
            "result": "PASS_MACHINE_CHECK",
            "shared_counts": dict(sorted(shared_labels.items())),
            "local_counts": dict(sorted(local_labels.items())),
        },
        "shared_online_fpr": {
            "definition": "accepted_wrong/all_wrong",
            "numerator": accepted_wrong["shared_leaky"],
            "denominator": total_wrong,
            "exact_value": "1/6",
        },
        "local_online_fpr": {
            "definition": "accepted_wrong/all_wrong",
            "numerator": accepted_wrong["local_leaky"],
            "denominator": total_wrong,
            "exact_value": "1/6",
        },
        "static_checks": {
            "seven_fixed_candidates": "PASS_MACHINE_CHECK",
            "one_shared_wrong_positive_per_row": "PASS_MACHINE_CHECK",
            "one_local_wrong_positive_per_row": "PASS_MACHINE_CHECK",
            "two_total_positives_per_leaky_row": "PASS_MACHINE_CHECK",
            "shared_local_candidate_exposure_identity": "PASS_MACHINE_CHECK",
        },
        "model_dependent_checks": {
            "base_logits": "NOT_RUN",
            "initial_wrong_target_reachability": "NOT_RUN",
            "relative_advantage": "NOT_RUN",
            "candidate_exposure_or_selection_distribution": "NOT_RUN",
            "tokenizer_level_balance": "NOT_RUN",
            "initial_state_identity": "NOT_RUN",
            "optimizer_and_budget_identity": "NOT_RUN",
        },
        "overall_g1_pass": None,
        **_review_fields(),
        "authorization": _authorization_record(),
        "model_dependent_status": _model_dependent_not_run(),
        "run_eligibility": False,
    }


def _audit_seal_receipt(
    *,
    audit_path: Path,
    audit_record_count: int,
) -> dict[str, Any]:
    audit_bytes = audit_path.read_bytes()
    lines = audit_bytes.splitlines(keepends=True)
    if len(lines) != audit_record_count or any(
        not line.endswith(b"\n") for line in lines
    ):
        raise ControlledTaskError(
            "target audit JSONL must have one LF-terminated line per row"
        )
    line_hashes = [_sha256_bytes(line) for line in lines]
    try:
        parsed_rows = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledTaskError(
            f"target audit JSONL is not valid canonical JSON: {exc}"
        ) from exc
    semantic_row_hashes = [
        row["row_bytes"]["sha256"] for row in parsed_rows
    ]
    if any(
        not isinstance(value, str) or len(value) != 64
        for value in semantic_row_hashes
    ):
        raise ControlledTaskError(
            "target audit rows are missing semantic row byte hashes"
        )
    genesis = "0" * 64
    chain: list[str] = []
    previous = genesis
    for line_hash in line_hashes:
        previous = _sha256_bytes((previous + line_hash).encode("ascii"))
        chain.append(previous)
    return {
        "schema_version": "target-audit-seal-receipt-v1",
        "seal_status": "SEALED_PRE_MODEL_ACTION",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "audit_filename": audit_path.name,
        "audit_file_sha256": _sha256_bytes(audit_bytes),
        "audit_file_byte_length": len(audit_bytes),
        "audit_record_count": audit_record_count,
        "line_hash_algorithm": "sha256",
        "line_hash_formula": "sha256(exact_jsonl_line_bytes_including_LF)",
        "line_hashes": line_hashes,
        "semantic_row_hash_algorithm": "sha256",
        "semantic_row_hash_formula": (
            "sha256(canonical_semantic_row_bytes_before_publication_metadata)"
        ),
        "semantic_row_hashes": semantic_row_hashes,
        "hash_chain_genesis": genesis,
        "hash_chain_formula": (
            "sha256(previous_chain_sha256_ascii || "
            "line_sha256_ascii)"
        ),
        "hash_chain": chain,
        "hash_chain_head": chain[-1],
        "sealed_before_model_action": True,
        "audit_used_for_design": False,
        "audit_used_for_matching": False,
        "audit_used_for_tuning": False,
        "audit_used_for_debug": False,
        "audit_used_for_integrity_validation": True,
        "change_policy": (
            "Any human-review disagreement requires a new version and a new "
            "seal; this sealed byte sequence must never be edited in place."
        ),
        **_review_fields(),
        "authorization": _authorization_record(),
        "model_dependent_status": _model_dependent_not_run(),
        "run_eligibility": False,
    }


def _randomization_model_env_prereg(
    *,
    seed_root: str,
    protocol_sha256: str,
    task_contract_sha256: str,
    verifier_implementation_sha256: str,
    builder_implementation_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "randomization-model-env-prereg-v1",
        "preregistration_status": "PRE_REVIEW",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "run_eligible": False,
        "seed_root_for_cpu_asset_generation": seed_root,
        "scientific_unit": "mapping_stack",
        "technical_seeds_are_independent_units": False,
        "screen_layout": {
            "task_pairs": 2,
            "mappings_per_pair": 2,
            "mirrors_per_mapping": 2,
            "mapping_stacks": 8,
        },
        "randomization": {
            "status": "NOT_FROZEN",
            "arm_assignment": None,
            "execution_order": None,
            "randomization_seed": None,
        },
        "model_environment": {
            "status": "NOT_BOUND",
            "model_identifier": None,
            "model_revision": None,
            "model_weights_sha256": None,
            "tokenizer_identifier": None,
            "tokenizer_revision": None,
            "tokenizer_files_sha256": None,
            "runtime_lockfile_sha256": None,
            "device": None,
        },
        "update_recipe": {
            "status": "NOT_FROZEN",
            "one_step_recipe": None,
            "clean_arm_update_semantics": None,
            "optimizer": None,
            "learning_rate": None,
            "batching": None,
            "parameter_budget": None,
        },
        "scoring": {
            "status": "NOT_FROZEN",
            "target_sequence_and_token_normalization": None,
            "target_aggregation": None,
            "missing_value_rule": None,
        },
        "gates": {
            "epsilon_replay": None,
            "g1_model_dependent_metric_definitions": None,
            "g1_model_dependent_balance_thresholds": None,
        },
        "bound_cpu_artifact_hashes": {
            PROTOCOL_FILENAME: protocol_sha256,
            "task_contract_sha256": task_contract_sha256,
            "src/controlled_tasks.py": verifier_implementation_sha256,
            "scripts/build_controlled_assets.py": (
                builder_implementation_sha256
            ),
        },
        "blocking_fields": [
            "randomization.arm_assignment",
            "randomization.execution_order",
            "randomization.randomization_seed",
            "model_environment.model_identifier",
            "model_environment.model_revision",
            "model_environment.model_weights_sha256",
            "model_environment.tokenizer_identifier",
            "model_environment.tokenizer_revision",
            "model_environment.tokenizer_files_sha256",
            "model_environment.runtime_lockfile_sha256",
            "model_environment.device",
            "update_recipe.one_step_recipe",
            "update_recipe.clean_arm_update_semantics",
            "update_recipe.optimizer",
            "update_recipe.learning_rate",
            "update_recipe.batching",
            "update_recipe.parameter_budget",
            "scoring.target_sequence_and_token_normalization",
            "scoring.target_aggregation",
            "scoring.missing_value_rule",
            "gates.epsilon_replay",
            "gates.g1_model_dependent_metric_definitions",
            "gates.g1_model_dependent_balance_thresholds",
            "human_review",
            "new_explicit_model_run_authorization",
        ],
        **_review_fields(),
        "authorization": _authorization_record(),
        "model_dependent_status": _model_dependent_not_run(),
    }


def _build_to_staging(
    *,
    staging_root: Path,
    seed_root: str,
) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise ControlledTaskError(
            f"required protocol is missing: {PROTOCOL_PATH}"
        )
    protocol_bytes = PROTOCOL_PATH.read_bytes()
    try:
        protocol = json.loads(protocol_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledTaskError(
            f"required protocol is not valid JSON: {exc}"
        ) from exc
    if (
        protocol.get("protocol_status") != "PRE_REVIEW"
        or protocol.get("experiment_status") != EXPERIMENT_STATUS
        or protocol.get("human_review_status") != HUMAN_REVIEW_STATUS
        or protocol.get("run_eligible") is not False
    ):
        raise ControlledTaskError(
            "protocol must remain PRE_REVIEW / NOT_RUN / "
            "PENDING_HUMAN_REVIEW / run_eligible=false"
        )
    protocol_sha256 = _sha256_bytes(protocol_bytes)

    controlled_tasks_path = SRC_DIR / "controlled_tasks.py"
    verifier_implementation_sha256 = _sha256_file(controlled_tasks_path)
    builder_implementation_sha256 = _sha256_file(SCRIPT_PATH)
    task_contract = task_spec_record()
    task_contract_sha256 = _sha256_bytes(
        _canonical_json_bytes(task_contract)
    )
    verifier_implementation_relpath = "src/controlled_tasks.py"

    (staging_root / "raw_source_bytes").mkdir(parents=True, exist_ok=False)
    (staging_root / "raw_target_bytes").mkdir(parents=True, exist_ok=False)

    ledger = UniquenessLedger()
    mapping_stack_rows: list[dict[str, Any]] = []
    source_bundles: list[dict[str, Any]] = []
    target_calibration_rows: list[dict[str, Any]] = []
    target_audit_rows: list[dict[str, Any]] = []
    verifier_rows: list[dict[str, Any]] = []
    source_machine_labels: list[dict[str, Any]] = []
    calibration_machine_labels: list[dict[str, Any]] = []
    audit_machine_labels: list[dict[str, Any]] = []

    for stack in _stack_blueprints():
        source_rows: list[dict[str, Any]] = []
        stack_calibration_rows: list[dict[str, Any]] = []
        stack_audit_rows: list[dict[str, Any]] = []

        for z in range(7):
            for replicate_index in range(SOURCE_REPETITIONS_PER_CLASS):
                row, machine_label = _make_row(
                    staging_root=staging_root,
                    ledger=ledger,
                    seed_root=seed_root,
                    stack=stack,
                    split_role="SOURCE",
                    task_id=stack["source_task_id"],
                    task_slot=stack["source_slot"],
                    codebook=stack["source_codebook"],
                    z=z,
                    replicate_index=replicate_index,
                )
                source_rows.append(row)
                source_machine_labels.append(machine_label)
                verifier_rows.append(
                    _verifier_manifest_row(
                        stack=stack,
                        row=row,
                        verifier_implementation_relpath=(
                            verifier_implementation_relpath
                        ),
                        verifier_implementation_sha256=(
                            verifier_implementation_sha256
                        ),
                        task_contract_sha256=task_contract_sha256,
                    )
                )

        for z in range(7):
            row, machine_label = _make_row(
                staging_root=staging_root,
                ledger=ledger,
                seed_root=seed_root,
                stack=stack,
                split_role="TARGET_CALIBRATION",
                task_id=stack["target_task_id"],
                task_slot=stack["target_slot"],
                codebook=stack["target_codebook"],
                z=z,
                replicate_index=0,
            )
            stack_calibration_rows.append(row)
            target_calibration_rows.append(row)
            calibration_machine_labels.append(machine_label)
            verifier_rows.append(
                _verifier_manifest_row(
                    stack=stack,
                    row=row,
                    verifier_implementation_relpath=(
                        verifier_implementation_relpath
                    ),
                    verifier_implementation_sha256=(
                        verifier_implementation_sha256
                    ),
                    task_contract_sha256=task_contract_sha256,
                )
            )

        for z in range(7):
            for replicate_index in range(TARGET_AUDIT_REPETITIONS_PER_CLASS):
                row, machine_label = _make_row(
                    staging_root=staging_root,
                    ledger=ledger,
                    seed_root=seed_root,
                    stack=stack,
                    split_role="TARGET_AUDIT",
                    task_id=stack["target_task_id"],
                    task_slot=stack["target_slot"],
                    codebook=stack["target_codebook"],
                    z=z,
                    replicate_index=replicate_index,
                )
                stack_audit_rows.append(row)
                target_audit_rows.append(row)
                audit_machine_labels.append(machine_label)
                verifier_rows.append(
                    _verifier_manifest_row(
                        stack=stack,
                        row=row,
                        verifier_implementation_relpath=(
                            verifier_implementation_relpath
                        ),
                        verifier_implementation_sha256=(
                            verifier_implementation_sha256
                        ),
                        task_contract_sha256=task_contract_sha256,
                    )
                )

        bundle = _source_bundle(stack, source_rows)
        source_bundles.append(bundle)
        mapping_stack_rows.append(
            _mapping_stack_record(
                stack=stack,
                seed_root=seed_root,
                source_bundle=bundle,
                calibration_rows=stack_calibration_rows,
                audit_rows=stack_audit_rows,
                task_contract_sha256=task_contract_sha256,
                verifier_implementation_sha256=verifier_implementation_sha256,
                builder_implementation_sha256=builder_implementation_sha256,
            )
        )

    _validate_cross_artifact_contract(
        mapping_stack_rows=mapping_stack_rows,
        source_bundles=source_bundles,
        target_calibration_rows=target_calibration_rows,
        target_audit_rows=target_audit_rows,
        verifier_rows=verifier_rows,
        source_machine_labels=source_machine_labels,
        calibration_machine_labels=calibration_machine_labels,
        audit_machine_labels=audit_machine_labels,
        ledger=ledger,
    )

    file_rows: dict[str, Sequence[Mapping[str, Any]]] = {
        "REAL_MAPPING_STACKS_V1.jsonl": mapping_stack_rows,
        "REAL_SOURCE_BUNDLES_V1.jsonl": source_bundles,
        "TARGET_CALIBRATION_REAL_V1.jsonl": target_calibration_rows,
        "TARGET_AUDIT_REAL_V1.jsonl": target_audit_rows,
        "VERIFIER_REWARD_MANIFEST_V1.jsonl": verifier_rows,
        "SOURCE_MACHINE_LABEL_MANIFEST_V1.jsonl": source_machine_labels,
        "TARGET_CALIBRATION_MACHINE_LABEL_MANIFEST_V1.jsonl": (
            calibration_machine_labels
        ),
        "TARGET_AUDIT_MACHINE_LABEL_MANIFEST_V1.jsonl": audit_machine_labels,
    }
    for filename, rows in file_rows.items():
        _write_jsonl(staging_root / filename, rows)

    (staging_root / PROTOCOL_FILENAME).write_bytes(protocol_bytes)
    _write_json(
        staging_root / "G1_OPPORTUNITY_AUDIT_V1.json",
        _g1_cpu_audit(
            source_bundles=source_bundles,
            verifier_rows=verifier_rows,
        ),
    )
    _write_json(
        staging_root / "TARGET_AUDIT_SEAL_RECEIPT.json",
        _audit_seal_receipt(
            audit_path=staging_root / "TARGET_AUDIT_REAL_V1.jsonl",
            audit_record_count=len(target_audit_rows),
        ),
    )
    _write_json(
        staging_root / "RANDOMIZATION_MODEL_ENV_PREREG_V1.json",
        _randomization_model_env_prereg(
            seed_root=seed_root,
            protocol_sha256=protocol_sha256,
            task_contract_sha256=task_contract_sha256,
            verifier_implementation_sha256=verifier_implementation_sha256,
            builder_implementation_sha256=builder_implementation_sha256,
        ),
    )

    machine_label_index = {
        "schema_version": "p4-r1-machine-label-manifest-index-v1",
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "machine_label_status": MACHINE_LABEL_STATUS,
        "human_review_status": HUMAN_REVIEW_STATUS,
        "human_review_receipt": None,
        "manifests": {
            filename: {
                "row_count": len(file_rows[filename]),
                "sha256": _sha256_file(staging_root / filename),
            }
            for filename in MACHINE_LABEL_FILES
        },
        "task_contract": task_contract,
        "task_contract_sha256": task_contract_sha256,
        "machine_label_implementation_relpath": verifier_implementation_relpath,
        "machine_label_implementation_sha256": (
            verifier_implementation_sha256
        ),
        "model_dependent_status": _model_dependent_not_run(),
    }
    _write_json(
        staging_root / "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
        machine_label_index,
    )

    artifact_hashes = {
        filename: {
            "sha256": _sha256_file(staging_root / filename),
            "byte_length": (staging_root / filename).stat().st_size,
        }
        for filename in (
            *PRIMARY_JSONL_FILES,
            *MACHINE_LABEL_FILES,
            PROTOCOL_FILENAME,
            "G1_OPPORTUNITY_AUDIT_V1.json",
            "TARGET_AUDIT_SEAL_RECEIPT.json",
            "RANDOMIZATION_MODEL_ENV_PREREG_V1.json",
            "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
        )
    }
    build_manifest = {
        "schema_version": "p4-r1-controlled-asset-build-manifest-v1",
        "generator_id": GENERATOR_ID,
        "experiment_status": EXPERIMENT_STATUS,
        "scientific_evidence": False,
        "formal_experiment": False,
        "seed_root": seed_root,
        "determinism_contract": {
            "stdlib_only": True,
            "cpu_only": True,
            "sha256_counter_mode_randomness": True,
            "canonical_json_ascii_lf": True,
            "wall_clock_fields": False,
            "existing_outputs_overwritten": False,
        },
        "screen_layout": {
            "task_pairs": 2,
            "mappings_per_pair": 2,
            "mirrors_per_mapping": 2,
            "mapping_stacks": 8,
        },
        "rows_per_stack": {
            "source": 14,
            "target_calibration": 7,
            "target_audit": 14,
        },
        "total_rows": {
            "source": len(source_machine_labels),
            "target_calibration": len(calibration_machine_labels),
            "target_audit": len(audit_machine_labels),
        },
        "total_candidate_records": (
            len(verifier_rows) * len(CANDIDATES)
        ),
        "uniqueness_audit": {
            **ledger.as_counts(),
            "expected_unique_rows": 8 * (14 + 7 + 14),
            "result": "PASS_MACHINE_CHECK",
        },
        "code_hashes": {
            "builder_implementation_relpath": "scripts/build_controlled_assets.py",
            "builder_implementation_sha256": builder_implementation_sha256,
            "task_and_verifier_implementation_relpath": (
                verifier_implementation_relpath
            ),
            "task_and_verifier_implementation_sha256": (
                verifier_implementation_sha256
            ),
            "task_contract_sha256": task_contract_sha256,
        },
        "artifact_hashes": artifact_hashes,
        "raw_byte_directories": {
            "source": "raw_source_bytes",
            "target": "raw_target_bytes",
            "files_per_row": 2,
        },
        **_review_fields(),
        "model_dependent_status": _model_dependent_not_run(),
        "run_eligibility": False,
        "run_eligibility_blockers": [
            "PENDING_HUMAN_REVIEW",
            "MODEL_TOKENIZER_RANDOMIZATION_ENVIRONMENT_NOT_BOUND",
            "MODEL_DEPENDENT_G1_G2_NOT_RUN",
            "MODEL_UPDATE_SCORING_EPSILON_AND_G1_THRESHOLDS_NOT_FROZEN",
            "NO_MODEL_RUN_AUTHORIZATION",
        ],
    }
    _write_json(
        staging_root / "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
        build_manifest,
    )
    return build_manifest


def _preflight_output_targets(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    conflicts = [
        str(output_dir / name)
        for name in ALL_OUTPUT_NAMES
        if (output_dir / name).exists()
    ]
    if conflicts:
        joined = "\n".join(f"  - {path}" for path in conflicts)
        raise ControlledTaskError(
            "refusing to overwrite existing controlled assets:\n" + joined
        )


def _publish_staging(staging_root: Path, output_dir: Path) -> None:
    # Publish the build manifest last.  Its presence means all preceding
    # deliverables were moved into place.
    publish_order = (
        *OUTPUT_DIRECTORIES,
        *PRIMARY_JSONL_FILES,
        *MACHINE_LABEL_FILES,
        *OTHER_OUTPUT_FILES[:-1],
        OTHER_OUTPUT_FILES[-1],
    )
    for name in publish_order:
        source = staging_root / name
        destination = output_dir / name
        if destination.exists():
            raise ControlledTaskError(
                f"refusing late overwrite during publish: {destination}"
            )
        os.replace(source, destination)


def build_controlled_assets(output_dir: Path, seed_root: str) -> dict[str, Any]:
    """Build and publish all assets, refusing any existing output target."""

    if not seed_root or not seed_root.isascii():
        raise ControlledTaskError("seed root must be non-empty ASCII")
    output_dir = output_dir.resolve()
    _preflight_output_targets(output_dir)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".p4-r1-controlled-assets-staging-",
            dir=str(output_dir),
        )
    )
    try:
        manifest = _build_to_staging(
            staging_root=staging_root,
            seed_root=seed_root,
        )
        _publish_staging(staging_root, output_dir)
        return manifest
    finally:
        # The resolved target is a tempfile-created child of output_dir.
        if staging_root.exists():
            resolved_stage = staging_root.resolve()
            if (
                resolved_stage.parent != output_dir
                or not resolved_stage.name.startswith(
                    ".p4-r1-controlled-assets-staging-"
                )
            ):
                raise ControlledTaskError(
                    "refusing to clean an unexpected staging path"
                )
            shutil.rmtree(resolved_stage)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic CPU-only controlled rows and machine-label "
            "manifests. This command never runs a model."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REAL_ASSETS_DIR,
        help=(
            "destination directory; defaults to GPT_PRO_REVIEW_RLVR_V1/"
            "real_assets and refuses to overwrite any target"
        ),
    )
    parser.add_argument(
        "--seed-root",
        default=DEFAULT_SEED_ROOT,
        help="non-empty ASCII namespace root for deterministic generation",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest = build_controlled_assets(args.output_dir, args.seed_root)
    print(
        json.dumps(
            {
                "status": "BUILT_MACHINE_ASSETS_PENDING_HUMAN_REVIEW",
                "experiment_status": manifest["experiment_status"],
                "human_review_status": manifest["human_review_status"],
                "output_dir": str(args.output_dir.resolve()),
                "build_manifest": "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
                "model_dependent_status": "NOT_RUN",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
