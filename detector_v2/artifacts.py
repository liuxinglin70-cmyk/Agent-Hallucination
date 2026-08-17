"""Self-evolution deployment interface (detector side).

Only ``prompt_directive`` is executable today. The interface is intentional:
future artifact kinds must be added here together with their executor, so a
deployable payload can never have runtime semantics that do not exist.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector_v2.packet import packet_features

ARTIFACT_REGISTRY_SCHEMA = "detector_v2.artifact_registry.v1"

ARTIFACT_KINDS: dict[str, str] = {
    "prompt_directive": "stage_prompt",
}

PROMPT_STAGES = frozenset({"judge", "defense"})

PACKET_FEATURE_NAMES = frozenset({
    "has_tool_calls",
    "has_retrieval_tool",
    "has_answer_tool",
    "has_numeric_content",
    "has_long_prose",
    "multi_turn_question",
    "question_has_numbers",
})


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"artifact field {field} must be a non-empty string")
    return text


def validate_prompt_directive(entry: Mapping[str, Any]) -> None:
    artifact_id = _require_text(entry.get("artifact_id"), "artifact_id")
    trigger = entry.get("trigger")
    trigger = trigger if isinstance(trigger, Mapping) else {}
    features = {
        str(name) for name in trigger.get("packet_features") or ()
    }
    if not features or not features <= PACKET_FEATURE_NAMES:
        raise ValueError(
            f"{artifact_id}: trigger.packet_features must be a non-empty "
            "subset of the packet-feature vocabulary"
        )
    stages = {
        str(name) for name in trigger.get("stages") or ("judge",)
    }
    if not stages or not stages <= PROMPT_STAGES:
        raise ValueError(
            f"{artifact_id}: trigger.stages must be a non-empty subset of "
            f"{sorted(PROMPT_STAGES)}"
        )
    intervention = entry.get("intervention")
    intervention = intervention if isinstance(intervention, Mapping) else {}
    _require_text(intervention.get("directive"), f"{artifact_id}.intervention.directive")
    _require_text(intervention.get("exclusion"), f"{artifact_id}.intervention.exclusion")


def validate_artifact(entry: Mapping[str, Any]) -> None:
    artifact_id = _require_text(entry.get("artifact_id"), "artifact_id")
    kind = _require_text(entry.get("kind"), f"{artifact_id}.kind")
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"{artifact_id}: kind {kind!r} has no executor in detector_v2"
        )
    if kind == "prompt_directive":
        validate_prompt_directive(entry)


def registry_hash(payload: Mapping[str, Any]) -> str:
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    canonical = json.dumps(
        hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_registry(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("artifact registry must be an object")
    if payload.get("schema_version") != ARTIFACT_REGISTRY_SCHEMA:
        raise ValueError("unknown artifact registry schema")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("artifact registry must contain an artifacts list")
    persisted = str(payload.get("content_hash") or "")
    if persisted and persisted != registry_hash(payload):
        raise ValueError("artifact registry content_hash does not match")
    for entry in artifacts:
        if not isinstance(entry, Mapping):
            raise ValueError("artifact registry entries must be objects")
        validate_artifact(entry)
    return [dict(entry) for entry in artifacts]


def load_artifact_registry(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate_registry(payload)
    return payload


class DeployPlan:
    """One validated registry, compiled once."""

    def __init__(self, artifacts: Sequence[Mapping[str, Any]]) -> None:
        self.artifacts = [dict(artifact) for artifact in artifacts]
        self.prompts: list[dict[str, Any]] = []
        for artifact in self.artifacts:
            if artifact.get("kind") != "prompt_directive":
                continue
            trigger = artifact.get("trigger") or {}
            intervention = artifact.get("intervention") or {}
            self.prompts.append({
                "artifact_id": str(artifact.get("artifact_id")),
                "stages": {
                    str(name) for name in trigger.get("stages") or ("judge",)
                },
                "trigger_features": {
                    str(name) for name in trigger.get("packet_features") or ()
                },
                "directive": str(intervention.get("directive") or ""),
                "exclusion": str(intervention.get("exclusion") or ""),
            })

    def prompt_block(
        self,
        features: Mapping[str, Any] | set[str],
        stage: str,
        hook: str | None = None,
    ) -> str:
        feature_set = set(features)
        routed = [
            entry for entry in self.prompts
            if stage in entry["stages"]
            and entry["trigger_features"] <= feature_set
        ]
        if not routed:
            return ""
        lines = [
            "\nDEPLOYED DIRECTIVES for this case (apply exactly as stated; "
            "NOT-WHEN is the boundary, never an optional hint):"
        ]
        for entry in routed:
            lines.append(
                f"- {entry['directive']} NOT WHEN {entry['exclusion']}"
            )
        return "\n" + "\n".join(lines)

    # No-op intervention hooks so the legacy plan can stand in where stage
    # code now expects a RuntimePlan (backward compatibility).
    def evidence_for(
        self,
        hook: str,
        packet: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def guard_actions(
        self,
        packet: Mapping[str, Any],
        facts: Mapping[str, Any] | Sequence[str] | None = None,
    ) -> list[str]:
        return []

    def deployment_summary(self) -> dict[str, Any]:
        return {
            "intervention_registry_hash": "",
            "active_interventions": [],
            "fired": [],
            "evidence": [],
            "guard_actions": [],
            "policy_conflict": False,
        }


def compile_deploy_plan(registry: Mapping[str, Any] | None) -> DeployPlan | None:
    if registry is None:
        return None
    return DeployPlan(validate_registry(registry))


def empty_plan() -> DeployPlan:
    return DeployPlan([])


__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_REGISTRY_SCHEMA",
    "DeployPlan",
    "PACKET_FEATURE_NAMES",
    "PROMPT_STAGES",
    "compile_deploy_plan",
    "empty_plan",
    "load_artifact_registry",
    "packet_features",
    "registry_hash",
    "validate_registry",
]
