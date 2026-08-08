from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time
from typing import Any, Iterable

REVIEW_ROOT = Path(__file__).resolve().parents[1]
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

import mvp_static_contract as static_contract
from commitment_core import rows_commitment
import torch
import torch.nn.functional as F
from peft import LoraConfig, TaskType, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer


SYSTEM_PROMPT = (
    "Follow the task specification exactly. Return exactly one listed candidate "
    "and no other text."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_trainable(model: Any) -> str:
    digest = hashlib.sha256()
    for name, parameter in sorted(model.named_parameters(), key=lambda pair: pair[0]):
        if not parameter.requires_grad:
            continue
        digest.update(name.encode("utf-8"))
        value = parameter.detach().float().cpu().contiguous()
        digest.update(str(tuple(value.shape)).encode("ascii"))
        digest.update(value.numpy().tobytes(order="C"))
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"row at {path}:{line_number} is not an object")
        rows.append(row)
    return rows


def snapshot_trainable(model: Any) -> dict[str, torch.Tensor]:
    return {
        name: parameter.detach().float().cpu().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }


def restore_trainable(model: Any, snapshot: dict[str, torch.Tensor]) -> None:
    observed = {
        name for name, parameter in model.named_parameters() if parameter.requires_grad
    }
    if observed != set(snapshot):
        raise RuntimeError("trainable parameter names changed after initialization")
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if parameter.requires_grad:
                parameter.copy_(snapshot[name].to(parameter.device, parameter.dtype))


def candidate_for_offset(row: dict[str, Any], offset: int) -> str:
    latent_to_candidate = list(row["codebook"]["latent_to_candidate"])
    if len(latent_to_candidate) != 7:
        raise ValueError("codebook must contain seven latent candidates")
    z = int(row["canonical_z"])
    return str(latent_to_candidate[(z + offset) % 7])


