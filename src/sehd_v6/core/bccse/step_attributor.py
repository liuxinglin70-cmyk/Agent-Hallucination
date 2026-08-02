"""BCCSE v5 — Step-level causal process reflection (self-supervised).

Core novelty of BCCSE v5: the detection pipeline's own causal attribution
capability is recursively applied to diagnose skill deficiencies.

Self-supervised trigger signals (NO gold label required):
- Unresolved: system admits "current skill insufficient to judge"
- CRS internal contradiction: dimension scores conflict with final decision
- Evidence chain incomplete: prosecution/defense coverage abnormally low
  (including one-sided adjudication where the losing side never completed)

Note (v5.4): the former "boundary hesitation" trigger was retired — it
required quantitative coverage readings, which the v3.5 proof court never
emits (coverage is categorical), so it could never fire in production.

Gold label is used as OPTIONAL enhancement when available:
- Forces trigger when prediction disagrees with gold
- Provides error type classification (FP/FN) for more targeted attribution
- NEVER enters skill content (rules must not contain gold info)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .genome import MODULE_IDS

from .signal_adapter import (
    CRS_WEIGHTS,
    CRSSignal,
    CoverageReading,
    extract_coverage,
    extract_crs_signal,
    resolve_authoritative_state,
)


class ErrorType(str, Enum):
    """Failure modes attributable to specific genome modules."""

    CORRECT = "correct"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    FALSE_NEGATIVE_UNRESOLVED = "false_negative_unresolved"
    FALSE_NEGATIVE_CLEAN = "false_negative_clean"
    UNRESOLVED_SHOULD_ERROR = "unresolved_should_error"
    UNRESOLVED_SHOULD_CLEAN = "unresolved_should_clean"
    UNRESOLVED_UNKNOWN = "unresolved_unknown"  # self-supervised: no gold
    UNRESOLVED_ERROR = "unresolved_error"
    INVALID_CERTIFICATE = "invalid_certificate"
    LOCALIZATION_ERROR = "localization_error"
    CANDIDATE_MISS = "candidate_miss"
    CRS_CONTRADICTION = "crs_contradiction"  # self-supervised
    EVIDENCE_INCOMPLETE = "evidence_incomplete"  # self-supervised
    FOCUSED_CERTIFICATE_QUARANTINE = "focused_certificate_quarantine"
    APPEAL_PROSECUTOR_FAILURE = "appeal_prosecutor_failure"
    APPEAL_DEFENDER_FAILURE = "appeal_defender_failure"
    APPEAL_COVERAGE_MISMATCH = "appeal_coverage_mismatch"
    LOCALIZATION_AMBIGUOUS = "localization_ambiguous"
    # Retired in v5.4 (unreachable: v3.5 coverage is categorical, never
    # quantitative). Retained so historical attribution records stay readable.
    BOUNDARY_HESITATION = "boundary_hesitation"
    SIGNAL_UNAVAILABLE = "signal_unavailable"  # malformed/incomplete trace


class ReflectionType(str, Enum):
    """SkillEvolver-style reflection classification."""

    STABLE_SUCCESS = "stable_success"
    DISCOVERY = "discovery"
    OPTIMIZATION = "optimization"
    SKILL_DEFECT = "skill_defect"
    EXECUTION_LAPSE = "execution_lapse"


class TriggerSource(str, Enum):
    """What triggered the reflection (for audit)."""

    UNRESOLVED = "unresolved"
    CRS_CONTRADICTION = "crs_contradiction"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    BOUNDARY_HESITATION = "boundary_hesitation"  # retired in v5.4 (unreachable)
    SIGNAL_UNAVAILABLE = "signal_unavailable"
    FOCUSED_QUARANTINE = "focused_quarantine"
    APPEAL_FAILURE = "appeal_failure"
    LOCALIZATION = "localization"
    GOLD_MISMATCH = "gold_mismatch"  # optional enhancement
    NONE = "none"  # no trigger (stable success)


# Mapping from error type to primary target module
ERROR_MODULE_MAP: dict[ErrorType, str] = {
    ErrorType.FALSE_POSITIVE: "clean_defense_boundary",
    ErrorType.FALSE_NEGATIVE: "evidence_obligation",
    ErrorType.FALSE_NEGATIVE_UNRESOLVED: "evidence_obligation",
    ErrorType.FALSE_NEGATIVE_CLEAN: "evidence_obligation",
    ErrorType.UNRESOLVED_SHOULD_ERROR: "evidence_obligation",
    # UNRESOLVED_SHOULD_CLEAN: defense already complete but output unresolved.
    # Root cause is abstention_rule, not evidence_obligation.
    ErrorType.UNRESOLVED_SHOULD_CLEAN: "abstention_rule",
    ErrorType.UNRESOLVED_UNKNOWN: "evidence_obligation",  # default for self-supervised
    ErrorType.UNRESOLVED_ERROR: "abstention_rule",
    ErrorType.INVALID_CERTIFICATE: "atomic_verifier_instruction",
    ErrorType.LOCALIZATION_ERROR: "causal_link_test",
    ErrorType.CANDIDATE_MISS: "candidate_trigger",
    ErrorType.CRS_CONTRADICTION: "evidence_obligation",
    ErrorType.EVIDENCE_INCOMPLETE: "evidence_obligation",
    ErrorType.FOCUSED_CERTIFICATE_QUARANTINE: "atomic_verifier_instruction",
    ErrorType.APPEAL_PROSECUTOR_FAILURE: "atomic_verifier_instruction",
    ErrorType.APPEAL_DEFENDER_FAILURE: "clean_defense_boundary",
    ErrorType.APPEAL_COVERAGE_MISMATCH: "candidate_to_verifier_route",
    ErrorType.LOCALIZATION_AMBIGUOUS: "earliest_origin_tiebreak",
    ErrorType.BOUNDARY_HESITATION: "clean_defense_boundary",
    ErrorType.SIGNAL_UNAVAILABLE: "evidence_obligation",
}

# Secondary module (alternative attribution)
ERROR_MODULE_SECONDARY: dict[ErrorType, str] = {
    ErrorType.FALSE_POSITIVE: "evidence_obligation",
    ErrorType.FALSE_NEGATIVE: "abstention_rule",
    ErrorType.FALSE_NEGATIVE_UNRESOLVED: "abstention_rule",
    ErrorType.FALSE_NEGATIVE_CLEAN: "clean_defense_boundary",
    ErrorType.UNRESOLVED_SHOULD_ERROR: "abstention_rule",
    # UNRESOLVED_SHOULD_CLEAN: secondary is clean_defense_boundary
    # (defense is already complete, verify it's correctly formed)
    ErrorType.UNRESOLVED_SHOULD_CLEAN: "clean_defense_boundary",
    ErrorType.UNRESOLVED_UNKNOWN: "abstention_rule",
    ErrorType.UNRESOLVED_ERROR: "evidence_obligation",
    ErrorType.INVALID_CERTIFICATE: "evidence_obligation",
    ErrorType.LOCALIZATION_ERROR: "earliest_origin_tiebreak",
    ErrorType.CANDIDATE_MISS: "candidate_to_verifier_route",
    ErrorType.CRS_CONTRADICTION: "atomic_verifier_instruction",
    ErrorType.EVIDENCE_INCOMPLETE: "atomic_verifier_instruction",
    ErrorType.FOCUSED_CERTIFICATE_QUARANTINE: "evidence_obligation",
    ErrorType.APPEAL_PROSECUTOR_FAILURE: "evidence_obligation",
    ErrorType.APPEAL_DEFENDER_FAILURE: "atomic_verifier_instruction",
    ErrorType.APPEAL_COVERAGE_MISMATCH: "atomic_verifier_instruction",
    ErrorType.LOCALIZATION_AMBIGUOUS: "causal_link_test",
    ErrorType.BOUNDARY_HESITATION: "abstention_rule",
    ErrorType.SIGNAL_UNAVAILABLE: "atomic_verifier_instruction",
}

# ============================================================
# Self-supervised trigger thresholds
# ============================================================

# Coverage below this is considered "abnormally low"
COVERAGE_LOW_THRESHOLD = 0.3
# Frozen PACA CRS decision thresholds used only for diagnostic consistency.
CRS_THRESHOLD_BORDERLINE = 0.28
CRS_THRESHOLD_ERROR = 0.45
# Adversarial equilibrium: min(p, d) / max(p, d). Below this ratio the
# adjudication is considered one-sided (insufficient adversarial testing).
# Only meaningful for QUANTITATIVE coverage readings; see
# `_adversarially_tested` for the categorical (production v3.5) rule.
ADVERSARIAL_EQUILIBRIUM_THRESHOLD = 0.4
def _compute_adversarial_equilibrium(
    prosecution: CoverageReading, defense: CoverageReading
) -> float:
    """Compute the adversarial equilibrium score of a proof adjudication.

    The adversarial equilibrium measures how balanced the prosecution and
    defense coverage are. A high score (close to 1.0) means both sides
    were thoroughly tested; a low score (close to 0.0) means the
    adjudication was one-sided — one party dominated without meaningful
    challenge from the other.

    This is the core architectural fix for the FP blind spot: the old
    design treated "high CRS + surviving error certificate" as
    high-confidence, but CRS is a prosecution-side score. A one-sided
    adjudication (high prosecution, low defense) can produce high CRS
    while being a false positive. The equilibrium score captures this.

    Returns:
        Equilibrium score in [0.0, 1.0]. Returns 0.0 when either side's
        coverage is unavailable (fail-closed: no evidence of balance).
    """
    p = prosecution.score
    d = defense.score
    if p is None or d is None:
        return 0.0
    if p <= 0 and d <= 0:
        return 0.0
    # FIX (B8): 分级阈值——当获胜方分数很高时，不平衡更可能是
    # 合法主导（证据确实一边倒）而非对抗不充分，适当放宽均衡
    # 要求，避免 EVIDENCE_INCOMPLETE 过度触发。上限仍限制在 1.0。
    max_score = max(p, d)
    if max_score > 0.8:
        return min(1.0, min(p, d) / max_score * 1.5)
    return min(p, d) / max_score


def _adversarially_tested(
    prosecution: CoverageReading, defense: CoverageReading
) -> bool:
    """Whether BOTH sides of the proof court were genuinely exercised.

    v5.4 fix: for CATEGORICAL coverage readings (the production v3.5
    contract), a side that is "incomplete"/"unresolved" means that side
    never finished its run — this is NOT legitimate dominance and must
    never count as adversarially tested. The old code fed the categorical
    state scores (complete=1.0, incomplete=0.3) through the equilibrium
    ratio, and the B8 boost (x1.5) lifted one-sided verdicts
    (1.0 vs 0.3 → 0.45) over the 0.4 threshold, rubber-stamping exactly
    the one-sided adjudications this gate was built to catch.

    Rules:
    - Either side categorical: both states must be "complete".
    - Both quantitative: equilibrium ratio must clear the threshold.
    - Unavailable: not tested (fail-closed).
    """
    if prosecution.state is not None or defense.state is not None:
        return prosecution.state == "complete" and defense.state == "complete"
    if prosecution.quantitative and defense.quantitative:
        return (
            _compute_adversarial_equilibrium(prosecution, defense)
            >= ADVERSARIAL_EQUILIBRIUM_THRESHOLD
        )
    return False


@dataclass(frozen=True)
class AttributionSignal:
    """Structured output of causal process reflection.

    This is the evolution signal that drives module-targeted mutation.
    Supports both self-supervised (no gold) and gold-enhanced modes.
    """

    error_type: ErrorType
    target_module: str
    secondary_module: str
    attribution_confidence: str  # high / medium / low (v5.3)
    reflection_type: ReflectionType
    trigger_source: TriggerSource
    evidence_summary: str
    prosecution_coverage: float | None
    defense_coverage: float | None
    prosecution_coverage_state: str | None
    defense_coverage_state: str | None
    coverage_quantitative: bool
    crs_scores: dict[str, float]  # five CRS dimension scores
    crs_aggregate: float
    crs_source: str
    crs_complete: bool
    consensus_gate_applied: bool
    candidate_count: int
    state: str
    gold_label: bool | None  # None = no gold available
    has_gold: bool
    gold_enhanced: bool
    responsible_step_correct: bool
    # v5.5: label-free process diagnostics used for root-cause routing.
    localization_status: str = "not_applicable"
    appeal_status: str = "not_required"
    focused_quarantined_candidate_count: int = 0
    process_diagnostic_applied: bool = False
    runtime_valid: bool = True
    # v5.5: when True this signal records an observation only. The detector
    # never produced an authoritative verdict, so the signal must never be
    # used as evolution, fitness, or correctness evidence.
    diagnostic_only: bool = False
    runtime_error_category: str = ""

    @property
    def should_evolve(self) -> bool:
        """Whether this case provides an evolution signal (triggers reflection).

        Diagnostic-only signals never trigger evolution: without an
        authoritative detector verdict there is no observable process to
        attribute a skill defect to.
        """
        if self.diagnostic_only:
            return False
        return self.error_type != ErrorType.CORRECT

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type.value,
            "target_module": self.target_module,
            "secondary_module": self.secondary_module,
            "attribution_confidence": self.attribution_confidence,
            "reflection_type": self.reflection_type.value,
            "trigger_source": self.trigger_source.value,
            "evidence_summary": self.evidence_summary,
            "prosecution_coverage": self.prosecution_coverage,
            "defense_coverage": self.defense_coverage,
            "prosecution_coverage_state": self.prosecution_coverage_state,
            "defense_coverage_state": self.defense_coverage_state,
            "coverage_quantitative": self.coverage_quantitative,
            "crs_scores": self.crs_scores,
            "crs_aggregate": self.crs_aggregate,
            "crs_source": self.crs_source,
            "crs_complete": self.crs_complete,
            "consensus_gate_applied": self.consensus_gate_applied,
            "candidate_count": self.candidate_count,
            "state": self.state,
            "gold_label": self.gold_label,
            "has_gold": self.has_gold,
            "gold_enhanced": self.gold_enhanced,
            "responsible_step_correct": self.responsible_step_correct,
            "localization_status": self.localization_status,
            "appeal_status": self.appeal_status,
            "focused_quarantined_candidate_count": (
                self.focused_quarantined_candidate_count
            ),
            "process_diagnostic_applied": self.process_diagnostic_applied,
            "runtime_valid": self.runtime_valid,
            "diagnostic_only": self.diagnostic_only,
            "runtime_error_category": self.runtime_error_category,
            "should_evolve": self.should_evolve,
        }


# Runtime failure categories. Free-form exception text must never reach a
# reflection prompt, so the runtime error is reduced to a bounded category.
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
    """Reduce a runtime failure to a bounded, promptable category."""
    lowered = str(error or "").lower()
    for category, markers in _RUNTIME_ERROR_CATEGORIES:
        if any(marker in lowered for marker in markers):
            return category
    return "unclassified_runtime_error"


def runtime_invalid_signal(
    *,
    error: str = "",
    state: str = "runtime_invalid",
) -> AttributionSignal:
    """Build diagnostic-only telemetry for a detector run that never completed.

    A runtime failure is genuinely informative — it says the current genome
    could not be executed on this case — but it is NOT an attribution: there
    is no authoritative verdict, no coverage reading, and no CRS signal, so
    nothing can be blamed on a specific skill module.

    The returned signal therefore:
    - reports ``should_evolve == False`` so it can never drive a mutation;
    - carries ``diagnostic_only=True`` so downstream consumers cannot
      mistake it for fitness or correctness evidence;
    - reduces the exception to a bounded ``runtime_error_category`` so no
      free-form runtime text can leak into a reflection prompt.
    """
    category = classify_runtime_error(error)
    return AttributionSignal(
        error_type=ErrorType.CORRECT,
        target_module="",
        secondary_module="",
        attribution_confidence="none",
        reflection_type=ReflectionType.EXECUTION_LAPSE,
        trigger_source=TriggerSource.SIGNAL_UNAVAILABLE,
        evidence_summary=" | ".join((
            f"state={state}",
            "trigger=runtime_invalid",
            "attribution=unavailable_no_authoritative_verdict",
            f"runtime_error_category={category}",
            "claim_scope=diagnostic_only_not_evolution_evidence",
        )),
        prosecution_coverage=None,
        defense_coverage=None,
        prosecution_coverage_state=None,
        defense_coverage_state=None,
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="unavailable",
        crs_complete=False,
        consensus_gate_applied=False,
        candidate_count=0,
        state=state,
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
        runtime_valid=False,
        diagnostic_only=True,
        runtime_error_category=category,
    )


def _prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Extract predicted polarity from state string."""
    state = resolve_authoritative_state(row)
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None  # unresolved


