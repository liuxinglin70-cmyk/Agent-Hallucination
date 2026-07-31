"""HTC-TES v3 — Hallucination Type Classifier.

Classifies agent hallucination cases into distinct types based on trajectory
features, detection process signals, and failure modes. This classification
enables type-specific detection experience learning.

The classifier operates in two modes:
1. Rule-based classification (no LLM): uses trajectory features and detection signals
2. LLM-enhanced classification: uses LLM for ambiguous cases

Hallucination types:
- FACTUAL: Agent makes unsupported factual claims
- INSTRUCTIONAL: Agent violates user instructions or task requirements
- TOOL: Agent misuses tools (wrong tool, wrong parameters)
- REASONING: Agent produces incorrect reasoning/decision logic
- OMISSION: Agent fails to act when it should
- ADOPTION: Agent adopts information without proper verification
- CONTEXTUAL: Agent loses context or produces out-of-context responses
- UNKNOWN: Cannot determine hallucination type
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class HallucinationType(str, Enum):
    """Classification of agent hallucination types.

    Each type represents a distinct failure mode that requires
    different detection strategies and rule generation approaches.
    """

    FACTUAL = "factual"
    INSTRUCTIONAL = "instructional"
    TOOL = "tool"
    REASONING = "reasoning"
    OMISSION = "omission"
    ADOPTION = "adoption"
    CONTEXTUAL = "contextual"
    UNKNOWN = "unknown"


# Trajectory feature patterns for rule-based classification
_FACTUAL_PATTERNS = [
    r"fabricat",
    r"unsupported.*claim",
    r"no.*evidence.*for.*claim",
    r"hallucinat.*fact",
    r"invented.*information",
]

_INSTRUCTIONAL_PATTERNS = [
    r"instruction.*violat",
    r"didn.*follow.*instruction",
    r"ignore.*user.*request",
    r"disobey",
    r"policy.*violation",
]

_TOOL_PATTERNS = [
    r"wrong.*tool",
    r"tool.*misuse",
    r"incorrect.*tool.*call",
    r"tool.*error",
    r"api.*error",
    r"failed.*tool",
]

_REASONING_PATTERNS = [
    r"reasoning.*error",
    r"logical.*error",
    r"incorrect.*inference",
    r"wrong.*conclusion",
    r"flawed.*reasoning",
]

_OMISSION_PATTERNS = [
    r"fail.*to.*act",
    r"missing.*action",
    r"should.*have.*called",
    r"omitted.*step",
    r"no.*response.*when.*expected",
]

_ADOPTION_PATTERNS = [
    r"adopt.*without.*verif",
    r"accept.*unsupported",
    r"use.*without.*confirm",
    r"unverified.*information",
]

_CONTEXTUAL_PATTERNS = [
    r"out.*of.*context",
    r"ignore.*context",
    r"context.*lost",
    r"irrelevant.*response",
    r"off.*topic",
]

# Error type to hallucination type mapping
_ERROR_TYPE_TO_HALLUCINATION: dict[str, HallucinationType] = {
    "false_positive": HallucinationType.FACTUAL,
    "false_negative_clean": HallucinationType.INSTRUCTIONAL,
    "false_negative_unresolved": HallucinationType.REASONING,
    "unresolved_should_error": HallucinationType.FACTUAL,
    "unresolved_should_clean": HallucinationType.CONTEXTUAL,
    "unresolved_unknown": HallucinationType.UNKNOWN,
    "localization_error": HallucinationType.REASONING,
    "candidate_miss": HallucinationType.OMISSION,
    "crs_contradiction": HallucinationType.REASONING,
    "evidence_incomplete": HallucinationType.FACTUAL,
    "boundary_hesitation": HallucinationType.CONTEXTUAL,
    "signal_unavailable": HallucinationType.UNKNOWN,
}


@dataclass(frozen=True)
class TypeClassificationResult:
    """Result of hallucination type classification."""

    primary_type: HallucinationType
    secondary_type: HallucinationType | None
    confidence: float  # 0.0-1.0
    evidence: dict[str, Any]
    classification_method: str  # "rule_based" or "llm_enhanced"

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_type": self.primary_type.value,
            "secondary_type": self.secondary_type.value if self.secondary_type else None,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "classification_method": self.classification_method,
        }


@dataclass
class HallucinationTypeClassifier:
    """Classifies agent hallucinations into distinct types.

    The classifier uses a multi-signal approach:
    1. Detection process signals (error type, module attribution)
    2. Trajectory content analysis (pattern matching on events)
    3. CRS score patterns (which dimensions are anomalous)
    4. Candidate nature (claim type, action type)

    When multiple signals conflict, confidence is reduced and
    secondary types are reported.
    """

    _rule_weight: float = 0.6
    _signal_weight: float = 0.4
    _min_confidence: float = 0.3

    def classify(
        self,
        detection_result: Mapping[str, Any],
        attribution_signal: Mapping[str, Any] | None = None,
    ) -> TypeClassificationResult:
        """Classify a hallucination case based on detection results.

        Args:
            detection_result: The detection pipeline output dict.
            attribution_signal: Optional attribution signal dict.

        Returns:
            TypeClassificationResult with primary and secondary types.
        """
        scores: dict[HallucinationType, float] = {t: 0.0 for t in HallucinationType}
        evidence: dict[str, Any] = {}

        # Signal 1: Error type mapping
        if attribution_signal:
            error_type = str(attribution_signal.get("error_type") or "")
            mapped = _ERROR_TYPE_TO_HALLUCINATION.get(error_type)
            if mapped:
                scores[mapped] += self._signal_weight
                evidence["error_type_signal"] = error_type

            target_module = str(attribution_signal.get("target_module") or "")
            module_type = self._map_module_to_type(target_module)
            if module_type:
                scores[module_type] += 0.2
                evidence["module_signal"] = target_module

        # Signal 2: Candidate nature analysis
        candidates = detection_result.get("census") or []
        if candidates:
            candidate_types = self._analyze_candidates(candidates)
            for htype, count in candidate_types.items():
                scores[htype] += 0.15 * min(count, 3)
            evidence["candidate_analysis"] = {
                t.value: c for t, c in candidate_types.items()
            }

        # Signal 3: CRS score pattern analysis
        crs_scores = detection_result.get("evidential_scores_parsed") or {}
        if crs_scores:
            crs_type = self._analyze_crs_pattern(crs_scores)
            if crs_type:
                scores[crs_type] += 0.15
                evidence["crs_pattern"] = crs_type.value

        # Signal 4: State transition analysis
        state = str(detection_result.get("state") or "")
        if state == "unresolved":
            crs_agg = self._get_crs_aggregate(detection_result)
            if crs_agg is not None:
                if crs_agg > 0.4:
                    scores[HallucinationType.FACTUAL] += 0.1
                elif crs_agg < 0.2:
                    scores[HallucinationType.CONTEXTUAL] += 0.1

        # Determine primary and secondary types
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary_type = sorted_scores[0][0]
        confidence = self._normalize_confidence(scores)

        secondary_type = None
        if len(sorted_scores) > 1 and sorted_scores[1][1] > 0.2:
            secondary_type = sorted_scores[1][0]

        if confidence < self._min_confidence:
            primary_type = HallucinationType.UNKNOWN
            confidence = 0.0
            secondary_type = None

        evidence["raw_scores"] = {t.value: round(s, 3) for t, s in sorted_scores}

        return TypeClassificationResult(
            primary_type=primary_type,
            secondary_type=secondary_type,
            confidence=confidence,
            evidence=evidence,
            classification_method="rule_based",
        )

    def _map_module_to_type(self, module: str) -> HallucinationType | None:
        """Map attribution module to likely hallucination type."""
        module_type_map = {
            "candidate_trigger": HallucinationType.OMISSION,
            "candidate_to_verifier_route": HallucinationType.TOOL,
            "atomic_verifier_instruction": HallucinationType.FACTUAL,
            "evidence_obligation": HallucinationType.FACTUAL,
            "clean_defense_boundary": HallucinationType.INSTRUCTIONAL,
            "causal_link_test": HallucinationType.REASONING,
            "earliest_origin_tiebreak": HallucinationType.REASONING,
            "abstention_rule": HallucinationType.CONTEXTUAL,
        }
        return module_type_map.get(module)

    def _analyze_candidates(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> dict[HallucinationType, int]:
        """Analyze candidate nature to infer hallucination types."""
        type_counts: dict[HallucinationType, int] = Counter()

        for candidate in candidates:
            kind = str(candidate.get("candidate_kind") or "").lower()
            claim = str(candidate.get("claim_or_commitment") or "").lower()

            if "assertion" in kind or "claim" in kind:
                type_counts[HallucinationType.FACTUAL] += 1
            if "action" in kind and ("tool" in claim or "api" in claim):
                type_counts[HallucinationType.TOOL] += 1
            if "action" in kind:
                type_counts[HallucinationType.FACTUAL] += 1
            if "omission" in kind:
                type_counts[HallucinationType.OMISSION] += 1
            if "adoption" in kind:
                type_counts[HallucinationType.ADOPTION] += 1

            # Check for instruction-related patterns
            if any(p in claim for p in _INSTRUCTIONAL_PATTERNS):
                type_counts[HallucinationType.INSTRUCTIONAL] += 1
            if any(p in claim for p in _TOOL_PATTERNS):
                type_counts[HallucinationType.TOOL] += 1

        return dict(type_counts)

    def _analyze_crs_pattern(
        self, crs_scores: Mapping[str, Any]
    ) -> HallucinationType | None:
        """Analyze CRS score patterns to infer hallucination type."""
        type_scores: dict[HallucinationType, float] = {}

        for dim, info in crs_scores.items():
            if not isinstance(info, Mapping):
                continue
            score = float(info.get("score") or 0)
            evidence = str(info.get("evidence") or "").lower()

            if dim == "factuality" and score < 0.3:
                type_scores[HallucinationType.FACTUAL] = (
                    type_scores.get(HallucinationType.FACTUAL, 0) + 0.4
                )
            if dim == "counterfactual" and score < 0.3:
                type_scores[HallucinationType.REASONING] = (
                    type_scores.get(HallucinationType.REASONING, 0) + 0.4
                )
            if dim == "recovery" and score < 0.3:
                type_scores[HallucinationType.INSTRUCTIONAL] = (
                    type_scores.get(HallucinationType.INSTRUCTIONAL, 0) + 0.3
                )
            if dim == "process" and score < 0.3:
                type_scores[HallucinationType.TOOL] = (
                    type_scores.get(HallucinationType.TOOL, 0) + 0.3
                )
            if dim == "scope" and score < 0.3:
                type_scores[HallucinationType.CONTEXTUAL] = (
                    type_scores.get(HallucinationType.CONTEXTUAL, 0) + 0.3
                )

        if not type_scores:
            return None
        return max(type_scores, key=type_scores.get)

    def _get_crs_aggregate(self, detection_result: Mapping[str, Any]) -> float | None:
        """Extract CRS aggregate score from detection result."""
        pa = detection_result.get("proof_adjudication") or {}
        if isinstance(pa, Mapping):
            for key in ("crs_aggregate", "crs_score", "aggregate_score"):
                value = pa.get(key)
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        pass

        scores = detection_result.get("evidential_scores_parsed") or {}
        if isinstance(scores, Mapping):
            total = 0.0
            count = 0
            for info in scores.values():
                if isinstance(info, Mapping):
                    score = float(info.get("score") or 0)
                    total += score
                    count += 1
            if count > 0:
                return total / count
        return None

    def _normalize_confidence(self, scores: dict[HallucinationType, float]) -> float:
        """Normalize scores to confidence value."""
        if not scores:
            return 0.0
        max_score = max(scores.values())
        total = sum(scores.values())
        if total == 0 or max_score == 0:
            return 0.0
        # Confidence = max_score / total (how dominant is the top type)
        raw_confidence = max_score / total if total > 0 else 0.0
        # Scale by absolute signal strength
        signal_strength = min(total / 2.0, 1.0)
        return min(raw_confidence * 0.7 + signal_strength * 0.3, 1.0)


def classify_hallucination_type(
    detection_result: Mapping[str, Any],
    attribution_signal: Mapping[str, Any] | None = None,
) -> TypeClassificationResult:
    """Convenience function for single-call classification.

    Args:
        detection_result: The detection pipeline output.
        attribution_signal: Optional attribution signal.

    Returns:
        TypeClassificationResult with hallucination type classification.
    """
    classifier = HallucinationTypeClassifier()
    return classifier.classify(detection_result, attribution_signal)
