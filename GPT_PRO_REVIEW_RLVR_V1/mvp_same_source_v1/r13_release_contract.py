"""Fail-closed static release and external authorization contract for R13.

The module is standard-library only.  It validates design bindings and signed
external decisions but deliberately contains no signing key, model loader, or
experiment backend.  A static master built here is never run authorization.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


PROTOCOL_SCHEMA = "r13-fixed-update-target-alignment-pilot-draft-r1"
MASTER_SCHEMA = "r13-static-master-inclusion-contract-r1"
MASTER_STATUS = "STATIC_CLOSURE_ONLY_EXTERNAL_REVIEW_AND_AUTHORIZATION_PENDING"
HUMAN_RECEIPT_SCHEMA = "r13-matched-panel-human-review-receipt-r1"
HUMAN_RECEIPT_TEMPLATE_SCHEMA = (
    "r13-matched-target-panel-human-review-receipt-template-r1"
)
HUMAN_RECEIPT_STATUS = "COMPLETED_INDEPENDENT_HUMAN_REVIEW"
HUMAN_REVIEW_CONTEXT = b"RLVR-R13-HUMAN-PANEL-REVIEW-V1\x00"
LAUNCH_ENVELOPE_SCHEMA = "r13-signed-launch-authorization-envelope-r1"
LAUNCH_PAYLOAD_SCHEMA = "r13-signed-launch-authorization-payload-r1"
LAUNCH_STATUS = "AUTHORIZED_BY_EXTERNAL_TRUSTED_SIGNER"
LAUNCH_CONTEXT = b"RLVR-R13-SIGNED-LAUNCH-AUTHORIZATION-V1\x00"
ACTION_ID = "RUN_R13_TARGET_ALIGNMENT_DEVELOPMENT_PILOT_R1"
MODEL_REVISION = "a10cc1512eabd3dde888204e902eca88bddb4951"
EXPECTED_STACKS = (
    "TP1-M0-A_TO_B",
    "TP1-M0-B_TO_A",
    "TP2-M0-A_TO_B",
    "TP2-M0-B_TO_A",
)
EXPECTED_ARMS = ("H0_ORIGINAL_M0_TARGET", "H1_SWITCHED_TARGET")
EXPECTED_IDENTITIES = (1, 2, 3, 4, 5)
EXPECTED_TARGET_OFFSETS = (1, 2, 3, 4, 5, 6)
VARIANTS = {
    "MINIMAL_4_PROCESS_A": {
        "replicates": ["A"],
        "os_processes": 4,
        "unique_design_source_updates": 20,
        "technical_update_executions": 20,
        "pre_target_identity_reads": 48,
        "post_target_identity_reads": 240,
        "total_target_identity_metric_cells": 288,
    },
    "REPRODUCIBILITY_8_PROCESS_AB": {
        "replicates": ["A", "B"],
        "os_processes": 8,
        "unique_design_source_updates": 20,
        "technical_update_executions": 40,
        "pre_target_identity_reads": 96,
        "post_target_identity_reads": 480,
        "total_target_identity_metric_cells": 576,
    },
}
REQUIRED_BINDING_ROLES = (
    "protocol",
    "source_bundles",
    "mapping_stacks",
    "base_target_calibration",
    "update_recipe_contract",
    "update_recipe_builder",
    "update_recipe_tests",
    "matched_panel_manifest",
    "matched_panel_jsonl",
    "matched_panel_raw_inventory_commitment",
    "matched_panel_allowlist_audit",
    "matched_panel_generator",
    "matched_panel_validator",
    "runner_core",
    "runner_tests",
    "model_backend_adapter",
    "model_backend_adapter_tests",
    "result_validator",
    "result_validator_tests",
    "model_inventory",
    "model_inventory_builder",
    "model_inventory_tests",
    "runtime_environment_reference",
    "runtime_environment_auditor",
    "runtime_environment_tests",
    "release_contract",
    "release_contract_tests",
)
ALLOWED_OPERATIONS = (
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "fixed_candidate_r13_target_alignment_development_pilot",
)
FORBIDDEN_OPERATIONS = (
    "hidden_audit_access",
    "sampled_rlvr",
    "multi_step_rlvr",
    "adaptive_retry",
    "outcome_dependent_rerun",
    "formal_or_confirmatory_claim",
    "automatic_next_stage",
)
HASH_RE = re.compile(r"[0-9a-f]{64}")
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
MAX_LAUNCH_TTL = timedelta(hours=24)
MAX_REVIEW_AGE = timedelta(days=30)
MAX_FUTURE_SKEW = timedelta(seconds=5)
HUMAN_REQUIRED_CHECKS = (
    "independently compare all 28 H0/H1 prompt pairs and confirm only the CODEBOOK line differs",
    "independently compare all 28 H0/H1 raw-row pairs and confirm only allowlisted mechanically implied leaves differ",
    "confirm every H0 prompt and raw row is byte-identical to its bound build_v5_repair_a original",
    "confirm K0..K6 candidate bijection and mechanically implied gold/shared/local labels in every row",
    "confirm q_H0 and q_H1 tables and r/q_H0/q_H1 distinctness for r=1..5",
)
HUMAN_COMPLETION_RULE = (
    "A reviewer must create a separate completed receipt, bind these exact hashes, "
    "fill every reviewer field, record PASS or REJECT, and provide a real "
    "externally verifiable signature. Editing this template in place is forbidden."
)
PROTOCOL_TOP_KEYS = {
    "schema_version",
    "status",
    "run_eligible",
    "model_execution_authorized",
    "model_execution_performed",
    "scientific_evidence",
    "formal_experiment",
    "formal_confirmatory",
    "purpose",
    "mechanism_scope",
    "motivation_and_outcome_awareness",
    "model_contract",
    "fixed_source_stacks",
    "fixed_source_update_contract",
    "target_alignment_intervention",
    "potential_outcomes_and_estimands",
    "execution_variants",
    "numerical_contract",
    "technical_pass_requires_all",
    "development_decision_rule",
    "result_validator_minimum_requirements",
    "claim_boundary",
    "authorization",
}
# R13 r1 is outcome-aware and frozen.  Any semantic or nested-schema change
# requires a new protocol version instead of being accepted under the r1 name.
EXPECTED_PROTOCOL_CANONICAL_SHA256 = (
    "91c45e4507ea2f7ba4e3bbeaa3cc749b7784d2fc422295841c1ae83101ca896a"
)


class R13ContractError(ValueError):
    """Raised when an R13 static or authorization invariant fails."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R13ContractError(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise R13ContractError("value is not canonical-JSON serializable") from error
    return (rendered + "\n").encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_hash(value: Any, field: str) -> str:
    require(
        isinstance(value, str) and HASH_RE.fullmatch(value) is not None,
        f"{field} must be lowercase SHA-256",
    )
    return value