def _check_step_correct(row: Mapping[str, Any]) -> bool:
    """Check if responsible step localization is correct."""
    raw_steps = row.get("gold_steps", row.get("hallucination_step"))
    values = raw_steps if isinstance(raw_steps, (list, tuple, set)) else (
        () if raw_steps is None else (raw_steps,)
    )
    gold_steps = tuple(str(s) for s in values if s is not None and str(s) != "")
    predicted = row.get("responsible_step")
    if not gold_steps or predicted is None:
        return False
    return str(predicted) in gold_steps


def _extract_coverage(
    row: Mapping[str, Any],
) -> tuple[CoverageReading, CoverageReading]:
    """Read the real categorical/numeric v3.5 coverage contract."""
    return extract_coverage(row)


def _extract_crs_scores(row: Mapping[str, Any]) -> CRSSignal:
    """Read native CRS or a provenance-tagged v3.5 proof projection."""
    return extract_crs_signal(row)


def _count_candidates(row: Mapping[str, Any]) -> int:
    """Count census candidates."""
    census = row.get("census") or []
    return len(census)


def _has_surviving_error_certificate(row: Mapping[str, Any]) -> bool:
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        return False
    return bool(proof.get("surviving_error_certificate_ids") or [])


def _process_diagnostics(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the detector's label-free process diagnostics block.

    Prefers the canonical ``diagnostics`` projection emitted by
    ``paca_tcl.evolution_contract.project_for_evolution``. When a raw
    detector result is supplied instead, the same fields are derived from
    ``evidence_health`` / ``proof_adjudication`` / ``providers`` so that
    attribution behaves identically on both shapes.
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
            proof.get("localization_status") or health.get("localization_status") or "not_applicable"
        ),
    }


