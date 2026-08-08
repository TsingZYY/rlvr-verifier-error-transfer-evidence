from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import r12_release_closure_audit as audit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = PROJECT_ROOT / "R11_GPT_PRO_STATIC_CUSTODY_PACKET_R1.zip"
INDEX_MEMBER = (
    "mvp_same_source_v1/frozen_r11_bridge_r1/R11_RELEASE_INDEX_R1.json"
)
MASTER_MEMBER = (
    "mvp_same_source_v1/frozen_r11_bridge_r1/"
    "R11_32_CELL_MASTER_INCLUSION_CONTRACT_R1.json"
)
PROTOCOL_MEMBER = (
    "formal_g1_development_r1/R11_GOLD_ONLY_DID_BRIDGE_PROTOCOL_R2.json"
)
FREEZER_MEMBER = "mvp_same_source_v1/freeze_r11_bridge_release.py"


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def finding(findings: list[dict[str, object]], code: str) -> dict[str, object]:
    return next(item for item in findings if item["code"] == code)


class StrictJsonTests(unittest.TestCase):
    def test_rejects_duplicate_keys_and_nonfinite_tokens(self) -> None:
        with self.assertRaisesRegex(audit.ReleaseClosureError, "duplicate JSON key"):
            audit.strict_json_bytes(b'{"a":1,"a":2}', "duplicate.json")
        with self.assertRaisesRegex(audit.ReleaseClosureError, "non-finite"):
            audit.strict_json_bytes(b'{"a":NaN}', "nan.json")