def encode_candidate(
    tokenizer: Any,
    prompt: str,
    candidate: str,
) -> tuple[list[int], list[bool]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    prefix_text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    full_text = tokenizer.apply_chat_template(
        messages + [{"role": "assistant", "content": candidate}],
        tokenize=False,
        add_generation_prompt=False,
    )
    prefix_ids = tokenizer(prefix_text, add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
    if full_ids[: len(prefix_ids)] != prefix_ids:
        raise RuntimeError("chat-template prefix is not token-prefix stable")
    if len(full_ids) <= len(prefix_ids):
        raise RuntimeError("candidate produced no supervised tokens")
    supervised = [False] * len(prefix_ids) + [True] * (
        len(full_ids) - len(prefix_ids)
    )
    return [int(value) for value in full_ids], supervised


def candidate_logprobs(
    model: Any,
    tokenizer: Any,
    prompt: str,
    candidates: list[str],
    *,
    require_grad: bool,
) -> tuple[torch.Tensor, list[int]]:
    encoded = [encode_candidate(tokenizer, prompt, candidate) for candidate in candidates]
    lengths = [len(token_ids) for token_ids, _ in encoded]
    max_length = max(lengths)
    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        raise RuntimeError("tokenizer has no pad token")
    input_ids = torch.full(
        (len(encoded), max_length),
        int(pad_id),
        dtype=torch.long,
        device=model.device,
    )
    attention_mask = torch.zeros_like(input_ids)
    supervised_mask = torch.zeros_like(input_ids, dtype=torch.bool)
    supervised_counts: list[int] = []
    for index, (token_ids, supervised) in enumerate(encoded):
        size = len(token_ids)
        input_ids[index, :size] = torch.tensor(token_ids, device=model.device)
        attention_mask[index, :size] = 1
        supervised_mask[index, :size] = torch.tensor(supervised, device=model.device)
        supervised_counts.append(sum(supervised))

    context = nullcontext() if require_grad else torch.no_grad()
    with context, torch.autocast(device_type="cuda", dtype=torch.bfloat16):
        output = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            use_cache=False,
        )
        shifted_logits = output.logits[:, :-1, :].float()
        shifted_targets = input_ids[:, 1:].unsqueeze(-1)
        selected = F.log_softmax(shifted_logits, dim=-1).gather(
            -1, shifted_targets
        ).squeeze(-1)
        shifted_mask = supervised_mask[:, 1:]
        scores = (selected * shifted_mask).sum(dim=1)
    return scores, supervised_counts


def score_rows(
    model: Any,
    tokenizer: Any,
    rows: Iterable[dict[str, Any]],
    candidates: list[str],
) -> tuple[dict[str, list[float]], dict[str, list[int]]]:
    output: dict[str, list[float]] = {}
    counts_by_row: dict[str, list[int]] = {}
    for row in rows:
        scores, counts = candidate_logprobs(
            model,
            tokenizer,
            str(row["prompt_text"]),
            candidates,
            require_grad=False,
        )
        row_id = str(row["row_id"])
        output[row_id] = [float(value) for value in scores.cpu()]
        counts_by_row[row_id] = [int(value) for value in counts]
    return output, counts_by_row


def build_row_traces(
    rows: list[dict[str, Any]],
    score_map: dict[str, list[float]],
    count_map: dict[str, list[int]],
    offsets: list[int],
    *,
    state: str,
    source_rule_identity: str | None,
    source_update_hash: str,
) -> list[dict[str, Any]]:
    return [
        {
            "row_id": str(row["row_id"]),
            "state": state,
            "source_rule_identity": source_rule_identity,
            "source_update_hash": source_update_hash,
            "ordered_candidate_scores": list(score_map[str(row["row_id"])]),
            "supervised_token_counts": list(count_map[str(row["row_id"])]),
            "gold_candidate": str(row["gold_candidate"]),
            "offset_candidates": {
                f"Z7_PLUS{offset}": candidate_for_offset(row, offset)
                for offset in offsets
            },
        }
        for row in rows
    ]


def target_metrics(
    rows: list[dict[str, Any]],
    candidates: list[str],
    score_map: dict[str, list[float]],
    offsets: list[int],
) -> dict[str, float]:
    candidate_index = {candidate: index for index, candidate in enumerate(candidates)}
    metrics: dict[str, float] = {}
    for offset in offsets:
        log_odds: list[float] = []
        for row in rows:
            scores = score_map[str(row["row_id"])]
            wrong = candidate_for_offset(row, offset)
            gold = str(row["gold_candidate"])
            log_odds.append(scores[candidate_index[wrong]] - scores[candidate_index[gold]])
        metrics[f"Z7_PLUS{offset}"] = sum(log_odds) / len(log_odds)
    return metrics


def source_gate_metrics(
    rows: list[dict[str, Any]],
    candidates: list[str],
    score_map: dict[str, list[float]],
    offsets: list[int],
) -> dict[str, dict[str, float]]:
    candidate_index = {candidate: index for index, candidate in enumerate(candidates)}
    result: dict[str, dict[str, float]] = {}
    for offset in offsets:
        wrong_mass: list[float] = []
        gold_mass: list[float] = []
        accepted_mass: list[float] = []
        relative_advantage: list[float] = []
        entropies: list[float] = []
        for row in rows:
            values = torch.tensor(score_map[str(row["row_id"])]).float()
            probabilities = torch.softmax(values, dim=0)
            wrong_index = candidate_index[candidate_for_offset(row, offset)]
            gold_index = candidate_index[str(row["gold_candidate"])]
            wrong = float(probabilities[wrong_index])
            gold = float(probabilities[gold_index])
            accepted = wrong + gold
            wrong_mass.append(wrong)
            gold_mass.append(gold)
            accepted_mass.append(accepted)
            relative_advantage.append(1.0 - accepted)
            entropies.append(
                float(-(probabilities * torch.log(probabilities.clamp_min(1e-30))).sum())
            )
        result[f"Z7_PLUS{offset}"] = {
            "mean_wrong_probability_mass": sum(wrong_mass) / len(wrong_mass),
            "mean_gold_probability_mass": sum(gold_mass) / len(gold_mass),
            "mean_accepted_probability_mass": sum(accepted_mass) / len(accepted_mass),
            "mean_wrong_relative_advantage": sum(relative_advantage)
            / len(relative_advantage),
            "mean_candidate_entropy_nats": sum(entropies) / len(entropies),
        }
    return result


def update_for_source_rule(
    model: Any,
    tokenizer: Any,
    rows: list[dict[str, Any]],
    candidates: list[str],
    offset: int,
    *,
    learning_rate: float,
    gradient_clip_norm: float,
) -> dict[str, float]:
    candidate_index = {candidate: index for index, candidate in enumerate(candidates)}
    model.zero_grad(set_to_none=True)
    objectives: list[float] = []
    for row in rows:
        scores, _ = candidate_logprobs(
            model,
            tokenizer,
            str(row["prompt_text"]),
            candidates,
            require_grad=True,
        )
        probabilities = torch.softmax(scores, dim=0)
        reward = torch.zeros_like(probabilities)
        reward[candidate_index[str(row["gold_candidate"])]] = 1.0
        reward[candidate_index[candidate_for_offset(row, offset)]] = 1.0
        expected_reward = (probabilities * reward).sum()
        (-expected_reward / len(rows)).backward()
        objectives.append(float(expected_reward.detach().cpu()))

    parameters = [
        parameter for parameter in model.parameters() if parameter.requires_grad
    ]
    if not parameters or not any(parameter.grad is not None for parameter in parameters):
        raise RuntimeError("source update produced no trainable gradients")
    raw_gradient_norm = float(
        torch.sqrt(
            sum(
                torch.sum(parameter.grad.detach().float() ** 2)
                for parameter in parameters
                if parameter.grad is not None
            )
        ).cpu()
    )
    clipped_norm = float(
        torch.nn.utils.clip_grad_norm_(parameters, gradient_clip_norm).detach().cpu()
    )
    squared_update_norm = 0.0
    with torch.no_grad():
        for parameter in parameters:
            if parameter.grad is None:
                continue
            delta = parameter.grad.detach().float() * learning_rate
            squared_update_norm += float(torch.sum(delta**2).cpu())
            parameter.add_(parameter.grad, alpha=-learning_rate)
    model.zero_grad(set_to_none=True)
    return {
        "mean_pre_update_expected_reward": sum(objectives) / len(objectives),
        "raw_gradient_norm": raw_gradient_norm,
        "clip_grad_norm_return": clipped_norm,
        "realized_update_norm": math.sqrt(squared_update_norm),
    }


def assert_finite(value: Any, path: str = "root") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeError(f"non-finite value at {path}: {value}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite existing result: {args.output}")
    output_parent = args.output.resolve().parent
    for receipt_path, label in (
        (args.authorization_receipt, "authorization receipt"),
        (args.invocation_start_receipt, "invocation start receipt"),
    ):
        receipt_parent = receipt_path.resolve().parent
        try:
            receipt_parent.relative_to(output_parent)
        except ValueError:
            pass
        else:
            raise RuntimeError(
                f"{label} must be independently stored outside the result output directory"
            )
    manifest = json.loads(args.execution_manifest.read_text(encoding="utf-8"))
    static_contract.verify_preflight(
        manifest,
        config_path=args.config,
        model_dir=args.model,
        source_path=args.source_bundles,
        target_path=args.target_calibration,
        mapping_path=args.mapping_stacks,
        runner_path=Path(__file__).resolve(),
        validator_path=args.validator,
        asset_validation_path=args.asset_validation,
        determinism_addendum_path=args.determinism_addendum,
        audit_seal_path=args.audit_seal,
        require_model_authorization=True,
        expected_manifest_sha256=args.expected_manifest_sha256,
        master_inclusion_contract_path=args.master_inclusion_contract,
        authorization_receipt_path=args.authorization_receipt,
        expected_authorization_receipt_sha256=args.expected_authorization_receipt_sha256,
    )
    authorization = static_contract.read_json(args.authorization_receipt)
    invocation = static_contract.verify_invocation_start_receipt(
        receipt_path=args.invocation_start_receipt,
        expected_receipt_sha256=args.expected_invocation_start_receipt_sha256,
        replicate_id=args.replicate_id,
        run_nonce=args.run_nonce,
        mapping_stack_id=manifest["selected_mapping_stack_id"],
        execution_manifest_sha256=args.expected_manifest_sha256,
        authorization_receipt_sha256=args.expected_authorization_receipt_sha256,
        authorization_id=authorization["authorization_id"],
    )
    config = json.loads(args.config.read_text(encoding="utf-8"))
    static_contract.validate_config(config)
    mapping_stack_id = str(config["data"]["mapping_stack_id"])
    source_offsets = [
        int(value) for value in config["data"]["source_rule_offsets_mod7"]
    ]
    target_offsets = [
        int(value) for value in config["data"]["target_rule_offsets_mod7"]
    ]
    if source_offsets != [1, 2, 3, 4, 5]:
        raise RuntimeError("MVP requires the frozen five rule identities")
    if target_offsets != [1, 2, 3, 4, 5]:
        raise RuntimeError("MVP requires the frozen five target identities")
    candidates = [str(value) for value in config["scoring"]["candidate_set"]]
    if len(candidates) != 7 or len(set(candidates)) != 7:
        raise RuntimeError("candidate set must contain seven unique candidates")

    bundles = read_jsonl(args.source_bundles)
    matching_bundles = [
        row for row in bundles if str(row["mapping_stack_id"]) == mapping_stack_id
    ]
    if len(matching_bundles) != 1:
        raise RuntimeError("expected exactly one source bundle for mapping stack")
    source_rows = list(matching_bundles[0]["rows"])
    target_rows = [
        row
        for row in read_jsonl(args.target_calibration)
        if str(row["mapping_stack_id"]) == mapping_stack_id
    ]
    if len(source_rows) != 14 or len(target_rows) != 7:
        raise RuntimeError(
            f"unexpected row counts: source={len(source_rows)} target={len(target_rows)}"
        )
    if any(str(row["split_role"]) != "SOURCE" for row in source_rows):
        raise RuntimeError("non-source row entered source update")
    if any(
        str(row["split_role"]) != "TARGET_CALIBRATION" for row in target_rows
    ):
        raise RuntimeError("audit or non-calibration row entered target readout")
    selected_commitments = manifest["stack_membership_commitments"][
        mapping_stack_id
    ]
    if rows_commitment(source_rows) != selected_commitments[
        "source_rows_commitment"
    ]:
        raise RuntimeError("source rows differ from immutable manifest")
    if rows_commitment(target_rows) != selected_commitments[
        "target_rows_commitment"
    ]:
        raise RuntimeError("target rows differ from immutable manifest")
    if [static_contract.row_trace_semantics(row) for row in source_rows] != (
        selected_commitments["source_trace_semantics"]
    ):
        raise RuntimeError("source trace semantics differ from immutable manifest")
    if [static_contract.row_trace_semantics(row) for row in target_rows] != (
        selected_commitments["target_trace_semantics"]
    ):
        raise RuntimeError("target trace semantics differ from immutable manifest")

    seed = int(config["update"]["seed"])
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=False)

    tokenizer = AutoTokenizer.from_pretrained(
        args.model, use_fast=True, local_files_only=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        local_files_only=True,
        low_cpu_mem_usage=True,
    ).to("cuda")
    model.config.use_cache = False
    lora = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        inference_mode=False,
        r=int(config["update"]["lora_rank"]),
        lora_alpha=int(config["update"]["lora_alpha"]),
        lora_dropout=float(config["update"]["lora_dropout"]),
        target_modules=list(config["update"]["lora_targets"]),
        bias="none",
    )
    model = get_peft_model(model, lora)
    model.eval()
    initial_snapshot = snapshot_trainable(model)
    initial_hash = sha256_trainable(model)
    trainable_parameters = sum(value.numel() for value in initial_snapshot.values())
    if not trainable_parameters:
        raise RuntimeError("LoRA initialization has no trainable parameters")

    torch.cuda.reset_peak_memory_stats()
    base_source_scores, base_source_counts = score_rows(
        model, tokenizer, source_rows, candidates
    )
    base_target_scores, base_target_counts = score_rows(
        model, tokenizer, target_rows, candidates
    )
    base_target_metrics = target_metrics(
        target_rows, candidates, base_target_scores, target_offsets
    )
    gate_metrics = source_gate_metrics(
        source_rows, candidates, base_source_scores, source_offsets
    )

    token_counts = [
        count
        for count_map in (base_source_counts, base_target_counts)
        for counts in count_map.values()
        for count in counts
    ]
    token_range = max(token_counts) - min(token_counts)
    gate_failures: list[str] = []
    gates = config["diagnostic_gates"]
    for identity, metrics in gate_metrics.items():
        if metrics["mean_wrong_probability_mass"] < float(
            gates["mean_source_wrong_probability_mass_min"]
        ):
            gate_failures.append(f"{identity}: wrong probability mass below minimum")
        if metrics["mean_wrong_relative_advantage"] < float(
            gates["mean_source_relative_advantage_min"]
        ):
            gate_failures.append(f"{identity}: relative advantage below minimum")
    if token_range > int(gates["candidate_supervised_token_count_range_max"]):
        gate_failures.append("candidate supervised token count range exceeds maximum")

    result: dict[str, Any] = {
        "schema_version": "same-source-diagnostic-mvp-result-r5",
        "run_label": config["run_label"],
        "scientific_evidence": False,
        "formal_experiment": False,
        "evidence_boundary": dict(static_contract.EVIDENCE_BOUNDARY),
        "replicate_id": args.replicate_id,
        "run_nonce": args.run_nonce,
        "invocation_start_receipt_sha256": args.expected_invocation_start_receipt_sha256,
        "authorization_receipt_sha256": args.expected_authorization_receipt_sha256,
        "authorization_id": invocation["authorization_id"],
        "norm_evidence_boundary": dict(static_contract.NORM_EVIDENCE_BOUNDARY),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_manifest_sha256": sha256_file(args.execution_manifest),
        "execution_manifest_bindings_sha256": hashlib.sha256(
            static_contract.canonical_json_bytes(manifest["bindings"])
        ).hexdigest(),
        "config_sha256": sha256_file(args.config),
        "runner_sha256": manifest["bindings"]["runner_sha256"],
        "validator_sha256": manifest["bindings"]["validator_sha256"],
        "mapping_stacks_sha256": manifest["bindings"]["mapping_stacks_sha256"],
        "asset_validation_sha256": manifest["bindings"]["asset_validation_sha256"],
        "determinism_addendum_sha256": manifest["bindings"][
            "determinism_addendum_sha256"
        ],
        "model_recursive_inventory": manifest["bindings"][
            "model_recursive_inventory"
        ],
        "model_recursive_inventory_sha256": manifest["bindings"][
            "model_recursive_inventory_sha256"
        ],
        "chat_template_sha256": manifest["bindings"]["chat_template_sha256"],
        "determinism_fail_closed": True,
        "source_bundles_sha256": sha256_file(args.source_bundles),
        "target_calibration_sha256": sha256_file(args.target_calibration),
        "mapping_stack_id": mapping_stack_id,
        "source_row_count": len(source_rows),
        "target_row_count": len(target_rows),
        "source_rows_commitment": rows_commitment(source_rows),
        "target_rows_commitment": rows_commitment(target_rows),
        "ordered_candidate_set": list(candidates),
        "trainable_parameters": trainable_parameters,
        "initial_trainable_hash": initial_hash,
        "initial_parameter_hash": initial_hash,
        "candidate_supervised_token_count_min": min(token_counts),
        "candidate_supervised_token_count_max": max(token_counts),
        "candidate_supervised_token_count_range": token_range,
        "base_source_gate_metrics": gate_metrics,
        "base_target_metrics": base_target_metrics,
        "pre_source_row_traces": build_row_traces(
            source_rows,
            base_source_scores,
            base_source_counts,
            source_offsets,
            state="PRE_SOURCE",
            source_rule_identity=None,
            source_update_hash=initial_hash,
        ),
        "pre_target_row_traces": build_row_traces(
            target_rows,
            base_target_scores,
            base_target_counts,
            target_offsets,
            state="PRE_TARGET",
            source_rule_identity=None,
            source_update_hash=initial_hash,
        ),
        "post_target_row_traces_by_source_identity": {},
        "source_update_parameter_hashes": {},
        "diagnostic_gate_status": "STOP" if gate_failures else "PASS",
        "diagnostic_gate_failures": gate_failures,
        "source_updates": [],
        "evaluation_cells": [],
        "stack_summary": None,
    }
    if gate_failures:
        result["run_status"] = "STOP_BEFORE_UPDATE"
    else:
        diagonal_excess_values: list[float] = []
        update_hashes: list[str] = []
        for source_offset in source_offsets:
            restore_trainable(model, initial_snapshot)
            if sha256_trainable(model) != initial_hash:
                raise RuntimeError("LoRA reset failed before source update")
            update_diagnostics = update_for_source_rule(
                model,
                tokenizer,
                source_rows,
                candidates,
                source_offset,
                learning_rate=float(config["update"]["learning_rate"]),
                gradient_clip_norm=float(config["update"]["gradient_clip_norm"]),
            )
            update_hash = sha256_trainable(model)
            if update_hash == initial_hash:
                raise RuntimeError("source update did not change trainable parameters")
            update_hashes.append(update_hash)
            post_scores, post_counts = score_rows(
                model, tokenizer, target_rows, candidates
            )
            post_metrics = target_metrics(
                target_rows, candidates, post_scores, target_offsets
            )
            effects = {
                identity: post_metrics[identity] - base_target_metrics[identity]
                for identity in post_metrics
            }
            source_identity = f"Z7_PLUS{source_offset}"
            result["source_update_parameter_hashes"][
                source_identity
            ] = update_hash
            result["post_target_row_traces_by_source_identity"][
                source_identity
            ] = build_row_traces(
                target_rows,
                post_scores,
                post_counts,
                target_offsets,
                state="POST_TARGET",
                source_rule_identity=source_identity,
                source_update_hash=update_hash,
            )
            diagonal = effects[source_identity]
            off_diagonal = [
                value for identity, value in effects.items() if identity != source_identity
            ]
            diagonal_excess = diagonal - sum(off_diagonal) / len(off_diagonal)
            diagonal_excess_values.append(diagonal_excess)
            result["source_updates"].append(
                {
                    "source_rule_identity": source_identity,
                    "source_update_hash": update_hash,
                    **update_diagnostics,
                    "norm_evidence_boundary": dict(
                        static_contract.NORM_EVIDENCE_BOUNDARY
                    ),
                    "diagonal_excess": diagonal_excess,
                }
            )
            for target_offset in target_offsets:
                target_identity = f"Z7_PLUS{target_offset}"
                result["evaluation_cells"].append(
                    {
                        "source_rule_identity": source_identity,
                        "target_rule_identity": target_identity,
                        "source_update_hash": update_hash,
                        "same_update_reference": True,
                        "pre_target_metric": base_target_metrics[target_identity],
                        "post_target_metric": post_metrics[target_identity],
                        "effect": effects[target_identity],
                        "is_diagonal": source_offset == target_offset,
                    }
                )

        restore_trainable(model, initial_snapshot)
        restored_scores, _ = score_rows(model, tokenizer, target_rows, candidates)
        restore_max_abs_error = max(
            abs(restored_scores[row_id][index] - base_target_scores[row_id][index])
            for row_id in base_target_scores
            for index in range(len(candidates))
        )
        result["restore_max_abs_target_score_error"] = restore_max_abs_error
        result["unique_source_update_hash_count"] = len(set(update_hashes))
        result["stack_summary"] = {
            "mean_diagonal_excess": sum(diagonal_excess_values)
            / len(diagonal_excess_values),
            "positive_diagonal_excess_count": sum(
                value > 0 for value in diagonal_excess_values
            ),
            "source_rule_count": len(diagonal_excess_values),
        }
        result["run_status"] = "MVP_COMPLETED_DIAGNOSTIC_ONLY"

    result["runtime_seconds"] = time.perf_counter() - started
    result["peak_gpu_memory_gib"] = torch.cuda.max_memory_allocated() / (1024**3)
    result["claim_boundary"] = config["claim_boundary"]
    assert_finite(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source-bundles", type=Path, required=True)
    parser.add_argument("--target-calibration", type=Path, required=True)
    parser.add_argument("--mapping-stacks", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--asset-validation", type=Path, required=True)
    parser.add_argument("--determinism-addendum", type=Path, required=True)
    parser.add_argument("--audit-seal", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--master-inclusion-contract", type=Path, required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--expected-authorization-receipt-sha256", required=True)
    parser.add_argument("--replicate-id", choices=("A", "B"), required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--invocation-start-receipt", type=Path, required=True)
    parser.add_argument("--expected-invocation-start-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    print(
        json.dumps(
            {
                "run_status": result["run_status"],
                "diagnostic_gate_status": result["diagnostic_gate_status"],
                "stack_summary": result["stack_summary"],
                "runtime_seconds": result["runtime_seconds"],
                "peak_gpu_memory_gib": result["peak_gpu_memory_gib"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
