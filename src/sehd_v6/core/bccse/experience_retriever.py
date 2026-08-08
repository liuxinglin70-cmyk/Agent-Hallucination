"""SEHD v6.1 — Structured experience retrieval for L1 reflection.

Replaces the shallow "historical hints" in the legacy reflection prompt with
a deterministic, pattern-based retrieval over the EvolutionExperienceMemory.
The retriever answers: *what worked, what failed, and why — for failures
structurally similar to the current one* — without ever exposing case
identity, gold labels, or raw trajectories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .evolution_experience_memory import EvolutionExperienceMemory, MechanismEntry
from .failure_episode import (
    CausalSlice,
    FailureEpisode,
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
    # Failure shape: what kind of structural break occurred
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

    # Causal position: where in the producer-consumer chain the break sits
    consumer = str(slice_payload.consumer or "")
    if "candidate" in consumer:
        position = "producer"
    elif "certificate" in consumer or "coverage" in consumer:
        position = "consumer"
    else:
        position = "propagation"

    # Impact scope: how much of the verdict the break can move
    terminal_effect = str(slice_payload.court_effect or "")
    if "terminal" in terminal_effect or "verdict" in terminal_effect:
        scope = "terminal"
    elif "coverage" in terminal_effect or "side" in terminal_effect:
        scope = "side_wide"
    else:
        scope = "single_candidate"

    # Context features: coarse trajectory descriptors (identity-free)
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


def extract_failure_pattern(episode: FailureEpisode) -> FailurePattern:
    """Extract a structured failure pattern from one FailureEpisode.

    The pattern is deterministic: same episode always yields the same pattern.
    No case identity, gold, or raw trajectory text participates.
    """
    return _pattern_from_slice(
        episode.causal_slice,
        tuple(str(v) for v in episode.attribution.get("mechanism_signature", ())),
    )


def _entry_pattern(entry: MechanismEntry) -> FailurePattern:
    """Reconstruct a stored entry's pattern from its representatives' slices.

    Level 2 retrieval previously compared the query against an all-"unknown"
    pattern, which silently discarded the shape/position/scope weights. Each
    representative already persists its causal slice, so the entry's pattern
    is recoverable; prefer the most informative (complete) representative.
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
    # Mechanism signature overlap (highest weight)
    sig_a = set(a.mechanism_signature)
    sig_b = set(b.mechanism_signature)
    if sig_a and sig_b:
        score += 0.4 * len(sig_a & sig_b) / max(len(sig_a), len(sig_b))
    # Failure shape match (an "unknown" side cannot match; skip its weight)
    if "unknown" not in (a.failure_shape, b.failure_shape):
        if a.failure_shape == b.failure_shape:
            score += 0.25
    # Causal position match
    if "unknown" not in (a.causal_position, b.causal_position):
        if a.causal_position == b.causal_position:
            score += 0.2
    # Impact scope match
    if "unknown" not in (a.impact_scope, b.impact_scope):
        if a.impact_scope == b.impact_scope:
            score += 0.1
    # Context overlap
    ctx_a = set(a.context_features)
    ctx_b = set(b.context_features)
    if ctx_a and ctx_b:
        score += 0.05 * len(ctx_a & ctx_b) / max(len(ctx_a), len(ctx_b))
    return min(1.0, score)


