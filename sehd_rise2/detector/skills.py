"""Evolved-capability loading interface (explicit, fail-closed).

The detector treats genomes as OPAQUE payloads: it loads and verifies them,
records them in provenance, and defers all execution semantics to the
future evolution framework. Loading is explicit (no framework-global
state) and fail-closed: a malformed payload or a hash mismatch raises
instead of silently falling back to the baseline detector.

Structural contract (minimal, closed):
- skill genome: an object with a ``modules`` object;
- policy genome: an object with an ``items`` list;
- both: an optional ``content_hash`` that must match the canonical hash of
  the payload with the ``content_hash`` field removed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical_text(data: Any) -> str:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def load_genome_payload(
    path: str | Path, *, kind: str, expected_hash: str | None = None,
) -> dict[str, Any]:
    """Load one persisted genome payload and verify its integrity."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{kind} genome file must contain an object")
    if kind == "skill" and not isinstance(payload.get("modules"), dict):
        raise ValueError("skill genome must contain a modules object")
    if kind == "policy" and not isinstance(payload.get("items"), list):
        raise ValueError("policy genome must contain an items list")
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    computed = hashlib.sha256(
        _canonical_text(hashable).encode("utf-8"),
    ).hexdigest()
    persisted = str(payload.get("content_hash") or "")
    if persisted and persisted != computed:
        raise ValueError(
            f"persisted {kind} genome content_hash does not match content"
        )
    if expected_hash is not None and computed != expected_hash:
        raise ValueError(
            f"loaded {kind} genome hash does not match expected_hash"
        )
    return payload


def load_skill_genome(
    path: str | Path, *, expected_hash: str | None = None,
) -> dict[str, Any]:
    """Explicitly load and verify one persisted L1 skill genome (opaque)."""
    return load_genome_payload(
        path, kind="skill", expected_hash=expected_hash,
    )


def load_policy_genome(
    path: str | Path, *, expected_hash: str | None = None,
) -> dict[str, Any]:
    """Explicitly load and verify one persisted L2 policy genome (opaque)."""
    return load_genome_payload(
        path, kind="policy", expected_hash=expected_hash,
    )


__all__ = [
    "load_genome_payload",
    "load_policy_genome",
    "load_skill_genome",
]
