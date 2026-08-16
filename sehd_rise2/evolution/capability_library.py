"""Persistent capability library: registry merge + status ledger.

A self-improving system needs a death process as well as a birth
process. The registry file accumulates what rounds earned; this ledger
tracks per-artifact status across rounds:

    born -> active -> held -> (active again | retired)

A first hold keeps the artifact alive but out of the round's deploy
arm; a second consecutive hold — or a hold whose screening reason is an
attributed true-positive loss — retires the artifact and removes it
from the registry so it cannot become a permanent prompt zoo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.artifacts import (
    ARTIFACT_REGISTRY_SCHEMA,
    registry_hash,
    validate_registry,
)

STATE_SCHEMA = "sehd2.capability_library_state.v1"
RETIRE_ON_REASON_MARKERS = ("true positive", "true-positive", "recovers a true positive")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "artifacts": {},
        "events": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    if not Path(path).exists():
        state = empty_state()
        _write_json(Path(path), state)
        return state
    state = _read_json(Path(path))
    if state.get("schema_version") != STATE_SCHEMA:
        raise ValueError(f"{path}: unknown capability library state schema")
    return state


def _entry(state: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    state["artifacts"].setdefault(
        artifact_id, {"status": "active", "held_rounds": 0, "events": []},
    )
    return state["artifacts"][artifact_id]


def record_promotions(
    *, state_path: Path, artifact_ids: Sequence[str], round_no: int,
) -> dict[str, Any]:
    """Mark the artifacts this round's G2 promoted as born/active."""
    state = load_state(state_path)
    for artifact_id in artifact_ids:
        entry = _entry(state, artifact_id)
        if entry["status"] not in {"active", "retired"}:
            entry["status"] = "active"
            entry["held_rounds"] = 0
        entry.setdefault("born_round", round_no)
        entry["events"].append({"round": round_no, "event": "promoted"})
    state["events"].append({
        "round": round_no, "event": "promotions",
        "artifact_ids": sorted(set(artifact_ids)),
    })
    _write_json(Path(state_path), state)
    return state


def active_artifact_ids(
    *, registry: Mapping[str, Any], state_path: Path,
) -> set[str]:
    """Artifact ids present in the registry and not retired in the ledger."""
    state = load_state(state_path)
    retired = {
        artifact_id for artifact_id, entry in state["artifacts"].items()
        if entry.get("status") == "retired"
    }
    return {
        str(artifact.get("artifact_id"))
        for artifact in registry.get("artifacts") or []
        if str(artifact.get("artifact_id")) not in retired
    }


def apply_screening(
    *, library_path: Path, state_path: Path,
    held_ids: Sequence[str], reasons: Mapping[str, str], round_no: int,
) -> dict[str, Any]:
    """Apply one deployment screen to the persistent library.

    ``held_ids`` are artifacts the screen excluded from this round's
    deploy arm. A hold is not immediately fatal, but two consecutive
    holds are. A hold attributed to a recovered true positive retires
    the artifact immediately.
    """
    library_path = Path(library_path)
    registry = validate_registry(_read_json(library_path))
    state = load_state(state_path)
    held = set(str(artifact_id) for artifact_id in held_ids)
    retired: list[str] = []
    held_now: list[str] = []
    for artifact in registry["artifacts"]:
        artifact_id = str(artifact.get("artifact_id"))
        entry = _entry(state, artifact_id)
        if artifact_id not in held:
            entry["status"] = "active"
            entry["held_rounds"] = 0
            entry["events"].append({
                "round": round_no, "event": "screen_passed",
            })
            continue
        reason = str(reasons.get(artifact_id) or "")
        entry["held_rounds"] = int(entry.get("held_rounds") or 0) + 1
        retire_now = (
            entry["held_rounds"] >= 2
            or any(marker in reason for marker in RETIRE_ON_REASON_MARKERS)
        )
        if retire_now:
            entry["status"] = "retired"
            entry["events"].append({
                "round": round_no, "event": "retired", "reason": reason,
            })
            retired.append(artifact_id)
        else:
            entry["status"] = "held"
            entry["events"].append({
                "round": round_no, "event": "held", "reason": reason,
            })
            held_now.append(artifact_id)
    if retired:
        keep = [
            dict(artifact) for artifact in registry["artifacts"]
            if str(artifact.get("artifact_id")) not in retired
        ]
        payload = {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA,
            "artifacts": keep,
        }
        payload["content_hash"] = registry_hash(payload)
        _write_json(library_path, payload)
    state["events"].append({
        "round": round_no, "event": "screen_applied",
        "held": sorted(held_now), "retired": sorted(retired),
    })
    _write_json(Path(state_path), state)
    return state


def load_registry(path: Path) -> dict[str, Any]:
    """Read and schema-check one persisted artifact registry."""
    registry = _read_json(Path(path))
    if not isinstance(registry, dict) or registry.get(
        "schema_version"
    ) != ARTIFACT_REGISTRY_SCHEMA:
        raise ValueError(f"{path}: not an artifact registry")
    return registry


def merge_round_registry(
    library_path: Path, round_registry: Mapping[str, Any], round_no: int,
) -> dict[str, Any]:
    """Fold one round's deployable artifacts into the persistent library.

    The library is the accumulating memory of the evolution channel:
    each round's deployables are namespaced ``R<round>-<id>`` and
    replace that round's previous block (idempotent re-runs), so the
    deployed detector carries every artifact any round ever earned.
    """
    prefix = f"R{round_no}-"
    existing = []
    if Path(library_path).exists():
        existing = [
            artifact for artifact in validate_registry(
                load_registry(Path(library_path))
            )
            if not str(artifact["artifact_id"]).startswith(prefix)
        ]
    incoming = validate_registry(dict(round_registry))
    namespaced = []
    for artifact in incoming:
        artifact = dict(artifact)
        artifact["artifact_id"] = prefix + str(artifact["artifact_id"])
        namespaced.append(artifact)
    payload: dict[str, Any] = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": existing + namespaced,
    }
    payload["content_hash"] = registry_hash(payload)
    _write_json(Path(library_path), payload)
    return payload


def filter_registry_by_state(
    registry: Mapping[str, Any], state_path: Path,
) -> list[dict[str, Any]]:
    active = active_artifact_ids(registry=registry, state_path=state_path)
    return [
        dict(artifact) for artifact in registry.get("artifacts") or []
        if str(artifact.get("artifact_id")) in active
    ]


__all__ = [
    "STATE_SCHEMA",
    "active_artifact_ids",
    "apply_screening",
    "empty_state",
    "filter_registry_by_state",
    "load_registry",
    "load_state",
    "merge_round_registry",
    "record_promotions",
]
