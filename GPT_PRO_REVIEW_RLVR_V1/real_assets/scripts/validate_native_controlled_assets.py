#!/usr/bin/env python3
"""Independent, fail-closed validator for the native P4-R1 V2 asset build.

This validator intentionally does not import ``controlled_tasks`` or the asset
builder.  The frozen task oracles, codebooks, verifier arms, byte contracts,
and pre-review safety state are reimplemented here with the Python standard
library only.  It never loads a tokenizer, model, optimizer, or ML framework.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
REVIEW_ROOT = SCRIPT_PATH.parents[2]
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from commitment_core import canonical_json_bytes, sha256_bytes  # noqa: E402


REPORT_SCHEMA_VERSION = "native-controlled-assets-validation-report-v1"
EVIDENCE_BOUNDARY = {
    "same_fpr_evidence": "NOT_SAME_FPR_EVIDENCE",
    "rlvr_mode": "NOT_SAMPLED_RLVR",
    "data_scope": "CALIBRATION_ONLY",
    "audit_status": "NOT_HIDDEN_AUDIT",
    "formal_g1_status": "NOT_FORMAL_G1",
    "scientific_evidence_status": "SCIENTIFIC_EVIDENCE_FALSE",
}
ASSET_SCHEMA_VERSION = "p4-r1-controlled-real-assets-v1"
EXPERIMENT_STATUS = "NOT_RUN"
HUMAN_REVIEW_STATUS = "PENDING_HUMAN_REVIEW"
MACHINE_LABEL_STATUS = "MACHINE_COMPUTED_PENDING_HUMAN_REVIEW"
MODULUS = 7
CANDIDATES = tuple(f"FINAL=K{i}" for i in range(MODULUS))
SHARED_OFFSET = 1
SHARED_RULE_ID = "S_Z7_PLUS1"

TASK_MOD7_SUM = "MOD7_SUM_V1"
TASK_DFA7_FINAL = "DFA7_FINAL_V1"
TASK_MARKED_RANK7 = "MARKED_RANK7_V1"
TASK_PAREN_MAX_DEPTH7 = "PAREN_MAX_DEPTH7_V1"
TASK_IDS = (
    TASK_MOD7_SUM,
    TASK_DFA7_FINAL,
    TASK_MARKED_RANK7,
    TASK_PAREN_MAX_DEPTH7,
)
LOCAL_OFFSETS = {
    TASK_MOD7_SUM: 2,
    TASK_DFA7_FINAL: 3,
    TASK_MARKED_RANK7: 4,
    TASK_PAREN_MAX_DEPTH7: 5,
}
LOCAL_RULE_IDS = {
    TASK_MOD7_SUM: "L_MOD7_PLUS2",
    TASK_DFA7_FINAL: "L_DFA7_PLUS3",
    TASK_MARKED_RANK7: "L_RANK7_PLUS4",
    TASK_PAREN_MAX_DEPTH7: "L_PAREN7_PLUS5",
}
DFA_TRANSITIONS = {
    0: (2, 4),
    1: (5, 0),
    2: (1, 6),
    3: (6, 2),
    4: (0, 5),
    5: (3, 1),
    6: (4, 3),
}

ARMS = (
    "clean",
    "shared_leaky",
    "local_leaky",
    "shared_frozen_probe_control",
    "local_frozen_probe_control",
)

MODEL_STATUS = {
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

ASSET_AUTHORIZATION = {
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

PROTOCOL_AUTHORIZATION = {
    "construct_cpu_only_task_rows_candidate_panels_manifests_and_seals": True,
    "cpu_schema_hash_lineage_overlap_and_bijection_checks": True,
    "cpu_clean_oracle_and_verifier_fpr_checks_after_human_review": True,
    "human_review_required": True,
    "gpu": False,
    "model_weight_load": False,
    "tokenizer_load": False,
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

FILES = {
    "mapping": "REAL_MAPPING_STACKS_V1.jsonl",
    "source": "REAL_SOURCE_BUNDLES_V1.jsonl",
    "calibration": "TARGET_CALIBRATION_REAL_V1.jsonl",
    "audit": "TARGET_AUDIT_REAL_V1.jsonl",
    "verifier": "VERIFIER_REWARD_MANIFEST_V1.jsonl",
    "source_labels": "SOURCE_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "calibration_labels": "TARGET_CALIBRATION_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "audit_labels": "TARGET_AUDIT_MACHINE_LABEL_MANIFEST_V1.jsonl",
    "protocol": "P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json",
    "g1": "G1_OPPORTUNITY_AUDIT_V1.json",
    "seal": "TARGET_AUDIT_SEAL_RECEIPT.json",
    "prereg": "RANDOMIZATION_MODEL_ENV_PREREG_V1.json",
    "label_index": "MACHINE_LABEL_MANIFEST_INDEX_V1.json",
    "build": "CONTROLLED_ASSET_BUILD_MANIFEST_V1.json",
}
ARTIFACT_FILENAMES = tuple(
    FILES[key]
    for key in (
        "mapping",
        "source",
        "calibration",
        "audit",
        "verifier",
        "source_labels",
        "calibration_labels",
        "audit_labels",
        "protocol",
        "g1",
        "seal",
        "prereg",
        "label_index",
    )
)
MACHINE_LABEL_FILENAMES = (
    FILES["source_labels"],
    FILES["calibration_labels"],
    FILES["audit_labels"],
)

SHA256_RE = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def exact_dict(value: Any, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def is_int(value: Any) -> bool:
    return type(value) is int


def codebook_record(mapping_id: str, slot: str) -> dict[str, Any]:
    definitions = {
        "mapping_0": {
            "A": (1, 0),
            "B": (2, 1),
        },
        "mapping_1": {
            "A": (3, 2),
            "B": (5, 4),
        },
    }
    multiplier, intercept = definitions[mapping_id][slot]
    return {
        "codebook_id": f"{mapping_id}_{slot}",
        "task_slot": slot,
        "formula": f"K(({multiplier}*z+{intercept}) mod 7)",
        "multiplier_mod7": multiplier,
        "intercept_mod7": intercept,
        "latent_to_candidate": [
            CANDIDATES[(multiplier * z + intercept) % MODULUS]
            for z in range(MODULUS)
        ],
    }


def candidate_for(codebook: Mapping[str, Any], z: int) -> str:
    return codebook["latent_to_candidate"][z]


def task_contract() -> dict[str, Any]:
    return {
        "modulus": 7,
        "candidate_order": list(CANDIDATES),
        "shared_offset_mod7": 1,
        "shared_rule_id": SHARED_RULE_ID,
        "local_offsets_mod7": dict(LOCAL_OFFSETS),
        "local_rule_ids": dict(LOCAL_RULE_IDS),
        "dfa_transitions": {
            str(state): {"x": pair[0], "y": pair[1]}
            for state, pair in DFA_TRANSITIONS.items()
        },
        "tasks": {
            TASK_MOD7_SUM: {
                "task_semantics": (
                    "sum two integers a,b in 0..20, then reduce modulo 7"
                ),
                "canonical_answer": "z = (a+b) mod 7",
                "local_offset": 2,
            },
            TASK_DFA7_FINAL: {
                "task_semantics": (
                    "execute the frozen x/y transition table from an initial "
                    "state over a length-2-to-6 sequence"
                ),
                "canonical_answer": "z = index of the final state Qz",
                "local_offset": 3,
            },
            TASK_MARKED_RANK7: {
                "task_semantics": (
                    "rank seven distinct scores and locate the marked item"
                ),
                "canonical_answer": (
                    "z = zero-based ascending rank of the marked item"
                ),
                "local_offset": 4,
            },
            TASK_PAREN_MAX_DEPTH7: {
                "task_semantics": "scan a pure balanced-parentheses string",
                "canonical_answer": (
                    "z = maximum parenthesis nesting depth minus 1"
                ),
                "local_offset": 5,
            },
        },
    }


TASK_CONTRACT = task_contract()
TASK_CONTRACT_SHA256 = sha256_bytes(canonical_json_bytes(TASK_CONTRACT))


STACKS = (
    {
        "mapping_stack_id": "TP1-M0-A_TO_B",
        "task_pair_id": "TP1_MOD7SUM_DFA7",
        "mapping_id": "mapping_0",
        "mirror_role": "A_TO_B",
        "mirror_counterpart_stack_id": "TP1-M0-B_TO_A",
        "task_a": TASK_MOD7_SUM,
        "task_b": TASK_DFA7_FINAL,
        "source_slot": "A",
        "target_slot": "B",
        "source_task_id": TASK_MOD7_SUM,
        "target_task_id": TASK_DFA7_FINAL,
    },
    {
        "mapping_stack_id": "TP1-M0-B_TO_A",
        "task_pair_id": "TP1_MOD7SUM_DFA7",
        "mapping_id": "mapping_0",
        "mirror_role": "B_TO_A",
        "mirror_counterpart_stack_id": "TP1-M0-A_TO_B",
        "task_a": TASK_MOD7_SUM,
        "task_b": TASK_DFA7_FINAL,
        "source_slot": "B",
        "target_slot": "A",
        "source_task_id": TASK_DFA7_FINAL,
        "target_task_id": TASK_MOD7_SUM,
    },
    {
        "mapping_stack_id": "TP1-M1-A_TO_B",
        "task_pair_id": "TP1_MOD7SUM_DFA7",
        "mapping_id": "mapping_1",
        "mirror_role": "A_TO_B",
        "mirror_counterpart_stack_id": "TP1-M1-B_TO_A",
        "task_a": TASK_MOD7_SUM,
        "task_b": TASK_DFA7_FINAL,
        "source_slot": "A",
        "target_slot": "B",
        "source_task_id": TASK_MOD7_SUM,
        "target_task_id": TASK_DFA7_FINAL,
    },
    {
        "mapping_stack_id": "TP1-M1-B_TO_A",
        "task_pair_id": "TP1_MOD7SUM_DFA7",
        "mapping_id": "mapping_1",
        "mirror_role": "B_TO_A",
        "mirror_counterpart_stack_id": "TP1-M1-A_TO_B",
        "task_a": TASK_MOD7_SUM,
        "task_b": TASK_DFA7_FINAL,
        "source_slot": "B",
        "target_slot": "A",
        "source_task_id": TASK_DFA7_FINAL,
        "target_task_id": TASK_MOD7_SUM,
    },
    {
        "mapping_stack_id": "TP2-M0-A_TO_B",
        "task_pair_id": "TP2_RANK7_PARENDEPTH7",
        "mapping_id": "mapping_0",
        "mirror_role": "A_TO_B",
        "mirror_counterpart_stack_id": "TP2-M0-B_TO_A",
        "task_a": TASK_MARKED_RANK7,
        "task_b": TASK_PAREN_MAX_DEPTH7,
        "source_slot": "A",
        "target_slot": "B",
        "source_task_id": TASK_MARKED_RANK7,
        "target_task_id": TASK_PAREN_MAX_DEPTH7,
    },
    {
        "mapping_stack_id": "TP2-M0-B_TO_A",
        "task_pair_id": "TP2_RANK7_PARENDEPTH7",
        "mapping_id": "mapping_0",
        "mirror_role": "B_TO_A",
        "mirror_counterpart_stack_id": "TP2-M0-A_TO_B",
        "task_a": TASK_MARKED_RANK7,
        "task_b": TASK_PAREN_MAX_DEPTH7,
        "source_slot": "B",
        "target_slot": "A",
        "source_task_id": TASK_PAREN_MAX_DEPTH7,
        "target_task_id": TASK_MARKED_RANK7,
    },
    {
        "mapping_stack_id": "TP2-M1-A_TO_B",
        "task_pair_id": "TP2_RANK7_PARENDEPTH7",
        "mapping_id": "mapping_1",
        "mirror_role": "A_TO_B",
        "mirror_counterpart_stack_id": "TP2-M1-B_TO_A",
        "task_a": TASK_MARKED_RANK7,
        "task_b": TASK_PAREN_MAX_DEPTH7,
        "source_slot": "A",
        "target_slot": "B",
        "source_task_id": TASK_MARKED_RANK7,
        "target_task_id": TASK_PAREN_MAX_DEPTH7,
    },
    {
        "mapping_stack_id": "TP2-M1-B_TO_A",
        "task_pair_id": "TP2_RANK7_PARENDEPTH7",
        "mapping_id": "mapping_1",
        "mirror_role": "B_TO_A",
        "mirror_counterpart_stack_id": "TP2-M1-A_TO_B",
        "task_a": TASK_MARKED_RANK7,
        "task_b": TASK_PAREN_MAX_DEPTH7,
        "source_slot": "B",
        "target_slot": "A",
        "source_task_id": TASK_PAREN_MAX_DEPTH7,
        "target_task_id": TASK_MARKED_RANK7,
    },
)
STACK_BY_ID = {stack["mapping_stack_id"]: stack for stack in STACKS}


class OracleError(ValueError):
    """Invalid controlled-task input."""


def evaluate_instance(
    task_id: str, instance: Any
) -> tuple[int, dict[str, Any]]:
    if not isinstance(instance, dict):
        raise OracleError("task_input must be an object")

    if task_id == TASK_MOD7_SUM:
        if set(instance) != {"a", "b"}:
            raise OracleError("MOD7_SUM fields must be exactly a,b")
        a, b = instance["a"], instance["b"]
        if (
            not is_int(a)
            or not is_int(b)
            or not 0 <= a <= 20
            or not 0 <= b <= 20
        ):
            raise OracleError("MOD7_SUM requires integer a,b in 0..20")
        total = a + b
        z = total % MODULUS
        return z, {
            "operation": "two_integer_sum_then_mod7",
            "a": a,
            "b": b,
            "sum": total,
            "remainder": z,
        }

    if task_id == TASK_DFA7_FINAL:
        if set(instance) != {"initial_state", "sequence", "transitions"}:
            raise OracleError(
                "DFA7_FINAL fields must be initial_state,sequence,transitions"
            )
        initial = instance["initial_state"]
        sequence = instance["sequence"]
        transitions = instance["transitions"]
        expected_transitions = {
            str(state): {"x": pair[0], "y": pair[1]}
            for state, pair in DFA_TRANSITIONS.items()
        }
        if not is_int(initial) or not 0 <= initial < MODULUS:
            raise OracleError("DFA initial_state must be an integer in 0..6")
        if (
            not isinstance(sequence, str)
            or not 2 <= len(sequence) <= 6
            or any(symbol not in "xy" for symbol in sequence)
        ):
            raise OracleError("DFA sequence must be x/y and length 2..6")
        if transitions != expected_transitions:
            raise OracleError("DFA transition table is not the frozen table")
        state = initial
        visited = [state]
        for symbol in sequence:
            state = transitions[str(state)][symbol]
            visited.append(state)
        return state, {
            "operation": "explicit_dfa_execution",
            "visited_states": visited,
            "final_state": state,
        }

    if task_id == TASK_MARKED_RANK7:
        if set(instance) != {"items"}:
            raise OracleError("MARKED_RANK7 fields must be exactly items")
        items = instance["items"]
        if not isinstance(items, list) or len(items) != MODULUS:
            raise OracleError("MARKED_RANK7 needs seven items")
        values: list[int] = []
        marked_values: list[int] = []
        for item in items:
            if not exact_dict(item, {"value", "marked"}):
                raise OracleError(
                    "each MARKED_RANK7 item needs value and marked only"
                )
            value, marked = item["value"], item["marked"]
            if not is_int(value) or type(marked) is not bool:
                raise OracleError("rank item field types are invalid")
            values.append(value)
            if marked:
                marked_values.append(value)
        if len(set(values)) != MODULUS:
            raise OracleError("rank values must be distinct")
        if len(marked_values) != 1:
            raise OracleError("exactly one rank item must be marked")
        marked_value = marked_values[0]
        z = sum(value < marked_value for value in values)
        return z, {
            "operation": "count_values_strictly_less_than_marked",
            "ascending_values": sorted(values),
            "marked_value": marked_value,
            "rank": z,
        }

    if task_id == TASK_PAREN_MAX_DEPTH7:
        if set(instance) != {"text"}:
            raise OracleError("PAREN_MAX_DEPTH7 fields must be exactly text")
        text = instance["text"]
        if (
            not isinstance(text, str)
            or not text
            or any(char not in "()" for char in text)
        ):
            raise OracleError("parenthesis text must be nonempty and pure")
        depth = 0
        maximum = 0
        trace: list[int] = []
        for char in text:
            depth += 1 if char == "(" else -1
            if depth < 0:
                raise OracleError("parenthesis text closes below zero")
            maximum = max(maximum, depth)
            trace.append(depth)
        if depth != 0:
            raise OracleError("parenthesis text is not balanced")
        if not 1 <= maximum <= MODULUS:
            raise OracleError("maximum parenthesis depth must be 1..7")
        z = maximum - 1
        return z, {
            "operation": "maximum_parenthesis_depth_minus_one",
            "maximum_depth": maximum,
            "canonical_z": z,
            "final_depth": 0,
            "character_count": len(text),
            "depth_trace_sha256": sha256_bytes(
                ",".join(str(value) for value in trace).encode("ascii")
            ),
        }

    raise OracleError(f"unknown task_id {task_id!r}")


class HashStream:
    """Independent implementation of the frozen SHA-256 counter stream."""

    def __init__(self, namespace: str) -> None:
        if not isinstance(namespace, str) or not namespace or not namespace.isascii():
            raise OracleError("seed namespace must be nonempty ASCII")
        self.key = hashlib.sha256(namespace.encode("ascii")).digest()
        self.counter = 0

    def word(self) -> int:
        material = self.key + self.counter.to_bytes(16, "big")
        self.counter += 1
        return int.from_bytes(hashlib.sha256(material).digest(), "big")

    def randbelow(self, bound: int) -> int:
        span = 1 << 256
        limit = span - (span % bound)
        while True:
            value = self.word()
            if value < limit:
                return value % bound

    def choice(self, values: Sequence[Any]) -> Any:
        return values[self.randbelow(len(values))]

    def shuffle(self, values: Iterable[Any]) -> list[Any]:
        result = list(values)
        for index in range(len(result) - 1, 0, -1):
            other = self.randbelow(index + 1)
            result[index], result[other] = result[other], result[index]
        return result

    def sample(self, values: Sequence[Any], count: int) -> list[Any]:
        return self.shuffle(values)[:count]


def _paren_chain(depth: int, stream: HashStream) -> str:
    if depth == 1:
        return "()"
    child = _paren_chain(depth - 1, stream)
    children = [child] + ["()" for _ in range(stream.randbelow(4))]
    return "(" + "".join(stream.shuffle(children)) + ")"


def generate_instance(task_id: str, z: int, namespace: str) -> dict[str, Any]:
    stream = HashStream(namespace)
    if task_id == TASK_MOD7_SUM:
        possibilities = [
            (a, b)
            for a in range(21)
            for b in range(21)
            if (a + b) % MODULUS == z
        ]
        a, b = stream.choice(possibilities)
        return {"a": a, "b": b}

    if task_id == TASK_DFA7_FINAL:
        possibilities: list[tuple[int, str]] = []
        for length in range(2, 7):
            for symbols in itertools.product("xy", repeat=length):
                sequence = "".join(symbols)
                for initial in range(MODULUS):
                    state = initial
                    for symbol in sequence:
                        state = DFA_TRANSITIONS[state][
                            0 if symbol == "x" else 1
                        ]
                    if state == z:
                        possibilities.append((initial, sequence))
        initial, sequence = stream.choice(possibilities)
        return {
            "initial_state": initial,
            "sequence": sequence,
            "transitions": {
                str(state): {"x": pair[0], "y": pair[1]}
                for state, pair in DFA_TRANSITIONS.items()
            },
        }

    if task_id == TASK_MARKED_RANK7:
        scores = sorted(stream.sample(tuple(range(10, 100)), MODULUS))
        marked = scores[z]
        return {
            "items": [
                {"value": value, "marked": value == marked}
                for value in stream.shuffle(scores)
            ]
        }

    if task_id == TASK_PAREN_MAX_DEPTH7:
        maximum = z + 1
        components = [_paren_chain(maximum, stream)]
        for _ in range(1 + stream.randbelow(12)):
            components.append(
                _paren_chain(1 + stream.randbelow(maximum), stream)
            )
        return {"text": "".join(stream.shuffle(components))}

    raise OracleError(f"unknown task_id {task_id!r}")


def render_prompt(
    task_id: str, instance: Mapping[str, Any], codebook: Mapping[str, Any]
) -> bytes:
    codebook_line = ";".join(
        f"z{z}->K{int(candidate[-1])}"
        for z, candidate in enumerate(codebook["latent_to_candidate"])
    )
    if task_id == TASK_MOD7_SUM:
        task_lines = [
            "RULE=Compute z=(a+b) modulo 7.",
            f"A={instance['a']}",
            f"B={instance['b']}",
        ]
    elif task_id == TASK_DFA7_FINAL:
        transition_line = ";".join(
            (
                f"Q{state}:x->Q{instance['transitions'][str(state)]['x']},"
                f"y->Q{instance['transitions'][str(state)]['y']}"
            )
            for state in range(MODULUS)
        )
        task_lines = [
            "RULE=Start at INITIAL_STATE and follow SEQUENCE from left to right.",
            f"INITIAL_STATE=Q{instance['initial_state']}",
            "SEQUENCE=" + instance["sequence"],
            "TRANSITIONS=" + transition_line,
        ]
    elif task_id == TASK_MARKED_RANK7:
        items = ",".join(
            f"{'*' if item['marked'] else ''}{item['value']}"
            for item in instance["items"]
        )
        task_lines = [
            (
                "RULE=z is the number of listed values strictly less than "
                "the value marked *."
            ),
            "ITEMS=" + items,
        ]
    elif task_id == TASK_PAREN_MAX_DEPTH7:
        task_lines = [
            (
                "RULE=The string is balanced; z is maximum parenthesis "
                "nesting depth minus 1."
            ),
            "TEXT=" + instance["text"],
        ]
    else:
        raise OracleError(f"unknown task_id {task_id!r}")

    lines = [
        f"TASK={task_id}",
        "CANONICAL_CLASS=z in {0,1,2,3,4,5,6}",
        "CODEBOOK=" + codebook_line,
        "CANDIDATES=" + "|".join(CANDIDATES),
        *task_lines,
        "ANSWER_FORMAT=Return exactly one candidate and no other text.",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def solve_prompt_text(prompt_text: str) -> int:
    """Solve the final model-visible prompt without consulting row metadata."""

    if not isinstance(prompt_text, str) or not prompt_text.isascii():
        raise OracleError("prompt must be non-empty ASCII text")
    if not prompt_text.endswith("\n") or "\r" in prompt_text:
        raise OracleError("prompt must use canonical LF termination")
    parsed: dict[str, str] = {}
    for line in prompt_text[:-1].split("\n"):
        if "=" not in line:
            raise OracleError("prompt line lacks key=value form")
        key, value = line.split("=", 1)
        if not key or key in parsed:
            raise OracleError("prompt contains an empty or duplicate key")
        parsed[key] = value

    task_id = parsed.get("TASK")
    if task_id not in TASK_IDS:
        raise OracleError("prompt TASK is missing or unknown")
    if parsed.get("CANONICAL_CLASS") != "z in {0,1,2,3,4,5,6}":
        raise OracleError("prompt canonical-class declaration changed")
    if parsed.get("CANDIDATES") != "|".join(CANDIDATES):
        raise OracleError("prompt candidate declaration changed")
    if parsed.get("ANSWER_FORMAT") != "Return exactly one candidate and no other text.":
        raise OracleError("prompt answer-format declaration changed")

    if task_id == TASK_MOD7_SUM:
        expected = {
            "TASK", "CANONICAL_CLASS", "CODEBOOK", "CANDIDATES",
            "RULE", "A", "B", "ANSWER_FORMAT",
        }
        if set(parsed) != expected or parsed["RULE"] != "Compute z=(a+b) modulo 7.":
            raise OracleError("MOD7 prompt fields or rule changed")
        try:
            a, b = int(parsed["A"]), int(parsed["B"])
        except ValueError as exc:
            raise OracleError("MOD7 prompt integers are invalid") from exc
        return evaluate_instance(task_id, {"a": a, "b": b})[0]

    if task_id == TASK_DFA7_FINAL:
        expected = {
            "TASK", "CANONICAL_CLASS", "CODEBOOK", "CANDIDATES",
            "RULE", "INITIAL_STATE", "SEQUENCE", "TRANSITIONS",
            "ANSWER_FORMAT",
        }
        if set(parsed) != expected or parsed["RULE"] != (
            "Start at INITIAL_STATE and follow SEQUENCE from left to right."
        ):
            raise OracleError("DFA prompt fields or rule changed")
        match = re.fullmatch(r"Q([0-6])", parsed["INITIAL_STATE"])
        if match is None:
            raise OracleError("DFA prompt initial state is invalid")
        transitions: dict[str, dict[str, int]] = {}
        for part in parsed["TRANSITIONS"].split(";"):
            hit = re.fullmatch(r"Q([0-6]):x->Q([0-6]),y->Q([0-6])", part)
            if hit is None:
                raise OracleError("DFA prompt transition table is invalid")
            state, x_state, y_state = map(int, hit.groups())
            if str(state) in transitions:
                raise OracleError("DFA prompt repeats a transition state")
            transitions[str(state)] = {"x": x_state, "y": y_state}
        return evaluate_instance(
            task_id,
            {
                "initial_state": int(match.group(1)),
                "sequence": parsed["SEQUENCE"],
                "transitions": transitions,
            },
        )[0]

    if task_id == TASK_MARKED_RANK7:
        expected = {
            "TASK", "CANONICAL_CLASS", "CODEBOOK", "CANDIDATES",
            "RULE", "ITEMS", "ANSWER_FORMAT",
        }
        if set(parsed) != expected or parsed["RULE"] != (
            "z is the number of listed values strictly less than the value marked *."
        ):
            raise OracleError("MARKED_RANK prompt fields or rule changed")
        items: list[dict[str, Any]] = []
        for token in parsed["ITEMS"].split(","):
            marked = token.startswith("*")
            digits = token[1:] if marked else token
            if re.fullmatch(r"[0-9]+", digits) is None:
                raise OracleError("MARKED_RANK item is not an unsigned integer")
            items.append({"value": int(digits), "marked": marked})
        return evaluate_instance(task_id, {"items": items})[0]

    expected = {
        "TASK", "CANONICAL_CLASS", "CODEBOOK", "CANDIDATES",
        "RULE", "TEXT", "ANSWER_FORMAT",
    }
    if set(parsed) != expected or parsed["RULE"] != (
        "The string is balanced; z is maximum parenthesis nesting depth minus 1."
    ):
        raise OracleError("PAREN prompt fields or rule changed")
    return evaluate_instance(task_id, {"text": parsed["TEXT"]})[0]


def label_triplet(
    task_id: str, z: int, codebook: Mapping[str, Any]
) -> dict[str, Any]:
    shared_z = (z + SHARED_OFFSET) % MODULUS
    local_z = (z + LOCAL_OFFSETS[task_id]) % MODULUS
    return {
        "gold_z": z,
        "shared_bug_z": shared_z,
        "local_bug_z": local_z,
        "gold_candidate": candidate_for(codebook, z),
        "shared_bug_candidate": candidate_for(codebook, shared_z),
        "local_bug_candidate": candidate_for(codebook, local_z),
        "shared_offset_mod7": SHARED_OFFSET,
        "shared_rule_id": SHARED_RULE_ID,
        "local_offset_mod7": LOCAL_OFFSETS[task_id],
        "local_rule_id": LOCAL_RULE_IDS[task_id],
    }


def verifier_arms(
    task_id: str, z: int, codebook: Mapping[str, Any]
) -> list[dict[str, Any]]:
    labels = label_triplet(task_id, z, codebook)
    gold = labels["gold_candidate"]
    definitions = (
        (
            "clean",
            (gold,),
            "capability_and_optimizer_drift_guardrail",
            "UNRESOLVED_CLEAN_ARM_UPDATE_SEMANTICS",
        ),
        (
            "shared_leaky",
            (gold, labels["shared_bug_candidate"]),
            "future_shared_verifier_error_update_if_separately_authorized",
            "ONE_STEP_UPDATE_RECIPE_NOT_FROZEN",
        ),
        (
            "local_leaky",
            (gold, labels["local_bug_candidate"]),
            "future_task_local_verifier_error_update_if_separately_authorized",
            "ONE_STEP_UPDATE_RECIPE_NOT_FROZEN",
        ),
        (
            "shared_frozen_probe_control",
            (gold, labels["shared_bug_candidate"]),
            "frozen_measurement_control",
            "NO_UPDATE_BY_DEFINITION",
        ),
        (
            "local_frozen_probe_control",
            (gold, labels["local_bug_candidate"]),
            "frozen_measurement_control",
            "NO_UPDATE_BY_DEFINITION",
        ),
    )
    result = []
    for arm_id, accepted, role, future_status in definitions:
        vector = [int(candidate in accepted) for candidate in CANDIDATES]
        wrong = sum(vector) - vector[CANDIDATES.index(gold)]
        result.append(
            {
                "arm_id": arm_id,
                "role": role,
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "future_update_status": future_status,
                "selection_exposure_adjustment": False,
                "accepted_candidates": list(accepted),
                "reward_vector_candidate_order": list(CANDIDATES),
                "reward_vector": vector,
                "positive_count": sum(vector),
                "wrong_positive_count": wrong,
                "online_fpr_definition": (
                    "accepted wrong candidates / all wrong candidates"
                ),
                "online_fpr_exact": f"{wrong}/6",
                "accepted_wrong_reward_mass_count": wrong,
                "candidate_count": 7,
                "candidate_exposure_contract": "IDENTICAL_FIXED_SEVEN",
            }
        )
    return result


def class_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(row.get("canonical_z") for row in rows)
    return {str(z): counts[z] for z in range(MODULUS)}


def panel_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    material = {
        "ordered_row_ids": [row.get("row_id") for row in rows],
        "ordered_row_bytes_sha256": [
            row.get("row_bytes", {}).get("sha256")
            if isinstance(row.get("row_bytes"), dict)
            else None
            for row in rows
        ],
        "ordered_prompt_sha256": [
            row.get("prompt_bytes", {}).get("sha256")
            if isinstance(row.get("prompt_bytes"), dict)
            else None
            for row in rows
        ],
    }
    return sha256_bytes(canonical_json_bytes(material))


def review_fields() -> dict[str, Any]:
    return {
        "machine_label_status": MACHINE_LABEL_STATUS,
        "human_review_status": HUMAN_REVIEW_STATUS,
        "human_review_receipt": None,
    }


def local_error_rules() -> dict[str, Any]:
    return {
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
    }


def prereg_blockers() -> list[str]:
    return [
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
    ]


class NativeValidator:
    """Stateful validator that records all detected violations."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.errors: list[dict[str, str]] = []
        self.counts: dict[str, int] = {}
        self.metrics: dict[str, Any] = {
            "prompt_visible_semantics_mismatches": 0,
            "prompt_visible_parse_errors": 0,
        }
        self.data: dict[str, Any] = {}
        self.file_bytes: dict[str, bytes] = {}
        self.rows_by_id: dict[str, dict[str, Any]] = {}
        self.traces_by_id: dict[str, dict[str, Any]] = {}
        self.raw_relpaths: set[str] = set()

    def error(self, code: str, path: str, message: str) -> None:
        if len(self.errors) < 5000:
            self.errors.append(
                {"code": code, "path": path, "message": message}
            )

    def expect(
        self,
        actual: Any,
        expected: Any,
        code: str,
        path: str,
        message: str | None = None,
    ) -> bool:
        if actual != expected:
            self.error(
                code,
                path,
                message
                or f"expected {expected!r}, got {actual!r}",
            )
            return False
        return True

    def _read_json(self, filename: str) -> Any:
        path = self.root / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.error("file_read_error", filename, str(exc))
            return None
        self.file_bytes[filename] = raw
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.error("invalid_json", filename, str(exc))
            return None

    def _read_jsonl(self, filename: str) -> list[Any] | None:
        path = self.root / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.error("file_read_error", filename, str(exc))
            return None
        self.file_bytes[filename] = raw
        if raw and not raw.endswith(b"\n"):
            self.error(
                "jsonl_missing_final_lf",
                filename,
                "JSONL must end with LF",
            )
        if b"\r" in raw:
            self.error(
                "jsonl_non_lf_newline",
                filename,
                "JSONL contains CR bytes",
            )
        rows: list[Any] = []
        for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
            path_label = f"{filename}:{line_number}"
            if not line.endswith(b"\n"):
                self.error(
                    "jsonl_line_missing_lf",
                    path_label,
                    "line is not LF terminated",
                )
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self.error("invalid_jsonl_record", path_label, str(exc))
                continue
            rows.append(row)
            try:
                expected_line = canonical_json_bytes(row)
            except (TypeError, ValueError, UnicodeError) as exc:
                self.error("noncanonical_jsonl", path_label, str(exc))
                continue
            if line != expected_line:
                self.error(
                    "noncanonical_jsonl",
                    path_label,
                    "record is not canonical JSON ASCII plus LF",
                )
        return rows

    def _check_review_state(self, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            self.error("record_not_object", path, "record must be an object")
            return
        self.expect(
            value.get("experiment_status"),
            EXPERIMENT_STATUS,
            "unsafe_experiment_status",
            f"{path}.experiment_status",
        )
        self.expect(
            value.get("scientific_evidence"),
            False,
            "unsafe_scientific_evidence",
            f"{path}.scientific_evidence",
        )
        if "machine_label_status" in value:
            self.expect(
                value.get("machine_label_status"),
                MACHINE_LABEL_STATUS,
                "machine_label_status_mismatch",
                f"{path}.machine_label_status",
            )
        if "human_review_status" in value:
            self.expect(
                value.get("human_review_status"),
                HUMAN_REVIEW_STATUS,
                "unsafe_human_review_status",
                f"{path}.human_review_status",
            )
        if "human_review_receipt" in value:
            self.expect(
                value.get("human_review_receipt"),
                None,
                "unexpected_human_review_receipt",
                f"{path}.human_review_receipt",
            )
        if "model_dependent_status" in value:
            self.expect(
                value.get("model_dependent_status"),
                MODEL_STATUS,
                "unsafe_model_dependent_status",
                f"{path}.model_dependent_status",
            )
        for key in ("run_eligible", "run_eligibility"):
            if key in value:
                self.expect(
                    value.get(key),
                    False,
                    "unsafe_run_eligibility",
                    f"{path}.{key}",
                )

    def _safe_raw_path(self, relpath: Any, path: str) -> Path | None:
        if (
            not isinstance(relpath, str)
            or not relpath
            or "\\" in relpath
            or relpath.startswith("/")
            or re.match(r"^[A-Za-z]:", relpath)
        ):
            self.error(
                "unsafe_raw_relpath",
                path,
                "raw path must be a relative forward-slash path",
            )
            return None
        parts = relpath.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            self.error(
                "unsafe_raw_relpath",
                path,
                "raw path contains an empty, dot, or parent component",
            )
            return None
        try:
            root_resolved = self.root.resolve()
            resolved = root_resolved.joinpath(*parts).resolve()
            if root_resolved != resolved and root_resolved not in resolved.parents:
                self.error(
                    "raw_path_escape",
                    path,
                    "raw path resolves outside the asset root",
                )
                return None
            return resolved
        except OSError as exc:
            self.error("raw_path_resolution_error", path, str(exc))
            return None

    def _validate_raw_descriptor(
        self,
        descriptor: Any,
        expected: dict[str, Any],
        expected_bytes: bytes,
        path: str,
        kind: str,
    ) -> None:
        self.expect(
            descriptor,
            expected,
            f"{kind}_descriptor_mismatch",
            path,
        )
        if not isinstance(descriptor, dict):
            return
        relpath = descriptor.get("raw_relpath")
        target = self._safe_raw_path(relpath, f"{path}.raw_relpath")
        if target is None:
            return
        if isinstance(relpath, str):
            if relpath in self.raw_relpaths:
                self.error(
                    "raw_relpath_reuse",
                    f"{path}.raw_relpath",
                    f"raw path reused: {relpath}",
                )
            self.raw_relpaths.add(relpath)
        try:
            actual_bytes = target.read_bytes()
        except OSError as exc:
            self.error(f"{kind}_read_error", str(target), str(exc))
            return
        if actual_bytes != expected_bytes:
            self.error(
                f"{kind}_exact_bytes_mismatch",
                str(target),
                "raw bytes differ from independent reconstruction",
            )
        if descriptor.get("byte_length") != len(actual_bytes):
            self.error(
                f"{kind}_byte_length_mismatch",
                str(target),
                "descriptor byte length differs from raw file",
            )
        if descriptor.get("sha256") != sha256_bytes(actual_bytes):
            self.error(
                f"{kind}_hash_mismatch",
                str(target),
                "descriptor SHA-256 differs from raw file",
            )

    def _validate_protocol(self, protocol: Any) -> None:
        path = FILES["protocol"]
        if not isinstance(protocol, dict):
            self.error("protocol_not_object", path, "protocol must be an object")
            return
        expected_scalars = {
            "schema_version": "p4-r1-real-protocol-v2-pre-review",
            "created_local_date": "2026-07-30",
            "protocol_status": "PRE_REVIEW",
            "experiment_status": "NOT_RUN",
            "human_review_status": "PENDING_HUMAN_REVIEW",
            "run_eligible": False,
            "authoritative_preregistration": False,
            "real_assets_bound": False,
            "scientific_evidence": False,
            "scientific_unit": {
                "unit": "mapping_stack",
                "screen_layout": "2 task_pairs x 2 mappings x 2 mirrors",
                "screen_stack_count": 8,
                "direction_is_mirror_not_new_task_pair": True,
                "prompts_candidates_and_rows_are_independent_units": False,
                "technical_seeds_are_independent_units": False,
            },
        }
        for key, expected in expected_scalars.items():
            self.expect(
                protocol.get(key),
                expected,
                "protocol_value_mismatch",
                f"{path}.{key}",
            )
        self.expect(
            protocol.get("authorization"),
            PROTOCOL_AUTHORIZATION,
            "protocol_authorization_mismatch",
            f"{path}.authorization",
        )
        task_interface = protocol.get("task_interface")
        if not isinstance(task_interface, dict):
            self.error(
                "protocol_task_interface_invalid",
                f"{path}.task_interface",
                "task_interface must be an object",
            )
        else:
            expected_interface = {
                "latent_answer_domain": "Z7",
                "latent_values": list(range(7)),
                "output_grammar": "FINAL=Kj",
                "candidate_count_per_row": 7,
                "wrong_candidate_count_per_row": 6,
                "candidate_order": list(CANDIDATES),
                "gold_candidate_formula": (
                    "FINAL=K[f(mapping_id, task_role, z)]"
                ),
                "shared_wrong_candidate_formula": (
                    "FINAL=K[f(mapping_id, task_role, (z+1) mod 7)]"
                ),
                "local_wrong_candidate_formula": (
                    "FINAL=K[f(mapping_id, task_role, "
                    "(z+local_shift[task_id]) mod 7)]"
                ),
                "all_candidates_required_on_every_row": True,
                "candidate_identity_and_order_frozen_within_stack": True,
            }
            self.expect(
                task_interface,
                expected_interface,
                "protocol_task_interface_mismatch",
                f"{path}.task_interface",
            )

        expected_tasks = [
            {
                "task_id": TASK_MOD7_SUM,
                "task_pair_id": "TP1_MOD7SUM_DFA7",
                "task_role": "A",
                "input_semantics": (
                    "exactly two integers a,b with a,b in 0..20"
                ),
                "latent_answer_formula": "z=(a+b) mod 7",
                "clean_oracle_family": "integer_modular_arithmetic",
                "local_rule_id": "L_MOD7_PLUS2",
                "local_shift_mod7": 2,
            },
            {
                "task_id": TASK_DFA7_FINAL,
                "task_pair_id": "TP1_MOD7SUM_DFA7",
                "task_role": "B",
                "input_semantics": (
                    "symbol sequence evaluated by a fully specified "
                    "deterministic seven-state automaton"
                ),
                "latent_answer_formula": (
                    "z=encoded_final_state_after_full_input"
                ),
                "clean_oracle_family": (
                    "deterministic_finite_automaton_execution"
                ),
                "local_rule_id": "L_DFA7_PLUS3",
                "local_shift_mod7": 3,
            },
            {
                "task_id": TASK_MARKED_RANK7,
                "task_pair_id": "TP2_RANK7_PARENDEPTH7",
                "task_role": "A",
                "input_semantics": (
                    "ordered seven-item sequence containing exactly one "
                    "marked item"
                ),
                "latent_answer_formula": (
                    "z=zero_based_rank_of_marked_item"
                ),
                "clean_oracle_family": "marked_position_extraction",
                "local_rule_id": "L_RANK7_PLUS4",
                "local_shift_mod7": 4,
            },
            {
                "task_id": TASK_PAREN_MAX_DEPTH7,
                "task_pair_id": "TP2_RANK7_PARENDEPTH7",
                "task_role": "B",
                "input_semantics": "well-formed parenthesis sequence",
                "latent_answer_formula": (
                    "z=maximum_nesting_depth-1, with "
                    "maximum_nesting_depth in 1..7"
                ),
                "clean_oracle_family": "parenthesis_stack_depth",
                "local_rule_id": "L_PAREN7_PLUS5",
                "local_shift_mod7": 5,
            },
        ]
        self.expect(
            protocol.get("tasks"),
            expected_tasks,
            "protocol_tasks_mismatch",
            f"{path}.tasks",
        )
        expected_pairs = [
            {
                "task_pair_id": "TP1_MOD7SUM_DFA7",
                "task_A": TASK_MOD7_SUM,
                "task_B": TASK_DFA7_FINAL,
                "task_disjointness_basis": (
                    "modular arithmetic over integer sequences versus "
                    "deterministic automaton execution over symbol sequences"
                ),
                "mirrors": ["A_TO_B", "B_TO_A"],
                "A_TO_B_and_B_TO_A_are": (
                    "two_directional_mirrors_of_one_task_pair"
                ),
            },
            {
                "task_pair_id": "TP2_RANK7_PARENDEPTH7",
                "task_A": TASK_MARKED_RANK7,
                "task_B": TASK_PAREN_MAX_DEPTH7,
                "task_disjointness_basis": (
                    "marked-item rank extraction versus syntactic "
                    "nesting-depth computation"
                ),
                "mirrors": ["A_TO_B", "B_TO_A"],
                "A_TO_B_and_B_TO_A_are": (
                    "two_directional_mirrors_of_one_task_pair"
                ),
            },
        ]
        self.expect(
            protocol.get("task_pairs"),
            expected_pairs,
            "protocol_task_pairs_mismatch",
            f"{path}.task_pairs",
        )
        expected_mappings = [
            {
                "mapping_id": "mapping_0",
                "task_role_A": {
                    "formula": "f_A(z)=z mod 7",
                    "multiplier": 1,
                    "offset": 0,
                },
                "task_role_B": {
                    "formula": "f_B(z)=(2*z+1) mod 7",
                    "multiplier": 2,
                    "offset": 1,
                },
                "bijective_over_Z7": True,
            },
            {
                "mapping_id": "mapping_1",
                "task_role_A": {
                    "formula": "f_A(z)=(3*z+2) mod 7",
                    "multiplier": 3,
                    "offset": 2,
                },
                "task_role_B": {
                    "formula": "f_B(z)=(5*z+4) mod 7",
                    "multiplier": 5,
                    "offset": 4,
                },
                "bijective_over_Z7": True,
            },
        ]
        self.expect(
            protocol.get("mappings"),
            expected_mappings,
            "protocol_mappings_mismatch",
            f"{path}.mappings",
        )
        expected_stacks = [
            {
                "stack_id": stack["mapping_stack_id"],
                "task_pair_id": stack["task_pair_id"],
                "mapping_id": stack["mapping_id"],
                "mirror": stack["mirror_role"],
                "source_task": stack["source_task_id"],
                "target_task": stack["target_task_id"],
                "source_rows": 14,
                "target_calibration_rows": 7,
                "target_audit_rows": 14,
                "status": "NOT_RUN",
            }
            for stack in STACKS
        ]
        self.expect(
            protocol.get("mapping_stacks"),
            expected_stacks,
            "protocol_mapping_stacks_mismatch",
            f"{path}.mapping_stacks",
        )
        self.expect(
            protocol.get("row_and_panel_contract"),
            {
                "per_stack": {
                    "source_rows": 14,
                    "target_calibration_rows": 7,
                    "target_audit_rows": 14,
                },
                "latent_class_balance": {
                    "source_rows_per_z": 2,
                    "target_calibration_rows_per_z": 1,
                    "target_audit_rows_per_z": 2,
                },
                "candidate_panel_per_row": list(CANDIDATES),
                "source_calibration_audit_lineage_overlap_allowed": False,
                "target_audit_use_for_calibration_or_selection_allowed": False,
                "audit_seal_required_before_any_future_model_action": True,
            },
            "protocol_panel_contract_mismatch",
            f"{path}.row_and_panel_contract",
        )
        rules = protocol.get("verifier_error_rules")
        if not isinstance(rules, dict):
            self.error(
                "protocol_error_rules_invalid",
                f"{path}.verifier_error_rules",
                "verifier_error_rules must be an object",
            )
        else:
            shared = rules.get("shared")
            if not isinstance(shared, dict):
                self.error(
                    "protocol_shared_rule_invalid",
                    f"{path}.verifier_error_rules.shared",
                    "shared rule must be an object",
                )
            else:
                for key, expected in {
                    "rule_id": SHARED_RULE_ID,
                    "scope": list(TASK_IDS),
                    "latent_transform": "z_wrong=(z+1) mod 7",
                    "application_order": (
                        "transform_latent_z_then_apply_task_role_mapping"
                    ),
                    "accepted_wrong_count_per_seven_candidate_row": 1,
                    "stable_across_all_tasks": True,
                }.items():
                    self.expect(
                        shared.get(key),
                        expected,
                        "protocol_shared_rule_mismatch",
                        f"{path}.verifier_error_rules.shared.{key}",
                    )
            local = rules.get("local")
            expected_local = [
                {
                    "rule_id": LOCAL_RULE_IDS[task_id],
                    "task_id": task_id,
                    "latent_transform": (
                        f"z_wrong=(z+{LOCAL_OFFSETS[task_id]}) mod 7"
                    ),
                    "accepted_wrong_count_per_seven_candidate_row": 1,
                }
                for task_id in TASK_IDS
            ]
            self.expect(
                local,
                expected_local,
                "protocol_local_rules_mismatch",
                f"{path}.verifier_error_rules.local",
            )
            fpr = rules.get("online_FPR")
            expected_fpr = {
                "definition": "accepted_wrong/all_wrong",
                "accepted_wrong": 1,
                "all_wrong": 6,
                "exact_value": "1/6",
                "applies_to": [
                    "shared_leaky",
                    "local_leaky",
                    "shared_frozen_probe_control",
                    "local_frozen_probe_control",
                ],
                "status": (
                    "CPU_STATIC_CONSTRUCTION_CONTRACT_NOT_EMPIRICAL_RESULT"
                ),
            }
            self.expect(
                fpr,
                expected_fpr,
                "protocol_fpr_mismatch",
                f"{path}.verifier_error_rules.online_FPR",
            )
        expected_protocol_arms = [
            {
                "arm_id": "clean",
                "role": "capability_and_optimizer_drift_guardrail",
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "future_update_semantics": None,
                "blocking_decision": "CLEAN_ARM_UPDATE_SEMANTICS",
            },
            {
                "arm_id": "shared_leaky",
                "role": (
                    "future_shared_verifier_error_update_if_separately_authorized"
                ),
                "verifier_rule_id": SHARED_RULE_ID,
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "future_update_recipe": None,
                "blocking_decision": "ONE_STEP_UPDATE_RECIPE",
            },
            {
                "arm_id": "local_leaky",
                "role": (
                    "future_task_local_verifier_error_update_if_separately_authorized"
                ),
                "verifier_rule_selection": "task_specific_local_rule",
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "future_update_recipe": None,
                "blocking_decision": "ONE_STEP_UPDATE_RECIPE",
            },
            {
                "arm_id": "shared_frozen_probe_control",
                "role": "frozen_measurement_control",
                "verifier_rule_id": SHARED_RULE_ID,
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "selection_exposure_adjustment": False,
            },
            {
                "arm_id": "local_frozen_probe_control",
                "role": "frozen_measurement_control",
                "verifier_rule_selection": "task_specific_local_rule",
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "selection_exposure_adjustment": False,
            },
        ]
        self.expect(
            protocol.get("arms"),
            expected_protocol_arms,
            "protocol_arms_mismatch",
            f"{path}.arms",
        )

    def _expected_split_contract(self, split_role: str) -> dict[str, Any]:
        if split_role == "SOURCE":
            return {
                "panel_role": "SOURCE_UPDATE_INPUT",
                "selection_eligible": False,
                "scientific_evaluation_eligible": False,
                "eligibility_blocker": (
                    "PENDING_HUMAN_REVIEW_AND_MODEL_DEPENDENT_G1_G2"
                ),
            }
        if split_role == "TARGET_CALIBRATION":
            return {
                "panel_role": "CALIBRATION_ONLY",
                "selection_eligible": False,
                "scientific_evaluation_eligible": False,
                "eligibility_blocker": (
                    "PENDING_HUMAN_REVIEW_AND_MODEL_DEPENDENT_G1_G2"
                ),
            }
        return {
            "panel_role": "SEALED_AUDIT_INTENDED",
            "seal_status": "BOUND_BY_TARGET_AUDIT_SEAL_RECEIPT",
            "selection_eligible": False,
            "calibration_eligible": False,
            "scientific_evaluation_eligible": False,
            "eligibility_blocker": (
                "PENDING_HUMAN_REVIEW_AND_MODEL_AUTHORIZATION"
            ),
        }

    def _raw_relpaths(
        self,
        split_role: str,
        stack_id: str,
        z: int,
        replicate: int,
        lineage_id: str,
    ) -> tuple[str, str]:
        prefix = {
            "SOURCE": "src",
            "TARGET_CALIBRATION": "cal",
            "TARGET_AUDIT": "audit",
        }[split_role]
        short = f"{prefix}-z{z}-r{replicate}-{lineage_id[:12]}"
        if split_role == "SOURCE":
            directory = f"raw_source_bytes/{stack_id}"
        elif split_role == "TARGET_CALIBRATION":
            directory = f"raw_target_bytes/{stack_id}/calibration"
        else:
            directory = f"raw_target_bytes/{stack_id}/audit"
        return (
            f"{directory}/{short}.prompt.txt",
            f"{directory}/{short}.row.json",
        )

    def _validate_row(
        self,
        row: Any,
        *,
        stack: Mapping[str, Any],
        split_role: str,
        z: int,
        replicate: int,
        path: str,
    ) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            self.error("row_not_object", path, "row must be an object")
            return None

        task_id = (
            stack["source_task_id"]
            if split_role == "SOURCE"
            else stack["target_task_id"]
        )
        slot = (
            stack["source_slot"]
            if split_role == "SOURCE"
            else stack["target_slot"]
        )
        codebook = codebook_record(stack["mapping_id"], slot)
        generation_attempt = row.get("generation_attempt")
        if not is_int(generation_attempt) or generation_attempt < 0:
            self.error(
                "generation_attempt_invalid",
                f"{path}.generation_attempt",
                "generation_attempt must be a nonnegative integer",
            )
            generation_attempt = 0
        seed_root = self.data.get("seed_root")
        if not isinstance(seed_root, str):
            seed_root = ""
        expected_namespace = (
            f"{seed_root}/{stack['mapping_stack_id']}/{split_role}/"
            f"{task_id}/z{z}/rep{replicate}/attempt{generation_attempt}"
        )
        self.expect(
            row.get("seed_namespace"),
            expected_namespace,
            "seed_namespace_mismatch",
            f"{path}.seed_namespace",
        )
        seed_namespace = row.get("seed_namespace")
        if not isinstance(seed_namespace, str):
            seed_namespace = expected_namespace
        seed_sha256 = sha256_bytes(seed_namespace.encode("ascii", "replace"))
        lineage_id = sha256_bytes(
            ("P4_R1_LINEAGE_V1|" + seed_namespace).encode(
                "ascii", "replace"
            )
        )
        self.expect(
            row.get("seed_namespace_sha256"),
            seed_sha256,
            "seed_hash_mismatch",
            f"{path}.seed_namespace_sha256",
        )
        self.expect(
            row.get("lineage_id"),
            lineage_id,
            "lineage_hash_mismatch",
            f"{path}.lineage_id",
        )
        prefix = {
            "SOURCE": "src",
            "TARGET_CALIBRATION": "cal",
            "TARGET_AUDIT": "audit",
        }[split_role]
        row_id = (
            f"{prefix}-{stack['mapping_stack_id']}-z{z}-r{replicate}-"
            f"{lineage_id[:12]}"
        )
        self.expect(
            row.get("row_id"),
            row_id,
            "row_id_mismatch",
            f"{path}.row_id",
        )

        task_input = row.get("task_input")
        trace: dict[str, Any] | None = None
        try:
            computed_z, trace = evaluate_instance(task_id, task_input)
        except (OracleError, TypeError, KeyError, ValueError) as exc:
            self.metrics["prompt_visible_parse_errors"] += 1
            self.error(
                "oracle_input_invalid",
                f"{path}.task_input",
                str(exc),
            )
            computed_z = None
        self.expect(
            computed_z,
            z,
            "oracle_z_mismatch",
            f"{path}.canonical_z",
            "independent task oracle does not produce the expected class",
        )
        if computed_z is not None:
            try:
                generated = generate_instance(task_id, z, seed_namespace)
            except (OracleError, TypeError, ValueError) as exc:
                self.error(
                    "deterministic_generation_error",
                    f"{path}.task_input",
                    str(exc),
                )
            else:
                self.expect(
                    task_input,
                    generated,
                    "deterministic_instance_mismatch",
                    f"{path}.task_input",
                    "input is not the frozen seed-derived instance",
                )

        try:
            task_input_bytes = canonical_json_bytes(task_input)
        except (TypeError, ValueError, UnicodeError) as exc:
            self.error(
                "task_input_not_canonicalizable",
                f"{path}.task_input",
                str(exc),
            )
            return None
        task_input_sha256 = sha256_bytes(task_input_bytes)
        self.expect(
            row.get("task_input_sha256"),
            task_input_sha256,
            "task_input_hash_mismatch",
            f"{path}.task_input_sha256",
        )
        labels = label_triplet(task_id, z, codebook)
        if len(
            {
                labels["gold_candidate"],
                labels["shared_bug_candidate"],
                labels["local_bug_candidate"],
            }
        ) != 3:
            self.error(
                "label_collision",
                path,
                "gold/shared/local labels must be distinct",
            )
        candidate_records = [
            {
                "candidate_index": index,
                "candidate": candidate,
                "is_gold": candidate == labels["gold_candidate"],
                "is_shared_wrong": (
                    candidate == labels["shared_bug_candidate"]
                ),
                "is_task_local_wrong": (
                    candidate == labels["local_bug_candidate"]
                ),
            }
            for index, candidate in enumerate(CANDIDATES)
        ]
        try:
            prompt_bytes = render_prompt(task_id, task_input, codebook)
        except (OracleError, TypeError, KeyError, UnicodeError) as exc:
            self.error(
                "prompt_reconstruction_error",
                f"{path}.prompt_text",
                str(exc),
            )
            return None
        prompt_text = prompt_bytes.decode("ascii")
        prompt_sha256 = sha256_bytes(prompt_bytes)
        published_prompt = row.get("prompt_text")
        try:
            visible_z = solve_prompt_text(published_prompt)
        except (OracleError, TypeError, KeyError, ValueError) as exc:
            self.error(
                "prompt_visible_parse_error",
                f"{path}.prompt_text",
                str(exc),
            )
        else:
            if visible_z != z:
                self.metrics["prompt_visible_semantics_mismatches"] += 1
            self.expect(
                visible_z,
                z,
                "prompt_visible_semantics_mismatch",
                f"{path}.prompt_text",
                "model-visible prompt answer differs from canonical_z",
            )
        prompt_relpath, row_relpath = self._raw_relpaths(
            split_role,
            stack["mapping_stack_id"],
            z,
            replicate,
            lineage_id,
        )
        semantic_row = {
            "schema_version": ASSET_SCHEMA_VERSION,
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "mapping_stack_id": stack["mapping_stack_id"],
            "task_pair_id": stack["task_pair_id"],
            "mapping_id": stack["mapping_id"],
            "mirror_role": stack["mirror_role"],
            "split_role": split_role,
            "row_id": row_id,
            "task_id": task_id,
            "task_slot": slot,
            "canonical_z": z,
            "replicate_index_within_class": replicate,
            "seed_namespace": seed_namespace,
            "seed_namespace_sha256": seed_sha256,
            "generation_attempt": generation_attempt,
            "lineage_id": lineage_id,
            "task_input": task_input,
            "task_input_sha256": task_input_sha256,
            "codebook": codebook,
            "candidate_order": list(CANDIDATES),
            "candidate_records": candidate_records,
            **labels,
            "prompt_text": prompt_text,
            **self._expected_split_contract(split_role),
            **review_fields(),
            "model_dependent_status": dict(MODEL_STATUS),
        }
        row_bytes = canonical_json_bytes(semantic_row)
        row_sha256 = sha256_bytes(row_bytes)
        prompt_descriptor = {
            "encoding": "ascii",
            "newline": "LF",
            "byte_length": len(prompt_bytes),
            "sha256": prompt_sha256,
            "raw_relpath": prompt_relpath,
        }
        row_descriptor = {
            "encoding": "canonical-json-ascii",
            "newline": "LF",
            "byte_length": len(row_bytes),
            "sha256": row_sha256,
            "raw_relpath": row_relpath,
        }
        expected_row = {
            **semantic_row,
            "prompt_bytes": prompt_descriptor,
            "row_bytes": row_descriptor,
        }
        self.expect(
            row,
            expected_row,
            "row_schema_or_value_mismatch",
            path,
            "published row differs from independent native reconstruction",
        )
        self._validate_raw_descriptor(
            row.get("prompt_bytes"),
            prompt_descriptor,
            prompt_bytes,
            f"{path}.prompt_bytes",
            "raw_prompt",
        )
        self._validate_raw_descriptor(
            row.get("row_bytes"),
            row_descriptor,
            row_bytes,
            f"{path}.row_bytes",
            "raw_row",
        )
        if row_id in self.rows_by_id:
            self.error(
                "row_id_reuse",
                f"{path}.row_id",
                f"row_id is reused: {row_id}",
            )
        else:
            self.rows_by_id[row_id] = expected_row
            if trace is not None:
                self.traces_by_id[row_id] = trace
        return expected_row

    def _validate_rows_and_bundles(
        self,
        source_bundles: Any,
        calibration_rows: Any,
        audit_rows: Any,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, list[dict[str, Any]]],
    ]:
        if not isinstance(source_bundles, list):
            self.error(
                "source_bundles_not_list",
                FILES["source"],
                "source bundle JSONL must parse as a list",
            )
            source_bundles = []
        if not isinstance(calibration_rows, list):
            self.error(
                "calibration_rows_not_list",
                FILES["calibration"],
                "calibration JSONL must parse as a list",
            )
            calibration_rows = []
        if not isinstance(audit_rows, list):
            self.error(
                "audit_rows_not_list",
                FILES["audit"],
                "audit JSONL must parse as a list",
            )
            audit_rows = []
        self.expect(
            len(source_bundles),
            8,
            "source_bundle_count_mismatch",
            FILES["source"],
        )
        self.expect(
            len(calibration_rows),
            56,
            "calibration_row_count_mismatch",
            FILES["calibration"],
        )
        self.expect(
            len(audit_rows),
            112,
            "audit_row_count_mismatch",
            FILES["audit"],
        )

        source_flat: list[dict[str, Any]] = []
        valid_calibration: list[dict[str, Any]] = []
        valid_audit: list[dict[str, Any]] = []
        rows_by_stack: dict[str, list[dict[str, Any]]] = {}
        calibration_index = 0
        audit_index = 0
        for stack_index, stack in enumerate(STACKS):
            stack_id = stack["mapping_stack_id"]
            stack_source: list[dict[str, Any]] = []
            stack_calibration: list[dict[str, Any]] = []
            stack_audit: list[dict[str, Any]] = []
            bundle = (
                source_bundles[stack_index]
                if stack_index < len(source_bundles)
                else None
            )
            if not isinstance(bundle, dict):
                self.error(
                    "source_bundle_not_object",
                    f"{FILES['source']}:{stack_index + 1}",
                    "source bundle must be an object",
                )
                bundle_rows: list[Any] = []
            else:
                bundle_rows = bundle.get("rows")
                if not isinstance(bundle_rows, list):
                    self.error(
                        "source_bundle_rows_invalid",
                        f"{FILES['source']}:{stack_index + 1}.rows",
                        "rows must be a list",
                    )
                    bundle_rows = []
            self.expect(
                len(bundle_rows),
                14,
                "source_row_count_mismatch",
                f"{FILES['source']}:{stack_index + 1}.rows",
            )
            source_position = 0
            for z in range(MODULUS):
                for replicate in range(2):
                    row = (
                        bundle_rows[source_position]
                        if source_position < len(bundle_rows)
                        else None
                    )
                    validated = self._validate_row(
                        row,
                        stack=stack,
                        split_role="SOURCE",
                        z=z,
                        replicate=replicate,
                        path=(
                            f"{FILES['source']}:{stack_index + 1}."
                            f"rows[{source_position}]"
                        ),
                    )
                    if validated is not None:
                        stack_source.append(validated)
                        source_flat.append(validated)
                    source_position += 1

            for z in range(MODULUS):
                row = (
                    calibration_rows[calibration_index]
                    if calibration_index < len(calibration_rows)
                    else None
                )
                validated = self._validate_row(
                    row,
                    stack=stack,
                    split_role="TARGET_CALIBRATION",
                    z=z,
                    replicate=0,
                    path=(
                        f"{FILES['calibration']}:{calibration_index + 1}"
                    ),
                )
                if validated is not None:
                    stack_calibration.append(validated)
                    valid_calibration.append(validated)
                calibration_index += 1

            for z in range(MODULUS):
                for replicate in range(2):
                    row = (
                        audit_rows[audit_index]
                        if audit_index < len(audit_rows)
                        else None
                    )
                    validated = self._validate_row(
                        row,
                        stack=stack,
                        split_role="TARGET_AUDIT",
                        z=z,
                        replicate=replicate,
                        path=f"{FILES['audit']}:{audit_index + 1}",
                    )
                    if validated is not None:
                        stack_audit.append(validated)
                        valid_audit.append(validated)
                    audit_index += 1

            rows_by_stack[stack_id] = [
                *stack_source,
                *stack_calibration,
                *stack_audit,
            ]
            if isinstance(bundle, dict) and len(stack_source) == 14:
                material = {
                    "mapping_stack_id": stack_id,
                    "source_task_id": stack["source_task_id"],
                    "ordered_row_ids": [
                        row["row_id"] for row in stack_source
                    ],
                    "ordered_row_bytes_sha256": [
                        row["row_bytes"]["sha256"] for row in stack_source
                    ],
                    "ordered_prompt_sha256": [
                        row["prompt_bytes"]["sha256"] for row in stack_source
                    ],
                    "candidate_order": list(CANDIDATES),
                }
                bundle_sha = sha256_bytes(canonical_json_bytes(material))
                expected_bundle = {
                    "schema_version": "p4-r1-real-source-bundle-v1",
                    "experiment_status": EXPERIMENT_STATUS,
                    "scientific_evidence": False,
                    "mapping_stack_id": stack_id,
                    "task_pair_id": stack["task_pair_id"],
                    "mapping_id": stack["mapping_id"],
                    "mirror_role": stack["mirror_role"],
                    "source_task_id": stack["source_task_id"],
                    "source_task_slot": stack["source_slot"],
                    "source_codebook": codebook_record(
                        stack["mapping_id"], stack["source_slot"]
                    ),
                    "source_row_count": 14,
                    "class_counts": {str(z): 2 for z in range(7)},
                    "candidate_order": list(CANDIDATES),
                    "source_bundle_sha256": bundle_sha,
                    "arm_source_bundle_binding": {
                        arm_id: bundle_sha for arm_id in ARMS
                    },
                    "arm_source_identity_exact": True,
                    "rows": stack_source,
                    **review_fields(),
                    "model_dependent_status": dict(MODEL_STATUS),
                }
                self.expect(
                    bundle,
                    expected_bundle,
                    "source_bundle_mismatch",
                    f"{FILES['source']}:{stack_index + 1}",
                )

        self.counts["source_rows"] = len(source_flat)
        self.counts["target_calibration_rows"] = len(valid_calibration)
        self.counts["target_audit_rows"] = len(valid_audit)
        self.counts["prompt_rows_total"] = (
            len(source_flat) + len(valid_calibration) + len(valid_audit)
        )
        return (
            source_flat,
            valid_calibration,
            valid_audit,
            rows_by_stack,
        )

    def _validate_mappings(
        self,
        mapping_rows: Any,
        source_bundles: Any,
        rows_by_stack: Mapping[str, list[dict[str, Any]]],
        code_hashes: Mapping[str, Any],
    ) -> None:
        if not isinstance(mapping_rows, list):
            self.error(
                "mapping_rows_not_list",
                FILES["mapping"],
                "mapping JSONL must parse as a list",
            )
            return
        self.expect(
            len(mapping_rows),
            8,
            "mapping_stack_count_mismatch",
            FILES["mapping"],
        )
        bundle_by_stack = {}
        if isinstance(source_bundles, list):
            bundle_by_stack = {
                bundle.get("mapping_stack_id"): bundle
                for bundle in source_bundles
                if isinstance(bundle, dict)
            }
        seed_root = self.data.get("seed_root")
        for index, stack in enumerate(STACKS):
            path = f"{FILES['mapping']}:{index + 1}"
            if index >= len(mapping_rows) or not isinstance(
                mapping_rows[index], dict
            ):
                self.error(
                    "mapping_record_missing",
                    path,
                    "expected mapping-stack record is absent",
                )
                continue
            row = mapping_rows[index]
            stack_rows = rows_by_stack.get(stack["mapping_stack_id"], [])
            source = [
                item for item in stack_rows if item.get("split_role") == "SOURCE"
            ]
            calibration = [
                item
                for item in stack_rows
                if item.get("split_role") == "TARGET_CALIBRATION"
            ]
            audit = [
                item
                for item in stack_rows
                if item.get("split_role") == "TARGET_AUDIT"
            ]
            bundle = bundle_by_stack.get(stack["mapping_stack_id"])
            source_bundle_sha = (
                bundle.get("source_bundle_sha256")
                if isinstance(bundle, dict)
                else None
            )
            expected = {
                "schema_version": "p4-r1-real-mapping-stack-v1",
                "experiment_status": EXPERIMENT_STATUS,
                "scientific_evidence": False,
                "mapping_stack_id": stack["mapping_stack_id"],
                "task_pair_id": stack["task_pair_id"],
                "mapping_id": stack["mapping_id"],
                "mirror_role": stack["mirror_role"],
                "mirror_counterpart_stack_id": (
                    stack["mirror_counterpart_stack_id"]
                ),
                "task_a": stack["task_a"],
                "task_b": stack["task_b"],
                "source_task_id": stack["source_task_id"],
                "source_task_slot": stack["source_slot"],
                "target_task_id": stack["target_task_id"],
                "target_task_slot": stack["target_slot"],
                "source_codebook": codebook_record(
                    stack["mapping_id"], stack["source_slot"]
                ),
                "target_codebook": codebook_record(
                    stack["mapping_id"], stack["target_slot"]
                ),
                "candidate_order": list(CANDIDATES),
                "shared_error_rule": {
                    "rule_id": SHARED_RULE_ID,
                    "space": "canonical_latent_z",
                    "formula": "(z+1) mod 7",
                    "offset_mod7": 1,
                    "cross_task_constant": True,
                },
                "local_error_rules": local_error_rules(),
                "row_counts": {
                    "source": 14,
                    "target_calibration": 7,
                    "target_audit": 14,
                },
                "class_counts": {
                    "source": {str(z): 2 for z in range(7)},
                    "target_calibration": {str(z): 1 for z in range(7)},
                    "target_audit": {str(z): 2 for z in range(7)},
                },
                "source_bundle_sha256": source_bundle_sha,
                "target_calibration_panel_sha256": (
                    panel_digest(calibration)
                    if len(calibration) == 7
                    else row.get("target_calibration_panel_sha256")
                ),
                "target_audit_panel_sha256": (
                    panel_digest(audit)
                    if len(audit) == 14
                    else row.get("target_audit_panel_sha256")
                ),
                "target_audit_seal_status": (
                    "BOUND_BY_TARGET_AUDIT_SEAL_RECEIPT"
                ),
                "target_audit_selection_eligible": False,
                "seed_namespace_roots": {
                    split: (
                        f"{seed_root}/{stack['mapping_stack_id']}/{split}/"
                    )
                    for split in (
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
                "task_contract_sha256": TASK_CONTRACT_SHA256,
                "verifier_implementation_sha256": code_hashes.get(
                    "task_and_verifier_implementation_sha256"
                ),
                "builder_implementation_sha256": code_hashes.get(
                    "builder_implementation_sha256"
                ),
                **review_fields(),
                "model_dependent_status": dict(MODEL_STATUS),
            }
            self.expect(
                row,
                expected,
                "mapping_stack_mismatch",
                path,
                "mapping-stack record differs from independent reconstruction",
            )

    def _validate_verifiers(
        self,
        verifier_rows: Any,
        rows_by_stack: Mapping[str, list[dict[str, Any]]],
        code_hashes: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(verifier_rows, list):
            self.error(
                "verifier_rows_not_list",
                FILES["verifier"],
                "verifier JSONL must parse as a list",
            )
            return []
        self.expect(
            len(verifier_rows),
            280,
            "verifier_row_count_mismatch",
            FILES["verifier"],
        )
        expected_input_rows = [
            row
            for stack in STACKS
            for row in rows_by_stack.get(stack["mapping_stack_id"], [])
        ]
        valid: list[dict[str, Any]] = []
        for index, source_row in enumerate(expected_input_rows):
            path = f"{FILES['verifier']}:{index + 1}"
            if index >= len(verifier_rows) or not isinstance(
                verifier_rows[index], dict
            ):
                self.error(
                    "verifier_record_missing",
                    path,
                    "expected verifier row is absent",
                )
                continue
            record = verifier_rows[index]
            stack = STACK_BY_ID[source_row["mapping_stack_id"]]
            slot = (
                stack["source_slot"]
                if source_row["split_role"] == "SOURCE"
                else stack["target_slot"]
            )
            codebook = codebook_record(stack["mapping_id"], slot)
            arms = verifier_arms(
                source_row["task_id"],
                source_row["canonical_z"],
                codebook,
            )
            arm_by_id = {arm["arm_id"]: arm for arm in arms}
            candidate_records = [
                {
                    "candidate_index": candidate_index,
                    "candidate": candidate,
                    "candidate_text": candidate,
                    "candidate_sha256": sha256_bytes(
                        candidate.encode("ascii")
                    ),
                    "reward_by_arm": {
                        arm_id: arm_by_id[arm_id]["reward_vector"][
                            candidate_index
                        ]
                        for arm_id in ARMS
                    },
                }
                for candidate_index, candidate in enumerate(CANDIDATES)
            ]
            expected = {
                "schema_version": "p4-r1-verifier-reward-manifest-v1",
                "experiment_status": EXPERIMENT_STATUS,
                "scientific_evidence": False,
                "mapping_stack_id": stack["mapping_stack_id"],
                "task_pair_id": stack["task_pair_id"],
                "mapping_id": stack["mapping_id"],
                "mirror_role": stack["mirror_role"],
                "row_id": source_row["row_id"],
                "split_role": source_row["split_role"],
                "row_bytes_sha256": source_row["row_bytes"]["sha256"],
                "prompt_sha256": source_row["prompt_bytes"]["sha256"],
                "task_id": source_row["task_id"],
                "canonical_z": source_row["canonical_z"],
                "candidate_order": list(CANDIDATES),
                "gold_candidate": source_row["gold_candidate"],
                "shared_bug_candidate": source_row[
                    "shared_bug_candidate"
                ],
                "local_bug_candidate": source_row["local_bug_candidate"],
                "verifier_id": "CONTROLLED_MOD7_EXACT_VERIFIER_V1",
                "verifier_contract": (
                    "clean accepts gold only; shared accepts gold and latent "
                    "z+1; local accepts gold and the task-fixed latent offset; "
                    "transformations occur before the task-slot codebook"
                ),
                "verifier_implementation_relpath": "src/controlled_tasks.py",
                "verifier_implementation_sha256": code_hashes.get(
                    "task_and_verifier_implementation_sha256"
                ),
                "task_contract_sha256": TASK_CONTRACT_SHA256,
                "arms": arms,
                "candidate_record_count": 7,
                "candidate_records": candidate_records,
                "model_free_enumeration_scope": (
                    "exact reward vectors, online FPR as "
                    "accepted-wrong/all-wrong, accepted-wrong count, positive "
                    "count, and fixed candidate exposure; not base logits, "
                    "reachability, model-conditioned log probability, "
                    "relative advantage, or pre/post outcomes"
                ),
                **review_fields(),
                "model_dependent_status": dict(MODEL_STATUS),
            }
            self.expect(
                record,
                expected,
                "verifier_reward_mismatch",
                path,
                "reward manifest differs from independent exact vectors",
            )
            valid.append(record)
        self.counts["verifier_rows"] = len(valid)
        self.counts["candidate_records"] = sum(
            len(row.get("candidate_records", []))
            for row in valid
            if isinstance(row.get("candidate_records"), list)
        )
        return valid

    def _validate_machine_labels(
        self,
        label_rows: Any,
        source_rows: Sequence[dict[str, Any]],
        filename: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(label_rows, list):
            self.error(
                "machine_labels_not_list",
                filename,
                "machine-label JSONL must parse as a list",
            )
            return []
        self.expect(
            len(label_rows),
            len(source_rows),
            "machine_label_count_mismatch",
            filename,
        )
        valid: list[dict[str, Any]] = []
        for index, source_row in enumerate(source_rows):
            path = f"{filename}:{index + 1}"
            if index >= len(label_rows) or not isinstance(
                label_rows[index], dict
            ):
                self.error(
                    "machine_label_missing",
                    path,
                    "expected machine-label row is absent",
                )
                continue
            label = label_rows[index]
            expected = {
                "schema_version": "p4-r1-machine-label-manifest-v1",
                "experiment_status": EXPERIMENT_STATUS,
                "scientific_evidence": False,
                "mapping_stack_id": source_row["mapping_stack_id"],
                "task_pair_id": source_row["task_pair_id"],
                "mapping_id": source_row["mapping_id"],
                "mirror_role": source_row["mirror_role"],
                "split_role": source_row["split_role"],
                "row_id": source_row["row_id"],
                "task_id": source_row["task_id"],
                "task_slot": source_row["task_slot"],
                "lineage_id": source_row["lineage_id"],
                "task_input_sha256": source_row["task_input_sha256"],
                "prompt_sha256": source_row["prompt_bytes"]["sha256"],
                "row_bytes_sha256": source_row["row_bytes"]["sha256"],
                "canonical_z": source_row["canonical_z"],
                "gold_candidate": source_row["gold_candidate"],
                "shared_bug_z": source_row["shared_bug_z"],
                "shared_bug_candidate": source_row[
                    "shared_bug_candidate"
                ],
                "shared_rule_id": SHARED_RULE_ID,
                "local_bug_z": source_row["local_bug_z"],
                "local_bug_candidate": source_row["local_bug_candidate"],
                "local_rule_id": LOCAL_RULE_IDS[source_row["task_id"]],
                "shared_offset_mod7": 1,
                "local_offset_mod7": LOCAL_OFFSETS[source_row["task_id"]],
                "machine_label_trace": self.traces_by_id.get(
                    source_row["row_id"]
                ),
                **review_fields(),
                "model_dependent_status": dict(MODEL_STATUS),
            }
            self.expect(
                label,
                expected,
                "machine_label_mismatch",
                path,
                "machine label or independent oracle trace differs",
            )
            valid.append(label)
        return valid

    def _validate_global_uniqueness(
        self, rows: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        field_extractors = {
            "unique_seed_namespaces": lambda row: row.get("seed_namespace"),
            "unique_lineage_ids": lambda row: row.get("lineage_id"),
            "unique_task_input_sha256": lambda row: row.get(
                "task_input_sha256"
            ),
            "unique_prompt_sha256": lambda row: (
                row.get("prompt_bytes", {}).get("sha256")
                if isinstance(row.get("prompt_bytes"), dict)
                else None
            ),
            "unique_row_bytes_sha256": lambda row: (
                row.get("row_bytes", {}).get("sha256")
                if isinstance(row.get("row_bytes"), dict)
                else None
            ),
        }
        result: dict[str, int] = {}
        for label, extractor in field_extractors.items():
            values = [extractor(row) for row in rows]
            normalized = [
                value
                if isinstance(value, (str, int, float, bool, type(None)))
                else repr(value)
                for value in values
            ]
            unique = len(set(normalized))
            result[label] = unique
            if len(values) != 280 or unique != 280 or None in values:
                self.error(
                    "global_uniqueness_failure",
                    label,
                    f"expected 280 present unique values, got {unique}",
                )
        result["unique_raw_relpaths"] = len(self.raw_relpaths)
        if len(self.raw_relpaths) != 560:
            self.error(
                "global_raw_path_uniqueness_failure",
                "raw_relpaths",
                f"expected 560 unique paths, got {len(self.raw_relpaths)}",
            )
        return result

    def _validate_raw_directory_inventory(self) -> None:
        referenced = set(self.raw_relpaths)
        actual: set[str] = set()
        for directory in ("raw_source_bytes", "raw_target_bytes"):
            root = self.root / directory
            if not root.is_dir():
                self.error(
                    "missing_raw_directory",
                    directory,
                    "required raw-byte directory is missing",
                )
                continue
            try:
                for path in root.rglob("*"):
                    if path.is_file():
                        actual.add(path.relative_to(self.root).as_posix())
            except OSError as exc:
                self.error("raw_inventory_error", directory, str(exc))
        missing = sorted(referenced - actual)
        extra = sorted(actual - referenced)
        if missing:
            self.error(
                "raw_inventory_missing_files",
                "raw_byte_directories",
                f"{len(missing)} referenced raw files are missing",
            )
        if extra:
            self.error(
                "raw_inventory_extra_files",
                "raw_byte_directories",
                f"{len(extra)} unreferenced raw files are present",
            )
        source_count = sum(
            path.startswith("raw_source_bytes/") for path in actual
        )
        target_count = sum(
            path.startswith("raw_target_bytes/") for path in actual
        )
        self.counts["raw_source_files"] = source_count
        self.counts["raw_target_files"] = target_count
        self.expect(
            source_count,
            224,
            "raw_source_file_count_mismatch",
            "raw_source_bytes",
        )
        self.expect(
            target_count,
            336,
            "raw_target_file_count_mismatch",
            "raw_target_bytes",
        )

    def _validate_g1(
        self,
        g1: Any,
        source_rows: Sequence[dict[str, Any]],
        verifier_rows: Sequence[dict[str, Any]],
    ) -> None:
        path = FILES["g1"]
        if not isinstance(g1, dict):
            self.error("g1_not_object", path, "G1 audit must be an object")
            return
        source_ids = {row.get("row_id") for row in source_rows}
        source_verifiers = [
            row
            for row in verifier_rows
            if row.get("row_id") in source_ids
            and row.get("split_role") == "SOURCE"
        ]
        shared_counts = Counter(
            row.get("shared_bug_candidate") for row in source_verifiers
        )
        local_counts = Counter(
            row.get("local_bug_candidate") for row in source_verifiers
        )
        accepted_wrong = {"shared_leaky": 0, "local_leaky": 0}
        for row in source_verifiers:
            arms = row.get("arms")
            if not isinstance(arms, list):
                continue
            by_id = {
                arm.get("arm_id"): arm
                for arm in arms
                if isinstance(arm, dict)
            }
            for arm_id in accepted_wrong:
                arm = by_id.get(arm_id)
                if isinstance(arm, dict) and is_int(
                    arm.get("wrong_positive_count")
                ):
                    accepted_wrong[arm_id] += arm["wrong_positive_count"]
        expected = {
            "schema_version": "g1-opportunity-audit-v1",
            "g1_status": "NOT_RUN",
            "cpu_static_contract_status": "PASS_MACHINE_CHECK",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "source_row_count": len(source_rows),
            "source_candidate_record_count": len(source_rows) * 7,
            "candidate_order": list(CANDIDATES),
            "candidate_panel_identical_between_arms": True,
            "source_prompt_identity_between_arms": True,
            "reward_vector_multiset_balance": "PASS_MACHINE_CHECK",
            "wrong_label_multiset_balance": {
                "result": "PASS_MACHINE_CHECK",
                "shared_counts": dict(sorted(shared_counts.items())),
                "local_counts": dict(sorted(local_counts.items())),
            },
            "shared_online_fpr": {
                "definition": "accepted_wrong/all_wrong",
                "numerator": accepted_wrong["shared_leaky"],
                "denominator": len(source_verifiers) * 6,
                "exact_value": "1/6",
            },
            "local_online_fpr": {
                "definition": "accepted_wrong/all_wrong",
                "numerator": accepted_wrong["local_leaky"],
                "denominator": len(source_verifiers) * 6,
                "exact_value": "1/6",
            },
            "static_checks": {
                "seven_fixed_candidates": "PASS_MACHINE_CHECK",
                "one_shared_wrong_positive_per_row": "PASS_MACHINE_CHECK",
                "one_local_wrong_positive_per_row": "PASS_MACHINE_CHECK",
                "two_total_positives_per_leaky_row": "PASS_MACHINE_CHECK",
                "shared_local_candidate_exposure_identity": (
                    "PASS_MACHINE_CHECK"
                ),
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
            **review_fields(),
            "authorization": dict(ASSET_AUTHORIZATION),
            "model_dependent_status": dict(MODEL_STATUS),
            "run_eligibility": False,
        }
        self.expect(
            g1,
            expected,
            "g1_audit_mismatch",
            path,
            "G1 report differs from source-only CPU recomputation",
        )
        self.metrics["shared_online_fpr"] = {
            "numerator": accepted_wrong["shared_leaky"],
            "denominator": len(source_verifiers) * 6,
        }
        self.metrics["local_online_fpr"] = {
            "numerator": accepted_wrong["local_leaky"],
            "denominator": len(source_verifiers) * 6,
        }
        if (
            accepted_wrong["shared_leaky"] * 6
            != len(source_verifiers) * 6
        ):
            self.error(
                "computed_shared_fpr_mismatch",
                path,
                "shared online FPR is not exactly 1/6",
            )
        if (
            accepted_wrong["local_leaky"] * 6
            != len(source_verifiers) * 6
        ):
            self.error(
                "computed_local_fpr_mismatch",
                path,
                "local online FPR is not exactly 1/6",
            )
        expected_balance = {candidate: 16 for candidate in CANDIDATES}
        if dict(shared_counts) != expected_balance:
            self.error(
                "shared_wrong_label_balance_mismatch",
                path,
                "shared wrong-label multiset must contain each label 16 times",
            )
        if dict(local_counts) != expected_balance:
            self.error(
                "local_wrong_label_balance_mismatch",
                path,
                "local wrong-label multiset must contain each label 16 times",
            )

    def _validate_seal(
        self, receipt: Any, audit_rows: Sequence[dict[str, Any]]
    ) -> None:
        path = FILES["seal"]
        if not isinstance(receipt, dict):
            self.error("seal_not_object", path, "seal receipt must be an object")
            return
        audit_bytes = self.file_bytes.get(FILES["audit"], b"")
        lines = audit_bytes.splitlines(keepends=True)
        if len(lines) != 112 or any(
            not line.endswith(b"\n") for line in lines
        ):
            self.error(
                "audit_exact_line_contract_failure",
                FILES["audit"],
                "audit must have exactly 112 LF-terminated records",
            )
        line_hashes = [sha256_bytes(line) for line in lines]
        semantic_row_hashes = [
            row.get("row_bytes", {}).get("sha256")
            if isinstance(row.get("row_bytes"), dict)
            else None
            for row in audit_rows
        ]
        genesis = "0" * 64
        chain: list[str] = []
        previous = genesis
        for line_hash in line_hashes:
            previous = sha256_bytes(
                (previous + line_hash).encode("ascii")
            )
            chain.append(previous)
        expected = {
            "schema_version": "target-audit-seal-receipt-v1",
            "seal_status": "SEALED_PRE_MODEL_ACTION",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "audit_filename": FILES["audit"],
            "audit_file_sha256": sha256_bytes(audit_bytes),
            "audit_file_byte_length": len(audit_bytes),
            "audit_record_count": len(audit_rows),
            "line_hash_algorithm": "sha256",
            "line_hash_formula": (
                "sha256(exact_jsonl_line_bytes_including_LF)"
            ),
            "line_hashes": line_hashes,
            "semantic_row_hash_algorithm": "sha256",
            "semantic_row_hash_formula": (
                "sha256(canonical_semantic_row_bytes_before_publication_metadata)"
            ),
            "semantic_row_hashes": semantic_row_hashes,
            "hash_chain_genesis": genesis,
            "hash_chain_formula": (
                "sha256(previous_chain_sha256_ascii || line_sha256_ascii)"
            ),
            "hash_chain": chain,
            "hash_chain_head": chain[-1] if chain else genesis,
            "sealed_before_model_action": True,
            "audit_used_for_design": False,
            "audit_used_for_matching": False,
            "audit_used_for_tuning": False,
            "audit_used_for_debug": False,
            "audit_used_for_integrity_validation": True,
            "change_policy": (
                "Any human-review disagreement requires a new version and a "
                "new seal; this sealed byte sequence must never be edited in "
                "place."
            ),
            **review_fields(),
            "authorization": dict(ASSET_AUTHORIZATION),
            "model_dependent_status": dict(MODEL_STATUS),
            "run_eligibility": False,
        }
        self.expect(
            receipt,
            expected,
            "audit_seal_mismatch",
            path,
            "seal does not bind the exact ordered audit JSONL bytes",
        )
        if receipt.get("audit_file_sha256") != sha256_bytes(audit_bytes):
            self.error(
                "audit_file_hash_mismatch",
                f"{path}.audit_file_sha256",
                "seal hash differs from exact audit bytes",
            )
        if receipt.get("line_hashes") != line_hashes:
            self.error(
                "audit_line_hashes_mismatch",
                f"{path}.line_hashes",
                "seal line hashes differ from exact LF-terminated lines",
            )
        if receipt.get("hash_chain") != chain:
            self.error(
                "audit_hash_chain_mismatch",
                f"{path}.hash_chain",
                "seal chain differs from independent recomputation",
            )

    def _validate_prereg(
        self,
        prereg: Any,
        build: Mapping[str, Any],
        protocol_sha256: str,
    ) -> None:
        path = FILES["prereg"]
        if not isinstance(prereg, dict):
            self.error(
                "prereg_not_object", path, "preregistration must be an object"
            )
            return
        code_hashes = build.get("code_hashes")
        if not isinstance(code_hashes, dict):
            code_hashes = {}
        expected = {
            "schema_version": "randomization-model-env-prereg-v1",
            "preregistration_status": "PRE_REVIEW",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "run_eligible": False,
            "seed_root_for_cpu_asset_generation": build.get("seed_root"),
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
                FILES["protocol"]: protocol_sha256,
                "task_contract_sha256": TASK_CONTRACT_SHA256,
                "src/controlled_tasks.py": code_hashes.get(
                    "task_and_verifier_implementation_sha256"
                ),
                "scripts/build_controlled_assets.py": code_hashes.get(
                    "builder_implementation_sha256"
                ),
            },
            "blocking_fields": prereg_blockers(),
            **review_fields(),
            "authorization": dict(ASSET_AUTHORIZATION),
            "model_dependent_status": dict(MODEL_STATUS),
        }
        self.expect(
            prereg,
            expected,
            "preregistration_mismatch",
            path,
            "preregistration blockers or bindings differ",
        )
        for dotted in prereg_blockers()[:-2]:
            cursor: Any = prereg
            for component in dotted.split("."):
                cursor = (
                    cursor.get(component)
                    if isinstance(cursor, dict)
                    else "MISSING"
                )
            if cursor is not None:
                self.error(
                    "prereg_blocker_not_null",
                    f"{path}.{dotted}",
                    "listed unresolved blocker must remain null",
                )

    def _validate_label_index(
        self,
        index: Any,
        build: Mapping[str, Any],
        label_counts: Mapping[str, int],
    ) -> None:
        path = FILES["label_index"]
        if not isinstance(index, dict):
            self.error(
                "label_index_not_object",
                path,
                "machine-label index must be an object",
            )
            return
        manifests = {}
        for filename in MACHINE_LABEL_FILENAMES:
            raw = self.file_bytes.get(filename, b"")
            manifests[filename] = {
                "row_count": label_counts.get(filename, 0),
                "sha256": sha256_bytes(raw),
            }
        code_hashes = build.get("code_hashes")
        if not isinstance(code_hashes, dict):
            code_hashes = {}
        expected = {
            "schema_version": "p4-r1-machine-label-manifest-index-v1",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            **review_fields(),
            "manifests": manifests,
            "task_contract": TASK_CONTRACT,
            "task_contract_sha256": TASK_CONTRACT_SHA256,
            "machine_label_implementation_relpath": "src/controlled_tasks.py",
            "machine_label_implementation_sha256": code_hashes.get(
                "task_and_verifier_implementation_sha256"
            ),
            "model_dependent_status": dict(MODEL_STATUS),
        }
        self.expect(
            index,
            expected,
            "machine_label_index_mismatch",
            path,
            "machine-label index, task contract, or file hashes differ",
        )

    def _current_implementation_hash(
        self, relpath: str, label: str
    ) -> str | None:
        real_assets = Path(__file__).resolve().parents[1]
        target = real_assets.joinpath(*relpath.split("/"))
        try:
            return sha256_file(target)
        except OSError as exc:
            self.error(
                "implementation_file_read_error",
                label,
                f"{target}: {exc}",
            )
            return None

    def _validate_build_manifest(
        self,
        build: Any,
        uniqueness: Mapping[str, int],
    ) -> None:
        path = FILES["build"]
        if not isinstance(build, dict):
            self.error(
                "build_manifest_not_object",
                path,
                "build manifest must be an object",
            )
            return
        artifact_hashes: dict[str, Any] = {}
        for filename in ARTIFACT_FILENAMES:
            raw = self.file_bytes.get(filename)
            if raw is None:
                try:
                    raw = (self.root / filename).read_bytes()
                except OSError:
                    raw = b""
            artifact_hashes[filename] = {
                "sha256": sha256_bytes(raw),
                "byte_length": len(raw),
            }
        self.expect(
            build.get("artifact_hashes"),
            artifact_hashes,
            "artifact_hash_manifest_mismatch",
            f"{path}.artifact_hashes",
            "direct artifact SHA-256 or byte length differs",
        )
        code_hashes = build.get("code_hashes")
        if not isinstance(code_hashes, dict):
            self.error(
                "code_hashes_invalid",
                f"{path}.code_hashes",
                "code_hashes must be an object",
            )
            code_hashes = {}
        expected_code_hashes = {
            "builder_implementation_relpath": (
                "scripts/build_controlled_assets.py"
            ),
            "builder_implementation_sha256": (
                self._current_implementation_hash(
                    "scripts/build_controlled_assets.py",
                    "builder_implementation",
                )
            ),
            "task_and_verifier_implementation_relpath": (
                "src/controlled_tasks.py"
            ),
            "task_and_verifier_implementation_sha256": (
                self._current_implementation_hash(
                    "src/controlled_tasks.py",
                    "task_and_verifier_implementation",
                )
            ),
            "task_contract_sha256": TASK_CONTRACT_SHA256,
        }
        self.expect(
            code_hashes,
            expected_code_hashes,
            "implementation_code_hash_mismatch",
            f"{path}.code_hashes",
            "manifest code provenance differs from live implementation bytes",
        )
        if code_hashes.get("builder_implementation_sha256") != (
            expected_code_hashes["builder_implementation_sha256"]
        ):
            self.error(
                "builder_code_hash_mismatch",
                f"{path}.code_hashes.builder_implementation_sha256",
                "builder bytes drifted after the asset build",
            )
        if code_hashes.get(
            "task_and_verifier_implementation_sha256"
        ) != expected_code_hashes[
            "task_and_verifier_implementation_sha256"
        ]:
            self.error(
                "task_verifier_code_hash_mismatch",
                (
                    f"{path}.code_hashes."
                    "task_and_verifier_implementation_sha256"
                ),
                "task/verifier bytes drifted after the asset build",
            )
        expected = {
            "schema_version": "p4-r1-controlled-asset-build-manifest-v1",
            "generator_id": "P4_R1_CONTROLLED_ASSET_BUILDER_V1",
            "experiment_status": EXPERIMENT_STATUS,
            "scientific_evidence": False,
            "formal_experiment": False,
            "seed_root": build.get("seed_root"),
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
                "source": 112,
                "target_calibration": 56,
                "target_audit": 112,
            },
            "total_candidate_records": 1960,
            "uniqueness_audit": {
                **dict(uniqueness),
                "expected_unique_rows": 280,
                "result": "PASS_MACHINE_CHECK",
            },
            "code_hashes": expected_code_hashes,
            "artifact_hashes": artifact_hashes,
            "raw_byte_directories": {
                "source": "raw_source_bytes",
                "target": "raw_target_bytes",
                "files_per_row": 2,
            },
            **review_fields(),
            "model_dependent_status": dict(MODEL_STATUS),
            "run_eligibility": False,
            "run_eligibility_blockers": [
                "PENDING_HUMAN_REVIEW",
                "MODEL_TOKENIZER_RANDOMIZATION_ENVIRONMENT_NOT_BOUND",
                "MODEL_DEPENDENT_G1_G2_NOT_RUN",
                (
                    "MODEL_UPDATE_SCORING_EPSILON_AND_G1_THRESHOLDS_NOT_FROZEN"
                ),
                "NO_MODEL_RUN_AUTHORIZATION",
            ],
        }
        self.expect(
            build,
            expected,
            "build_manifest_mismatch",
            path,
            "build manifest differs from fully recomputed build contract",
        )

    def run(self) -> dict[str, Any]:
        if not self.root.exists() or not self.root.is_dir():
            self.error(
                "root_not_directory",
                str(self.root),
                "asset root does not exist or is not a directory",
            )
            return self.report()
        for filename in (*FILES.values(),):
            if not (self.root / filename).is_file():
                self.error(
                    "missing_required_file",
                    filename,
                    "required native artifact is missing",
                )

        build = (
            self._read_json(FILES["build"])
            if (self.root / FILES["build"]).is_file()
            else None
        )
        if isinstance(build, dict):
            seed_root = build.get("seed_root")
            if (
                not isinstance(seed_root, str)
                or not seed_root
                or not seed_root.isascii()
            ):
                self.error(
                    "seed_root_invalid",
                    f"{FILES['build']}.seed_root",
                    "seed root must be nonempty ASCII",
                )
                seed_root = ""
            self.data["seed_root"] = seed_root
        else:
            self.data["seed_root"] = ""

        protocol = (
            self._read_json(FILES["protocol"])
            if (self.root / FILES["protocol"]).is_file()
            else None
        )
        mapping_rows = (
            self._read_jsonl(FILES["mapping"])
            if (self.root / FILES["mapping"]).is_file()
            else None
        )
        source_bundles = (
            self._read_jsonl(FILES["source"])
            if (self.root / FILES["source"]).is_file()
            else None
        )
        calibration_rows = (
            self._read_jsonl(FILES["calibration"])
            if (self.root / FILES["calibration"]).is_file()
            else None
        )
        audit_rows = (
            self._read_jsonl(FILES["audit"])
            if (self.root / FILES["audit"]).is_file()
            else None
        )
        verifier_rows = (
            self._read_jsonl(FILES["verifier"])
            if (self.root / FILES["verifier"]).is_file()
            else None
        )
        source_labels = (
            self._read_jsonl(FILES["source_labels"])
            if (self.root / FILES["source_labels"]).is_file()
            else None
        )
        calibration_labels = (
            self._read_jsonl(FILES["calibration_labels"])
            if (self.root / FILES["calibration_labels"]).is_file()
            else None
        )
        audit_labels = (
            self._read_jsonl(FILES["audit_labels"])
            if (self.root / FILES["audit_labels"]).is_file()
            else None
        )
        g1 = (
            self._read_json(FILES["g1"])
            if (self.root / FILES["g1"]).is_file()
            else None
        )
        seal = (
            self._read_json(FILES["seal"])
            if (self.root / FILES["seal"]).is_file()
            else None
        )
        prereg = (
            self._read_json(FILES["prereg"])
            if (self.root / FILES["prereg"]).is_file()
            else None
        )
        label_index = (
            self._read_json(FILES["label_index"])
            if (self.root / FILES["label_index"]).is_file()
            else None
        )

        if protocol is not None:
            self._validate_protocol(protocol)
        (
            source_flat,
            calibration_valid,
            audit_valid,
            rows_by_stack,
        ) = self._validate_rows_and_bundles(
            source_bundles,
            calibration_rows,
            audit_rows,
        )
        all_rows = [*source_flat, *calibration_valid, *audit_valid]
        uniqueness = self._validate_global_uniqueness(all_rows)
        self._validate_raw_directory_inventory()

        code_hashes = (
            build.get("code_hashes", {})
            if isinstance(build, dict)
            else {}
        )
        if not isinstance(code_hashes, dict):
            code_hashes = {}
        self._validate_mappings(
            mapping_rows,
            source_bundles,
            rows_by_stack,
            code_hashes,
        )
        verifier_valid = self._validate_verifiers(
            verifier_rows, rows_by_stack, code_hashes
        )
        source_label_valid = self._validate_machine_labels(
            source_labels, source_flat, FILES["source_labels"]
        )
        calibration_label_valid = self._validate_machine_labels(
            calibration_labels,
            calibration_valid,
            FILES["calibration_labels"],
        )
        audit_label_valid = self._validate_machine_labels(
            audit_labels, audit_valid, FILES["audit_labels"]
        )
        self.counts["machine_label_rows"] = (
            len(source_label_valid)
            + len(calibration_label_valid)
            + len(audit_label_valid)
        )
        self._validate_g1(g1, source_flat, verifier_valid)
        self._validate_seal(seal, audit_valid)
        if isinstance(build, dict):
            protocol_hash = sha256_bytes(
                self.file_bytes.get(FILES["protocol"], b"")
            )
            self._validate_prereg(prereg, build, protocol_hash)
            self._validate_label_index(
                label_index,
                build,
                {
                    FILES["source_labels"]: len(source_label_valid),
                    FILES["calibration_labels"]: len(
                        calibration_label_valid
                    ),
                    FILES["audit_labels"]: len(audit_label_valid),
                },
            )
        else:
            self.error(
                "cross_artifact_validation_blocked",
                FILES["build"],
                "cannot validate prereg/index without build manifest",
            )
        self._validate_build_manifest(build, uniqueness)
        return self.report()

    def report(self) -> dict[str, Any]:
        critical_asset_sha256 = {
            FILES[key]: sha256_file(self.root / FILES[key])
            for key in ("mapping", "source", "calibration", "seal")
            if (self.root / FILES[key]).is_file()
        }
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "root": ".",
            "portable_root": ".",
            "critical_asset_sha256": critical_asset_sha256,
            "valid": not self.errors,
            "error_count": len(self.errors),
            "errors": self.errors,
            "counts": self.counts,
            "metrics": self.metrics,
            "model_execution_performed": False,
            "scientific_evidence": False,
            "formal_experiment": False,
            "evidence_boundary": dict(EVIDENCE_BOUNDARY),
        }


def validate_native_controlled_assets(root: str | Path) -> dict[str, Any]:
    """Validate a native controlled-asset root without model execution."""

    try:
        return NativeValidator(Path(root)).run()
    except Exception as exc:  # last-resort fail-closed boundary
        try:
            resolved = str(Path(root).resolve())
        except Exception:
            resolved = str(root)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "root": ".",
            "portable_root": ".",
            "critical_asset_sha256": {},
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
            "scientific_evidence": False,
            "formal_experiment": False,
            "evidence_boundary": dict(EVIDENCE_BOUNDARY),
        }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Independent CPU-only validation of native controlled RLVR assets"
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="native build root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate_native_controlled_assets(args.root)
    rendered = json.dumps(
        report, ensure_ascii=False, sort_keys=True, indent=2
    )
    print(rendered)
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"failed to write report: {exc}", file=sys.stderr)
            return 2
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
