"""The compiled deployment plan: validate once, consume everywhere.

A registry payload is raw dictionaries; every stage used to re-scan
and re-filter it on every call. The plan is the single compilation of
one registry into the shapes its consumers actually use, keyed by the
registry's content hash so repeated detections reuse the compiled
result instead of re-validating raw entries:

    registry payload -> compile_deploy_plan() -> DeployPlan
        .prompts      prompt directives in the renderer's shape
        .predicates   evidence predicates (trigger compiled to a set)
        .generators   candidate generators (trigger compiled to a set)
        .policy_actions / .policy_preconditions   adjudication tables

The plan is immutable; the detector's execution points only ever read
from it.
"""

from __future__ import annotations

import threading
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.artifacts import (
    policy_actions_of,
    policy_preconditions_of,
    validate_registry,
)

_PLAN_CACHE: dict[str, "DeployPlan"] = {}
_CACHE_LOCK = threading.Lock()


class DeployPlan:
    """One registry, compiled. Consumers read attributes, never scan."""

    __slots__ = (
        "artifacts_hash", "artifacts", "prompts", "predicates",
        "generators", "policy_actions", "policy_preconditions",
        "policy_ids",
    )

    def __init__(self, artifacts: Sequence[Mapping[str, Any]]):
        self.artifacts = [dict(artifact) for artifact in artifacts]
        self.artifacts_hash = ""
        self.prompts: list[dict[str, Any]] = []
        self.predicates: list[dict[str, Any]] = []
        self.generators: list[dict[str, Any]] = []
        self.policy_actions: dict[str, str] = {}
        self.policy_preconditions: dict[str, Mapping[str, Any]] = {}
        self.policy_ids: list[str] = []
        policies = []
        for artifact in self.artifacts:
            kind = str(artifact.get("kind") or "")
            if kind == "prompt_directive":
                trigger = artifact.get("trigger") or {}
                intervention = artifact.get("intervention") or {}
                self.prompts.append({
                    "convention_id": str(artifact.get("artifact_id")),
                    "trigger_features": set(
                        str(name)
                        for name in trigger.get("packet_features") or ()
                    ),
                    "directive": str(intervention.get("directive") or ""),
                    "exclusion": str(intervention.get("exclusion") or ""),
                })
            elif kind == "evidence_predicate":
                self.predicates.append(dict(artifact))
            elif kind == "candidate_generator":
                self.generators.append(dict(artifact))
            elif kind == "adjudication_policy":
                policies.append(dict(artifact))
                self.policy_ids.append(str(artifact.get("artifact_id")))
        self.policy_actions = policy_actions_of(policies)
        self.policy_preconditions = policy_preconditions_of(policies)

    def routed_prompts(self, features: Mapping[str, Any]) -> list[dict]:
        """Prompt directives whose trigger fully matches the packet."""
        feature_set = set(features)
        return [
            entry for entry in self.prompts
            if entry["trigger_features"]
            and entry["trigger_features"] <= feature_set
        ]

    def routed_predicates(
        self, features: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Evidence predicates whose trigger fully matches the packet."""
        feature_set = set(features)
        routed = []
        for artifact in self.predicates:
            wanted = {
                str(name)
                for name in (
                    (artifact.get("trigger") or {}).get("packet_features")
                    or ()
                )
            }
            if wanted and wanted <= feature_set:
                routed.append(artifact)
        return routed

    def routed_generators(
        self, features: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Candidate generators whose trigger fully matches the packet."""
        feature_set = set(features)
        routed = []
        for artifact in self.generators:
            wanted = {
                str(name)
                for name in (
                    (artifact.get("trigger") or {}).get("packet_features")
                    or ()
                )
            }
            if wanted and wanted <= feature_set:
                routed.append(artifact)
        return routed


def compile_deploy_plan(
    registry: Mapping[str, Any] | None,
) -> DeployPlan | None:
    """Validate one registry payload once and return its compiled plan.

    Compilation is memoised on the registry's ``content_hash``: the
    same deployed registry costs one validation no matter how many
    detections run against it. A payload without a hash (ad-hoc
    one-artifact replays) is validated and compiled every call —
    correct, just uncached.
    """
    if registry is None:
        return None
    artifacts = validate_registry(registry)
    cache_key = str(registry.get("content_hash") or "")
    if cache_key:
        with _CACHE_LOCK:
            plan = _PLAN_CACHE.get(cache_key)
            if plan is not None:
                return plan
    plan = DeployPlan(artifacts)
    plan.artifacts_hash = cache_key
    if cache_key:
        with _CACHE_LOCK:
            _PLAN_CACHE[cache_key] = plan
    return plan


__all__ = [
    "DeployPlan",
    "compile_deploy_plan",
]
