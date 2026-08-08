from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import r13_model_inventory as inventory


class R13ModelInventoryTests(unittest.TestCase):
    def make_model(self, root: Path) -> Path:
        model = root / "model"
        model.mkdir()
        for index, name in enumerate(inventory.EXPECTED_FILES):
            (model / name).write_bytes(f"member-{index}\n".encode("ascii"))
        cache = model / ".cache" / "huggingface" / "download"
        cache.mkdir(parents=True)
        (cache / "config.json.metadata").write_bytes(b"metadata\n")
        return model

    def test_build_and_revalidate_exact_semantic_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = self.make_model(Path(temp))
            value = inventory.build_inventory(model)
            inventory.validate_inventory(value, model)
            self.assertEqual(value["semantic_file_count"], 8)
            self.assertEqual(
                value["ignored_cache_policy"]["observed_paths"],
                [".cache/huggingface/download/config.json.metadata"],
            )
            self.assertFalse(value["model_loaded"])

    def test_missing_or_extra_semantic_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = self.make_model(Path(temp))
            (model / "vocab.json").unlink()
            with self.assertRaises(inventory.InventoryError):
                inventory.build_inventory(model)
            (model / "vocab.json").write_bytes(b"restored\n")
            (model / "unexpected.bin").write_bytes(b"x")
            with self.assertRaises(inventory.InventoryError):
                inventory.build_inventory(model)

    def test_semantic_byte_and_cache_inventory_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = self.make_model(Path(temp))
            value = inventory.build_inventory(model)
            (model / "config.json").write_bytes(b"changed\n")
            with self.assertRaises(inventory.InventoryError):
                inventory.validate_inventory(value, model)
            (model / "config.json").write_bytes(b"member-0\n")
            extra_cache = model / ".cache" / "new.metadata"
            extra_cache.write_bytes(b"x")
            with self.assertRaises(inventory.InventoryError):
                inventory.validate_inventory(value, model)

    def test_schema_revision_hash_and_model_action_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            value = inventory.build_inventory(self.make_model(Path(temp)))
            mutations = []
            changed = copy.deepcopy(value)
            changed["revision"] = "0" * 40
            mutations.append(changed)
            changed = copy.deepcopy(value)
            changed["semantic_files"][0]["sha256"] = "not-a-hash"
            mutations.append(changed)
            changed = copy.deepcopy(value)
            changed["model_forward_performed"] = True
            mutations.append(changed)
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    with self.assertRaises(inventory.InventoryError):
                        inventory.validate_inventory(mutation)

    def test_duplicate_key_nonfinite_and_noncanonical_json_are_rejected(self) -> None:
        with self.assertRaises(inventory.InventoryError):
            inventory._strict_object(b'{"a":1,"a":2}', "duplicate")
        with self.assertRaises(inventory.InventoryError):
            inventory._strict_object(b'{"a":NaN}', "nonfinite")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = inventory.build_inventory(self.make_model(root))
            path = root / "inventory.json"
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
            with self.assertRaises(inventory.InventoryError):
                inventory.read_inventory(path)

    def test_symlink_member_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = self.make_model(root)
            target = root / "replacement.txt"
            target.write_bytes(b"replacement\n")
            member = model / "vocab.json"
            member.unlink()
            try:
                member.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(inventory.InventoryError):
                inventory.build_inventory(model)


if __name__ == "__main__":
    unittest.main()
