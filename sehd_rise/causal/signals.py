"""Detection output -> evolution side: the single projection point.

Plan §2.11 names a single authority for Detection Truth: court output is the
terminal basis, and this module is its only legal projection into the
evolution side. The old package split that projection in two: a five-
dimensional CRS projection (``signal_adapter``) and a label-free evolution
projection (``paca_tcl.evolution_contract``). Downstream attribution then
re-read both shapes from the raw row, producing the §1.11 first duplication
("Detector Result -> signal_adapter -> AttributionSignal -> FailureAttribution
-> FailureEpisode ..."). The unified ``DetectionSignal`` collapses the two
projections into one record carrying:

- terminal state + decision authority (single resolution path),
- the CRS vector whenever a native or certificate projection is available,
- coverage readings for both proof sides,
- the label-free diagnostics block (appeal status, localization, focused
  quarantine, authority failure reasons, consensus gate),
- the provider-artifact integrity frontier the failure classifier consumes,
- the self-supervised defect reading (unresolved / CRS-contradiction /
  evidence-incomplete / signal-unavailable / runtime-invalid),
- the self-supervised process diagnostic (appeal / focused-quarantine /
  localization-ambiguous) which is a deterministic fact of the row,
- the optional gold facts when ``use_gold`` is explicitly requested.

Label-truth enters via opt-in: the default evolution path stays
label-free. No LLM ever reads a prompt-assembled value from this module;
cut/episode derive their semantic record from the projected fields.

Layering: causal imports platform, skills, detection only. This module
imports the gold coercion from platform, audit translations from skills, and
the court audit projection helper from detection; it never reaches back into
loop/planning/execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from sehd_rise.platform.gold import coerce_gold_label
from sehd_rise.skills.vocabulary.contracts import (
    DEFENSE_CHECK_FIELDS,
    DEFENSE_RELATIONAL_INVARIANTS,
    ERROR_CHECK_FIELDS,
    ERROR_RELATIONAL_INVARIANTS,
)


# ============================================================
# Frozen CRS configuration (protocol-fix weight vector)
# ============================================================

CRS_DIMENSIONS = (
    "factuality",
    "counterfactual",
    "recovery",
    "process",
    "scope",
)

# Frozen by the BCCSE protocol; recovery is a positive recovery score, so its
# risk contribution is (1 - recovery) inside the aggregate.
CRS_WEIGHTS = {
    "factuality": 0.35,
    "counterfactual": 0.15,
    "recovery": 0.20,
    "process": 0.20,
    "scope": 0.10,
}

VALID_DETECTION_STATES = frozenset({
    "verified_error", "verified_clean", "unresolved",
})

# Earlier vocabulary occasionally carried verified_hallucination /
# verified_incident / verified_non_hallucination; map them through the same
# normalizer so resume/replay of historical payloads cannot crash here.
_LEGACY_INCIDENT_STATE_MAP = {
    "verified_error": "verified_error",
    "verified_hallucination": "verified_error",
    "verified_incident": "verified_error",
    "verified_clean": "verified_clean",
    "verified_non_hallucination": "verified_clean",
    "no_verified_incident": "verified_clean",
    "unresolved": "unresolved",
}


# ============================================================
# Coverage readings
# ============================================================

@dataclass(frozen=True)
class CoverageReading:
    score: float | None
    state: str | None
    quantitative: bool


COVERAGE_STATE_SCORE = {
    "complete": 1.0,
    # Lowered from 0.5 to align with COVERAGE_LOW_THRESHOLD: an incomplete
    # categorical state must never read as a moderate score.
    "incomplete": 0.3,
    "unresolved": 0.0,
}


def _unit_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= number <= 1.0:
        return None
    return number


def parse_coverage(value: Any) -> CoverageReading:
    """Parse categorical v3.5 coverage or a pre-v3.5 numeric coverage ratio."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in COVERAGE_STATE_SCORE:
            return CoverageReading(
                score=COVERAGE_STATE_SCORE[normalized],
                state=normalized,
                quantitative=False,
            )
        value = normalized
    score = _unit_float(value)
    if score is None:
        return CoverageReading(score=None, state=None, quantitative=False)
    return CoverageReading(score=score, state=None, quantitative=True)


def extract_coverage(
    row: Mapping[str, Any],
) -> tuple[CoverageReading, CoverageReading]:
    """Read coverage without turning missing or malformed values into zero."""
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        unavailable = CoverageReading(None, None, False)
        return unavailable, unavailable
    return (
        parse_coverage(proof.get("prosecution_coverage")),
        parse_coverage(proof.get("defense_coverage")),
    )


# ============================================================
# CRS signal (native vector or provenance-tagged projection)
# ============================================================

@dataclass(frozen=True)
class CRSSignal:
    scores: dict[str, float]
    aggregate: float
    source: str
    certificate_id: str | None
    complete: bool
    # Projection confidence: 1.0 for native CRS; reduced for certificate
    # projections when enum-mapped dimensions hit unknown values (the 0.5
    # fallback).
    projection_confidence: float = 1.0


def compute_crs(scores: Mapping[str, float]) -> float:
    """Compute CRS with the frozen 0.35/0.15/0.20/0.20/0.10 weights."""
    value = (
        CRS_WEIGHTS["factuality"] * float(scores["factuality"])
        + CRS_WEIGHTS["counterfactual"] * float(scores["counterfactual"])
        + CRS_WEIGHTS["recovery"] * (1.0 - float(scores["recovery"]))
        + CRS_WEIGHTS["process"] * float(scores["process"])
        + CRS_WEIGHTS["scope"] * float(scores["scope"])
    )
    return round(max(0.0, min(1.0, value)), 6)


_FACTUALITY_BY_BREACH = {
    "contradiction": 1.0,
    "fabrication": 1.0,
    "unsupported_commitment": 0.9,
    "instruction_violation": 0.85,
    "invalid_action": 0.8,
    "consequential_omission": 0.8,
    "redundant_repetition": 0.55,
}

_COUNTERFACTUAL_BY_STATUS = {
    "causes_incorrect_result": 1.0,
    "propagates_to_incorrect_result": 0.8,
    "unresolved": 0.5,
    "process_only": 0.2,
}

_RECOVERY_BY_STATE = {
    "none": 0.0,
    "post_impact_residual": 0.25,
    "unresolved": 0.5,
    "pre_impact_repaired": 1.0,
}

_SCOPE_BY_IMPACT = {
    "final_answer": 1.0,
    "persistent_external_state": 1.0,
    "persistent_workspace_state": 0.9,
    "committed_tool_action": 0.85,
    "unrecovered_reasoning_dependency": 0.75,
    "process_debt": 0.2,
}


def _normalize_native(candidate: Any) -> dict[str, float]:
    if not isinstance(candidate, Mapping):
        return {}
    scores: dict[str, float] = {}
    for dimension in CRS_DIMENSIONS:
        raw = candidate.get(dimension)
        if isinstance(raw, Mapping):
            raw = raw.get("score")
        if raw is None:
            raw = candidate.get(f"{dimension}_score")
        value = _unit_float(raw)
        if value is None:
            return {}
        scores[dimension] = value
    return scores


