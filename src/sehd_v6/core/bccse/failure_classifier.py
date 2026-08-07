"""Deterministic failure classifier for SEHD v6.

Attribution answers exactly one question: *at which layer, on which
artifact, did the failure happen?* Routing an unownable failure to some
module anyway ends in no-edit steps or no-effect replays, so classification
is a pure deterministic function of the addressable failure shape of one
detection record — no guessing, no LLM, no heuristics. The two evolvable
surfaces are:

- L1 = the 8 provider-rule modules (artifact = the provider-emitted
  candidate / certificate / evidence field),
- L2 = the adjudication policies (artifacts = consensus-gate policy /
  coverage-validity policy / boundary-consumption policy).

Classification is a pure deterministic function of the addressable
failure shape of one detection record — no guessing, no LLM, no
heuristics. Every predicate test goes through the ``v6_contracts``
``L2_PREDICATES`` registry (single source of truth); this module never
re-implements predicate logic.

Input contract for ``classify_failure(record)``
-----------------------------------------------
``record`` is the per-detection record dict with these addressable
fields (all defensively read; missing fields never raise):

- ``record["adjudication"]``: proof-court result dict with keys
  ``state``, ``prosecution_coverage``, ``defense_coverage``,
  ``valid_error_certificate_count``, ``valid_defense_certificate_count``,
  ``surviving_error_certificate_ids``,
  ``quarantined_error_certificate_ids``,
  ``quarantined_defense_certificate_ids``,
  ``consensus_gate_applied``, ``consensus_gate_blocked_ids``,
  ``required_candidate_count``, ``error_audits``, ``defense_audits``.
- ``record["boundary_path_state"]``: str, may be absent/empty.
- ``record["provider_artifacts"]``: optional list; each item has
  ``{"kind": "candidate" | "error_certificate" | "defense_certificate",
  "module_hint": str, "malformed": bool, "missing_fields": [str]}``.
  The evolution runner builds this list as the complete integrity
  projection of one detection (construct-stage candidate counts, court
  audit failed-checks, quarantine tallies, appeal provider status, and the
  single gold-informed FN+omission signal).

Classification rules (priority order)
-------------------------------------
1. Provider artifact missing/malformed -> L1, owning module resolved via
   ``ARTIFACT_OWNER`` (``module_hint`` wins when it is a registered
   module id). Reason: ``provider_artifact_missing`` /
   ``provider_artifact_malformed``.
2. Consensus gate blocked nonempty -> L2, ``consensus_gate_policy``.
3. Either side claims coverage=complete with zero valid certificates
   on that side -> L2, ``coverage_validity_policy``.
4. Boundary path verified_clean but terminal unresolved -> L2,
   ``boundary_consumption_policy``.
5. Fall-through: unresolved terminal -> ``no_actionable``; terminal
   verdict present but internally inconsistent ->
   ``detector_layer_out_of_scope``; otherwise ``no_actionable``.

Why this order: a missing or malformed provider artifact is the most
upstream failure — the adjudication layer cannot even observe facts
correctly if its inputs are broken, so artifact integrity must be
checked before any adjudication-layer pattern (rules 2-4), which all
presume well-formed provider output.
"""

from __future__ import annotations

from typing import Any

from .genome import MODULE_IDS
from .v6_contracts import (
    L2_ARTIFACT_APPEAL_MECHANISM,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_PREDICATES,
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
    FailureAttribution,
    build_adjudication_facts,
)

# ============================================================
# L1 ownership map: provider artifact kind -> owning rule module
# ============================================================

ARTIFACT_OWNER: dict[str, str] = {
    "candidate": "candidate_trigger",
    "error_certificate": "evidence_obligation",
    "defense_certificate": "clean_defense_boundary",
}

PROVIDER_ARTIFACT_KINDS = frozenset(ARTIFACT_OWNER)

# Reason codes (closed vocabulary, local to the classifier).
REASON_PROVIDER_MISSING = "provider_artifact_missing"
REASON_PROVIDER_MALFORMED = "provider_artifact_malformed"
REASON_CONSENSUS_BLOCKED = "consensus_gate_blocked"
REASON_HOLLOW_DEFENSE = "hollow_complete_defense"
REASON_HOLLOW_PROSECUTION = "hollow_complete_prosecution"
REASON_BOUNDARY_CONFLICT = "boundary_terminal_conflict"
REASON_INTERNAL_INCONSISTENCY = "internal_inconsistency"
REASON_NO_FAILURE_SHAPE = "no_failure_shape"
# v6.1 扩展：新失败形状
REASON_APPEAL_PENDING = "appeal_pending_blocked"
REASON_APPEAL_FAILED = "appeal_failed_with_errors"
REASON_LOCALIZATION_FALLBACK = "localization_temporal_fallback"
REASON_RESPONSIBLE_STEP_MISSING = "responsible_step_missing"
REASON_CASCADING_FAILURE = "cascading_failure"
REASON_QUARANTINE_PATTERN = "quarantine_pattern_dominant"


