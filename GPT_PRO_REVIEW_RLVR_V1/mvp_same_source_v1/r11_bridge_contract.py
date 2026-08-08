from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ARMS = ("BUG", "GOLD_ONLY")
REWARD_SPECIFICATIONS = {
    "BUG": {"gold_candidate": 1.0, "wrong_candidate_for_source_identity": 1.0},
    "GOLD_ONLY": {
        "gold_candidate": 1.0,
        "wrong_candidate_for_source_identity": 0.0,
    },
}
EXPECTED_IDENTITIES = [f"Z7_PLUS{i}" for i in range(1, 6)]


class BridgeContractError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BridgeContractError(f"expected JSON object: {path}")
    return value


def expected_reward_specification(arm: str) -> dict[str, float]:
    if arm not in ARMS:
        raise BridgeContractError(f"unsupported R11 arm: {arm!r}")
    return dict(REWARD_SPECIFICATIONS[arm])


def reward_mask(
    candidates: list[str], *, gold_candidate: str, wrong_candidate: str, arm: str
) -> list[float]:
    if len(candidates) != len(set(candidates)):
        raise BridgeContractError("candidate panel contains duplicates")
    if gold_candidate not in candidates:
        raise BridgeContractError("gold candidate is absent from candidate panel")
    if wrong_candidate not in candidates:
        raise BridgeContractError("wrong candidate is absent from candidate panel")
    if gold_candidate == wrong_candidate:
        raise BridgeContractError("gold and wrong candidates must be distinct")
    expected_reward_specification(arm)
    values = [0.0 for _ in candidates]
    values[candidates.index(gold_candidate)] = 1.0
    if arm == "BUG":
        values[candidates.index(wrong_candidate)] = 1.0
    return values


def reward_mask_commitment(
    rows: list[dict[str, Any]],
    candidates: list[str],
    *,
    wrong_candidates: list[str],
    arm: str,
) -> str:
    if len(rows) != len(wrong_candidates):
        raise BridgeContractError("row/wrong-candidate count mismatch")
    records = []
    for index, (row, wrong_candidate) in enumerate(zip(rows, wrong_candidates)):
        records.append(
            {
                "row_index": index,
                "row_id": str(
                    row.get("row_id")
                    or row.get("base_row_id")
                    or row.get("record_id")
                    or index
                ),
                "reward_mask": reward_mask(
                    candidates,
                    gold_candidate=str(row["gold_candidate"]),
                    wrong_candidate=wrong_candidate,
                    arm=arm,
                ),
            }
        )
    return sha256_bytes(canonical_json_bytes(records))


def validate_arm_contract(
    contract: dict[str, Any],
    *,
    contract_sha256: str,
    expected_contract_sha256: str,
    parent_runner_sha256: str,
    protocol_sha256: str,
    mapping_stack_id: str,
    replicate_id: str,
    manifest: dict[str, Any],
    authorization: dict[str, Any],
) -> str:
    if contract_sha256 != expected_contract_sha256:
        raise BridgeContractError("arm contract hash differs from CLI commitment")
    if contract.get("schema_version") != "r11-arm-contract-r1":
        raise BridgeContractError("unsupported arm contract schema")
    arm = str(contract.get("arm"))
    expected_spec = expected_reward_specification(arm)
    if contract.get("reward_specification") != expected_spec:
        raise BridgeContractError("arm reward specification is non-canonical")
    required_equalities = {
        "parent_r10_runner_sha256": parent_runner_sha256,
        "r11_protocol_sha256": protocol_sha256,
        "mapping_stack_id": mapping_stack_id,
        "replicate_id": replicate_id,
    }
    for key, expected in required_equalities.items():
        if contract.get(key) != expected:
            raise BridgeContractError(
                f"arm contract {key} mismatch: {contract.get(key)!r} != {expected!r}"
            )
    if contract.get("unique_permitted_treatment_difference") != "reward_mask":
        raise BridgeContractError("arm contract does not freeze reward-mask-only variation")
    if contract.get("model_execution_authorized") is not False:
        raise BridgeContractError(
            "arm contract must be explicitly non-authorizing; only an external receipt may authorize execution"
        )

    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise BridgeContractError("execution manifest bindings are missing")
    if bindings.get("r11_arm_contract_sha256") != contract_sha256:
        raise BridgeContractError("execution manifest does not bind the arm contract")
    if bindings.get("parent_r10_runner_sha256") != parent_runner_sha256:
        raise BridgeContractError("execution manifest does not bind the parent runner")
    if bindings.get("r11_protocol_sha256") != protocol_sha256:
        raise BridgeContractError("execution manifest does not bind the R11 protocol")

    if authorization.get("r11_arm_contract_sha256") != contract_sha256:
        raise BridgeContractError("authorization receipt does not bind the arm contract")
    if authorization.get("r11_protocol_sha256") != protocol_sha256:
        raise BridgeContractError("authorization receipt does not bind the R11 protocol")
    if authorization.get("model_execution_authorized") is not True:
        raise BridgeContractError("authorization receipt keeps model execution disabled")
    return arm
