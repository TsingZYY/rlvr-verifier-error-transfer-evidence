"""Run one command and write deterministic stdout/stderr/hash receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--stdout", required=True, type=Path)
    parser.add_argument("--stderr", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    completed = subprocess.run(command, capture_output=True, check=False)
    for path in (args.receipt, args.stdout, args.stderr):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.stdout.write_bytes(completed.stdout)
    args.stderr.write_bytes(completed.stderr)
    receipt_argv = list(command)
    if receipt_argv and Path(receipt_argv[0]).is_absolute():
        executable_name = Path(receipt_argv[0]).name.lower()
        receipt_argv[0] = "python" if executable_name in {
            "python",
            "python.exe",
            "python3",
            "python3.exe",
        } else executable_name
    receipt = {
        "argv": receipt_argv,
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout),
        "stderr_sha256": sha256_bytes(completed.stderr),
    }
    args.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    sys.stdout.buffer.write(completed.stdout)
    sys.stderr.buffer.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
