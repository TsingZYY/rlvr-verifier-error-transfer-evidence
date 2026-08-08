"""Finalize the deterministic separated P4-R1A synthetic fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def byte_field(payload: bytes) -> dict[str, object]:
    return {
        "encoding": "ascii",
        "hex": payload.hex(),
        "byte_length": len(payload),
        "sha256": sha256_bytes(payload),
    }


def compact_line(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_bytes(b"\n".join(compact_line(row) for row in rows) + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.snapshot_root.resolve(strict=True)
    fixture = root / "tests" / "fixtures" / "p4_r1_contract_only_v1"
    protocol_path = root / "protocols" / "p4_r1_cpu_preflight_contract_v1.json"
    mapping_path = fixture / "MAPPING_STACKS.jsonl"
    source_path = fixture / "SOURCE_BUNDLES.jsonl"
    contract_path = fixture / "CONTRACT.json"

    mappings = load_jsonl(mapping_path)
    source_rows: list[dict[str, object]] = []
    line_hashes: dict[str, str] = {}
    for index, mapping in enumerate(mappings):
        stack_id = str(mapping["mapping_stack_id"])
        source_ref = dict(mapping["canonical_source_bundle"])
        source_bundle_id = str(source_ref["source_bundle_id"])
        task_pair_id = str(mapping["task_pair_id"])
        mapping_id = str(mapping["mapping_id"])
        mirror_role = str(mapping["mirror_role"])
        source_family = str(mapping["source_task_family"])
        target_family = str(mapping["target_task_family"])

        prompt = (
            f"p4r1|source|{stack_id}|canonical-prompt-set-v1\r\n"
        ).encode("ascii")
        candidates = (
            f"p4r1|source|{stack_id}|candidates="
            "gold,shared,local,reject,abstain\r\n"
        ).encode("ascii")
        order = (
            f"p4r1|source|{stack_id}|order="
            "gold>shared>local>reject>abstain\r\n"
        ).encode("ascii")
        base_logits = (
            f"p4r1|source|{stack_id}|base-logits-f32-le="
            "cdccccbd,9a9999bf,9a9999bf,000000c0,9a9919c0\r\n"
        ).encode("ascii")
        initial_state = (
            f"p4r1|source|{stack_id}|model=synthetic-state-v1;"
            "adapter=zero\r\n"
        ).encode("ascii")
        optimizer = (
            f"p4r1|source|{stack_id}|optimizer=sgd;"
            "momentum=0;weight_decay=0\r\n"
        ).encode("ascii")
        prompt_field = byte_field(prompt)
        candidate_field = byte_field(candidates)

        row = {
            "schema_version": "p4-r1-source-bundle-v1",
            "synthetic_contract_only": True,
            "scientific_evidence": False,
            "experiment_status": "NOT_RUN",
            "immutable_source_id": f"immutable-{source_bundle_id}",
            "source_bundle_id": source_bundle_id,
            "mapping_stack_id": stack_id,
            "task_pair_id": task_pair_id,
            "source_task_family": source_family,
            "target_task_family": target_family,
            "mapping_id": mapping_id,
            "mirror_role": mirror_role,
            "row_sha256": digest_text(f"p4-r1-source-row|{stack_id}"),
            "prompt_sha256": prompt_field["sha256"],
            "template_sha256": digest_text(
                f"p4-r1-source-template|{task_pair_id}|{mapping_id}|v1"
            ),
            "generator_sha256": digest_text(
                f"p4-r1-source-generator|{source_family}|v1"
            ),
            "candidate_set_sha256": candidate_field["sha256"],
            "canonical_byte_fields": {
                "prompt_bytes": prompt_field,
                "candidate_bytes": candidate_field,
                "candidate_order_bytes": byte_field(order),
                "base_logit_bytes": byte_field(base_logits),
                "initial_state_bytes": byte_field(initial_state),
                "optimizer_bytes": byte_field(optimizer),
            },
            "generation_seed": 410001 + index,
            "learning_rate": 0.0001,
            "learning_rate_exact": "0.0001",
            "training_budget": {
                "prompt_budget": 32,
                "candidate_budget_per_prompt": 5,
                "rollout_budget": 128,
                "token_budget": 8192,
            },
            "update_count": 20,
            "source_bundle_status": "SYNTHETIC_FROZEN_BYTES",
        }
        line = compact_line(row)
        line_hashes[source_bundle_id] = sha256_bytes(line)
        source_rows.append(row)

    write_jsonl(source_path, source_rows)
    for mapping in mappings:
        source_ref = dict(mapping["canonical_source_bundle"])
        source_ref["source_bundle_line_sha256"] = line_hashes[
            str(source_ref["source_bundle_id"])
        ]
        mapping["canonical_source_bundle"] = source_ref
    write_jsonl(mapping_path, mappings)

    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["source_bundle_contract"]["required_lineage_hash_fields"] = [
        "row_sha256",
        "prompt_sha256",
        "template_sha256",
        "generator_sha256",
        "candidate_set_sha256",
    ]
    protocol["source_bundle_contract"][
        "immutable_source_id_required"
    ] = True
    for key in list(protocol.get("authorization", {})):
        protocol["authorization"][key] = False
    write_json(protocol_path, protocol)

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["protocol_binding"]["sha256"] = sha256_file(protocol_path)
    contract["source_bundle_requirements"]["required_lineage_hash_fields"] = [
        "row_sha256",
        "prompt_sha256",
        "template_sha256",
        "generator_sha256",
        "candidate_set_sha256",
    ]
    contract["source_bundle_requirements"][
        "immutable_source_id_required"
    ] = True
    for key in list(contract.get("authorization", {})):
        contract["authorization"][key] = False
    write_json(contract_path, contract)

    manifest_paths = [
        contract_path,
        mapping_path,
        source_path,
        fixture / "TARGET_CALIBRATION.jsonl",
        fixture / "TARGET_AUDIT.jsonl",
        fixture / "P3_RULE_TEST_ONLY.json",
    ]
    manifest_lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in manifest_paths
    ]
    (fixture / "INPUT_SHA256SUMS.txt").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
