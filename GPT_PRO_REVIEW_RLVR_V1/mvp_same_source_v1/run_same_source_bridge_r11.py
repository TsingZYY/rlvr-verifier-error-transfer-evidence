from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import r11_bridge_contract as bridge
import r11_static_contract as r11_custody
import run_same_source_mvp as parent


PARENT_RUNNER_PATH = Path(__file__).resolve().with_name("run_same_source_mvp.py")
EXPECTED_PARENT_RUNNER_SHA256 = (
    "439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d"
)
PROTOCOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "formal_g1_development_r1"
    / "R11_GOLD_ONLY_DID_BRIDGE_PROTOCOL_R2.json"
)
EXPECTED_PROTOCOL_SHA256 = (
    "c9015c8d04da3b95d43162ab087e5fe4b4dcf1c4a5e689f2df610d2e379efd78"
)


def update_for_source_rule_r11(
    model: Any,
    tokenizer: Any,
    rows: list[dict[str, Any]],
    candidates: list[str],
    offset: int,
    *,
    learning_rate: float,
    gradient_clip_norm: float,
    arm: str,
) -> dict[str, Any]:
    wrong_candidates = [parent.candidate_for_offset(row, offset) for row in rows]
    mask_sha256 = bridge.reward_mask_commitment(
        rows,
        candidates,
        wrong_candidates=wrong_candidates,
        arm=arm,
    )
    model.zero_grad(set_to_none=True)
    objectives: list[float] = []
    for row, wrong_candidate in zip(rows, wrong_candidates):
        scores, _ = parent.candidate_logprobs(
            model,
            tokenizer,
            str(row["prompt_text"]),
            candidates,
            require_grad=True,
        )
        probabilities = parent.torch.softmax(scores, dim=0)
        mask = bridge.reward_mask(
            candidates,
            gold_candidate=str(row["gold_candidate"]),
            wrong_candidate=wrong_candidate,
            arm=arm,
        )
        reward = parent.torch.tensor(
            mask, dtype=probabilities.dtype, device=probabilities.device
        )
        expected_reward = (probabilities * reward).sum()
        (-expected_reward / len(rows)).backward()
        objectives.append(float(expected_reward.detach().cpu()))

    parameters = [
        parameter for parameter in model.parameters() if parameter.requires_grad
    ]
    if not parameters or not any(parameter.grad is not None for parameter in parameters):
        raise RuntimeError("source update produced no trainable gradients")
    raw_gradient_norm = float(
        parent.torch.sqrt(
            sum(
                parent.torch.sum(parameter.grad.detach().float() ** 2)
                for parameter in parameters
                if parameter.grad is not None
            )
        ).cpu()
    )
    clipped_norm = float(
        parent.torch.nn.utils.clip_grad_norm_(parameters, gradient_clip_norm)
        .detach()
        .cpu()
    )
    if raw_gradient_norm > gradient_clip_norm:
        raise RuntimeError(
            "R11 frozen bridge forbids realized gradient clipping: "
            f"raw={raw_gradient_norm} clip={gradient_clip_norm}"
        )
    squared_update_norm = 0.0
    with parent.torch.no_grad():
        for parameter in parameters:
            if parameter.grad is None:
                continue
            delta = parameter.grad.detach().float() * learning_rate
            squared_update_norm += float(parent.torch.sum(delta**2).cpu())
            parameter.add_(parameter.grad, alpha=-learning_rate)
    model.zero_grad(set_to_none=True)
    return {
        "arm": arm,
        "reward_mask_sha256": mask_sha256,
        "mean_pre_update_expected_reward": sum(objectives) / len(objectives),
        "raw_gradient_norm": raw_gradient_norm,
        "clip_grad_norm_return": clipped_norm,
        "realized_update_norm": math.sqrt(squared_update_norm),
        "clipping_not_triggered": True,
    }


