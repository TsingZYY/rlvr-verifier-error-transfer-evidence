"""Fast deterministic process-level R10 output audit for an independent reviewer."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def validator_args(output: str, root: Path) -> list[str]:
    return [
        "--master-inclusion-contract", str(root / "missing-master.json"),
        "--expected-master-sha256", "0" * 64,
        "--result-anchor", str(root / "missing-anchor.json"),
        "--expected-result-anchor-sha256", "1" * 64,
        "--validation-report", str(root / "missing-report.json"),
        "--expected-validation-report-sha256", "2" * 64,
        "--output", output,
    ]


def load_validator(path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("r10_completion", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child(validator: Path, output: str, marker: Path) -> int:
    module = load_validator(validator)

    def delayed_missing_hash(_path: Path) -> str:
        marker.write_text("post-precheck\n", encoding="ascii")
        time.sleep(1.0)
        raise FileNotFoundError("deterministic post-precheck delay")

    module.contract.sha256_file = delayed_missing_hash
    return module.main(validator_args(output, marker.parent))


def run_cli(validator: Path, cwd: Path, output: str, root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(validator), *validator_args(output, root)],
        cwd=cwd, capture_output=True, text=True, timeout=15,
    )
    decoded = json.loads(result.stdout)
    return {
        "exit": result.returncode,
        "canonical_fail": decoded.get("validation_status") == "FAIL"
        and decoded.get("aggregate") is None,
        "error": decoded.get("errors", [None])[0],
        "stderr_empty": result.stderr == "",
    }


def wait_marker(marker: Path) -> None:
    deadline = time.monotonic() + 5
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("child never reached post-precheck marker")
        time.sleep(0.01)


def main(validator: Path) -> int:
    validator = validator.resolve(strict=True)
    sentinel = b"DO-NOT-OVERWRITE\n"
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cwd = root / "cwd"
        outside = root / "outside"
        cwd.mkdir()
        outside.mkdir()

        fresh = run_cli(validator, cwd, "fresh.json", root)
        fresh["created_in_cwd"] = (cwd / "fresh.json").is_file()
        results["A_fresh_leaf"] = fresh

        escape = outside / "escape.json"
        case = run_cli(validator, cwd, str(escape), root)
        case["outside_not_created"] = not escape.exists()
        results["B_absolute_outside"] = case
        nested = cwd / "nested" / "escape.json"
        parent_escape = root / "escape.json"
        nested_case = run_cli(validator, cwd, "nested/escape.json", root)
        nested_case["not_created"] = not nested.exists()
        dotdot_case = run_cli(validator, cwd, "../escape.json", root)
        dotdot_case["not_created"] = not parent_escape.exists()
        results["C_nested_and_dotdot"] = {
            "nested": nested_case, "dotdot": dotdot_case
        }

        alias = root / "cwd-alias"
        try:
            os.symlink(cwd, alias, target_is_directory=True)
            alias_case = run_cli(validator, cwd, str(alias / "alias.json"), root)
            alias_case["target_not_created"] = not (cwd / "alias.json").exists()
            results["cwd_alias"] = alias_case
        except (OSError, NotImplementedError) as error:
            results["cwd_alias"] = {"platform_unavailable": type(error).__name__}

        leaves: dict[str, object] = {}
        regular = cwd / "regular.json"
        regular.write_bytes(sentinel)
        leaves["regular"] = {**run_cli(validator, cwd, regular.name, root),
                             "preserved": regular.read_bytes() == sentinel}
        directory_leaf = cwd / "directory.json"
        directory_leaf.mkdir()
        leaves["directory"] = {**run_cli(validator, cwd, directory_leaf.name, root),
                               "preserved": directory_leaf.is_dir()}
        hard_target = cwd / "hard-target.json"
        hard_target.write_bytes(sentinel)
        hard_leaf = cwd / "hardlink.json"
        os.link(hard_target, hard_leaf)
        leaves["hardlink"] = {**run_cli(validator, cwd, hard_leaf.name, root),
                              "preserved": hard_leaf.read_bytes() == sentinel
                              and hard_target.read_bytes() == sentinel}
        valid_target = cwd / "valid-target.json"
        valid_target.write_bytes(sentinel)
        valid_link = cwd / "valid-link.json"
        broken_link = cwd / "broken-link.json"
        try:
            os.symlink(valid_target, valid_link)
            os.symlink(cwd / "never-created.json", broken_link)
            leaves["valid_symlink"] = {
                **run_cli(validator, cwd, valid_link.name, root),
                "entry_preserved": valid_link.is_symlink(),
                "target_preserved": valid_target.read_bytes() == sentinel,
            }
            leaves["broken_symlink"] = {
                **run_cli(validator, cwd, broken_link.name, root),
                "entry_preserved": os.path.lexists(broken_link),
                "target_not_created": not (cwd / "never-created.json").exists(),
            }
        except (OSError, NotImplementedError) as error:
            leaves["symlinks"] = {"platform_unavailable": type(error).__name__}
        results["D_preexisting_leaves"] = leaves

        marker = root / "race-ready"
        race_leaf = cwd / "race.json"
        race_process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child",
             "--validator", str(validator), "--output", race_leaf.name,
             "--marker", str(marker)],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        wait_marker(marker)
        race_leaf.write_bytes(sentinel)
        stdout, stderr = race_process.communicate(timeout=15)
        decoded = json.loads(stdout)
        results["E_exclusive_race"] = {
            "exit": race_process.returncode,
            "canonical_fail": decoded.get("validation_status") == "FAIL"
            and decoded.get("aggregate") is None,
            "hit_FileExistsError": "FileExistsError"
            in decoded.get("errors", [""])[0],
            "racing_entry_preserved": race_leaf.read_bytes() == sentinel,
            "stderr_empty": stderr == "",
        }

        original = root / "original-cwd"
        moved = root / "moved-cwd"
        victim = root / "victim"
        original.mkdir()
        victim.mkdir()
        ancestor_marker = root / "ancestor-ready"
        ancestor_process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child",
             "--validator", str(validator), "--output", "ancestor.json",
             "--marker", str(ancestor_marker)],
            cwd=original, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        wait_marker(ancestor_marker)
        try:
            original.rename(moved)
            os.symlink(victim, original, target_is_directory=True)
            stdout, stderr = ancestor_process.communicate(timeout=15)
            decoded = json.loads(stdout)
            results["F_ancestor_swap"] = {
                "platform_unavailable": False,
                "exit": ancestor_process.returncode,
                "canonical_fail": decoded.get("validation_status") == "FAIL"
                and decoded.get("aggregate") is None,
                "created_in_bound_original": (moved / "ancestor.json").is_file(),
                "not_redirected_to_victim": not (victim / "ancestor.json").exists(),
                "stderr_empty": stderr == "",
            }
        except (OSError, NotImplementedError) as error:
            stdout, stderr = ancestor_process.communicate(timeout=15)
            results["F_ancestor_swap"] = {
                "platform_unavailable": type(error).__name__,
                "reason": str(error)[:200],
                "child_exit": ancestor_process.returncode,
                "child_stderr_empty": stderr == "",
            }

    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--marker", type=Path)
    args = parser.parse_args()
    if args.child:
        if args.output is None or args.marker is None:
            parser.error("--child requires --output and --marker")
        raise SystemExit(child(args.validator, args.output, args.marker))
    raise SystemExit(main(args.validator))
