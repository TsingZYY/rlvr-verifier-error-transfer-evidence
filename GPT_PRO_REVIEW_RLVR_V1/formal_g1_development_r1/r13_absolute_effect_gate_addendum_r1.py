"""Fail-closed static R13 absolute-effect interpretation addendum.

This module does not authorize or perform model reads, model updates, or a run.
It binds a second interpretation gate to the raw ``E`` estimand already frozen
in the R13 protocol without mutating that protocol.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "r13-absolute-effect-gate-addendum-r1"
STATUS = "STATIC_ADDENDUM_FROZEN_RUN_INELIGIBLE_MODEL_NOT_TOUCHED"
PARENT_PROTOCOL_SCHEMA = "r13-fixed-update-target-alignment-pilot-draft-r1"
PARENT_PROTOCOL_SHA256 = "9e5c5aa889beecb5d66cd5a1920c9aa86ed3040586ec744cbb069216e20c06ab"

STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
SOURCE_IDENTITIES = (1, 2, 3, 4, 5)
TARGET_OFFSETS = (1, 2, 3, 4, 5, 6)
TASK_PAIRS = ("TP1", "TP2")
TECHNICAL_TOLERANCE = 1e-12
DIRECTIONAL_EPSILON = 1e-10

RAW_EFFECT_FORMULA = (
    "E(k,s,h,r,q)=[post log P_h(z+q)-post log P_h(z)]-"
    "[pre log P_h(z+q)-pre log P_h(z)], averaged over the seven matched target rows."
)
ORIGINAL_ANCHOR_FORMULA = (
    "A_original(k,s,r)=E(k,s,H0_ORIGINAL_M0_TARGET,r,q_H0(r))"
)
ALIGNED_ANCHOR_FORMULA = (
    "A_aligned(k,s,r)=E(k,s,H1_SWITCHED_TARGET,r,q_H1(r))"
)
ABSOLUTE_DIFFERENCE_FORMULA = (
    "D_absolute(k,s,r)=A_aligned(k,s,r)-A_original(k,s,r)"
)
STACK_ABSOLUTE_FORMULA = (
    "A_aligned_stack(s)=equal_mean_over_r_1_to_5(equal_mean_over_technical_replicates_k(A_aligned(k,s,r)))"
)
HASH_RE = re.compile(r"[0-9a-f]{64}")


class AbsoluteEffectGateError(ValueError):
    """The addendum, its parent binding, or an input matrix failed closed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AbsoluteEffectGateError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                AbsoluteEffectGateError(f"non-finite JSON in {path}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AbsoluteEffectGateError(f"invalid JSON: {path}") from error
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def expected_counts(technical_replicates: int) -> dict[str, int]:
    require(
        type(technical_replicates) is int and technical_replicates in (1, 2),
        "only the frozen A or A/B technical variants are count-valid",
    )
    raw_e_per_replicate = len(STACKS) * len(ARMS) * len(SOURCE_IDENTITIES) * len(TARGET_OFFSETS)
    anchors_per_arm_per_replicate = len(STACKS) * len(SOURCE_IDENTITIES)
    return {
        "technical_replicates": technical_replicates,
        "raw_E_cells_before_technical_averaging": raw_e_per_replicate * technical_replicates,
        "original_anchor_cells_before_technical_averaging": anchors_per_arm_per_replicate * technical_replicates,
        "aligned_anchor_cells_before_technical_averaging": anchors_per_arm_per_replicate * technical_replicates,
        "design_level_original_anchor_cells_after_technical_averaging": anchors_per_arm_per_replicate,
        "design_level_aligned_anchor_cells_after_technical_averaging": anchors_per_arm_per_replicate,
        "design_level_descriptive_difference_cells": anchors_per_arm_per_replicate,
        "stack_by_identity_rows_per_anchor": anchors_per_arm_per_replicate,
        "stack_means_per_anchor": len(STACKS),
        "task_pair_descriptive_means_per_anchor": len(TASK_PAIRS),
        "overall_descriptive_means_per_anchor": 1,
    }


def build_addendum() -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "parent_protocol": {
            "schema_version": PARENT_PROTOCOL_SCHEMA,
            "sha256": PARENT_PROTOCOL_SHA256,
            "mutation_rule": "PARENT_PROTOCOL_R1_MUST_REMAIN_BYTE_IDENTICAL; THIS_IS_A_SEPARATE_ADDENDUM",
            "inherited_raw_effect_formula": RAW_EFFECT_FORMULA,
        },
        "scope": {
            "purpose": "Prevent a positive relative interface interaction from being mislabeled as absolute wrong-vs-gold amplification.",
            "input": "EXISTING_VALIDATED_R13_RAW_E_ONLY",
            "new_target_model_reads": 0,
            "new_source_model_updates": 0,
            "new_model_actions": 0,
            "changes_parent_estimands": False,
            "changes_parent_execution_counts": False,
        },
        "fixed_design": {
            "ordered_stack_ids": list(STACKS),
            "ordered_arms": list(ARMS),
            "source_identities": list(SOURCE_IDENTITIES),
            "target_offsets": list(TARGET_OFFSETS),
            "task_pairs": list(TASK_PAIRS),
        },
        "absolute_anchor_estimands": {
            "original_target_anchor": ORIGINAL_ANCHOR_FORMULA,
            "switched_aligned_target_anchor": ALIGNED_ANCHOR_FORMULA,
            "descriptive_aligned_minus_original": ABSOLUTE_DIFFERENCE_FORMULA,
            "primary_absolute_stack_statistic": STACK_ABSOLUTE_FORMULA,
            "anchor_semantics": "Each A value is the pre-to-post change in a wrong-vs-gold log-probability contrast at the arm's own surface-aligned target location; it is selected directly from raw E and is never double-centered.",
            "difference_is_not_primary_gate": True,
        },
        "aggregation_order": [
            "recompute and validate raw E within each exact technical replicate k, stack s, arm h, source identity r, and target offset q",
            "derive A_original and A_aligned directly from each replicate's raw E using the frozen q_H0(r) and q_H1(r); do not use R, G, F, C, centering, clipping, or imputation",
            "for A/B, require every raw E(k,s,h,r,q) cell to agree within the technical absolute tolerance before averaging any technical replicate",
            "equal-average technical replicates only within the identical s,h,r,q design cell",
            "report all 20 stack-by-identity A_original values, all 20 A_aligned values, and all 20 descriptive differences",
            "equal-average the five fixed source identities within each stack and report all four stack means before task-pair or overall summaries",
            "report both task-pair means separately before one descriptive overall mean",
            "never treat identities, offsets, arms, panels, technical replicates, directions, or processes as independent task samples",
        ],
        "count_contract": {
            "fixed_cardinalities": {
                "stacks": 4,
                "arms": 2,
                "source_identities_per_stack": 5,
                "target_offsets_per_raw_E_row": 6,
                "task_pair_clusters": 2,
            },
            "MINIMAL_4_PROCESS_A": expected_counts(1),
            "REPRODUCIBILITY_8_PROCESS_AB": expected_counts(2),
            "design_level_counts_must_not_double_under_AB": True,
        },
        "threshold_contract": {
            "technical_absolute_tolerance": TECHNICAL_TOLERANCE,
            "technical_use_only": "raw-E deterministic recomputation and A/B cell-wise reproducibility",
            "directional_dead_zone_epsilon": DIRECTIONAL_EPSILON,
            "directional_positive_rule": "value > epsilon",
            "directional_not_positive_rule": "value <= epsilon",
            "epsilon_is_not_a_practical_margin": True,
            "threshold_adaptation_after_results": "FORBIDDEN_REQUIRES_NEW_ADDENDUM_VERSION",
        },
        "absolute_gate": {
            "technical_gate": "all inherited R13 technical gates and this addendum's schema, parent hash, formulas, counts, raw-E coverage, finiteness, and A/B raw-E tolerance checks pass",
            "directional_gate": "for each of the four fixed stacks, A_aligned_stack(s) > directional_dead_zone_epsilon",
            "identity_reporting_is_mandatory_but_not_an_independent_pass_count": True,
            "original_anchor_has_no_directional_pass_requirement": True,
            "joint_label_requires": [
                "the frozen parent relative interface-switch gate passes",
                "the addendum absolute aligned-target directional gate passes in all four stacks",
            ],
            "pass_label": "R13_RELATIVE_SWITCH_PLUS_ABSOLUTE_ALIGNED_WRONG_VS_GOLD_AMPLIFICATION_DEVELOPMENT_ONLY",
            "automatic_progression": False,
        },
        "interpretation_grid": [
            {
                "relative_interface_stack_mean": "> epsilon",
                "aligned_absolute_stack_mean": "> epsilon",
                "label": "RELATIVE_SWITCH_WITH_ABSOLUTE_ALIGNED_WRONG_VS_GOLD_AMPLIFICATION_DEVELOPMENT_ONLY",
            },
            {
                "relative_interface_stack_mean": "> epsilon",
                "aligned_absolute_stack_mean": "<= 0",
                "label": "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION",
            },
            {
                "relative_interface_stack_mean": "> epsilon",
                "aligned_absolute_stack_mean": "0 < value <= epsilon",
                "label": "RELATIVE_SWITCH_WITH_ABSOLUTE_DEAD_ZONE_NOT_AMPLIFICATION",
            },
            {
                "relative_interface_stack_mean": "<= epsilon",
                "aligned_absolute_stack_mean": "> epsilon",
                "label": "ABSOLUTE_WRONG_VS_GOLD_AMPLIFICATION_WITHOUT_INTERFACE_SWITCH_ATTRIBUTION",
            },
            {
                "relative_interface_stack_mean": "<= epsilon",
                "aligned_absolute_stack_mean": "<= epsilon",
                "label": "NO_DIRECTIONAL_SUPPORT_FOR_INTERFACE_SWITCH_OR_ABSOLUTE_AMPLIFICATION",
            },
        ],
        "claim_boundary": {
            "can_support_if_joint_gate_passes": "Development-only evidence in the four frozen M0 stacks that the fixed source BUG update both repositions the relative target readout under the interface switch and increases the switched arm's surface-aligned wrong-vs-gold log-probability contrast from pre to post.",
            "relative_positive_absolute_nonpositive": "Relative preservation or slower decay at the aligned location only; never call it absolute amplification.",
            "cannot_support": [
                "an increase in absolute wrong-candidate probability rather than a decrease in gold probability",
                "BUG-versus-Gold-only causal decomposition",
                "shared semantic identity",
                "same empirical, natural, or policy-weighted FPR",
                "sampled or multi-step RLVR transfer",
                "capability damage or deployment risk",
                "population task generalization",
                "formal or confirmatory inference",
                "a practical-magnitude claim",
            ],
            "sample_size_boundary": "The two outcome-aware development task-pair clusters remain the scientific scope; no SE, CI, inferential p-value, or population claim is permitted.",
        },
        "authorization_boundary": {
            "run_eligible": False,
            "model_execution_authorized": False,
            "model_execution_performed": False,
            "new_model_reads_allowed": False,
            "new_model_updates_allowed": False,
            "scientific_evidence": False,
            "formal_experiment": False,
        },
    }
    return value


