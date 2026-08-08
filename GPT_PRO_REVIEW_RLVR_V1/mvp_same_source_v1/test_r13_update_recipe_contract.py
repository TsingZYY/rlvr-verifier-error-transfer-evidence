from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import r13_update_recipe_contract as recipe


ROOT = Path(__file__).resolve().parents[1]
MVP = Path(__file__).resolve().parent
FORMAL = ROOT / "formal_g1_development_r1"
ASSETS = ROOT / "real_assets" / "build_v5_repair_a"


def actual_contract() -> dict:
    return recipe.build_contract(
        config_paths_by_stack=recipe._configs(MVP / "frozen_eight_stack_r10"),
        protocol_path=FORMAL / "R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json",
        source_bundles_path=ASSETS / "REAL_SOURCE_BUNDLES_V1.jsonl",
        mapping_stacks_path=ASSETS / "REAL_MAPPING_STACKS_V1.jsonl",
        model_inventory_path=FORMAL / "R13_MODEL_INVENTORY_R1.json",
        runtime_environment_path=FORMAL / "R13_RUNTIME_ENVIRONMENT_REFERENCE_R1.json",
        parent_runner_path=MVP / "run_same_source_mvp.py",
    )


class R13UpdateRecipeTests(unittest.TestCase):
    def test_actual_four_m0_parent_configs_freeze_one_exact_recipe(self) -> None:
        value = actual_contract()
        recipe.validate_contract(value)
        self.assertEqual(value["ordered_stack_ids"], list(recipe.STACKS))
        self.assertFalse(value["production_backend_adapter_bound"])
        self.assertFalse(value["model_execution_performed"])

    def test_parent_config_update_scoring_or_stack_drift_fail(self) -> None:
        parent = recipe.strict_json(MVP / "frozen_eight_stack_r10" / "01_tp1_m0_a_to_b_config.json")
        changed = copy.deepcopy(parent)
        changed["update"]["learning_rate"] = 0.2
        with self.assertRaises(recipe.UpdateRecipeError):
            recipe.validate_parent_config(changed, "TP1-M0-A_TO_B")
        changed = copy.deepcopy(parent)
        changed["scoring"]["candidate_set"].reverse()
        with self.assertRaises(recipe.UpdateRecipeError):
            recipe.validate_parent_config(changed, "TP1-M0-A_TO_B")
        with self.assertRaises(recipe.UpdateRecipeError):
            recipe.validate_parent_config(parent, "TP2-M0-A_TO_B")

    def test_target_input_backend_binding_or_authorization_drift_fail(self) -> None:
        base = actual_contract()
        mutations = []
        changed = copy.deepcopy(base)
        changed["source_update_forbidden_inputs"].remove("target codebook")
        mutations.append(changed)
        changed = copy.deepcopy(base)
        changed["source_update_input_allowlist"][0] = "target arm"
        mutations.append(changed)
        changed = copy.deepcopy(base)
        changed["mathematical_update_semantics"]["parameter_step"] = (
            "theta := theta + 0.1 * clipped_gradient"
        )
        mutations.append(changed)
        changed = copy.deepcopy(base)
        changed["production_backend_adapter_bound"] = True
        mutations.append(changed)
        changed = copy.deepcopy(base)
        changed["model_execution_authorized"] = True
        mutations.append(changed)
        for mutation in mutations:
            with self.assertRaises(recipe.UpdateRecipeError):
                recipe.validate_contract(mutation)

    def test_duplicate_parent_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = MVP / "frozen_eight_stack_r10" / "01_tp1_m0_a_to_b_config.json"
            (directory / "a_config.json").write_bytes(source.read_bytes())
            (directory / "b_config.json").write_bytes(source.read_bytes())
            with self.assertRaises(recipe.UpdateRecipeError):
                recipe._configs(directory)


if __name__ == "__main__":
    unittest.main()
