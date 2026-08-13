"""Structured experience retrieval + negative-feedback insight for planners.

Two prompt-safe input constructors for the L1/L2 planners (plan §2.6):
``retrieve_structured_experience`` answers *what worked, what failed, and
why — for failures structurally similar to the current one* and
``analyze_negative_feedback`` turns the rejected-edit buffer into structured
avoidance guidance. Both are deterministic and identity-free.

Retrieval reads the mechanism index owned by ``experience.memory`` — the
single Experience Truth — and never stores a copy of it. The exact-signature
pass delegates to ``ExperienceMemory.retrieve``; the shape/context fallback
scans ``ExperienceMemory.entries`` with the same weighted similarity the old
retriever used, reusing the memory's ``_refs_for_entry`` projection so the
prompt-facing reference shape stays single-sourced in the experience layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Mapping, Sequence

from sehd_rise.causal.episode import (
    CausalFailureEpisode,
    CausalSlice,
    HistoricalEpisodeReference,
)
from sehd_rise.experience.memory import (
    NO_EFFECT_REASONS,
    ExperienceMemory,
    MechanismEntry,
    episode_signature,
)


@dataclass(frozen=True)
class FailurePattern:
    """Structured failure pattern extracted from one episode."""

    mechanism_signature: tuple[str, ...]
    failure_shape: str  # "missing_field" | "malformed_output" | "wrong_routing" | "coverage_gap"
    causal_position: str  # "producer" | "consumer" | "propagation"
    impact_scope: str  # "single_candidate" | "side_wide" | "terminal"
    context_features: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperienceMatch:
    """One matched historical experience with its outcome."""

    pattern: FailurePattern
    similarity_score: float
    successful_interventions: tuple[dict[str, Any], ...] = ()
    failed_interventions: tuple[dict[str, Any], ...] = ()
    negative_feedback_insights: tuple[str, ...] = ()
    retrieval_path: str = "exact"  # "exact" | "shape_context" | "causal"


def _pattern_from_slice(
    slice_payload: CausalSlice,
    mechanism_signature: tuple[str, ...],
) -> FailurePattern:
    """Derive a FailurePattern from one causal slice (deterministic)."""
    missing = set(slice_payload.missing_fields)
    malformed = set(slice_payload.malformed_fields)
    if missing and malformed:
        shape = "missing_and_malformed"
    elif missing:
        shape = "missing_field"
    elif malformed:
        shape = "malformed_output"
    else:
        shape = "unknown"

    consumer = str(slice_payload.consumer or "")
    if "candidate" in consumer:
        position = "producer"
    elif "certificate" in consumer or "coverage" in consumer:
        position = "consumer"
    else:
        position = "propagation"

    terminal_effect = str(slice_payload.court_effect or "")
    if "terminal" in terminal_effect or "verdict" in terminal_effect:
        scope = "terminal"
    elif "coverage" in terminal_effect or "side" in terminal_effect:
        scope = "side_wide"
    else:
        scope = "single_candidate"

    context: list[str] = []
    if slice_payload.completeness == "partial":
        context.append("partial_episode")
    if slice_payload.quarantine_state:
        context.append(f"quarantine_{slice_payload.quarantine_state}")
    if slice_payload.certificate_state:
        context.append(f"certificate_{slice_payload.certificate_state}")

    return FailurePattern(
        mechanism_signature=tuple(str(v) for v in mechanism_signature),
        failure_shape=shape,
        causal_position=position,
        impact_scope=scope,
        context_features=tuple(sorted(context)),
    )


def extract_failure_pattern(episode: CausalFailureEpisode) -> FailurePattern:
    """Extract a structured failure pattern from one causal episode.

    The mechanism-signature component uses the shared episode signature —
    the same key the experience memory stores entries under — so the query
    and the index always speak the same vocabulary.
    """
    return _pattern_from_slice(
        episode.causal_slice,
        episode_signature(episode),
    )


def _entry_pattern(entry: MechanismEntry) -> FailurePattern:
    """Reconstruct a stored entry's pattern from its representatives' slices.

    Each representative persists its causal slice, so the entry's pattern is
    recoverable; prefer the most informative (complete) representative.
    """
    representatives = [
        rep for rep in entry.representatives.values() if rep is not None
    ]
    if representatives:
        representatives.sort(
            key=lambda rep: (
                rep.completeness != "complete",
                -int(rep.evidence_fields),
                -int(rep.sequence),
            )
        )
        return _pattern_from_slice(
            CausalSlice.from_dict(representatives[0].causal_slice),
            entry.signature,
        )
    return FailurePattern(
        mechanism_signature=entry.signature,
        failure_shape="unknown",
        causal_position="unknown",
        impact_scope="unknown",
    )


def _similarity(a: FailurePattern, b: FailurePattern) -> float:
    """Deterministic similarity between two patterns (0.0-1.0).

    Weighted exact-match over discrete fields; no embeddings, no thresholds.
    """
    score = 0.0
    sig_a = set(a.mechanism_signature)
    sig_b = set(b.mechanism_signature)
    if sig_a and sig_b:
        score += 0.4 * len(sig_a & sig_b) / max(len(sig_a), len(sig_b))
    if "unknown" not in (a.failure_shape, b.failure_shape):
        if a.failure_shape == b.failure_shape:
            score += 0.25
    if "unknown" not in (a.causal_position, b.causal_position):
        if a.causal_position == b.causal_position:
            score += 0.2
    if "unknown" not in (a.impact_scope, b.impact_scope):
        if a.impact_scope == b.impact_scope:
            score += 0.1
    ctx_a = set(a.context_features)
    ctx_b = set(b.context_features)
    if ctx_a and ctx_b:
        score += 0.05 * len(ctx_a & ctx_b) / max(len(ctx_a), len(ctx_b))
    return min(1.0, score)


def _matched_signature_indices(
    entry: MechanismEntry, episode: CausalFailureEpisode,
) -> tuple[int, ...]:
    """Signature positions where a shape-matched entry really agrees.

    The signature fact order is fixed by ``build_mechanism_signature``
    (layer, artifact, reason_code, evolution_direction, candidate_kind,
    consumer). A shape match may agree on nothing but the failure shape;
    presenting only the agreeing positions keeps the projection honest
    instead of claiming a full signature match.
    """
    slice_payload = episode.causal_slice
    current = (
        str(episode.attribution_snapshot.get("layer") or ""),
        str(episode.attribution_snapshot.get("owner") or ""),
        str(episode.attribution_snapshot.get("reason_code") or ""),
        str(episode.detection_snapshot.get("evolution_direction") or ""),
        str(slice_payload.candidate_kind or ""),
        str(slice_payload.consumer or ""),
    )
    return tuple(
        index
        for index, value in enumerate(current)
        if index < len(entry.signature) and entry.signature[index] == value
    )


def _intervention_from_reference(
    reference: HistoricalEpisodeReference,
) -> dict[str, Any]:
    """The prompt-safe intervention view of one historical reference."""
    return {
        "condition": reference.rule_condition,
        "action_spec": reference.action_spec,
        "outcome": reference.local_outcome,
    }


def _is_effective_attempt(reference: HistoricalEpisodeReference) -> bool:
    """An accept carrying a recorded no-effect reason is a failed
    intervention: the outcome shape proves the edit did not move the
    terminal state, so the prompt must render it as a failure (with its
    no_effect_reason) instead of a success precedent."""
    outcome = reference.local_outcome
    if not outcome:
        return False
    if not str(outcome.get("decision") or "").startswith("accept"):
        return False
    return str(outcome.get("no_effect_reason") or "") not in NO_EFFECT_REASONS


def _match_from_reference(
    pattern: FailurePattern,
    reference: HistoricalEpisodeReference,
    similarity: float,
    path: str,
) -> ExperienceMatch:
    accepted = _is_effective_attempt(reference)
    intervention = _intervention_from_reference(reference)
    return ExperienceMatch(
        pattern=pattern,
        similarity_score=similarity,
        successful_interventions=(intervention,) if accepted else (),
        failed_interventions=() if accepted else (intervention,),
        retrieval_path=path,
    )


def retrieve_structured_experience(
    episode: CausalFailureEpisode,
    memory: ExperienceMemory,
    *,
    max_results: int = 3,
) -> list[ExperienceMatch]:
    """Retrieve structured historical experience for the current episode.

    Level 1 delegates to the mechanism index's exact-signature path;
    Levels 2/3 are the shape + context similarity scan over the same index.
    Each level is deterministic and identity-free.
    """
    current_pattern = extract_failure_pattern(episode)

    exact_refs = memory.retrieve(
        episode=episode,
        exclude_episode_id=episode.episode_id,
    )
    if exact_refs:
        return [
            _match_from_reference(
                current_pattern, reference, 1.0, "exact",
            )
            for reference in exact_refs
        ][:max_results]

    entries = list(memory.entries())
    scored: list[tuple[float, Any]] = []
    for entry in entries:
        sim = _similarity(current_pattern, _entry_pattern(entry))
        if sim > 0.3:
            scored.append((sim, entry))

    # If the strict pass is too thin, widen to a lower bound rather than
    # mixing two different match definitions in one result set.
    if len(scored) < max_results:
        for entry in entries:
            if any(entry is e for _, e in scored):
                continue
            sim = _similarity(current_pattern, _entry_pattern(entry))
            if sim > 0.2:
                scored.append((sim, entry))

    if scored:
        scored.sort(key=lambda x: -x[0])
        results: list[ExperienceMatch] = []
        for sim, entry in scored[:max_results]:
            # A shape-similarity match must only claim the signature
            # positions that actually agree with the current episode.
            refs = memory._refs_for_entry(
                entry, episode, episode.episode_id,
                level=_matched_signature_indices(entry, episode),
            )
            results.extend(
                _match_from_reference(
                    current_pattern, reference, sim, "shape_context",
                )
                for reference in refs
            )
        return results[:max_results]
    return []


def format_experience_for_prompt(
    matches: Sequence[ExperienceMatch],
    *,
    max_chars: int = 2000,
) -> str:
    """Format structured experience for the reflection prompt.

    Bounded, canonical, and identity-free. The model sees *what worked and
    what failed*, never *which case it came from*.
    """
    if not matches:
        return ""

    lines: list[str] = ["<structured_experience>"]

    def emit_intervention(intervention: Mapping[str, Any]) -> None:
        action = intervention.get("action_spec") or {}
        outcome = intervention.get("outcome") or {}
        condition = escape(str(intervention.get("condition") or ""))
        lines.append("      <intervention>")
        if condition:
            lines.append(f"        <condition>{condition}</condition>")
        lines.append(
            f"        <module>{escape(str(action.get('module_id') or 'unknown'))}</module>"
        )
        lines.append(
            f"        <action>{escape(str(action.get('operation') or 'unknown'))}</action>"
        )
        for tag, key in (
            ("output_field", "output_field"),
            ("postcondition", "postcondition"),
            ("observable_field", "observable_field"),
            ("expected_direction", "expected_direction"),
        ):
            value = str(action.get(key) or "")
            if value:
                lines.append(f"        <{tag}>{escape(value)}</{tag}>")
        lines.append(
            f"        <outcome>{escape(str(outcome.get('decision') or 'unknown'))}</outcome>"
        )
        no_effect = str(outcome.get("no_effect_reason") or "")
        if no_effect:
            lines.append(
                f"        <no_effect_reason>{escape(no_effect)}</no_effect_reason>"
            )
        lines.append("      </intervention>")

    for index, match in enumerate(matches[:3]):
        lines.append(f"  <match index=\"{index}\" similarity=\"{match.similarity_score:.2f}\" path=\"{match.retrieval_path}\">")
        if match.successful_interventions:
            lines.append("    <successful_interventions>")
            for intervention in match.successful_interventions[:2]:
                emit_intervention(intervention)
            lines.append("    </successful_interventions>")
        if match.failed_interventions:
            lines.append("    <failed_interventions>")
            for intervention in match.failed_interventions[:2]:
                emit_intervention(intervention)
            lines.append("    </failed_interventions>")
        lines.append("  </match>")
    lines.append("</structured_experience>")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 20] + "\n... (truncated)\n</structured_experience>"
    return text


# ============================================================
# Negative-feedback analysis
# ============================================================
# The rejected-edit buffer shape is defined by the L1 planner
# (``l1.RejectedEditEntry``); importing it would couple this module to the
# prompt builder, so the entry type stays a structural view (``Any``) and
# the analysis reads only the fields the buffer contract names.

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


ROOT_CAUSE_SCHEMA_MISMATCH = "schema_mismatch"
ROOT_CAUSE_UNOWNED_TARGET = "unowned_target"
ROOT_CAUSE_CONDITION_OVERLOAD = "condition_overload"
ROOT_CAUSE_NO_OBSERVABLE_EFFECT = "no_observable_effect"
ROOT_CAUSE_GOLD_LEAKAGE = "gold_leakage"
ROOT_CAUSE_DUPLICATE_RULE = "duplicate_rule"
ROOT_CAUSE_BASELINE_VIOLATION = "baseline_violation"
ROOT_CAUSE_UNKNOWN = "unknown"


def _classify_root_cause(rejected: RejectedEditEntry) -> str:
    """Map one rejected edit to its root cause (closed vocabulary).

    Uses exact prefix matching against known gate rejection codes, not
    substring containment, so a reason like "invalid_schema" never matches
    "invalid_parameter" by accident.
    """
    reasons = [str(r).lower() for r in rejected.reject_reasons]
    observed = rejected.observed_effect.lower()

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
            "abstract actions that do not change registry-declared court fields."
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
    priority_order = {"critical": 0, "moderate": 1, "minor": 2}
    cause_severity: dict[str, int] = {}

    for rejected in rejected_buffer:
        root_cause = _classify_root_cause(rejected)
        severity = _severity_for(rejected)
        stage = rejected.rejection_stage

        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        cause_counts[root_cause] = cause_counts.get(root_cause, 0) + 1
        cause_severity[root_cause] = min(
            cause_severity.get(root_cause, 3), priority_order[severity],
        )

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

    unique_causes = sorted(
        cause_counts.items(),
        key=lambda x: (cause_severity[x[0]], -x[1]),
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

    critical = [i for i in analysis.insights if i.severity == "critical"]
    moderate = [i for i in analysis.insights if i.severity == "moderate"]

    for insight in (critical + moderate)[:3]:
        lines.append(f"  <insight severity=\"{insight.severity}\">")
        lines.append(f"    <root_cause>{insight.root_cause}</root_cause>")
        lines.append(f"    <how_to_avoid>{insight.how_to_avoid}</how_to_avoid>")
        lines.append("  </insight>")

    lines.append("</negative_feedback_analysis>")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 30] + "\n... (truncated)\n</negative_feedback_analysis>"
    return text


__all__ = [
    "ExperienceMatch",
    "FailurePattern",
    "NegativeFeedbackAnalysis",
    "NegativeFeedbackInsight",
    "analyze_negative_feedback",
    "extract_failure_pattern",
    "format_experience_for_prompt",
    "format_negative_feedback_for_prompt",
    "retrieve_structured_experience",
]