def validate_parent_protocol(protocol_path: Path) -> dict[str, Any]:
    require(sha256_file(protocol_path) == PARENT_PROTOCOL_SHA256, "parent protocol byte hash drift")
    protocol = strict_json(protocol_path)
    require(protocol.get("schema_version") == PARENT_PROTOCOL_SCHEMA, "parent protocol schema drift")
    require(protocol.get("run_eligible") is False, "parent protocol unexpectedly run eligible")
    require(protocol.get("model_execution_authorized") is False, "parent protocol unexpectedly authorizes model execution")
    require(
        protocol.get("potential_outcomes_and_estimands", {}).get("raw_effect") == RAW_EFFECT_FORMULA,
        "parent raw E formula drift",
    )
    require(
        protocol.get("fixed_source_stacks", {}).get("ordered_stack_ids") == list(STACKS),
        "parent stack order drift",
    )
    estimands = protocol.get("potential_outcomes_and_estimands", {})
    require(estimands.get("target_identities") == list(TARGET_OFFSETS), "parent target offset drift")
    numerical = protocol.get("numerical_contract", {})
    require(numerical.get("technical_absolute_tolerance") == TECHNICAL_TOLERANCE, "parent technical tolerance drift")
    require(numerical.get("directional_dead_zone_epsilon") == DIRECTIONAL_EPSILON, "parent directional epsilon drift")
    return protocol


