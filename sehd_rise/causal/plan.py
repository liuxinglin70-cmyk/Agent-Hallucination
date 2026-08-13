"""Canonical RepairPlan protocol: the single repair-record both skills share.

Plan §2.7 replaces the per-layer repair story with a unified protocol:

    RepairPlan
    |-- failure identity (episode_id)
    |-- layer (L1 or L2)
    |-- owner
    |-- target causal edge (the FailureEdge the plan would lift)
    |-- executor (L1_COURT_TRANSFORM for L1, ADJUDICATION_POLICY for L2)
    |-- legal operations (the closed opcode/action set the plan may propose)
    |-- effect contract (what the operator must change in the next detection)
    |-- evaluation contract (how the next detection measures the change)

L1 and L2 keep their novelty (post-court rule vs adjudication-policy) but share
this protocol so the planning/execution/evaluation layers downstream do not
re-interpret repair semantics per layer. This module owns:

- the ``RepairPlan`` data model,
- the ``EXECUTOR_*`` closed vocabulary,
- ``build_repair_plan``: compile one ``RepairPlan`` per repair-frontier edge
  of a ``CausalFailureEpisode`` (Phase 4 only direction-agnostic check;
  Phase 6 narrows by evolution direction).

The framework compiles owner/route/output object/output field/legal
operations from the edge + the closed skills vocabulary; the reflection
model only chooses an edge and selects an operation from the legal enum,
never inventing execution semantics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.causal.episode import CausalFailureEpisode, FailureEdge
from sehd_rise.skills.capabilities import (
    L1_COURT_TRANSFORM_EXECUTOR,
    L1_CONSTRUCTIVE_HARNESS_EXECUTOR,
    L2_ADJUDICATION_POLICY_EXECUTOR,
    SKILL_CAPABILITY_REGISTRY,
)
from sehd_rise.skills.vocabulary.contracts import (
    LAYER_L1,
    LAYER_L2,
)


# ============================================================
# Executors (closed vocabulary)
# ============================================================

EXECUTOR_L1_COURT_TRANSFORM = L1_COURT_TRANSFORM_EXECUTOR
EXECUTOR_L1_CONSTRUCTIVE_HARNESS = L1_CONSTRUCTIVE_HARNESS_EXECUTOR
EXECUTOR_ADJUDICATION_POLICY = L2_ADJUDICATION_POLICY_EXECUTOR
EXECUTORS = frozenset({
    EXECUTOR_L1_COURT_TRANSFORM,
    EXECUTOR_L1_CONSTRUCTIVE_HARNESS,
    EXECUTOR_ADJUDICATION_POLICY,
})


# ============================================================
# RepairPlan
# ============================================================

@dataclass(frozen=True)
class RepairPlan:
    """One canonical repair plan targeting one causal failure edge.

    Fields are the §2.7 protocol surface. ``legal_operations`` is the closed
    opcode (L1) or action (L2) set the plan may propose; the reflection model
    picks one item from the enum and the framework re-evaluates deterministically.
    ``auto_selected_operation`` is set when exactly one operation can lift the
    target edge, so the model does not need to choose.

    The plan does NOT carry the proposal payload (rule text, rationale,
    mechanism hypothesis); that is the planning layer's contribution and
    lives in `planning/`. Here we only fix the execution contract the L1/L2
    paths are obliged to honour.
    """

    plan_id: str
    episode_id: str
    skill_layer: str  # LAYER_L1 or LAYER_L2
    owner: str
    target_causal_edge: FailureEdge
    executor: str  # registry executor identifier
    legal_operations: tuple[str, ...]
    effect_contract: Mapping[str, Any]
    evaluation_contract: Mapping[str, Any]
    target_domain: str = ""
    grounding_query: Mapping[str, Any] = field(default_factory=dict)
    required_binding_schema: Mapping[str, Any] = field(default_factory=dict)
    auto_selected_operation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "episode_id": self.episode_id,
            "skill_layer": self.skill_layer,
            "owner": self.owner,
            "target_causal_edge": self.target_causal_edge.to_dict(),
            "executor": self.executor,
            "legal_operations": list(self.legal_operations),
            "effect_contract": dict(self.effect_contract),
            "evaluation_contract": dict(self.evaluation_contract),
            "target_domain": self.target_domain,
            "grounding_query": dict(self.grounding_query),
            "required_binding_schema": dict(self.required_binding_schema),
            "auto_selected_operation": self.auto_selected_operation,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "RepairPlan":
        return cls(
            plan_id=str(value.get("plan_id") or ""),
            episode_id=str(value.get("episode_id") or ""),
            skill_layer=str(value.get("skill_layer") or ""),
            owner=str(value.get("owner") or ""),
            target_causal_edge=FailureEdge.from_dict(
                dict(value.get("target_causal_edge") or {})
            ),
            executor=str(value.get("executor") or ""),
            legal_operations=tuple(
                str(item)
                for item in (value.get("legal_operations") or ())
                if str(item)
            ),
            effect_contract=dict(value.get("effect_contract") or {}),
            evaluation_contract=dict(value.get("evaluation_contract") or {}),
            target_domain=str(value.get("target_domain") or ""),
            grounding_query=dict(value.get("grounding_query") or {}),
            required_binding_schema=dict(value.get("required_binding_schema") or {}),
            auto_selected_operation=str(value.get("auto_selected_operation") or ""),
        )


# ============================================================
# Direction-filtered operation sets
# ============================================================

def _direction_legal_operations(
    module: str, evolution_direction: str, failure_shape: str = "",
) -> tuple[str, ...]:
    """Return the exact capability-filtered legal operation set for an L1 module.

    A repair plan is legal only when the capability's declared
    ``applicable_failure_shapes`` contain the current ``failure_shape``; the
    weak module+direction match that predated this filter let a
    ``provider_artifact_missing`` cut borrow ``construct_candidate`` (declared
    for ``gold_false_negative_clean`` only) and report REPAIRABLE for a shape
    the registry never promised to repair. An empty ``failure_shape`` keeps the
    Phase 4 direction-only behavior (no shape filter).
    """
    return SKILL_CAPABILITY_REGISTRY.operations_for(
        skill_layer=LAYER_L1,
        modules=(module,),
        failure_shape=str(failure_shape or ""),
        direction=str(evolution_direction or ""),
    )


def _executor_for_layer(skill_layer: str) -> str:
    """Maps skill layer to its canonical executor (plan §2.7)."""
    if skill_layer == LAYER_L1:
        return EXECUTOR_L1_COURT_TRANSFORM
    if skill_layer == LAYER_L2:
        return EXECUTOR_ADJUDICATION_POLICY
    return ""


def _plan_id(episode_id: str, edge_id: str, skill_layer: str) -> str:
    payload = {
        "episode_id": episode_id,
        "edge_id": edge_id,
        "skill_layer": skill_layer,
    }
    text = __import__("json").dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "plan_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _l1_effect_contract(
    edge: FailureEdge, owner: str, capability: Any | None = None,
) -> dict[str, Any]:
    """The execution-side contract an L1 plan promises to satisfy.

    Cached deterministically from the edge: producer route, output object,
    field, observed/required postcondition, and the carrier the verdict
    traveled on (certificate_id source artifact) when present.
    """
    if capability is None:
        capabilities = SKILL_CAPABILITY_REGISTRY.for_module(owner)
        capability = capabilities[0] if capabilities else None
    return {
        "producer_route": (
            capability.route_ids[0]
            if capability and capability.route_ids else edge.producer_route
        ),
        "output_object": (
            capability.output_object if capability else edge.output_object
        ),
        "output_field": edge.field,
        "observed": edge.observed,
        "required": edge.required,
        "blocking_predicate": edge.blocking_predicate,
        "source_artifact_id": edge.source_artifact_id,
    }


def _l1_evaluation_contract(
    edge: FailureEdge, evolution_direction: str
) -> dict[str, Any]:
    """How the next detection should evaluate this plan.

    Phase 6 supplies a tighter evaluation surface; for Phase 4 the
    contract fixes the surface downstream phases are obliged to read.
    """
    return {
        "evolution_direction": str(evolution_direction or ""),
        "target_field": edge.field,
        "target_predicate": edge.blocking_predicate,
        "consumer": edge.consumer,
    }


def _l2_effect_contract(edge: FailureEdge, owner: str) -> dict[str, Any]:
    return {
        "policy_surface": owner,
        "output_object": edge.output_object,
        "output_field": edge.field,
        "observed": edge.observed,
        "required": edge.required,
        "blocking_predicate": edge.blocking_predicate,
    }


def _l2_evaluation_contract(edge: FailureEdge) -> dict[str, Any]:
    return {
        "policy_surface": edge.output_object,
        "target_predicate": edge.blocking_predicate,
        "terminal_state_consumer": edge.consumer,
    }


# ============================================================
# Build
# ============================================================

def build_repair_plan(
    episode: CausalFailureEpisode,
    *,
    allowed_modules: Sequence[str] = (),
    evolution_direction: str = "",
) -> tuple[RepairPlan, ...]:
    """Compile one ``RepairPlan`` per executable repair edge of the episode.

    The episode MUST be on an evolvable surface (skill_layer L1 or L2). Plan
    L1 mode routes via ``L1_COURT_TRANSFORM`` over registry capabilities;
    L2 mode routes via ``ADJUDICATION_POLICY`` over the L2 action vocabulary.

    For L1: each repair_frontier edge whose owner is in ``allowed_modules``
    and whose field is executable in the given direction produces one
    RepairPlan. The capability lookup is exact: ``failure_shape`` (the
    episode's ``reason_code``), owner, direction and target field must all
    match a registered capability, so a shape the registry never promised to
    repair compiles zero plans instead of borrowing a foreign operation.
    ``auto_selected_operation`` is set when exactly one operation can lift
    the edge.

    For L2: the episode carries a single synthetic edge for the L2 surface;
    its legal_operations is the closed L2 action set the policy may propose.
    Phase 6 narrows this with the candidate's `params` whitelist.

    Edges whose owner is not in ``allowed_modules`` or whose field is not
    executable in the given direction are skipped: they cannot compile a
    plan. The empty tuple returned from this function is the C2 invariant
    witness the repairability authority consumes to dispatch
    ``UNADDRESSABLE_FRAMEWORK_DEFECT``.
    """
    if episode.skill_layer not in (LAYER_L1, LAYER_L2):
        return ()
    legal_module_set = {str(value) for value in allowed_modules}
    plans: list[RepairPlan] = []

    if episode.skill_layer == LAYER_L1:
        failure_shape = str(getattr(episode, "reason_code", "") or "")
        for edge in episode.causal_failure_edge_frontier:
            owner = str(edge.owner_module)
            # When the allow-list is empty the L1 surface is left open
            # (the runner explicitly narrows in Phase 6 via evolution
            # direction + per-module prevalidation). Strict membership
            # filtering only happens when the caller supplies a non-empty
            # allow-list.
            if legal_module_set and owner not in legal_module_set:
                continue
            field = str(edge.field)
            direction_ops = _direction_legal_operations(
                owner, evolution_direction, failure_shape,
            )
            executable_ops = tuple(
                op for op in direction_ops
                if field in SKILL_CAPABILITY_REGISTRY.require(op).operation_targets
            )
            if not executable_ops:
                continue
            auto_op = executable_ops[0] if len(executable_ops) == 1 else ""
            capability = SKILL_CAPABILITY_REGISTRY.require(
                auto_op or executable_ops[0]
            )
            plans.append(RepairPlan(
                plan_id=_plan_id(episode.episode_id, edge.edge_id, LAYER_L1),
                episode_id=episode.episode_id,
                skill_layer=LAYER_L1,
                owner=owner,
                target_causal_edge=edge,
                executor=capability.executor,
                legal_operations=executable_ops,
                effect_contract=_l1_effect_contract(edge, owner, capability),
                evaluation_contract=_l1_evaluation_contract(edge, evolution_direction),
                target_domain=capability.input_binding_domain,
                grounding_query={
                    "semantics": "entity_scoped_binding_algebra",
                    "causal_target_field": edge.field,
                    "source_artifact_id": edge.source_artifact_id,
                    "blocking_predicate": edge.blocking_predicate,
                },
                required_binding_schema={
                    "domain": capability.input_binding_domain,
                    "required_fields": list(capability.required_binding_fields),
                    "cardinality": capability.cardinality,
                },
                auto_selected_operation=auto_op,
            ))
        return tuple(plans)

    # L2: one synthetic edge per repair surface. The L2 surface is the
    # artifact itself, so per-module filtering does not apply at this
    # layer; Phase 6 narrows by the candidate's ``params`` whitelist.
    for edge in episode.causal_failure_edge_frontier:
        owner = str(edge.output_object) or episode.failure_owner
        legal_ops = SKILL_CAPABILITY_REGISTRY.operations_for(
            skill_layer=LAYER_L2,
            modules=(owner,),
            failure_shape=episode.reason_code,
            direction=evolution_direction,
        )
        if not legal_ops:
            continue
        auto_op = legal_ops[0] if len(legal_ops) == 1 else ""
        capability = SKILL_CAPABILITY_REGISTRY.require(auto_op or legal_ops[0])
        plans.append(RepairPlan(
            plan_id=_plan_id(episode.episode_id, edge.edge_id, LAYER_L2),
            episode_id=episode.episode_id,
            skill_layer=LAYER_L2,
            owner=owner,
            target_causal_edge=edge,
            executor=EXECUTOR_ADJUDICATION_POLICY,
            legal_operations=legal_ops,
            effect_contract=_l2_effect_contract(edge, owner),
            evaluation_contract=_l2_evaluation_contract(edge),
            target_domain=capability.input_binding_domain,
            grounding_query={
                "semantics": "entity_scoped_binding_algebra",
                "causal_target_field": edge.field,
                "blocking_predicate": edge.blocking_predicate,
            },
            required_binding_schema={
                "domain": capability.input_binding_domain,
                "required_fields": list(capability.required_binding_fields),
                "cardinality": capability.cardinality,
            },
            auto_selected_operation=auto_op,
        ))
    return tuple(plans)


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "EXECUTOR_ADJUDICATION_POLICY",
    "EXECUTOR_L1_CONSTRUCTIVE_HARNESS",
    "EXECUTOR_L1_COURT_TRANSFORM",
    "EXECUTORS",
    "RepairPlan",
    "build_repair_plan",
]
