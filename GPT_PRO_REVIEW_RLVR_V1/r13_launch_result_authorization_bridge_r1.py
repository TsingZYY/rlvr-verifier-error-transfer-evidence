"""Fail-closed R13 launch-to-result authorization compatibility bridge.

The frozen R13 release contract signs a launch *envelope* under
``RLVR-R13-SIGNED-LAUNCH-AUTHORIZATION-V1`` and atomically consumes
``(authorization_id, single_use_nonce)``.  The frozen result validator instead
expects a flat authorization object under a different legacy signature context
and asks whether ``(authorization_id, run_id)`` is still unconsumed.  Treating
the original launch signature as a signature over that flat object would be a
signature-substitution bug; asking for an unconsumed nonce after launch would
also contradict the release state machine.

This module leaves both frozen files unchanged.  It:

* verifies the original release envelope with its original context;
* atomically binds its one-shot nonce to one run id and one sealed-batch
  commitment through operator-owned callbacks;
* builds an exact, deterministic projection for the legacy result-validator
  schema; and
* supplies a narrow validator-policy adapter that re-verifies the *original*
  signed launch message and atomically advances the same claim from
  ``START_CLAIMED`` to ``RESULT_VALIDATION_CLAIMED``.

No trust root or durable ledger is implemented here.  The bridge does not prove
that a model started or completed, and it cannot authorize scientific result
release: independently trusted start and terminal receipts plus a sealed-batch
collector/release decision remain required.  Physical serial execution is
allowed, but intermediate scientific results must remain sealed and must not
change later cells.  Under the current signed launch, a technical failure burns
the authorization; a retry requires a fresh pre-authorized nonce.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
from typing import Any, Callable
import uuid

from formal_g1_development_r1 import validate_r13_target_alignment_results_r1 as result_validator
from mvp_same_source_v1 import r13_release_contract as release_contract


BRIDGE_SCHEMA = "r13-launch-result-authorization-bridge-r1"
BATCH_COMMITMENT_SCHEMA = "r13-sealed-development-batch-commitment-r1"
BATCH_COMMITMENT_STATUS = "PREFROZEN_ONE_SHOT_RESULTS_SEALED"
RESULT_AUTHORIZATION_CONTEXT = b"RLVR-R13-EXTERNAL-RUN-AUTHORIZATION-V1\x00"

VALIDATOR_ALLOWED_OPERATIONS = (
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "run_r13_target_alignment_pilot",
)
VALIDATOR_FORBIDDEN_OPERATIONS = (
    "adaptive_retry",
    "outcome_dependent_rerun",
    "hidden_audit",
    "sampled_rlvr",
    "formal_or_confirmatory_claim",
)

LAUNCH_OPERATION_TO_VALIDATOR_OPERATION = {
    "tokenizer_load": "tokenizer_load",
    "model_weight_load": "model_weight_load",
    "model_forward": "model_forward",
    "gradient": "gradient",
    "optimizer_step": "optimizer_step",
    "fixed_candidate_r13_target_alignment_development_pilot": (
        "run_r13_target_alignment_pilot"
    ),
}
LAUNCH_FORBIDDEN_TO_VALIDATOR_FORBIDDEN = {
    "adaptive_retry": "adaptive_retry",
    "outcome_dependent_rerun": "outcome_dependent_rerun",
    "hidden_audit_access": "hidden_audit",
    "sampled_rlvr": "sampled_rlvr",
    "formal_or_confirmatory_claim": "formal_or_confirmatory_claim",
}

BATCH_COMMITMENT_KEYS = {
    "schema_version",
    "status",
    "authorization_id",
    "single_use_nonce",
    "run_id",
    "action_id",
    "selected_variant",
    "ordered_process_ids",
    "master_sha256",
    "protocol_sha256",
    "release_member_manifest_sha256",
    "output_root_binding_sha256",
    "physical_serial_execution_allowed",
    "all_cells_frozen_before_first_model_action",
    "intermediate_scientific_result_release_allowed",
    "result_dependent_change_to_later_cells_allowed",
    "same_nonce_retry_allowed",
    "technical_failure_disposition",
    "result_release_condition",
}


class R13AuthorizationBridgeError(RuntimeError):
    """A compatibility, external-trust, or release-boundary gate failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R13AuthorizationBridgeError(message)


