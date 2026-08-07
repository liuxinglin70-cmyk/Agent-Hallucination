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
        "agent_control_evidence_binding": anchored(
            row.get("agent_control_evidence_refs"), control,
        ),
        "impact_kind": str(row.get("impact_kind") or "") in IMPACT_KINDS,
        "impact_events": v1._event_refs(impact_events, event_order, nonempty=True),
        "impact_evidence": v1._evidence_refs(row.get("impact_evidence_refs"), addressable, nonempty=True),
        "impact_evidence_binding": isinstance(impact_events, list) and any(
            anchored(impact_evidence, event_id) for event_id in impact_events
        ),
        "recovery_state": recovery in RECOVERY_STATES,
        "recovery_evidence": v1._evidence_refs(
            row.get("recovery_evidence_refs"), addressable,
            nonempty=recovery in {"post_impact_residual", "pre_impact_repaired"},
        ),
        "benchmark_causal_status": str(row.get("benchmark_causal_status") or "") in BENCHMARK_CAUSAL_STATES,
        "benchmark_causal_evidence": v1._evidence_refs(
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
    return {"decision": "pass" if not failed else "quarantine", "failed_checks": failed, "checks": checks}