def strict_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise R13ContractError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise R13ContractError(f"non-finite JSON value in {label}: {token}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R13ContractError(f"invalid UTF-8 JSON in {label}") from error
    require(isinstance(value, dict), f"{label} must be one JSON object")
    return value


def read_strict_json(path: Path, *, canonical: bool = False) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"missing regular JSON: {path}")
    raw = path.read_bytes()
    value = strict_json_bytes(raw, str(path))
    if canonical:
        require(raw == canonical_json_bytes(value), f"non-canonical JSON: {path}")
    return value


def parse_rfc3339_utc(value: Any, field: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{field} must end in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise R13ContractError(f"{field} is not RFC3339 UTC") from error
    require(parsed.tzinfo is not None, f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def format_rfc3339_utc(value: datetime) -> str:
    require(value.tzinfo is not None, "timestamp must be timezone aware")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def process_ids(variant: str) -> list[str]:
    require(variant in VARIANTS, f"unknown R13 execution variant: {variant}")
    return [
        f"{stack}|{replicate}"
        for stack in EXPECTED_STACKS
        for replicate in VARIANTS[variant]["replicates"]
    ]


def _q_table(factor: int) -> dict[str, int]:
    return {str(identity): (factor * identity) % 7 for identity in EXPECTED_IDENTITIES}


def validate_protocol(protocol: dict[str, Any]) -> None:
    require(set(protocol) == PROTOCOL_TOP_KEYS, "R13 protocol top-level schema drift")
    require(
        sha256_bytes(canonical_json_bytes(protocol))
        == EXPECTED_PROTOCOL_CANONICAL_SHA256,
        "R13 frozen protocol semantic or nested-schema drift",
    )
    require(protocol.get("schema_version") == PROTOCOL_SCHEMA, "R13 protocol version drift")
    require(protocol.get("status") == "DRAFT_STATIC_DESIGN_NOT_AUTHORIZED_NO_MODEL_RUN", "R13 protocol status drift")
    for field in (
        "run_eligible",
        "model_execution_authorized",
        "model_execution_performed",
        "scientific_evidence",
        "formal_experiment",
        "formal_confirmatory",
    ):
        require(protocol.get(field) is False, f"protocol {field} must be false")
    model = protocol.get("model_contract")
    require(isinstance(model, dict), "missing model contract")
    require(model.get("revision") == MODEL_REVISION, "R13 model revision drift")
    require(model.get("local_model_inventory_must_be_hash_bound") is True, "model inventory is not required")
    stacks = protocol.get("fixed_source_stacks")
    require(isinstance(stacks, dict), "missing fixed source stacks")
    require(stacks.get("ordered_stack_ids") == list(EXPECTED_STACKS), "R13 stack coverage drift")
    require(stacks.get("top_level_scientific_clusters") == 2, "scientific cluster count drift")
    update = protocol.get("fixed_source_update_contract")
    require(isinstance(update, dict), "missing source update contract")
    require(update.get("source_identities") == list(EXPECTED_IDENTITIES), "source identity drift")
    require(update.get("candidate_panel_size") == 7, "candidate panel size drift")
    require(
        "compute U exactly once" in str(update.get("same_update_for_both_target_arms", "")),
        "same-update invariant missing",
    )
    intervention = protocol.get("target_alignment_intervention")
    require(isinstance(intervention, dict), "missing target intervention")
    require(intervention.get("ordered_arms") == list(EXPECTED_ARMS), "target arm drift")
    require(intervention.get("q6_required") is True, "q6 is not required")
    expected_factors = {
        "H0_ORIGINAL_M0_TARGET": {"A_TO_B": 4, "B_TO_A": 2},
        "H1_SWITCHED_TARGET": {"A_TO_B": 3, "B_TO_A": 3},
    }
    for arm, by_direction in expected_factors.items():
        arm_value = intervention.get(arm)
        require(isinstance(arm_value, dict), f"missing intervention arm {arm}")
        for direction, factor in by_direction.items():
            direction_value = arm_value.get(direction)
            require(isinstance(direction_value, dict), f"missing {arm}/{direction}")
            require(direction_value.get("surface_factor") == factor, "surface factor drift")
            require(direction_value.get("q_surface_by_r") == _q_table(factor), "surface q table drift")
    for direction in ("A_TO_B", "B_TO_A"):
        for identity in EXPECTED_IDENTITIES:
            h0 = expected_factors["H0_ORIGINAL_M0_TARGET"][direction] * identity % 7
            h1 = expected_factors["H1_SWITCHED_TARGET"][direction] * identity % 7
            require(len({identity, h0, h1}) == 3 and 0 not in {identity, h0, h1}, "q locations are not pairwise distinct")
    variants = protocol.get("execution_variants")
    require(isinstance(variants, dict), "missing execution variants")
    for name, expected in VARIANTS.items():
        observed = variants.get(name)
        require(isinstance(observed, dict), f"missing execution variant {name}")
        for field, expected_value in expected.items():
            require(observed.get(field) == expected_value, f"{name}/{field} count drift")
    decision = protocol.get("development_decision_rule")
    require(isinstance(decision, dict), "missing decision rule")
    require(decision.get("automatic_progression") is False, "automatic progression enabled")
    authorization = protocol.get("authorization")
    require(isinstance(authorization, dict), "missing authorization boundary")
    require(authorization.get("cpu_static_design_and_tests") is True, "static work disabled")
    for field in (
        "tokenizer_load",
        "model_weight_load",
        "model_forward",
        "gradient",
        "optimizer_step",
        "run_r13_target_alignment_pilot",
    ):
        require(authorization.get(field) is False, f"protocol self-authorizes {field}")


MASTER_KEYS = {
    "schema_version",
    "status",
    "selected_variant",
    "ordered_process_ids",
    "ordered_stacks",
    "ordered_target_arms",
    "source_identities",
    "target_offsets",
    "design_counts",
    "binding_sha256_by_role",
    "model_revision",
    "development_only",
    "formal_confirmatory",
    "scientific_evidence",
    "model_execution_authorized",
    "model_execution_performed",
    "external_human_review_required",
    "external_signed_launch_required",
    "automatic_progression",
}


def build_master(*, selected_variant: str, binding_sha256_by_role: dict[str, str]) -> dict[str, Any]:
    require(selected_variant in VARIANTS, "unknown R13 master variant")
    require(
        set(binding_sha256_by_role) == set(REQUIRED_BINDING_ROLES),
        "R13 master binding role coverage drift",
    )
    for role, digest in binding_sha256_by_role.items():
        validate_hash(digest, f"binding {role}")
    require(
        len(set(binding_sha256_by_role.values())) == len(binding_sha256_by_role),
        "cross-role binding hash alias detected",
    )
    master = {
        "schema_version": MASTER_SCHEMA,
        "status": MASTER_STATUS,
        "selected_variant": selected_variant,
        "ordered_process_ids": process_ids(selected_variant),
        "ordered_stacks": list(EXPECTED_STACKS),
        "ordered_target_arms": list(EXPECTED_ARMS),
        "source_identities": list(EXPECTED_IDENTITIES),
        "target_offsets": list(EXPECTED_TARGET_OFFSETS),
        "design_counts": dict(VARIANTS[selected_variant]),
        "binding_sha256_by_role": dict(sorted(binding_sha256_by_role.items())),
        "model_revision": MODEL_REVISION,
        "development_only": True,
        "formal_confirmatory": False,
        "scientific_evidence": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "external_human_review_required": True,
        "external_signed_launch_required": True,
        "automatic_progression": False,
    }
    validate_master(master)
    return master


def validate_master(master: dict[str, Any]) -> None:
    require(set(master) == MASTER_KEYS, "R13 master schema drift")
    require(master["schema_version"] == MASTER_SCHEMA, "R13 master version drift")
    require(master["status"] == MASTER_STATUS, "R13 master status drift")
    variant = master["selected_variant"]
    require(variant in VARIANTS, "R13 master variant drift")
    require(master["ordered_process_ids"] == process_ids(variant), "process coverage drift")
    require(master["ordered_stacks"] == list(EXPECTED_STACKS), "master stack drift")
    require(master["ordered_target_arms"] == list(EXPECTED_ARMS), "master arm drift")
    require(master["source_identities"] == list(EXPECTED_IDENTITIES), "master identity drift")
    require(master["target_offsets"] == list(EXPECTED_TARGET_OFFSETS), "master q coverage drift")
    require(master["design_counts"] == VARIANTS[variant], "master design count drift")
    bindings = master["binding_sha256_by_role"]
    require(isinstance(bindings, dict) and set(bindings) == set(REQUIRED_BINDING_ROLES), "master binding coverage drift")
    for role, digest in bindings.items():
        validate_hash(digest, f"master binding {role}")
    require(len(set(bindings.values())) == len(bindings), "master cross-role hash alias")
    require(master["model_revision"] == MODEL_REVISION, "master model revision drift")
    expected_flags = {
        "development_only": True,
        "formal_confirmatory": False,
        "scientific_evidence": False,
        "model_execution_authorized": False,
        "model_execution_performed": False,
        "external_human_review_required": True,
        "external_signed_launch_required": True,
        "automatic_progression": False,
    }
    for field, expected in expected_flags.items():
        require(master[field] is expected, f"master {field} drift")


def validate_runtime_dependency_closure_reference(
    reference: dict[str, Any], expected_sha256: str
) -> None:
    """Require an actually bound runtime reference to claim full-byte closure.

    The current R13 reference deliberately records ``False`` and therefore
    cannot pass this launch gate.  A future fully inventoried environment must
    use a new reference before authorization is possible.
    """

    require(isinstance(reference, dict), "runtime dependency reference must be an object")
    validate_hash(expected_sha256, "bound runtime dependency reference")
    require(
        sha256_bytes(canonical_json_bytes(reference)) == expected_sha256,
        "runtime dependency reference bytes do not match master binding",
    )
    require(
        reference.get("full_dependency_content_hash_bound") is True,
        "BLOCKED_FULL_DEPENDENCY_CONTENT_BINDING_INCOMPLETE",
    )


@dataclass(frozen=True)
class TrustedSignerPolicy:
    """Verifier supplied by an operator-owned layer outside this release."""

    signer_id: str
    key_fingerprint_sha256: str
    signature_algorithm: str
    verify_signature: Callable[[bytes, bytes], bool]


@dataclass(frozen=True)
class ExternalTrustPolicy:
    """Out-of-repository trust roots required before any launch can pass.

    This release deliberately provides no production instance.  In particular,
    the model runner must not construct these callbacks itself.  The nonce
    callback must atomically consume the pair and return ``False`` for replay;
    a read-only "is unused" check is insufficient.
    """

    launch_signer: TrustedSignerPolicy
    verify_human_review_signature: Callable[[bytes, str, str, str], bool]
    runtime_dependency_closure_is_current: Callable[[str], bool]
    consume_authorization_nonce: Callable[[str, str], bool]


SIGNATURE_KEYS = {"algorithm", "signer_id", "key_fingerprint_sha256", "value_base64"}
ENVELOPE_KEYS = {"schema_version", "payload", "signature"}


def _require_signer_policy(
    policy: TrustedSignerPolicy | None, label: str
) -> TrustedSignerPolicy:
    require(
        isinstance(policy, TrustedSignerPolicy),
        f"BLOCKED_EXTERNAL_TRUST_POLICY_NOT_PROVISIONED: {label}",
    )
    require(isinstance(policy.signer_id, str) and policy.signer_id.strip(), f"{label} signer id missing")
    validate_hash(policy.key_fingerprint_sha256, f"{label} key fingerprint")
    require(
        isinstance(policy.signature_algorithm, str)
        and policy.signature_algorithm.strip(),
        f"{label} signature algorithm missing",
    )
    require(callable(policy.verify_signature), f"{label} signature verifier missing")
    return policy


def _require_external_trust_policy(
    policy: ExternalTrustPolicy | None,
) -> ExternalTrustPolicy:
    require(
        isinstance(policy, ExternalTrustPolicy),
        "BLOCKED_EXTERNAL_TRUST_POLICY_NOT_PROVISIONED: launch, review, dependency, and nonce trust are external",
    )
    _require_signer_policy(policy.launch_signer, "launch")
    require(
        callable(policy.verify_human_review_signature),
        "BLOCKED_EXTERNAL_HUMAN_REVIEW_VERIFIER_NOT_PROVISIONED",
    )
    require(
        callable(policy.runtime_dependency_closure_is_current),
        "BLOCKED_FULL_DEPENDENCY_CONTENT_BINDING_INCOMPLETE",
    )
    require(
        callable(policy.consume_authorization_nonce),
        "BLOCKED_ATOMIC_NONCE_CONSUMER_NOT_PROVISIONED",
    )
    return policy


def _verify_envelope(
    envelope: dict[str, Any],
    *,
    envelope_schema: str,
    context: bytes,
    policy: TrustedSignerPolicy | None,
) -> dict[str, Any]:
    selected_policy = _require_signer_policy(policy, "signed envelope")
    require(set(envelope) == ENVELOPE_KEYS, "signed envelope schema drift")
    require(envelope["schema_version"] == envelope_schema, "signed envelope version drift")
    payload = envelope["payload"]
    signature = envelope["signature"]
    require(isinstance(payload, dict), "signed payload must be an object")
    require(isinstance(signature, dict) and set(signature) == SIGNATURE_KEYS, "signature schema drift")
    require(signature["algorithm"] == selected_policy.signature_algorithm, "signature algorithm mismatch")
    require(signature["signer_id"] == selected_policy.signer_id, "signer id mismatch")
    require(signature["key_fingerprint_sha256"] == selected_policy.key_fingerprint_sha256, "signer key mismatch")
    try:
        signature_bytes = base64.b64decode(signature["value_base64"], validate=True)
    except (TypeError, ValueError, binascii.Error) as error:
        raise R13ContractError("invalid base64 signature") from error
    require(len(signature_bytes) > 0, "empty signature")
    signed_message = context + canonical_json_bytes(payload)
    require(
        selected_policy.verify_signature(signed_message, signature_bytes),
        "external signature verification failed",
    )
    return payload


HUMAN_RECEIPT_KEYS = {
    "asset_bindings",
    "completion_rule",
    "decision",
    "required_checks",
    "review_completed",
    "review_method",
    "review_notes",
    "reviewed_at_utc",
    "reviewer_affiliation_or_role",
    "reviewer_name",
    "schema_version",
    "signature",
    "signature_algorithm",
    "signature_key_id",
    "status",
    "template_only",
    "protocol_sha256",
    "allowlist_receipt_sha256",
}
HUMAN_ASSET_BINDING_KEYS = {
    "manifest_relpath",
    "manifest_sha256",
    "panel_jsonl_relpath",
    "panel_jsonl_sha256",
    "raw_inventory_commitment_sha256",
}


def validate_human_review_receipt(
    receipt: dict[str, Any],
    *,
    verify_signature: Callable[[bytes, str, str, str], bool],
    expected_protocol_sha256: str,
    expected_panel_manifest_sha256: str,
    expected_panel_jsonl_sha256: str,
    expected_raw_inventory_commitment_sha256: str,
    expected_allowlist_receipt_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    require(
        isinstance(receipt, dict) and set(receipt) == HUMAN_RECEIPT_KEYS,
        "human-review receipt schema drift",
    )
    require(receipt["schema_version"] == HUMAN_RECEIPT_SCHEMA, "human-review receipt version drift")
    require(receipt["status"] == HUMAN_RECEIPT_STATUS, "human review is incomplete")
    require(receipt["template_only"] is False, "human review is still a template")
    require(receipt["review_completed"] is True, "human review was not completed")
    require(receipt["decision"] == "PASS", "human review did not pass")
    require(receipt["completion_rule"] == HUMAN_COMPLETION_RULE, "human review completion rule drift")
    require(receipt["required_checks"] == list(HUMAN_REQUIRED_CHECKS), "human review required-check drift")
    bindings = receipt["asset_bindings"]
    require(
        isinstance(bindings, dict) and set(bindings) == HUMAN_ASSET_BINDING_KEYS,
        "human-review asset-binding schema drift",
    )
    require(
        bindings["manifest_relpath"] == "R13_MATCHED_TARGET_PANEL_MANIFEST_R1.json",
        "human-review manifest relpath drift",
    )
    require(
        bindings["panel_jsonl_relpath"] == "R13_MATCHED_TARGET_PANELS_R1.jsonl",
        "human-review panel relpath drift",
    )
    expected = {
        "protocol_sha256": expected_protocol_sha256,
        "allowlist_receipt_sha256": expected_allowlist_receipt_sha256,
    }
    expected_bindings = {
        "manifest_sha256": expected_panel_manifest_sha256,
        "panel_jsonl_sha256": expected_panel_jsonl_sha256,
        "raw_inventory_commitment_sha256": expected_raw_inventory_commitment_sha256,
    }
    for field, digest in expected.items():
        validate_hash(digest, f"expected {field}")
        require(receipt[field] == digest, f"human review {field} mismatch")
    for field, digest in expected_bindings.items():
        validate_hash(digest, f"expected asset binding {field}")
        require(bindings[field] == digest, f"human review asset binding {field} mismatch")
    reviewed_at = parse_rfc3339_utc(receipt["reviewed_at_utc"], "reviewed_at_utc")
    selected_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    require(reviewed_at <= selected_now + MAX_FUTURE_SKEW, "human review is future dated")
    require(selected_now - reviewed_at <= MAX_REVIEW_AGE, "human review is stale")
    for field in (
        "reviewer_name",
        "reviewer_affiliation_or_role",
        "review_method",
        "review_notes",
        "signature",
        "signature_algorithm",
        "signature_key_id",
    ):
        require(isinstance(receipt[field], str) and receipt[field].strip(), f"human review {field} missing")
    require(callable(verify_signature), "external human-review verifier missing")
    signed_payload = {key: value for key, value in receipt.items() if key != "signature"}
    signed_message = HUMAN_REVIEW_CONTEXT + canonical_json_bytes(signed_payload)
    require(
        verify_signature(
            signed_message,
            receipt["signature"],
            receipt["signature_algorithm"],
            receipt["signature_key_id"],
        ),
        "human-review signature verification failed",
    )
    return receipt


LAUNCH_PAYLOAD_KEYS = {
    "schema_version",
    "status",
    "action_id",
    "authorization_id",
    "single_use_nonce",
    "issued_at",
    "expires_at",
    "selected_variant",
    "ordered_process_ids",
    "master_sha256",
    "protocol_sha256",
    "model_inventory_sha256",
    "matched_panel_manifest_sha256",
    "human_review_receipt_sha256",
    "runner_sha256",
    "result_validator_sha256",
    "release_member_manifest_sha256",
    "output_root_binding_sha256",
    "allowed_operations",
    "forbidden_operations",
    "one_shot",
    "adaptive_retry",
    "automatic_progression",
    "development_only",
    "formal_confirmatory",
    "model_execution_authorized",
}


def validate_signed_launch(
    envelope: dict[str, Any],
    *,
    trusted_policy: ExternalTrustPolicy | None,
    master: dict[str, Any],
    human_review_receipt: dict[str, Any],
    runtime_environment_reference: dict[str, Any],
    expected_member_manifest_sha256: str,
    expected_output_root_binding_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    external = _require_external_trust_policy(trusted_policy)
    validate_master(master)
    master_sha256 = sha256_bytes(canonical_json_bytes(master))
    bindings = master["binding_sha256_by_role"]
    validate_runtime_dependency_closure_reference(
        runtime_environment_reference, bindings["runtime_environment_reference"]
    )
    selected_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    validate_human_review_receipt(
        human_review_receipt,
        verify_signature=external.verify_human_review_signature,
        expected_protocol_sha256=bindings["protocol"],
        expected_panel_manifest_sha256=bindings["matched_panel_manifest"],
        expected_panel_jsonl_sha256=bindings["matched_panel_jsonl"],
        expected_raw_inventory_commitment_sha256=bindings[
            "matched_panel_raw_inventory_commitment"
        ],
        expected_allowlist_receipt_sha256=bindings["matched_panel_allowlist_audit"],
        now=selected_now,
    )
    human_review_receipt_sha256 = sha256_bytes(
        canonical_json_bytes(human_review_receipt)
    )
    payload = _verify_envelope(
        envelope,
        envelope_schema=LAUNCH_ENVELOPE_SCHEMA,
        context=LAUNCH_CONTEXT,
        policy=external.launch_signer,
    )
    require(set(payload) == LAUNCH_PAYLOAD_KEYS, "launch payload schema drift")
    require(payload["schema_version"] == LAUNCH_PAYLOAD_SCHEMA, "launch payload version drift")
    require(payload["status"] == LAUNCH_STATUS, "launch is not externally authorized")
    require(payload["action_id"] == ACTION_ID, "launch action drift")
    try:
        parsed_id = uuid.UUID(str(payload["authorization_id"]), version=4)
    except (ValueError, AttributeError) as error:
        raise R13ContractError("authorization_id is not UUIDv4") from error
    require(str(parsed_id) == payload["authorization_id"], "authorization_id is not canonical UUIDv4")
    require(isinstance(payload["single_use_nonce"], str) and NONCE_RE.fullmatch(payload["single_use_nonce"]) is not None, "invalid single-use nonce")
    issued = parse_rfc3339_utc(payload["issued_at"], "issued_at")
    expires = parse_rfc3339_utc(payload["expires_at"], "expires_at")
    require(issued < expires, "launch expiry does not follow issue time")
    require(expires - issued <= MAX_LAUNCH_TTL, "launch TTL exceeds 24 hours")
    require(issued <= selected_now + MAX_FUTURE_SKEW, "launch is future dated")
    require(selected_now < expires, "launch authorization expired")
    variant = master["selected_variant"]
    require(payload["selected_variant"] == variant, "launch variant mismatch")
    require(payload["ordered_process_ids"] == process_ids(variant), "launch process coverage drift")
    external_hashes = {
        "master_sha256": master_sha256,
        "human_review_receipt_sha256": human_review_receipt_sha256,
        "release_member_manifest_sha256": expected_member_manifest_sha256,
        "output_root_binding_sha256": expected_output_root_binding_sha256,
    }
    for field, expected in external_hashes.items():
        validate_hash(expected, f"expected {field}")
        require(payload[field] == expected, f"launch {field} mismatch")
    require(payload["protocol_sha256"] == bindings["protocol"], "launch protocol mismatch")
    require(payload["model_inventory_sha256"] == bindings["model_inventory"], "launch model inventory mismatch")
    require(payload["matched_panel_manifest_sha256"] == bindings["matched_panel_manifest"], "launch panel manifest mismatch")
    require(payload["runner_sha256"] == bindings["runner_core"], "launch runner mismatch")
    require(payload["result_validator_sha256"] == bindings["result_validator"], "launch validator mismatch")
    require(payload["allowed_operations"] == list(ALLOWED_OPERATIONS), "launch allowed operations drift")
    require(payload["forbidden_operations"] == list(FORBIDDEN_OPERATIONS), "launch forbidden operations drift")
    expected_flags = {
        "one_shot": True,
        "adaptive_retry": False,
        "automatic_progression": False,
        "development_only": True,
        "formal_confirmatory": False,
        "model_execution_authorized": True,
    }
    for field, expected in expected_flags.items():
        require(payload[field] is expected, f"launch {field} drift")
    require(
        external.runtime_dependency_closure_is_current(
            bindings["runtime_environment_reference"]
        ),
        "BLOCKED_FULL_DEPENDENCY_CONTENT_BINDING_INCOMPLETE",
    )
    require(
        external.consume_authorization_nonce(
            payload["authorization_id"], payload["single_use_nonce"]
        ),
        "launch authorization replay or atomic nonce consumption failed",
    )
    return payload