def canonical_json_bytes(value: Any) -> bytes:
    """Use the byte-identical canonicalization shared by both frozen modules."""

    release_bytes = release_contract.canonical_json_bytes(value)
    validator_bytes = result_validator.canonical_json_bytes(value)
    require(
        release_bytes == validator_bytes,
        "release/validator canonical JSON implementations diverged",
    )
    return release_bytes


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_uuid4(value: Any, label: str) -> str:
    require(isinstance(value, str), f"{label} must be a string")
    try:
        parsed = uuid.UUID(value, version=4)
    except (ValueError, AttributeError) as error:
        raise R13AuthorizationBridgeError(f"{label} is not UUIDv4") from error
    require(str(parsed) == value, f"{label} is not canonical UUIDv4")
    return value


@dataclass(frozen=True)
class ExternalBridgeTrustPolicy:
    """Operator-owned trust and atomic state transitions.

    This repository intentionally provides no production implementation.
    ``atomic_claim_launch`` must perform exactly one durable transition from an
    unseen authorization to a start claim bound to the supplied run and batch.
    ``atomic_claim_result_validation`` must perform exactly one durable
    transition from that matching start claim to a result-validation claim.
    Both callbacks must return ``False`` for replay or any binding mismatch.
    """

    launch_signer: release_contract.TrustedSignerPolicy
    verify_human_review_signature: Callable[[bytes, str, str, str], bool]
    runtime_dependency_closure_is_current: Callable[[str], bool]
    atomic_claim_launch: Callable[[str, str, str, str], bool]
    atomic_claim_result_validation: Callable[[str, str, str, str], bool]


@dataclass(frozen=True)
class AuthorizationBridge:
    """A compatibility projection, not an execution or result-release receipt."""

    schema_version: str
    launch_payload: dict[str, Any]
    validator_authorization: dict[str, Any]
    validator_expected_bindings: dict[str, str]
    validator_trusted_policy: result_validator.ExternalTrustPolicy
    batch_commitment: dict[str, Any]
    batch_commitment_sha256: str
    audit: dict[str, Any]


def _require_external_policy(
    policy: ExternalBridgeTrustPolicy | None,
) -> ExternalBridgeTrustPolicy:
    require(
        isinstance(policy, ExternalBridgeTrustPolicy),
        "BLOCKED_EXTERNAL_BRIDGE_TRUST_POLICY_NOT_PROVISIONED",
    )
    signer = policy.launch_signer
    require(
        isinstance(signer, release_contract.TrustedSignerPolicy),
        "BLOCKED_EXTERNAL_LAUNCH_SIGNER_POLICY_NOT_PROVISIONED",
    )
    release_contract.validate_hash(
        signer.key_fingerprint_sha256, "bridge launch signer fingerprint"
    )
    require(bool(signer.signer_id.strip()), "bridge launch signer id missing")
    require(
        bool(signer.signature_algorithm.strip()),
        "bridge launch signature algorithm missing",
    )
    for callback, message in (
        (signer.verify_signature, "external launch signature verifier missing"),
        (
            policy.verify_human_review_signature,
            "external human-review signature verifier missing",
        ),
        (
            policy.runtime_dependency_closure_is_current,
            "external dependency-closure verifier missing",
        ),
        (policy.atomic_claim_launch, "external atomic launch claimant missing"),
        (
            policy.atomic_claim_result_validation,
            "external atomic result-validation claimant missing",
        ),
    ):
        require(callable(callback), message)
    return policy


def _unverified_launch_payload(envelope: Any) -> dict[str, Any]:
    """Read only enough to form a callback closure; this grants no authority."""

    require(
        isinstance(envelope, dict)
        and set(envelope) == release_contract.ENVELOPE_KEYS,
        "signed launch envelope schema drift",
    )
    require(
        envelope.get("schema_version") == release_contract.LAUNCH_ENVELOPE_SCHEMA,
        "signed launch envelope version drift",
    )
    payload = envelope.get("payload")
    require(isinstance(payload, dict), "signed launch payload must be an object")
    return payload


