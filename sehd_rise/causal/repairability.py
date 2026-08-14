"""Single Repairability Authority (plan §2.1, §2.2, §2.5).

The old package split the "should this case enter evolution?" answer across
several authorities that depended on each other in subtle ways:

- ``step_attributor.AttributionSignal.should_evolve`` -- the v5 trigger,
  conflated with attribution confidence and read directly by the runner;
- ``failure_classifier.classify_failure`` -- the L1/L2/DETECTOR/NONE
  layer decision;
- ``failure_episode.UnaddressableFailure`` -- raised when an actionable
  layer had no legal executable surface, silently swallowed as
  ``skip_unaddressable_failure`` and folded into the same no-edit funnel
  as a benign abstention.

Plan §2.1 replaces that with a single authority that consumes the
``CausalFailureEpisode`` and returns a closed-vocabulary verdict:

    NON_EVOLVABLE
    REPAIRABLE
    CAPABILITY_GAP                  (learn-worthy failure, but no current
                                     executable Skill primitive)
    UNADDRESSABLE_FRAMEWORK_DEFECT   (plan §2.2 -- declared evolvable
                                       but no legal RepairPlan exists)
    FRAMEWORK_DEFECT                  (plan §2.5 -- detector boundary
                                       origin: validator/transport/parse/
                                       route/config -- never a Skill)

C1: ``should_evolve`` is absorbed; only ``REPAIRABLE`` enters evolution.
C2: an ``LAYER_L1`` / ``LAYER_L2`` attribution with no legal RepairPlan is
    ``UNADDRESSABLE_FRAMEWORK_DEFECT`` -- a framework invariant violation,
    not a no-edit / abstention.
C4: validator / transport / parse / route / config defects are always
    ``FRAMEWORK_DEFECT``; they never produce a Skill regardless of any
    other reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from sehd_rise.causal.cut import (
    ORIGIN_PROVIDER_GENERATION,
    ORIGIN_ROUTE_CONFIG,
    ORIGIN_TRANSPORT_PARSE,
    ORIGIN_VALIDATOR_CONTRACT,
    REASON_DEMOTED_L1_NO_EXECUTABLE,
    REASON_INTERNAL_INCONSISTENCY,
    REASON_NO_FAILURE_SHAPE,
    allowed_l1_modules,
    build_causal_cut,
    infer_evolution_direction,
    repair_direction,
)
from sehd_rise.causal.episode import CausalFailureEpisode, build_causal_episode
from sehd_rise.causal.plan import build_repair_plan
from sehd_rise.causal.signals import project_detection
from sehd_rise.skills.capabilities import SKILL_CAPABILITY_REGISTRY
from sehd_rise.skills.vocabulary.contracts import (
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
)


# ============================================================
# Verdict vocabulary (closed)
# ============================================================

class RepairabilityStatus(str, Enum):
    """Outcome of the single repairability analysis.

    ``NON_EVOLVABLE`` covers detector / no-actionable / runtime-invalid
    cuts that never claimed an evolvable surface; evolution is not
    DFA-reachable but no framework invariant was violated.

    ``REPAIRABLE`` means at least one legal RepairPlan compiles.

    ``CAPABILITY_GAP`` means the failure is causally attributed and should be
    learned, but the current Skill language has no runtime primitive for the
    desired transition.  It is not a stable/non-evolvable case and not a
    framework implementation defect.

    ``UNADDRESSABLE_FRAMEWORK_DEFECT`` is the §2.2 invariant violation: an
    L1 / L2 surface was declared (the cut landed on it) but no legal
    RepairPlan exists. Phase 9 records this in its own funnel bucket,
    never the no-edit bucket.

    ``FRAMEWORK_DEFECT`` is the §2.5 detector-boundary origin (validator /
    transport / parse / route / config); it is always a framework defect
    and never produces a Skill regardless of any other reading.
    """

    NON_EVOLVABLE = "non_evolvable"
    REPAIRABLE = "repairable"
    CAPABILITY_GAP = "capability_gap"
    UNADDRESSABLE_FRAMEWORK_DEFECT = "unaddressable_framework_defect"
    FRAMEWORK_DEFECT = "framework_defect"


# Closed reasons that explain each verdict. They never enter a prompt
# directly; the runner emits them into the §2.9 outcome funnel.
REASON_NON_EVOLVABLE_NO_ACTIONABLE = "non_evolvable_no_actionable_layer"
REASON_NON_EVOLVABLE_DETECTOR_INTERNAL = "non_evolvable_detector_internal_inconsistency"
REASON_NON_EVOLVABLE_DETECTOR_BOUNDARY_ORIGIN = "non_evolvable_detector_boundary_origin"
REASON_NON_EVOLVABLE_NO_FAILURE_SHAPE = "non_evolvable_no_failure_shape"
REASON_NON_EVOLVABLE_RUNTIME_INVALID = "non_evolvable_runtime_invalid"
REASON_REPAIRABLE_LEGAL_PLAN_AVAILABLE = "repairable_legal_plan_available"
REASON_CAPABILITY_GAP_NO_EXECUTABLE_PRIMITIVE = (
    "capability_gap_no_executable_primitive"
)
REASON_UNADDRESSABLE_NO_LEGAL_PLAN = "unaddressable_no_legal_repair_plan"
REASON_UNADDRESSABLE_DEMOTED_L1 = "unaddressable_demoted_l1_no_executable"
REASON_FRAMEWORK_DEFECT_TRANSPORT_PARSE = "framework_defect_transport_or_parse_fault"
REASON_FRAMEWORK_DEFECT_VALIDATOR_CONTRACT = "framework_defect_validator_contract_fault"
REASON_FRAMEWORK_DEFECT_ROUTE_CONFIG = "framework_defect_route_or_config_fault"


_DETECTOR_ORIGIN_REASON = {
    ORIGIN_TRANSPORT_PARSE: REASON_FRAMEWORK_DEFECT_TRANSPORT_PARSE,
    ORIGIN_VALIDATOR_CONTRACT: REASON_FRAMEWORK_DEFECT_VALIDATOR_CONTRACT,
    ORIGIN_ROUTE_CONFIG: REASON_FRAMEWORK_DEFECT_ROUTE_CONFIG,
}


# ============================================================
# Verdict
# ============================================================

@dataclass(frozen=True)
class RepairabilityVerdict:
    """Outcome of ``analyze`` for one causal episode.

    Carries the closed-vocabulary status, a closed-vocabulary reason, and
    the deterministic evidence the authority used to reach the verdict
    (frontier size, plan count if compiled, detector origin, etc.). The
    reason tokens are the values Phase 9's outcome funnel reads; the
    evidence is for audit only.
    """

    status: RepairabilityStatus
    reason: str
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


# ============================================================
# Single authority
# ============================================================

def analyze(
    episode: CausalFailureEpisode,
    *,
    allowed_modules: Sequence[str] = (),
    evolution_direction: str = "",
) -> RepairabilityVerdict:
    """Apply the single Repairability Authority to one episode.

    Decision order (first match wins; every branch is closed-vocab):

    1. Detector boundary origin (transport / parse / validator / route /
       config) -> ``FRAMEWORK_DEFECT`` (plan §2.5 -- always, never a
       Skill). The owner recorded by the cut is the boundary repair label,
       so the episode's ``failure_origin`` is the closed-vocab token.
    2. Diagnostic-only (runtime invalid) episode -> ``NON_EVOLVABLE``
       (no authoritative verdict, no evolvable surface can be claimed).
    3. Non-evolvable skill layer (DETECTOR internal inconsistency or
       NONE) -> ``NON_EVOLVABLE`` with the matching closed reason.
    4. Evolvable surface (L1 / L2) declared:
       - Compile at least one legal ``RepairPlan``.
       - If a plan exists -> ``REPAIRABLE``.
       - If no plan exists and the registry has no matching primitive ->
         ``CAPABILITY_GAP``.
       - If the registry says an exact primitive exists but no plan compiled ->
         ``UNADDRESSABLE_FRAMEWORK_DEFECT`` (semantic-closure invariant).
    """
    # --- 1. Detector boundary origin (C4) ---------------------------
    if episode.failure_origin in _DETECTOR_ORIGIN_REASON:
        return RepairabilityVerdict(
            status=RepairabilityStatus.FRAMEWORK_DEFECT,
            reason=_DETECTOR_ORIGIN_REASON[episode.failure_origin],
            evidence={
                "failure_origin": episode.failure_origin,
                "boundary_owner": episode.failure_owner,
                "skill_layer": episode.skill_layer,
            },
        )

    # --- 2. Runtime-invalid (diagnostic-only) ----------------------
    if bool(episode.detection_snapshot.get("diagnostic_only")):
        return RepairabilityVerdict(
            status=RepairabilityStatus.NON_EVOLVABLE,
            reason=REASON_NON_EVOLVABLE_RUNTIME_INVALID,
            evidence={"terminal_state": episode.terminal_state},
        )

    # --- 3. Non-evolvable skill layer ------------------------------
    if episode.skill_layer == LAYER_DETECTOR:
        # The cut classified this as a detector-side internal
        # inconsistency (surviving errors with clean verdict, or all
        # candidates quarantined with no certificates). Plan §2.5: the
        # detector inference boundary owns it.
        if episode.reason_code == REASON_INTERNAL_INCONSISTENCY:
            return RepairabilityVerdict(
                status=RepairabilityStatus.NON_EVOLVABLE,
                reason=REASON_NON_EVOLVABLE_DETECTOR_INTERNAL,
                evidence={
                    "terminal_state": episode.terminal_state,
                    "reason_code": episode.reason_code,
                },
            )
        return RepairabilityVerdict(
            status=RepairabilityStatus.NON_EVOLVABLE,
            reason=REASON_NON_EVOLVABLE_DETECTOR_BOUNDARY_ORIGIN,
            evidence={
                "terminal_state": episode.terminal_state,
                "reason_code": episode.reason_code,
            },
        )

    if episode.skill_layer == LAYER_NONE:
        if episode.reason_code == REASON_DEMOTED_L1_NO_EXECUTABLE:
            classified_owner = str(
                episode.attribution_snapshot.get("owner")
                or episode.attribution_snapshot.get("classified_owner")
                or episode.failure_owner
            )
            gap = SKILL_CAPABILITY_REGISTRY.gap_for(
                failure_shape=str(
                    episode.attribution_snapshot.get("classified_reason")
                    or episode.reason_code
                ),
                owner=classified_owner,
                desired_transition=evolution_direction,
            )
            return RepairabilityVerdict(
                status=RepairabilityStatus.CAPABILITY_GAP,
                reason=REASON_CAPABILITY_GAP_NO_EXECUTABLE_PRIMITIVE,
                evidence={"registry_queried": True, **gap.to_dict()},
            )
        if episode.reason_code == REASON_NO_FAILURE_SHAPE:
            return RepairabilityVerdict(
                status=RepairabilityStatus.NON_EVOLVABLE,
                reason=REASON_NON_EVOLVABLE_NO_FAILURE_SHAPE,
                evidence={"terminal_state": episode.terminal_state},
            )
        return RepairabilityVerdict(
            status=RepairabilityStatus.NON_EVOLVABLE,
            reason=REASON_NON_EVOLVABLE_NO_ACTIONABLE,
            evidence={
                "terminal_state": episode.terminal_state,
                "reason_code": episode.reason_code,
            },
        )

    # --- 4. Evolvable surface (L1 / L2) declared -------------------
    if episode.skill_layer not in (LAYER_L1, LAYER_L2):
        # Defensive: an unknown skill layer is a framework defect (we
        # should never observe one because the cut emits a closed layer
        # token; if we do, the closed-vocabulary invariant was broken).
        return RepairabilityVerdict(
            status=RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT,
            reason=REASON_UNADDRESSABLE_NO_LEGAL_PLAN,
            evidence={
                "skill_layer": episode.skill_layer,
                "unknown_layer": True,
            },
        )

    plans = build_repair_plan(
        episode,
        allowed_modules=allowed_modules,
        evolution_direction=evolution_direction,
    )
    if plans:
        primary = plans[0]
        return RepairabilityVerdict(
            status=RepairabilityStatus.REPAIRABLE,
            reason=REASON_REPAIRABLE_LEGAL_PLAN_AVAILABLE,
            evidence={
                "plan_count": len(plans),
                "primary_plan_id": primary.plan_id,
                "primary_executor": primary.executor,
                "primary_legal_operations": list(primary.legal_operations),
                "primary_auto_selected_operation": primary.auto_selected_operation,
            },
        )

    exact_capabilities = tuple(
        capability
        for edge in episode.causal_failure_edge_frontier
        for capability in SKILL_CAPABILITY_REGISTRY.query(
            skill_layer=episode.skill_layer,
            modules=((
                edge.owner_module or episode.failure_owner
                if episode.skill_layer == LAYER_L1
                else edge.output_object or episode.failure_owner
            ),),
            failure_shape=episode.reason_code,
            direction=evolution_direction,
            target_field=(edge.field if episode.skill_layer == LAYER_L1 else ""),
        )
    )
    if not exact_capabilities:
        gap = SKILL_CAPABILITY_REGISTRY.gap_for(
            failure_shape=episode.reason_code,
            owner=episode.failure_owner,
            desired_transition=evolution_direction,
        )
        return RepairabilityVerdict(
            status=RepairabilityStatus.CAPABILITY_GAP,
            reason=REASON_CAPABILITY_GAP_NO_EXECUTABLE_PRIMITIVE,
            evidence={
                "registry_queried": True,
                "skill_layer": episode.skill_layer,
                "frontier_size": len(episode.causal_failure_edge_frontier),
                "allowed_modules": list(allowed_modules),
                **gap.to_dict(),
            },
        )

    # An evolvable surface was declared (LAYER_L1 / LAYER_L2) but no legal
    # RepairPlan compiled. Plan §2.2: this is a framework invariant
    # violation, NOT a no-edit/abstention; Phase 9 routes it to its own
    # funnel bucket.
    return RepairabilityVerdict(
        status=RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT,
        reason=REASON_UNADDRESSABLE_NO_LEGAL_PLAN,
        evidence={
            "skill_layer": episode.skill_layer,
            "failure_owner": episode.failure_owner,
            "reason_code": episode.reason_code,
            "frontier_size": len(episode.causal_failure_edge_frontier),
            "allowed_modules": list(allowed_modules),
            "evolution_direction": evolution_direction,
        },
    )


# ============================================================
# Trigger predicate — the single shared source
# ============================================================

def triggered_episode(
    result: Mapping[str, Any], *, use_gold: bool, gold_label: bool | None,
) -> bool:
    """Whether a detection result carries an active failure.

    The trigger means *failure exists*, not *failure is currently
    repairable*. It is derived with the exact same canonical context the
    main loop uses (evolution direction + the cut's legal L1 module
    surface), so the main route, the source replay and the canary guard all
    read one repairability truth. ``REPAIRABLE``, ``CAPABILITY_GAP``,
    ``UNADDRESSABLE_FRAMEWORK_DEFECT`` and ``FRAMEWORK_DEFECT`` are all
    active failures: only a no-failure-shape ``NON_EVOLVABLE`` verdict (or
    a runtime-invalid projection) closes the trigger. A failure that merely
    falls from repairable to capability-gap therefore stays open.
    """
    payload = dict(result)
    if use_gold and gold_label is not None:
        payload["gold_label"] = gold_label
    signal = project_detection(payload, use_gold=use_gold)
    if signal.diagnostic_only:
        return False
    cut = build_causal_cut(signal)
    direction = repair_direction(signal, cut)
    modules = allowed_l1_modules(
        signal,
        cut.owner,
        (
            tuple(cut.evidence.get("artifact_frontier") or ())
            if isinstance(cut.evidence, Mapping) else ()
        ),
        cut.secondary_owner,
    )
    episode = build_causal_episode(
        signal=signal,
        cut=cut,
        record_key=str(payload.get("case_id") or ""),
        evolution_direction=direction,
        allowed_modules=modules,
    )
    verdict = analyze(
        episode,
        allowed_modules=(modules if cut.skill_layer == LAYER_L1 else ()),
        evolution_direction=direction,
    )
    return verdict.status is not RepairabilityStatus.NON_EVOLVABLE


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "REASON_FRAMEWORK_DEFECT_ROUTE_CONFIG",
    "REASON_FRAMEWORK_DEFECT_TRANSPORT_PARSE",
    "REASON_FRAMEWORK_DEFECT_VALIDATOR_CONTRACT",
    "REASON_CAPABILITY_GAP_NO_EXECUTABLE_PRIMITIVE",
    "REASON_NON_EVOLVABLE_DETECTOR_BOUNDARY_ORIGIN",
    "REASON_NON_EVOLVABLE_DETECTOR_INTERNAL",
    "REASON_NON_EVOLVABLE_NO_ACTIONABLE",
    "REASON_NON_EVOLVABLE_NO_FAILURE_SHAPE",
    "REASON_NON_EVOLVABLE_RUNTIME_INVALID",
    "REASON_REPAIRABLE_LEGAL_PLAN_AVAILABLE",
    "REASON_UNADDRESSABLE_DEMOTED_L1",
    "REASON_UNADDRESSABLE_NO_LEGAL_PLAN",
    "RepairabilityStatus",
    "RepairabilityVerdict",
    "analyze",
    "triggered_episode",
]
