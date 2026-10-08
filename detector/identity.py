"""Canonical content digests and the inputs that identify one detector execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

EXECUTION_IDENTITY_SCHEMA = "detector.execution_identity.v1"


def digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_identity(*packages: str) -> str:
    root = Path(__file__).resolve().parent.parent
    values = {}
    for package in packages or ("detector",):
        for path in sorted((root / package).rglob("*.py")):
            if "tests" not in path.parts and "__pycache__" not in path.parts:
                values[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(values)


def artifact_digest(path: str | Path) -> str:
    target = Path(path)
    if target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    if target.is_dir():
        return digest({
            item.relative_to(target).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(target.rglob("*"))
            if item.is_file() and "__pycache__" not in item.parts
        })
    raise ValueError(f"declared artifact does not exist: {target}")


def build_execution_identity(
    *, source: str, config: Mapping[str, Any], record: Mapping[str, Any],
    packet: Mapping[str, Any], evidence: Mapping[str, Any] | None = None,
    registry: Mapping[str, Any] | None = None, replay: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = (config.get("causal_audit") or {}).get("scorer_artifact")
    payload = {
        "schema_version": EXECUTION_IDENTITY_SCHEMA,
        "source": source, "config": digest(config), "record": digest(record),
        "packet": digest(packet), "evidence": digest(evidence),
        "registry": digest(registry), "replay": digest(replay),
        "artifacts": {"scorer": artifact_digest(artifact)} if artifact is not None else {},
    }
    return {**payload, "digest": digest(payload)}