def build_sealed_batch_commitment(
    launch_payload: dict[str, Any], *, run_id: str
) -> dict[str, Any]:
    """Build the deterministic batch claim bound by the external ledger.

    The commitment permits physical serial execution.  It forbids releasing
    intermediate scientific outcomes or using them to alter remaining cells.
    The current launch is one-shot with no same-nonce retry; a technical fault
    therefore terminates this authorization and requires a fresh authorization.
    """

    _valid_uuid4(run_id, "bridge run id")
    required_launch_fields = {
        "authorization_id",
        "single_use_nonce",
        "action_id",
        "selected_variant",
        "ordered_process_ids",
        "master_sha256",
        "protocol_sha256",
        "release_member_manifest_sha256",
        "output_root_binding_sha256",
        "one_shot",
        "adaptive_retry",
        "automatic_progression",
        "forbidden_operations",
    }
    require(
        required_launch_fields.issubset(launch_payload),
        "launch payload lacks batch-commitment fields",
    )
    require(launch_payload["one_shot"] is True, "batch launch is not one-shot")
    require(
        launch_payload["adaptive_retry"] is False,
        "batch launch permits adaptive retry",
    )
    require(
        launch_payload["automatic_progression"] is False,
        "batch launch permits automatic progression",
    )
    forbidden = set(launch_payload["forbidden_operations"])
    require(
        {"outcome_dependent_rerun", "adaptive_retry"}.issubset(forbidden),
        "batch launch does not forbid result-dependent rerun",
    )
    commitment = {
        "schema_version": BATCH_COMMITMENT_SCHEMA,
        "status": BATCH_COMMITMENT_STATUS,
        "authorization_id": launch_payload["authorization_id"],
        "single_use_nonce": launch_payload["single_use_nonce"],
        "run_id": run_id,
        "action_id": launch_payload["action_id"],
        "selected_variant": launch_payload["selected_variant"],
        "ordered_process_ids": list(launch_payload["ordered_process_ids"]),
        "master_sha256": launch_payload["master_sha256"],
        "protocol_sha256": launch_payload["protocol_sha256"],
        "release_member_manifest_sha256": launch_payload[
            "release_member_manifest_sha256"
        ],
        "output_root_binding_sha256": launch_payload[
            "output_root_binding_sha256"
        ],
        "physical_serial_execution_allowed": True,
        "all_cells_frozen_before_first_model_action": True,
        "intermediate_scientific_result_release_allowed": False,
        "result_dependent_change_to_later_cells_allowed": False,
        "same_nonce_retry_allowed": False,
        "technical_failure_disposition": (
            "ABORT_AND_BURN_AUTHORIZATION; ANY_RETRY_REQUIRES_A_FRESH_"
            "PREFROZEN_SIGNED_AUTHORIZATION"
        ),
        "result_release_condition": (
            "WITHHOLD_UNTIL_ALL_PREFROZEN_CELLS_HAVE_TRUSTED_TERMINAL_"
            "DISPOSITIONS_AND_THE_COMPLETE_BATCH_IS_VALIDATED_ONCE"
        ),
    }
    require(set(commitment) == BATCH_COMMITMENT_KEYS, "batch commitment drift")
    return commitment


def _validator_bindings_from_launch(
    *, launch_payload: dict[str, Any], master: dict[str, Any]
) -> dict[str, str]:
    bindings = master["binding_sha256_by_role"]
    mapped = {
        "protocol_sha256": bindings["protocol"],
        "panel_bundle_sha256": bindings["matched_panel_manifest"],
        "allowlist_receipt_sha256": bindings["matched_panel_allowlist_audit"],
        "human_review_receipt_sha256": launch_payload[
            "human_review_receipt_sha256"
        ],
        "runner_sha256": bindings["runner_core"],
        "validator_sha256": bindings["result_validator"],
        "model_inventory_sha256": bindings["model_inventory"],
        "source_assets_sha256": bindings["source_bundles"],
    }
    require(
        set(mapped) == set(result_validator.AUTH_BOUND_BINDING_KEYS),
        "release-to-validator binding projection drift",
    )
    for label, digest in mapped.items():
        release_contract.validate_hash(digest, f"projected {label}")
    return mapped