def _native_crs(row: Mapping[str, Any]) -> CRSSignal | None:
    proof = row.get("proof_adjudication") or {}
    adjudication = row.get("adjudication") or {}
    candidates = (
        proof.get("crs_scores") if isinstance(proof, Mapping) else None,
        proof.get("crs") if isinstance(proof, Mapping) else None,
        row.get("crs_scores"),
        row.get("crs"),
        row.get("evidential_scores_parsed"),
        adjudication.get("crs_scores") if isinstance(adjudication, Mapping) else None,
        adjudication.get("crs") if isinstance(adjudication, Mapping) else None,
    )
    for candidate in candidates:
        scores = _normalize_native(candidate)
        if scores:
            return CRSSignal(
                scores=scores,
                aggregate=compute_crs(scores),
                source="native",
                certificate_id=None,
                complete=True,
            )

    breakdown = row.get("crs_breakdown")
    if not breakdown and isinstance(adjudication, Mapping):
        breakdown = adjudication.get("crs_breakdown")
    if isinstance(breakdown, list):
        normalized: list[tuple[float, dict[str, float], str | None]] = []
        for item in breakdown:
            if not isinstance(item, Mapping):
                continue
            for candidate in (item.get("adjusted_scores"), item.get("raw_scores"), item):
                scores = _normalize_native(candidate)
                if scores:
                    normalized.append(
                        (
                            compute_crs(scores),
                            scores,
                            str(item.get("candidate_event_id") or "") or None,
                        )
                    )
                    break
        if normalized:
            aggregate, scores, certificate_id = max(normalized, key=lambda item: item[0])
            return CRSSignal(
                scores=scores,
                aggregate=aggregate,
                source="native_breakdown",
                certificate_id=certificate_id,
                complete=True,
            )
    return None


def _certificate_projection(
    certificate: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Project one validated proof certificate into CRS-compatible dimensions.

    Returns ``(scores, projection_confidence)``; confidence is the fraction of
    enum-mapped dimensions whose source field matched a known value (unknown
    values fall back to 0.5 and reduce confidence).
    """
    process_checks = (
        bool(certificate.get("norm_evidence_refs")),
        bool(certificate.get("violation_evidence_refs")),
        bool(certificate.get("agent_control_evidence_refs")),
        bool(certificate.get("impact_evidence_refs")),
    )
    process_score = sum(process_checks) / len(process_checks)
    known_hits = (
        str(certificate.get("breach_type") or "") in _FACTUALITY_BY_BREACH,
        str(certificate.get("benchmark_causal_status") or "") in _COUNTERFACTUAL_BY_STATUS,
        str(certificate.get("recovery_state") or "") in _RECOVERY_BY_STATE,
        str(certificate.get("impact_kind") or "") in _SCOPE_BY_IMPACT,
    )
    confidence = sum(known_hits) / len(known_hits)
    return {
        "factuality": _FACTUALITY_BY_BREACH.get(
            str(certificate.get("breach_type") or ""), 0.5
        ),
        "counterfactual": _COUNTERFACTUAL_BY_STATUS.get(
            str(certificate.get("benchmark_causal_status") or ""), 0.5
        ),
        "recovery": _RECOVERY_BY_STATE.get(
            str(certificate.get("recovery_state") or ""), 0.5
        ),
        "process": process_score,
        "scope": _SCOPE_BY_IMPACT.get(
            str(certificate.get("impact_kind") or ""), 0.5
        ),
    }, confidence


def _prosecution_certificates(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    prosecution = row.get("combined_prosecution") or row.get("prosecution") or {}
    if not isinstance(prosecution, Mapping):
        return []
    candidates = prosecution.get("candidates") or []
    return [item for item in candidates if isinstance(item, Mapping)]


def _proof_projection(row: Mapping[str, Any]) -> CRSSignal:
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        proof = {}
    state = resolve_evolution_state(row)
    certificates = _prosecution_certificates(row)
    quarantined = {
        str(value)
        for value in proof.get("quarantined_error_certificate_ids") or []
    }
    certificates = [
        item
        for item in certificates
        if str(item.get("certificate_id") or "") not in quarantined
    ]

    if state == "verified_error":
        eligible_ids = {
            str(value)
            for value in proof.get("surviving_error_certificate_ids") or []
        }
        if eligible_ids:
            certificates = [
                item
                for item in certificates
                if str(item.get("certificate_id") or "") in eligible_ids
            ]
    elif state == "verified_clean":
        # A complete clean proof is positive evidence of no causal error.
        prosecution, defense = extract_coverage(row)
        if prosecution.state == "complete" and defense.state == "complete":
            scores = {
                "factuality": 0.0,
                "counterfactual": 0.0,
                "recovery": 1.0,
                "process": 0.0,
                "scope": 0.0,
            }
            return CRSSignal(
                scores=scores,
                aggregate=compute_crs(scores),
                source="proof_projection_clean",
                certificate_id=None,
                complete=True,
                projection_confidence=1.0,
            )

    projected: list[tuple[float, dict[str, float], str | None, float]] = []
    for certificate in certificates:
        scores, confidence = _certificate_projection(certificate)
        projected.append(
            (
                compute_crs(scores),
                scores,
                str(certificate.get("certificate_id") or "") or None,
                confidence,
            )
        )
    if projected:
        aggregate, scores, certificate_id, confidence = max(
            projected, key=lambda item: item[0]
        )
        return CRSSignal(
            scores=scores,
            aggregate=aggregate,
            source="proof_projection_certificate",
            certificate_id=certificate_id,
            complete=True,
            projection_confidence=confidence,
        )

    # Unresolved proof with no usable certificate: 0.5 explicitly denotes
    # epistemic uncertainty. This is not a silent all-zero fallback.
    scores = {dimension: 0.5 for dimension in CRS_DIMENSIONS}
    return CRSSignal(
        scores=scores,
        aggregate=compute_crs(scores),
        source="proof_projection_uncertain",
        certificate_id=None,
        complete=False,
        projection_confidence=0.0,
    )


def extract_crs_signal(row: Mapping[str, Any]) -> CRSSignal:
    """Return native CRS when available, else a provenance-tagged projection."""
    return _native_crs(row) or _proof_projection(row)


# ============================================================
# Authority resolution
# ============================================================

def decision_authority_invalid(row: Mapping[str, Any]) -> bool:
    """Whether a detector result is barred from authoritative decisions.

    A present authority that is not ``authoritative`` fails closed. An ABSENT
    authority is not classified here: the runner boundary treats a missing
    authority as a hard error, and ``resolve_authoritative_state`` needs to
    distinguish "no authority recorded" from "authority rejected".
    """
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    top = str(row.get("decision_authority") or "").strip().lower()
    inner = str(proof.get("decision_authority") or "").strip().lower()
    return (
        row.get("runtime_valid") is False
        or bool(top and top != "authoritative")
        or bool(inner and inner != "authoritative")
    )


def missing_decision_authority(row: Mapping[str, Any]) -> bool:
    """Whether the row carries no top-level decision authority at all."""
    return not str(row.get("decision_authority") or "").strip()


def has_explicit_authority(row: Mapping[str, Any]) -> bool:
    """Whether the row carries the authoritative envelope required for scoring."""
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    top = str(row.get("decision_authority") or "").strip().lower()
    inner = str(proof.get("decision_authority") or "").strip().lower()
    return top == "authoritative" and (not inner or inner == "authoritative")


def resolve_authoritative_state(row: Mapping[str, Any]) -> str:
    """Resolve the decision state without rewriting factual occurrence data.

    Invalid decision authority exposes ``unresolved`` to attribution and
    promotion consumers even if the occurrence ledger records an error. The
    original row/ledger stays untouched for incident audit.
    """
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    if decision_authority_invalid(row):
        return "unresolved"
    top_ledger = row.get("occurrence_ledger")
    top_ledger = top_ledger if isinstance(top_ledger, Mapping) else {}
    proof_ledger = proof.get("occurrence_ledger")
    proof_ledger = proof_ledger if isinstance(proof_ledger, Mapping) else {}
    occurrence_candidates = (
        ("occurrence_state", row.get("occurrence_state")),
        ("proof_adjudication.occurrence_state", proof.get("occurrence_state")),
        ("occurrence_ledger.occurrence_state", top_ledger.get("occurrence_state")),
        ("occurrence_ledger.state", top_ledger.get("state")),
        (
            "proof_adjudication.occurrence_ledger.occurrence_state",
            proof_ledger.get("occurrence_state"),
        ),
        (
            "proof_adjudication.occurrence_ledger.state",
            proof_ledger.get("state"),
        ),
    )
    for source, value in occurrence_candidates:
        if value in (None, ""):
            continue
        normalized = str(value)
        if normalized not in VALID_DETECTION_STATES:
            raise ValueError(f"Invalid {source}: {value!r}")
        return normalized

    incident = row.get("incident_state")
    if isinstance(incident, Mapping):
        occurrence = incident.get("occurrence_state")
        if occurrence not in (None, ""):
            normalized = str(occurrence)
            if normalized not in VALID_DETECTION_STATES:
                raise ValueError(
                    f"Invalid incident_state.occurrence_state: {occurrence!r}"
                )
            return normalized
        incident = incident.get("state") or incident.get("verdict")
    if incident not in (None, ""):
        normalized = _LEGACY_INCIDENT_STATE_MAP.get(str(incident))
        if normalized is None:
            raise ValueError(f"Invalid incident_state: {incident!r}")
        return normalized

    legacy_state = str(row.get("state") or "")
    if legacy_state not in VALID_DETECTION_STATES:
        # Earlier payloads may carry pre-v3.5 vocabulary; normalize them through
        # the same map as incident_state instead of crashing a resume/replay.
        normalized_legacy = _LEGACY_INCIDENT_STATE_MAP.get(legacy_state)
        if normalized_legacy is not None:
            return normalized_legacy
        raise ValueError(f"Invalid detector state: {legacy_state!r}")
    return legacy_state


def resolve_evolution_state(row: Mapping[str, Any]) -> str:
    """Resolve the terminal state consumed by the evolution loop.

    The evolution loop reads the COURT'S terminal verdict, not the occurrence
    ledger. The ledger is append-only factual record that intentionally stays
    unchanged when an L2 policy alters the court's terminal edge, and it can
    disagree with the terminal verdict on a plain detection. Evolution credits
    gold polarity, triggers and fitness against the terminal edge, so a
    policy-aware result AND every plain detection read the terminal ``state``,
    never the occurrence-first projection used by external occurrence metrics.
    """
    if decision_authority_invalid(row):
        return "unresolved"
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    for value in (row.get("state"), proof.get("state")):
        if value in (None, ""):
            continue
        normalized = str(value)
        if normalized not in VALID_DETECTION_STATES:
            raise ValueError(f"Invalid evolution terminal state: {value!r}")
        return normalized
    # Terminal fields absent (historical/partial payloads): fall back to the
    # occurrence-first projection, which is at least a decisive reading.
    return resolve_authoritative_state(row)


def prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Predicted polarity of a detector row; ``None`` for unresolved."""
    state = resolve_evolution_state(row)
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


# ============================================================
# Runtime error categorization (prompt-safe bounded vocabulary)
# ============================================================

_RUNTIME_ERROR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("provider_timeout", ("timeout", "timederror", "deadline")),
    ("provider_rate_limit", ("rate limit", "ratelimit", "429", "too many requests")),
    ("provider_auth", ("unauthorized", "forbidden", "401", "403", "api key")),
    ("provider_transport", ("connection", "socket", "ssl", "dns", "network")),
    ("provider_contract", ("json", "schema", "parse", "decode", "validation")),
    ("detector_authority", ("decision authority", "runtime_valid", "authority")),
    ("detector_contract", ("firewall", "coverage", "certificate", "census")),
)


