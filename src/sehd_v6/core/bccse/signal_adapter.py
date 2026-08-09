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
from typing import Any, Mapping, Sequence

from ...utils.gold_label import coerce_gold_label
from .promotion_gates import MetricSnapshot
from .types import ExperienceRecord


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

VALID_DETECTION_STATES = frozenset({
    "verified_error", "verified_clean", "unresolved",
})
_LEGACY_INCIDENT_STATE_MAP = {
    "verified_error": "verified_error",
    "verified_hallucination": "verified_error",
    "verified_incident": "verified_error",
    "verified_clean": "verified_clean",
    "verified_non_hallucination": "verified_clean",
    "no_verified_incident": "verified_clean",
    "unresolved": "unresolved",
}


def decision_authority_invalid(row: Mapping[str, Any]) -> bool:
    """Whether a detector result is barred from authoritative decisions.

    Single authority for the decision-authority check (the runner used to
    keep a second, divergent implementation): a present authority that is
    not ``authoritative`` is invalid (fail-closed).  An ABSENT authority is
    not classified here — the runner treats a missing authority as a hard
    error at its own boundary, and ``resolve_authoritative_state`` needs to
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
    """Whether the result carries no top-level decision authority at all.

    A row without an explicit authority is not resumable evidence: callers
    that require one fail closed instead of silently treating the absence
    as authoritative.
    """
    return not str(row.get("decision_authority") or "").strip()


def resolve_authoritative_state(row: Mapping[str, Any]) -> str:
    """Resolve the decision state without rewriting factual occurrence data.

    Invalid decision authority exposes ``unresolved`` to attribution and
    promotion consumers even if the factual occurrence ledger records an
    error. The original row/ledger remains untouched for incident audit.
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
                    "Invalid incident_state.occurrence_state: "
                    f"{occurrence!r}"
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
        # Legacy payloads may carry the pre-v3.5 top-level vocabulary
        # (verified_hallucination etc.); normalize them through the same map
        # as incident_state instead of crashing a resume/replay of old data.
        normalized_legacy = _LEGACY_INCIDENT_STATE_MAP.get(legacy_state)
        if normalized_legacy is not None:
            return normalized_legacy
        raise ValueError(f"Invalid detector state: {legacy_state!r}")
    return legacy_state


def resolve_evolution_state(row: Mapping[str, Any]) -> str:
    """Resolve the terminal state consumed by the evolution loop.

    The occurrence ledger is an append-only factual record and intentionally
    remains unchanged when an L2 policy alters the court's terminal edge.  A
    policy-aware result therefore needs its terminal ``state`` rather than
    the occurrence-first state used by external occurrence metrics.
    """
    if decision_authority_invalid(row):
        return "unresolved"
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    policy_evaluation = proof.get("policy_evaluation")
    if isinstance(policy_evaluation, Mapping):
        for value in (row.get("state"), proof.get("state")):
            if value in (None, ""):
                continue
            normalized = str(value)
            if normalized not in VALID_DETECTION_STATES:
                raise ValueError(f"Invalid evolution terminal state: {value!r}")
            return normalized
    return resolve_authoritative_state(row)


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
    # FIX (B1): 投影置信度——native CRS 为 1.0；证书投影按“枚举字段
    # 命中比例”计算（未识别字段回退 0.5 会降低置信度）；不确定
    # 回退为 0.0。下游可据此区分高/低可信的 CRS 诊断信号。
    projection_confidence: float = 1.0


COVERAGE_STATE_SCORE = {
    "complete": 1.0,
    "incomplete": 0.3,  # was 0.5 — lowered to align with COVERAGE_LOW_THRESHOLD
    "unresolved": 0.0,
}
# Backwards-compatible private alias. ``COVERAGE_STATE_SCORE`` is the single
# source of truth and is also consumed by fitness._coverage_rank, which used
# to keep its own duplicate literal of this table.
_COVERAGE_STATE_SCORE = COVERAGE_STATE_SCORE


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