def run_bridge(args: argparse.Namespace) -> dict[str, Any]:
    parent_runner_sha256 = bridge.sha256_file(PARENT_RUNNER_PATH)
    if parent_runner_sha256 != EXPECTED_PARENT_RUNNER_SHA256:
        raise bridge.BridgeContractError("frozen parent R10 runner hash mismatch")
    protocol_sha256 = bridge.sha256_file(PROTOCOL_PATH)
    if protocol_sha256 != EXPECTED_PROTOCOL_SHA256:
        raise bridge.BridgeContractError("frozen R11 protocol hash mismatch")
    contract_sha256 = bridge.sha256_file(args.arm_contract)
    if contract_sha256 != args.expected_arm_contract_sha256:
        raise r11_custody.R11ContractError("external expected arm-contract hash mismatch")
    contract = bridge.read_json(args.arm_contract)
    config = bridge.read_json(args.config)
    manifest = bridge.read_json(args.execution_manifest)
    mapping_stack_id = str(config["data"]["mapping_stack_id"])
    selected_cell = r11_custody.validate_arm_contract(
        contract,
        expected_parent_runner_sha256=parent_runner_sha256,
        expected_protocol_sha256=protocol_sha256,
    )
    arm = str(contract["arm"])
    if selected_cell != r11_custody.cell_id(mapping_stack_id, arm, args.replicate_id):
        raise r11_custody.R11ContractError("config/arm/replicate cell binding mismatch")
    if r11_custody.validate_manifest_shape(manifest) != selected_cell:
        raise r11_custody.R11ContractError("manifest/arm-contract cell binding mismatch")
    if manifest["bindings"]["r11_arm_contract_sha256"] != contract_sha256:
        raise r11_custody.R11ContractError("manifest arm-contract hash mismatch")

    original_update = parent.update_for_source_rule
    original_file = parent.__file__
    original_verify_preflight = parent.static_contract.verify_preflight
    original_verify_invocation = parent.static_contract.verify_invocation_start_receipt

    def bound_update(*call_args: Any, **call_kwargs: Any) -> dict[str, Any]:
        return update_for_source_rule_r11(
            *call_args, **call_kwargs, arm=arm
        )

    def bound_preflight(manifest_value: dict[str, Any], **kwargs: Any) -> None:
        if kwargs.pop("require_model_authorization", None) is not True:
            raise r11_custody.R11ContractError("R11 model authorization gate was bypassed")
        r11_custody.verify_preflight(
            manifest_value,
            **kwargs,
            require_model_authorization=True,
            arm_contract_path=args.arm_contract,
            expected_arm_contract_sha256=args.expected_arm_contract_sha256,
            parent_runner_path=PARENT_RUNNER_PATH,
            protocol_path=PROTOCOL_PATH,
            bridge_contract_path=Path(bridge.__file__).resolve(),
        )

    def bound_invocation(**kwargs: Any) -> dict[str, Any]:
        return r11_custody.verify_invocation_start_receipt(
            **kwargs,
            arm=arm,
            arm_contract_sha256=contract_sha256,
        )

    parent.update_for_source_rule = bound_update
    parent.__file__ = str(Path(__file__).resolve())
    parent.static_contract.verify_preflight = bound_preflight
    parent.static_contract.verify_invocation_start_receipt = bound_invocation
    try:
        result = parent.run(args)
    finally:
        parent.update_for_source_rule = original_update
        parent.__file__ = original_file
        parent.static_contract.verify_preflight = original_verify_preflight
        parent.static_contract.verify_invocation_start_receipt = original_verify_invocation

    if [item.get("source_rule_identity") for item in result["source_updates"]] != bridge.EXPECTED_IDENTITIES:
        raise RuntimeError("R11 runner did not produce the frozen five source identities")
    for item in result["source_updates"]:
        if item.get("arm") != arm or not item.get("reward_mask_sha256"):
            raise RuntimeError("R11 source update lacks a bound arm/reward-mask commitment")
        if item.get("clipping_not_triggered") is not True:
            raise RuntimeError("R11 source update clipping gate failed")

    result["bridge_schema_version"] = "r11-same-source-bridge-result-r1"
    result["arm"] = arm
    result["r11_arm_contract_sha256"] = contract_sha256
    result["r11_protocol_sha256"] = protocol_sha256
    result["parent_r10_runner_sha256"] = parent_runner_sha256
    result["unique_permitted_treatment_difference"] = "reward_mask"
    result["development_screen_only"] = True
    result["formal_confirmatory"] = False
    result["sampled_rlvr"] = False
    result["hidden_audit"] = False
    parent.assert_finite(result)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--arm-contract", type=Path, required=True)
    parser.add_argument("--expected-arm-contract-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_bridge(args)
    print(
        json.dumps(
            {
                "run_status": result["run_status"],
                "arm": result["arm"],
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
