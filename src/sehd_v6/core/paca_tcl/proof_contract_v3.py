"""PACA-TCL v3 proof-court contract.

Two independent provider paths construct error and clean certificates.  This
module validates only addressable proof obligations and resolves them with a
deterministic lattice.  Provider labels, scores, and route identity never enter
the final rule.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import contract_v1 as v1


COVERAGE_STATES = v1.COVERAGE_STATES
CLAIM_KINDS = {"agent_assertion", "agent_action", "agent_omission", "agent_adoption"}
NORM_SOURCES = {"user_instruction", "tool_contract", "prior_trajectory_evidence", "logical_invariant", "external_evidence"}
BREACH_TYPES = {
    "contradiction", "fabrication", "unsupported_commitment", "instruction_violation",
    "invalid_action", "consequential_omission", "redundant_repetition",
}
IMPACT_KINDS = {
    "final_answer", "persistent_external_state", "persistent_workspace_state",
    "committed_tool_action", "unrecovered_reasoning_dependency", "process_debt",
}
RECOVERY_STATES = {"none", "post_impact_residual", "pre_impact_repaired", "unresolved"}
BENCHMARK_CAUSAL_STATES = {
    "causes_incorrect_result", "propagates_to_incorrect_result", "process_only", "unresolved",
}
DEFENSE_TYPES = {
    "external_unadopted", "explicitly_authorized", "supported_or_correct",
    "ordinary_failure_without_commitment", "legitimate_retry_with_state_change",
    "information_gainful_repetition", "required_revalidation", "insufficient_evidence",
}
DOMINATING_DEFENSES = DEFENSE_TYPES - {"insufficient_evidence"}

ERROR_FIELDS = {
    "certificate_id", "source_candidate_id", "origin_event_id", "claim_kind", "norm_source", "norm_evidence_refs",
    "breach_type", "violation_evidence_refs", "agent_control_event_id", "agent_control_evidence_refs",
    "impact_kind", "impact_event_ids", "impact_evidence_refs", "recovery_state",
    "recovery_evidence_refs", "benchmark_causal_status", "benchmark_causal_evidence_refs", "rationale",
}
DEFENSE_FIELDS = {
    "certificate_id", "target_candidate_id", "target_origin_event_id",
    "defense_type", "evidence_refs", "rationale",
}


def _address_space(packet: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    return v1._address_space(packet)


def validate_error_certificate(
    row: dict[str, Any], packet: dict[str, Any], candidate_origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    event_order, addressable = _address_space(packet)
    recovery = str(row.get("recovery_state") or "")
    impact_events = row.get("impact_event_ids")
    origin = row.get("origin_event_id")
    control = row.get("agent_control_event_id")
    source_candidate = str(row.get("source_candidate_id") or "")
    candidate_scope = (
        candidate_origins is None
        or source_candidate in candidate_origins
        and candidate_origins[source_candidate] == str(origin)
    )
    ordered = (
        v1._event_ref(origin, event_order)
        and v1._event_ref(control, event_order)
        and v1._event_refs(impact_events, event_order, nonempty=True)
    )
    if ordered:
        ordered = event_order[str(origin)] <= event_order[str(control)] <= min(event_order[ref] for ref in impact_events)
    checks = {
        "required_fields": not (ERROR_FIELDS - set(row)),
        "certificate_id": bool(str(row.get("certificate_id") or "").strip()),
        "source_candidate": bool(source_candidate) and candidate_scope,
        "origin": v1._event_ref(origin, event_order),
        "claim_kind": str(row.get("claim_kind") or "") in CLAIM_KINDS,
        "norm_source": str(row.get("norm_source") or "") in NORM_SOURCES,
        "norm_evidence": v1._evidence_refs(row.get("norm_evidence_refs"), addressable, nonempty=True),
        "breach_type": str(row.get("breach_type") or "") in BREACH_TYPES,
        "violation_evidence": v1._evidence_refs(row.get("violation_evidence_refs"), addressable, nonempty=True),
        "agent_control": v1._event_ref(control, event_order),
        "agent_control_evidence": v1._evidence_refs(row.get("agent_control_evidence_refs"), addressable, nonempty=True),
        "impact_kind": str(row.get("impact_kind") or "") in IMPACT_KINDS,
        "impact_events": v1._event_refs(impact_events, event_order, nonempty=True),
        "impact_evidence": v1._evidence_refs(row.get("impact_evidence_refs"), addressable, nonempty=True),
        "recovery_state": recovery in RECOVERY_STATES,
        "recovery_evidence": v1._evidence_refs(
            row.get("recovery_evidence_refs"), addressable,
            nonempty=recovery in {"post_impact_residual", "pre_impact_repaired"},
        ),
        "benchmark_causal_status": str(row.get("benchmark_causal_status") or "") in BENCHMARK_CAUSAL_STATES,
        "benchmark_causal_evidence": v1._evidence_refs(
            row.get("benchmark_causal_evidence_refs"), addressable,
            nonempty=str(row.get("benchmark_causal_status") or "") in {
                "causes_incorrect_result", "propagates_to_incorrect_result",
            },
        ),
        "ordered_chain": ordered,
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {"decision": "pass" if not failed else "quarantine", "failed_checks": failed, "checks": checks}


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
        "target_origin": v1._event_ref(row.get("target_origin_event_id"), event_order),
        "defense_type": defense in DEFENSE_TYPES,
        "evidence": v1._evidence_refs(row.get("evidence_refs"), addressable, nonempty=True),
        "rationale": bool(str(row.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {"decision": "pass" if not failed else "quarantine", "failed_checks": failed, "checks": checks}


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


def _defense_dominates(error: dict[str, Any], defense: dict[str, Any]) -> bool:
    """Require a defense to negate the same mechanism as the error proof."""

    defense_type = str(defense.get("defense_type") or "")
    breach_type = str(error.get("breach_type") or "")
    claim_kind = str(error.get("claim_kind") or "")
    if defense_type == "insufficient_evidence":
        return False
    if defense_type == "supported_or_correct":
        return True
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
    return False


def adjudicate_proof_court(
    *, required_candidates: list[dict[str, Any]], prosecution: dict[str, Any],
    defense: dict[str, Any], packet: dict[str, Any],
) -> dict[str, Any]:
    """Apply proof admissibility, defense dominance, and fail-closed coverage."""

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

    defenses_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in valid_defenses:
        defenses_by_candidate.setdefault(str(row["target_candidate_id"]), []).append(row)
    surviving = []
    auxiliary_process = []
    defeated = []
    for row in valid_errors:
        source_candidate = str(row["source_candidate_id"])
        # Recovery is an impact attribute, not an incident eraser.  Unknown
        # recovery cannot veto a separately proven incorrect result.
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

    invalid_count = sum(audit["decision"] != "pass" for audit in p_audits + d_audits)
    clean_covered = {
        candidate_id for candidate_id, rows in defenses_by_candidate.items()
        if any(row["defense_type"] in DOMINATING_DEFENSES for row in rows)
    }
    auxiliary_candidates = {str(row["source_candidate_id"]) for row in auxiliary_process}
    positive_candidates = {str(row["source_candidate_id"]) for row in surviving}
    if surviving:
        winner = min(surviving, key=lambda row: (event_order[str(row["origin_event_id"])], str(row["certificate_id"])))
        state, responsible = "verified_error", str(winner["origin_event_id"])
    else:
        complete = p_coverage == d_coverage == "complete" and not invalid_count
        if complete and set(required) <= clean_covered | auxiliary_candidates:
            state, responsible = "verified_clean", None
        elif complete and not required and not valid_errors:
            state, responsible = "verified_clean", None
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
    return {
        "state": state,
        "responsible_event_id": responsible,
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
        "wide_construct_state": "verified_process_incident" if auxiliary_process else (
            "verified_hallucination" if surviving else "no_verified_incident"
        ),
        "defeated_error_certificate_ids": defeated,
        "quarantined_error_certificate_ids": [str(row.get("certificate_id") or "") for row, audit in zip(p_rows, p_audits) if audit["decision"] != "pass"],
        "quarantined_defense_certificate_ids": [str(row.get("certificate_id") or "") for row, audit in zip(d_rows, d_audits) if audit["decision"] != "pass"],
        "prosecution_coverage": p_coverage,
        "defense_coverage": d_coverage,
        "error_audits": p_audits,
        "defense_audits": d_audits,
        "defense_type_counts": dict(Counter(str(row["defense_type"]) for row in valid_defenses)),
    }