def _certificate_projection(
    certificate: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Project one validated proof certificate into CRS-compatible dimensions.

    Returns:
        (scores, projection_confidence) — confidence is the fraction of
        enum-mapped dimensions whose source field matched a known value
        (unknown values fall back to 0.5 and reduce confidence).
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
                # 完整控辩覆盖的确定性投影：置信度 1.0
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
    # epistemic uncertainty.  This is not a silent all-zero fallback.
    scores = {dimension: 0.5 for dimension in CRS_DIMENSIONS}
    return CRSSignal(
        scores=scores,
        aggregate=compute_crs(scores),
        source="proof_projection_uncertain",
        certificate_id=None,
        complete=False,
        projection_confidence=0.0,
    )


def has_explicit_authority(row: Mapping[str, Any]) -> bool:
    """Whether the row carries the authoritative envelope required for scoring."""
    proof = row.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    top = str(row.get("decision_authority") or "").strip().lower()
    inner = str(proof.get("decision_authority") or "").strip().lower()
    return (
        top == "authoritative"
        and (not inner or inner == "authoritative")
    )


def prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Predicted polarity of a detector row; ``None`` for unresolved."""
    state = resolve_evolution_state(row)
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


def _scoreable_state(row: Mapping[str, Any]) -> bool | None:
    if row.get("runtime_valid") is not True or not has_explicit_authority(row):
        return None
    return prediction_state(row)


def _candidate_origin_refs(row: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for candidate in row.get("census") or ():
        if not isinstance(candidate, Mapping):
            continue
        value = candidate.get("origin_hypothesis_event_id")
        if value is not None:
            refs.append(str(value))
    return tuple(dict.fromkeys(refs))


def _walk_provider_meta(value: Any):
    if isinstance(value, Mapping):
        if value.get("request_hash") and not value.get("cache_hit"):
            yield value
        for child in value.values():
            yield from _walk_provider_meta(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_provider_meta(child)


def experience_record_from_row(
    row: Mapping[str, Any], *, include_gold: bool = False,
) -> ExperienceRecord:
    """Convert one detector row into the compiler-only, label-free view.

    Gold fields enter the record only when ``include_gold`` is explicitly
    requested; the default evolution path stays label-free.
    """
    state = resolve_evolution_state(row)
    gold_steps = tuple(str(item) for item in row.get("gold_steps") or ())
    health = row.get("evidence_health") or {}
    proof = row.get("proof_adjudication") or {}
    quarantined = (
        len(proof.get("quarantined_error_certificate_ids") or ())
        + len(proof.get("quarantined_defense_certificate_ids") or ())
    )
    provider_metas = tuple(_walk_provider_meta(row.get("providers") or {}))
    responsible = row.get("occurrence_responsible_step")
    if responsible is None:
        responsible = row.get("responsible_event_id")
    if responsible is None:
        responsible = row.get("responsible_step")
    return ExperienceRecord(
        record_key=str(row.get("case_ref") or ""),
        partition="evolution",
        gold_positive=(coerce_gold_label(row.get("gold_label")) if include_gold else None),
        predicted_positive=prediction_state(row),
        gold_responsible_ref=(gold_steps[0] if include_gold and gold_steps else None),
        predicted_responsible_ref=(
            str(responsible)
            if responsible is not None and prediction_state(row) is not None
            else None
        ),
        candidate_origin_refs=_candidate_origin_refs(row),
        analysis_dimension="trajectory_causal_validity",
        origin_relation="earliest_agent_controlled_origin",
        evidence_obligation="complete_admissible_causal_chain",
        clean_boundary="supported_or_correct_or_ordinary_failure",
        certificate_valid=not decision_authority_invalid(row),
        unresolved=state == "unresolved",
        model_calls=len(provider_metas),
        latency_seconds=sum(float(meta.get("latency_seconds") or 0.0) for meta in provider_metas),
        metadata={
            "proof_state": state,
            "occurrence_state": row.get("occurrence_state"),
            "legacy_proof_state": row.get("state"),
            "prediction_state_authority": (
                "invalid" if decision_authority_invalid(row) else "occurrence"
            ),
            "wide_construct_state": row.get("wide_construct_state"),
            "construct_view_status": health.get("construct_view_status"),
            "candidate_count": len(row.get("census") or ()),
            "required_candidate_count": int(health.get("required_candidate_count") or 0),
            "resolved_candidate_count": int(health.get("resolved_candidate_count") or 0),
            "quarantined_certificate_count": quarantined,
        },
    )


def metric_snapshot(rows: Sequence[Mapping[str, Any]]) -> MetricSnapshot:
    """Aggregate a MetricSnapshot from detector rows.

    The evolution terminal state (``resolve_evolution_state``) is the single
    authority for polarity; abstentions never enter the confusion buckets.
    """
    metas = tuple(
        meta
        for row in rows
        for meta in _walk_provider_meta(row.get("providers") or {})
    )
    case_count = len(rows)
    unresolved = 0
    gold_positive = gold_negative = 0
    tp = fp = fn = tn = step_correct = 0
    for row in rows:
        predicted = _scoreable_state(row)
        if predicted is None:
            unresolved += 1
            continue
        gold = coerce_gold_label(row.get("gold_label"))
        if gold is None:
            continue
        if predicted and gold:
            tp += 1
            gold_positive += 1
            gold_steps = {str(item) for item in row.get("gold_steps") or ()}
            responsible = row.get("occurrence_responsible_step")
            if responsible is None:
                responsible = row.get("responsible_step")
            step_correct += int(
                bool(gold_steps)
                and responsible is not None
                and str(responsible) in gold_steps
            )
        elif predicted:
            fp += 1
            gold_negative += 1
        elif gold:
            fn += 1
            gold_positive += 1
        else:
            tn += 1
            gold_negative += 1

    valid_outputs = case_count - unresolved
    # Precision uses predicted-side totals; recall / FPR / step accuracy use
    # gold-side totals (tp+fn / fp+tn), so recall is not precision under a
    # different name.
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / gold_positive if gold_positive else 0.0
    hallucination_f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    clean_precision = tn / (tn + fn) if tn + fn else 0.0
    clean_recall = tn / gold_negative if gold_negative else 0.0
    clean_f1 = (
        2 * clean_precision * clean_recall / (clean_precision + clean_recall)
        if clean_precision + clean_recall else 0.0
    )
    return MetricSnapshot(
        macro_f1=(hallucination_f1 + clean_f1) / 2,
        precision=precision,
        hallucination_recall=recall,
        clean_fpr=fp / gold_negative if gold_negative else 0.0,
        responsible_step_accuracy=(
            step_correct / gold_positive if gold_positive else 0.0
        ),
        valid_output_rate=valid_outputs / case_count if case_count else 0.0,
        unresolved_rate=unresolved / case_count if case_count else 0.0,
        invalid_rate=(
            1.0 - valid_outputs / case_count if case_count else 0.0
        ),
        model_calls=len(metas),
        latency_seconds=sum(float(meta.get("latency_seconds") or 0.0) for meta in metas),
        cost=0.0,
    )


def extract_crs_signal(row: Mapping[str, Any]) -> CRSSignal:
    """Return native CRS when available, otherwise a provenance-tagged projection."""
    return _native_crs(row) or _proof_projection(row)