def classify_runtime_error(error: str) -> str:
    """Reduce a runtime failure to a bounded, prompt-safe category."""
    lowered = str(error or "").lower()
    for category, markers in _RUNTIME_ERROR_CATEGORIES:
        if any(marker in lowered for marker in markers):
            return category
    return "unclassified_runtime_error"


# ============================================================
# Self-supervised defect + process diagnostic vocabulary
# ============================================================

class DetectionDefect(str, Enum):
    """Self-supervised defect the detector itself reports about a row.

    Deterministic function of (state, runtime_valid, crs, coverage): no gold
    label, no LLM inference. Cut.py routes the defect to an owner; gold only
    refines polarity for FP/FN routing.
    """

    NONE = "none"
    UNRESOLVED = "unresolved"
    CRS_CONTRADICTION = "crs_contradiction"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    SIGNAL_UNAVAILABLE = "signal_unavailable"
    RUNTIME_INVALID = "runtime_invalid"
    # Retired in v5.4 (unreachable: v3.5 coverage is categorical, never the
    # quantitative ratio this trigger required). Preserved as a closed-vocab
    # token so historical attributions remain readable.
    BOUNDARY_HESITATION = "boundary_hesitation"


class ProcessDiagnostic(str, Enum):
    """Self-supervised reading of which internal proof stage broke.

    A deterministic fact of the diagnostics block (focused-prosecution
    quarantine, appeal failure, ambiguous localization), independent of any
    gold label. Cut.py uses it to refine the owner module when the
    self-supervised cascade fires without gold.
    """

    NONE = "none"
    FOCUSED_QUARANTINE = "focused_quarantine"
    APPEAL_FAILURE = "appeal_failure"
    LOCALIZATION_AMBIGUOUS = "localization_ambiguous"