def validate_addendum(value: dict[str, Any], *, protocol_path: Path | None = None) -> None:
    require(isinstance(value, dict), "addendum must be a JSON object")
    require(value == build_addendum(), "absolute-effect addendum schema or frozen semantics drift")
    parent_hash = value["parent_protocol"]["sha256"]
    require(isinstance(parent_hash, str) and HASH_RE.fullmatch(parent_hash) is not None, "invalid parent SHA-256")
    if protocol_path is not None:
        validate_parent_protocol(protocol_path)


def _finite(value: Any, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(float(value)), f"{label} must be finite")
    return float(value)


def validate_raw_e_matrix(raw_e: Mapping[str, Mapping[int, Mapping[int, float]]]) -> None:
    require(set(raw_e) == set(ARMS), "raw E arm coverage drift")
    for arm in ARMS:
        by_identity = raw_e[arm]
        require(set(by_identity) == set(SOURCE_IDENTITIES), f"{arm}: raw E source identity coverage drift")
        for r in SOURCE_IDENTITIES:
            by_offset = by_identity[r]
            require(set(by_offset) == set(TARGET_OFFSETS), f"{arm}/r{r}: raw E target offset coverage drift")
            for q in TARGET_OFFSETS:
                _finite(by_offset[q], f"{arm}/r{r}/q{q}")