# ============================================================
# Helpers (pure, defensive record reading)
# ============================================================

def _find_first_bad_artifact(
    artifacts: Any,
) -> tuple[dict[str, Any], str] | None:
    """Return (artifact, reason_code) for the first missing/malformed
    provider artifact, else None. Malformed is reported before missing
    fields on the same artifact (a malformed artifact's field list is
    itself unreliable)."""
    if not isinstance(artifacts, list):
        return None
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in PROVIDER_ARTIFACT_KINDS:
            continue
        if bool(item.get("malformed")):
            return item, REASON_PROVIDER_MALFORMED
        missing_fields = item.get("missing_fields")
        if missing_fields not in (None, "") and not isinstance(
            missing_fields, (list, tuple, set)
        ):
            return item, REASON_PROVIDER_MALFORMED
        if list(missing_fields or []):
            return item, REASON_PROVIDER_MISSING
    return None


def _resolve_l1_module(artifact: dict[str, Any], reason: str) -> str:
    """Deterministic artifact-kind -> module mapping; a registered
    ``module_hint`` takes precedence over the default owner.

    A certificate that was emitted but failed field validation is a
    certificate-quality gap owned by the verifier instruction; a certificate
    that should exist but was never emitted is an evidence-obligation gap.
    """
    hint = str(artifact.get("module_hint") or "")
    if hint in MODULE_IDS:
        return hint
    if (
        str(artifact.get("kind") or "") == "error_certificate"
        and reason == REASON_PROVIDER_MALFORMED
    ):
        return "atomic_verifier_instruction"
    return ARTIFACT_OWNER[str(artifact["kind"])]


def _attr(layer: str, artifact: str, reason: str, evidence: dict[str, Any]) -> FailureAttribution:
    return FailureAttribution(
        layer=layer, artifact=artifact, reason_code=reason, evidence=evidence
    )


# ============================================================
# Classifier
# ============================================================

