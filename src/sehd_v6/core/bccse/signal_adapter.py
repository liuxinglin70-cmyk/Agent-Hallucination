"""BCCSE v5 adapters for PACA-TCL v3.5 self-supervision signals.

PACA-TCL proof-court v3.5 intentionally does not emit the continuous CRS
object used by earlier PACA variants.  BCCSE nevertheless needs an auditable
five-dimensional diagnostic signal.  This module therefore:

1. reads a native five-dimensional CRS vector when one is present; otherwise
2. projects v3.5's validated proof-certificate fields into the same five
   dimensions without changing the frozen detector or its final decision.

The projection is diagnostic-only.  Its provenance is returned with every
vector so it cannot be mistaken for a native PACA-TCL v3.5 output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CRS_DIMENSIONS = (
    "factuality",
    "counterfactual",
    "recovery",
    "process",
    "scope",
)

# Frozen by the BCCSE protocol.  Recovery is a positive recovery score, hence
# its risk contribution is (1 - recovery).
CRS_WEIGHTS = {
    "factuality": 0.35,
    "counterfactual": 0.15,
    "recovery": 0.20,
    "process": 0.20,
    "scope": 0.10,
}


@dataclass(frozen=True)
class CoverageReading:
    score: float | None
    state: str | None
    quantitative: bool


@dataclass(frozen=True)
class CRSSignal:
    scores: dict[str, float]
    aggregate: float
    source: str
    certificate_id: str | None
    complete: bool


_COVERAGE_STATE_SCORE = {
    "complete": 1.0,
    "incomplete": 0.5,
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
    """Parse categorical v3.5 coverage or a legacy numeric coverage ratio."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _COVERAGE_STATE_SCORE:
            return CoverageReading(
                score=_COVERAGE_STATE_SCORE[normalized],
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
            for candidate in (
                item.get("adjusted_scores"),
                item.get("raw_scores"),
                item,
            ):
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


def _certificate_projection(certificate: Mapping[str, Any]) -> dict[str, float]:
    """Project one validated proof certificate into CRS-compatible dimensions."""
    process_checks = (
        bool(certificate.get("norm_evidence_refs")),
        bool(certificate.get("violation_evidence_refs")),
        bool(certificate.get("agent_control_evidence_refs")),
        bool(certificate.get("impact_evidence_refs")),
    )
    process_score = sum(process_checks) / len(process_checks)
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
    }


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
    state = str(row.get("state") or proof.get("state") or "")
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
            )

    projected: list[tuple[float, dict[str, float], str | None]] = []
    for certificate in certificates:
        scores = _certificate_projection(certificate)
        projected.append(
            (
                compute_crs(scores),
                scores,
                str(certificate.get("certificate_id") or "") or None,
            )
        )
    if projected:
        aggregate, scores, certificate_id = max(projected, key=lambda item: item[0])
        return CRSSignal(
            scores=scores,
            aggregate=aggregate,
            source="proof_projection_certificate",
            certificate_id=certificate_id,
            complete=True,
        )

    # Unresolved proof with no usable certificate: 0.5 explicitly denotes
    # epistemic uncertainty.  This is not a silent all-zero fallback.
    scores = {dimension: 0.5 for dimension in CRS_DIMENSIONS}
    return CRSSignal(
        scores=scores,
        aggregate=compute_crs(scores),
        source="proof_projection_uncertain",
        certificate_id=None,
        complete=False,
    )


def extract_crs_signal(row: Mapping[str, Any]) -> CRSSignal:
    """Return native CRS when available, otherwise a provenance-tagged projection."""
    return _native_crs(row) or _proof_projection(row)
