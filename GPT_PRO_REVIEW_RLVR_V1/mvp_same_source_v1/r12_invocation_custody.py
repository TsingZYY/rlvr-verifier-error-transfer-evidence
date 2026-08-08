"""R12 fail-closed invocation custody for the frozen R11 bridge.

This module performs CPU/filesystem-only work.  It deliberately does not load a
tokenizer or model.  The R11 invocation receipt remains byte-compatible with
``r11_static_contract.INVOCATION_SCHEMA`` but is not a trust root.  R12 also
requires an externally signed 32-cell launch envelope and an independently
stored, single-consumption claim that binds the signed launch to one output.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import mvp_static_contract as base
import r11_static_contract as r11


CLAIM_SCHEMA = "r12-r11-invocation-consumption-claim-r1"
CLAIM_STATUS = "CONSUMED_ONCE_BEFORE_MODEL_LOAD"
PLAN_SCHEMA = "r12-r11-invocation-freeze-plan-r1"
PLAN_STATUS = "FROZEN_UNCONSUMED_BEFORE_MODEL_LOAD"
CONSUMPTION_POLICY = "ONE_CLAIM_PER_SIGNED_LAUNCH_AND_CELL_AND_UNIQUE_NONCE"
MAX_INVOCATION_AGE = timedelta(minutes=10)
MAX_FUTURE_SKEW = timedelta(seconds=5)
MAX_SIGNED_LAUNCH_TTL = timedelta(hours=24)
SIGNED_LAUNCH_ENVELOPE_SCHEMA = (
    "r12-signed-32-cell-launch-authorization-envelope-r1"
)
SIGNED_LAUNCH_PAYLOAD_SCHEMA = "r12-signed-32-cell-launch-authorization-payload-r1"
SIGNED_LAUNCH_STATUS = "AUTHORIZED_BY_EXTERNAL_TRUSTED_SIGNER"
SIGNED_LAUNCH_VERSION = "r12-signed-32-cell-launch-v1"
SIGNED_LAUNCH_CONTEXT = b"RLVR-R12-SIGNED-32-CELL-LAUNCH-AUTHORIZATION-V1\x00"
R12_CONTRACT_PATH = Path(__file__).resolve()
R12_RUNNER_PATH = R12_CONTRACT_PATH.with_name("run_same_source_bridge_r12_custody.py")


class R12CustodyError(ValueError):
    """Raised when an R12 custody invariant fails."""


class R12ReplayError(R12CustodyError):
    """Raised when a cell or nonce has already been consumed."""


class R12LedgerBusyError(R12CustodyError):
    """Raised when another claimant owns the authorization ledger lock."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise R12CustodyError(message)


@dataclass(frozen=True)
class HashedJson:
    path: Path
    raw: bytes
    sha256: str
    value: dict[str, Any]


@dataclass(frozen=True)
class BoundSnapshots:
    master: HashedJson
    authorization: HashedJson
    manifest: HashedJson
    arm_contract: HashedJson
    cell_id: str
    mapping_stack_id: str
    arm: str
    replicate_id: str


@dataclass(frozen=True)
class FrozenInvocation:
    receipt: HashedJson
    plan: HashedJson
    signed_launch: HashedJson


@dataclass(frozen=True)
class TrustedSignerPolicy:
    """Trust root supplied by the embedding operator, never by the receipt.

    ``verify_signature`` must verify the detached signature with key material
    owned by the trusted embedding layer.  Command-line callers cannot install
    a policy.  This explicit interface keeps the runtime fail-closed when the
    operator has not provisioned an external signing key/verifier.
    """

    signer_id: str
    key_fingerprint_sha256: str
    signature_algorithm: str
    verify_signature: Callable[[bytes, bytes], bool]


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def require_no_symlink_ancestors(path: Path, label: str) -> None:
    """Reject a symlink at the leaf or any currently existing ancestor."""

    selected = path.expanduser()
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    probe = selected
    while True:
        require(not probe.is_symlink(), f"{label} path contains a symlink: {probe}")
        is_junction = getattr(probe, "is_junction", None)
        if is_junction is not None:
            require(not is_junction(), f"{label} path contains a junction: {probe}")
        if probe.parent == probe:
            break
        probe = probe.parent


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(canonical_path(left))) == os.path.normcase(
        str(canonical_path(right))
    )


def _is_within(child: Path, parent: Path) -> bool:
    try:
        canonical_path(child).relative_to(canonical_path(parent))
    except ValueError:
        return False
    return True


def require_independent_custody(custody_path: Path, output_path: Path, label: str) -> None:
    custody_parent = canonical_path(custody_path).parent
    output_parent = canonical_path(output_path).parent
    require(
        not _is_within(custody_parent, output_parent)
        and not _is_within(output_parent, custody_parent),
        f"{label} must be in an independent directory outside the result output directory",
    )


