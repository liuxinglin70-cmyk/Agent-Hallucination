"""PACA-TCL v3.5 proof-court contract with focused prosecution support.

Compared to v3.4, this contract adds three structural interventions:

H1 - Candidate-Focused Prosecution Support:
  - Per-candidate prosecution validation (each focused call returns one certificate)
  - Quarantine feedback metadata for iterative refinement
  - Causal chain completeness scoring

H2 - Calibrated Appeal with Consensus Gate:
  - Consensus gate: appeal-sourced positive requires independent initial support
  - Informed defender integration (defender sees prosecution certificates)
  - Tightened trigger priority enforcement (only 0-1 pass through)

H3 - Two-Phase Localization:
  - Phase 1: binary incident confirmation (unchanged from v3.4)
  - Phase 2: pairwise counterfactual comparison for earliest-origin selection
  - Replaces simple min(event_order) with causal sufficiency ranking

The fundamental architectural change: prosecution information flow topology
shifts from broadcast-all to focused-per-candidate, and earliest-origin
selection shifts from temporal minimum to counterfactual sufficiency.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Sequence

from . import contract_v1 as v1
from .proof_contract_v3 import _address_space, validate_error_certificate
from .proof_contract_v3_4 import (
    BREACH_TYPES,
    CLAIM_KINDS,
    COVERAGE_STATES,
    DEFENSE_FIELDS,
    DEFENSE_TYPES,
    DOMINATING_DEFENSES,
    ERROR_FIELDS,
    IMPACT_KINDS,
    NORM_SOURCES,
    RECOVERY_STATES,
    BENCHMARK_CAUSAL_STATES,
    check_commitment,
    check_materiality,
    route_evidence_authority,
    validate_defense_certificate,
    _defense_dominates,
    _validate_top,
)


# ============================================================
# H1: Focused Prosecution Support
# ============================================================

# Causal chain scaffolding stages that a complete prosecution must address
CAUSAL_CHAIN_STAGES = [
    "norm_identification",       # What norm/obligation was violated?
    "origin_event",              # What specific agent event is the origin?
    "agent_control",             # How did the agent control/adopt/generate this?
    "propagation_path",          # How does the error propagate?
    "impact_on_result",          # What is the impact on the produced result?
    "counterfactual_test",       # Would correcting this alone fix the result?
]


def validate_focused_prosecution(
    row: dict[str, Any], packet: dict[str, Any], candidate_origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a single-candidate focused prosecution certificate.

    Same structural validation as v3.4 validate_error_certificate, plus
    causal chain completeness scoring for the scaffolding feedback loop.
    """
    base_audit = validate_error_certificate(row, packet, candidate_origins)

    # Score causal chain completeness (for quarantine feedback).  The focused
    # prosecution prompt mandates the scaffold markers NORM:/ORIGIN:/CONTROL:/
    # PROPAGATION:/IMPACT:/COUNTERFACTUAL: in the rationale; marker presence is
    # the primary signal.  When a provider ignores the scaffold entirely,
    # fall back to the keyword signals so the score stays non-degenerate.
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
    """Build structured feedback for a quarantined certificate to guide retry.

    This is the core of the iterative refinement loop: instead of just
    discarding a quarantined certificate, we tell the prosecution WHAT
    failed and WHY, so it can construct a better certificate.
    """
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
# H2: Consensus Gate
# ============================================================

