"""Model-free tests for the R13 static runtime byte-closure tool."""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
import zipfile

import r13_runtime_closure as closure


class RuntimeClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="_r13_runtime_closure_", dir=Path(__file__).parent
        )
        self.base = Path(self._temporary.name).resolve()
        self.runtime = self.base / "runtime"
        self.release = self.base / "release"
        self.work = self.base / "empty_working_directory"
        (self.runtime / "Lib" / "pkg").mkdir(parents=True)
        (self.runtime / "DLLs").mkdir()
        self.release.mkdir()
        self.work.mkdir()
        (self.runtime / "python.exe").write_bytes(b"dummy-python-executable\n")
        (self.runtime / "Lib" / "os.py").write_bytes(b"name = 'sealed-os'\n")
        (self.runtime / "Lib" / "pkg" / "__init__.py").write_bytes(
            b"VALUE = 7\n"
        )
        (self.runtime / "DLLs" / "core.dll").write_bytes(b"dummy-dll\x00")
        (self.runtime / "data.bin").write_bytes(b"all-distributed-data-is-bound\n")
        with zipfile.ZipFile(self.runtime / "python310.zip", "w") as archive:
            archive.writestr("zipmod.py", "ZIP_VALUE = 11\n")
        (self.release / "runner.py").write_bytes(b"raise SystemExit(0)\n")
        self.spec = {
            "schema_version": closure.SPEC_SCHEMA,
            "status": closure.SPEC_STATUS,
            "run_eligible": False,
            "model_execution_authorized": False,
            "expected_python_version": closure.EXPECTED_PYTHON_VERSION,
            "required_launch_flags": dict(closure.REQUIRED_LAUNCH_FLAGS),
            "roots": [
                {
                    "root_id": "runtime",
                    "role": "python_runtime",
                    "path": str(self.runtime),
                },
                {
                    "root_id": "release",
                    "role": "release_code",
                    "path": str(self.release),
                },
            ],
            "interpreter_path": str(self.runtime / "python.exe"),
            "release_entrypoint": str(self.release / "runner.py"),
            "ordered_sys_path": [
                str(self.runtime / "Lib"),
                str(self.runtime / "python310.zip"),
                str(self.release),
            ],
            "working_directory": str(self.work),
            "external_content_addressed_launch_receipt_required": True,
            "immutable_read_only_mount_required": True,
            "native_dependency_graph_attestation_required": True,
            "os_driver_attestation_required": True,
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_build_and_filesystem_replay_are_static_and_fail_closed(self) -> None:
        manifest = closure.build_manifest(self.spec)
        closure.validate_manifest(manifest)
        closure.verify_manifest_against_filesystem(manifest, self.spec)
        self.assertFalse(manifest["run_eligible"])
        self.assertFalse(manifest["full_dependency_content_hash_bound"])
        self.assertEqual(manifest["model_action_count"], 0)
        self.assertTrue(manifest["all_allowed_content_bytes_hashed"])
        self.assertEqual(
            {row["relative_path"] for row in manifest["native_artifacts"]},
            {"DLLs/core.dll", "python.exe"},
        )
        self.assertIn(
            "EXTERNAL_CONTENT_ADDRESSED_LAUNCH_RECEIPT_REQUIRED",
            manifest["blockers"],
        )

    def test_any_post_freeze_byte_change_breaks_filesystem_replay(self) -> None:
        manifest = closure.build_manifest(self.spec)
        (self.runtime / "data.bin").write_bytes(b"changed-after-freeze\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "differs"):
            closure.verify_manifest_against_filesystem(manifest, self.spec)

    def test_shadow_provider_across_sys_path_is_rejected(self) -> None:
        (self.release / "os.py").write_bytes(b"MALICIOUS = True\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "shadow import"):
            closure.build_manifest(self.spec)

    def test_module_package_ambiguity_within_one_import_root_is_rejected(self) -> None:
        (self.runtime / "Lib" / "pkg.py").write_bytes(b"AMBIGUOUS = True\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "ambiguous provider"):
            closure.build_manifest(self.spec)

    def test_cache_hook_and_nonempty_cwd_are_rejected(self) -> None:
        (self.runtime / "Lib" / "inject.pth").write_bytes(b"import bad\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "injection"):
            closure.build_manifest(self.spec)
        (self.runtime / "Lib" / "inject.pth").unlink()
        (self.work / "torch.py").write_bytes(b"shadow = True\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "must be empty"):
            closure.build_manifest(self.spec)

    def test_escaping_zip_member_is_rejected(self) -> None:
        with zipfile.ZipFile(self.runtime / "python310.zip", "w") as archive:
            archive.writestr("../escape.py", "BAD = True\n")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "escaping zip member"):
            closure.build_manifest(self.spec)

    def test_hardlink_is_rejected(self) -> None:
        try:
            (self.runtime / "linked_runner.py").hardlink_to(
                self.release / "runner.py"
            )
        except OSError as error:
            self.skipTest(f"hardlinks unavailable: {error}")
        with self.assertRaisesRegex(closure.RuntimeClosureError, "hard-linked"):
            closure.build_manifest(self.spec)

    def test_boolean_stub_and_manifest_provider_tamper_are_rejected(self) -> None:
        with self.assertRaises(closure.RuntimeClosureError):
            closure.validate_manifest(
                {
                    "schema_version": "stub",
                    "status": "stub",
                    "full_dependency_content_hash_bound": True,
                }
            )
        manifest = closure.build_manifest(self.spec)
        tampered = copy.deepcopy(manifest)
        tampered["top_level_import_providers"][0]["source"] = "not-in-tree.py"
        with self.assertRaisesRegex(closure.RuntimeClosureError, "not derivable"):
            closure.validate_manifest(tampered)


if __name__ == "__main__":
    unittest.main()
