from __future__ import annotations

import copy
import unittest

import r11_bridge_contract as bridge


PARENT = "a" * 64
PROTOCOL = "b" * 64
CONTRACT_HASH = "c" * 64


def valid_contract() -> dict:
    return {
        "schema_version": "r11-arm-contract-r1",
        "arm": "BUG",
        "reward_specification": bridge.expected_reward_specification("BUG"),
        "unique_permitted_treatment_difference": "reward_mask",
        "parent_r10_runner_sha256": PARENT,
        "r11_protocol_sha256": PROTOCOL,
        "mapping_stack_id": "TP1-M0-A_TO_B",
        "replicate_id": "A",
        "model_execution_authorized": False,
    }


def valid_manifest() -> dict:
    return {
        "bindings": {
            "r11_arm_contract_sha256": CONTRACT_HASH,
            "parent_r10_runner_sha256": PARENT,
            "r11_protocol_sha256": PROTOCOL,
        }
    }


def valid_authorization() -> dict:
    return {
        "r11_arm_contract_sha256": CONTRACT_HASH,
        "r11_protocol_sha256": PROTOCOL,
        "model_execution_authorized": True,
    }


class RewardMaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = ["A", "B", "C"]

    def test_bug_mask_rewards_gold_and_wrong(self) -> None:
        self.assertEqual(
            bridge.reward_mask(
                self.candidates,
                gold_candidate="A",
                wrong_candidate="C",
                arm="BUG",
            ),
            [1.0, 0.0, 1.0],
        )

    def test_gold_only_mask_rewards_only_gold(self) -> None:
        self.assertEqual(
            bridge.reward_mask(
                self.candidates,
                gold_candidate="A",
                wrong_candidate="C",
                arm="GOLD_ONLY",
            ),
            [1.0, 0.0, 0.0],
        )

    def test_duplicate_candidate_panel_fails(self) -> None:
        with self.assertRaises(bridge.BridgeContractError):
            bridge.reward_mask(
                ["A", "A"],
                gold_candidate="A",
                wrong_candidate="A",
                arm="BUG",
            )

    def test_gold_wrong_collision_fails(self) -> None:
        with self.assertRaises(bridge.BridgeContractError):
            bridge.reward_mask(
                self.candidates,
                gold_candidate="A",
                wrong_candidate="A",
                arm="BUG",
            )


class ArmContractTests(unittest.TestCase):
    def validate(self, contract: dict) -> str:
        return bridge.validate_arm_contract(
            contract,
            contract_sha256=CONTRACT_HASH,
            expected_contract_sha256=CONTRACT_HASH,
            parent_runner_sha256=PARENT,
            protocol_sha256=PROTOCOL,
            mapping_stack_id="TP1-M0-A_TO_B",
            replicate_id="A",
            manifest=valid_manifest(),
            authorization=valid_authorization(),
        )

    def test_accepts_complete_bound_contract(self) -> None:
        self.assertEqual(self.validate(valid_contract()), "BUG")

    def test_rejects_noncanonical_reward_specification(self) -> None:
        contract = valid_contract()
        contract["reward_specification"]["wrong_candidate_for_source_identity"] = 0.0
        with self.assertRaises(bridge.BridgeContractError):
            self.validate(contract)

    def test_rejects_self_authorizing_contract(self) -> None:
        contract = valid_contract()
        contract["model_execution_authorized"] = True
        with self.assertRaises(bridge.BridgeContractError):
            self.validate(contract)

    def test_rejects_stack_mismatch(self) -> None:
        contract = valid_contract()
        contract["mapping_stack_id"] = "TP2-M1-B_TO_A"
        with self.assertRaises(bridge.BridgeContractError):
            self.validate(contract)

    def test_rejects_unbound_authorization(self) -> None:
        authorization = valid_authorization()
        authorization["r11_arm_contract_sha256"] = "d" * 64
        with self.assertRaises(bridge.BridgeContractError):
            bridge.validate_arm_contract(
                valid_contract(),
                contract_sha256=CONTRACT_HASH,
                expected_contract_sha256=CONTRACT_HASH,
                parent_runner_sha256=PARENT,
                protocol_sha256=PROTOCOL,
                mapping_stack_id="TP1-M0-A_TO_B",
                replicate_id="A",
                manifest=valid_manifest(),
                authorization=authorization,
            )

    def test_gold_contract_uses_canonical_gold_spec(self) -> None:
        contract = valid_contract()
        contract["arm"] = "GOLD_ONLY"
        contract["reward_specification"] = bridge.expected_reward_specification(
            "GOLD_ONLY"
        )
        self.assertEqual(self.validate(contract), "GOLD_ONLY")


if __name__ == "__main__":
    unittest.main()
