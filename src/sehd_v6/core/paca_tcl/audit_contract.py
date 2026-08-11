"""Typed repair vocabulary for proof-court certificate audits.

The proof court reports predicate names (for example ``ordered_chain``),
whereas providers emit certificate fields.  Treating those two namespaces as
the same made reflection propose field edits for non-existent fields.  This
module is the single translation owner used by both the court and BCCSE:

* ``failed_checks`` names the violated validation predicates;
* ``missing_fields`` names provider fields that are actually empty/absent;
* ``failed_fields`` names all provider fields participating in them;
* ``failed_invariants`` names predicates that require a relational repair.

The three projections deliberately coexist.  A field repair is appropriate
for an empty scalar/list; an invariant repair is appropriate when populated
fields fail a cross-field or trajectory-addressability relation.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


ERROR_CHECK_FIELDS: Mapping[str, tuple[str, ...]] = {
    "certificate_id": ("certificate_id",),
    "source_candidate": ("source_candidate_id",),
    "origin": ("origin_event_id",),
    "claim_kind": ("claim_kind",),
    "norm_source": ("norm_source",),
    "norm_evidence": ("norm_evidence_refs",),
    "breach_type": ("breach_type",),
    "violation_evidence": ("violation_evidence_refs",),
    "agent_control": ("agent_control_event_id",),
    "agent_control_evidence": ("agent_control_evidence_refs",),
    "agent_control_evidence_binding": (
        "agent_control_event_id", "agent_control_evidence_refs",
    ),
    "impact_kind": ("impact_kind",),
    "impact_events": ("impact_event_ids",),
    "impact_evidence": ("impact_evidence_refs",),
    "impact_evidence_binding": ("impact_event_ids", "impact_evidence_refs"),
    "recovery_state": ("recovery_state",),
    "recovery_evidence": ("recovery_evidence_refs",),
    "benchmark_causal_status": ("benchmark_causal_status",),
    "benchmark_causal_evidence": ("benchmark_causal_evidence_refs",),
    "benchmark_causal_evidence_binding": (
        "impact_event_ids", "benchmark_causal_evidence_refs",
    ),
    "ordered_chain": (
        "origin_event_id", "agent_control_event_id", "impact_event_ids",
    ),
    "rationale": ("rationale",),
}

DEFENSE_CHECK_FIELDS: Mapping[str, tuple[str, ...]] = {
    "certificate_id": ("certificate_id",),
    "target_candidate": ("target_candidate_id",),
    "target_origin": ("target_origin_event_id",),
    "defense_type": ("defense_type",),
    "evidence": ("evidence_refs",),
    "rationale": ("rationale",),
}

# These checks cannot be repaired by merely making one field non-empty.  Their
# semantics depend on two or more fields, scope membership, or trajectory
# addressability, so they form the closed invariant action surface.
ERROR_RELATIONAL_INVARIANTS = frozenset({
    "source_candidate", "origin", "norm_evidence", "violation_evidence",
    "agent_control", "agent_control_evidence",
    "agent_control_evidence_binding", "impact_events", "impact_evidence",
    "impact_evidence_binding", "recovery_evidence",
    "benchmark_causal_evidence", "benchmark_causal_evidence_binding",
    "ordered_chain",
})
DEFENSE_RELATIONAL_INVARIANTS = frozenset({
    "target_candidate", "target_origin", "evidence",
})

ERROR_INVARIANT_TARGETS = tuple(sorted(ERROR_CHECK_FIELDS))
DEFENSE_INVARIANT_TARGETS = tuple(sorted(DEFENSE_CHECK_FIELDS))


def project_audit_failures(
    *,
    row: Mapping[str, Any],
    failed_checks: Sequence[str],
    required_fields: Sequence[str],
    check_fields: Mapping[str, Sequence[str]],
    relational_invariants: frozenset[str],
) -> dict[str, list[str]]:
    """Return normalized provider-field and invariant repair targets."""
    failed = sorted({str(name) for name in failed_checks if str(name)})
    missing_required = (
        sorted(set(str(name) for name in required_fields).difference(row))
        if "required_fields" in failed else []
    )
    fields = set(missing_required)
    missing = set(missing_required)
    invariants: set[str] = set()
    for check in failed:
        if check == "required_fields":
            continue
        participating = tuple(
            str(name) for name in check_fields.get(check, ())
        )
        fields.update(participating)
        missing.update(
            name
            for name in participating
            if row.get(name) in (None, "", [], {})
        )
        if check in relational_invariants:
            invariants.add(check)
    return {
        "missing_fields": sorted(missing),
        "failed_fields": sorted(fields),
        "failed_invariants": sorted(invariants),
    }
