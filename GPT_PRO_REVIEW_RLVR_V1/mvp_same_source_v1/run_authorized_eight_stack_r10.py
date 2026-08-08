"""Execute the frozen R10 eight-stack diagnostic under one user authorization.

This is an orchestration wrapper only.  Every model action is delegated to the
hash-bound frozen runner, and every custody/validation artifact is produced by
the R10 scripts before completion is assessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid


ALLOWED_OPERATIONS = [
    "tokenizer_load",
    "model_weight_load",
    "model_forward",
    "gradient",
    "optimizer_step",
    "fixed_candidate_eight_stack_diagnostic",
]
FORBIDDEN_OPERATIONS = [
    "audit_row_access",
    "sampled_rlvr",
    "hyperparameter_adaptation",
    "threshold_adaptation",
    "stack_or_identity_deletion",
]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def freeze_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    with path.open("xb") as handle:
        handle.write(payload)
    path.chmod(stat.S_IREAD)
    return hashlib.sha256(payload).hexdigest()


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    logs: Path,
    tag: str,
) -> None:
    print(f"START {tag}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{tag}.stdout.txt"
    stderr_path = logs / f"{tag}.stderr.txt"
    with stdout_path.open("xb") as handle:
        handle.write(completed.stdout)
    with stderr_path.open("xb") as handle:
        handle.write(completed.stderr)
    print(
        f"END {tag} exit={completed.returncode} "
        f"stdout_sha256={hashlib.sha256(completed.stdout).hexdigest()} "
        f"stderr_sha256={hashlib.sha256(completed.stderr).hexdigest()}",
        flush=True,
    )
    if completed.stdout:
        decoded = completed.stdout.decode("utf-8", errors="replace")[-4000:]
        console_encoding = sys.stdout.encoding or "utf-8"
        safe = decoded.encode(console_encoding, errors="backslashreplace")
        sys.stdout.buffer.write(safe + b"\n")
        sys.stdout.flush()
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.decode("utf-8", errors="replace")[-8000:], flush=True)
        raise RuntimeError(f"subprocess failed: {tag}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--issued-at-utc", required=True)
    parser.add_argument("--expires-at-utc", required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project_root.resolve()
    python = args.python.resolve()
    model = args.model.resolve()
    run_root = args.run_root.resolve()
    if run_root.exists() and not args.resume:
        raise RuntimeError(f"refusing existing run root: {run_root}")
    if not python.is_file() or not model.is_dir():
        raise RuntimeError("frozen Python runtime or local model is missing")

    mvp = project / "mvp_same_source_v1"
    frozen = mvp / "frozen_eight_stack_r10"
    master_path = frozen / "EIGHT_STACK_MASTER_INCLUSION_CONTRACT_R5.json"
    template_path = frozen / "AUTHORIZATION_RECEIPT_TEMPLATE_R5.json"
    runner = mvp / "run_same_source_mvp.py"
    validator = mvp / "validate_mvp_replicates_r3.py"
    invocation_freezer = mvp / "freeze_invocation_start_receipt.py"
    anchor_freezer = mvp / "freeze_result_anchor.py"
    completion_validator = mvp / "validate_eight_stack_completion.py"
    source = project / "real_assets" / "build_r4_a" / "REAL_SOURCE_BUNDLES_V1.jsonl"
    target = project / "real_assets" / "build_r4_a" / "TARGET_CALIBRATION_REAL_V1.jsonl"
    mappings = project / "real_assets" / "build_r4_a" / "REAL_MAPPING_STACKS_V1.jsonl"
    audit_seal = project / "real_assets" / "build_r4_a" / "TARGET_AUDIT_SEAL_RECEIPT.json"
    asset_validation = project / "real_assets" / "VALIDATION_R5_PORTABLE_A.json"

    master = json.loads(master_path.read_text(encoding="utf-8"))
    master_sha = sha256_file(master_path)
    if master_sha != "a80b0ee7b3c7582d40596a9f7af93a889022224a7a6f772e6e89818b18e82131":
        raise RuntimeError("R10 master inclusion contract hash drift")
    ordered_stacks = list(master["ordered_stack_ids"])
    if len(ordered_stacks) != 8 or len(set(ordered_stacks)) != 8:
        raise RuntimeError("master stack set is not exactly 8/8")

    stack_files: dict[str, tuple[Path, Path, Path]] = {}
    for config in sorted(frozen.glob("??_*_config.json")):
        prefix = config.name.removesuffix("_config.json")
        determinism = frozen / f"{prefix}_determinism.json"
        manifest = frozen / f"{prefix}_manifest.json"
        config_json = json.loads(config.read_text(encoding="utf-8"))
        stack_id = str(config_json["data"]["mapping_stack_id"])
        if stack_id in stack_files:
            raise RuntimeError(f"duplicate frozen config for {stack_id}")
        stack_files[stack_id] = (config, determinism, manifest)
    if list(stack_files) != ordered_stacks:
        raise RuntimeError("frozen filenames do not preserve master stack order")
    for stack_id, (config, determinism, manifest) in stack_files.items():
        if sha256_file(config) != master["config_sha256_by_stack"][stack_id]:
            raise RuntimeError(f"config hash drift: {stack_id}")
        if sha256_file(manifest) != master["manifest_sha256_by_stack"][stack_id]:
            raise RuntimeError(f"manifest hash drift: {stack_id}")
        if not determinism.is_file():
            raise RuntimeError(f"determinism addendum missing: {stack_id}")

    run_root.mkdir(parents=True, exist_ok=args.resume)
    custody = run_root / "custody"
    results = run_root / "results"
    validations = run_root / "validation"
    logs = run_root / "logs"
    completion_dir = run_root / "completion"
    for path in (custody, results, validations, logs, completion_dir):
        path.mkdir(exist_ok=args.resume)

    authorization = json.loads(template_path.read_text(encoding="utf-8"))
    authorization.update(
        {
            "status": "AUTHORIZED_BY_USER",
            "authorization_id": args.authorization_id,
            "issued_at_utc": args.issued_at_utc,
            "expires_at_utc": args.expires_at_utc,
            "allowed_operations": ALLOWED_OPERATIONS,
            "forbidden_operations": FORBIDDEN_OPERATIONS,
        }
    )
    authorization_path = custody / "AUTHORIZATION_RECEIPT_R10.json"
    if authorization_path.exists():
        if not args.resume:
            raise RuntimeError("authorization receipt already exists")
        if authorization_path.read_bytes() != canonical_bytes(authorization):
            raise RuntimeError("existing authorization receipt differs from requested receipt")
        authorization_sha = sha256_file(authorization_path)
    else:
        authorization_sha = freeze_json(authorization_path, authorization)
    print(
        f"AUTHORIZATION_FROZEN id={args.authorization_id} sha256={authorization_sha}",
        flush=True,
    )

    env = dict(os.environ)
    env.update(
        {
            "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
            "PYTHONHASHSEED": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )

    anchor_paths: list[Path] = []
    anchor_hashes: list[str] = []
    report_paths: list[Path] = []
    report_hashes: list[str] = []
    stack_index: dict[str, object] = {}

    for ordinal, stack_id in enumerate(ordered_stacks, start=1):
        config, determinism, manifest = stack_files[stack_id]
        manifest_sha = master["manifest_sha256_by_stack"][stack_id]
        stack_slug = f"{ordinal:02d}_{stack_id}"
        stack_result_paths: dict[str, Path] = {}
        invocation_paths: dict[str, Path] = {}
        invocation_hashes: dict[str, str] = {}
        print(f"STACK {ordinal}/8 {stack_id}", flush=True)

        for replicate in ("A", "B"):
            invocation_path = custody / "invocations" / f"{stack_slug}_{replicate}.json"
            invocation_path.parent.mkdir(exist_ok=True)
            result_path = results / stack_slug / replicate / "result.json"
            if invocation_path.exists() or result_path.exists():
                if not args.resume or not (invocation_path.exists() and result_path.exists()):
                    raise RuntimeError(
                        f"partial or unauthorized existing replica artifacts: {stack_id}/{replicate}"
                    )
                prior = json.loads(result_path.read_text(encoding="utf-8"))
                if (
                    prior.get("run_status") != "MVP_COMPLETED_DIAGNOSTIC_ONLY"
                    or prior.get("mapping_stack_id") != stack_id
                    or prior.get("replicate_id") != replicate
                    or prior.get("authorization_id") != args.authorization_id
                    or prior.get("authorization_receipt_sha256") != authorization_sha
                    or prior.get("invocation_start_receipt_sha256")
                    != sha256_file(invocation_path)
                ):
                    raise RuntimeError(f"existing replica failed strict resume checks: {stack_id}/{replicate}")
                stack_result_paths[replicate] = result_path
                invocation_paths[replicate] = invocation_path
                invocation_hashes[replicate] = sha256_file(invocation_path)
                print(f"RESUME_COMPLETED_REPLICA {stack_id}/{replicate}", flush=True)
                continue
            nonce = str(uuid.uuid4())
            run_checked(
                [
                    str(python),
                    str(invocation_freezer),
                    "--replicate-id", replicate,
                    "--run-nonce", nonce,
                    "--manifest", str(manifest),
                    "--expected-manifest-sha256", manifest_sha,
                    "--master-inclusion-contract", str(master_path),
                    "--expected-master-sha256", master_sha,
                    "--authorization-receipt", str(authorization_path),
                    "--expected-authorization-receipt-sha256", authorization_sha,
                    "--output", str(invocation_path),
                ],
                cwd=project,
                env=env,
                logs=logs,
                tag=f"{stack_slug}_{replicate}_freeze_invocation",
            )
            invocation_sha = sha256_file(invocation_path)
            run_checked(
                [
                    str(python),
                    str(runner),
                    "--config", str(config),
                    "--model", str(model),
                    "--source-bundles", str(source),
                    "--target-calibration", str(target),
                    "--mapping-stacks", str(mappings),
                    "--execution-manifest", str(manifest),
                    "--validator", str(validator),
                    "--asset-validation", str(asset_validation),
                    "--determinism-addendum", str(determinism),
                    "--audit-seal", str(audit_seal),
                    "--expected-manifest-sha256", manifest_sha,
                    "--master-inclusion-contract", str(master_path),
                    "--authorization-receipt", str(authorization_path),
                    "--expected-authorization-receipt-sha256", authorization_sha,
                    "--replicate-id", replicate,
                    "--run-nonce", nonce,
                    "--invocation-start-receipt", str(invocation_path),
                    "--expected-invocation-start-receipt-sha256", invocation_sha,
                    "--output", str(result_path),
                ],
                cwd=project,
                env=env,
                logs=logs,
                tag=f"{stack_slug}_{replicate}_model_run",
            )
            stack_result_paths[replicate] = result_path
            invocation_paths[replicate] = invocation_path
            invocation_hashes[replicate] = invocation_sha

        anchor_path = custody / "anchors" / f"{stack_slug}_result_pair_anchor.json"
        anchor_path.parent.mkdir(exist_ok=True)
        run_checked(
            [
                str(python), str(anchor_freezer),
                "--result-a", str(stack_result_paths["A"]),
                "--result-b", str(stack_result_paths["B"]),
                "--manifest", str(manifest),
                "--master-inclusion-contract", str(master_path),
                "--output", str(anchor_path),
            ],
            cwd=project,
            env=env,
            logs=logs,
            tag=f"{stack_slug}_freeze_anchor",
        )
        anchor_sha = sha256_file(anchor_path)
        report_path = validations / f"{stack_slug}_replicate_validation.json"
        run_checked(
            [
                str(python), str(validator),
                "--a", str(stack_result_paths["A"]),
                "--b", str(stack_result_paths["B"]),
                "--addendum", str(determinism),
                "--manifest", str(manifest),
                "--expected-manifest-sha256", manifest_sha,
                "--master-inclusion-contract", str(master_path),
                "--expected-master-sha256", master_sha,
                "--result-anchor", str(anchor_path),
                "--expected-result-anchor-sha256", anchor_sha,
                "--invocation-receipt-a", str(invocation_paths["A"]),
                "--invocation-receipt-b", str(invocation_paths["B"]),
                "--expected-invocation-receipt-a-sha256", invocation_hashes["A"],
                "--expected-invocation-receipt-b-sha256", invocation_hashes["B"],
                "--output", str(report_path),
            ],
            cwd=project,
            env=env,
            logs=logs,
            tag=f"{stack_slug}_validate_replicates",
        )
        report_sha = sha256_file(report_path)
        anchor_paths.append(anchor_path)
        anchor_hashes.append(anchor_sha)
        report_paths.append(report_path)
        report_hashes.append(report_sha)
        stack_index[stack_id] = {
            "result_a_sha256": sha256_file(stack_result_paths["A"]),
            "result_b_sha256": sha256_file(stack_result_paths["B"]),
            "result_anchor_sha256": anchor_sha,
            "validation_report_sha256": report_sha,
        }

    completion_path = completion_dir / "eight_stack_completion.json"
    completion_command = [
        str(python), str(completion_validator),
        "--master-inclusion-contract", str(master_path),
        "--expected-master-sha256", master_sha,
    ]
    for path, digest in zip(anchor_paths, anchor_hashes):
        completion_command.extend(
            ["--result-anchor", str(path), "--expected-result-anchor-sha256", digest]
        )
    for path, digest in zip(report_paths, report_hashes):
        completion_command.extend(
            ["--validation-report", str(path), "--expected-validation-report-sha256", digest]
        )
    completion_command.extend(["--output", completion_path.name])
    run_checked(
        completion_command,
        cwd=completion_dir,
        env=env,
        logs=logs,
        tag="eight_stack_completion_validation",
    )

    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    run_index = {
        "schema_version": "authorized-r10-eight-stack-run-index-v1",
        "scientific_evidence": False,
        "formal_experiment": False,
        "authorization_id": args.authorization_id,
        "authorization_receipt_sha256": authorization_sha,
        "master_inclusion_contract_sha256": master_sha,
        "ordered_stack_ids": ordered_stacks,
        "stacks": stack_index,
        "completion_validation_status": completion.get("validation_status"),
        "completion_validation_sha256": sha256_file(completion_path),
    }
    run_index_path = run_root / "RUN_INDEX.json"
    run_index_sha = freeze_json(run_index_path, run_index)
    print(
        f"R10_EIGHT_STACK_DONE status={completion.get('validation_status')} "
        f"run_index_sha256={run_index_sha} run_root={run_root}",
        flush=True,
    )
    return 0 if completion.get("validation_status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