# Appeal failure markers raised by the v3.5 proof engine appeal stage. Matched
# as substrings against ``diagnostics.appeal_error`` to decide which side of
# the appellate court actually broke.
_APPEAL_PROSECUTION_MARKERS = (
    "appeal prosecution is incomplete",
    "appeal prosecution contains another candidate",
    "appeal prosecutor input firewall violation",
)
_APPEAL_DEFENSE_MARKERS = (
    "appeal defense is incomplete",
    "appeal defender input firewall violation",
)
_APPEAL_COVERAGE_MARKERS = (
    "appeal defense candidate coverage mismatch",
)
_APPEAL_INCOMPLETE_STATUSES = frozenset({
    "incomplete_initial_error_preserved",
    "incomplete_fail_closed",
    "provider_error",
})


def _detect_crs_contradiction(state: str, crs_signal: CRSSignal) -> bool:
    """Compare the fixed-weight aggregate with the proof-court decision.

    Recovery is inverted in the CRS formula, so raw dimensions cannot all be
    treated as same-polarity "error support".
    """
    if not crs_signal.complete:
        return False
    recovery = crs_signal.scores.get("recovery")
    if state == "verified_error":
        if (
            crs_signal.aggregate < CRS_THRESHOLD_ERROR
            and recovery is not None
            and recovery >= 0.75
        ):
            # Aggregate may be low mainly because of high recovery. Recompute
            # without the recovery dimension before declaring contradiction,
            # so "repaired but did cause impact" does not register as one.
            recovery_free = (
                crs_signal.aggregate
                - CRS_WEIGHTS["recovery"] * (1.0 - recovery)
            ) / (1.0 - CRS_WEIGHTS["recovery"])
            return recovery_free < CRS_THRESHOLD_ERROR
        return crs_signal.aggregate < CRS_THRESHOLD_ERROR
    if state == "verified_clean":
        # High recovery itself supports clean: other dimensions pushing
        # aggregate above the threshold does not contradict the clean verdict.
        if recovery is not None and recovery >= 0.75:
            return False
        return crs_signal.aggregate >= CRS_THRESHOLD_BORDERLINE
    return False


# CRS thresholds. ``CRS_THRESHOLD_BORDERLINE`` is the lower band of acceptable
# clean verdicts; below it the verdict contradicts the dimensional evidence.
# ``CRS_THRESHOLD_ERROR`` is the minimum aggregate an error verdict needs to
# not contradict its own dimensional support.
CRS_THRESHOLD_BORDERLINE = 0.28
CRS_THRESHOLD_ERROR = 0.45

# Coverage below this is considered "abnormally low".
COVERAGE_LOW_THRESHOLD = 0.3
# Adversarial equilibrium: min(p, d) / max(p, d). Below this ratio the
# adjudication is one-sided (insufficient adversarial testing). Only
# meaningful for quantitative coverage readings; see ``_adversarially_tested``
# for the categorical (production v3.5) rule.
ADVERSARIAL_EQUILIBRIUM_THRESHOLD = 0.4


def _compute_adversarial_equilibrium(
    prosecution: CoverageReading, defense: CoverageReading
) -> float:
    """Adversarial equilibrium ratio of prosecution vs defense coverage.

    Returns 0.0 when either side's coverage is unavailable (fail-closed):
    no evidence of balance. The x1.5 boost above 0.8 recognizes that a high
    winning-side score often reflects legitimate evidence dominance rather
    than insufficient challenge.
    """
    p = prosecution.score
    d = defense.score
    if p is None or d is None:
        return 0.0
    if p <= 0 and d <= 0:
        return 0.0
    max_score = max(p, d)
    if max_score > 0.8:
        return min(1.0, min(p, d) / max_score * 1.5)
    return min(p, d) / max_score


def _adversarially_tested(
    prosecution: CoverageReading, defense: CoverageReading
) -> bool:
    """Whether BOTH sides of the proof court were genuinely exercised.

    For categorical coverage (the production v3.5 contract), a side that is
    "incomplete"/"unresolved" never finished its run and must never count as
    adversarially tested. For quantitative coverage, the equilibrium ratio
    must clear the threshold. Unavailable: not tested (fail-closed).
    """
    if prosecution.state is not None or defense.state is not None:
        return prosecution.state == "complete" and defense.state == "complete"
    if prosecution.quantitative and defense.quantitative:
        return (
            _compute_adversarial_equilibrium(prosecution, defense)
            >= ADVERSARIAL_EQUILIBRIUM_THRESHOLD
        )
    return False


def _detect_evidence_incomplete(
    prosecution: CoverageReading, defense: CoverageReading, state: str
) -> bool:
    """Detect one-sided or evidence-starved adjudication.

    A court-style adjudication requires BOTH sides to have adequate coverage.
    When either side is starved the verdict is unreliable regardless of which
    side won. The winner must be complete; the challenger must have had
    adequate opportunity to rebut.
    """
    if state in ("unresolved", "verified_error"):
        winner = prosecution
    else:
        winner = defense
    if winner.state is not None:
        if winner.state != "complete":
            return True
    elif not winner.quantitative or winner.score is None:
        return True
    elif winner.score < COVERAGE_LOW_THRESHOLD:
        return True

    if state == "verified_error":
        challenger = defense
    elif state == "verified_clean":
        challenger = prosecution
    else:
        # Unresolved: both sides matter; inspect the weaker one.
        challenger = defense if (prosecution.score or 0) >= (defense.score or 0) else prosecution

    if challenger.state is not None:
        if challenger.state != "complete":
            return True
    elif not challenger.quantitative or challenger.score is None:
        return True
    elif challenger.score < COVERAGE_LOW_THRESHOLD:
        return True

    return False


def _has_surviving_error_certificate(row: Mapping[str, Any]) -> bool:
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        return False
    return bool(proof.get("surviving_error_certificate_ids") or [])


# ============================================================
# Diagnostics block (label-free process projection)
# ============================================================

