"""Complete evolution-state manifest for cross-run inheritance.

A completed run binds its three resumable artifacts (L1 Skill genome,
adjudication-policy genome, experience memory) by sha256 in one manifest so
a new data stream can inherit the full state atomically.  Inheritance is
strict: a manifest whose paths are missing or whose hashes do not match is
rejected before anything is loaded, and explicit caller parameters that
conflict with the manifest are refused.

This is distinct from ``--resume-from``: resume continues the same input
queue from a checkpoint; inheritance starts a new case stream with the
previous run's final genome, policy and memory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

MANIFEST_SCHEMA_VERSION = "sehd.evolution_state.v1"
MANIFEST_NAME = "evolution_state_manifest.json"

_ARTIFACT_KEYS = (
    "provider_rule_genome",
    "adjudication_policy_genome",
    "experience_memory",
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_state_manifest(
    output_dir: Path,
    *,
    run_id: str,
    lineage: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Bind the final artifacts of a completed run by hash and write the
    manifest next to them.  Raises FileNotFoundError when any artifact is
    missing, so an incomplete run can never leave a valid manifest."""
    output_dir = Path(output_dir)
    artifact_paths = {
        key: output_dir / name
        for key, name in (
            ("provider_rule_genome", "champion_genome.json"),
            ("adjudication_policy_genome", "policy_genome.json"),
            ("experience_memory", "htc_tes_experience_memory.json"),
        )
    }
    missing = [
        str(path)
        for path in artifact_paths.values()
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "cannot write evolution state manifest, artifacts missing: "
            + ", ".join(missing)
        )
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_run_id": str(run_id or ""),
        "lineage": [
            {
                "run_id": str(entry.get("run_id") or ""),
                "dir": str(entry.get("dir") or ""),
            }
            for entry in lineage
        ],
    }
    for key, path in artifact_paths.items():
        manifest[key] = {
            "path": path.name,
            "sha256": _file_sha256(path),
        }
    destination = output_dir / MANIFEST_NAME
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def load_state_manifest(manifest_dir: Path) -> dict[str, Any]:
    """Load and fully validate a completed run's state manifest.

    Raises FileNotFoundError when the manifest or any bound artifact is
    missing, and ValueError when the schema or any artifact hash does not
    match.  Returns the manifest with absolute artifact paths resolved.
    """
    manifest_dir = Path(manifest_dir)
    manifest_path = manifest_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no evolution state manifest found in {manifest_dir}"
        )
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("evolution state manifest must contain an object")
    schema = str(manifest.get("schema_version") or "")
    if schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported evolution state schema_version: {schema!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION!r})"
        )
    for key in _ARTIFACT_KEYS:
        entry = manifest.get(key)
        if not isinstance(entry, Mapping):
            raise ValueError(f"manifest artifact entry missing: {key}")
        expected = str(entry.get("sha256") or "")
        if not expected:
            raise ValueError(f"manifest artifact entry lacks sha256: {key}")
        raw_path = str(entry.get("path") or "")
        manifest_root = manifest_dir.resolve()
        resolved = (manifest_dir / raw_path).resolve()
        if not resolved.is_relative_to(manifest_root):
            raise ValueError(
                f"manifest artifact path escapes the manifest directory: "
                f"{raw_path}"
            )
        if not resolved.is_file():
            raise FileNotFoundError(
                f"manifest artifact missing: {resolved}"
            )
        actual = _file_sha256(resolved)
        if actual != expected:
            raise ValueError(
                f"manifest artifact hash mismatch: {resolved} "
                f"(expected {expected[:12]}..., got {actual[:12]}...)"
            )
        manifest[key] = {
            "path": str(resolved),
            "sha256": expected,
        }
    return manifest