def apply_consensus_gate(
    *, surviving_errors: list[dict[str, Any]], initial_prosecution: dict[str, Any] | None,
    appeal_source_marker: str = "appeal_",
    initial_supported_candidate_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the consensus gate to filter appeal-sourced positives.

    Rule: An error certificate whose certificate_id starts with the appeal
    source marker can only survive if at least one INITIAL prosecution
    certificate independently supports the same source_candidate_id with
    benchmark_causal_status in {causes_incorrect_result, propagates_to_incorrect_result}.

    This prevents the appeal layer from creating positives that the initial
    prosecution did not independently find -- the primary cause of FP in v3.4.

    Returns:
        (consensus_passed, consensus_blocked) -- errors that pass vs blocked by gate
    """
    if initial_prosecution is None:
        # Without independent initial support, appeal-created positives cannot
        # satisfy consensus.
        return (
            [row for row in surviving_errors if not str(row.get("certificate_id") or "").startswith(appeal_source_marker)],
            [row for row in surviving_errors if str(row.get("certificate_id") or "").startswith(appeal_source_marker)],
        )

    # Build set of candidate_ids that initial prosecution independently supports
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
            # Appeal-sourced: requires consensus
            if source_candidate in initial_supported:
                passed.append(error)
            else:
                blocked.append(error)
        else:
            # Initial-sourced: passes without consensus requirement
            passed.append(error)

    return passed, blocked


# ============================================================
# H3: Two-Phase Localization
# ============================================================

def select_earliest_origin_temporal(
    surviving: list[dict[str, Any]], event_order: dict[str, int],
) -> dict[str, Any] | None:
    """Phase 1 fallback: simple temporal minimum (same as v3.4)."""
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
    Otherwise, fall back to temporal minimum.

    counterfactual_rankings format:
    {
        "event_001": {"causal_sufficiency_score": 0.9, "is_root_cause": True},
        "event_003": {"causal_sufficiency_score": 0.3, "is_root_cause": False},
    }

    Selection algorithm:
    1. If any origin has is_root_cause=True, select the earliest among them
    2. Otherwise, select the origin with highest causal_sufficiency_score
    3. Tie-break by temporal order (earlier wins)
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

    # Check for root cause designations
    root_causes = [
        row for row in surviving
        if normalized_rankings.get(str(row["origin_event_id"]), {}).get("is_root_cause", False)
    ]

    if root_causes:
        # Among root causes, pick earliest
        return min(root_causes, key=lambda row: (
            event_order.get(str(row["origin_event_id"]), 10**9),
            str(row["certificate_id"]),
        ))

    # No explicit root cause; use sufficiency score with temporal tiebreak
    def sort_key(row: dict[str, Any]) -> tuple:
        origin = str(row["origin_event_id"])
        score = normalized_rankings.get(origin, {}).get("causal_sufficiency_score", 0.0)
        # Higher score first (negate for ascending sort), then earlier event
        return (-score, event_order.get(origin, 10**9), str(row["certificate_id"]))

    return min(surviving, key=sort_key)


# ============================================================
# Appeal defense merge
# ============================================================

def _merge_appeal_defense(
    *, appeal_defense: dict[str, Any], packet: dict[str, Any],
    candidate_origins: dict[str, str],
    valid_defenses: list[dict[str, Any]],
    d_audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge appeal defense certificates into the defense pool.

    Why this function exists: the appeal defender produces a second defense
    bundle that must be validated, deduplicated against the base defense,
    and merged with dominance rules before the court can proceed. The merge
    logic is self-contained and has no side effects beyond the two lists it
    returns.
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
    return valid_defenses, d_audits


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

    # P9: pre-ledger consensus-restore policy.  An audited policy may return
    # a blocked certificate to the surviving set BEFORE the occurrence
    # snapshot, so the occurrence ledger and earliest-origin localization
    # flow from the final surviving set (no post-ledger repair inconsistency).
    pre_ledger_restore_evaluation: dict[str, Any] | None = None
    if pre_ledger_policy_items and consensus_blocked:
        from .policy_evaluator import (
            PreLedgerPolicyFacts,
            evaluate_pre_ledger_policies,
        )

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

    Why this function exists: after all evidence has been validated,
    defenses have been merged, and the consensus gate has run, the court
    must compute the terminal state (verified_error/verified_clean/
    unresolved), the occurrence ledger, and the full result schema.
    Extracting this phase keeps the evidence-processing pipeline readable
    and makes the terminal contract testable in isolation.
    """
    # Truth adjudication precedes ledger append: only causal certificates that
    # survive schema validation, mechanism-matched defenses, deterministic
    # materiality/commitment checks, and (when enabled) consensus establish an
    # occurrence.  Recovery is downstream disposition and cannot erase it.
    occurrence_errors = list(surviving)

    invalid_count = sum(audit["decision"] != "pass" for audit in p_audits + d_audits)
    clean_covered = {
        candidate_id for candidate_id, rows in defenses_by_candidate.items()
        if any(row["defense_type"] in DOMINATING_DEFENSES for row in rows)
    }
    auxiliary_candidates = {str(row["source_candidate_id"]) for row in auxiliary_process}
    positive_candidates = {str(row["source_candidate_id"]) for row in surviving}

    coverage_complete = p_coverage == d_coverage == "complete" and not invalid_count
    candidate_terminal = set(required) <= clean_covered | auxiliary_candidates | positive_candidates
    if surviving:
        # v3.5 H3: TWO-PHASE LOCALIZATION
        winner = select_earliest_origin_counterfactual(
            surviving, event_order, counterfactual_rankings,
        )
        state, responsible = "verified_error", str(winner["origin_event_id"])
    else:
        if coverage_complete and candidate_terminal:
            state, responsible = "verified_clean", None
        elif coverage_complete and not required and not valid_errors:
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

    occurrence_winner = select_earliest_origin_counterfactual(
        occurrence_errors, event_order, counterfactual_rankings,
    )
    occurrence_state = (
        "verified_error" if occurrence_winner is not None
        else "verified_clean" if coverage_complete
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
        "wide_construct_state": "verified_process_incident" if auxiliary_process else (
            "verified_hallucination" if surviving else "no_verified_incident"
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
        "error_audits": p_audits,
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
        # v3.5 diagnostics
        "consensus_gate_blocked_ids": [
            str(row["certificate_id"]) for row in consensus_blocked
        ],
        "consensus_gate_applied": apply_consensus_gate_flag and initial_prosecution is not None,
        # P9 pre-ledger restore audit (None when no pre-ledger policy ran).
        "pre_ledger_restore": pre_ledger_restore_evaluation,
        "counterfactual_rankings_used": counterfactual_rankings is not None,
        "focused_prosecution_chain_completeness": (
            [a.get("causal_chain_completeness", 0.0) for a in focused_prosecution_audits]
            if focused_prosecution_audits else None
        ),
    }

    # L2 policy hook: policies are structural coverage-validity
    # interventions, so the evaluator's simplified terminal recomputation is
    # ignored in favor of this court's own full rule (candidate_terminal /
    # required / valid_errors are only addressable here). The boundary path
    # state is an engine-level fact injected by the caller. Policies act on
    # the terminal edge only: the occurrence ledger records what the
    # evidence established before policy, so it is intentionally not
    # recomputed here.
    if policy_items:
        from ..bccse.v6_contracts import build_adjudication_facts
        from .policy_evaluator import evaluate_policies

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
        result["policy_evaluation"] = evaluation.to_dict()

    return result


# ============================================================
# v3.5: Main adjudication with all three interventions
# ============================================================

def adjudicate_proof_court(
    *, required_candidates: list[dict[str, Any]], prosecution: dict[str, Any],
    defense: dict[str, Any], packet: dict[str, Any],
    # v3.4 options (preserved)
    apply_materiality_check: bool = True,
    apply_commitment_check: bool = True,
    appeal_defense: dict[str, Any] | None = None,
    # v3.5 new options
    initial_prosecution: dict[str, Any] | None = None,
    apply_consensus_gate_flag: bool = True,
    counterfactual_rankings: dict[str, dict[str, Any]] | None = None,
    focused_prosecution_audits: list[dict[str, Any]] | None = None,
    # L2 adjudication policy hook. The hook dependencies are resolved
    # lazily inside the function body because importing
    # ``..bccse.v6_contracts`` at module level would initialize the bccse
    # package (and its evolution surface) for every detector-path import,
    # breaking the stable-detector package isolation contract.
    policy_items: Sequence["PolicyItem"] | None = None,
    boundary_path_state: str = "",
    # Fail-closed default: a caller that does not prove boundary authority
    # (the deterministic boundary path passed its contract validation) can
    # never trigger a boundary-consuming terminal move.
    boundary_authority_valid: bool = False,
    # The vocabulary registry the policy items resolve against. Defaults to
    # the baseline registry; a genome carrying an evolved registry passes it
    # here so pending/disabled entries are never executed.
    policy_registry=None,
    # P9: pre-ledger consensus-restore policies.  They act after the
    # consensus gate and before the occurrence snapshot, restoring audited
    # blocked certificates into the surviving set so the occurrence ledger
    # and earliest-origin localization flow from the final surviving set.
    pre_ledger_policy_items: Sequence["PolicyItem"] | None = None,
) -> dict[str, Any]:
    """v3.5 proof court adjudication with consensus gate and counterfactual localization.

    New in v3.5:
    - initial_prosecution: the pre-appeal prosecution (for consensus gate)
    - apply_consensus_gate_flag: enable/disable consensus gate (H2)
    - counterfactual_rankings: pairwise comparison results (H3)
    - focused_prosecution_audits: per-candidate audit results with chain scores (H1)

    policy_items optionally applies deterministic L2 coverage-validity
    policies after adjudication; boundary_path_state supplies the engine-level
    boundary fact that the court itself cannot address. Both default to inert.
    ``boundary_authority_valid`` certifies that the boundary path came from
    the deterministic engine path with a passing contract validation; it
    defaults to False so boundary consumption stays fail-closed for any
    caller that cannot establish it.
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

    # v3.4: merge appeal defender into defense pool (preserved)
    if appeal_defense is not None:
        valid_defenses, d_audits = _merge_appeal_defense(
            appeal_defense=appeal_defense,
            packet=packet,
            candidate_origins=candidate_origins,
            valid_defenses=valid_defenses,
            d_audits=d_audits,
        )

    defenses_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in valid_defenses:
        defenses_by_candidate.setdefault(str(row["target_candidate_id"]), []).append(row)

    # v3.4: materiality pre-check (preserved)
    materiality_results: dict[str, dict[str, Any]] = {}
    if apply_materiality_check:
        for row in valid_errors:
            if str(row.get("breach_type") or "") == "contradiction":
                result = check_materiality(row, packet)
                materiality_results[str(row["certificate_id"])] = result
                if result["decision"] == "immaterial_difference" and result.get("defense_type"):
                    target_id = str(row["source_candidate_id"])
                    synthetic_defense = {
                        "certificate_id": f"materiality_defense_{row['certificate_id']}",
                        "target_candidate_id": target_id,
                        "target_origin_event_id": str(row.get("origin_event_id") or ""),
                        "defense_type": result["defense_type"],
                        "evidence_refs": [str(row.get("origin_event_id") or "")],
                        "rationale": result["reason"],
                    }
                    defenses_by_candidate.setdefault(target_id, []).append(synthetic_defense)

    # v3.4: commitment pre-check (preserved)
    commitment_results: dict[str, dict[str, Any]] = {}
    if apply_commitment_check:
        for row in valid_errors:
            if str(row.get("breach_type") or "") == "invalid_action":
                result = check_commitment(row, packet)
                commitment_results[str(row["certificate_id"])] = result
                if result["decision"] == "rejected_attempt" and result.get("defense_type"):
                    target_id = str(row["source_candidate_id"])
                    synthetic_defense = {
                        "certificate_id": f"commitment_defense_{row['certificate_id']}",
                        "target_candidate_id": target_id,
                        "target_origin_event_id": str(row.get("origin_event_id") or ""),
                        "defense_type": result["defense_type"],
                        "evidence_refs": [str(row.get("origin_event_id") or "")],
                        "rationale": result["reason"],
                    }
                    defenses_by_candidate.setdefault(target_id, []).append(synthetic_defense)

    # Defense dominance (preserved from v3.4)
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

    # v3.5 H2: CONSENSUS GATE. Only admissible initial certificates count as
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