# Appeal failure messages raised by the v3.5 proof engine appeal stage.
# Matched as substrings against ``diagnostics.appeal_error`` to decide which
# side of the appellate court actually broke.
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
# Appeal statuses indicating the appellate stage did not complete.
_APPEAL_INCOMPLETE_STATUSES = frozenset({
    "incomplete_initial_error_preserved",
    "incomplete_fail_closed",
    "provider_error",
})

# Error types whose polarity is established by an external gold label.
# Process diagnostics may refine their partner module but must never
# overwrite the polarity itself.
_GOLD_POLARITY_ERROR_TYPES = frozenset({
    ErrorType.FALSE_POSITIVE,
    ErrorType.FALSE_NEGATIVE,
    ErrorType.FALSE_NEGATIVE_UNRESOLVED,
    ErrorType.FALSE_NEGATIVE_CLEAN,
    ErrorType.UNRESOLVED_SHOULD_ERROR,
    ErrorType.UNRESOLVED_SHOULD_CLEAN,
    ErrorType.LOCALIZATION_ERROR,
    ErrorType.CANDIDATE_MISS,
})

# Generic self-supervised outcomes that a concrete process diagnostic
# should refine into a specific broken stage.
_REFINABLE_ERROR_TYPES = frozenset({
    ErrorType.CORRECT,
    ErrorType.UNRESOLVED_UNKNOWN,
    ErrorType.CRS_CONTRADICTION,
    ErrorType.EVIDENCE_INCOMPLETE,
    ErrorType.SIGNAL_UNAVAILABLE,
})


