"""Single canonical commitment implementation shared by every R3/R4 component."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical ASCII JSON with exactly one trailing LF."""

    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def rows_commitment(rows: Any) -> str:
    return sha256_bytes(canonical_json_bytes(rows))