def _validate_operation_projection(launch_payload: dict[str, Any]) -> None:
    allowed = launch_payload["allowed_operations"]
    require(
        set(allowed) == set(LAUNCH_OPERATION_TO_VALIDATOR_OPERATION),
        "release allowed-operation vocabulary drift",
    )
    mapped_allowed = tuple(
        LAUNCH_OPERATION_TO_VALIDATOR_OPERATION[item] for item in allowed
    )
    require(
        mapped_allowed == VALIDATOR_ALLOWED_OPERATIONS,
        "release-to-validator allowed-operation ordering drift",
    )

    forbidden = set(launch_payload["forbidden_operations"])
    require(
        set(LAUNCH_FORBIDDEN_TO_VALIDATOR_FORBIDDEN).issubset(forbidden),
        "release forbidden-operation vocabulary is weaker than validator",
    )
    mapped_forbidden = {
        LAUNCH_FORBIDDEN_TO_VALIDATOR_FORBIDDEN[item]
        for item in LAUNCH_FORBIDDEN_TO_VALIDATOR_FORBIDDEN
    }
    require(
        mapped_forbidden == set(VALIDATOR_FORBIDDEN_OPERATIONS),
        "release-to-validator forbidden-operation projection drift",
    )


def _build_validator_authorization(
    *,
    launch_payload: dict[str, Any],
    envelope_signature: dict[str, Any],
    run_id: str,
    projected_bindings: dict[str, str],
) -> dict[str, Any]:
    _validate_operation_projection(launch_payload)
    authorization = {
        "schema_version": result_validator.AUTHORIZATION_SCHEMA,
        "status": "AUTHORIZED_BEFORE_MODEL_LOAD",
        "synthetic_fixture": False,
        "authorization_id": launch_payload["authorization_id"],
        "run_id": run_id,
        "issued_at_utc": launch_payload["issued_at"],
        "expires_at_utc": launch_payload["expires_at"],
        "selected_variant": launch_payload["selected_variant"],
        "ordered_process_ids": list(launch_payload["ordered_process_ids"]),
        "bindings": dict(projected_bindings),
        "model_execution_authorized": launch_payload[
            "model_execution_authorized"
        ],
        "single_use": launch_payload["one_shot"],
        "allowed_operations": list(VALIDATOR_ALLOWED_OPERATIONS),
        "forbidden_operations": list(VALIDATOR_FORBIDDEN_OPERATIONS),
        "trusted_signer_id": envelope_signature["signer_id"],
        "trusted_signer_key_fingerprint_sha256": envelope_signature[
            "key_fingerprint_sha256"
        ],
        "signature_algorithm": envelope_signature["algorithm"],
        # This is an identifier/carrier for the original launch signature.  It
        # is never treated as a signature over this projected object.
        "signature": envelope_signature["value_base64"],
    }
    require(
        set(authorization) == result_validator.AUTHORIZATION_KEYS,
        "validator authorization projection schema drift",
    )
    return authorization