def _process_diagnostics(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the detector's label-free process diagnostics block.

    Prefers the canonical ``diagnostics`` projection; when a raw detector
    result is supplied the same fields are derived from ``evidence_health``
    / ``proof_adjudication`` / ``providers`` so attribution behaves
    identically on both shapes.
    """
    diagnostics = row.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        merged = dict(diagnostics)
        merged.setdefault("focused_quarantined_candidate_count", 0)
        merged.setdefault("appeal_status", "not_required")
        merged.setdefault("appeal_error_type", "")
        merged.setdefault("appeal_error", "")
        merged.setdefault("localization_status", "not_applicable")
        return merged
    health = row.get("evidence_health")
    health = health if isinstance(health, Mapping) else {}
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    providers = row.get("providers")
    providers = providers if isinstance(providers, Mapping) else {}
    focused = providers.get("focused_prosecution")
    focused = focused if isinstance(focused, Mapping) else {}
    appeal_error = providers.get("appeal_error")
    appeal_error = appeal_error if isinstance(appeal_error, Mapping) else {}
    authority_reasons = row.get("authority_failure_reasons") or []
    return {
        "focused_quarantined_candidate_count": int(
            health.get("focused_prosecution_quarantined_candidate_count") or sum(
                isinstance(meta, Mapping) and bool(meta.get("all_quarantined"))
                for meta in focused.values()
            )
        ),
        "appeal_status": str(
            proof.get("appeal_status") or health.get("appeal_status") or "not_required"
        ),
        "appeal_error_type": str(appeal_error.get("error_type") or ""),
        "appeal_error": str(appeal_error.get("error") or ""),
        "localization_status": str(
            proof.get("localization_status")
            or health.get("localization_status") or "not_applicable"
        ),
        "consensus_gate_applied": bool(proof.get("consensus_gate_applied")),
        "consensus_gate_blocked_count": len(
            proof.get("consensus_gate_blocked_ids") or []
        ),
        "counterfactual_rankings_used": bool(
            proof.get("counterfactual_rankings_used")
        ),
        "required_candidate_count": int(
            proof.get("required_candidate_count") or 0
        ),
        "resolved_candidate_count": int(
            proof.get("resolved_candidate_count") or 0
        ),
        "authority_failure_reasons": [
            dict(reason) for reason in authority_reasons
            if isinstance(reason, Mapping)
        ],
        "verdict_authority_scope": str(
            proof.get("verdict_authority_scope")
            or str(row.get("decision_authority") or "")
        ),
        "focused_quarantined_candidate_ids": sorted(
            str(candidate_id) for candidate_id, meta in focused.items()
            if isinstance(meta, Mapping) and meta.get("all_quarantined")
        ),
    }


def _classify_appeal_failure(diagnostics: Mapping[str, Any]) -> str:
    """Classify which appellate party failed, if the appeal stage broke."""
    status = str(diagnostics.get("appeal_status") or "not_required")
    error_text = str(diagnostics.get("appeal_error") or "").lower()
    error_type = str(diagnostics.get("appeal_error_type") or "")
    status_incomplete = any(
        status.startswith(prefix) for prefix in _APPEAL_INCOMPLETE_STATUSES
    )
    if not status_incomplete and not error_type:
        return ""
    if any(marker in error_text for marker in _APPEAL_COVERAGE_MARKERS):
        return "appeal_coverage_mismatch"
    if any(marker in error_text for marker in _APPEAL_DEFENSE_MARKERS):
        return "appeal_defender_failure"
    if any(marker in error_text for marker in _APPEAL_PROSECUTION_MARKERS):
        return "appeal_prosecutor_failure"
    # An unrecognized appeal breakage still broke the prosecution-side
    # appellate obligation, which is the stage that runs first.
    return "appeal_prosecutor_failure"


def _classify_process_diagnostic(
    diagnostics: Mapping[str, Any],
) -> ProcessDiagnostic:
    """Identify the process stage that broke, from label-free diagnostics."""
    if _classify_appeal_failure(diagnostics):
        return ProcessDiagnostic.APPEAL_FAILURE

    try:
        quarantined = int(diagnostics.get("focused_quarantined_candidate_count") or 0)
    except (TypeError, ValueError):
        quarantined = 0
    if quarantined > 0:
        return ProcessDiagnostic.FOCUSED_QUARANTINE

    localization = str(diagnostics.get("localization_status") or "not_applicable")
    if localization == "failed_temporal_fallback":
        # Two or more error certificates survived but counterfactual
        # localization could not rank them; responsible step was chosen by
        # temporal fallback rather than causal sufficiency.
        return ProcessDiagnostic.LOCALIZATION_AMBIGUOUS
    return ProcessDiagnostic.NONE


# ============================================================
# Provider-artifact integrity frontier
# ============================================================

# Kinds of provider artifacts the failure cut consumes as evidence. The
# owner module table is owned by skills.vocabulary.contracts (single source
# for the L1 module ids); here we only name the artifact kinds.
ARTIFACT_KIND_CANDIDATE = "candidate"
ARTIFACT_KIND_ERROR_CERTIFICATE = "error_certificate"
ARTIFACT_KIND_DEFENSE_CERTIFICATE = "defense_certificate"
ARTIFACT_KINDS: tuple[str, ...] = (
    ARTIFACT_KIND_CANDIDATE,
    ARTIFACT_KIND_ERROR_CERTIFICATE,
    ARTIFACT_KIND_DEFENSE_CERTIFICATE,
)


def _candidate_rows(
    detection_result: Mapping[str, Any],
    kind: str,
) -> list[Mapping[str, Any]]:
    """Read rows for one artifact kind from a detection result."""
    if kind == ARTIFACT_KIND_CANDIDATE:
        rows = detection_result.get("census") or []
        return [row for row in rows if isinstance(row, Mapping)]
    if kind == ARTIFACT_KIND_ERROR_CERTIFICATE:
        payload = (
            detection_result.get("combined_prosecution")
            or detection_result.get("prosecution")
        )
    else:
        payload = (
            detection_result.get("combined_defense")
            or detection_result.get("defense")
        )
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("candidates") or payload.get("certificates") or []
    return [row for row in rows if isinstance(row, Mapping)]


def project_provider_artifacts(
    *,
    detection_result: Mapping[str, Any],
    signal: "DetectionSignal",
    candidate_kind_hint: str = "",
) -> list[dict[str, Any]]:
    """Project every addressable provider-artifact integrity signal.

    Plan §2.4: an anomalous artifact is EVIDENCE for the causal cut, not the
    owner itself. This projection keeps the closed-vocabulary fields
    (``failed_invariants`` / ``failed_fields`` / ``missing_fields`` /
    ``source``) so the cut can route each kind to a registered L1 module and
    each typed origin to a layer; it never invents owner semantics.

    Only label-free runtime evidence is read. The single gold-informed
    signal is the FN+omission candidate absence which only a gold label can
    expose; it contributes a ``candidate / missing=["candidate"]`` artifact
    that the cut classifies through the normal missing-artifact rule.
    """
    artifacts: list[dict[str, Any]] = []
    adjudication = detection_result.get("proof_adjudication") or {}
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    health = detection_result.get("evidence_health") or {}
    if not isinstance(health, Mapping):
        health = {}

    def add(
        kind: str,
        *,
        malformed: bool,
        missing: list[str],
        failed_fields: Sequence[str] = (),
        failed_invariants: Sequence[str] = (),
        artifact_id: str = "",
        source: str = "",
    ) -> None:
        artifact: dict[str, Any] = {
            "kind": kind,
            "module_hint": "",
            "malformed": malformed,
            "missing_fields": missing,
        }
        normalized_failed_fields = sorted({
            str(name) for name in failed_fields if str(name)
        })
        normalized_failed_invariants = sorted({
            str(name) for name in failed_invariants if str(name)
        })
        if normalized_failed_fields:
            artifact["failed_fields"] = normalized_failed_fields
        if normalized_failed_invariants:
            artifact["failed_invariants"] = normalized_failed_invariants
        if artifact_id:
            artifact["artifact_id"] = artifact_id
        if source:
            artifact["source"] = source
        artifacts.append(artifact)

    # Gold-informed FN+omission: only an external label can prove a clean
    # verdict on a gold-error case missed the candidate at its origin.
    gold_label = signal.gold_label
    state = signal.state
    type_value = candidate_kind_hint
    if bool(gold_label) and state == "verified_clean" and type_value == "omission":
        add(ARTIFACT_KIND_CANDIDATE, malformed=False, missing=["candidate"])

    try:
        required = int(health.get("required_candidate_count") or 0)
        accepted = int(health.get("construct_accepted_candidate_count") or 0)
        raw = int(health.get("construct_raw_candidate_count") or 0)
    except (TypeError, ValueError):
        required = accepted = raw = 0
    if required > 0 and accepted == 0:
        if raw > 0:
            # Construct produced raw candidates but accepted none: the
            # malformed construct-stage candidate is the upstream signal.
            add(ARTIFACT_KIND_CANDIDATE, malformed=True, missing=[])
        else:
            add(ARTIFACT_KIND_CANDIDATE, malformed=False, missing=["candidate"])

    authority_reasons = health.get("construct_authority_failure_reasons")
    if isinstance(authority_reasons, list) and authority_reasons:
        add(ARTIFACT_KIND_CANDIDATE, malformed=True, missing=[])

    # Per-certificate audits carry the field-level evidence needed to name a
    # broken field; aggregate quarantine counts are containment telemetry and
    # deliberately not emitted as artifacts.
    for audit_key, kind in (
        ("error_audits", ARTIFACT_KIND_ERROR_CERTIFICATE),
        ("defense_audits", ARTIFACT_KIND_DEFENSE_CERTIFICATE),
    ):
        audits = adjudication.get(audit_key)
        if not isinstance(audits, list):
            continue
        for audit in audits:
            if not isinstance(audit, Mapping):
                continue
            if str(audit.get("decision") or "") == "pass":
                continue
            failed_fields = audit.get("failed_fields")
            missing_from_audit = audit.get("missing_fields")
            failed_invariants = audit.get("failed_invariants")
            # Boundary for pre-contract cached detections: stored-form audits
            # audits carried only predicate labels. Re-project them via the
            # typed check->field contract rather than treating check names as
            # JSON fields.
            if not isinstance(failed_fields, list):
                legacy_checks = audit.get("failed_checks")
                if isinstance(legacy_checks, list):
                    check_fields = (
                        ERROR_CHECK_FIELDS
                        if kind == ARTIFACT_KIND_ERROR_CERTIFICATE else DEFENSE_CHECK_FIELDS
                    )
                    relational = (
                        ERROR_RELATIONAL_INVARIANTS
                        if kind == ARTIFACT_KIND_ERROR_CERTIFICATE
                        else DEFENSE_RELATIONAL_INVARIANTS
                    )
                    semantic_fields = sorted({
                        str(field)
                        for check in legacy_checks
                        for field in check_fields.get(str(check), ())
                    })
                    registered_fields = {
                        str(field)
                        for fields in check_fields.values()
                        for field in fields
                    }
                    direct_fields = sorted({
                        str(check) for check in legacy_checks
                        if str(check) in registered_fields
                    })
                    failed_fields = semantic_fields
                    failed_invariants = sorted({
                        str(check) for check in legacy_checks
                        if str(check) in relational
                    })
                    artifact_id = str(
                        audit.get("certificate_id")
                        or audit.get("candidate_id")
                        or ""
                    )
                    row = next(
                        (
                            item for item in _candidate_rows(detection_result, kind)
                            if str(
                                item.get("certificate_id")
                                or item.get("candidate_id")
                                or ""
                            ) == artifact_id
                        ),
                        {},
                    )
                    # Some pre-contract audits used provider field names
                    # directly as checks. Accept only registered fields, and
                    # call them missing only when the addressed row proves it.
                    missing_from_audit = [
                        field for field in direct_fields
                        if row.get(field) in (None, "", [], {})
                    ]
            if isinstance(failed_fields, list):
                participating_fields = sorted({
                    str(name) for name in failed_fields if str(name)
                })
                missing_fields = (
                    sorted({str(name) for name in missing_from_audit if str(name)})
                    if isinstance(missing_from_audit, list) else []
                )
                invariants = (
                    sorted({str(name) for name in failed_invariants if str(name)})
                    if isinstance(failed_invariants, list) else []
                )
                if missing_fields or participating_fields or invariants:
                    add(
                        kind,
                        malformed=True,
                        missing=missing_fields,
                        failed_fields=participating_fields,
                        failed_invariants=invariants,
                        artifact_id=str(
                            audit.get("certificate_id")
                            or audit.get("candidate_id")
                            or ""
                        ),
                        source=audit_key,
                    )

    if str(health.get("appeal_status") or "") == "provider_error":
        # A provider error during appeal did not emit content; the malformed
        # marker routes the cut to the transport/parse boundary origin.
        add(ARTIFACT_KIND_ERROR_CERTIFICATE, malformed=True, missing=[])

    try:
        court_required = int(adjudication.get("required_candidate_count") or 0)
    except (TypeError, ValueError):
        court_required = 0
    try:
        valid_error = int(adjudication.get("valid_error_certificate_count") or 0)
    except (TypeError, ValueError):
        valid_error = 0
    try:
        valid_defense = int(adjudication.get("valid_defense_certificate_count") or 0)
    except (TypeError, ValueError):
        valid_defense = 0
    surviving_ids = adjudication.get("surviving_error_certificate_ids")
    surviving_count = len(surviving_ids) if isinstance(surviving_ids, list) else 0
    prosecution_state = (
        signal.prosecution_coverage.state
        or str(adjudication.get("prosecution_coverage") or "")
    )
    defense_state = (
        signal.defense_coverage.state
        or str(adjudication.get("defense_coverage") or "")
    )
    error_type_token = ""
    gold_polarity = ""
    if signal.gold_label is True:
        gold_polarity = "error"
    elif signal.gold_label is False:
        gold_polarity = "clean"
    if signal.defect is DetectionDefect.UNRESOLVED:
        if gold_polarity == "error":
            error_type_token = "unresolved_should_error"
        elif gold_polarity == "clean":
            error_type_token = "unresolved_should_clean"
    hollow_defense = defense_state == "complete" and valid_defense == 0
    hollow_prosecution = prosecution_state == "complete" and valid_error == 0
    polarity_known = error_type_token in {
        "unresolved_should_error", "unresolved_should_clean",
    }
    requires_error_certificate = (
        error_type_token == "unresolved_should_error"
        or (not polarity_known and defense_state == "complete")
    )
    requires_defense_certificate = (
        error_type_token == "unresolved_should_clean"
        or (not polarity_known and prosecution_state == "complete")
    )
    if (
        requires_error_certificate
        and state == "unresolved"
        and prosecution_state == "incomplete"
        and valid_error == 0
        and surviving_count == 0
        and court_required > 0
        and signal.candidate_count > 0
        and not hollow_defense
    ):
        add(ARTIFACT_KIND_ERROR_CERTIFICATE, malformed=False, missing=["certificate"])

    if (
        requires_defense_certificate
        and state == "unresolved"
        and defense_state == "incomplete"
        and valid_defense == 0
        and court_required > 0
        and signal.candidate_count > 0
        and not hollow_prosecution
    ):
        add(ARTIFACT_KIND_DEFENSE_CERTIFICATE, malformed=False, missing=["certificate"])

    return artifacts


# ============================================================
# DetectionSignal (the unified projection)
# ============================================================

@dataclass(frozen=True)
class DetectionSignal:
    """Single projection of one detection result onto the evolution side.

    Carries: terminal state, authority, CRS vector, coverage readings, the
    label-free process diagnostics block, the provider-artifact integrity
    frontier, the self-supervised defect and process diagnostic readings,
    and the optional gold facts. Cut/episode derive their semantic records
    from these projected fields; they never re-reach into the raw row for a
    fact that lives on the signal.
    """

    state: str
    runtime_valid: bool | None
    decision_authority: str
    responsible_event_id: str | None
    responsible_step: int | str | None
    census: tuple[Mapping[str, Any], ...]
    prosecution: Mapping[str, Any]
    defense: Mapping[str, Any]
    proof_adjudication: Mapping[str, Any]
    crs: CRSSignal
    prosecution_coverage: CoverageReading
    defense_coverage: CoverageReading
    consensus_gate_applied: bool
    candidate_count: int
    diagnostics: Mapping[str, Any]
    defect: DetectionDefect
    defect_confidence: str  # high / medium / low / none
    process_diagnostic: ProcessDiagnostic
    diagnostic_only: bool
    runtime_error_category: str
    gold_label: bool | None
    has_gold: bool
    gold_steps: tuple[str, ...]
    gold_responsible_step: str | None
    provider_artifacts: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "sehd_rise.causal.signals.v1",
            "state": self.state,
            "runtime_valid": self.runtime_valid,
            "decision_authority": self.decision_authority,
            "responsible_event_id": self.responsible_event_id,
            "responsible_step": self.responsible_step,
            "crs_scores": dict(self.crs.scores),
            "crs_aggregate": self.crs.aggregate,
            "crs_source": self.crs.source,
            "crs_certificate_id": self.crs.certificate_id,
            "crs_complete": self.crs.complete,
            "crs_projection_confidence": self.crs.projection_confidence,
            "prosecution_coverage": self.prosecution_coverage.score,
            "defense_coverage": self.defense_coverage.score,
            "prosecution_coverage_state": self.prosecution_coverage.state,
            "defense_coverage_state": self.defense_coverage.state,
            "consensus_gate_applied": self.consensus_gate_applied,
            "candidate_count": self.candidate_count,
            "diagnostics": dict(self.diagnostics),
            "defect": self.defect.value,
            "defect_confidence": self.defect_confidence,
            "process_diagnostic": self.process_diagnostic.value,
            "diagnostic_only": self.diagnostic_only,
            "runtime_error_category": self.runtime_error_category,
            "gold_label": self.gold_label,
            "has_gold": self.has_gold,
            "gold_steps": list(self.gold_steps),
            "gold_responsible_step": self.gold_responsible_step,
            "provider_artifacts": [dict(item) for item in self.provider_artifacts],
        }


def _defect_confidence(
    defect: DetectionDefect,
    process_diagnostic: ProcessDiagnostic,
) -> str:
    """Bucket the self-supervised reading's attribution confidence.

    Per the v5.3 contract: high when the detector reported the exact broken
    stage; medium for gold-directed unresolved and CRS/boundary signals; low
    for unresolved_unknown and unavailable signals; ``none`` for stable
    success (no defect) and runtime-invalid (no attribution possible).
    """
    if defect is DetectionDefect.NONE:
        return "none"
    if defect is DetectionDefect.RUNTIME_INVALID:
        return "none"
    if process_diagnostic is not ProcessDiagnostic.NONE:
        # The detector reported which stage broke; the root-cause module
        # follows from pipeline structure, not from inference.
        return "high"
    if defect in (
        DetectionDefect.UNRESOLVED,
        DetectionDefect.CRS_CONTRADICTION,
        DetectionDefect.BOUNDARY_HESITATION,
    ):
        return "medium"
    return "low"


def _resolve_defect(
    *,
    row: Mapping[str, Any],
    state: str,
    crs_signal: CRSSignal,
    runtime_valid: bool | None,
    prosecution: CoverageReading,
    defense: CoverageReading,
) -> DetectionDefect:
    """Identify the self-supervised defect from row-internal signals.

    Cascade (gold-independent): a runtime-invalid row -> RUNTIME_INVALID; an
    unresolved terminal -> UNRESOLVED; an incomplete CRS -> SIGNAL_UNAVAILABLE;
    a CRS-versus-decision contradiction -> CRS_CONTRADICTION; a one-sided or
    evidence-starved adjudication -> EVIDENCE_INCOMPLETE. Otherwise NONE.
    """
    if runtime_valid is False or decision_authority_invalid(row):
        return DetectionDefect.RUNTIME_INVALID
    if state == "unresolved":
        return DetectionDefect.UNRESOLVED
    if not crs_signal.complete:
        return DetectionDefect.SIGNAL_UNAVAILABLE
    if _detect_crs_contradiction(state, crs_signal):
        return DetectionDefect.CRS_CONTRADICTION
    if _detect_evidence_incomplete(prosecution, defense, state):
        return DetectionDefect.EVIDENCE_INCOMPLETE
    return DetectionDefect.NONE


def runtime_invalid_signal(
    *,
    error: str = "",
    state: str = "runtime_invalid",
) -> DetectionSignal:
    """Build a diagnostic-only signal for a detector run that never completed.

    A runtime failure is genuinely informative (the genome could not be
    executed) but it is NOT an attribution: there is no authoritative
    verdict, no coverage reading, no CRS signal. The returned signal reports
    ``defect = RUNTIME_INVALID`` and ``diagnostic_only = True`` so downstream
    consumers cannot mistake it for fitness or correctness evidence. The
    exception text is reduced to a bounded ``runtime_error_category`` so no
    free-form runtime text can leak into a reflection prompt.
    """
    category = classify_runtime_error(error)
    crs = CRSSignal(
        scores={dimension: 0.0 for dimension in CRS_DIMENSIONS},
        aggregate=0.0,
        source="unavailable",
        certificate_id=None,
        complete=False,
        projection_confidence=0.0,
    )
    coverage = CoverageReading(None, None, False)
    return DetectionSignal(
        state=state,
        runtime_valid=False,
        decision_authority="",
        responsible_event_id=None,
        responsible_step=None,
        census=(),
        prosecution={},
        defense={},
        proof_adjudication={},
        crs=crs,
        prosecution_coverage=coverage,
        defense_coverage=coverage,
        consensus_gate_applied=False,
        candidate_count=0,
        diagnostics={},
        defect=DetectionDefect.RUNTIME_INVALID,
        defect_confidence="none",
        process_diagnostic=ProcessDiagnostic.NONE,
        diagnostic_only=True,
        runtime_error_category=category,
        gold_label=None,
        has_gold=False,
        gold_steps=(),
        gold_responsible_step=None,
        provider_artifacts=(),
    )


def project_detection(
    row: Mapping[str, Any],
    *,
    use_gold: bool = False,
    candidate_kind_hint: str = "",
) -> DetectionSignal:
    """Project one detection result into the unified evolution signal.

    Single authority: every evolution consumer reads projected fields from
    the returned ``DetectionSignal`` instead of re-walking the raw row. The
    label-truth fields enter the projection only when ``use_gold`` is enabled.
    """
    runtime_valid = row.get("runtime_valid")
    decision_authority = str(row.get("decision_authority") or "")
    if runtime_valid is False or decision_authority_invalid(row):
        state = "unresolved"
    else:
        state = resolve_evolution_state(row)

    proof = row.get("proof_adjudication") or {}
    proof = proof if isinstance(proof, Mapping) else {}
    prosecution = row.get("combined_prosecution") or row.get("prosecution") or {}
    if not isinstance(prosecution, Mapping):
        prosecution = {}
    defense = row.get("combined_defense") or row.get("defense") or {}
    if not isinstance(defense, Mapping):
        defense = {}
    census = row.get("census") or []
    census = tuple(item for item in census if isinstance(item, Mapping))

    crs_signal = extract_crs_signal(row)
    prosecution_coverage, defense_coverage = extract_coverage(row)

    responsible_event_id = row.get("responsible_event_id")
    responsible_event_id = (
        str(responsible_event_id) if responsible_event_id is not None else None
    )
    responsible_step = row.get("responsible_step")
    if not isinstance(responsible_step, (int, str)) or responsible_step == "":
        responsible_step = None

    diagnostics = _process_diagnostics(row)
    consensus_gate_applied = bool(
        (row.get("proof_adjudication") or {}).get("consensus_gate_applied")
    )

    # Label-truth is opt-in: in self-supervised mode the record omits
    # label-truth fields; the defect cascade and the artifact frontier
    # stay label-free.
    raw_has_gold = "gold_label" in row and row.get("gold_label") is not None
    has_gold = bool(use_gold and raw_has_gold)
    if has_gold and not isinstance(row["gold_label"], bool):
        # ``coerce_gold_label`` accepts string forms ("true"/"1"/"yes") for
        # robustness against dataset dumps, but the explicit opt-in path
        # requires the caller to supply a real boolean so the gold-vs-pred
        # comparison stays type-clean.
        coerced = coerce_gold_label(row["gold_label"])
        if coerced is None:
            raise ValueError("gold_label must be bool in gold-enhanced mode")
        gold_label: bool | None = coerced
    else:
        gold_label = bool(row["gold_label"]) if has_gold else None

    raw_steps = row.get("gold_steps")
    if raw_steps is None:
        raw_steps = row.get("hallucination_step")
    values = (
        raw_steps if isinstance(raw_steps, (list, tuple, set))
        else (() if raw_steps is None else (raw_steps,))
    )
    gold_steps = tuple(str(s) for s in values if s is not None and str(s) != "")
    gold_responsible_step = gold_steps[0] if (has_gold and gold_steps) else None

    defect = _resolve_defect(
        row=row, state=state, crs_signal=crs_signal,
        runtime_valid=runtime_valid,
        prosecution=prosecution_coverage, defense=defense_coverage,
    )
    process_diagnostic = _classify_process_diagnostic(diagnostics)
    diagnostic_only = defect is DetectionDefect.RUNTIME_INVALID

    provisional = DetectionSignal(
        state=state,
        runtime_valid=runtime_valid if runtime_valid is not None else None,
        decision_authority=decision_authority,
        responsible_event_id=responsible_event_id,
        responsible_step=responsible_step,
        census=census,
        prosecution=prosecution,
        defense=defense,
        proof_adjudication=proof,
        crs=crs_signal,
        prosecution_coverage=prosecution_coverage,
        defense_coverage=defense_coverage,
        consensus_gate_applied=consensus_gate_applied,
        candidate_count=len(census),
        diagnostics=diagnostics,
        defect=defect,
        defect_confidence="",  # filled after artifact projection
        process_diagnostic=process_diagnostic,
        diagnostic_only=diagnostic_only,
        runtime_error_category="",
        gold_label=gold_label,
        has_gold=has_gold,
        gold_steps=gold_steps,
        gold_responsible_step=gold_responsible_step,
        provider_artifacts=(),
    )
    provider_artifacts = tuple(
        project_provider_artifacts(
            detection_result=row,
            signal=provisional,
            candidate_kind_hint=candidate_kind_hint,
        )
    )
    runtime_error_category = ""
    if defect is DetectionDefect.RUNTIME_INVALID:
        # Callers using ``project_detection`` on a runtime-invalid row do not
        # pass an exception string here; the runtime-error category is the
        # bounded token ``detector_authority`` when authority failed, or the
        # ``unclassified_runtime_error`` default. The dedicated constructor
        # ``runtime_invalid_signal(error=...)`` should be preferred when the
        # raw error string is available.
        runtime_error_category = (
            "detector_authority" if decision_authority_invalid(row)
            else "unclassified_runtime_error"
        )
    confidence = _defect_confidence(defect, process_diagnostic)
    return DetectionSignal(
        state=state,
        runtime_valid=provisional.runtime_valid,
        decision_authority=provisional.decision_authority,
        responsible_event_id=provisional.responsible_event_id,
        responsible_step=provisional.responsible_step,
        census=census,
        prosecution=prosecution,
        defense=defense,
        proof_adjudication=proof,
        crs=crs_signal,
        prosecution_coverage=prosecution_coverage,
        defense_coverage=defense_coverage,
        consensus_gate_applied=consensus_gate_applied,
        candidate_count=len(census),
        diagnostics=diagnostics,
        defect=defect,
        defect_confidence=confidence,
        process_diagnostic=process_diagnostic,
        diagnostic_only=diagnostic_only,
        runtime_error_category=runtime_error_category,
        gold_label=gold_label,
        has_gold=has_gold,
        gold_steps=gold_steps,
        gold_responsible_step=gold_responsible_step,
        provider_artifacts=provider_artifacts,
    )


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "ADVERSARIAL_EQUILIBRIUM_THRESHOLD",
    "ARTIFACT_KIND_CANDIDATE",
    "ARTIFACT_KIND_DEFENSE_CERTIFICATE",
    "ARTIFACT_KIND_ERROR_CERTIFICATE",
    "ARTIFACT_KINDS",
    "COVERAGE_LOW_THRESHOLD",
    "COVERAGE_STATE_SCORE",
    "CRS_DIMENSIONS",
    "CRS_THRESHOLD_BORDERLINE",
    "CRS_THRESHOLD_ERROR",
    "CRS_WEIGHTS",
    "CRSSignal",
    "CoverageReading",
    "DetectionDefect",
    "DetectionSignal",
    "ProcessDiagnostic",
    "VALID_DETECTION_STATES",
    "classify_runtime_error",
    "compute_crs",
    "decision_authority_invalid",
    "extract_coverage",
    "extract_crs_signal",
    "has_explicit_authority",
    "missing_decision_authority",
    "parse_coverage",
    "prediction_state",
    "project_detection",
    "project_provider_artifacts",
    "resolve_authoritative_state",
    "resolve_evolution_state",
    "runtime_invalid_signal",
]