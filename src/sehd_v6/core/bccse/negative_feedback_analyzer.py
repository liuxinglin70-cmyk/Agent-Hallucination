"""SEHD v6.1 — Deep negative-feedback analysis for L1 reflection.

Turns the legacy "do not repeat these" rejected-edit list into structured,
actionable insights. The analyzer answers: *why was this rejected, what
pattern does the rejection reveal, and how should the next proposal avoid
the same trap* — without ever exposing case identity or gold labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Avoid circular import: RejectedEditEntry is defined in reflection_engine,
# which imports this module. We use Any for type hints and import lazily
# inside functions.
RejectedEditEntry = Any


@dataclass(frozen=True)
class NegativeFeedbackInsight:
    """One structured insight extracted from a rejected edit."""

    rejection_category: str
    root_cause: str
    what_went_wrong: str
    how_to_avoid: str
    severity: str  # "critical" | "moderate" | "minor"
    pattern_signature: str  # closed-vocabulary pattern tag


@dataclass(frozen=True)
class NegativeFeedbackAnalysis:
    """Aggregated negative-feedback insights for the current reflection."""

    insights: tuple[NegativeFeedbackInsight, ...] = ()
    dominant_rejection_stage: str = ""
    most_common_root_cause: str = ""
    avoidance_priorities: tuple[str, ...] = ()


# Root-cause taxonomy (closed vocabulary). Each rejected edit maps to exactly
# one root cause; the prompt uses these to tell the model *what to avoid*.
ROOT_CAUSE_SCHEMA_MISMATCH = "schema_mismatch"
ROOT_CAUSE_UNOWNED_TARGET = "unowned_target"
ROOT_CAUSE_CONDITION_OVERLOAD = "condition_overload"
ROOT_CAUSE_NO_OBSERVABLE_EFFECT = "no_observable_effect"
ROOT_CAUSE_GOLD_LEAKAGE = "gold_leakage"
ROOT_CAUSE_DUPLICATE_RULE = "duplicate_rule"
ROOT_CAUSE_BASELINE_VIOLATION = "baseline_violation"
ROOT_CAUSE_UNKNOWN = "unknown"

_ROOT_CAUSES = frozenset({
    ROOT_CAUSE_SCHEMA_MISMATCH,
    ROOT_CAUSE_UNOWNED_TARGET,
    ROOT_CAUSE_CONDITION_OVERLOAD,
    ROOT_CAUSE_NO_OBSERVABLE_EFFECT,
    ROOT_CAUSE_GOLD_LEAKAGE,
    ROOT_CAUSE_DUPLICATE_RULE,
    ROOT_CAUSE_BASELINE_VIOLATION,
    ROOT_CAUSE_UNKNOWN,
})


def _classify_root_cause(rejected: RejectedEditEntry) -> str:
    """Map one rejected edit to its root cause (closed vocabulary).

    Uses exact prefix matching against known gate rejection codes, not
    substring containment, so a reason like "invalid_schema" never matches
    "invalid_parameter" by accident.
    """
    reasons = [str(r).lower() for r in rejected.reject_reasons]
    observed = rejected.observed_effect.lower()

    # Check for exact prefix matches first (most specific wins)
    for reason in reasons:
        if reason.startswith("gold_leak") or reason.startswith("identity_leak"):
            return ROOT_CAUSE_GOLD_LEAKAGE
        if reason.startswith("immutable_baseline") or reason.startswith("protected_baseline"):
            return ROOT_CAUSE_BASELINE_VIOLATION
        if reason.startswith("semantic_overlap") or reason.startswith("duplicate"):
            return ROOT_CAUSE_DUPLICATE_RULE
        if reason.startswith("condition_overload"):
            return ROOT_CAUSE_CONDITION_OVERLOAD
        if reason.startswith("ownership_mismatch") or reason.startswith("unowned"):
            return ROOT_CAUSE_UNOWNED_TARGET
        if reason.startswith("invalid_schema") or reason.startswith("rule_spec"):
            return ROOT_CAUSE_SCHEMA_MISMATCH
        if reason.startswith("no_effect") or reason.startswith("no_observable"):
            return ROOT_CAUSE_NO_OBSERVABLE_EFFECT

    # Fall back to substring checks for legacy free-form reasons
    reasons_text = " ".join(reasons)
    if "gold" in reasons_text or "identity" in reasons_text or "leak" in reasons_text:
        return ROOT_CAUSE_GOLD_LEAKAGE
    if "baseline" in reasons_text or "immutable" in reasons_text:
        return ROOT_CAUSE_BASELINE_VIOLATION
    if "duplicate" in reasons_text or "overlap" in reasons_text:
        return ROOT_CAUSE_DUPLICATE_RULE
    if "overload" in reasons_text or "conjunction" in reasons_text:
        return ROOT_CAUSE_CONDITION_OVERLOAD
    if "unowned" in reasons_text or "ownership" in reasons_text or "forbidden" in reasons_text:
        return ROOT_CAUSE_UNOWNED_TARGET
    if "schema" in reasons_text or "invalid" in reasons_text:
        return ROOT_CAUSE_SCHEMA_MISMATCH
    if "no_effect" in observed or "no_observable" in observed:
        return ROOT_CAUSE_NO_OBSERVABLE_EFFECT
    return ROOT_CAUSE_UNKNOWN


def _severity_for(rejected: RejectedEditEntry) -> str:
    """Assign severity based on rejection stage and budget consumed."""
    if rejected.rejection_stage == "structural_gate":
        return "critical"
    if rejected.rejection_stage == "source_replay":
        return "moderate"
    if rejected.budget_consumed > 2:
        return "moderate"
    return "minor"


def _avoidance_strategy(root_cause: str) -> str:
    """Closed-vocabulary avoidance strategy for each root cause."""
    strategies = {
        ROOT_CAUSE_SCHEMA_MISMATCH: (
            "Ensure the rule follows the exact slot contract: one condition "
            "clause, one closed-opcode action, and valid evidence anchor."
        ),
        ROOT_CAUSE_UNOWNED_TARGET: (
            "Target only surfaces owned by the selected module; check the "
            "module charter before naming an output field."
        ),
        ROOT_CAUSE_CONDITION_OVERLOAD: (
            "Use at most one conjunction in the condition; prefer one "
            "decisive observable pattern over compound enumeration."
        ),
        ROOT_CAUSE_NO_OBSERVABLE_EFFECT: (
            "Predict an effect that source replay can measure; avoid "
            "abstract actions that do not change provider output fields."
        ),
        ROOT_CAUSE_GOLD_LEAKAGE: (
            "Never include gold labels, case IDs, or dataset identity in "
            "rule content; keep conditions trajectory-observable only."
        ),
        ROOT_CAUSE_DUPLICATE_RULE: (
            "Check the current skill genome for semantically overlapping "
            "rules; add a distinct discriminator, not a restatement."
        ),
        ROOT_CAUSE_BASELINE_VIOLATION: (
            "Do not modify or remove immutable baseline rules; only "
            "explicitly evolvable rules may be changed."
        ),
        ROOT_CAUSE_UNKNOWN: (
            "Review the structural gate contract and ensure the edit "
            "matches the module's executable action specs."
        ),
    }
    return strategies.get(root_cause, strategies[ROOT_CAUSE_UNKNOWN])


def analyze_negative_feedback(
    rejected_buffer: Sequence[RejectedEditEntry],
) -> NegativeFeedbackAnalysis:
    """Analyze a buffer of rejected edits into structured insights.

    The analysis is deterministic: same buffer always yields the same
    insights. No LLM, no heuristics.
    """
    if not rejected_buffer:
        return NegativeFeedbackAnalysis()

    insights: list[NegativeFeedbackInsight] = []
    stage_counts: dict[str, int] = {}
    cause_counts: dict[str, int] = {}

    for rejected in rejected_buffer:
        root_cause = _classify_root_cause(rejected)
        severity = _severity_for(rejected)
        stage = rejected.rejection_stage

        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        cause_counts[root_cause] = cause_counts.get(root_cause, 0) + 1

        # Pattern signature: closed-vocabulary tag for prompt use
        pattern = f"{stage}:{root_cause}"

        insights.append(NegativeFeedbackInsight(
            rejection_category=stage,
            root_cause=root_cause,
            what_went_wrong=(
                f"rejected at {stage}: {'; '.join(rejected.reject_reasons[:2])}"
            ),
            how_to_avoid=_avoidance_strategy(root_cause),
            severity=severity,
            pattern_signature=pattern,
        ))

    dominant_stage = max(stage_counts, key=stage_counts.get, default="")
    most_common_cause = max(cause_counts, key=cause_counts.get, default="")

    # Avoidance priorities: ordered by severity and frequency
    priority_order = {"critical": 0, "moderate": 1, "minor": 2}
    unique_causes = sorted(
        cause_counts.items(),
        key=lambda x: (priority_order.get(
            _severity_for(next(r for r in rejected_buffer if _classify_root_cause(r) == x[0])),
            3
        ), -x[1]),
    )
    avoidance_priorities = tuple(
        _avoidance_strategy(cause) for cause, _ in unique_causes[:3]
    )

    return NegativeFeedbackAnalysis(
        insights=tuple(insights),
        dominant_rejection_stage=dominant_stage,
        most_common_root_cause=most_common_cause,
        avoidance_priorities=avoidance_priorities,
    )


def format_negative_feedback_for_prompt(
    analysis: NegativeFeedbackAnalysis,
    *,
    max_chars: int = 1500,
) -> str:
    """Format negative-feedback insights for the reflection prompt.

    Bounded, canonical, and actionable. The model sees *what to avoid and
    why*, not *which case produced the rejection*.
    """
    if not analysis.insights:
        return ""

    lines: list[str] = ["<negative_feedback_analysis>"]
    lines.append(f"  <dominant_rejection_stage>{analysis.dominant_rejection_stage}</dominant_rejection_stage>")
    lines.append(f"  <most_common_root_cause>{analysis.most_common_root_cause}</most_common_root_cause>")

    if analysis.avoidance_priorities:
        lines.append("  <avoidance_priorities>")
        for index, strategy in enumerate(analysis.avoidance_priorities[:3]):
            lines.append(f"    <priority index=\"{index}\">{strategy}</priority>")
        lines.append("  </avoidance_priorities>")

    # Show only the most severe insights
    critical = [i for i in analysis.insights if i.severity == "critical"]
    moderate = [i for i in analysis.insights if i.severity == "moderate"]

    for insight in (critical + moderate)[:3]:
        lines.append(f"  <insight severity=\"{insight.severity}\">")
        lines.append(f"    <root_cause>{insight.root_cause}</root_cause>")
        lines.append(f"    <how_to_avoid>{insight.how_to_avoid}</how_to_avoid>")
        lines.append(f"  </insight>")

    lines.append("</negative_feedback_analysis>")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 30] + "\n... (truncated)\n</negative_feedback_analysis>"
    return text
