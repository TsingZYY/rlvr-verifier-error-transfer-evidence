"""Run process-level CPU/static attacks against the R10 cwd-bound output contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
VALIDATOR = HERE / "validate_eight_stack_completion.py"


def command(output: str, master: Path) -> list[str]:
    return [
        sys.executable,
        str(VALIDATOR),
        "--master-inclusion-contract",
        str(master),
        "--expected-master-sha256",
        "0" * 64,
        "--result-anchor",
        str(master.parent / "missing-anchor.json"),
        "--expected-result-anchor-sha256",
        "1" * 64,
        "--validation-report",
        str(master.parent / "missing-report.json"),
        "--expected-validation-report-sha256",
        "2" * 64,
        "--output",
        output,
    ]


def run_case(cwd: Path, output: str, master: Path) -> dict:
    result = subprocess.run(
        command(output, master), cwd=cwd, capture_output=True, text=True, timeout=30
    )
    decoded = json.loads(result.stdout)
    return {
        "exit_code": result.returncode,
        "stdout_is_single_fail_json": decoded.get("validation_status") == "FAIL"
        and decoded.get("aggregate") is None,
        "error": decoded.get("errors", [None])[0],
        "stderr_empty": result.stderr == "",
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cwd = root / "cwd"
        outside = root / "outside"
        cwd.mkdir()
        outside.mkdir()
        missing_master = root / "missing-master.json"
        results: dict[str, object] = {}

        fresh = run_case(cwd, "fresh.json", missing_master)
        fresh_path = cwd / "fresh.json"
        fresh["created_in_cwd"] = fresh_path.is_file()
        fresh["file_is_canonical_fail_json"] = (
            json.loads(fresh_path.read_bytes()).get("validation_status") == "FAIL"
        )
        results["fresh_leaf"] = fresh

        outside_path = outside / "escape.json"
        outside_case = run_case(cwd, str(outside_path), missing_master)
        outside_case["outside_not_created"] = not outside_path.exists()
        results["absolute_outside_cwd"] = outside_case

        for label, value, created in (
            ("nested_relative", "nested/escape.json", cwd / "nested" / "escape.json"),
            ("dotdot_relative", "../escape.json", root / "escape.json"),
        ):
            case = run_case(cwd, value, missing_master)
            case["outside_not_created"] = not created.exists()
            results[label] = case

        alias = root / "cwd-alias"
        try:
            os.symlink(cwd, alias, target_is_directory=True)
            alias_path = alias / "alias.json"
            case = run_case(cwd, str(alias_path), missing_master)
            case["alias_target_not_created"] = not (cwd / "alias.json").exists()
            results["cwd_symlink_alias"] = case
        except (OSError, NotImplementedError) as error:
            results["cwd_symlink_alias"] = {
                "platform_unavailable": type(error).__name__
            }

        sentinel = b"DO-NOT-OVERWRITE\n"
        leaf_states: dict[str, object] = {}
        regular = cwd / "regular.json"
        regular.write_bytes(sentinel)
        leaf_states["regular"] = {
            **run_case(cwd, regular.name, missing_master),
            "preserved": regular.read_bytes() == sentinel,
        }
        directory_leaf = cwd / "directory.json"
        directory_leaf.mkdir()
        leaf_states["directory"] = {
            **run_case(cwd, directory_leaf.name, missing_master),
            "preserved": directory_leaf.is_dir(),
        }
        hard_target = cwd / "hard-target.json"
        hard_target.write_bytes(sentinel)
        hard_leaf = cwd / "hardlink.json"
        os.link(hard_target, hard_leaf)
        leaf_states["hardlink"] = {
            **run_case(cwd, hard_leaf.name, missing_master),
            "preserved": hard_leaf.read_bytes() == sentinel
            and hard_target.read_bytes() == sentinel,
        }
        symlink_target = cwd / "symlink-target.json"
        symlink_target.write_bytes(sentinel)
        symlink_leaf = cwd / "symlink.json"
        broken_leaf = cwd / "broken.json"
        try:
            os.symlink(symlink_target, symlink_leaf)
            os.symlink(cwd / "missing-target.json", broken_leaf)
            leaf_states["valid_symlink"] = {
                **run_case(cwd, symlink_leaf.name, missing_master),
                "entry_preserved": symlink_leaf.is_symlink(),
                "target_preserved": symlink_target.read_bytes() == sentinel,
            }
            leaf_states["broken_symlink"] = {
                **run_case(cwd, broken_leaf.name, missing_master),
                "entry_preserved": os.path.lexists(broken_leaf),
                "target_not_created": not (cwd / "missing-target.json").exists(),
            }
        except (OSError, NotImplementedError) as error:
            leaf_states["symlinks"] = {"platform_unavailable": type(error).__name__}
        results["preexisting_exact_leaf"] = leaf_states

        large_master = root / "large-master.json"
        with large_master.open("wb") as handle:
            handle.truncate(256 * 1024 * 1024)
        race_leaf = cwd / "race.json"
        race_process = subprocess.Popen(
            command(race_leaf.name, large_master),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.12)
        race_leaf.write_bytes(sentinel)
        race_stdout, race_stderr = race_process.communicate(timeout=30)
        race_decoded = json.loads(race_stdout)
        results["exclusive_leaf_race"] = {
            "exit_code": race_process.returncode,
            "stdout_is_single_fail_json": race_decoded.get("validation_status") == "FAIL"
            and race_decoded.get("aggregate") is None,
            "error": race_decoded.get("errors", [None])[0],
            "stderr_empty": race_stderr == "",
            "racing_entry_preserved": race_leaf.read_bytes() == sentinel,
            "hit_exclusive_open": "FileExistsError"
            in race_decoded.get("errors", [""])[0],
        }

        original = root / "original-cwd"
        moved = root / "moved-cwd"
        victim = root / "victim"
        original.mkdir()
        victim.mkdir()
        ancestor_process = subprocess.Popen(
            command("ancestor.json", large_master),
            cwd=original,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.12)
        try:
            original.rename(moved)
            os.symlink(victim, original, target_is_directory=True)
            ancestor_stdout, ancestor_stderr = ancestor_process.communicate(timeout=30)
            ancestor_decoded = json.loads(ancestor_stdout)
            results["ancestor_swap"] = {
                "platform_unavailable": False,
                "exit_code": ancestor_process.returncode,
                "stdout_is_single_fail_json": ancestor_decoded.get("validation_status")
                == "FAIL"
                and ancestor_decoded.get("aggregate") is None,
                "stderr_empty": ancestor_stderr == "",
                "created_in_bound_original_directory": (moved / "ancestor.json").is_file(),
                "not_redirected_to_symlink_target": not (victim / "ancestor.json").exists(),
            }
        except (OSError, NotImplementedError) as error:
            ancestor_process.terminate()
            ancestor_process.communicate(timeout=30)
            results["ancestor_swap"] = {
                "platform_unavailable": type(error).__name__,
                "reason": str(error)[:200],
            }

        print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
