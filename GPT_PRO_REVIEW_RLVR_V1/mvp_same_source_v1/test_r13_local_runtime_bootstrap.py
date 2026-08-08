"""Model-free tests for the local R13 development runtime bootstrap."""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import r13_local_runtime_bootstrap as bootstrap


PROJECT = Path(__file__).resolve().parent
WORKSPACE = PROJECT.parents[1]
HYBRID_PYTHON = WORKSPACE / ".mvp-venv" / "Scripts" / "python.exe"
REFERENCE = PROJECT.parent / "formal_g1_development_r1" / bootstrap.REFERENCE_FILENAME


class LocalRuntimeBootstrapUnitTests(unittest.TestCase):
    def test_gpu_parser_enforces_shape_and_threshold(self) -> None:
        line = "0, NVIDIA GeForce RTX 5060 Laptop GPU, 595.79, 8151, 6543, 1608, 12.0\n"
        observed = bootstrap.parse_gpu_query(line, 5500)
        self.assertTrue(observed["free_memory_gate_passed"])
        self.assertFalse(observed["resource_reserved"])
        self.assertEqual(observed["memory_free_mib"], 6543)
        bootstrap.parse_gpu_query(
            "0, GPU, 1.0, 5500, 5500, 0, 12.0\n", 5500
        )
        with self.assertRaisesRegex(
            bootstrap.LocalRuntimeBootstrapError, "free-memory gate"
        ):
            bootstrap.parse_gpu_query(line, 7000)
        with self.assertRaises(bootstrap.LocalRuntimeBootstrapError):
            bootstrap.parse_gpu_query("0, malformed\n", 1)
        with self.assertRaisesRegex(
            bootstrap.LocalRuntimeBootstrapError, "exactly one"
        ):
            bootstrap.parse_gpu_query(line + line, 1)

    def test_fixed_environment_overwrites_conflicts(self) -> None:
        previous = {key: os.environ.get(key) for key in bootstrap.FIXED_ENVIRONMENT}
        try:
            for key in bootstrap.FIXED_ENVIRONMENT:
                os.environ[key] = "hostile-value"
            self.assertEqual(
                bootstrap.freeze_environment(), bootstrap.FIXED_ENVIRONMENT
            )
            self.assertEqual(
                {key: os.environ.get(key) for key in bootstrap.FIXED_ENVIRONMENT},
                bootstrap.FIXED_ENVIRONMENT,
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_strict_json_rejects_duplicate_and_nonfinite(self) -> None:
        with self.assertRaisesRegex(
            bootstrap.LocalRuntimeBootstrapError, "duplicate key"
        ):
            bootstrap.strict_json_loads(b'{"a":1,"a":2}\n', "fixture")
        with self.assertRaisesRegex(
            bootstrap.LocalRuntimeBootstrapError, "non-finite"
        ):
            bootstrap.strict_json_loads(b'{"a":NaN}\n', "fixture")

    def test_dispatch_allowlist_excludes_model_runner(self) -> None:
        with self.assertRaisesRegex(
            bootstrap.LocalRuntimeBootstrapError, "not in the static allowlist"
        ):
            bootstrap.resolve_dispatch("run_same_source_mvp", PROJECT)
        safe = bootstrap.resolve_dispatch("r13_runner_core", PROJECT)
        self.assertTrue(safe["requested"])
        self.assertFalse(safe["model_capable_dispatch_enabled"])

    def test_import_blocker_prevents_torch_before_import(self) -> None:
        self.assertNotIn("torch", sys.modules)
        with bootstrap.block_forbidden_imports():
            with self.assertRaisesRegex(
                bootstrap.LocalRuntimeBootstrapError,
                "model/tokenizer-capable import is disabled",
            ):
                importlib.import_module("torch")
        self.assertNotIn("torch", sys.modules)


class LocalRuntimeBootstrapSubprocessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not HYBRID_PYTHON.is_file():
            raise unittest.SkipTest("frozen hybrid Python is unavailable")
        if not REFERENCE.is_file():
            raise unittest.SkipTest("frozen runtime reference is unavailable")
        if not Path(r"C:\Windows\System32\nvidia-smi.exe").is_file():
            raise unittest.SkipTest("system nvidia-smi is unavailable")

    def _command(
        self,
        receipt: Path,
        *,
        flags: list[str] | None = None,
        dispatch_module: str | None = None,
    ) -> list[str]:
        command = [
            str(HYBRID_PYTHON),
            *(flags if flags is not None else ["-I", "-S", "-B"]),
            str(PROJECT / "r13_local_runtime_bootstrap.py"),
            "--runtime-reference",
            str(REFERENCE),
            "--project-path",
            str(PROJECT),
            "--receipt",
            str(receipt),
            "--min-free-gpu-mib",
            "1",
        ]
        if dispatch_module is not None:
            command.extend(["--dispatch-module", dispatch_module])
        return command

    def _run(
        self,
        *,
        flags: list[str] | None = None,
        dispatch_module: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool, bytes | None]:
        with tempfile.TemporaryDirectory(prefix="r13_local_bootstrap_") as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            environment = os.environ.copy()
            for key in bootstrap.FIXED_ENVIRONMENT:
                environment[key] = "hostile-parent-value"
            completed = subprocess.run(
                self._command(
                    receipt,
                    flags=flags,
                    dispatch_module=dispatch_module,
                ),
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=60,
            )
            exists = receipt.exists()
            raw = receipt.read_bytes() if exists else None
            return completed, exists, raw

    def test_real_hybrid_preflight_writes_non_authorizing_receipt(self) -> None:
        completed, exists, raw = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(exists)
        self.assertIsNotNone(raw)
        assert raw is not None
        receipt = bootstrap.strict_json_loads(raw, "receipt")
        self.assertEqual(raw, bootstrap.canonical_json_bytes(receipt))
        bootstrap.validate_receipt(receipt)
        self.assertEqual(receipt["status"], bootstrap.STATUS)
        self.assertFalse(receipt["run_eligible"])
        self.assertFalse(receipt["model_execution_authorized"])
        self.assertFalse(receipt["full_dependency_content_hash_bound"])
        self.assertEqual(receipt["python"]["initial_prefix_under_no_site"], receipt["python"]["base_prefix"])
        self.assertEqual(receipt["python"]["rebuilt_prefix"], receipt["python"]["referenced_overlay_prefix"])
        self.assertEqual(receipt["environment"], bootstrap.FIXED_ENVIRONMENT)
        self.assertGreaterEqual(
            len(receipt["startup_isolation"]["hook_files_present_but_not_executed"]),
            2,
        )
        tampered = copy.deepcopy(receipt)
        tampered["model_execution_authorized"] = True
        with self.assertRaises(bootstrap.LocalRuntimeBootstrapError):
            bootstrap.validate_receipt(tampered)

    def test_missing_no_site_fails_without_success_receipt(self) -> None:
        completed, exists, _ = self._run(flags=["-I", "-B"])
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(exists)
        self.assertIn("LOCAL_RUNTIME_PREFLIGHT_FAILED", completed.stderr)

    def test_controlled_static_dispatch_runs_runner_core_only(self) -> None:
        completed, exists, raw = self._run(dispatch_module="r13_runner_core")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(exists)
        assert raw is not None
        receipt = json.loads(raw.decode("ascii"))
        self.assertTrue(receipt["dispatch"]["requested"])
        self.assertEqual(receipt["dispatch"]["module"], "r13_runner_core")
        self.assertFalse(receipt["dispatch"]["model_capable_dispatch_enabled"])

    def test_static_receipt_verifier_replays_recorded_files(self) -> None:
        completed, exists, raw = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(exists)
        assert raw is not None
        with tempfile.TemporaryDirectory(prefix="r13_receipt_verify_") as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            receipt.write_bytes(raw)
            verified = subprocess.run(
                [
                    str(HYBRID_PYTHON),
                    "-I",
                    "-S",
                    "-B",
                    str(PROJECT / "r13_local_runtime_bootstrap.py"),
                    "--verify-receipt",
                    str(receipt),
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=60,
            )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        summary = json.loads(verified.stdout)
        self.assertEqual(
            summary["status"],
            "STATIC_RECEIPT_STRUCTURE_AND_RECORDED_FILES_MATCH",
        )
        self.assertFalse(summary["gpu_observation_replayed"])

    def test_cli_rejects_model_capable_dispatch_without_receipt(self) -> None:
        completed, exists, _ = self._run(dispatch_module="run_same_source_mvp")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