def _classify_appeal_failure(diagnostics: Mapping[str, Any]) -> ErrorType | None:
    """Classify which appellate party failed, if the appeal stage broke."""
    status = str(diagnostics.get("appeal_status") or "not_required")
    error_text = str(diagnostics.get("appeal_error") or "").lower()
    error_type = str(diagnostics.get("appeal_error_type") or "")
    if status not in _APPEAL_INCOMPLETE_STATUSES and not error_type:
        return None
    if any(marker in error_text for marker in _APPEAL_COVERAGE_MARKERS):
        return ErrorType.APPEAL_COVERAGE_MISMATCH
    if any(marker in error_text for marker in _APPEAL_DEFENSE_MARKERS):
        return ErrorType.APPEAL_DEFENDER_FAILURE
    if any(marker in error_text for marker in _APPEAL_PROSECUTION_MARKERS):
        return ErrorType.APPEAL_PROSECUTOR_FAILURE
    # An unrecognized appeal breakage still broke the prosecution-side
    # appellate obligation, which is the stage that runs first.
    return ErrorType.APPEAL_PROSECUTOR_FAILURE


def _classify_process_diagnostics(
    diagnostics: Mapping[str, Any],
) -> tuple[ErrorType, TriggerSource] | None:
    """Identify the process stage that broke, from label-free diagnostics.

    Priority reflects execution order in the v3.5 proof court: an appeal
    breakage invalidates more of the trace than a per-candidate quarantine,
    which in turn is more fundamental than a localization tie-break.

    Returns None when diagnostics show no process-stage defect.
    """
    appeal_error_type = _classify_appeal_failure(diagnostics)
    if appeal_error_type is not None:
        return appeal_error_type, TriggerSource.APPEAL_FAILURE

    if _diagnostic_int(diagnostics, "focused_quarantined_candidate_count") > 0:
        return (
            ErrorType.FOCUSED_CERTIFICATE_QUARANTINE,
            TriggerSource.FOCUSED_QUARANTINE,
        )

    localization = str(diagnostics.get("localization_status") or "not_applicable")
    if localization == "failed_temporal_fallback":
        # Two or more error certificates survived but counterfactual
        # localization could not rank them, so the responsible step was
        # chosen by temporal fallback rather than causal sufficiency.
        return ErrorType.LOCALIZATION_AMBIGUOUS, TriggerSource.LOCALIZATION
    return None