def retrieve_structured_experience(
    episode: FailureEpisode,
    memory: EvolutionExperienceMemory,
    *,
    max_results: int = 3,
) -> list[ExperienceMatch]:
    """Retrieve structured historical experience for the current episode.

    Three-level fallback:
    1. Exact mechanism signature match (current EvolutionExperienceMemory)
    2. Shape + context similarity match
    3. Causal position + impact scope match

    Each level is deterministic and identity-free.
    """
    current_pattern = extract_failure_pattern(episode)

    # Level 1: exact signature match (existing memory path)
    exact_refs = memory.retrieve(
        episode=episode,
        exclude_episode_id=episode.episode_id,
    )
    if exact_refs:
        matches = []
        for ref in exact_refs:
            match = ExperienceMatch(
                pattern=current_pattern,
                similarity_score=1.0,
                successful_interventions=(
                    ({"action_spec": ref.action_spec, "outcome": ref.local_outcome})
                    if ref.local_outcome and ref.local_outcome.get("decision", "").startswith("accept")
                    else ()
                ),
                failed_interventions=(
                    ({"action_spec": ref.action_spec, "outcome": ref.local_outcome})
                    if ref.local_outcome and not ref.local_outcome.get("decision", "").startswith("accept")
                    else ()
                ),
                retrieval_path="exact",
            )
            matches.append(match)
        return matches[:max_results]

    # Level 2: shape + context similarity（带索引优化）
    # 使用分层索引限制检索范围，避免全量遍历
    shape_matches: list[tuple[float, Any]] = []
    entries = list(memory.entries())
    
    # 索引优化：按 failure_shape 和 causal_position 预过滤
    target_shape = current_pattern.failure_shape
    target_position = current_pattern.causal_position
    
    # 第一轮：精确形状+位置匹配
    for entry in entries:
        # 从签名中提取形状和位置信息（如果签名包含这些信息）
        entry_sig = entry.signature
        # 签名格式: (layer, artifact, reason_code, direction, candidate_kind, consumer, fields...)
        # 检查是否有匹配的元素
        if len(entry_sig) >= 2:
            # 快速预过滤：检查签名的前几个元素是否匹配
            sig_match = any(
                target_shape in str(elem) or target_position in str(elem)
                for elem in entry_sig[:3]
            )
            if not sig_match:
                continue

        sim = _similarity(current_pattern, _entry_pattern(entry))
        if sim > 0.3:
            shape_matches.append((sim, entry))

    # 如果预过滤后结果太少，放宽条件
    if len(shape_matches) < max_results:
        for entry in entries:
            if any(entry is e for _, e in shape_matches):
                continue  # 跳过已匹配的
            sim = _similarity(current_pattern, _entry_pattern(entry))
            if sim > 0.2:  # 放宽阈值
                shape_matches.append((sim, entry))
    
    # 去重并排序
    seen = set()
    unique_matches = []
    for sim, entry in shape_matches:
        if id(entry) not in seen:
            seen.add(id(entry))
            unique_matches.append((sim, entry))
    shape_matches = unique_matches

    if shape_matches:
        shape_matches.sort(key=lambda x: -x[0])
        results = []
        for sim, entry in shape_matches[:max_results]:
            # Retrieve representative for this entry
            refs = memory._refs_for_entry(entry, episode, episode.episode_id)
            for ref in refs:
                results.append(ExperienceMatch(
                    pattern=current_pattern,
                    similarity_score=sim,
                    successful_interventions=(
                        ({"action_spec": ref.action_spec, "outcome": ref.local_outcome})
                        if ref.local_outcome and ref.local_outcome.get("decision", "").startswith("accept")
                        else ()
                    ),
                    failed_interventions=(
                        ({"action_spec": ref.action_spec, "outcome": ref.local_outcome})
                        if ref.local_outcome and not ref.local_outcome.get("decision", "").startswith("accept")
                        else ()
                    ),
                    retrieval_path="shape_context",
                ))
        return results[:max_results]

    # Level 3: no usable history
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
    for index, match in enumerate(matches[:3]):
        lines.append(f"  <match index=\"{index}\" similarity=\"{match.similarity_score:.2f}\" path=\"{match.retrieval_path}\">")
        if match.successful_interventions:
            lines.append("    <successful_interventions>")
            for intervention in match.successful_interventions[:2]:
                action = intervention.get("action_spec", {})
                outcome = intervention.get("outcome", {})
                lines.append(f"      <intervention>")
                lines.append(f"        <action>{action.get('operation', 'unknown')}</action>")
                lines.append(f"        <module>{action.get('target_module', 'unknown')}</module>")
                lines.append(f"        <outcome>{outcome.get('decision', 'unknown')}</outcome>")
                lines.append(f"      </intervention>")
            lines.append("    </successful_interventions>")
        if match.failed_interventions:
            lines.append("    <failed_interventions>")
            for intervention in match.failed_interventions[:2]:
                action = intervention.get("action_spec", {})
                outcome = intervention.get("outcome", {})
                lines.append(f"      <intervention>")
                lines.append(f"        <action>{action.get('operation', 'unknown')}</action>")
                lines.append(f"        <module>{action.get('target_module', 'unknown')}</module>")
                lines.append(f"        <outcome>{outcome.get('decision', 'unknown')}</outcome>")
                lines.append(f"        <no_effect_reason>{outcome.get('no_effect_reason', '')}</no_effect_reason>")
                lines.append(f"      </intervention>")
            lines.append("    </failed_interventions>")
        lines.append("  </match>")
    lines.append("</structured_experience>")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 20] + "\n... (truncated)\n</structured_experience>"
    return text
