from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("r13_runtime_environment.py")
SPEC = importlib.util.spec_from_file_location("r13_runtime_environment_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def fake_file(seed: str) -> dict:
    return {
        "path": str((Path.cwd() / seed).resolve()),
        "size_bytes": 1,
        "sha256": (seed.encode("ascii").hex() + "0" * 64)[:64],
    }


def fake_environment() -> dict:
    distributions = []
    for index, (name, (version, module)) in enumerate(sorted(runtime.EXPECTED_DISTRIBUTIONS.items())):
        distributions.append(
            {
                "distribution": name,
                "version": version,
                "top_level_module": module,
                "module_origin": fake_file(f"module{index}"),
                "dist_info_path": str((Path.cwd() / f"dist{index}.dist-info").resolve()),
                "metadata_files": {
                    "METADATA": fake_file(f"metadata{index}"),
                    "RECORD": fake_file(f"record{index}"),
                },
                "full_distribution_content_hash_bound": False,
                "full_content_binding_blocker": "RECORD_AND_ENTRYPOINT_ONLY_NOT_ALL_INSTALLED_BYTES",
            }
        )
    return {
        "schema_version": runtime.SCHEMA,
        "status": runtime.STATUS,
        "python_version": runtime.EXPECTED_PYTHON,
        "python_implementation": "cpython",
        "platform_tag": "win-amd64",
        "sys_prefix": str((Path.cwd() / "venv").resolve()),
        "sys_base_prefix": str((Path.cwd() / "base").resolve()),
        "interpreter_files": [fake_file("python"), fake_file("pyvenv")],
        "isolated_flags": {
            "isolated": 1,
            "ignore_environment": 1,
            "no_user_site": 1,
            "dont_write_bytecode": 1,
            "safe_path_flag_available": False,
            "safe_path": None,
        },
        "ordered_sys_path_before_release_insertion": [
            str((Path.cwd() / "base" / "lib").resolve()),
            str((Path.cwd() / "venv" / "site-packages").resolve()),
        ],
        "current_working_directory_excluded": True,
        "release_code_inserted": False,
        "distributions": distributions,
        "full_dependency_content_hash_bound": False,
        "full_dependency_binding_blocker": "HASH_ALL_RUNTIME_DISTRIBUTION_FILES_OR_BUILD_CLEAN_IMMUTABLE_IMAGE",
        "tokenizer_loaded": False,
        "model_weight_loaded": False,
        "model_forward_performed": False,
        "gradient_performed": False,
        "optimizer_step_performed": False,
        "model_execution_authorized": False,
    }


class RuntimeEnvironmentContractTests(unittest.TestCase):
    def test_reference_shape_passes_without_claiming_full_dependency_binding(self) -> None:
        value = fake_environment()
        runtime.validate_environment(value)
        self.assertFalse(value["full_dependency_content_hash_bound"])
        self.assertFalse(value["model_execution_authorized"])

    def test_path_flag_version_and_overclaim_drift_fail(self) -> None:
        mutations = []
        changed = copy.deepcopy(fake_environment())
        changed["isolated_flags"]["isolated"] = 0
        mutations.append(changed)
        changed = copy.deepcopy(fake_environment())
        changed["python_version"] = "3.13.9"
        mutations.append(changed)
        changed = copy.deepcopy(fake_environment())
        changed["full_dependency_content_hash_bound"] = True
        mutations.append(changed)
        changed = copy.deepcopy(fake_environment())
        changed["model_weight_loaded"] = True
        mutations.append(changed)
        for mutation in mutations:
            with self.assertRaises(runtime.RuntimeEnvironmentError):
                runtime.validate_environment(mutation)

    def test_distribution_missing_wrong_origin_hash_or_version_fail(self) -> None:
        changed = fake_environment()
        changed["distributions"].pop()
        with self.assertRaises(runtime.RuntimeEnvironmentError):
            runtime.validate_environment(changed)
        changed = fake_environment()
        changed["distributions"][0]["version"] = "0"
        with self.assertRaises(runtime.RuntimeEnvironmentError):
            runtime.validate_environment(changed)
        changed = fake_environment()
        changed["distributions"][0]["module_origin"]["sha256"] = "bad"
        with self.assertRaises(runtime.RuntimeEnvironmentError):
            runtime.validate_environment(changed)


if __name__ == "__main__":
    unittest.main()
