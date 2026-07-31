"""BCCSE v5 — Step-level causal process reflection (self-supervised).

Core novelty of BCCSE v5: the detection pipeline's own causal attribution
capability is recursively applied to diagnose skill deficiencies.

Self-supervised trigger signals (NO gold label required):
- Unresolved: system admits "current skill insufficient to judge"
- CRS internal contradiction: dimension scores conflict with final decision
- Evidence chain incomplete: prosecution/defense coverage abnormally low
- Boundary hesitation: prosecution and defense strengths are close

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
    CRSSignal,
    CoverageReading,
    extract_coverage,
    extract_crs_signal,
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
    BOUNDARY_HESITATION = "boundary_hesitation"  # self-supervised
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
    BOUNDARY_HESITATION = "boundary_hesitation"
    SIGNAL_UNAVAILABLE = "signal_unavailable"
    GOLD_MISMATCH = "gold_mismatch"  # optional enhancement
    NONE = "none"  # no trigger (stable success)


# Mapping from error type to primary target module
ERROR_MODULE_MAP: dict[ErrorType, str] = {
    ErrorType.FALSE_POSITIVE: "clean_defense_boundary",
    ErrorType.FALSE_NEGATIVE_UNRESOLVED: "evidence_obligation",
    ErrorType.FALSE_NEGATIVE_CLEAN: "evidence_obligation",
    ErrorType.UNRESOLVED_SHOULD_ERROR: "evidence_obligation",
    # UNRESOLVED_SHOULD_CLEAN: defense already complete but output unresolved.
    # Root cause is abstention_rule, not evidence_obligation.
    ErrorType.UNRESOLVED_SHOULD_CLEAN: "abstention_rule",
    ErrorType.UNRESOLVED_UNKNOWN: "evidence_obligation",  # default for self-supervised
    ErrorType.LOCALIZATION_ERROR: "causal_link_test",
    ErrorType.CANDIDATE_MISS: "candidate_trigger",
    ErrorType.CRS_CONTRADICTION: "evidence_obligation",
    ErrorType.EVIDENCE_INCOMPLETE: "evidence_obligation",
    ErrorType.BOUNDARY_HESITATION: "clean_defense_boundary",
    ErrorType.SIGNAL_UNAVAILABLE: "evidence_obligation",
}

# Secondary module (alternative attribution)
ERROR_MODULE_SECONDARY: dict[ErrorType, str] = {
    ErrorType.FALSE_POSITIVE: "evidence_obligation",
    ErrorType.FALSE_NEGATIVE_UNRESOLVED: "abstention_rule",
    ErrorType.FALSE_NEGATIVE_CLEAN: "clean_defense_boundary",
    ErrorType.UNRESOLVED_SHOULD_ERROR: "abstention_rule",
    # UNRESOLVED_SHOULD_CLEAN: secondary is clean_defense_boundary
    # (defense is already complete, verify it's correctly formed)
    ErrorType.UNRESOLVED_SHOULD_CLEAN: "clean_defense_boundary",
    ErrorType.UNRESOLVED_UNKNOWN: "abstention_rule",
    ErrorType.LOCALIZATION_ERROR: "earliest_origin_tiebreak",
    ErrorType.CANDIDATE_MISS: "candidate_to_verifier_route",
    ErrorType.CRS_CONTRADICTION: "atomic_verifier_instruction",
    ErrorType.EVIDENCE_INCOMPLETE: "atomic_verifier_instruction",
    ErrorType.BOUNDARY_HESITATION: "abstention_rule",
    ErrorType.SIGNAL_UNAVAILABLE: "atomic_verifier_instruction",
}

# ============================================================
# Self-supervised trigger thresholds
# ============================================================

# Coverage below this is considered "abnormally low"
COVERAGE_LOW_THRESHOLD = 0.3
# Prosecution-defense gap below this is "hesitation"
BOUNDARY_HESITATION_GAP = 0.15
# Frozen PACA CRS decision thresholds used only for diagnostic consistency.
CRS_THRESHOLD_BORDERLINE = 0.28
CRS_THRESHOLD_ERROR = 0.45


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

    @property
    def should_evolve(self) -> bool:
        """Whether this case provides an evolution signal (triggers reflection)."""
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
            "should_evolve": self.should_evolve,
        }


def _prediction_state(row: Mapping[str, Any]) -> bool | None:
    """Extract predicted polarity from state string."""
    state = str(row.get("state") or "")
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None  # unresolved


def _check_step_correct(row: Mapping[str, Any]) -> bool:
    """Check if responsible step localization is correct."""
    gold_steps = tuple(str(s) for s in row.get("gold_steps") or ())
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
    if state == "verified_error":
        return crs_signal.aggregate < CRS_THRESHOLD_ERROR
    if state == "verified_clean":
        return crs_signal.aggregate >= CRS_THRESHOLD_BORDERLINE
    return False


def _detect_evidence_incomplete(
    prosecution: CoverageReading, defense: CoverageReading, state: str
) -> bool:
    """Detect if evidence chain is abnormally incomplete.

    Triggers when relevant coverage is below threshold:
    - For unresolved/verified_error: prosecution coverage too low
    - For verified_clean: defense coverage too low
    """
    relevant = prosecution if state in ("unresolved", "verified_error") else defense
    if relevant.state is not None:
        return relevant.state != "complete"
    if not relevant.quantitative or relevant.score is None:
        return True
    # Both sides very low → system has almost no evidence
    return bool(
        relevant.quantitative
        and relevant.score is not None
        and relevant.score < COVERAGE_LOW_THRESHOLD
    )


def _detect_boundary_hesitation(
    prosecution: CoverageReading, defense: CoverageReading, state: str
) -> bool:
    """Detect if prosecution and defense are too close (boundary unclear).

    Only relevant when a decision WAS made (not unresolved).
    If both sides have reasonable coverage but the gap is tiny,
    the decision boundary is not well-defined.
    """
    if state == "unresolved":
        return False  # unresolved is already handled separately
    if not (prosecution.quantitative and defense.quantitative):
        return False
    if prosecution.score is None or defense.score is None:
        return False
    # Both sides must have some substance for this to be meaningful
    if prosecution.score < 0.1 or defense.score < 0.1:
        return False
    gap = abs(prosecution.score - defense.score)
    return gap < BOUNDARY_HESITATION_GAP


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
    state = str(row.get("state") or "")
    if state not in {"verified_error", "verified_clean", "unresolved"}:
        raise ValueError(f"Unsupported detection state for attribution: {state!r}")
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

    # --- Check high-confidence pass (NOT triggered) ---
    is_high_confidence = False
    if state == "verified_error":
        if (
            crs_signal.complete
            and crs_signal.aggregate >= CRS_THRESHOLD_ERROR
            and _has_surviving_error_certificate(row)
        ):
            is_high_confidence = True
    elif state == "verified_clean":
        if (
            crs_signal.complete
            and crs_signal.aggregate < CRS_THRESHOLD_BORDERLINE
            and defense.state == "complete"
        ):
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

        # Trigger 2: required diagnostic proof signals are unavailable.
        elif not crs_signal.complete:
            trigger_source = TriggerSource.SIGNAL_UNAVAILABLE
            error_type = ErrorType.SIGNAL_UNAVAILABLE

        # Trigger 3: CRS contradiction
        elif _detect_crs_contradiction(state, crs_signal):
            trigger_source = TriggerSource.CRS_CONTRADICTION
            error_type = ErrorType.CRS_CONTRADICTION

        # Trigger 4: Evidence incomplete
        elif _detect_evidence_incomplete(prosecution, defense, state):
            trigger_source = TriggerSource.EVIDENCE_INCOMPLETE
            error_type = ErrorType.EVIDENCE_INCOMPLETE

        # Trigger 5: Boundary hesitation
        elif _detect_boundary_hesitation(prosecution, defense, state):
            trigger_source = TriggerSource.BOUNDARY_HESITATION
            error_type = ErrorType.BOUNDARY_HESITATION

        # Trigger 6: Gold mismatch (optional, lowest priority)
        elif use_gold and has_gold and predicted is not None and predicted != gold:
            trigger_source = TriggerSource.GOLD_MISMATCH
            if predicted is True and gold is False:
                error_type = ErrorType.FALSE_POSITIVE
            elif predicted is False and gold is True:
                error_type = ErrorType.FALSE_NEGATIVE_CLEAN
            else:
                error_type = ErrorType.CORRECT

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
    # Module attribution
    # ================================================================
    target_module = ERROR_MODULE_MAP.get(error_type, "evidence_obligation")
    secondary_module = ERROR_MODULE_SECONDARY.get(error_type, "abstention_rule")

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
    if error_type == ErrorType.BOUNDARY_HESITATION:
        evidence_parts.append(
            f"prosecution_defense_gap={abs(prosecution_cov - defense_cov):.3f}"
        )

    # Attribution confidence (v5.3): high for definite error types,
    # medium for CRS/boundary signals, low for unresolved/unavailable.
    # Attribution confidence (v5.3): high for definite error types,
    # medium for gold-directed unresolved and CRS/boundary signals,
    # low for unresolved_unknown and unavailable signals.
    if error_type in (ErrorType.FALSE_POSITIVE, ErrorType.FALSE_NEGATIVE_UNRESOLVED,
                      ErrorType.FALSE_NEGATIVE_CLEAN, ErrorType.LOCALIZATION_ERROR):
        attribution_confidence = "high"
    elif error_type in (ErrorType.UNRESOLVED_SHOULD_ERROR,
                        ErrorType.UNRESOLVED_SHOULD_CLEAN,
                        ErrorType.CRS_CONTRADICTION,
                        ErrorType.BOUNDARY_HESITATION):
        # Gold direction is known → attribution is more reliable
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