class ArchivePathTests(unittest.TestCase):
    def test_rejects_backslash_traversal_and_casefold_collision(self) -> None:
        # zipfile normalizes a newly-written Windows separator, so exercise the
        # raw-name predicate directly for archives produced by other tools.
        self.assertEqual(
            audit._safe_member_name(r"bad\file.txt"),
            (False, "backslash_separator"),
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive_file:
                archive_file.writestr("ok/file.txt", b"ok")
                archive_file.writestr(r"bad\file.txt", b"bad")
                archive_file.writestr("../escape.txt", b"bad")
                archive_file.writestr("Case.txt", b"a")
                archive_file.writestr("case.txt", b"b")
            _, issues = audit.read_archive(archive_path)
        reasons = {item["reason"] for item in issues}
        self.assertIn("non_canonical_component", reasons)
        self.assertTrue(any(reason.startswith("casefold_collision_with:") for reason in reasons))


class MemberManifestTests(unittest.TestCase):
    def test_exact_manifest_passes_and_missing_manifest_fails(self) -> None:
        payload = {"code.py": b"x = 1\n", "data.json": b"{}"}
        manifest = {
            "schema_version": audit.MEMBER_MANIFEST_SCHEMA,
            "member_sha256_by_path": {
                name: audit.sha256_bytes(data) for name, data in payload.items()
            },
        }
        members = dict(payload)
        members[audit.DEFAULT_MEMBER_MANIFEST] = json_bytes(manifest)
        checks: list[dict[str, object]] = []
        audit.audit_member_manifest(members, audit.DEFAULT_MEMBER_MANIFEST, checks)
        self.assertEqual(checks[0]["status"], "PASS")

        checks = []
        audit.audit_member_manifest(payload, audit.DEFAULT_MEMBER_MANIFEST, checks)
        self.assertEqual(checks[0]["status"], "FAIL")


class SourceClosureTests(unittest.TestCase):
    def test_project_local_import_requires_source_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("import local_dep\n", encoding="utf-8")
            (root / "local_dep.py").write_text("VALUE = 1\n", encoding="utf-8")
            members = {"src/main.py": (root / "main.py").read_bytes()}
            checks: list[dict[str, object]] = []
            audit.audit_source_closure(members, [root], checks)
            self.assertEqual(checks[0]["status"], "FAIL")
            missing = checks[0]["evidence"]["missing_local_modules"]
            self.assertEqual(set(missing), {"local_dep"})

            members["src/local_dep.py"] = (root / "local_dep.py").read_bytes()
            checks = []
            audit.audit_source_closure(members, [root], checks)
            self.assertEqual(checks[0]["status"], "PASS")


class CellBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.members, _ = audit.read_archive(ARCHIVE)

    def test_current_32_cells_are_key_content_bound(self) -> None:
        checks: list[dict[str, object]] = []
        audit.audit_cell_bindings(
            self.members, INDEX_MEMBER, MASTER_MEMBER, checks
        )
        self.assertEqual(checks[0]["status"], "PASS")
        self.assertEqual(checks[0]["evidence"]["checked_cells"], 32)

    def test_manifest_alias_is_rejected_even_when_index_has_32_keys(self) -> None:
        members = dict(self.members)
        index = audit.strict_json_bytes(members[INDEX_MEMBER], INDEX_MEMBER)
        cells = list(audit.EXPECTED_R11_CELLS)
        index = copy.deepcopy(index)
        index["cell_files"][cells[1]]["manifest"] = index["cell_files"][cells[0]][
            "manifest"
        ]
        members[INDEX_MEMBER] = json_bytes(index)
        checks: list[dict[str, object]] = []
        audit.audit_cell_bindings(members, INDEX_MEMBER, MASTER_MEMBER, checks)
        self.assertEqual(checks[0]["status"], "FAIL")
        errors = checks[0]["evidence"]["errors"]
        self.assertTrue(any("manifest_alias" in item for item in errors))
        self.assertTrue(any("manifest_internal_cell_mismatch" in item for item in errors))


class RevisionAndPortabilityTests(unittest.TestCase):
    def test_revision_platform_and_inventory_pollution_are_independent_blockers(self) -> None:
        members = {
            "protocol.json": json_bytes({"model": {"revision": "0" * 40}}),
            "one_config.json": json_bytes(
                {"model": {"revision": audit.EXPECTED_MODEL_REVISION}}
            ),
            "one_manifest.json": json_bytes(
                {
                    "runtime_observed_at_manifest_build": {"platform": "host-A"},
                    "bindings": {
                        "model_recursive_inventory": [
                            {"path": ".cache/huggingface/download/x.metadata"}
                        ]
                    },
                }
            ),
        }
        checks: list[dict[str, object]] = []
        audit.audit_revisions_and_manifests(
            members, "protocol.json", audit.EXPECTED_MODEL_REVISION, checks
        )
        self.assertEqual(
            {item["code"] for item in checks if item["status"] == "FAIL"},
            {
                "MODEL_REVISION_SINGLE_SOURCE_OF_TRUTH",
                "CANONICAL_MANIFEST_PLATFORM_INDEPENDENT",
                "MODEL_INVENTORY_SEMANTIC_WHITELIST",
            },
        )

        members["protocol.json"] = json_bytes(
            {"model": {"revision": audit.EXPECTED_MODEL_REVISION}}
        )
        members["one_manifest.json"] = json_bytes(
            {
                "runtime_observed_at_manifest_build": {},
                "bindings": {
                    "model_recursive_inventory": [
                        {"path": "model.safetensors"},
                        {"path": "tokenizer.json"},
                    ]
                },
            }
        )
        checks = []
        audit.audit_revisions_and_manifests(
            members, "protocol.json", audit.EXPECTED_MODEL_REVISION, checks
        )
        self.assertTrue(all(item["status"] == "PASS" for item in checks))


class ExactGlobTests(unittest.TestCase):
    def test_next_glob_is_detected_and_cardinality_is_checked(self) -> None:
        unsafe = b"from pathlib import Path\np = next(Path('.').glob('*'))\n"
        safe = b"from pathlib import Path\nxs = list(Path('.').glob('*'))\nassert len(xs) == 1\n"
        self.assertEqual(audit._contains_next_glob(unsafe, "unsafe.py"), [2])
        self.assertEqual(audit._contains_next_glob(safe, "safe.py"), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, 9):
                (root / f"{index:02d}_stack_config.json").write_text("{}", encoding="utf-8")
                (root / f"{index:02d}_stack_determinism.json").write_text("{}", encoding="utf-8")
            checks: list[dict[str, object]] = []
            audit.audit_exact_globs(
                {"freeze.py": safe}, "freeze.py", root, checks
            )
            self.assertTrue(all(item["status"] == "PASS" for item in checks))

            (root / "01_duplicate_config.json").write_text("{}", encoding="utf-8")
            checks = []
            audit.audit_exact_globs(
                {"freeze.py": safe}, "freeze.py", root, checks
            )
            self.assertEqual(finding(checks, "CURRENT_R10_GLOB_CARDINALITY")["status"], "FAIL")


class CurrentPacketRegressionTests(unittest.TestCase):
    def test_current_packet_is_blocked_but_zip_paths_are_posix(self) -> None:
        report = audit.audit_release(
            archive_path=ARCHIVE,
            source_roots=[
                PROJECT_ROOT / "mvp_same_source_v1",
                PROJECT_ROOT / "formal_g1_development_r1",
                PROJECT_ROOT / "commitment_core.py",
            ],
            index_member=INDEX_MEMBER,
            master_member=MASTER_MEMBER,
            protocol_member=PROTOCOL_MEMBER,
            freezer_member=FREEZER_MEMBER,
            r10_contract_dir=PROJECT_ROOT / "mvp_same_source_v1/frozen_eight_stack_r10",
        )
        self.assertEqual(report["status"], "BLOCKED_RELEASE_CLOSURE")
        self.assertFalse(report["run_eligible"])
        self.assertFalse(report["model_execution_performed"])
        path_check = finding(report["findings"], "ZIP_PORTABLE_CANONICAL_PATHS")
        self.assertEqual(path_check["status"], "PASS")
        self.assertEqual(path_check["evidence"]["backslash_member_count"], 0)
        self.assertEqual(path_check["evidence"]["posix_subpath_member_count"], 110)
        pollution = finding(report["findings"], "NO_BYTECODE_OR_CACHE_POLLUTION")
        self.assertEqual(pollution["status"], "FAIL")
        self.assertEqual(len(pollution["evidence"]["prohibited_members"]), 4)
        closure = finding(report["findings"], "LOCAL_SOURCE_DEPENDENCY_CLOSURE")
        self.assertEqual(
            set(closure["evidence"]["missing_local_modules"]),
            {"analyze_r10_g1_development"},
        )
        self.assertEqual(
            finding(report["findings"], "CELL_KEY_CONTENT_AND_UNIQUENESS_BINDING")["status"],
            "PASS",
        )
        self.assertEqual(
            finding(report["findings"], "CURRENT_R10_GLOB_CARDINALITY")["status"],
            "PASS",
        )


if __name__ == "__main__":
    unittest.main()