def _build_validator_policy_adapter(
    *,
    external: ExternalBridgeTrustPolicy,
    envelope: dict[str, Any],
    launch_payload: dict[str, Any],
    authorization: dict[str, Any],
    run_id: str,
    batch_commitment_sha256: str,
) -> result_validator.ExternalTrustPolicy:
    signature_record = envelope["signature"]
    signature_text = signature_record["value_base64"]
    try:
        signature_bytes = base64.b64decode(signature_text, validate=True)
    except (TypeError, ValueError, binascii.Error) as error:
        raise R13AuthorizationBridgeError("invalid original launch signature base64") from error
    require(bool(signature_bytes), "empty original launch signature")

    expected_validator_message = RESULT_AUTHORIZATION_CONTEXT + canonical_json_bytes(
        {key: value for key, value in authorization.items() if key != "signature"}
    )
    original_launch_message = release_contract.LAUNCH_CONTEXT + canonical_json_bytes(
        launch_payload
    )
    authorization_id = launch_payload["authorization_id"]
    nonce = launch_payload["single_use_nonce"]

    def verify_authorization_signature(message: bytes, signature: str) -> bool:
        """Verify exact projection plus the original release signature/context."""

        if not isinstance(message, bytes) or not isinstance(signature, str):
            return False
        if not hmac.compare_digest(message, expected_validator_message):
            return False
        if not hmac.compare_digest(signature, signature_text):
            return False
        try:
            return bool(
                external.launch_signer.verify_signature(
                    original_launch_message, signature_bytes
                )
            )
        except Exception:
            return False

    def claim_result_validation(
        observed_authorization_id: str, observed_run_id: str
    ) -> bool:
        """Adapt the legacy 'unconsumed' callback to a second atomic claim.

        The nonce is already consumed into ``START_CLAIMED``.  Returning true
        means the external ledger atomically advanced that exact active claim
        to ``RESULT_VALIDATION_CLAIMED``; it never means the nonce was unused.
        """

        if observed_authorization_id != authorization_id or observed_run_id != run_id:
            return False
        try:
            return bool(
                external.atomic_claim_result_validation(
                    authorization_id, nonce, run_id, batch_commitment_sha256
                )
            )
        except Exception:
            return False

    return result_validator.ExternalTrustPolicy(
        authorization_signer_id=external.launch_signer.signer_id,
        authorization_key_fingerprint_sha256=(
            external.launch_signer.key_fingerprint_sha256
        ),
        authorization_signature_algorithm=(
            external.launch_signer.signature_algorithm
        ),
        verify_authorization_signature=verify_authorization_signature,
        verify_human_review_signature=external.verify_human_review_signature,
        # Frozen field name retained only for API compatibility.  The callback
        # performs START_CLAIMED -> RESULT_VALIDATION_CLAIMED atomically.
        authorization_nonce_is_unconsumed=claim_result_validation,
    )


