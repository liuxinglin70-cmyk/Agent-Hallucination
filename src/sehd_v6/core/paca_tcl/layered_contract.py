"""PACA-TCL v2: genuinely layered contracts and deterministic case fusion.

The v1 experiment asked one model to emit qualification, responsibility,
boundary, propagation, recovery, and impact fields at once.  V2 makes those
obligations independent: qualification never has to manufacture propagation
fields, and propagation is requested only for candidates that survive the
qualification gate.  A separate BCCC-style boundary path is fused by a frozen
error-union / clean-consensus rule.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import contract_v1 as v1


CASE_STATES = {"verified_error", "verified_clean", "unresolved"}

QUALIFICATION_FIELDS = {
    "candidate_id", "violation_state", "actor_state", "boundary_state",
    "positive_evidence_refs", "actor_evidence_refs", "boundary_evidence_refs",
    "rationale",
}
PROPAGATION_FIELDS = {
    "candidate_id", "propagation_state", "residual_impact",
    "consumption_event_ids", "consumption_evidence_refs",
    "impact_event_ids", "impact_evidence_refs", "recovery_event_id",
    "recovery_evidence_refs", "impact_surfaces", "rationale",
}


def _failed(checks: dict[str, bool]) -> list[str]:
    return sorted(name for name, passed in checks.items() if not passed)


def _address_space(packet: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    return v1._address_space(packet)


def validate_qualification(row: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the small semantic/boundary layer without causal-chain fields."""

    _, addressable = _address_space(packet)
    violation = str(row.get("violation_state") or "")
    actor = str(row.get("actor_state") or "")
    boundary = str(row.get("boundary_state") or "")
    checks = {
        "required_fields": not (QUALIFICATION_FIELDS - set(row)),
        "candidate_id": bool(str(row.get("candidate_id") or "").strip()),
        "violation_state": violation in v1.VIOLATION_STATES,
        "actor_state": actor in v1.ACTOR_STATES,
        "boundary_state": boundary in v1.BOUNDARY_STATES,
        "positive_evidence": v1._evidence_refs(
            row.get("positive_evidence_refs"), addressable,
            nonempty=violation == "verified_violation",
        ),
        "actor_evidence": v1._evidence_refs(
            row.get("actor_evidence_refs"), addressable,
            nonempty=actor != "unresolved",
        ),
        "boundary_evidence": v1._evidence_refs(
            row.get("boundary_evidence_refs"), addressable,
            nonempty=boundary == "triggered",
        ),
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = _failed(checks)
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
            and v1._event_refs(consumption, event_order, nonempty=True)
            and v1._event_refs(impact, event_order, nonempty=True)
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
            origin_event_id in event_order and v1._event_ref(recovery, event_order)
            and event_order[origin_event_id] <= event_order[str(recovery)] and not impact
        )
    elif state == "impacted_then_recovered":
        consistent = chain_ordered and v1._event_ref(recovery, event_order)
        if consistent:
            consistent = event_order[str(recovery)] >= max(event_order[ref] for ref in impact)
    elif state == "unrecovered_impact":
        consistent = chain_ordered and recovery is None

    checks = {
        "required_fields": not (PROPAGATION_FIELDS - set(row)),
        "candidate_id": bool(str(row.get("candidate_id") or "").strip()),
        "propagation_state": state in v1.PROPAGATION_STATES,
        "residual_impact": isinstance(row.get("residual_impact"), bool),
        "consumption_events": v1._event_refs(consumption, event_order),
        "consumption_evidence": v1._evidence_refs(
            row.get("consumption_evidence_refs"), addressable, nonempty=impacted
        ),
        "impact_events": v1._event_refs(impact, event_order),
        "impact_evidence": v1._evidence_refs(row.get("impact_evidence_refs"), addressable, nonempty=impacted),
        "recovery_addressable": v1._event_ref(recovery, event_order, nullable=True),
        "recovery_evidence": v1._evidence_refs(
            row.get("recovery_evidence_refs"), addressable, nonempty=recovery is not None
        ),
        "impact_surfaces": isinstance(surfaces, list) and set(map(str, surfaces)) <= v1.IMPACT_SURFACES,
        "ordered_causal_chain": chain_ordered,
        "propagation_consistent": consistent,
        "evidence_denied_by_state": not v1._evidence_denied_by_state(state, row),
        "impacted_has_surface": not impacted or bool(surfaces),
        "nonimpacted_has_no_surface": impacted or not surfaces,
        "residual_consistent": not row.get("residual_impact") or impacted,
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = _failed(checks)
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

    if census_coverage not in v1.COVERAGE_STATES or qualification_coverage not in v1.COVERAGE_STATES:
        raise ValueError("invalid coverage state")
    if propagation_coverage not in v1.COVERAGE_STATES:
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
    """Frozen P0-supported merger: error union and clean consensus."""

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
