"""Deterministic, model-free task and verifier primitives for P4-R1 assets.

This module deliberately imports only the Python standard library.  It creates
machine labels for four controlled tasks whose canonical answer is a latent
integer z in {0, ..., 6}.  Surface answers always use the common candidate
vocabulary FINAL=K0, ..., FINAL=K6.

Machine-generated labels are not human-reviewed evidence.  The build script
marks every generated row PENDING_HUMAN_REVIEW and every model-dependent field
NOT_RUN.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


MODULUS = 7
CANDIDATES = tuple(f"FINAL=K{index}" for index in range(MODULUS))
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

# Local errors are task-fixed.  They do not depend on mapping or direction.
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

TASK_SPECS = {
    TASK_MOD7_SUM: {
        "task_semantics": "sum two integers a,b in 0..20, then reduce modulo 7",
        "canonical_answer": "z = (a+b) mod 7",
        "local_offset": LOCAL_OFFSETS[TASK_MOD7_SUM],
    },
    TASK_DFA7_FINAL: {
        "task_semantics": (
            "execute the frozen x/y transition table from an initial state "
            "over a length-2-to-6 sequence"
        ),
        "canonical_answer": "z = index of the final state Qz",
        "local_offset": LOCAL_OFFSETS[TASK_DFA7_FINAL],
    },
    TASK_MARKED_RANK7: {
        "task_semantics": "rank seven distinct scores and locate the marked item",
        "canonical_answer": "z = zero-based ascending rank of the marked item",
        "local_offset": LOCAL_OFFSETS[TASK_MARKED_RANK7],
    },
    TASK_PAREN_MAX_DEPTH7: {
        "task_semantics": "scan a pure balanced-parentheses string",
        "canonical_answer": "z = maximum parenthesis nesting depth minus 1",
        "local_offset": LOCAL_OFFSETS[TASK_PAREN_MAX_DEPTH7],
    },
}

# Frozen table from 07_GPT_PRO_TASK_PAIR_VERDICT.md.  Tuple order is (x, y).
DFA_TRANSITIONS = {
    0: (2, 4),
    1: (5, 0),
    2: (1, 6),
    3: (6, 2),
    4: (0, 5),
    5: (3, 1),
    6: (4, 3),
}


class ControlledTaskError(ValueError):
    """Raised when a task instance or codebook violates the frozen contract."""


class HashStream:
    """A cross-version deterministic SHA-256 counter-mode pseudo-random stream."""

    def __init__(self, namespace: str) -> None:
        if not namespace or not namespace.isascii():
            raise ControlledTaskError("seed namespace must be non-empty ASCII")
        self._key = hashlib.sha256(namespace.encode("ascii")).digest()
        self._counter = 0

    def _word(self) -> int:
        material = self._key + self._counter.to_bytes(16, "big")
        self._counter += 1
        return int.from_bytes(hashlib.sha256(material).digest(), "big")

    def randbelow(self, bound: int) -> int:
        if bound <= 0:
            raise ControlledTaskError("randbelow bound must be positive")
        span = 1 << 256
        limit = span - (span % bound)
        while True:
            value = self._word()
            if value < limit:
                return value % bound

    def choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise ControlledTaskError("cannot choose from an empty sequence")
        return values[self.randbelow(len(values))]

    def shuffle(self, values: Iterable[Any]) -> list[Any]:
        result = list(values)
        for index in range(len(result) - 1, 0, -1):
            swap_index = self.randbelow(index + 1)
            result[index], result[swap_index] = result[swap_index], result[index]
        return result

    def sample(self, values: Sequence[Any], count: int) -> list[Any]:
        if count < 0 or count > len(values):
            raise ControlledTaskError("sample count is outside the population")
        return self.shuffle(values)[:count]


@dataclass(frozen=True)
class Codebook:
    """A bijection from latent z to the visible candidate index."""

    codebook_id: str
    task_slot: str
    multiplier: int
    intercept: int

    def __post_init__(self) -> None:
        if self.task_slot not in {"A", "B"}:
            raise ControlledTaskError("task_slot must be A or B")
        visible = [self.label_index(z) for z in range(MODULUS)]
        if sorted(visible) != list(range(MODULUS)):
            raise ControlledTaskError("codebook must be a bijection modulo 7")

    def label_index(self, z: int) -> int:
        _validate_z(z)
        return (self.multiplier * z + self.intercept) % MODULUS

    def candidate(self, z: int) -> str:
        return CANDIDATES[self.label_index(z)]

    def as_record(self) -> dict[str, Any]:
        return {
            "codebook_id": self.codebook_id,
            "task_slot": self.task_slot,
            "formula": (
                f"K(({self.multiplier}*z+{self.intercept}) mod {MODULUS})"
            ),
            "multiplier_mod7": self.multiplier,
            "intercept_mod7": self.intercept,
            "latent_to_candidate": [
                self.candidate(z) for z in range(MODULUS)
            ],
        }


def _validate_z(z: int) -> None:
    if type(z) is not int or not 0 <= z < MODULUS:
        raise ControlledTaskError("canonical z must be an integer in 0..6")


def make_codebook(
    mapping_id: str,
    task_slot: str,
    multiplier: int,
    intercept: int,
) -> Codebook:
    return Codebook(
        codebook_id=f"{mapping_id}_{task_slot}",
        task_slot=task_slot,
        multiplier=multiplier,
        intercept=intercept,
    )


def _mod7_sum_instance(z: int, stream: HashStream) -> dict[str, Any]:
    candidates = [
        (a, b)
        for a in range(21)
        for b in range(21)
        if (a + b) % MODULUS == z
    ]
    a, b = stream.choice(candidates)
    return {"a": a, "b": b}


def _dfa7_instance(z: int, stream: HashStream) -> dict[str, Any]:
    candidates: list[tuple[int, str]] = []
    for sequence_length in range(2, 7):
        for symbols in itertools.product("xy", repeat=sequence_length):
            sequence = "".join(symbols)
            for initial_state in range(MODULUS):
                state = initial_state
                for symbol in sequence:
                    state = DFA_TRANSITIONS[state][0 if symbol == "x" else 1]
                if state == z:
                    candidates.append((initial_state, sequence))
    initial_state, sequence = stream.choice(candidates)
    transitions = {
        str(state): {"x": destinations[0], "y": destinations[1]}
        for state, destinations in DFA_TRANSITIONS.items()
    }
    return {
        "initial_state": initial_state,
        "sequence": sequence,
        "transitions": transitions,
    }


def _marked_rank7_instance(z: int, stream: HashStream) -> dict[str, Any]:
    scores = sorted(stream.sample(tuple(range(10, 100)), MODULUS))
    marked_score = scores[z]
    score_order = stream.shuffle(scores)
    items = [
        {
            "value": score,
            "marked": score == marked_score,
        }
        for score in score_order
    ]
    return {"items": items}


def _paren_chain(depth: int, stream: HashStream) -> str:
    """Create one component with exact depth and deterministic shape variation."""

    if depth <= 0:
        raise ControlledTaskError("parenthesis component depth must be positive")
    if depth == 1:
        return "()"
    distinguished_child = _paren_chain(depth - 1, stream)
    leaf_count = stream.randbelow(4)
    children = [distinguished_child] + ["()" for _ in range(leaf_count)]
    return "(" + "".join(stream.shuffle(children)) + ")"


def _paren_max_depth7_instance(z: int, stream: HashStream) -> dict[str, Any]:
    maximum_depth = z + 1
    distinguished = _paren_chain(maximum_depth, stream)
    # For depth 1, varying the number of top-level pairs is the only possible
    # pure-parentheses structural variation.  For deeper classes, both the
    # distinguished tree and the additional component depths vary.
    extra_count = 1 + stream.randbelow(12)
    components = [distinguished]
    for _ in range(extra_count):
        component_depth = 1 + stream.randbelow(maximum_depth)
        components.append(_paren_chain(component_depth, stream))
    return {"text": "".join(stream.shuffle(components))}


def generate_instance(
    task_id: str,
    z: int,
    seed_namespace: str,
) -> dict[str, Any]:
    """Generate one deterministic task instance with an exact canonical class."""

    _validate_z(z)
    stream = HashStream(seed_namespace)
    if task_id == TASK_MOD7_SUM:
        instance = _mod7_sum_instance(z, stream)
    elif task_id == TASK_DFA7_FINAL:
        instance = _dfa7_instance(z, stream)
    elif task_id == TASK_MARKED_RANK7:
        instance = _marked_rank7_instance(z, stream)
    elif task_id == TASK_PAREN_MAX_DEPTH7:
        instance = _paren_max_depth7_instance(z, stream)
    else:
        raise ControlledTaskError(f"unknown task_id: {task_id}")

    computed, _ = evaluate_instance(task_id, instance)
    if computed != z:
        raise ControlledTaskError(
            f"generator/evaluator disagreement for {task_id}: {computed} != {z}"
        )
    return instance


def evaluate_instance(
    task_id: str,
    instance: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Return the canonical z and a deterministic machine-label trace."""

    if task_id == TASK_MOD7_SUM:
        a = instance.get("a")
        b = instance.get("b")
        if (
            type(a) is not int
            or type(b) is not int
            or not 0 <= a <= 20
            or not 0 <= b <= 20
        ):
            raise ControlledTaskError("MOD7_SUM requires a,b in 0..20")
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
        initial_state = instance.get("initial_state")
        sequence = instance.get("sequence")
        transitions = instance.get("transitions")
        if type(initial_state) is not int or not 0 <= initial_state < MODULUS:
            raise ControlledTaskError("DFA initial state is invalid")
        if (
            not isinstance(sequence, str)
            or not 2 <= len(sequence) <= 6
            or any(symbol not in "xy" for symbol in sequence)
        ):
            raise ControlledTaskError(
                "DFA sequence must contain only x/y and have length 2..6"
            )
        if not isinstance(transitions, dict):
            raise ControlledTaskError("DFA transitions must be an object")
        expected_transitions = {
            str(state): {"x": destinations[0], "y": destinations[1]}
            for state, destinations in DFA_TRANSITIONS.items()
        }
        if transitions != expected_transitions:
            raise ControlledTaskError("DFA transition table differs from frozen table")
        state = initial_state
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
        items = instance.get("items")
        if not isinstance(items, list) or len(items) != MODULUS:
            raise ControlledTaskError("MARKED_RANK7 requires seven items")
        values: list[int] = []
        marked_items: list[Mapping[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ControlledTaskError("MARKED_RANK7 item must be an object")
            value = item.get("value")
            marked = item.get("marked")
            if type(value) is not int or type(marked) is not bool:
                raise ControlledTaskError("MARKED_RANK7 item fields are invalid")
            values.append(value)
            if marked:
                marked_items.append(item)
        if len(set(values)) != MODULUS:
            raise ControlledTaskError("MARKED_RANK7 values must be unique")
        if len(marked_items) != 1:
            raise ControlledTaskError("MARKED_RANK7 requires exactly one marked item")
        marked_value = marked_items[0]["value"]
        z = sum(value < marked_value for value in values)
        return z, {
            "operation": "count_values_strictly_less_than_marked",
            "ascending_values": sorted(values),
            "marked_value": marked_value,
            "rank": z,
        }

    if task_id == TASK_PAREN_MAX_DEPTH7:
        text = instance.get("text")
        if (
            not isinstance(text, str)
            or not text
            or any(character not in "()" for character in text)
        ):
            raise ControlledTaskError(
                "PAREN_MAX_DEPTH7 text must be non-empty pure parentheses"
            )
        depth = 0
        maximum = 0
        depth_trace: list[int] = []
        for character in text:
            if character == "(":
                depth += 1
                maximum = max(maximum, depth)
            elif character == ")":
                depth -= 1
                if depth < 0:
                    raise ControlledTaskError("parenthesis text closes below depth zero")
            depth_trace.append(depth)
        if depth != 0:
            raise ControlledTaskError("parenthesis text is not balanced")
        if not 1 <= maximum <= MODULUS:
            raise ControlledTaskError("parenthesis maximum depth must be in 1..7")
        z = maximum - 1
        return z, {
            "operation": "maximum_parenthesis_depth_minus_one",
            "maximum_depth": maximum,
            "canonical_z": z,
            "final_depth": depth,
            "character_count": len(text),
            "depth_trace_sha256": hashlib.sha256(
                ",".join(str(value) for value in depth_trace).encode("ascii")
            ).hexdigest(),
        }

    raise ControlledTaskError(f"unknown task_id: {task_id}")


def _codebook_line(codebook: Codebook) -> str:
    return ";".join(
        f"z{z}->K{codebook.label_index(z)}" for z in range(MODULUS)
    )


def _task_lines(task_id: str, instance: Mapping[str, Any]) -> list[str]:
    if task_id == TASK_MOD7_SUM:
        return [
            "RULE=Compute z=(a+b) modulo 7.",
            f"A={instance['a']}",
            f"B={instance['b']}",
        ]

    if task_id == TASK_DFA7_FINAL:
        lines = [
            "RULE=Start at INITIAL_STATE and follow SEQUENCE from left to right.",
            f"INITIAL_STATE=Q{instance['initial_state']}",
            "SEQUENCE=" + instance["sequence"],
        ]
        rendered = ";".join(
            (
                f"Q{state}:x->Q{instance['transitions'][str(state)]['x']},"
                f"y->Q{instance['transitions'][str(state)]['y']}"
            )
            for state in range(MODULUS)
        )
        lines.append("TRANSITIONS=" + rendered)
        return lines

    if task_id == TASK_MARKED_RANK7:
        rendered_items = ",".join(
            f"{'*' if item['marked'] else ''}{item['value']}"
            for item in instance["items"]
        )
        return [
            "RULE=z is the number of listed values strictly less than the value marked *.",
            f"ITEMS={rendered_items}",
        ]

    if task_id == TASK_PAREN_MAX_DEPTH7:
        return [
            "RULE=The string is balanced; z is maximum parenthesis nesting depth minus 1.",
            "TEXT=" + instance["text"],
        ]

    raise ControlledTaskError(f"unknown task_id: {task_id}")


def render_prompt(
    task_id: str,
    instance: Mapping[str, Any],
    codebook: Codebook,
) -> bytes:
    """Render canonical ASCII/LF prompt bytes for one controlled row."""

    computed_z, _ = evaluate_instance(task_id, instance)
    _validate_z(computed_z)
    lines = [
        f"TASK={task_id}",
        "CANONICAL_CLASS=z in {0,1,2,3,4,5,6}",
        "CODEBOOK=" + _codebook_line(codebook),
        "CANDIDATES=" + "|".join(CANDIDATES),
        *_task_lines(task_id, instance),
        "ANSWER_FORMAT=Return exactly one candidate and no other text.",
    ]
    text = "\n".join(lines) + "\n"
    if not text.isascii():
        raise ControlledTaskError("canonical prompt unexpectedly contains non-ASCII text")
    return text.encode("ascii")


def label_triplet(
    task_id: str,
    z: int,
    codebook: Codebook,
) -> dict[str, Any]:
    """Return gold/shared/local labels after latent-space transformations."""

    _validate_z(z)
    local_offset = LOCAL_OFFSETS[task_id]
    shared_z = (z + SHARED_OFFSET) % MODULUS
    local_z = (z + local_offset) % MODULUS
    labels = {
        "gold_z": z,
        "shared_bug_z": shared_z,
        "local_bug_z": local_z,
        "gold_candidate": codebook.candidate(z),
        "shared_bug_candidate": codebook.candidate(shared_z),
        "local_bug_candidate": codebook.candidate(local_z),
        "shared_offset_mod7": SHARED_OFFSET,
        "shared_rule_id": SHARED_RULE_ID,
        "local_offset_mod7": local_offset,
        "local_rule_id": LOCAL_RULE_IDS[task_id],
    }
    if len(
        {
            labels["gold_candidate"],
            labels["shared_bug_candidate"],
            labels["local_bug_candidate"],
        }
    ) != 3:
        raise ControlledTaskError(
            "gold, shared-wrong, and task-local-wrong labels must be distinct"
        )
    return labels


def reward_vector(accepted_candidates: Iterable[str]) -> list[int]:
    accepted = set(accepted_candidates)
    unknown = accepted.difference(CANDIDATES)
    if unknown:
        raise ControlledTaskError(f"unknown accepted candidates: {sorted(unknown)}")
    return [1 if candidate in accepted else 0 for candidate in CANDIDATES]


def verifier_arm_records(
    task_id: str,
    z: int,
    codebook: Codebook,
) -> list[dict[str, Any]]:
    """Build exact model-free reward vectors aligned to CANDIDATES."""

    labels = label_triplet(task_id, z, codebook)
    gold = labels["gold_candidate"]
    shared = labels["shared_bug_candidate"]
    local = labels["local_bug_candidate"]
    definitions = (
        (
            "clean",
            (gold,),
            "capability_and_optimizer_drift_guardrail",
            "UNRESOLVED_CLEAN_ARM_UPDATE_SEMANTICS",
        ),
        (
            "shared_leaky",
            (gold, shared),
            "future_shared_verifier_error_update_if_separately_authorized",
            "ONE_STEP_UPDATE_RECIPE_NOT_FROZEN",
        ),
        (
            "local_leaky",
            (gold, local),
            "future_task_local_verifier_error_update_if_separately_authorized",
            "ONE_STEP_UPDATE_RECIPE_NOT_FROZEN",
        ),
        (
            "shared_frozen_probe_control",
            (gold, shared),
            "frozen_measurement_control",
            "NO_UPDATE_BY_DEFINITION",
        ),
        (
            "local_frozen_probe_control",
            (gold, local),
            "frozen_measurement_control",
            "NO_UPDATE_BY_DEFINITION",
        ),
    )
    records: list[dict[str, Any]] = []
    for arm_id, accepted, role, future_update_status in definitions:
        vector = reward_vector(accepted)
        wrong_positive_count = sum(vector) - int(vector[CANDIDATES.index(gold)] == 1)
        records.append(
            {
                "arm_id": arm_id,
                "role": role,
                "current_parameter_update": False,
                "parameter_update_authorized": False,
                "future_update_status": future_update_status,
                "selection_exposure_adjustment": False,
                "accepted_candidates": list(accepted),
                "reward_vector_candidate_order": list(CANDIDATES),
                "reward_vector": vector,
                "positive_count": sum(vector),
                "wrong_positive_count": wrong_positive_count,
                "online_fpr_definition": (
                    "accepted wrong candidates / all wrong candidates"
                ),
                "online_fpr_exact": (
                    f"{wrong_positive_count}/{MODULUS - 1}"
                ),
                "accepted_wrong_reward_mass_count": wrong_positive_count,
                "candidate_count": MODULUS,
                "candidate_exposure_contract": "IDENTICAL_FIXED_SEVEN",
            }
        )
    return records


def task_spec_record() -> dict[str, Any]:
    return {
        "modulus": MODULUS,
        "candidate_order": list(CANDIDATES),
        "shared_offset_mod7": SHARED_OFFSET,
        "shared_rule_id": SHARED_RULE_ID,
        "local_offsets_mod7": dict(LOCAL_OFFSETS),
        "local_rule_ids": dict(LOCAL_RULE_IDS),
        "dfa_transitions": {
            str(state): {"x": destinations[0], "y": destinations[1]}
            for state, destinations in DFA_TRANSITIONS.items()
        },
        "tasks": TASK_SPECS,
    }