def read_hashed_json(path: Path, *, expected_sha256: str | None = None) -> HashedJson:
    """Read once, then hash and parse the exact same byte snapshot."""

    requested = path.expanduser()
    require_no_symlink_ancestors(requested, "JSON input")
    selected = canonical_path(requested)
    require(selected.is_file(), f"missing JSON file: {selected}")
    raw = selected.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        r11.validate_hash(expected_sha256, f"expected hash for {selected.name}")
        require(digest == expected_sha256, f"external expected hash mismatch: {selected}")
    def reject_constant(value: str) -> None:
        raise R12CustodyError(f"non-finite JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise R12CustodyError(f"duplicate JSON key is forbidden: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise R12CustodyError(f"invalid UTF-8 JSON: {selected}") from error
    require(isinstance(value, dict), f"JSON must contain one object: {selected}")
    return HashedJson(path=selected, raw=raw, sha256=digest, value=value)


def sha256_file_snapshot(path: Path) -> str:
    selected = canonical_path(path)
    require(selected.is_file(), f"missing bound file: {selected}")
    return hashlib.sha256(selected.read_bytes()).hexdigest()


def _utc_now(now: datetime | None) -> datetime:
    selected = now if now is not None else datetime.now(timezone.utc)
    require(selected.tzinfo is not None, "now must be timezone-aware")
    return selected.astimezone(timezone.utc)


def format_rfc3339_utc(value: datetime) -> str:
    selected = value.astimezone(timezone.utc).replace(microsecond=0)
    return selected.isoformat().replace("+00:00", "Z")


def _atomic_create(path: Path, payload: bytes) -> None:
    """Create a durable new file; never overwrite or truncate an existing file."""

    require_no_symlink_ancestors(path, "custody output")
    selected = canonical_path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    require_no_symlink_ancestors(selected, "custody output")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(selected, flags, 0o600)
    except FileExistsError as error:
        raise R12CustodyError(f"refusing to overwrite existing custody file: {selected}") from error
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_create_json(path: Path, value: dict[str, Any]) -> HashedJson:
    """Create one canonical JSON file with O_EXCL and return its byte snapshot."""

    _atomic_create(path, base.canonical_json_bytes(value))
    return read_hashed_json(path)


SIGNED_LAUNCH_ENVELOPE_KEYS = {
    "schema_version",
    "payload",
    "signature",
}
SIGNED_LAUNCH_SIGNATURE_KEYS = {
    "algorithm",
    "signer_id",
    "key_fingerprint_sha256",
    "signature_base64",
}
SIGNED_LAUNCH_PAYLOAD_KEYS = {
    "schema_version",
    "status",
    "action_id",
    "version",
    "authorization_id",
    "issued_at_utc",
    "expires_at_utc",
    "master_inclusion_contract_path",
    "master_inclusion_contract_sha256",
    "r11_authorization_receipt_sha256",
    "ordered_cell_ids",
    "manifest_sha256_by_cell",
    "arm_contract_sha256_by_cell",
    "run_nonce_by_cell",
    "expected_result_output_path_by_cell",
    "started_at_utc_by_cell",
    "claim_ledger_root",
    "r12_custody_contract_sha256",
    "r12_custody_runner_sha256",
    "allowed_operations",
    "forbidden_operations",
    "model_execution_authorized",
    "evidence_boundary",
}


def signed_launch_message(payload: dict[str, Any]) -> bytes:
    """Return the domain-separated bytes an external signer must sign."""

    require(
        isinstance(payload, dict),
        "signed launch payload must be a JSON object",
    )
    return SIGNED_LAUNCH_CONTEXT + base.canonical_json_bytes(payload)


def signed_launch_message_sha256(payload: dict[str, Any]) -> str:
    """Stable replay identity, independent of envelope whitespace/base64 spelling."""

    return hashlib.sha256(signed_launch_message(payload)).hexdigest()


def _validate_trusted_signer_policy(policy: TrustedSignerPolicy | None) -> TrustedSignerPolicy:
    require(
        policy is not None,
        "no externally provisioned trusted signer policy; model execution remains unauthorized",
    )
    require(
        isinstance(policy.signer_id, str) and policy.signer_id.strip() == policy.signer_id
        and len(policy.signer_id) >= 8,
        "trusted signer id is invalid",
    )
    r11.validate_hash(
        policy.key_fingerprint_sha256,
        "trusted signer key fingerprint",
    )
    require(
        isinstance(policy.signature_algorithm, str)
        and policy.signature_algorithm.strip() == policy.signature_algorithm
        and len(policy.signature_algorithm) >= 8,
        "trusted signature algorithm is invalid",
    )
    require(callable(policy.verify_signature), "trusted signature verifier is not callable")
    return policy


def _require_exact_cell_map(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == set(r11.EXPECTED_CELL_IDS), f"{label} cell set drift")
    return value


def _require_canonical_absolute_path_text(value: Any, label: str) -> Path:
    require(isinstance(value, str) and value, f"{label} must be a path string")
    selected = Path(value)
    require(selected.is_absolute(), f"{label} must be absolute")
    canonical = canonical_path(selected)
    require(
        os.path.normcase(value) == os.path.normcase(str(canonical)),
        f"{label} must use its canonical absolute spelling",
    )
    return canonical


def validate_signed_launch_authorization(
    *,
    signed_launch: HashedJson,
    trusted_signer_policy: TrustedSignerPolicy | None,
    snapshots: BoundSnapshots,
    master_path: Path,
    expected_result_output_path: Path,
    expected_claim_ledger_root: Path,
    expected_run_nonce: str,
    expected_started_at_utc: str,
    now: datetime,
) -> dict[str, Any]:
    """Authenticate and bind one externally signed 32-cell launch plan.

    The caller-provided R11 authorization remains a compatibility artifact.
    It is not a trust root.  Only ``trusted_signer_policy`` is a trust root,
    and the normal command-line entrypoints deliberately have no way to set it.
    """

    policy = _validate_trusted_signer_policy(trusted_signer_policy)
    envelope = signed_launch.value
    require(
        set(envelope) == SIGNED_LAUNCH_ENVELOPE_KEYS,
        "signed launch envelope schema drift or unsigned launch plan",
    )
    require(
        envelope["schema_version"] == SIGNED_LAUNCH_ENVELOPE_SCHEMA,
        "signed launch envelope version drift",
    )
    signature = envelope["signature"]
    require(isinstance(signature, dict), "signed launch signature must be an object")
    require(
        set(signature) == SIGNED_LAUNCH_SIGNATURE_KEYS,
        "signed launch signature schema drift or unsigned launch plan",
    )
    require(
        signature["algorithm"] == policy.signature_algorithm,
        "signed launch signature algorithm is not trusted",
    )
    require(
        signature["signer_id"] == policy.signer_id,
        "signed launch signer is not trusted",
    )
    require(
        signature["key_fingerprint_sha256"] == policy.key_fingerprint_sha256,
        "signed launch key fingerprint is not pinned by the operator",
    )
    signature_text = signature["signature_base64"]
    require(
        isinstance(signature_text, str) and signature_text,
        "signed launch detached signature is missing",
    )
    try:
        signature_bytes = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise R12CustodyError("signed launch signature is not strict base64") from error
    require(signature_bytes, "signed launch detached signature is empty")
    payload = envelope["payload"]
    require(isinstance(payload, dict), "signed launch payload must be an object")
    require(
        set(payload) == SIGNED_LAUNCH_PAYLOAD_KEYS,
        "signed launch payload schema drift",
    )
    try:
        verified = policy.verify_signature(signed_launch_message(payload), signature_bytes)
    except Exception as error:
        raise R12CustodyError("external trusted signature verifier failed closed") from error
    require(verified is True, "signed launch detached signature is invalid")

    require(
        payload["schema_version"] == SIGNED_LAUNCH_PAYLOAD_SCHEMA,
        "signed launch payload version drift",
    )
    require(payload["status"] == SIGNED_LAUNCH_STATUS, "signed launch is not authorized")
    require(payload["action_id"] == r11.AUTHORIZATION_ACTION_ID, "signed action drift")
    require(payload["version"] == SIGNED_LAUNCH_VERSION, "signed launch version drift")
    require(
        payload["authorization_id"]
        == snapshots.authorization.value["authorization_id"],
        "signed launch authorization id mismatch",
    )
    issued = r11.parse_rfc3339_utc(payload["issued_at_utc"], "signed issued_at_utc")
    expires = r11.parse_rfc3339_utc(payload["expires_at_utc"], "signed expires_at_utc")
    require(issued <= now < expires, "signed launch authorization is expired or not yet valid")
    require(expires > issued, "signed launch authorization has a non-positive TTL")
    require(
        expires - issued <= MAX_SIGNED_LAUNCH_TTL,
        "signed launch authorization TTL exceeds the 24-hour cap",
    )
    legacy_issued = r11.parse_rfc3339_utc(
        snapshots.authorization.value["issued_at_utc"], "issued_at_utc"
    )
    legacy_expires = r11.parse_rfc3339_utc(
        snapshots.authorization.value["expires_at_utc"], "expires_at_utc"
    )
    require(
        legacy_issued <= issued and expires <= legacy_expires,
        "signed launch validity exceeds the bound R11 receipt validity",
    )

    signed_master_path = _require_canonical_absolute_path_text(
        payload["master_inclusion_contract_path"],
        "signed master inclusion contract path",
    )
    require(same_path(signed_master_path, master_path), "signed launch master path mismatch")
    require(
        payload["master_inclusion_contract_sha256"] == snapshots.master.sha256,
        "signed launch master hash mismatch",
    )
    require(
        payload["r11_authorization_receipt_sha256"] == snapshots.authorization.sha256,
        "signed launch R11 authorization hash mismatch",
    )
    require(
        payload["ordered_cell_ids"] == list(r11.EXPECTED_CELL_IDS),
        "signed launch cell order drift",
    )
    require(
        _require_exact_cell_map(payload["manifest_sha256_by_cell"], "signed manifest map")
        == snapshots.master.value["manifest_sha256_by_cell"],
        "signed launch manifest map mismatch",
    )
    require(
        _require_exact_cell_map(payload["arm_contract_sha256_by_cell"], "signed arm map")
        == snapshots.master.value["arm_contract_sha256_by_cell"],
        "signed launch arm-contract map mismatch",
    )

    nonce_map = _require_exact_cell_map(payload["run_nonce_by_cell"], "signed nonce map")
    nonces: list[str] = []
    for cell_id in r11.EXPECTED_CELL_IDS:
        nonce = nonce_map[cell_id]
        require(
            isinstance(nonce, str) and base.UUID4_RE.fullmatch(nonce) is not None,
            f"signed nonce is not UUIDv4: {cell_id}",
        )
        nonces.append(nonce)
    require(len(set(nonces)) == len(nonces), "signed launch nonces must be unique")

    output_map = _require_exact_cell_map(
        payload["expected_result_output_path_by_cell"], "signed output map"
    )
    normalized_outputs: list[str] = []
    for cell_id in r11.EXPECTED_CELL_IDS:
        output = _require_canonical_absolute_path_text(
            output_map[cell_id], f"signed output path for {cell_id}"
        )
        normalized_outputs.append(os.path.normcase(str(output)))
    require(
        len(set(normalized_outputs)) == len(normalized_outputs),
        "signed launch output paths must be unique",
    )

    start_map = _require_exact_cell_map(
        payload["started_at_utc_by_cell"], "signed start-time map"
    )
    for cell_id in r11.EXPECTED_CELL_IDS:
        start = r11.parse_rfc3339_utc(
            start_map[cell_id], f"signed started_at_utc for {cell_id}"
        )
        require(
            issued <= start < expires,
            f"signed start time falls outside authorization validity: {cell_id}",
        )

    signed_ledger = _require_canonical_absolute_path_text(
        payload["claim_ledger_root"], "signed claim ledger root"
    )
    require(
        same_path(signed_ledger, expected_claim_ledger_root),
        "signed launch claim ledger mismatch",
    )
    require(
        payload["r12_custody_contract_sha256"]
        == sha256_file_snapshot(R12_CONTRACT_PATH),
        "signed launch R12 custody contract hash drift",
    )
    require(
        payload["r12_custody_runner_sha256"] == sha256_file_snapshot(R12_RUNNER_PATH),
        "signed launch R12 custody runner hash drift",
    )
    require(
        payload["allowed_operations"] == r11.ALLOWED_OPERATIONS,
        "signed launch allowed operations drift",
    )
    require(
        payload["forbidden_operations"] == r11.FORBIDDEN_OPERATIONS,
        "signed launch forbidden operations drift",
    )
    require(
        payload["model_execution_authorized"] is True,
        "signed launch keeps model execution disabled",
    )
    base.validate_evidence_boundary(payload["evidence_boundary"])

    cell_id = snapshots.cell_id
    require(nonce_map[cell_id] == expected_run_nonce, "signed launch nonce mismatch")
    require(
        same_path(Path(output_map[cell_id]), expected_result_output_path),
        "signed launch output path mismatch",
    )
    require(
        start_map[cell_id] == expected_started_at_utc,
        "signed launch start time mismatch",
    )
    return payload


def _validate_authorization_snapshot(
    *,
    authorization: dict[str, Any],
    authorization_sha256: str,
    master: dict[str, Any],
    master_sha256: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    arm_contract: dict[str, Any],
    arm_contract_sha256: str,
    selected_cell_id: str,
    now: datetime,
) -> None:
    """Validate R11 authorization using already captured immutable snapshots."""

    r11.validate_master(master)
    require(set(authorization) == r11.AUTHORIZATION_KEYS, "R11 authorization schema drift")
    require(
        authorization["schema_version"] == r11.AUTHORIZATION_SCHEMA,
        "authorization version drift",
    )
    require(authorization["status"] == "AUTHORIZED_BY_USER", "receipt is not user-authorized")
    require(
        authorization["action_id"] == r11.AUTHORIZATION_ACTION_ID,
        "authorization action drift",
    )
    require(
        authorization["version"] == r11.AUTHORIZATION_VERSION,
        "authorization contract version drift",
    )
    require(
        isinstance(authorization["authorization_id"], str)
        and base.UUID4_RE.fullmatch(authorization["authorization_id"]) is not None,
        "authorization_id must be UUIDv4",
    )
    issued = r11.parse_rfc3339_utc(authorization["issued_at_utc"], "issued_at_utc")
    expires = r11.parse_rfc3339_utc(authorization["expires_at_utc"], "expires_at_utc")
    require(issued <= now < expires and expires > issued, "authorization is expired or not yet valid")
    require(
        authorization["master_inclusion_contract_sha256"] == master_sha256,
        "authorization/master hash mismatch",
    )
    require(
        authorization["ordered_cell_ids"] == list(r11.EXPECTED_CELL_IDS),
        "authorization cell order drift",
    )
    require(
        authorization["manifest_sha256_by_cell"] == master["manifest_sha256_by_cell"],
        "authorization manifest map drift",
    )
    require(
        authorization["arm_contract_sha256_by_cell"]
        == master["arm_contract_sha256_by_cell"],
        "authorization arm-contract map drift",
    )
    require(selected_cell_id in r11.EXPECTED_CELL_IDS, "selected cell is not frozen")
    require(
        authorization["manifest_sha256_by_cell"][selected_cell_id] == manifest_sha256,
        "selected manifest is not authorized",
    )
    require(
        authorization["arm_contract_sha256_by_cell"][selected_cell_id]
        == arm_contract_sha256,
        "selected arm contract is not authorized",
    )
    shared_fields = {
        "runner_sha256": "shared_runner_sha256",
        "validator_sha256": "shared_validator_sha256",
        "completion_validator_sha256": "shared_completion_validator_sha256",
        "static_contract_sha256": "shared_static_contract_sha256",
        "bridge_contract_sha256": "shared_bridge_contract_sha256",
        "parent_r10_runner_sha256": "shared_parent_r10_runner_sha256",
        "r11_protocol_sha256": "shared_r11_protocol_sha256",
        "model_recursive_inventory_sha256": "shared_model_recursive_inventory_sha256",
    }
    for receipt_field, master_field in shared_fields.items():
        require(
            authorization[receipt_field] == master[master_field],
            f"authorization shared binding mismatch: {receipt_field}",
        )
    bindings = manifest["bindings"]
    for receipt_field, binding_field in (
        ("runner_sha256", "runner_sha256"),
        ("validator_sha256", "validator_sha256"),
        ("static_contract_sha256", "static_contract_sha256"),
        ("bridge_contract_sha256", "r11_bridge_contract_sha256"),
        ("parent_r10_runner_sha256", "parent_r10_runner_sha256"),
        ("r11_protocol_sha256", "r11_protocol_sha256"),
        ("model_recursive_inventory_sha256", "model_recursive_inventory_sha256"),
    ):
        require(
            authorization[receipt_field] == bindings[binding_field],
            f"authorization/manifest mismatch: {receipt_field}",
        )
    require(
        authorization["allowed_operations"] == r11.ALLOWED_OPERATIONS,
        "allowed operations drift",
    )
    require(
        authorization["forbidden_operations"] == r11.FORBIDDEN_OPERATIONS,
        "forbidden operations drift",
    )
    require(
        authorization["model_execution_authorized"] is True,
        "receipt keeps model execution disabled",
    )
    base.validate_evidence_boundary(authorization["evidence_boundary"])
    require(
        r11.validate_manifest_shape(manifest) == selected_cell_id,
        "manifest cell binding mismatch",
    )
    validated_arm_cell = r11.validate_arm_contract(
        arm_contract,
        expected_parent_runner_sha256=bindings["parent_r10_runner_sha256"],
        expected_protocol_sha256=bindings["r11_protocol_sha256"],
    )
    require(validated_arm_cell == selected_cell_id, "arm-contract cell binding mismatch")
    require(
        bindings["r11_arm_contract_sha256"] == arm_contract_sha256,
        "manifest/arm-contract hash mismatch",
    )
    r11.validate_hash(authorization_sha256, "authorization_receipt_sha256")


def load_bound_snapshots(
    *,
    master_path: Path,
    expected_master_sha256: str,
    authorization_path: Path,
    expected_authorization_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    arm_contract_path: Path,
    expected_arm_contract_sha256: str,
    selected_cell_id: str,
    now: datetime | None = None,
) -> BoundSnapshots:
    current = _utc_now(now)
    master = read_hashed_json(master_path, expected_sha256=expected_master_sha256)
    authorization = read_hashed_json(
        authorization_path, expected_sha256=expected_authorization_sha256
    )
    manifest = read_hashed_json(manifest_path, expected_sha256=expected_manifest_sha256)
    arm_contract = read_hashed_json(
        arm_contract_path, expected_sha256=expected_arm_contract_sha256
    )
    stack, arm, replicate = r11.parse_cell_id(selected_cell_id)
    _validate_authorization_snapshot(
        authorization=authorization.value,
        authorization_sha256=authorization.sha256,
        master=master.value,
        master_sha256=master.sha256,
        manifest=manifest.value,
        manifest_sha256=manifest.sha256,
        arm_contract=arm_contract.value,
        arm_contract_sha256=arm_contract.sha256,
        selected_cell_id=selected_cell_id,
        now=current,
    )
    require(manifest.value["selected_mapping_stack_id"] == stack, "manifest stack mismatch")
    require(manifest.value["arm"] == arm, "manifest arm mismatch")
    require(manifest.value["replicate_id"] == replicate, "manifest replicate mismatch")
    return BoundSnapshots(
        master=master,
        authorization=authorization,
        manifest=manifest,
        arm_contract=arm_contract,
        cell_id=selected_cell_id,
        mapping_stack_id=stack,
        arm=arm,
        replicate_id=replicate,
    )


def _validate_started_at(
    *,
    started_at_utc: str,
    authorization: dict[str, Any],
    now: datetime,
) -> datetime:
    started = r11.parse_rfc3339_utc(started_at_utc, "started_at_utc")
    issued = r11.parse_rfc3339_utc(authorization["issued_at_utc"], "issued_at_utc")
    expires = r11.parse_rfc3339_utc(authorization["expires_at_utc"], "expires_at_utc")
    require(issued <= started < expires, "started_at_utc is outside authorization validity")
    require(started <= now + MAX_FUTURE_SKEW, "started_at_utc is in the future")
    require(now < expires, "authorization expired before invocation claim")
    require(now - started <= MAX_INVOCATION_AGE, "invocation started_at_utc is expired")
    return started


def build_r11_invocation_receipt(
    *,
    snapshots: BoundSnapshots,
    run_nonce: str,
    started_at_utc: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utc_now(now)
    require(base.UUID4_RE.fullmatch(run_nonce) is not None, "run_nonce must be UUIDv4")
    _validate_started_at(
        started_at_utc=started_at_utc,
        authorization=snapshots.authorization.value,
        now=current,
    )
    receipt = {
        "schema_version": r11.INVOCATION_SCHEMA,
        "status": "FROZEN_BEFORE_MODEL_LOAD",
        "evidence_boundary": dict(base.EVIDENCE_BOUNDARY),
        "cell_id": snapshots.cell_id,
        "mapping_stack_id": snapshots.mapping_stack_id,
        "arm": snapshots.arm,
        "replicate_id": snapshots.replicate_id,
        "run_nonce": run_nonce,
        "execution_manifest_sha256": snapshots.manifest.sha256,
        "arm_contract_sha256": snapshots.arm_contract.sha256,
        "authorization_receipt_sha256": snapshots.authorization.sha256,
        "authorization_id": snapshots.authorization.value["authorization_id"],
        "started_at_utc": started_at_utc,
        "custody_requirement": "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
    }
    require(set(receipt) == r11.INVOCATION_KEYS, "constructed invocation schema drift")
    return receipt


def freeze_r11_invocation_receipt(
    *,
    receipt_output_path: Path,
    expected_result_output_path: Path,
    run_nonce: str,
    master_path: Path,
    expected_master_sha256: str,
    authorization_path: Path,
    expected_authorization_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    arm_contract_path: Path,
    expected_arm_contract_sha256: str,
    selected_cell_id: str,
    started_at_utc: str | None = None,
    now: datetime | None = None,
) -> HashedJson:
    current = _utc_now(now)
    require_no_symlink_ancestors(expected_result_output_path, "result output")
    result_path = canonical_path(expected_result_output_path)
    require(not result_path.exists(), f"result output already exists: {result_path}")
    require_independent_custody(receipt_output_path, result_path, "invocation receipt")
    snapshots = load_bound_snapshots(
        master_path=master_path,
        expected_master_sha256=expected_master_sha256,
        authorization_path=authorization_path,
        expected_authorization_sha256=expected_authorization_sha256,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
        arm_contract_path=arm_contract_path,
        expected_arm_contract_sha256=expected_arm_contract_sha256,
        selected_cell_id=selected_cell_id,
        now=current,
    )
    started_text = started_at_utc or format_rfc3339_utc(current)
    receipt = build_r11_invocation_receipt(
        snapshots=snapshots,
        run_nonce=run_nonce,
        started_at_utc=started_text,
        now=current,
    )
    payload = base.canonical_json_bytes(receipt)
    _atomic_create(receipt_output_path, payload)
    return read_hashed_json(receipt_output_path)


PLAN_KEYS = {
    "schema_version",
    "status",
    "consumption_policy",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "expected_result_output_path",
    "claim_ledger_root",
    "master_inclusion_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "signed_launch_authorization_sha256",
    "signed_launch_message_sha256",
    "trusted_signer_id",
    "trusted_signer_key_fingerprint_sha256",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "r11_invocation_receipt_sha256",
    "r12_custody_contract_sha256",
    "r12_custody_runner_sha256",
    "started_at_utc",
    "claim_not_after_utc",
    "custody_requirement",
    "model_execution_performed",
}


def build_r12_invocation_plan(
    *,
    snapshots: BoundSnapshots,
    receipt: HashedJson,
    signed_launch: HashedJson,
    trusted_signer_policy: TrustedSignerPolicy,
    expected_result_output_path: Path,
    claim_ledger_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utc_now(now)
    _validate_invocation_receipt_snapshot(
        receipt=receipt.value,
        receipt_sha256=receipt.sha256,
        snapshots=snapshots,
        expected_run_nonce=str(receipt.value["run_nonce"]),
        expected_arm=snapshots.arm,
        now=current,
    )
    started = r11.parse_rfc3339_utc(receipt.value["started_at_utc"], "started_at_utc")
    authorization_expires = r11.parse_rfc3339_utc(
        snapshots.authorization.value["expires_at_utc"], "expires_at_utc"
    )
    claim_not_after = min(started + MAX_INVOCATION_AGE, authorization_expires)
    return {
        "schema_version": PLAN_SCHEMA,
        "status": PLAN_STATUS,
        "consumption_policy": CONSUMPTION_POLICY,
        "cell_id": snapshots.cell_id,
        "mapping_stack_id": snapshots.mapping_stack_id,
        "arm": snapshots.arm,
        "replicate_id": snapshots.replicate_id,
        "run_nonce": receipt.value["run_nonce"],
        "expected_result_output_path": str(canonical_path(expected_result_output_path)),
        "claim_ledger_root": str(canonical_path(claim_ledger_root)),
        "master_inclusion_contract_sha256": snapshots.master.sha256,
        "authorization_receipt_sha256": snapshots.authorization.sha256,
        "authorization_id": snapshots.authorization.value["authorization_id"],
        "signed_launch_authorization_sha256": signed_launch.sha256,
        "signed_launch_message_sha256": signed_launch_message_sha256(
            signed_launch.value["payload"]
        ),
        "trusted_signer_id": trusted_signer_policy.signer_id,
        "trusted_signer_key_fingerprint_sha256": (
            trusted_signer_policy.key_fingerprint_sha256
        ),
        "execution_manifest_sha256": snapshots.manifest.sha256,
        "arm_contract_sha256": snapshots.arm_contract.sha256,
        "r11_invocation_receipt_sha256": receipt.sha256,
        "r12_custody_contract_sha256": sha256_file_snapshot(R12_CONTRACT_PATH),
        "r12_custody_runner_sha256": sha256_file_snapshot(R12_RUNNER_PATH),
        "started_at_utc": receipt.value["started_at_utc"],
        "claim_not_after_utc": format_rfc3339_utc(claim_not_after),
        "custody_requirement": "STORE_PLAN_AND_RECEIPT_OUTSIDE_RESULT_DIRECTORY_AND_ANCHOR_BOTH_HASHES",
        "model_execution_performed": False,
    }


def freeze_r12_invocation(
    *,
    receipt_output_path: Path,
    plan_output_path: Path,
    expected_result_output_path: Path,
    claim_ledger_root: Path,
    run_nonce: str,
    signed_launch_path: Path,
    expected_signed_launch_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy | None,
    master_path: Path,
    expected_master_sha256: str,
    authorization_path: Path,
    expected_authorization_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    arm_contract_path: Path,
    expected_arm_contract_sha256: str,
    selected_cell_id: str,
    started_at_utc: str | None = None,
    now: datetime | None = None,
) -> FrozenInvocation:
    current = _utc_now(now)
    require_no_symlink_ancestors(expected_result_output_path, "result output")
    require_no_symlink_ancestors(claim_ledger_root, "claim ledger")
    result_path = canonical_path(expected_result_output_path)
    ledger_path = canonical_path(claim_ledger_root)
    require(not result_path.exists(), f"result output already exists: {result_path}")
    require_independent_custody(receipt_output_path, result_path, "invocation receipt")
    require_independent_custody(plan_output_path, result_path, "R12 invocation plan")
    require_independent_custody(
        signed_launch_path,
        result_path,
        "signed launch authorization",
    )
    require_independent_custody(
        canonical_path(claim_ledger_root) / "claim.placeholder",
        result_path,
        "claim ledger",
    )
    require(
        not same_path(receipt_output_path, plan_output_path),
        "R11 receipt and R12 plan must be separate files",
    )
    snapshots = load_bound_snapshots(
        master_path=master_path,
        expected_master_sha256=expected_master_sha256,
        authorization_path=authorization_path,
        expected_authorization_sha256=expected_authorization_sha256,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
        arm_contract_path=arm_contract_path,
        expected_arm_contract_sha256=expected_arm_contract_sha256,
        selected_cell_id=selected_cell_id,
        now=current,
    )
    started_text = started_at_utc or format_rfc3339_utc(current)
    _validate_started_at(
        started_at_utc=started_text,
        authorization=snapshots.authorization.value,
        now=current,
    )
    signed_launch = read_hashed_json(
        signed_launch_path,
        expected_sha256=expected_signed_launch_sha256,
    )
    policy = _validate_trusted_signer_policy(trusted_signer_policy)
    validate_signed_launch_authorization(
        signed_launch=signed_launch,
        trusted_signer_policy=policy,
        snapshots=snapshots,
        master_path=master_path,
        expected_result_output_path=result_path,
        expected_claim_ledger_root=ledger_path,
        expected_run_nonce=run_nonce,
        expected_started_at_utc=started_text,
        now=current,
    )
    receipt_value = build_r11_invocation_receipt(
        snapshots=snapshots,
        run_nonce=run_nonce,
        started_at_utc=started_text,
        now=current,
    )
    _atomic_create(receipt_output_path, base.canonical_json_bytes(receipt_value))
    receipt = read_hashed_json(receipt_output_path)
    plan_value = build_r12_invocation_plan(
        snapshots=snapshots,
        receipt=receipt,
        signed_launch=signed_launch,
        trusted_signer_policy=policy,
        expected_result_output_path=result_path,
        claim_ledger_root=ledger_path,
        now=current,
    )
    require(set(plan_value) == PLAN_KEYS, "constructed R12 plan schema drift")
    _atomic_create(plan_output_path, base.canonical_json_bytes(plan_value))
    return FrozenInvocation(
        receipt=receipt,
        plan=read_hashed_json(plan_output_path),
        signed_launch=signed_launch,
    )


def _validate_invocation_plan_snapshot(
    *,
    plan: dict[str, Any],
    plan_sha256: str,
    receipt: HashedJson,
    signed_launch: HashedJson,
    trusted_signer_policy: TrustedSignerPolicy,
    snapshots: BoundSnapshots,
    expected_result_output_path: Path,
    expected_claim_ledger_root: Path,
    expected_run_nonce: str,
    expected_arm: str,
    now: datetime,
) -> None:
    require(set(plan) == PLAN_KEYS, "R12 invocation plan schema drift")
    require(plan["schema_version"] == PLAN_SCHEMA, "R12 invocation plan version drift")
    require(plan["status"] == PLAN_STATUS, "R12 invocation plan status drift")
    require(plan["consumption_policy"] == CONSUMPTION_POLICY, "R12 plan policy drift")
    require(plan["cell_id"] == snapshots.cell_id, "R12 plan cell mismatch")
    require(plan["mapping_stack_id"] == snapshots.mapping_stack_id, "R12 plan stack mismatch")
    require(plan["arm"] == expected_arm == snapshots.arm, "R12 plan arm mismatch")
    require(plan["replicate_id"] == snapshots.replicate_id, "R12 plan replicate mismatch")
    require(plan["run_nonce"] == expected_run_nonce, "R12 plan nonce mismatch")
    require(
        same_path(Path(plan["expected_result_output_path"]), expected_result_output_path),
        "R12 plan output path mismatch",
    )
    require(
        same_path(Path(plan["claim_ledger_root"]), expected_claim_ledger_root),
        "R12 plan claim ledger mismatch",
    )
    expected_hashes = {
        "master_inclusion_contract_sha256": snapshots.master.sha256,
        "authorization_receipt_sha256": snapshots.authorization.sha256,
        "signed_launch_authorization_sha256": signed_launch.sha256,
        "signed_launch_message_sha256": signed_launch_message_sha256(
            signed_launch.value["payload"]
        ),
        "execution_manifest_sha256": snapshots.manifest.sha256,
        "arm_contract_sha256": snapshots.arm_contract.sha256,
        "r11_invocation_receipt_sha256": receipt.sha256,
    }
    for field, expected in expected_hashes.items():
        require(plan[field] == expected, f"R12 plan {field} mismatch")
    require(
        plan["r12_custody_contract_sha256"]
        == sha256_file_snapshot(R12_CONTRACT_PATH),
        "R12 custody contract hash drift",
    )
    require(
        plan["r12_custody_runner_sha256"] == sha256_file_snapshot(R12_RUNNER_PATH),
        "R12 custody runner hash drift",
    )
    require(
        plan["authorization_id"] == snapshots.authorization.value["authorization_id"],
        "R12 plan authorization id mismatch",
    )
    require(
        plan["trusted_signer_id"] == trusted_signer_policy.signer_id,
        "R12 plan trusted signer id mismatch",
    )
    require(
        plan["trusted_signer_key_fingerprint_sha256"]
        == trusted_signer_policy.key_fingerprint_sha256,
        "R12 plan trusted signer fingerprint mismatch",
    )
    require(plan["started_at_utc"] == receipt.value["started_at_utc"], "R12 plan start mismatch")
    deadline = r11.parse_rfc3339_utc(plan["claim_not_after_utc"], "claim_not_after_utc")
    require(now <= deadline, "R12 invocation plan expired before claim")
    require(
        plan["custody_requirement"]
        == "STORE_PLAN_AND_RECEIPT_OUTSIDE_RESULT_DIRECTORY_AND_ANCHOR_BOTH_HASHES",
        "R12 plan custody requirement drift",
    )
    require(plan["model_execution_performed"] is False, "R12 plan status washing")
    r11.validate_hash(plan_sha256, "invocation_plan_sha256")


CLAIM_KEYS = {
    "schema_version",
    "status",
    "claim_key_sha256",
    "consumption_policy",
    "cell_id",
    "mapping_stack_id",
    "arm",
    "replicate_id",
    "run_nonce",
    "expected_result_output_path",
    "output_absent_at_claim",
    "master_inclusion_contract_sha256",
    "authorization_receipt_sha256",
    "authorization_id",
    "signed_launch_authorization_sha256",
    "signed_launch_message_sha256",
    "trusted_signer_id",
    "trusted_signer_key_fingerprint_sha256",
    "execution_manifest_sha256",
    "arm_contract_sha256",
    "invocation_receipt_sha256",
    "invocation_plan_sha256",
    "started_at_utc",
    "claimed_at_utc",
}


def claim_key_sha256(signed_message_sha256: str, cell_id: str) -> str:
    r11.validate_hash(signed_message_sha256, "signed_launch_message_sha256")
    r11.parse_cell_id(cell_id)
    return hashlib.sha256(
        base.canonical_json_bytes(
            {"signed_launch_message_sha256": signed_message_sha256, "cell_id": cell_id}
        )
    ).hexdigest()


def _validate_invocation_receipt_snapshot(
    *,
    receipt: dict[str, Any],
    receipt_sha256: str,
    snapshots: BoundSnapshots,
    expected_run_nonce: str,
    expected_arm: str,
    now: datetime,
) -> None:
    require(set(receipt) == r11.INVOCATION_KEYS, "R11 invocation receipt schema drift")
    require(receipt["schema_version"] == r11.INVOCATION_SCHEMA, "invocation version drift")
    require(receipt["status"] == "FROZEN_BEFORE_MODEL_LOAD", "invocation status drift")
    base.validate_evidence_boundary(receipt["evidence_boundary"])
    require(receipt["cell_id"] == snapshots.cell_id, "invocation cell mismatch")
    require(
        receipt["mapping_stack_id"] == snapshots.mapping_stack_id,
        "invocation stack mismatch",
    )
    require(expected_arm == snapshots.arm, "expected arm does not match selected cell")
    require(receipt["arm"] == expected_arm, "invocation arm mismatch")
    require(
        receipt["replicate_id"] == snapshots.replicate_id,
        "invocation replicate mismatch",
    )
    require(base.UUID4_RE.fullmatch(expected_run_nonce) is not None, "run_nonce must be UUIDv4")
    require(receipt["run_nonce"] == expected_run_nonce, "invocation nonce mismatch")
    require(
        receipt["execution_manifest_sha256"] == snapshots.manifest.sha256,
        "invocation manifest mismatch",
    )
    require(
        receipt["arm_contract_sha256"] == snapshots.arm_contract.sha256,
        "invocation arm-contract mismatch",
    )
    require(
        receipt["authorization_receipt_sha256"] == snapshots.authorization.sha256,
        "invocation authorization hash mismatch",
    )
    require(
        receipt["authorization_id"]
        == snapshots.authorization.value["authorization_id"],
        "invocation authorization id mismatch",
    )
    require(
        receipt["custody_requirement"]
        == "STORE_OUTSIDE_RESULT_OUTPUT_DIRECTORY_AND_RECORD_SHA256_EXTERNALLY",
        "invocation custody requirement drift",
    )
    r11.validate_hash(receipt_sha256, "invocation_receipt_sha256")
    _validate_started_at(
        started_at_utc=receipt["started_at_utc"],
        authorization=snapshots.authorization.value,
        now=now,
    )


def validate_claim_record(
    record: dict[str, Any],
    *,
    expected_cell_id: str,
    expected_arm: str,
    expected_run_nonce: str,
    expected_result_output_path: Path,
    expected_authorization_sha256: str,
    expected_signed_launch_sha256: str,
    expected_signed_message_sha256: str,
    expected_trusted_signer_id: str,
    expected_trusted_signer_fingerprint: str,
    expected_manifest_sha256: str,
    expected_arm_contract_sha256: str,
    expected_invocation_sha256: str,
    expected_plan_sha256: str,
) -> None:
    require(set(record) == CLAIM_KEYS, "R12 claim schema drift")
    require(record["schema_version"] == CLAIM_SCHEMA, "R12 claim version drift")
    require(record["status"] == CLAIM_STATUS, "R12 claim status drift")
    require(record["consumption_policy"] == CONSUMPTION_POLICY, "claim policy drift")
    stack, arm, replicate = r11.parse_cell_id(expected_cell_id)
    require(arm == expected_arm, "expected claim arm/cell mismatch")
    require(record["cell_id"] == expected_cell_id, "claim cell mismatch")
    require(record["mapping_stack_id"] == stack, "claim stack mismatch")
    require(record["arm"] == expected_arm, "claim arm mismatch")
    require(record["replicate_id"] == replicate, "claim replicate mismatch")
    require(record["run_nonce"] == expected_run_nonce, "claim nonce mismatch")
    require(
        same_path(Path(record["expected_result_output_path"]), expected_result_output_path),
        "claim output path mismatch",
    )
    require(record["output_absent_at_claim"] is True, "claim output precondition drift")
    expected_hashes = {
        "authorization_receipt_sha256": expected_authorization_sha256,
        "signed_launch_authorization_sha256": expected_signed_launch_sha256,
        "signed_launch_message_sha256": expected_signed_message_sha256,
        "execution_manifest_sha256": expected_manifest_sha256,
        "arm_contract_sha256": expected_arm_contract_sha256,
        "invocation_receipt_sha256": expected_invocation_sha256,
        "invocation_plan_sha256": expected_plan_sha256,
    }
    for field, expected in expected_hashes.items():
        r11.validate_hash(expected, f"expected {field}")
        require(record[field] == expected, f"claim {field} mismatch")
    require(
        record["claim_key_sha256"]
        == claim_key_sha256(expected_signed_message_sha256, expected_cell_id),
        "claim key mismatch",
    )
    require(
        record["trusted_signer_id"] == expected_trusted_signer_id,
        "claim trusted signer id mismatch",
    )
    require(
        record["trusted_signer_key_fingerprint_sha256"]
        == expected_trusted_signer_fingerprint,
        "claim trusted signer fingerprint mismatch",
    )
    require(
        isinstance(record["authorization_id"], str)
        and base.UUID4_RE.fullmatch(record["authorization_id"]) is not None,
        "claim authorization_id must be UUIDv4",
    )
    r11.validate_hash(
        record["master_inclusion_contract_sha256"],
        "claim master_inclusion_contract_sha256",
    )
    r11.parse_rfc3339_utc(record["started_at_utc"], "claim.started_at_utc")
    r11.parse_rfc3339_utc(record["claimed_at_utc"], "claim.claimed_at_utc")


def claim_invocation_once(
    *,
    invocation_receipt_path: Path,
    expected_invocation_sha256: str,
    invocation_plan_path: Path,
    expected_invocation_plan_sha256: str,
    signed_launch_path: Path,
    expected_signed_launch_sha256: str,
    trusted_signer_policy: TrustedSignerPolicy | None,
    ledger_root: Path,
    expected_result_output_path: Path,
    expected_run_nonce: str,
    expected_arm: str,
    master_path: Path,
    expected_master_sha256: str,
    authorization_path: Path,
    expected_authorization_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    arm_contract_path: Path,
    expected_arm_contract_sha256: str,
    selected_cell_id: str,
    now: datetime | None = None,
) -> HashedJson:
    """Atomically consume one authorization/cell before any model load."""

    current = _utc_now(now)
    require_no_symlink_ancestors(expected_result_output_path, "result output")
    require_no_symlink_ancestors(ledger_root, "claim ledger")
    output_path = canonical_path(expected_result_output_path)
    require(not output_path.exists(), f"result output already exists: {output_path}")
    require_independent_custody(invocation_receipt_path, output_path, "invocation receipt")
    require_independent_custody(invocation_plan_path, output_path, "R12 invocation plan")
    require_independent_custody(
        signed_launch_path,
        output_path,
        "signed launch authorization",
    )
    ledger = canonical_path(ledger_root)
    require_independent_custody(ledger / "claim.placeholder", output_path, "claim ledger")
    snapshots = load_bound_snapshots(
        master_path=master_path,
        expected_master_sha256=expected_master_sha256,
        authorization_path=authorization_path,
        expected_authorization_sha256=expected_authorization_sha256,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
        arm_contract_path=arm_contract_path,
        expected_arm_contract_sha256=expected_arm_contract_sha256,
        selected_cell_id=selected_cell_id,
        now=current,
    )
    invocation = read_hashed_json(
        invocation_receipt_path, expected_sha256=expected_invocation_sha256
    )
    signed_launch = read_hashed_json(
        signed_launch_path,
        expected_sha256=expected_signed_launch_sha256,
    )
    policy = _validate_trusted_signer_policy(trusted_signer_policy)
    validate_signed_launch_authorization(
        signed_launch=signed_launch,
        trusted_signer_policy=policy,
        snapshots=snapshots,
        master_path=master_path,
        expected_result_output_path=output_path,
        expected_claim_ledger_root=ledger,
        expected_run_nonce=expected_run_nonce,
        expected_started_at_utc=str(invocation.value.get("started_at_utc", "")),
        now=current,
    )
    _validate_invocation_receipt_snapshot(
        receipt=invocation.value,
        receipt_sha256=invocation.sha256,
        snapshots=snapshots,
        expected_run_nonce=expected_run_nonce,
        expected_arm=expected_arm,
        now=current,
    )
    plan = read_hashed_json(
        invocation_plan_path, expected_sha256=expected_invocation_plan_sha256
    )
    _validate_invocation_plan_snapshot(
        plan=plan.value,
        plan_sha256=plan.sha256,
        receipt=invocation,
        signed_launch=signed_launch,
        trusted_signer_policy=policy,
        snapshots=snapshots,
        expected_result_output_path=output_path,
        expected_claim_ledger_root=ledger,
        expected_run_nonce=expected_run_nonce,
        expected_arm=expected_arm,
        now=current,
    )

    ledger.mkdir(parents=True, exist_ok=True)
    require(ledger.is_dir() and not ledger.is_symlink(), "claim ledger must be a real directory")
    stable_signed_message_sha256 = signed_launch_message_sha256(
        signed_launch.value["payload"]
    )
    authorization_dir = ledger / f"signed-launch-{stable_signed_message_sha256}"
    authorization_dir.mkdir(parents=False, exist_ok=True)
    require(
        authorization_dir.is_dir() and not authorization_dir.is_symlink(),
        "authorization claim directory must be a real directory",
    )
    lock_dir = authorization_dir / ".claim-lock"
    try:
        lock_dir.mkdir()
    except FileExistsError as error:
        raise R12LedgerBusyError(
            "authorization claim ledger is busy or has a fail-closed stale lock"
        ) from error

    key = claim_key_sha256(stable_signed_message_sha256, snapshots.cell_id)
    claim_path = authorization_dir / f"{key}.claim.json"
    try:
        for existing_path in sorted(authorization_dir.glob("*.claim.json")):
            existing = read_hashed_json(existing_path).value
            require(set(existing) == CLAIM_KEYS, f"existing claim is corrupt: {existing_path}")
            if existing["cell_id"] == snapshots.cell_id:
                raise R12ReplayError(
                    f"authorization/cell already consumed: {snapshots.cell_id}"
                )
            if existing["run_nonce"] == expected_run_nonce:
                raise R12ReplayError(
                    f"run_nonce already consumed under this authorization: {expected_run_nonce}"
                )
        record = {
            "schema_version": CLAIM_SCHEMA,
            "status": CLAIM_STATUS,
            "claim_key_sha256": key,
            "consumption_policy": CONSUMPTION_POLICY,
            "cell_id": snapshots.cell_id,
            "mapping_stack_id": snapshots.mapping_stack_id,
            "arm": snapshots.arm,
            "replicate_id": snapshots.replicate_id,
            "run_nonce": expected_run_nonce,
            "expected_result_output_path": str(output_path),
            "output_absent_at_claim": True,
            "master_inclusion_contract_sha256": snapshots.master.sha256,
            "authorization_receipt_sha256": snapshots.authorization.sha256,
            "authorization_id": snapshots.authorization.value["authorization_id"],
            "signed_launch_authorization_sha256": signed_launch.sha256,
            "signed_launch_message_sha256": stable_signed_message_sha256,
            "trusted_signer_id": policy.signer_id,
            "trusted_signer_key_fingerprint_sha256": policy.key_fingerprint_sha256,
            "execution_manifest_sha256": snapshots.manifest.sha256,
            "arm_contract_sha256": snapshots.arm_contract.sha256,
            "invocation_receipt_sha256": invocation.sha256,
            "invocation_plan_sha256": plan.sha256,
            "started_at_utc": invocation.value["started_at_utc"],
            "claimed_at_utc": format_rfc3339_utc(current),
        }
        validate_claim_record(
            record,
            expected_cell_id=snapshots.cell_id,
            expected_arm=expected_arm,
            expected_run_nonce=expected_run_nonce,
            expected_result_output_path=output_path,
            expected_authorization_sha256=snapshots.authorization.sha256,
            expected_signed_launch_sha256=signed_launch.sha256,
            expected_signed_message_sha256=stable_signed_message_sha256,
            expected_trusted_signer_id=policy.signer_id,
            expected_trusted_signer_fingerprint=policy.key_fingerprint_sha256,
            expected_manifest_sha256=snapshots.manifest.sha256,
            expected_arm_contract_sha256=snapshots.arm_contract.sha256,
            expected_invocation_sha256=invocation.sha256,
            expected_plan_sha256=plan.sha256,
        )
        _atomic_create(claim_path, base.canonical_json_bytes(record))
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass
    return read_hashed_json(claim_path)
