"""Proof-court contract: deterministic validation and adjudication.

The contract grew across five incremental generations (single typed ledger →
genuinely layered qualification/propagation → proof court with symmetric
appeals → focused prosecution + calibrated appeal).  This module is the
single self-contained successor: every obligation the live adjudicator
actually depends on is inlined once, no version-suffix helpers, no
cross-generation imports.

Provider labels, scores, and route identity never enter the final rule.  Two
independent provider paths construct error and clean certificates; this
contract validates only addressable proof obligations and resolves them with
a deterministic lattice.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from sehd_rise.detection.court.audit import (
    DEFENSE_CHECK_FIELDS,
    DEFENSE_RELATIONAL_INVARIANTS,
    ERROR_CHECK_FIELDS,
    ERROR_RELATIONAL_INVARIANTS,
    project_audit_failures,
)
from sehd_rise.detection.policy import (
    PreLedgerPolicyFacts,
    build_adjudication_facts,
    evaluate_policies,
    evaluate_pre_ledger_policies,
)
from sehd_rise.skills.vocabulary.contracts import PolicyItem

if TYPE_CHECKING:
    pass


# ============================================================
# Typed causal-ledger vocabulary
# ============================================================

AXES = frozenset({"F", "Fa", "P", "S"})
VIOLATION_STATES = frozenset({"verified_violation", "verified_non_violation", "unresolved"})
ACTOR_STATES = frozenset({
    "agent_caused", "agent_contributed", "environment_caused",
    "external_input_caused", "unresolved",
})
BOUNDARY_STATES = frozenset({"cleared", "triggered", "unresolved"})
PROPAGATION_STATES = frozenset({
    "no_consumption", "recovered_before_impact",
    "impacted_then_recovered", "unrecovered_impact", "unresolved",
})
IMPACT_SURFACES = frozenset({"A", "X", "R"})
COVERAGE_STATES = frozenset({"complete", "incomplete", "unresolved"})
CASE_STATES = frozenset({"verified_error", "verified_clean", "unresolved"})

REQUIRED_CANDIDATE_FIELDS = frozenset({
    "candidate_id", "axes", "candidate_kind", "trigger_event_id",
    "origin_event_id", "claim_or_commitment", "dependency_event_ids",
    "violation_state", "actor_state", "boundary_state",
    "propagation_state", "residual_impact", "positive_evidence_refs",
    "actor_evidence_refs", "boundary_evidence_refs",
    "consumption_event_ids", "consumption_evidence_refs",
    "impact_event_ids", "impact_evidence_refs",
    "recovery_event_id", "recovery_evidence_refs",
    "impact_surfaces", "rationale",
})

QUALIFICATION_FIELDS = frozenset({
    "candidate_id", "violation_state", "actor_state", "boundary_state",
    "positive_evidence_refs", "actor_evidence_refs", "boundary_evidence_refs",
    "rationale",
})
PROPAGATION_FIELDS = frozenset({
    "candidate_id", "propagation_state", "residual_impact",
    "consumption_event_ids", "consumption_evidence_refs",
    "impact_event_ids", "impact_evidence_refs", "recovery_event_id",
    "recovery_evidence_refs", "impact_surfaces", "rationale",
})

CLAIM_KINDS = frozenset({
    "agent_assertion", "agent_action", "agent_omission", "agent_adoption",
})
NORM_SOURCES = frozenset({
    "user_instruction", "tool_contract", "prior_trajectory_evidence",
    "logical_invariant", "external_evidence",
})
BREACH_TYPES = frozenset({
    "contradiction", "fabrication", "unsupported_commitment",
    "instruction_violation", "invalid_action", "consequential_omission",
    "redundant_repetition",
})
IMPACT_KINDS = frozenset({
    "final_answer", "persistent_external_state", "persistent_workspace_state",
    "committed_tool_action", "unrecovered_reasoning_dependency",
    "process_debt",
})
RECOVERY_STATES = frozenset({
    "none", "post_impact_residual", "pre_impact_repaired", "unresolved",
})
BENCHMARK_CAUSAL_STATES = frozenset({
    "causes_incorrect_result", "propagates_to_incorrect_result",
    "process_only", "unresolved",
})
# The defense vocabulary is the extended set: three appeal-only defense types
# were added when the symmetric appellate subsystem was introduced.
DEFENSE_TYPES = frozenset({
    "external_unadopted", "explicitly_authorized", "supported_or_correct",
    "ordinary_failure_without_commitment", "legitimate_retry_with_state_change",
    "information_gainful_repetition", "required_revalidation", "insufficient_evidence",
    "materially_equivalent_or_within_tolerance",
    "rejected_attempt_no_state_change",
    "absence_of_observation_not_fabrication",
})
DOMINATING_DEFENSES = DEFENSE_TYPES - {"insufficient_evidence"}

ERROR_FIELDS = frozenset({
    "certificate_id", "source_candidate_id", "origin_event_id", "claim_kind",
    "norm_source", "norm_evidence_refs", "breach_type",
    "violation_evidence_refs", "agent_control_event_id",
    "agent_control_evidence_refs", "impact_kind", "impact_event_ids",
    "impact_evidence_refs", "recovery_state", "recovery_evidence_refs",
    "benchmark_causal_status", "benchmark_causal_evidence_refs", "rationale",
})
DEFENSE_FIELDS = frozenset({
    "certificate_id", "target_candidate_id", "target_origin_event_id",
    "defense_type", "evidence_refs", "rationale",
})

CAUSAL_CHAIN_STAGES = [
    "norm_identification", "origin_event", "agent_control",
    "propagation_path", "impact_on_result", "counterfactual_test",
]


# ============================================================
# Packet address space (shared by every layer)
# ============================================================

def _address_space(packet: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    events = packet.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("packet must contain events")
    event_order: dict[str, int] = {}
    evidence_refs: set[str] = set()
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise ValueError("packet event must be an object")
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in event_order:
            raise ValueError("packet event ids must be present and unique")
        event_order[event_id] = index
        evidence_refs.add(event_id)
        for interaction in event.get("tool_interactions") or []:
            if not isinstance(interaction, dict):
                raise ValueError("tool interaction must be an object")
            interaction_id = str(interaction.get("interaction_id") or "")
            if not interaction_id or interaction_id in evidence_refs:
                raise ValueError("interaction ids must be present and globally unique")
            evidence_refs.add(interaction_id)
    return event_order, evidence_refs


def _event_ref(value: Any, event_order: dict[str, int], *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return isinstance(value, str) and value in event_order


def _event_refs(value: Any, event_order: dict[str, int], *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(ref, str) and ref in event_order for ref in value)
    )


def _evidence_refs(value: Any, addressable: set[str], *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(ref, str) and ref in addressable for ref in value)
    )


def _ordered_chain(candidate: dict[str, Any], event_order: dict[str, int]) -> bool:
    origin = candidate.get("origin_event_id")
    consumption = candidate.get("consumption_event_ids") or []
    impact = candidate.get("impact_event_ids") or []
    if not _event_ref(origin, event_order):
        return False
    if not _event_refs(consumption, event_order, nonempty=True):
        return False
    if not _event_refs(impact, event_order, nonempty=True):
        return False
    origin_order = event_order[str(origin)]
    consumption_orders = [event_order[str(ref)] for ref in consumption]
    impact_orders = [event_order[str(ref)] for ref in impact]
    return (
        consumption_orders == sorted(consumption_orders)
        and impact_orders == sorted(impact_orders)
        and origin_order <= consumption_orders[0] <= impact_orders[0]
        and consumption_orders[-1] <= impact_orders[-1]
    )


def _evidence_denied_by_state(state: str, row: Mapping[str, Any]) -> bool:
    """Evidence lists may not cite events the propagation state denies.

    ``no_consumption`` means nothing was consumed, impacted or recovered;
    ``recovered_before_impact`` means impact never happened.  A row in
    either state carrying the corresponding evidence refs contradicts
    itself (the provider prompt requires ``[]`` for non-applicable lists).
    """
    if state == "no_consumption":
        return bool(row.get("consumption_evidence_refs") or
                    row.get("impact_evidence_refs"))
    if state == "recovered_before_impact":
        return bool(row.get("impact_evidence_refs"))
    return False


# ============================================================
# Single typed-ledger candidate (qualification + boundary + propagation in
# one row).  Used by the single-ledger adjudicator path only.
# ============================================================

def validate_candidate(candidate: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Validate syntax, addressability, and temporal invariants for one row."""

    event_order, addressable = _address_space(packet)
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
    violation = str(candidate.get("violation_state") or "")
    actor = str(candidate.get("actor_state") or "")
    boundary = str(candidate.get("boundary_state") or "")
    propagation = str(candidate.get("propagation_state") or "")
    axes = candidate.get("axes")
    surfaces = candidate.get("impact_surfaces")
    recovery = candidate.get("recovery_event_id")

    needs_positive_evidence = violation == "verified_violation"
    needs_actor_evidence = actor != "unresolved"
    needs_boundary_evidence = boundary == "triggered"
    impacted = propagation in {"impacted_then_recovered", "unrecovered_impact"}
    chain_ordered = _ordered_chain(candidate, event_order) if impacted else True

    propagation_consistent = True
    if propagation == "no_consumption":
        propagation_consistent = not candidate.get("consumption_event_ids") and not candidate.get("impact_event_ids") and recovery is None
    elif propagation == "recovered_before_impact":
        propagation_consistent = (
            _event_ref(candidate.get("origin_event_id"), event_order)
            and _event_ref(recovery, event_order)
            and event_order[str(candidate["origin_event_id"])] <= event_order[str(recovery)]
            and not candidate.get("impact_event_ids")
        )
    elif propagation == "impacted_then_recovered":
        propagation_consistent = (
            chain_ordered
            and _event_ref(recovery, event_order)
            and event_order[str(recovery)] >= max(event_order[str(ref)] for ref in candidate.get("impact_event_ids") or [])
        )
    elif propagation == "unrecovered_impact":
        propagation_consistent = chain_ordered and recovery is None

    checks = {
        "required_fields": not missing,
        "candidate_id": bool(str(candidate.get("candidate_id") or "").strip()),
        "axes": isinstance(axes, list) and bool(axes) and set(map(str, axes)) <= AXES,
        "candidate_kind": bool(str(candidate.get("candidate_kind") or "").strip()),
        "claim_or_commitment": bool(str(candidate.get("claim_or_commitment") or "").strip()),
        "trigger_addressable": (
            isinstance(candidate.get("trigger_event_id"), str)
            and candidate.get("trigger_event_id") in addressable
        ),
        "origin_addressable": _event_ref(candidate.get("origin_event_id"), event_order),
        "dependency_refs": _evidence_refs(candidate.get("dependency_event_ids"), addressable),
        "violation_state": violation in VIOLATION_STATES,
        "actor_state": actor in ACTOR_STATES,
        "boundary_state": boundary in BOUNDARY_STATES,
        "propagation_state": propagation in PROPAGATION_STATES,
        "residual_impact": isinstance(candidate.get("residual_impact"), bool),
        "impact_surfaces": isinstance(surfaces, list) and set(map(str, surfaces)) <= IMPACT_SURFACES,
        "positive_evidence": _evidence_refs(candidate.get("positive_evidence_refs"), addressable, nonempty=needs_positive_evidence),
        "actor_evidence": _evidence_refs(candidate.get("actor_evidence_refs"), addressable, nonempty=needs_actor_evidence),
        "boundary_evidence": _evidence_refs(candidate.get("boundary_evidence_refs"), addressable, nonempty=needs_boundary_evidence),
        "consumption_events": _event_refs(candidate.get("consumption_event_ids"), event_order),
        "consumption_evidence": _evidence_refs(candidate.get("consumption_evidence_refs"), addressable, nonempty=impacted),
        "impact_events": _event_refs(candidate.get("impact_event_ids"), event_order),
        "impact_evidence": _evidence_refs(candidate.get("impact_evidence_refs"), addressable, nonempty=impacted),
        "recovery_addressable": _event_ref(recovery, event_order, nullable=True),
        "recovery_evidence": _evidence_refs(candidate.get("recovery_evidence_refs"), addressable, nonempty=recovery is not None),
        "ordered_causal_chain": chain_ordered,
        "propagation_consistent": propagation_consistent,
        "evidence_denied_by_state": not _evidence_denied_by_state(
            propagation, candidate,
        ),
        "impacted_has_surface": not impacted or bool(surfaces),
        "nonimpacted_has_no_surface": impacted or not surfaces,
        "residual_consistent": not candidate.get("residual_impact") or propagation in {"impacted_then_recovered", "unrecovered_impact"},
        "rationale": bool(str(candidate.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "decision": "pass" if not failed else "quarantine",
        "checks": checks,
        "failed_checks": failed,
        "missing": missing,
    }


def candidate_verdict(candidate: dict[str, Any]) -> str:
    """Map one structurally valid row to the frozen benchmark tri-state."""

    violation = str(candidate["violation_state"])
    actor = str(candidate["actor_state"])
    boundary = str(candidate["boundary_state"])
    propagation = str(candidate["propagation_state"])
    residual = bool(candidate["residual_impact"])

    if "unresolved" in {violation, actor, boundary, propagation}:
        return "unresolved"
    if violation == "verified_non_violation":
        return "verified_clean"
    if actor in {"environment_caused", "external_input_caused"}:
        return "verified_clean"
    if boundary == "triggered":
        return "verified_clean"
    if propagation in {"no_consumption", "recovered_before_impact"}:
        return "verified_clean"
    if propagation == "impacted_then_recovered" and not residual:
        return "verified_clean"
    if (
        violation == "verified_violation"
        and actor in {"agent_caused", "agent_contributed"}
        and boundary == "cleared"
        and (propagation == "unrecovered_impact" or (propagation == "impacted_then_recovered" and residual))
    ):
        return "verified_error"
    return "unresolved"


def validate_ledger(ledger: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    coverage = str(ledger.get("coverage_state") or "")
    rows = ledger.get("candidates")
    if coverage not in COVERAGE_STATES:
        raise ValueError("invalid coverage_state")
    if not isinstance(rows, list):
        raise ValueError("candidates must be a list")
    if len(rows) > 12:
        raise ValueError("at most 12 candidates are allowed")
    ids = [str(row.get("candidate_id") or "") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("candidate ids must be present and unique")
    validations = [validate_candidate(row, packet) for row in rows]
    quarantined = [ids[index] for index, value in enumerate(validations) if value["decision"] != "pass"]
    return {
        "coverage_state": coverage,
        "candidate_count": len(rows),
        "usable_candidate_count": len(rows) - len(quarantined),
        "quarantined_candidate_ids": quarantined,
        "candidates": validations,
    }


def adjudicate_ledger(ledger: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Adjudicate usable rows; quarantine is candidate-local and fail-closed."""

    validation = validate_ledger(ledger, packet)
    quarantined = set(validation["quarantined_candidate_ids"])
    usable = [row for row in ledger["candidates"] if row["candidate_id"] not in quarantined]
    per_candidate = {row["candidate_id"]: candidate_verdict(row) for row in usable}
    event_order, _ = _address_space(packet)
    positives = [row for row in usable if per_candidate[row["candidate_id"]] == "verified_error"]

    if positives:
        winner = min(positives, key=lambda row: (event_order[str(row["origin_event_id"])], str(row["candidate_id"])))
        state = "verified_error"
        responsible_event_id = winner["origin_event_id"]
        responsible_candidate_id = winner["candidate_id"]
    elif quarantined or ledger["coverage_state"] != "complete" or any(value == "unresolved" for value in per_candidate.values()):
        state = "unresolved"
        responsible_event_id = None
        responsible_candidate_id = None
    else:
        state = "verified_clean"
        responsible_event_id = None
        responsible_candidate_id = None

    return {
        "state": state,
        "responsible_event_id": responsible_event_id,
        "responsible_candidate_id": responsible_candidate_id,
        "candidate_state_counts": dict(Counter(per_candidate.values())),
        "candidate_states": per_candidate,
        "coverage_state": ledger["coverage_state"],
        "quarantined_candidate_ids": sorted(quarantined),
        "contract_validation": validation,
    }


# ============================================================
# Genuinely layered candidate path (qualification and propagation
# separated into independent provider obligations).
# ============================================================

def validate_qualification(row: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the small semantic/boundary layer without causal-chain fields."""

    _, addressable = _address_space(packet)
    violation = str(row.get("violation_state") or "")
    actor = str(row.get("actor_state") or "")
    boundary = str(row.get("boundary_state") or "")
    checks = {
        "required_fields": not (QUALIFICATION_FIELDS - set(row)),
        "candidate_id": bool(str(row.get("candidate_id") or "").strip()),
        "violation_state": violation in VIOLATION_STATES,
        "actor_state": actor in ACTOR_STATES,
        "boundary_state": boundary in BOUNDARY_STATES,
        "positive_evidence": _evidence_refs(
            row.get("positive_evidence_refs"), addressable,
            nonempty=violation == "verified_violation",
        ),
        "actor_evidence": _evidence_refs(
            row.get("actor_evidence_refs"), addressable,
            nonempty=actor != "unresolved",
        ),
        "boundary_evidence": _evidence_refs(
            row.get("boundary_evidence_refs"), addressable,
            nonempty=boundary == "triggered",
        ),
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {"decision": "pass" if not failed else "quarantine", "checks": checks, "failed_checks": failed}


def qualification_state(row: dict[str, Any]) -> str:
    values = {str(row["violation_state"]), str(row["actor_state"]), str(row["boundary_state"])}
    if "unresolved" in values:
        return "unresolved"
    if row["violation_state"] == "verified_non_violation":
        return "verified_clean"
    if row["actor_state"] in {"environment_caused", "external_input_caused"}:
        return "verified_clean"
    if row["boundary_state"] == "triggered":
        return "verified_clean"
    if row["violation_state"] == "verified_violation" and row["actor_state"] in {
        "agent_caused", "agent_contributed"
    } and row["boundary_state"] == "cleared":
        return "qualified"
    return "unresolved"


def validate_propagation(row: dict[str, Any], origin_event_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the causal-chain layer only after a candidate is qualified."""

    event_order, addressable = _address_space(packet)
    state = str(row.get("propagation_state") or "")
    recovery = row.get("recovery_event_id")
    impacted = state in {"impacted_then_recovered", "unrecovered_impact"}
    consumption = row.get("consumption_event_ids")
    impact = row.get("impact_event_ids")
    surfaces = row.get("impact_surfaces")

    chain_ordered = True
    if impacted:
        chain_ordered = (
            origin_event_id in event_order
            and _event_refs(consumption, event_order, nonempty=True)
            and _event_refs(impact, event_order, nonempty=True)
        )
        if chain_ordered:
            c_order = [event_order[ref] for ref in consumption]
            i_order = [event_order[ref] for ref in impact]
            chain_ordered = (
                c_order == sorted(c_order) and i_order == sorted(i_order)
                and event_order[origin_event_id] <= c_order[0] <= i_order[0]
                and c_order[-1] <= i_order[-1]
            )

    consistent = True
    if state == "no_consumption":
        consistent = not consumption and not impact and recovery is None
    elif state == "recovered_before_impact":
        consistent = (
            origin_event_id in event_order and _event_ref(recovery, event_order)
            and event_order[origin_event_id] <= event_order[str(recovery)] and not impact
        )
    elif state == "impacted_then_recovered":
        consistent = chain_ordered and _event_ref(recovery, event_order)
        if consistent:
            consistent = event_order[str(recovery)] >= max(event_order[ref] for ref in impact)
    elif state == "unrecovered_impact":
        consistent = chain_ordered and recovery is None

    checks = {
        "required_fields": not (PROPAGATION_FIELDS - set(row)),
        "candidate_id": bool(str(row.get("candidate_id") or "").strip()),
        "propagation_state": state in PROPAGATION_STATES,
        "residual_impact": isinstance(row.get("residual_impact"), bool),
        "consumption_events": _event_refs(consumption, event_order),
        "consumption_evidence": _evidence_refs(
            row.get("consumption_evidence_refs"), addressable, nonempty=impacted
        ),
        "impact_events": _event_refs(impact, event_order),
        "impact_evidence": _evidence_refs(row.get("impact_evidence_refs"), addressable, nonempty=impacted),
        "recovery_addressable": _event_ref(recovery, event_order, nullable=True),
        "recovery_evidence": _evidence_refs(
            row.get("recovery_evidence_refs"), addressable, nonempty=recovery is not None
        ),
        "impact_surfaces": isinstance(surfaces, list) and set(map(str, surfaces)) <= IMPACT_SURFACES,
        "ordered_causal_chain": chain_ordered,
        "propagation_consistent": consistent,
        "evidence_denied_by_state": not _evidence_denied_by_state(state, row),
        "impacted_has_surface": not impacted or bool(surfaces),
        "nonimpacted_has_no_surface": impacted or not surfaces,
        "residual_consistent": not row.get("residual_impact") or impacted,
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {"decision": "pass" if not failed else "quarantine", "checks": checks, "failed_checks": failed}


def propagation_state(row: dict[str, Any]) -> str:
    state = str(row["propagation_state"])
    if state == "unresolved":
        return "unresolved"
    if state in {"no_consumption", "recovered_before_impact"}:
        return "verified_clean"
    if state == "impacted_then_recovered" and not row["residual_impact"]:
        return "verified_clean"
    if state == "unrecovered_impact" or (state == "impacted_then_recovered" and row["residual_impact"]):
        return "verified_error"
    return "unresolved"


def adjudicate_candidate_path(
    *,
    census: list[dict[str, Any]],
    census_coverage: str,
    qualifications: list[dict[str, Any]],
    qualification_coverage: str,
    propagations: list[dict[str, Any]],
    propagation_coverage: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Join layers by candidate ID and quarantine failures locally."""

    if census_coverage not in COVERAGE_STATES or qualification_coverage not in COVERAGE_STATES:
        raise ValueError("invalid coverage state")
    if propagation_coverage not in COVERAGE_STATES:
        raise ValueError("invalid propagation coverage state")
    census_by_id = {str(row.get("candidate_id") or ""): row for row in census}
    if not all(census_by_id) or len(census_by_id) != len(census):
        raise ValueError("census candidate ids must be present and unique")
    q_by_id = {str(row.get("candidate_id") or ""): row for row in qualifications}
    p_by_id = {str(row.get("candidate_id") or ""): row for row in propagations}
    if len(q_by_id) != len(qualifications) or len(p_by_id) != len(propagations):
        raise ValueError("layer candidate ids must be present and unique")

    states: dict[str, str] = {}
    audits: dict[str, Any] = {}
    for candidate_id, source in census_by_id.items():
        qualification = q_by_id.get(candidate_id)
        if qualification is None:
            states[candidate_id] = "unresolved"
            audits[candidate_id] = {"qualification": "missing", "propagation": "not_requested"}
            continue
        q_audit = validate_qualification(qualification, packet)
        if q_audit["decision"] != "pass":
            states[candidate_id] = "unresolved"
            audits[candidate_id] = {"qualification": q_audit, "propagation": "not_requested"}
            continue
        q_state = qualification_state(qualification)
        if q_state == "verified_clean":
            states[candidate_id] = "verified_clean"
            audits[candidate_id] = {"qualification": q_audit, "propagation": "not_required"}
            continue
        if q_state == "unresolved":
            states[candidate_id] = "unresolved"
            audits[candidate_id] = {"qualification": q_audit, "propagation": "not_requested"}
            continue

        propagation = p_by_id.get(candidate_id)
        if propagation is None:
            states[candidate_id] = "unresolved"
            audits[candidate_id] = {"qualification": q_audit, "propagation": "missing"}
            continue
        origin_event_id = str(
            source.get("origin_event_id")
            or source.get("origin_hypothesis_event_id")
            or ""
        )
        p_audit = validate_propagation(propagation, origin_event_id, packet)
        states[candidate_id] = propagation_state(propagation) if p_audit["decision"] == "pass" else "unresolved"
        audits[candidate_id] = {"qualification": q_audit, "propagation": p_audit}

    event_order, _ = _address_space(packet)
    positives = [
        {**census_by_id[cid], "origin_event_id": str(
            census_by_id[cid].get("origin_event_id")
            or census_by_id[cid].get("origin_hypothesis_event_id")
            or ""
        )}
        for cid, state in states.items() if state == "verified_error"
    ]
    coverage_complete = (
        census_coverage == qualification_coverage == "complete"
        and set(q_by_id) == set(census_by_id)
        and propagation_coverage == "complete"
    )
    if positives:
        winner = min(positives, key=lambda row: (event_order[str(row["origin_event_id"])], str(row["candidate_id"])))
        state, responsible = "verified_error", str(winner["origin_event_id"])
    elif coverage_complete and all(state == "verified_clean" for state in states.values()):
        state, responsible = "verified_clean", None
    else:
        state, responsible = "unresolved", None
    return {
        "state": state,
        "responsible_event_id": responsible,
        "candidate_states": states,
        "candidate_state_counts": dict(Counter(states.values())),
        "layer_audits": audits,
        "coverage_complete": coverage_complete,
        "unexpected_qualification_ids": sorted(set(q_by_id) - set(census_by_id)),
        "unexpected_propagation_ids": sorted(set(p_by_id) - set(census_by_id)),
    }


def merge_case_paths(candidate_path: dict[str, Any], boundary_path: dict[str, Any]) -> dict[str, Any]:
    """Frozen merger: error union and clean consensus."""

    candidate_state = str(candidate_path.get("state") or "unresolved")
    boundary_state = str(boundary_path.get("state") or "unresolved")
    if candidate_state not in CASE_STATES or boundary_state not in CASE_STATES:
        raise ValueError("invalid case-path state")
    contributors = []
    if candidate_state == "verified_error":
        contributors.append("candidate_causal_path")
    if boundary_state == "verified_error":
        contributors.append("boundary_path")
    if contributors:
        state = "verified_error"
        responsible = (
            candidate_path.get("responsible_event_id")
            if candidate_state == "verified_error"
            else boundary_path.get("responsible_event_id")
        )
    elif candidate_state == boundary_state == "verified_clean":
        state, responsible = "verified_clean", None
    else:
        state, responsible = "unresolved", None
    return {
        "state": state,
        "responsible_event_id": responsible,
        "contributors": contributors,
        "candidate_path_state": candidate_state,
        "boundary_path_state": boundary_state,
    }


# ============================================================
# Proof-court certificate validation
# ============================================================

def validate_error_certificate(
    row: dict[str, Any], packet: dict[str, Any], candidate_origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    event_order, addressable = _address_space(packet)
    recovery = str(row.get("recovery_state") or "")
    impact_events = row.get("impact_event_ids")
    origin = row.get("origin_event_id")
    control = row.get("agent_control_event_id")
    source_candidate = str(row.get("source_candidate_id") or "")

    def anchored(refs: Any, event_id: Any) -> bool:
        event = str(event_id or "")
        return isinstance(refs, list) and any(
            str(ref) == event or str(ref).startswith(event + "_tool_")
            for ref in refs
        )

    impact_evidence = row.get("impact_evidence_refs")
    causal_evidence = row.get("benchmark_causal_evidence_refs")
    candidate_scope = (
        candidate_origins is None
        or source_candidate in candidate_origins
        and candidate_origins[source_candidate] == str(origin)
    )
    ordered = (
        _event_ref(origin, event_order)
        and _event_ref(control, event_order)
        and _event_refs(impact_events, event_order, nonempty=True)
    )
    if ordered:
        ordered = event_order[str(origin)] <= event_order[str(control)] <= min(event_order[ref] for ref in impact_events)
    checks = {
        "required_fields": not (ERROR_FIELDS - set(row)),
        "certificate_id": bool(str(row.get("certificate_id") or "").strip()),
        "source_candidate": bool(source_candidate) and candidate_scope,
        "origin": _event_ref(origin, event_order),
        "claim_kind": str(row.get("claim_kind") or "") in CLAIM_KINDS,
        "norm_source": str(row.get("norm_source") or "") in NORM_SOURCES,
        "norm_evidence": _evidence_refs(row.get("norm_evidence_refs"), addressable, nonempty=True),
        "breach_type": str(row.get("breach_type") or "") in BREACH_TYPES,
        "violation_evidence": _evidence_refs(row.get("violation_evidence_refs"), addressable, nonempty=True),
        "agent_control": _event_ref(control, event_order),
        "agent_control_evidence": _evidence_refs(row.get("agent_control_evidence_refs"), addressable, nonempty=True),
        "agent_control_evidence_binding": anchored(
            row.get("agent_control_evidence_refs"), control,
        ),
        "impact_kind": str(row.get("impact_kind") or "") in IMPACT_KINDS,
        "impact_events": _event_refs(impact_events, event_order, nonempty=True),
        "impact_evidence": _evidence_refs(row.get("impact_evidence_refs"), addressable, nonempty=True),
        "impact_evidence_binding": isinstance(impact_events, list) and any(
            anchored(impact_evidence, event_id) for event_id in impact_events
        ),
        "recovery_state": recovery in RECOVERY_STATES,
        "recovery_evidence": _evidence_refs(
            row.get("recovery_evidence_refs"), addressable,
            nonempty=recovery in {"post_impact_residual", "pre_impact_repaired"},
        ),
        "benchmark_causal_status": str(row.get("benchmark_causal_status") or "") in BENCHMARK_CAUSAL_STATES,
        "benchmark_causal_evidence": _evidence_refs(
            causal_evidence, addressable,
            nonempty=str(row.get("benchmark_causal_status") or "") in {
                "causes_incorrect_result", "propagates_to_incorrect_result",
            },
        ),
        "benchmark_causal_evidence_binding": (
            str(row.get("benchmark_causal_status") or "") not in {
                "causes_incorrect_result", "propagates_to_incorrect_result",
            }
            or isinstance(impact_events, list) and any(
                anchored(causal_evidence, event_id) for event_id in impact_events
            )
        ),
        "ordered_chain": ordered,
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    repair_projection = project_audit_failures(
        row=row,
        failed_checks=failed,
        required_fields=ERROR_FIELDS,
        check_fields=ERROR_CHECK_FIELDS,
        relational_invariants=ERROR_RELATIONAL_INVARIANTS,
    )
    return {
        "decision": "pass" if not failed else "quarantine",
        "failed_checks": failed,
        **repair_projection,
        "checks": checks,
    }


def validate_defense_certificate(
    row: dict[str, Any], packet: dict[str, Any], candidate_origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    event_order, addressable = _address_space(packet)
    defense = str(row.get("defense_type") or "")
    target_candidate = str(row.get("target_candidate_id") or "")
    target_origin = str(row.get("target_origin_event_id") or "")
    candidate_scope = (
        candidate_origins is None
        or target_candidate in candidate_origins
        and candidate_origins[target_candidate] == target_origin
    )
    checks = {
        "required_fields": not (DEFENSE_FIELDS - set(row)),
        "certificate_id": bool(str(row.get("certificate_id") or "").strip()),
        "target_candidate": bool(target_candidate) and candidate_scope,
        "target_origin": _event_ref(row.get("target_origin_event_id"), event_order),
        "defense_type": defense in DEFENSE_TYPES,
        "evidence": _evidence_refs(row.get("evidence_refs"), addressable, nonempty=True),
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    repair_projection = project_audit_failures(
        row=row,
        failed_checks=failed,
        required_fields=DEFENSE_FIELDS,
        check_fields=DEFENSE_CHECK_FIELDS,
        relational_invariants=DEFENSE_RELATIONAL_INVARIANTS,
    )
    return {
        "decision": "pass" if not failed else "quarantine",
        "failed_checks": failed,
        **repair_projection,
        "checks": checks,
    }


# ============================================================
# Deterministic guardrails against over-prosecution
# ============================================================

def route_evidence_authority(error_certificate: dict[str, Any]) -> str:
    """Route an error certificate to its evidence-authority verifier type."""
    breach_type = str(error_certificate.get("breach_type") or "")
    norm_source = str(error_certificate.get("norm_source") or "")

    # Math route: numerical/logical contradictions that are recomputation-verifiable.
    if breach_type == "contradiction" and norm_source in {"logical_invariant", "prior_trajectory_evidence"}:
        return "math"
    # Tool/state route.
    if breach_type in {"invalid_action", "consequential_omission"}:
        return "tool_state"
    # Retrieval/source route.
    if breach_type == "fabrication":
        return "retrieval_source"
    if breach_type == "contradiction" and norm_source == "external_evidence":
        return "retrieval_source"
    # Instruction/context route.
    if breach_type == "instruction_violation":
        return "instruction_context"
    return "general"


# Keyword-driven materiality screening: it flags contradictions whose
# rationale declares tolerance/rounding language as immaterial. A true
# recomputation-based numeric comparison is intentionally out of scope here
# (see the math evidence-authority route); no tolerance constant is claimed.
EXACT_PRECISION_KEYWORDS = [
    "exact", "precise", "exactly", "precisely", "to the last digit",
    "精确", "准确到", "保留所有小数",
]


def _is_numerical_discrepancy(rationale: str, breach_type: str, norm_source: str) -> bool:
    """Heuristic: does this error certificate allege a numerical/value discrepancy?"""
    if breach_type != "contradiction":
        return False
    if norm_source not in {"logical_invariant", "prior_trajectory_evidence"}:
        return False
    numerical_signals = [
        "value should be", "correct value", "actual value", "incorrect result",
        "expected", "computed", "calculated", "miscalculation", "rounding",
        "off by", "difference", "discrepancy", "≠", "!=", "vs",
    ]
    rationale_lower = rationale.lower()
    return any(signal in rationale_lower for signal in numerical_signals)


def check_materiality(
    error_certificate: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic materiality check for numerical/equivalence errors."""
    breach_type = str(error_certificate.get("breach_type") or "")
    norm_source = str(error_certificate.get("norm_source") or "")
    rationale = str(error_certificate.get("rationale") or "")

    if not _is_numerical_discrepancy(rationale, breach_type, norm_source):
        return {"decision": "unresolved", "reason": "not a numerical discrepancy claim",
                "defense_type": None}

    has_exact_requirement = any(kw in rationale.lower() for kw in EXACT_PRECISION_KEYWORDS)
    if has_exact_requirement:
        return {"decision": "material_error", "reason": "task requires exact precision",
                "defense_type": None}

    tolerance_signals = (
        "approximately", "approximately equal", "within tolerance",
        "rounding tolerance", "rounded to", "equivalent within", "≈",
    )
    has_tolerance_language = any(
        signal in rationale.lower() for signal in tolerance_signals
    )

    if has_tolerance_language:
        return {
            "decision": "immaterial_difference",
            "reason": "numerical discrepancy appears to be within rounding tolerance; "
                      "no evidence of task-required exact precision",
            "defense_type": "materially_equivalent_or_within_tolerance",
        }

    return {"decision": "unresolved",
            "reason": "numerical discrepancy needs math verifier recomputation",
            "defense_type": None}


def check_commitment(
    error_certificate: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic commitment/impact boundary check.

    Distinguishes a rejected/failed tool action (ordinary failure) from a
    committed state change, a pre-impact repair, and a persistent wrong
    state, so over-prosecution of non-committed attempts cannot inflate the
    positive count.
    """
    breach_type = str(error_certificate.get("breach_type") or "")
    if breach_type != "invalid_action":
        return {"decision": "unresolved", "reason": "not an invalid_action claim",
                "defense_type": None}

    impact_kind = str(error_certificate.get("impact_kind") or "")
    recovery_state = str(error_certificate.get("recovery_state") or "")
    rationale = str(error_certificate.get("rationale") or "")

    rejection_signals = ["rejected", "failed", "error returned", "denied",
                         "did not succeed", "was refused", "not accepted"]
    has_rejection = any(s in rationale.lower() for s in rejection_signals)

    if has_rejection and impact_kind == "committed_tool_action":
        return {
            "decision": "rejected_attempt",
            "reason": "tool action was rejected/failed with no state change or incorrect result",
            "defense_type": "rejected_attempt_no_state_change",
        }

    if impact_kind == "process_debt":
        return {"decision": "unresolved", "reason": "process_debt is auxiliary",
                "defense_type": None}

    if recovery_state == "pre_impact_repaired" and impact_kind not in {
        "final_answer", "persistent_external_state",
    }:
        return {"decision": "unresolved",
                "reason": "pre-impact repair with non-persistent impact",
                "defense_type": None}

    return {"decision": "committed_error", "reason": "action appears committed with impact",
            "defense_type": None}


def _defense_dominates(error: dict[str, Any], defense: dict[str, Any]) -> bool:
    """Require a defense to negate the same mechanism as the error proof."""

    defense_type = str(defense.get("defense_type") or "")
    breach_type = str(error.get("breach_type") or "")
    claim_kind = str(error.get("claim_kind") or "")

    if defense_type == "insufficient_evidence":
        return False

    if defense_type == "supported_or_correct":
        if breach_type not in {
            "contradiction", "fabrication", "unsupported_commitment",
        }:
            return False
        error_refs = {
            str(ref)
            for field in (
                "norm_evidence_refs", "violation_evidence_refs",
                "agent_control_evidence_refs", "impact_evidence_refs",
                "benchmark_causal_evidence_refs",
            )
            for ref in (error.get(field) or [])
        }
        defense_refs = {
            str(ref) for ref in (defense.get("evidence_refs") or [])
        }
        return bool(error_refs & defense_refs)
    if defense_type == "external_unadopted":
        return claim_kind == "agent_adoption"
    if defense_type == "explicitly_authorized":
        return breach_type == "instruction_violation"
    if defense_type == "ordinary_failure_without_commitment":
        return breach_type in {"unsupported_commitment", "consequential_omission"}
    if defense_type in {
        "legitimate_retry_with_state_change", "information_gainful_repetition", "required_revalidation",
    }:
        return breach_type == "redundant_repetition"

    if defense_type == "materially_equivalent_or_within_tolerance":
        return breach_type == "contradiction"
    if defense_type == "rejected_attempt_no_state_change":
        return breach_type == "invalid_action"
    if defense_type == "absence_of_observation_not_fabrication":
        return breach_type == "fabrication"

    return False


def _validate_top(value: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    coverage = str(value.get("coverage_state") or "")
    rows = value.get("candidates")
    if coverage not in COVERAGE_STATES:
        raise ValueError("invalid coverage_state")
    if not isinstance(rows, list) or len(rows) > 32 or not all(isinstance(row, dict) for row in rows):
        raise ValueError("invalid certificate list")
    ids = [str(row.get("certificate_id") or "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("certificate ids must be present and unique")
    return coverage, rows


# ============================================================
# Focused prosecution: per-candidate certificate + chain score
# ============================================================

def validate_focused_prosecution(
    row: dict[str, Any], packet: dict[str, Any], candidate_origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a single-candidate focused prosecution certificate.

    Same structural validation as a court error certificate, plus a causal
    chain completeness score for the quarantine feedback loop.  The focused
    prosecution prompt mandates scaffold markers in the rationale; marker
    presence is the primary signal.  When a provider ignores the scaffold
    entirely, fall back to keyword signals so the score stays non-degenerate.
    """
    base_audit = validate_error_certificate(row, packet, candidate_origins)

    rationale = str(row.get("rationale") or "")
    rationale_upper = rationale.upper()
    chain_scores = {}
    markers = {
        "norm_identification": "NORM:",
        "origin_event": "ORIGIN:",
        "agent_control": "CONTROL:",
        "propagation_path": "PROPAGATION:",
        "impact_on_result": "IMPACT:",
        "counterfactual_test": "COUNTERFACTUAL:",
    }
    if all(marker in rationale_upper for marker in markers.values()):
        # Every mandated scaffold marker is present; a stage counts only when
        # its section carries substantive content (not just an empty header).
        marker_order = [(stage, marker) for stage, marker in markers.items()]
        for index, (stage, marker) in enumerate(marker_order):
            start = rationale_upper.find(marker) + len(marker)
            end = (
                rationale_upper.find(marker_order[index + 1][1])
                if index + 1 < len(marker_order) else len(rationale_upper)
            )
            section = rationale[start:end].strip("| :\n\t")
            chain_scores[stage] = len(section) >= 20
    else:
        chain_signals = {
            "norm_identification": ["norm", "obligation", "requirement", "instruction", "contract", "rule"],
            "origin_event": ["origin", "event", "step", "action", "assertion", "omission"],
            "agent_control": ["agent", "controlled", "adopted", "generated", "decided", "chose"],
            "propagation_path": ["propagat", "downstream", "subsequent", "leads to", "causes", "results in"],
            "impact_on_result": ["impact", "result", "answer", "output", "incorrect", "wrong"],
            "counterfactual_test": ["counterfactual", "if corrected", "would fix", "sufficient", "necessary"],
        }
        rationale_lower = rationale.lower()
        for stage, signals in chain_signals.items():
            chain_scores[stage] = any(signal in rationale_lower for signal in signals)

    completeness = sum(chain_scores.values()) / len(chain_scores)
    base_audit["causal_chain_completeness"] = completeness
    base_audit["causal_chain_scores"] = chain_scores
    base_audit["missing_chain_stages"] = [
        stage for stage, present in chain_scores.items() if not present
    ]

    return base_audit


def build_quarantine_feedback(audit: dict[str, Any]) -> str:
    """Build structured feedback for a quarantined certificate to guide retry."""
    failed_checks = audit.get("failed_checks") or []
    missing_stages = audit.get("missing_chain_stages") or []

    feedback_parts = []
    if failed_checks:
        feedback_parts.append(
            f"STRUCTURAL_FAILURES: {', '.join(failed_checks)}. "
            "Fix these field-level issues first."
        )
    if missing_stages:
        stage_guidance = {
            "norm_identification": "Explicitly state which norm/obligation/rule was violated and cite the evidence for that norm.",
            "origin_event": "Identify the specific agent event where the error originated.",
            "agent_control": "Explain how the agent controlled, adopted, or generated this error (not external).",
            "propagation_path": "Trace how the error propagates from origin to downstream events.",
            "impact_on_result": "State the specific impact on the produced result (final_answer, persistent state, etc.).",
            "counterfactual_test": "Argue why correcting THIS origin alone would be causally sufficient to fix the result.",
        }
        guidance = "; ".join(
            f"{stage}: {stage_guidance.get(stage, 'Address this stage.')}"
            for stage in missing_stages
        )
        feedback_parts.append(f"CAUSAL_CHAIN_GAPS: {guidance}")

    if not feedback_parts:
        feedback_parts.append("Certificate was quarantined for unspecified reasons. Reconstruct with full causal chain.")

    return " | ".join(feedback_parts)


# ============================================================
# Consensus gate
# ============================================================

def apply_consensus_gate(
    *, surviving_errors: list[dict[str, Any]], initial_prosecution: dict[str, Any] | None,
    appeal_source_marker: str = "appeal_",
    initial_supported_candidate_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the consensus gate to filter appeal-sourced positives.

    An error certificate whose certificate_id starts with the appeal source
    marker can only survive if at least one INITIAL prosecution certificate
    independently supports the same source_candidate_id with a causal
    benchmark status.  This prevents the appeal layer from creating positives
    that the initial prosecution did not independently find.
    """
    if initial_prosecution is None:
        return (
            [row for row in surviving_errors if not str(row.get("certificate_id") or "").startswith(appeal_source_marker)],
            [row for row in surviving_errors if str(row.get("certificate_id") or "").startswith(appeal_source_marker)],
        )

    initial_supported = set(initial_supported_candidate_ids or ())
    if initial_supported_candidate_ids is None:
        for cert in (initial_prosecution.get("candidates") or []):
            causal_status = str(cert.get("benchmark_causal_status") or "")
            if causal_status in {"causes_incorrect_result", "propagates_to_incorrect_result"}:
                initial_supported.add(str(cert.get("source_candidate_id") or ""))

    passed = []
    blocked = []
    for error in surviving_errors:
        cert_id = str(error.get("certificate_id") or "")
        source_candidate = str(error.get("source_candidate_id") or "")
        if cert_id.startswith(appeal_source_marker):
            if source_candidate in initial_supported:
                passed.append(error)
            else:
                blocked.append(error)
        else:
            passed.append(error)

    return passed, blocked


# ============================================================
# Two-phase localization
# ============================================================

def select_earliest_origin_temporal(
    surviving: list[dict[str, Any]], event_order: dict[str, int],
) -> dict[str, Any] | None:
    """Phase 1 fallback: simple temporal minimum."""
    if not surviving:
        return None
    return min(surviving, key=lambda row: (
        event_order.get(str(row["origin_event_id"]), 10**9),
        str(row["certificate_id"]),
    ))


def select_earliest_origin_counterfactual(
    surviving: list[dict[str, Any]],
    event_order: dict[str, int],
    counterfactual_rankings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Phase 2: counterfactual-sufficiency-based origin selection.

    If counterfactual_rankings are provided (from a pairwise comparison
    provider call), use them to rank origins by causal sufficiency.
    Otherwise, fall back to the temporal minimum.

    Selection algorithm:
    1. If any origin has is_root_cause=True, select the earliest among them.
    2. Otherwise, select the origin with highest causal_sufficiency_score.
    3. Tie-break by temporal order (earlier wins).
    """
    if not surviving:
        return None

    allowed_origins = {
        str(row.get("origin_event_id") or "") for row in surviving
    }
    normalized_rankings: dict[str, dict[str, Any]] = {}
    for origin, ranking in (counterfactual_rankings or {}).items():
        if str(origin) not in allowed_origins or not isinstance(ranking, dict):
            continue
        raw_score = ranking.get("causal_sufficiency_score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            continue
        root = ranking.get("is_root_cause")
        if not isinstance(root, bool):
            continue
        normalized_rankings[str(origin)] = {
            "causal_sufficiency_score": score,
            "is_root_cause": root,
        }

    if not normalized_rankings:
        return select_earliest_origin_temporal(surviving, event_order)

    root_causes = [
        row for row in surviving
        if normalized_rankings.get(str(row["origin_event_id"]), {}).get("is_root_cause", False)
    ]

    if root_causes:
        return min(root_causes, key=lambda row: (
            event_order.get(str(row["origin_event_id"]), 10**9),
            str(row["certificate_id"]),
        ))

    def sort_key(row: dict[str, Any]) -> tuple:
        origin = str(row["origin_event_id"])
        score = normalized_rankings.get(origin, {}).get("causal_sufficiency_score", 0.0)
        return (-score, event_order.get(origin, 10**9), str(row["certificate_id"]))

    return min(surviving, key=sort_key)


# ============================================================
# Appeal defense merge
# ============================================================

def _merge_appeal_defense(
    *, appeal_defense: dict[str, Any], packet: dict[str, Any],
    candidate_origins: dict[str, str],
    valid_defenses: list[dict[str, Any]],
    d_rows: list[dict[str, Any]],
    d_audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge appeal defense certificates into the defense pool.

    The appeal defender produces a second defense bundle that must be
    validated, deduplicated against the base defense, and merged with
    dominance rules before the court can proceed. The merge logic is
    self-contained and has no side effects beyond the three lists it
    returns (rows and audits stay aligned so quarantine bookkeeping can
    zip them without silently truncating).
    """
    ad_coverage, ad_rows = _validate_top(appeal_defense)
    ad_audits = [
        validate_defense_certificate(row, packet, candidate_origins)
        for row in ad_rows
    ]
    if ad_coverage != "complete" or any(
        audit["decision"] != "pass" for audit in ad_audits
    ):
        raise ValueError("required appeal defense is incomplete or invalid")
    ad_valid = [
        row for row, audit in zip(ad_rows, ad_audits)
        if audit["decision"] == "pass"
    ]
    ad_dominating = {
        str(row["target_candidate_id"]): row
        for row in ad_valid
        if str(row.get("defense_type") or "") in DOMINATING_DEFENSES
    }
    valid_defenses = [
        row for row in valid_defenses
        if not (
            str(row.get("defense_type") or "") == "insufficient_evidence"
            and str(row.get("target_candidate_id") or "") in ad_dominating
        )
    ]
    seen_defense_keys = {
        (str(row["target_candidate_id"]), str(row.get("defense_type") or ""))
        for row in valid_defenses
    }
    for row in ad_valid:
        key = (str(row["target_candidate_id"]), str(row.get("defense_type") or ""))
        if key not in seen_defense_keys:
            valid_defenses.append(row)
            seen_defense_keys.add(key)
    d_audits = [*d_audits, *ad_audits]
    d_rows = [*d_rows, *ad_rows]
    return valid_defenses, d_rows, d_audits


# ============================================================
# Deterministic pre-checks (materiality + commitment)
# ============================================================

def _run_deterministic_prechecks(
    *, valid_errors: list[dict[str, Any]], packet: dict[str, Any],
    apply_materiality_check: bool, apply_commitment_check: bool,
    defenses_by_candidate: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run deterministic materiality/commitment pre-checks over valid errors.

    Both pre-checks share one shape — filter valid errors by breach type,
    run a deterministic check, and convert a passing verdict into a
    synthetic defense that joins the dominance pool. Keeping them in one
    place makes the guardrail pipeline (what may demote a prosecution
    certificate before dominance) auditable at a glance.
    """
    materiality_results: dict[str, dict[str, Any]] = {}
    commitment_results: dict[str, dict[str, Any]] = {}
    for row in valid_errors:
        breach_type = str(row.get("breach_type") or "")
        if apply_materiality_check and breach_type == "contradiction":
            check, results, prefix, trigger = (
                check_materiality, materiality_results,
                "materiality_defense_", "immaterial_difference",
            )
        elif apply_commitment_check and breach_type == "invalid_action":
            check, results, prefix, trigger = (
                check_commitment, commitment_results,
                "commitment_defense_", "rejected_attempt",
            )
        else:
            continue
        result = check(row)
        results[str(row["certificate_id"])] = result
        if result["decision"] == trigger and result.get("defense_type"):
            target_id = str(row["source_candidate_id"])
            defenses_by_candidate.setdefault(target_id, []).append({
                "certificate_id": f"{prefix}{row['certificate_id']}",
                "target_candidate_id": target_id,
                "target_origin_event_id": str(row.get("origin_event_id") or ""),
                "defense_type": result["defense_type"],
                "evidence_refs": [str(row.get("origin_event_id") or "")],
                "rationale": result["reason"],
            })
    return materiality_results, commitment_results


# ============================================================
# Consensus gate + pre-ledger restore
# ============================================================

def _apply_consensus_gate_and_pre_ledger(
    *, surviving: list[dict[str, Any]],
    initial_prosecution: dict[str, Any] | None,
    apply_consensus_gate_flag: bool,
    packet: dict[str, Any],
    candidate_origins: dict[str, str],
    defeated: list[str],
    pre_ledger_policy_items: Sequence["PolicyItem"] | None,
    policy_registry,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Apply consensus gate and optional pre-ledger restore policies.

    Returns (surviving, consensus_blocked, pre_ledger_restore_evaluation).
    """
    consensus_blocked: list[dict[str, Any]] = []
    if apply_consensus_gate_flag and initial_prosecution is not None:
        initial_rows = initial_prosecution.get("candidates") or []
        admissible_initial = [
            row for row in initial_rows
            if isinstance(row, dict)
            and validate_error_certificate(row, packet, candidate_origins)["decision"] == "pass"
        ]
        surviving, consensus_blocked = apply_consensus_gate(
            surviving_errors=surviving,
            initial_prosecution={"candidates": admissible_initial},
            initial_supported_candidate_ids=None,
        )

    # A pre-ledger consensus-restore policy may return a blocked certificate
    # to the surviving set BEFORE the occurrence snapshot, so the occurrence
    # ledger and earliest-origin localization flow from the final surviving
    # set (no post-ledger repair inconsistency).
    pre_ledger_restore_evaluation: dict[str, Any] | None = None
    if pre_ledger_policy_items and consensus_blocked:
        def _status_map(rows, key):
            return {
                str(row.get("certificate_id") or ""): str(row.get(key) or "")
                for row in rows
            }

        defeated_ids = set(defeated)
        facts = PreLedgerPolicyFacts(
            consensus_gate_applied=True,
            blocked_certificate_ids=tuple(
                str(row.get("certificate_id") or "") for row in consensus_blocked
            ),
            blocked_certificate_audit_status={
                str(row.get("certificate_id") or ""): "pass"
                for row in consensus_blocked
            },
            blocked_certificate_causal_status=_status_map(
                consensus_blocked, "benchmark_causal_status",
            ),
            blocked_certificate_impact_kind=_status_map(
                consensus_blocked, "impact_kind",
            ),
            blocked_certificate_defense_status={
                str(row.get("certificate_id") or ""): (
                    "dominated"
                    if str(row.get("certificate_id") or "") in defeated_ids
                    else "standing"
                )
                for row in consensus_blocked
            },
            blocked_certificate_quarantine_status={
                str(row.get("certificate_id") or ""): (
                    "quarantined" if row.get("quarantined") else "clean"
                )
                for row in consensus_blocked
            },
            terminal_without_restore="",
        )
        restore_result = evaluate_pre_ledger_policies(
            facts, pre_ledger_policy_items, registry=policy_registry,
        )
        pre_ledger_restore_evaluation = restore_result.to_dict()
        if restore_result.restored_certificate_ids:
            restored_ids = set(restore_result.restored_certificate_ids)
            restored_rows = [
                row for row in consensus_blocked
                if str(row.get("certificate_id") or "") in restored_ids
            ]
            surviving = [*surviving, *restored_rows]
            # The ledger records a certificate in exactly one bucket: a
            # restored row leaves the blocked list as it re-enters the
            # surviving set.
            consensus_blocked = [
                row for row in consensus_blocked
                if str(row.get("certificate_id") or "") not in restored_ids
            ]

    return surviving, consensus_blocked, pre_ledger_restore_evaluation


# ============================================================
# Terminal state resolution + result assembly
# ============================================================

def _resolve_terminal_state(
    *, surviving: list[dict[str, Any]],
    auxiliary_process: list[dict[str, Any]],
    defeated: list[str],
    valid_errors: list[dict[str, Any]],
    valid_defenses: list[dict[str, Any]],
    p_rows: list[dict[str, Any]], p_audits: list[dict[str, Any]],
    d_rows: list[dict[str, Any]], d_audits: list[dict[str, Any]],
    defenses_by_candidate: dict[str, list[dict[str, Any]]],
    materiality_results: dict[str, dict[str, Any]],
    commitment_results: dict[str, dict[str, Any]],
    required: list[str],
    candidate_origins: dict[str, str],
    p_coverage: str, d_coverage: str,
    event_order: dict[str, int],
    counterfactual_rankings: dict[str, dict[str, Any]] | None,
    apply_consensus_gate_flag: bool,
    initial_prosecution: dict[str, Any] | None,
    consensus_blocked: list[dict[str, Any]],
    pre_ledger_restore_evaluation: dict[str, Any] | None,
    focused_prosecution_audits: list[dict[str, Any]] | None,
    policy_items: Sequence["PolicyItem"] | None,
    boundary_path_state: str,
    boundary_authority_valid: bool,
    policy_registry,
) -> dict[str, Any]:
    """Resolve the terminal verdict and assemble the result dictionary.

    After all evidence has been validated, defenses have been merged, and
    the consensus gate has run, the court computes the terminal state
    (verified_error/verified_clean/unresolved), the occurrence ledger, and
    the full result schema. Extracting this phase keeps the
    evidence-processing pipeline readable and makes the terminal contract
    testable in isolation.
    """
    # Truth adjudication precedes ledger append: only causal certificates
    # that survive schema validation, mechanism-matched defenses,
    # deterministic materiality/commitment checks, and (when enabled)
    # consensus establish an occurrence.  Recovery is downstream disposition
    # and cannot erase it.
    occurrence_errors = list(surviving)

    invalid_count = sum(audit["decision"] != "pass" for audit in p_audits + d_audits)
    clean_covered = {
        candidate_id for candidate_id, rows in defenses_by_candidate.items()
        if any(row["defense_type"] in DOMINATING_DEFENSES for row in rows)
    }
    auxiliary_candidates = {str(row["source_candidate_id"]) for row in auxiliary_process}
    positive_candidates = {str(row["source_candidate_id"]) for row in surviving}

    coverage_complete = p_coverage == d_coverage == "complete" and not invalid_count
    # Surviving certificates are empty in every branch that consumes
    # candidate_terminal (the clean/unresolved decision paths), so they
    # cannot contribute to the coverage-complete check.
    candidate_terminal = set(required) <= clean_covered | auxiliary_candidates
    # A clean verdict needs complete coverage AND every required candidate
    # resolved (clean-covered or auxiliary).  A candidate the court could not
    # resolve must stay unresolved — "no incident occurred" is a clean claim,
    # never the residual of an unresolved candidate.
    clean_terminal = coverage_complete and (
        candidate_terminal or (not required and not valid_errors)
    )
    if surviving:
        winner = select_earliest_origin_counterfactual(
            surviving, event_order, counterfactual_rankings,
        )
        state, responsible = "verified_error", str(winner["origin_event_id"])
    elif clean_terminal:
        state, responsible = "verified_clean", None
        winner = None
    else:
        state, responsible = "unresolved", None
        winner = None

    impact_status = None
    if winner is not None:
        impact_status = {
            "none": "unrecovered",
            "pre_impact_repaired": "recovered_before_downstream_impact",
            "post_impact_residual": "recovered_after_impact",
            "unresolved": "unresolved",
        }[str(winner["recovery_state"])]

    occurrence_winner = select_earliest_origin_counterfactual(
        occurrence_errors, event_order, counterfactual_rankings,
    )
    occurrence_state = (
        "verified_error" if occurrence_winner is not None
        else "verified_clean" if clean_terminal
        else "unresolved"
    )
    occurrence_impact_status = None
    if occurrence_winner is not None:
        occurrence_impact_status = {
            "none": "unrecovered",
            "pre_impact_repaired": "recovered_before_downstream_impact",
            "post_impact_residual": "recovered_after_impact",
            "unresolved": "unresolved",
        }[str(occurrence_winner["recovery_state"])]

    all_defense_types = [str(row["defense_type"]) for row in valid_defenses]
    for results in [materiality_results, commitment_results]:
        for result in results.values():
            if result.get("defense_type"):
                all_defense_types.append(str(result["defense_type"]))

    # Machine-readable terminal proof.  The adjudicator used to expose only
    # aggregate counters, forcing the evolution side to guess a root cause
    # from the first quarantined row.  These facts are derived from the
    # exact predicates above and do not participate in the verdict; they
    # make the already-made decision causally inspectable by reflection and
    # diagnostics.
    uncovered_candidates = sorted(
        set(required).difference(clean_covered | auxiliary_candidates)
    )
    invalid_error_ids = sorted(
        str(row.get("certificate_id") or "")
        for row, audit in zip(p_rows, p_audits)
        if audit["decision"] != "pass"
    )
    invalid_defense_ids = sorted(
        str(row.get("certificate_id") or "")
        for row, audit in zip(d_rows, d_audits)
        if audit["decision"] != "pass"
    )
    blocking_facts: list[dict[str, Any]] = []
    if state == "unresolved":
        if p_coverage != "complete":
            blocking_facts.append({
                "predicate": "prosecution_coverage_complete",
                "observed": p_coverage,
                "required": "complete",
            })
        if d_coverage != "complete":
            blocking_facts.append({
                "predicate": "defense_coverage_complete",
                "observed": d_coverage,
                "required": "complete",
            })
        if invalid_error_ids:
            blocking_facts.append({
                "predicate": "error_certificates_valid",
                "certificate_ids": invalid_error_ids,
            })
        if invalid_defense_ids:
            blocking_facts.append({
                "predicate": "defense_certificates_valid",
                "certificate_ids": invalid_defense_ids,
            })
        if uncovered_candidates:
            blocking_facts.append({
                "predicate": "required_candidates_terminal",
                "candidate_ids": uncovered_candidates,
            })
    terminal_basis = {
        "schema_version": "sehd.proof_terminal_basis.v1",
        "state": state,
        "decisive_facts": {
            "surviving_error_certificate_ids": sorted(
                str(row["certificate_id"]) for row in surviving
            ),
            "coverage_complete": coverage_complete,
            "candidate_terminal": candidate_terminal,
        },
        "blocking_facts": blocking_facts,
    }

    result = {
        "state": state,
        "responsible_event_id": responsible,
        "occurrence_state": occurrence_state,
        "incident_occurred": (
            True if occurrence_state == "verified_error"
            else False if occurrence_state == "verified_clean"
            else None
        ),
        "occurrence_responsible_event_id": (
            str(occurrence_winner["origin_event_id"])
            if occurrence_winner is not None else None
        ),
        "occurrence_error_certificate_ids": [
            str(row["certificate_id"]) for row in occurrence_errors
        ],
        "occurrence_error_certificate_count": len(occurrence_errors),
        "occurrence_impact_kind": (
            str(occurrence_winner["impact_kind"])
            if occurrence_winner is not None else None
        ),
        "occurrence_recovery_state": (
            str(occurrence_winner["recovery_state"])
            if occurrence_winner is not None else None
        ),
        "occurrence_impact_status": occurrence_impact_status,
        "occurrence_ledger": {
            "state": occurrence_state,
            "incident_occurred": (
                True if occurrence_state == "verified_error"
                else False if occurrence_state == "verified_clean"
                else None
            ),
            "certificate_ids": [
                str(row["certificate_id"]) for row in occurrence_errors
            ],
            "responsible_event_id": (
                str(occurrence_winner["origin_event_id"])
                if occurrence_winner is not None else None
            ),
            "impact_kind": (
                str(occurrence_winner["impact_kind"])
                if occurrence_winner is not None else None
            ),
            "recovery_state": (
                str(occurrence_winner["recovery_state"])
                if occurrence_winner is not None else None
            ),
            "impact_status": occurrence_impact_status,
        },
        "winning_error_certificate_id": str(winner["certificate_id"]) if winner is not None else None,
        "winning_impact_kind": str(winner["impact_kind"]) if winner is not None else None,
        "impact_status": impact_status,
        "required_candidate_count": len(required),
        "required_origin_count": len(set(candidate_origins.values())),
        "resolved_candidate_count": len(clean_covered | auxiliary_candidates | positive_candidates),
        "valid_error_certificate_count": len(valid_errors),
        "valid_defense_certificate_count": len(valid_defenses),
        "surviving_error_certificate_ids": [str(row["certificate_id"]) for row in surviving],
        "auxiliary_process_certificate_ids": [str(row["certificate_id"]) for row in auxiliary_process],
        # The candidate partition the episode projection consumes: which
        # candidates carry surviving (positive) error evidence and which are
        # covered by a dominating clean defense.
        "positive_candidate_ids": sorted(positive_candidates),
        "clean_covered_candidate_ids": sorted(clean_covered),
        # A surviving causal error is the strongest verified incident: it
        # must outrank an auxiliary process-only note, never be masked by it.
        "wide_construct_state": (
            "verified_hallucination" if surviving else
            "verified_process_incident" if auxiliary_process else
            "no_verified_incident"
        ),
        "defeated_error_certificate_ids": defeated,
        "quarantined_error_certificate_ids": [
            str(row.get("certificate_id") or "")
            for row, audit in zip(p_rows, p_audits) if audit["decision"] != "pass"
        ],
        "quarantined_defense_certificate_ids": [
            str(row.get("certificate_id") or "")
            for row, audit in zip(d_rows, d_audits) if audit["decision"] != "pass"
        ],
        "prosecution_coverage": p_coverage,
        "defense_coverage": d_coverage,
        "terminal_basis": terminal_basis,
        # Audits carry the certificate id of the row they judged, so
        # consumers key by id instead of re-deriving a positional zip
        # against whichever prosecution subset they happen to hold.
        "error_audits": [
            {**audit, "certificate_id": str(row.get("certificate_id") or "")}
            for row, audit in zip(p_rows, p_audits)
        ],
        "defense_audits": d_audits,
        "defense_type_counts": dict(Counter(all_defense_types)),
        "materiality_results": materiality_results,
        "commitment_results": commitment_results,
        "evidence_authority_routes": {
            str(row["certificate_id"]): route_evidence_authority(row)
            for row in valid_errors
        },
        "synthetic_defense_count": sum(
            1 for results in [materiality_results, commitment_results]
            for result in results.values()
            if result.get("defense_type")
        ),
        "consensus_gate_blocked_ids": [
            str(row["certificate_id"]) for row in consensus_blocked
        ],
        "consensus_gate_applied": apply_consensus_gate_flag and initial_prosecution is not None,
        "pre_ledger_restore": pre_ledger_restore_evaluation,
        "counterfactual_rankings_used": counterfactual_rankings is not None,
        "focused_prosecution_chain_completeness": (
            [a.get("causal_chain_completeness", 0.0) for a in focused_prosecution_audits]
            if focused_prosecution_audits else None
        ),
    }

    # L2 policy hook: policies are structural coverage-validity
    # interventions that may shift coverage.  The court owns the terminal
    # rule (candidate_terminal / required / valid_errors are only
    # addressable here), so it recomputes the terminal state from the
    # effective coverages — the evaluator carries no simplified copy. The
    # boundary path state is an engine-level fact injected by the caller.
    # Policies act on the terminal edge only: the occurrence ledger records
    # what the evidence established before policy, so it is intentionally
    # not recomputed here.
    if policy_items:
        facts = build_adjudication_facts(
            result,
            boundary_path_state=boundary_path_state,
            boundary_authority_valid=boundary_authority_valid,
        )
        evaluation = evaluate_policies(
            facts, policy_items, registry=policy_registry,
        )
        eff_prosecution = evaluation.effective_prosecution_coverage
        eff_defense = evaluation.effective_defense_coverage
        coverage_shifted = eff_prosecution != p_coverage or eff_defense != d_coverage
        if coverage_shifted:
            eff_coverage_complete = (
                eff_prosecution == eff_defense == "complete" and not invalid_count
            )
            if surviving:
                recomputed_state = "verified_error"
            elif eff_coverage_complete and candidate_terminal:
                recomputed_state = "verified_clean"
            elif eff_coverage_complete and not required and not valid_errors:
                recomputed_state = "verified_clean"
            else:
                recomputed_state = "unresolved"
        else:
            recomputed_state = state
        if coverage_shifted or recomputed_state != state:
            result["pre_policy_state"] = state
            result["state"] = recomputed_state
        if (
            evaluation.terminal_override == "verified_clean"
            and result["state"] == "unresolved"
        ):
            # Boundary consumption acts on the terminal edge only. The
            # evaluator already enforced every precondition (verified-clean
            # boundary, valid authority, unresolved terminal, zero surviving
            # errors), so this transition cannot cross a verified_error and
            # the occurrence ledger is intentionally not recomputed.
            result.setdefault("pre_policy_state", state)
            result["state"] = "verified_clean"
        policy_evaluation = evaluation.to_dict()
        # The court owns the post-policy terminal state; project it onto the
        # evaluation record so no consumer can read a divergent copy.
        policy_evaluation["terminal_state"] = result["state"]
        result["policy_evaluation"] = policy_evaluation

    return result


# ============================================================
# Main adjudication: proof court with focused prosecution, calibrated
# appeal, two-phase localization, and deterministic pre-checks.
# ============================================================

def adjudicate_proof_court(
    *, required_candidates: list[dict[str, Any]], prosecution: dict[str, Any],
    defense: dict[str, Any], packet: dict[str, Any],
    apply_materiality_check: bool = True,
    apply_commitment_check: bool = True,
    appeal_defense: dict[str, Any] | None = None,
    initial_prosecution: dict[str, Any] | None = None,
    apply_consensus_gate_flag: bool = True,
    counterfactual_rankings: dict[str, dict[str, Any]] | None = None,
    focused_prosecution_audits: list[dict[str, Any]] | None = None,
    policy_items: Sequence["PolicyItem"] | None = None,
    boundary_path_state: str = "",
    boundary_authority_valid: bool = False,
    policy_registry=None,
    pre_ledger_policy_items: Sequence["PolicyItem"] | None = None,
) -> dict[str, Any]:
    """Adjudicate the proof court with consensus gate and counterfactual localization.

    ``policy_items`` optionally applies deterministic L2 coverage-validity
    policies after adjudication; ``boundary_path_state`` supplies the
    engine-level boundary fact that the court itself cannot address. Both
    default to inert.  ``boundary_authority_valid`` certifies that the
    boundary path came from the deterministic engine path with a passing
    contract validation; it defaults to False so boundary consumption stays
    fail-closed for any caller that cannot establish it.
    """

    event_order, _ = _address_space(packet)
    candidate_origins: dict[str, str] = {}
    for row in required_candidates:
        candidate_id = str(row.get("candidate_id") or "")
        origin = str(row.get("origin_hypothesis_event_id") or "")
        if not candidate_id or candidate_id in candidate_origins:
            raise ValueError("required candidate ids must be present and unique")
        if origin not in event_order:
            raise ValueError("required candidate origin is not addressable")
        candidate_origins[candidate_id] = origin
    required = list(candidate_origins)
    p_coverage, p_rows = _validate_top(prosecution)
    d_coverage, d_rows = _validate_top(defense)
    p_audits = [validate_error_certificate(row, packet, candidate_origins) for row in p_rows]
    d_audits = [validate_defense_certificate(row, packet, candidate_origins) for row in d_rows]
    valid_errors = [row for row, audit in zip(p_rows, p_audits) if audit["decision"] == "pass"]
    valid_defenses = [row for row, audit in zip(d_rows, d_audits) if audit["decision"] == "pass"]

    # Merge appeal defender into defense pool.
    if appeal_defense is not None:
        valid_defenses, d_rows, d_audits = _merge_appeal_defense(
            appeal_defense=appeal_defense,
            packet=packet,
            candidate_origins=candidate_origins,
            valid_defenses=valid_defenses,
            d_rows=d_rows,
            d_audits=d_audits,
        )

    defenses_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in valid_defenses:
        defenses_by_candidate.setdefault(str(row["target_candidate_id"]), []).append(row)

    materiality_results, commitment_results = _run_deterministic_prechecks(
        valid_errors=valid_errors, packet=packet,
        apply_materiality_check=apply_materiality_check,
        apply_commitment_check=apply_commitment_check,
        defenses_by_candidate=defenses_by_candidate,
    )

    # Defense dominance.
    surviving = []
    auxiliary_process = []
    defeated = []
    for row in valid_errors:
        source_candidate = str(row["source_candidate_id"])
        dominates = any(
            _defense_dominates(row, item)
            for item in defenses_by_candidate.get(source_candidate, [])
        )
        if not dominates:
            if (
                row["benchmark_causal_status"] in {"causes_incorrect_result", "propagates_to_incorrect_result"}
                and row["impact_kind"] != "process_debt"
            ):
                surviving.append(row)
            elif row["benchmark_causal_status"] == "process_only" or row["impact_kind"] == "process_debt":
                auxiliary_process.append(row)
        else:
            defeated.append(str(row["certificate_id"]))

    # Consensus gate. Only admissible initial certificates count as
    # independent support; quarantined raw model output has no voting power.
    # ``initial_supported_candidate_ids`` stays None so the gate derives it
    # from the admissible initial prosecution itself: deriving it from the
    # merged surviving rows would drop candidates whose initial certificate
    # was deduplicated away by _merge_prosecutions (identical initial+appeal
    # certs keep only the appeal row), erasing independently verified
    # positives and blocking the appeal certificate that corroborates them.
    surviving, consensus_blocked, pre_ledger_restore_evaluation = (
        _apply_consensus_gate_and_pre_ledger(
            surviving=surviving,
            initial_prosecution=initial_prosecution,
            apply_consensus_gate_flag=apply_consensus_gate_flag,
            packet=packet,
            candidate_origins=candidate_origins,
            defeated=defeated,
            pre_ledger_policy_items=pre_ledger_policy_items,
            policy_registry=policy_registry,
        )
    )

    return _resolve_terminal_state(
        surviving=surviving, auxiliary_process=auxiliary_process,
        defeated=defeated, valid_errors=valid_errors,
        valid_defenses=valid_defenses, p_rows=p_rows, p_audits=p_audits,
        d_rows=d_rows, d_audits=d_audits,
        defenses_by_candidate=defenses_by_candidate,
        materiality_results=materiality_results,
        commitment_results=commitment_results,
        required=required, candidate_origins=candidate_origins,
        p_coverage=p_coverage, d_coverage=d_coverage,
        event_order=event_order,
        counterfactual_rankings=counterfactual_rankings,
        apply_consensus_gate_flag=apply_consensus_gate_flag,
        initial_prosecution=initial_prosecution,
        consensus_blocked=consensus_blocked,
        pre_ledger_restore_evaluation=pre_ledger_restore_evaluation,
        focused_prosecution_audits=focused_prosecution_audits,
        policy_items=policy_items,
        boundary_path_state=boundary_path_state,
        boundary_authority_valid=boundary_authority_valid,
        policy_registry=policy_registry,
    )
