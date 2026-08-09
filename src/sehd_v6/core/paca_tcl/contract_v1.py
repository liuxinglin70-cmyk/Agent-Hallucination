"""PACA-TCL v1 typed causal-ledger contract and deterministic adjudicator.

PACA-TCL retains PACA's dependency-aware candidate discovery, but replaces
continuous CRS scoring with candidate-local, addressable obligations inspired
by the strongest BCCC boundary controls.  Provider judgements are quarantined
per candidate: one malformed candidate cannot erase a separate valid root, and
it can never silently become a clean decision.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


AXES = {"F", "Fa", "P", "S"}
VIOLATION_STATES = {"verified_violation", "verified_non_violation", "unresolved"}
ACTOR_STATES = {
    "agent_caused",
    "agent_contributed",
    "environment_caused",
    "external_input_caused",
    "unresolved",
}
BOUNDARY_STATES = {"cleared", "triggered", "unresolved"}
PROPAGATION_STATES = {
    "no_consumption",
    "recovered_before_impact",
    "impacted_then_recovered",
    "unrecovered_impact",
    "unresolved",
}
IMPACT_SURFACES = {"A", "X", "R"}
COVERAGE_STATES = {"complete", "incomplete", "unresolved"}

REQUIRED_CANDIDATE_FIELDS = {
    "candidate_id",
    "axes",
    "candidate_kind",
    "trigger_event_id",
    "origin_event_id",
    "claim_or_commitment",
    "dependency_event_ids",
    "violation_state",
    "actor_state",
    "boundary_state",
    "propagation_state",
    "residual_impact",
    "positive_evidence_refs",
    "actor_evidence_refs",
    "boundary_evidence_refs",
    "consumption_event_ids",
    "consumption_evidence_refs",
    "impact_event_ids",
    "impact_evidence_refs",
    "recovery_event_id",
    "recovery_evidence_refs",
    "impact_surfaces",
    "rationale",
}


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
