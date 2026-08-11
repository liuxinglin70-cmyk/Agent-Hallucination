"""Canonical JSON serialization, content hashes, and atomic JSON writes.

Deterministic serialization is the integrity anchor for packets, checkpoint
payloads, output manifests, and provider-request caching: every consumer must
hash and compare the same byte shape, so the canonical form lives here alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    """Write a restart/audit artifact without exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Canonical content hash of an embedded checkpoint payload.

    The checkpoint memory anchor is a plain dict (not a genome with its own
    content_hash), so its integrity is pinned with the same canonical hash the
    payloads themselves use at write time.
    """
    return stable_hash(payload)