def bridge_signed_launch_to_result_authorization(
    envelope: dict[str, Any],
    *,
    run_id: str,
    trusted_policy: ExternalBridgeTrustPolicy | None,
    master: dict[str, Any],
    human_review_receipt: dict[str, Any],
    runtime_environment_reference: dict[str, Any],
    expected_member_manifest_sha256: str,
    expected_output_root_binding_sha256: str,
    now: Any | None = None,
) -> AuthorizationBridge:
    """Validate, atomically claim, and project one signed launch.

    Success establishes authorization-schema compatibility only.  It does not
    attest a model start/terminal event and does not release scientific results.
    """

    external = _require_external_policy(trusted_policy)
    _valid_uuid4(run_id, "bridge run id")
    unverified_payload = _unverified_launch_payload(envelope)
    batch_commitment = build_sealed_batch_commitment(
        unverified_payload, run_id=run_id
    )
    batch_commitment_sha256 = sha256_bytes(canonical_json_bytes(batch_commitment))

    def consume_and_bind_launch(authorization_id: str, nonce: str) -> bool:
        if (
            authorization_id != unverified_payload.get("authorization_id")
            or nonce != unverified_payload.get("single_use_nonce")
        ):
            return False
        try:
            return bool(
                external.atomic_claim_launch(
                    authorization_id,
                    nonce,
                    run_id,
                    batch_commitment_sha256,
                )
            )
        except Exception:
            return False

    release_policy = release_contract.ExternalTrustPolicy(
        launch_signer=external.launch_signer,
        verify_human_review_signature=external.verify_human_review_signature,
        runtime_dependency_closure_is_current=(
            external.runtime_dependency_closure_is_current
        ),
        consume_authorization_nonce=consume_and_bind_launch,
    )
    try:
        launch_payload = release_contract.validate_signed_launch(
            envelope,
            trusted_policy=release_policy,
            master=master,
            human_review_receipt=human_review_receipt,
            runtime_environment_reference=runtime_environment_reference,
            expected_member_manifest_sha256=expected_member_manifest_sha256,
            expected_output_root_binding_sha256=expected_output_root_binding_sha256,
            now=now,
        )
    except release_contract.R13ContractError as error:
        raise R13AuthorizationBridgeError(str(error)) from error

    require(
        launch_payload == unverified_payload,
        "verified launch payload differs from commitment payload",
    )
    projected_bindings = _validator_bindings_from_launch(
        launch_payload=launch_payload, master=master
    )
    authorization = _build_validator_authorization(
        launch_payload=launch_payload,
        envelope_signature=envelope["signature"],
        run_id=run_id,
        projected_bindings=projected_bindings,
    )
    validator_expected_bindings = {
        **projected_bindings,
        "authorization_sha256": sha256_bytes(canonical_json_bytes(authorization)),
    }
    require(
        set(validator_expected_bindings) == set(result_validator.BINDING_KEYS),
        "validator full binding projection drift",
    )
    validator_policy = _build_validator_policy_adapter(
        external=external,
        envelope=envelope,
        launch_payload=launch_payload,
        authorization=authorization,
        run_id=run_id,
        batch_commitment_sha256=batch_commitment_sha256,
    )
    audit = {
        "schema_version": BRIDGE_SCHEMA,
        "release_envelope_schema_accepted": True,
        "validator_flat_schema_projected": True,
        "authorization_schema_and_context_compatibility_closed": True,
        "original_release_signature_context_reused_exactly": True,
        "projected_flat_object_claimed_as_directly_signed": False,
        "batch_commitment_directly_signed_in_launch_payload": False,
        "nonce_transition_at_launch": "UNSEEN_TO_START_CLAIMED",
        "nonce_transition_at_result_validation": (
            "START_CLAIMED_TO_RESULT_VALIDATION_CLAIMED"
        ),
        "physical_serial_execution_allowed": True,
        "intermediate_scientific_result_release_allowed": False,
        "result_dependent_change_to_later_cells_allowed": False,
        "model_start_attested": False,
        "model_terminal_execution_attested": False,
        "sealed_batch_completion_attested": False,
        "production_result_validation_chain_closed": False,
        "scientific_result_release_authorized": False,
        "unresolved_p0": [
            "NO_TRUSTED_MEMBER_MANIFEST_PROOF_BINDING_THIS_BRIDGE_IMPLEMENTATION",
            "NO_INDEPENDENTLY_SIGNED_TRUSTED_START_RECEIPT_BOUND_TO_RUN_AND_BATCH",
            "NO_INDEPENDENTLY_SIGNED_TERMINAL_RECEIPT_BOUND_TO_RESULT_BYTES",
            "NO_TRUSTED_SEALED_BATCH_COLLECTOR_AND_ATOMIC_RELEASE_DECISION",
            "NO_PRODUCTION_EXTERNAL_SIGNER_OR_DURABLE_NONCE_LEDGER_IN_REPOSITORY",
        ],
    }
    return AuthorizationBridge(
        schema_version=BRIDGE_SCHEMA,
        launch_payload=launch_payload,
        validator_authorization=authorization,
        validator_expected_bindings=validator_expected_bindings,
        validator_trusted_policy=validator_policy,
        batch_commitment=batch_commitment,
        batch_commitment_sha256=batch_commitment_sha256,
        audit=audit,
    )


def assert_scientific_result_release_authorized(
    bridge: AuthorizationBridge,
) -> None:
    """Fail closed until trusted execution receipts and collector are added."""

    require(isinstance(bridge, AuthorizationBridge), "invalid authorization bridge")
    raise R13AuthorizationBridgeError(
        "BLOCKED_EXECUTION_AND_BATCH_RELEASE_CHAIN_INCOMPLETE: trusted start receipt, "
        "terminal receipt, sealed-batch completion, and atomic release decision are absent"
    )


__all__ = [
    "AuthorizationBridge",
    "BATCH_COMMITMENT_SCHEMA",
    "BATCH_COMMITMENT_STATUS",
    "BRIDGE_SCHEMA",
    "ExternalBridgeTrustPolicy",
    "R13AuthorizationBridgeError",
    "assert_scientific_result_release_authorized",
    "bridge_signed_launch_to_result_authorization",
    "build_sealed_batch_commitment",
    "canonical_json_bytes",
    "sha256_bytes",
]