def _diagnostic_int(diagnostics: Mapping[str, Any], key: str) -> int:
    try:
        return int(diagnostics.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _route_process_failure(
    row: Mapping[str, Any],
    *,
    state: str,
    candidate_count: int,
    prosecution: CoverageReading,
    defense: CoverageReading,
    default_module: str,
    default_secondary: str,
) -> tuple[str, str]:
    """Route a process failure to the stage where the trace actually broke."""
    if candidate_count == 0:
        return "candidate_trigger", "candidate_to_verifier_route"

    # v5.3 fix: When defense is already complete but output is unresolved,
    # root cause is abstention_rule (defense complete → should be clean),
    # Secondary is clean_defense_boundary (verify defense is correctly formed).
    if state == "unresolved" and defense.state == "complete":
        return "abstention_rule", "clean_defense_boundary"

    proof = row.get("proof_adjudication") or {}
    prosecution_payload = (
        row.get("combined_prosecution") or row.get("prosecution") or {}
    )
    prosecution_rows = (
        prosecution_payload.get("candidates") or []
        if isinstance(prosecution_payload, Mapping)
        else []
    )
    quarantined_errors = (
        proof.get("quarantined_error_certificate_ids") or []
        if isinstance(proof, Mapping)
        else []
    )

    if prosecution.state in {"incomplete", "unresolved"}:
        if not prosecution_rows:
            return "candidate_to_verifier_route", "evidence_obligation"
        if quarantined_errors:
            return "atomic_verifier_instruction", "evidence_obligation"
        return "evidence_obligation", "atomic_verifier_instruction"

    if defense.state in {"incomplete", "unresolved"}:
        return "clean_defense_boundary", "evidence_obligation"

    if state == "unresolved":
        return "abstention_rule", "causal_link_test"

    return default_module, default_secondary


def _detect_crs_contradiction(state: str, crs_signal: CRSSignal) -> bool:
    """Compare the fixed-weight aggregate with the proof-court decision.

    Recovery is inverted in the CRS formula, so raw dimensions cannot all be
    treated as same-polarity "error support".
    """
    if not crs_signal.complete:
        return False
    # FIX (C7): recovery 是倒置维度（风险贡献为 1-recovery）。高 recovery
    # （错误在影响前已修复）既拉低 aggregate，也正向支持 clean 判定，
    # 矛盾检测必须区分这两种效应。
    recovery = crs_signal.scores.get("recovery")
    if state == "verified_error":
        if (
            crs_signal.aggregate < CRS_THRESHOLD_ERROR
            and recovery is not None
            and recovery >= 0.75
        ):
            # aggregate 偏低可能主要由高 recovery 造成；剔除 recovery
            # 维度后重新归一再比较，避免将“已修复但确有影响的错误”
            # 误判为矛盾。
            recovery_free = (
                crs_signal.aggregate
                - CRS_WEIGHTS["recovery"] * (1.0 - recovery)
            ) / (1.0 - CRS_WEIGHTS["recovery"])
            return recovery_free < CRS_THRESHOLD_ERROR
        return crs_signal.aggregate < CRS_THRESHOLD_ERROR
    if state == "verified_clean":
        # 高 recovery 本身支持 clean：其他维度将 aggregate 推高到阈值
        # 之上并不与 clean 判定矛盾。
        if recovery is not None and recovery >= 0.75:
            return False
        return crs_signal.aggregate >= CRS_THRESHOLD_BORDERLINE
    return False


def _detect_evidence_incomplete(
    prosecution: CoverageReading, defense: CoverageReading, state: str
) -> bool:
    """Detect if the adjudication was one-sided or evidence-starved.

    Architectural principle: a court-style adjudication requires BOTH
    prosecution and defense to have adequate coverage. When either side
    is starved, the verdict is unreliable regardless of which side won.

    This replaces the old asymmetric logic (which only checked the
    "relevant" side per state) with a symmetric equilibrium check:
    - For verified_error: prosecution must be complete AND defense must
      have adequate coverage (otherwise it's a one-sided FP risk).
    - For verified_clean: defense must be complete AND prosecution must
      have adequate coverage (otherwise it's a one-sided FN risk).
    - For unresolved: either side incomplete explains the abstention.
    """
    # Check the "winning" side first — it must be complete.
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

    # Check the "losing" side — it must have had adequate opportunity
    # to rebut. A one-sided verdict is not reliable.
    if state == "verified_error":
        challenger = defense
    elif state == "verified_clean":
        challenger = prosecution
    else:
        # unresolved: both sides matter, check the weaker one
        challenger = defense if (prosecution.score or 0) >= (defense.score or 0) else prosecution

    # Challenger check: symmetric with winner check — any non-complete
    # state is insufficient. The old logic required BOTH state != "complete"
    # AND score < threshold, which let categorical "incomplete" (score 0.5)
    # slip through. Now: state must be "complete" (or score >= threshold
    # if quantitative).
    if challenger.state is not None:
        if challenger.state != "complete":
            return True
    elif not challenger.quantitative or challenger.score is None:
        return True
    elif challenger.score < COVERAGE_LOW_THRESHOLD:
        return True

    return False


def _detect_boundary_hesitation(
    prosecution: CoverageReading, defense: CoverageReading, state: str
) -> bool:
    """RETIRED (v5.4): always False.

    The v3.5 proof court emits only categorical coverage states
    (complete/incomplete/unresolved), never quantitative scores, so a
    score-gap "hesitation" check could never fire in production. The
    function is kept as a non-operational stub so that historical
    attribution records mentioning the trigger remain interpretable;
    it is no longer wired into the trigger cascade.
    """
    return False


def attribute_failure(
    row: Mapping[str, Any],
    *,
    use_gold: bool = False,
) -> AttributionSignal:
    """Perform causal process reflection on a single detection result.

    Self-supervised mode (use_gold=False):
        Uses only pipeline internal signals to determine if reflection
        should be triggered. Does NOT require gold labels.

    Gold-enhanced mode (use_gold=True, explicit opt-in):
        Additionally uses gold labels for more precise error classification.
        Gold label NEVER enters skill content — only used for signal routing.

    Trigger conditions (any one triggers reflection):
    1. state == "unresolved"
    2. CRS internal contradiction
    3. Evidence chain incomplete (coverage abnormally low)
    4. Boundary hesitation (prosecution ≈ defense)
    5. Gold mismatch (optional enhancement)

    NOT triggered:
    - High-confidence verified_error (all CRS dimensions consistently high)
    - High-confidence verified_clean (defense overwhelming)
    """
    state = resolve_authoritative_state(row)
    predicted = _prediction_state(row)
    prosecution, defense = _extract_coverage(row)
    prosecution_cov, defense_cov = prosecution.score, defense.score
    crs_signal = _extract_crs_scores(row)
    crs_scores = crs_signal.scores
    consensus_gate = bool(
        (row.get("proof_adjudication") or {}).get("consensus_gate_applied")
    )
    candidate_count = _count_candidates(row)

    # Gold is a strict opt-in. In self-supervised mode it is not read into
    # attribution, summaries, candidate routing, or localization checks.
    raw_has_gold = "gold_label" in row and row.get("gold_label") is not None
    has_gold = bool(use_gold and raw_has_gold)
    if has_gold and not isinstance(row["gold_label"], bool):
        raise ValueError("gold_label must be bool in gold-enhanced mode")
    gold: bool | None = bool(row["gold_label"]) if has_gold else None
    step_correct = _check_step_correct(row) if has_gold else False

    # ================================================================
    # Self-supervised trigger detection
    # ================================================================
    trigger_source = TriggerSource.NONE
    error_type = ErrorType.CORRECT

    # --- Adversarial Equilibrium Confidence Gate ---
    #
    # Architectural principle: a court-style adjudication is only
    # "high confidence" when BOTH prosecution and defense were
    # thoroughly tested. The old design checked only prosecution-side
    # signals (CRS, surviving error certificate), which made it blind
    # to false positives where the defense had no chance to rebut.
    #
    # The new gate requires:
    #   1. Strong prosecution evidence (CRS ≥ threshold + surviving cert)
    #   2. Adversarial equilibrium ≥ threshold (both sides tested)
    #
    # When equilibrium is low, the case is NOT high-confidence and
    # falls through to the trigger cascade, where it will be caught
    # by evidence_incomplete (one-sided adjudication).
    both_sides_tested = _adversarially_tested(prosecution, defense)

    is_high_confidence = False
    if state == "verified_error":
        prosecution_strong = (
            crs_signal.complete
            and crs_signal.projection_confidence >= 0.75
            and crs_signal.aggregate >= CRS_THRESHOLD_ERROR
            and _has_surviving_error_certificate(row)
        )
        if prosecution_strong and both_sides_tested:
            is_high_confidence = True
    elif state == "verified_clean":
        defense_strong = (
            crs_signal.complete
            and crs_signal.projection_confidence >= 0.75
            and crs_signal.aggregate < CRS_THRESHOLD_BORDERLINE
            and defense.state == "complete"
        )
        if defense_strong and both_sides_tested:
            is_high_confidence = True

    if is_high_confidence:
        # Check gold mismatch even for high-confidence (optional enhancement)
        if use_gold and has_gold and predicted is not None and predicted != gold:
            trigger_source = TriggerSource.GOLD_MISMATCH
            if predicted is True and gold is False:
                error_type = ErrorType.FALSE_POSITIVE
            else:
                error_type = ErrorType.FALSE_NEGATIVE_CLEAN
        else:
            # Truly stable success
            if use_gold and predicted is not None and has_gold and predicted == gold:
                if gold and not step_correct:
                    error_type = ErrorType.LOCALIZATION_ERROR
                    trigger_source = TriggerSource.GOLD_MISMATCH
                else:
                    error_type = ErrorType.CORRECT
            else:
                error_type = ErrorType.CORRECT  # high confidence → pass

    # --- Self-supervised triggers (if not already triggered by gold) ---
    if (
        not is_high_confidence
        and error_type == ErrorType.CORRECT
        and trigger_source == TriggerSource.NONE
    ):
        # Trigger 1: Unresolved
        if state == "unresolved":
            trigger_source = TriggerSource.UNRESOLVED
            if use_gold and has_gold:
                if gold is True:
                    error_type = ErrorType.UNRESOLVED_SHOULD_ERROR
                else:
                    error_type = ErrorType.UNRESOLVED_SHOULD_CLEAN
            else:
                error_type = ErrorType.UNRESOLVED_UNKNOWN

        # Trigger 2: gold polarity. An external label is stronger evidence
        # than any detector-internal process signal, so a gold-established
        # FP/FN must claim the error type before the self-supervised
        # triggers below can relabel the case as a mere process defect.
        elif use_gold and has_gold and predicted is not None and predicted != gold:
            trigger_source = TriggerSource.GOLD_MISMATCH
            if predicted is True and gold is False:
                error_type = ErrorType.FALSE_POSITIVE
            else:
                error_type = ErrorType.FALSE_NEGATIVE_CLEAN

        # Trigger 3: gold-confirmed verdict with a mislocalized origin.
        elif (
            use_gold
            and has_gold
            and predicted is not None
            and predicted == gold
            and gold is True
            and not step_correct
        ):
            trigger_source = TriggerSource.GOLD_MISMATCH
            error_type = ErrorType.LOCALIZATION_ERROR

        # Trigger 4: required diagnostic proof signals are unavailable.
        elif not crs_signal.complete:
            trigger_source = TriggerSource.SIGNAL_UNAVAILABLE
            error_type = ErrorType.SIGNAL_UNAVAILABLE

        # Trigger 5: CRS contradiction
        elif _detect_crs_contradiction(state, crs_signal):
            trigger_source = TriggerSource.CRS_CONTRADICTION
            error_type = ErrorType.CRS_CONTRADICTION

        # Trigger 6: Evidence incomplete
        elif _detect_evidence_incomplete(prosecution, defense, state):
            trigger_source = TriggerSource.EVIDENCE_INCOMPLETE
            error_type = ErrorType.EVIDENCE_INCOMPLETE

        # Boundary hesitation retired in v5.4: it required quantitative
        # coverage readings the v3.5 proof court never emits, so the
        # trigger was unreachable in production.

    # --- Additional: candidate miss detection ---
    if (
        has_gold
        and error_type != ErrorType.CORRECT
        and candidate_count == 0
        and gold is True
    ):
        error_type = ErrorType.CANDIDATE_MISS
        trigger_source = TriggerSource.EVIDENCE_INCOMPLETE

    # --- Refine unresolved with self-supervised sub-signals ---
    if error_type == ErrorType.UNRESOLVED_UNKNOWN:
        # Further attribute unresolved cases by internal signals
        if (
            prosecution_cov is not None
            and defense_cov is not None
            and prosecution_cov < COVERAGE_LOW_THRESHOLD
            and defense_cov < COVERAGE_LOW_THRESHOLD
        ):
            # Both weak → abstention rule too conservative
            error_type = ErrorType.UNRESOLVED_UNKNOWN  # keep, but target abstention
        elif prosecution_cov is not None and prosecution_cov < COVERAGE_LOW_THRESHOLD:
            pass  # prosecution weak → evidence_obligation (default)
        elif defense_cov is not None and defense_cov < COVERAGE_LOW_THRESHOLD:
            # Defense weak → clean_defense_boundary needs enrichment
            error_type = ErrorType.UNRESOLVED_UNKNOWN  # re-route below

    # ================================================================
    # Process diagnostics (v5.5)
    # ================================================================
    # The detector emits a label-free diagnostics block describing which
    # pipeline stage broke (focused-prosecution quarantine, appeal failure,
    # ambiguous localization). This is strictly a ROOT-CAUSE signal:
    #
    #   - Gold FP/FN and gold localization polarity always win. Diagnostics
    #     may only refine the partner module on those cases.
    #   - A gold-confirmed correct verdict is never turned into a failure by
    #     a process irregularity that did not change the outcome.
    #   - In self-supervised mode, diagnostics refine a generic outcome into
    #     the concrete stage that failed, so mutation targets the right
    #     module instead of defaulting to evidence_obligation.
    diagnostics = _process_diagnostics(row)
    quarantined_count = _diagnostic_int(
        diagnostics, "focused_quarantined_candidate_count"
    )
    diagnostic_finding = _classify_process_diagnostics(diagnostics)
    process_diagnostic_applied = False

    # Gold localization polarity beats the localization process diagnostic:
    # if gold confirms the responsible step is right, an ambiguous tie-break
    # did not actually mislocalize, so earliest_origin_tiebreak is not the
    # module to blame.
    if (
        diagnostic_finding is not None
        and diagnostic_finding[0] == ErrorType.LOCALIZATION_AMBIGUOUS
        and has_gold
        and step_correct
    ):
        diagnostic_finding = None

    # Gold-verified polarity precedence: when gold matches the prediction and
    # localization is right, the case is CORRECT. Self-supervised cascade
    # reclassifications (SIGNAL_UNAVAILABLE, CRS_CONTRADICTION, ...) are
    # diagnostic artifacts and must not supersede a gold-confirmed outcome.
    gold_verified_correct = (
        has_gold
        and predicted is not None
        and predicted == gold
        and (not gold or step_correct)
    )
    if gold_verified_correct:
        error_type = ErrorType.CORRECT
        trigger_source = TriggerSource.NONE

    gold_polarity_locked = has_gold and (
        error_type in _GOLD_POLARITY_ERROR_TYPES or gold_verified_correct
    )

    if (
        diagnostic_finding is not None
        and not gold_polarity_locked
        and error_type in _REFINABLE_ERROR_TYPES
    ):
        error_type, trigger_source = diagnostic_finding
        process_diagnostic_applied = True

    # ================================================================
    # Module attribution
    # ================================================================
    target_module = ERROR_MODULE_MAP.get(error_type, "evidence_obligation")
    secondary_module = ERROR_MODULE_SECONDARY.get(error_type, "abstention_rule")

    # Gold keeps polarity AND its primary module (an FP is a defense failure,
    # not a localization failure). Diagnostics only supply the partner module
    # so the repair prompt names both the gold failure and the process defect.
    if (
        gold_polarity_locked
        and not gold_verified_correct
        and diagnostic_finding is not None
    ):
        diagnostic_module = ERROR_MODULE_MAP.get(diagnostic_finding[0])
        if diagnostic_module is not None and diagnostic_module != target_module:
            secondary_module = diagnostic_module
            process_diagnostic_applied = True

    if error_type in {
        ErrorType.UNRESOLVED_UNKNOWN,
        ErrorType.UNRESOLVED_SHOULD_ERROR,
        ErrorType.UNRESOLVED_SHOULD_CLEAN,
        ErrorType.EVIDENCE_INCOMPLETE,
        ErrorType.CANDIDATE_MISS,
        ErrorType.SIGNAL_UNAVAILABLE,
    }:
        target_module, secondary_module = _route_process_failure(
            row,
            state=state,
            candidate_count=candidate_count,
            prosecution=prosecution,
            defense=defense,
            default_module=target_module,
            default_secondary=secondary_module,
        )

    # Special routing for unresolved with weak defense
    if error_type == ErrorType.UNRESOLVED_UNKNOWN:
        if (
            defense_cov is not None
            and prosecution_cov is not None
            and defense_cov < prosecution_cov
            and defense_cov < COVERAGE_LOW_THRESHOLD
        ):
            target_module = "clean_defense_boundary"
            secondary_module = "abstention_rule"

    # ================================================================
    # Reflection type
    # ================================================================
    if error_type == ErrorType.CORRECT:
        reflection_type = ReflectionType.STABLE_SUCCESS
    elif error_type == ErrorType.FALSE_POSITIVE:
        reflection_type = ReflectionType.SKILL_DEFECT
    elif error_type in (
        ErrorType.UNRESOLVED_SHOULD_ERROR,
        ErrorType.UNRESOLVED_SHOULD_CLEAN,
        ErrorType.UNRESOLVED_UNKNOWN,
        ErrorType.CANDIDATE_MISS,
    ):
        reflection_type = ReflectionType.DISCOVERY
    elif error_type == ErrorType.LOCALIZATION_ERROR:
        reflection_type = ReflectionType.OPTIMIZATION
    elif error_type in (
        ErrorType.CRS_CONTRADICTION,
        ErrorType.EVIDENCE_INCOMPLETE,
        ErrorType.BOUNDARY_HESITATION,
        ErrorType.SIGNAL_UNAVAILABLE,
    ):
        reflection_type = ReflectionType.OPTIMIZATION
    elif error_type in (
        ErrorType.FOCUSED_CERTIFICATE_QUARANTINE,
        ErrorType.APPEAL_PROSECUTOR_FAILURE,
        ErrorType.APPEAL_DEFENDER_FAILURE,
        ErrorType.APPEAL_COVERAGE_MISMATCH,
    ):
        # A named stage failed to produce the output its contract requires:
        # the skill text for that stage is deficient, not merely suboptimal.
        reflection_type = ReflectionType.SKILL_DEFECT
    else:
        reflection_type = ReflectionType.OPTIMIZATION

    # ================================================================
    # Evidence summary
    # ================================================================
    def coverage_text(value: float | None) -> str:
        return "unavailable" if value is None else f"{value:.2f}"

    evidence_parts = [
        f"state={state}",
        f"trigger={trigger_source.value}",
        f"prosecution_coverage={coverage_text(prosecution_cov)}",
        f"defense_coverage={coverage_text(defense_cov)}",
        f"prosecution_coverage_state={prosecution.state or 'unavailable'}",
        f"defense_coverage_state={defense.state or 'unavailable'}",
        f"candidates={candidate_count}",
        f"consensus_gate={'applied' if consensus_gate else 'not_applied'}",
    ]
    if crs_scores:
        crs_str = ",".join(f"{k}={v:.2f}" for k, v in crs_scores.items())
        evidence_parts.append(f"crs=[{crs_str}]")
        evidence_parts.append(f"crs_aggregate={crs_signal.aggregate:.3f}")
        evidence_parts.append(f"crs_source={crs_signal.source}")
    if has_gold:
        evidence_parts.append(f"gold={'positive' if gold else 'negative'}")
    else:
        evidence_parts.append("supervision=self_supervised")
    if state == "unresolved":
        evidence_parts.append("detector_abstained_due_to_insufficient_evidence")
    if error_type == ErrorType.FALSE_POSITIVE:
        evidence_parts.append("defense_boundary_insufficient_to_protect_clean_case")
    if error_type == ErrorType.LOCALIZATION_ERROR:
        gold_steps = row.get("gold_steps") or []
        evidence_parts.append(
            f"step_mismatch:predicted={row.get('responsible_step')}_gold={gold_steps}"
        )
    if error_type == ErrorType.CRS_CONTRADICTION:
        evidence_parts.append("crs_dimensions_contradict_final_decision")
    if process_diagnostic_applied:
        evidence_parts.append("process_diagnostic=applied")
    localization_status = str(
        diagnostics.get("localization_status") or "not_applicable"
    )
    appeal_status = str(diagnostics.get("appeal_status") or "not_required")
    if localization_status != "not_applicable":
        evidence_parts.append(f"localization_status={localization_status}")
    if appeal_status != "not_required":
        evidence_parts.append(f"appeal_status={appeal_status}")
    if quarantined_count:
        evidence_parts.append(
            f"focused_quarantined_candidates={quarantined_count}"
        )

    # Attribution confidence (v5.3): high for definite error types,
    # medium for CRS/boundary signals, low for unresolved/unavailable.
    # Attribution confidence (v5.3): high for definite error types,
    # medium for gold-directed unresolved and CRS/boundary signals,
    # low for unresolved_unknown and unavailable signals.
    if error_type in (ErrorType.FALSE_POSITIVE, ErrorType.FALSE_NEGATIVE_UNRESOLVED,
                      ErrorType.FALSE_NEGATIVE_CLEAN, ErrorType.LOCALIZATION_ERROR):
        attribution_confidence = "high"
    elif error_type in (ErrorType.FOCUSED_CERTIFICATE_QUARANTINE,
                        ErrorType.APPEAL_PROSECUTOR_FAILURE,
                        ErrorType.APPEAL_DEFENDER_FAILURE,
                        ErrorType.APPEAL_COVERAGE_MISMATCH,
                        ErrorType.LOCALIZATION_AMBIGUOUS):
        # The detector reported exactly which stage broke; the root-cause
        # module follows from the pipeline structure, not from inference.
        attribution_confidence = "high"
    elif error_type in (ErrorType.UNRESOLVED_SHOULD_ERROR,
                        ErrorType.UNRESOLVED_SHOULD_CLEAN,
                        ErrorType.CRS_CONTRADICTION,
                        ErrorType.BOUNDARY_HESITATION):
        # Gold direction is known -> attribution is more reliable
        attribution_confidence = "medium"
    else:
        attribution_confidence = "low"

    return AttributionSignal(
        error_type=error_type,
        target_module=target_module,
        secondary_module=secondary_module,
        attribution_confidence=attribution_confidence,
        reflection_type=reflection_type,
        trigger_source=trigger_source,
        evidence_summary=" | ".join(evidence_parts),
        prosecution_coverage=prosecution_cov,
        defense_coverage=defense_cov,
        prosecution_coverage_state=prosecution.state,
        defense_coverage_state=defense.state,
        coverage_quantitative=prosecution.quantitative and defense.quantitative,
        crs_scores=crs_scores,
        crs_aggregate=crs_signal.aggregate,
        crs_source=crs_signal.source,
        crs_complete=crs_signal.complete,
        consensus_gate_applied=consensus_gate,
        candidate_count=candidate_count,
        state=state,
        gold_label=gold,
        has_gold=has_gold,
        gold_enhanced=has_gold,
        responsible_step_correct=step_correct,
        localization_status=str(
            diagnostics.get("localization_status") or "not_applicable"
        ),
        appeal_status=str(diagnostics.get("appeal_status") or "not_required"),
        focused_quarantined_candidate_count=quarantined_count,
        process_diagnostic_applied=process_diagnostic_applied,
        runtime_valid=row.get("runtime_valid") is not False,
    )


# ============================================================
# LLM-enhanced attribution (v5.3)
# ============================================================

def build_attribution_llm_prompt(
    signal: AttributionSignal,
) -> tuple[str, str]:
    """Build the LLM prompt for enhanced attribution when confidence is low.

    Returns (system_prompt, user_prompt) tuple. The LLM analyzes the error
    type, state combination, and module responsibilities to identify the
    most likely root-cause module.
    """
    system = """You are the BCCSE v5 attribution engine. Your task is to identify
which detection module is most likely the ROOT CAUSE of the failure.

Available modules and their responsibilities:
1. candidate_trigger - Creates candidates at the earliest observable origin of unsupported claims/actions
2. evidence_obligation - Requires cited evidence for each causal-chain field (norm/breach/control/impact/counterfactual)
3. candidate_to_verifier_route - Routes candidates to the matching verifier (claim/action/omission/adoption)
4. atomic_verifier_instruction - Verifies each candidate independently against cited trajectory events
5. clean_defense_boundary - Supports clean when tool output supports claim, failure is repaired, or deviation is authorized
6. causal_link_test - Tests whether correcting the origin would change the outcome (causal sufficiency)
7. earliest_origin_tiebreak - Selects the earliest causally sufficient origin among multiple candidates
8. abstention_rule - Returns unresolved when neither a complete error proof nor a complete clean defense is established

Key principles:
- candidate_trigger and route are about CANDIDATE CREATION and ROUTING, not final verdict
- evidence_obligation and verifier are about PROOF COMPLETENESS, not final verdict
- clean_defense_boundary is about DEFENSE FORMATION, not final verdict
- abstention_rule is the FINAL VERDICT boundary
- When defense is complete but output is unresolved, the root cause is almost always abstention_rule
- When prosecution is complete but defense is missing, the root cause is clean_defense_boundary
- When candidates exist but no prosecution rows are produced, the root cause is candidate_to_verifier_route
- When candidates don't exist, the root cause is candidate_trigger

Return exactly one JSON object and no other text:
{"primary_module": "<module_id>", "secondary_module": "<module_id>", "confidence": "high|medium|low", "rationale": "<one sentence>"}"""

    user = f"""Detection state: {signal.state}
Error type: {signal.error_type.value}
Prosecution coverage: {signal.prosecution_coverage} ({signal.prosecution_coverage_state})
Defense coverage: {signal.defense_coverage} ({signal.defense_coverage_state})
Candidate count: {signal.candidate_count}
CRS aggregate: {signal.crs_aggregate:.3f} ({signal.crs_source})
Gold label: {signal.gold_label}

Current rule-based attribution: primary={signal.target_module}, secondary={signal.secondary_module}

Analyze the detection state and error type. Which module is most likely
the root cause of this failure? Consider the module's responsibility and
whether the current state is consistent with that module failing.
If defense is already complete but output is unresolved, the root cause
is abstention_rule, not the prosecution path.
"""

    return system, user


def parse_attribution_llm_response(
    response_text: str,
    fallback_primary: str,
    fallback_secondary: str,
) -> tuple[str, str, str]:
    """Parse LLM attribution response. Returns (primary, secondary, confidence).

    Falls back to rule-based attribution if parsing fails.
    """
    import json
    import re

    # Try to extract JSON from the response
    json_match = re.search(r"\{[^{}]*\}", response_text)
    if not json_match:
        return fallback_primary, fallback_secondary, "low"

    try:
        data = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        return fallback_primary, fallback_secondary, "low"

    primary = str(data.get("primary_module", ""))
    secondary = str(data.get("secondary_module", ""))
    confidence = str(data.get("confidence", "low")).lower()

    # Validate module IDs
    valid_modules = set(MODULE_IDS)
    if primary not in valid_modules:
        return fallback_primary, fallback_secondary, "low"
    if secondary not in valid_modules:
        secondary = fallback_secondary
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    return primary, secondary, confidence