def classify_failure(record: dict[str, Any]) -> FailureAttribution:
    """Classify one detection record into a deterministic attribution.

    Pure function: identical records always yield identical
    attributions. See the module docstring for the input contract and
    the priority rationale.
    """
    record = record if isinstance(record, dict) else {}
    court_result = record.get("adjudication")
    if not isinstance(court_result, dict):
        court_result = {}
    facts = build_adjudication_facts(
        court_result,
        boundary_path_state=str(record.get("boundary_path_state") or ""),
    )

    # Rule 1 — most upstream: provider artifact integrity.
    bad = _find_first_bad_artifact(record.get("provider_artifacts"))
    if bad is not None:
        artifact, reason = bad
        return _attr(
            LAYER_L1,
            _resolve_l1_module(artifact, reason),
            reason,
            {
                "kind": str(artifact.get("kind") or ""),
                "module_hint": str(artifact.get("module_hint") or ""),
                "missing_fields": list(artifact.get("missing_fields") or []),
                "artifact_id": str(artifact.get("artifact_id") or ""),
                "source": str(artifact.get("source") or ""),
            },
        )

    # Rule 2 — consensus gate blocked live candidates. The gate is a
    # protection mechanism: blocking low-confidence candidates only becomes
    # an L2 failure when the court cannot reach a verdict because of it. A
    # gate that filtered candidates yet still produced a verdict is the
    # detector working as designed, not an addressable failure.
    if (
        L2_PREDICATES["consensus_gate_blocked_nonempty"](facts)
        and facts.terminal_state == "unresolved"
    ):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_CONSENSUS_GATE,
            REASON_CONSENSUS_BLOCKED,
            {
                "consensus_gate_blocked_count": facts.consensus_gate_blocked_count,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 3 — hollow "complete" coverage: both sides go through the
    # registered predicates, so the vocabulary stays single-sourced.
    if L2_PREDICATES["defense_coverage_complete_and_zero_certificates"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_COVERAGE_VALIDITY,
            REASON_HOLLOW_DEFENSE,
            {
                "defense_coverage": facts.defense_coverage,
                "valid_defense_certificate_count": facts.valid_defense_certificate_count,
            },
        )
    if L2_PREDICATES["prosecution_coverage_complete_and_zero_certificates"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_COVERAGE_VALIDITY,
            REASON_HOLLOW_PROSECUTION,
            {
                "prosecution_coverage": facts.prosecution_coverage,
                "valid_error_certificate_count": facts.valid_error_certificate_count,
            },
        )

    # Rule 4 — boundary path result not consumed by the terminal state.
    if L2_PREDICATES["boundary_state_conflicts_with_terminal"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_BOUNDARY_CONSUMPTION,
            REASON_BOUNDARY_CONFLICT,
            {
                "boundary_path_state": facts.boundary_path_state,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4.1 — v6.1: appeal pending blocked terminal.
    if L2_PREDICATES["appeal_pending_and_terminal_unresolved"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_APPEAL_MECHANISM,
            REASON_APPEAL_PENDING,
            {
                "appeal_status": facts.appeal_status,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4.2 — v6.1: appeal failed with surviving errors.
    if L2_PREDICATES["appeal_failed_with_surviving_errors"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_APPEAL_MECHANISM,
            REASON_APPEAL_FAILED,
            {
                "appeal_status": facts.appeal_status,
                "surviving_error_count": facts.surviving_error_count,
            },
        )

    # Rule 4.3 — v6.1: localization fallback used.
    if L2_PREDICATES["localization_failed_with_temporal_fallback"](facts):
        return _attr(
            LAYER_L1,
            "earliest_origin_tiebreak",
            REASON_LOCALIZATION_FALLBACK,
            {
                "localization_status": facts.localization_status,
            },
        )

    # Rule 4.4 — v6.1: responsible step missing with error verdict.
    # Only fires when the court explicitly reported a localization attempt
    # but produced no responsible step; a missing field in the record is
    # not a failure shape (the fixture simply never ran localization).
    if (
        L2_PREDICATES["responsible_step_missing_and_verdict_error"](facts)
        and facts.localization_status != ""
    ):
        return _attr(
            LAYER_L1,
            "earliest_origin_tiebreak",
            REASON_RESPONSIBLE_STEP_MISSING,
            {
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4.5 — v6.1: quarantine pattern analysis.
    if L2_PREDICATES["quarantine_schema_mismatch_dominant"](facts):
        return _attr(
            LAYER_L1,
            "atomic_verifier_instruction",
            REASON_QUARANTINE_PATTERN,
            {
                "dominant_reason": "schema_mismatch",
                "count": facts.quarantine_reason_schema_mismatch,
            },
        )
    if L2_PREDICATES["quarantine_evidence_missing_dominant"](facts):
        return _attr(
            LAYER_L1,
            "evidence_obligation",
            REASON_QUARANTINE_PATTERN,
            {
                "dominant_reason": "evidence_missing",
                "count": facts.quarantine_reason_evidence_missing,
            },
        )

    # Rule 5 — fall-through.
    if facts.terminal_state == "unresolved":
        return _attr(LAYER_NONE, "", REASON_NO_FAILURE_SHAPE, {
            "terminal_state": facts.terminal_state,
        })
    # Terminal verdict present but internally inconsistent: the court
    # verdict contradicts its own certificate bookkeeping. Both checks
    # reuse registered predicates.
    if facts.terminal_state in ("verified_clean", "verified_error"):
        if L2_PREDICATES["surviving_errors_nonempty_and_verdict_clean"](facts):
            return _attr(LAYER_DETECTOR, "", REASON_INTERNAL_INCONSISTENCY, {
                "terminal_state": facts.terminal_state,
                "surviving_error_count": facts.surviving_error_count,
            })
        if (
            L2_PREDICATES["all_candidates_quarantined"](facts)
            and facts.total_certificate_count == 0
        ):
            return _attr(LAYER_DETECTOR, "", REASON_INTERNAL_INCONSISTENCY, {
                "terminal_state": facts.terminal_state,
                "quarantined_error_count": facts.quarantined_error_count,
                "total_certificate_count": facts.total_certificate_count,
            })
    return _attr(LAYER_NONE, "", REASON_NO_FAILURE_SHAPE, {
        "terminal_state": facts.terminal_state,
    })