def validate_alignments(q_by_arm: Mapping[str, Mapping[int, int]]) -> None:
    require(set(q_by_arm) == set(ARMS), "alignment arm coverage drift")
    for arm in ARMS:
        require(set(q_by_arm[arm]) == set(SOURCE_IDENTITIES), f"{arm}: alignment identity coverage drift")
        for r in SOURCE_IDENTITIES:
            require(type(q_by_arm[arm][r]) is int and q_by_arm[arm][r] in TARGET_OFFSETS, f"{arm}/r{r}: invalid q alignment")


def average_technical_raw_e(
    by_replicate: Mapping[str, Mapping[str, Mapping[int, Mapping[int, float]]]],
) -> dict[str, dict[int, dict[int, float]]]:
    labels = tuple(by_replicate)
    require(labels in (("A",), ("A", "B")), "technical replicate labels/order must be A or A,B")
    for label in labels:
        validate_raw_e_matrix(by_replicate[label])
    if labels == ("A", "B"):
        for arm in ARMS:
            for r in SOURCE_IDENTITIES:
                for q in TARGET_OFFSETS:
                    difference = abs(float(by_replicate["A"][arm][r][q]) - float(by_replicate["B"][arm][r][q]))
                    require(difference <= TECHNICAL_TOLERANCE, f"A/B raw E mismatch at {arm}/r{r}/q{q}")
    divisor = float(len(labels))
    return {
        arm: {
            r: {
                q: sum(float(by_replicate[label][arm][r][q]) for label in labels) / divisor
                for q in TARGET_OFFSETS
            }
            for r in SOURCE_IDENTITIES
        }
        for arm in ARMS
    }


def derive_absolute_anchors(
    raw_e: Mapping[str, Mapping[int, Mapping[int, float]]],
    q_by_arm: Mapping[str, Mapping[int, int]],
) -> dict[str, dict[int, float]]:
    validate_raw_e_matrix(raw_e)
    validate_alignments(q_by_arm)
    original = {
        r: float(raw_e[ARMS[0]][r][q_by_arm[ARMS[0]][r]])
        for r in SOURCE_IDENTITIES
    }
    aligned = {
        r: float(raw_e[ARMS[1]][r][q_by_arm[ARMS[1]][r]])
        for r in SOURCE_IDENTITIES
    }
    return {
        "A_original_by_source_identity": original,
        "A_aligned_by_source_identity": aligned,
        "D_absolute_by_source_identity": {r: aligned[r] - original[r] for r in SOURCE_IDENTITIES},
    }


def classify_stack(relative_interface_mean: float, aligned_absolute_mean: float) -> str:
    relative = _finite(relative_interface_mean, "relative interface mean")
    absolute = _finite(aligned_absolute_mean, "aligned absolute mean")
    if relative > DIRECTIONAL_EPSILON:
        if absolute > DIRECTIONAL_EPSILON:
            return "RELATIVE_SWITCH_WITH_ABSOLUTE_ALIGNED_WRONG_VS_GOLD_AMPLIFICATION_DEVELOPMENT_ONLY"
        if absolute <= 0.0:
            return "RELATIVE_PRESERVATION_ONLY_NOT_ABSOLUTE_AMPLIFICATION"
        return "RELATIVE_SWITCH_WITH_ABSOLUTE_DEAD_ZONE_NOT_AMPLIFICATION"
    if absolute > DIRECTIONAL_EPSILON:
        return "ABSOLUTE_WRONG_VS_GOLD_AMPLIFICATION_WITHOUT_INTERFACE_SWITCH_ATTRIBUTION"
    return "NO_DIRECTIONAL_SUPPORT_FOR_INTERFACE_SWITCH_OR_ABSOLUTE_AMPLIFICATION"


__all__ = [
    "ABSOLUTE_DIFFERENCE_FORMULA",
    "ALIGNED_ANCHOR_FORMULA",
    "ARMS",
    "AbsoluteEffectGateError",
    "DIRECTIONAL_EPSILON",
    "ORIGINAL_ANCHOR_FORMULA",
    "PARENT_PROTOCOL_SHA256",
    "RAW_EFFECT_FORMULA",
    "SCHEMA",
    "SOURCE_IDENTITIES",
    "STACKS",
    "STATUS",
    "TARGET_OFFSETS",
    "TECHNICAL_TOLERANCE",
    "average_technical_raw_e",
    "build_addendum",
    "canonical_json_bytes",
    "classify_stack",
    "derive_absolute_anchors",
    "expected_counts",
    "sha256_file",
    "strict_json",
    "validate_addendum",
    "validate_alignments",
    "validate_parent_protocol",
    "validate_raw_e_matrix",
]
