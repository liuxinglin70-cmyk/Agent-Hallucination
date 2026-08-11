"""Hallucination type classifier (hierarchical architecture).

Classifies agent hallucination cases into distinct types based on trajectory
features, detection process signals, and failure modes. This classification
enables type-specific detection experience learning.

Architectural principle -- Hierarchical Classification:
    An earlier design used flat weighted fusion of 4 signals, where a
    no-information signal (UNRESOLVED_UNKNOWN) could dominate typed signals
    from candidate analysis and CRS patterns. The current design is
    hierarchical:

    Layer 1 (Structural): Classify from detection_result's intrinsic
        features -- candidate kind, CRS pattern, state x coverage. These
        signals always carry information about WHAT went wrong.

    Layer 2 (Attribution Enhancement): Use attribution_signal's error_type
        ONLY when it carries information (i.e. not UNRESOLVED_UNKNOWN /
        SIGNAL_UNAVAILABLE). No-information error types are excluded
        entirely from scoring, not just down-weighted.

    Layer 3 (Evidence threshold): Emit a typed result only when weighted and
        independent evidence clears a conservative threshold; otherwise use
        UNKNOWN. A lone weak structural cue is not sufficient.

No-information signals cannot pollute the scoring space, while UNKNOWN remains
reachable when the remaining evidence is too weak to support typed routing.

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
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import resolve_evolution_state


def _normalize_signal(signal: Any) -> dict[str, Any]:
    """Normalize an attribution signal to a dict for uniform access.

    The classifier accepts both an ``AttributionSignal``-shaped dataclass
    (the canonical type produced by the attribution step) and plain
    ``Mapping[str, Any]`` dicts (used in tests and standalone callers).

    This helper centralizes the conversion so downstream code can use
    plain ``dict.get`` access without coupling to the dataclass shape.
    """
    if signal is None:
        return {}
    if isinstance(signal, Mapping):
        return dict(signal)
    # The attribution signal exposes to_dict() returning the same field
    # names and value types as the dict contract.
    if hasattr(signal, "to_dict"):
        return signal.to_dict()
    return {}


def _as_string_sequence(value: Any) -> tuple[str, ...]:
    """Normalize a census ``axes``/list-like field to a string sequence.

    ``str`` inputs are NOT iterated (a bare string would expand to its
    characters); ``None`` and non-sequence values normalize to empty.
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if item is not None)
    return ()


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

# Error type to hallucination type mapping.
# ErrorType has 21 values; CORRECT is skipped (no hallucination).
# 语义映射说明：
# - false_positive: 误报（clean 被判 error），多为边界过严 → CONTEXTUAL/INSTRUCTIONAL
# - false_negative*: 漏检 error，多为遗漏或证据不足 → OMISSION/FACTUAL
# - unresolved_should_error: 应判 error 但 abstained → OMISSION
# - unresolved_should_clean: 应判 clean 但 abstained → CONTEXTUAL
# - focused_certificate_quarantine: 证据链被隔离、无法定案 → TOOL
# - appeal_*_failure / coverage_mismatch: 上诉阶段证据对抗失效 → TOOL
# - localization_ambiguous: 定位模糊（多个候选源）→ REASONING
_ERROR_TYPE_TO_HALLUCINATION: dict[str, HallucinationType] = {
    "false_positive": HallucinationType.CONTEXTUAL,
    "false_negative": HallucinationType.OMISSION,
    "false_negative_clean": HallucinationType.OMISSION,
    "false_negative_unresolved": HallucinationType.OMISSION,
    "unresolved_should_error": HallucinationType.OMISSION,
    "unresolved_should_clean": HallucinationType.CONTEXTUAL,
    "unresolved_unknown": HallucinationType.UNKNOWN,
    "unresolved_error": HallucinationType.REASONING,
    "invalid_certificate": HallucinationType.TOOL,
    "localization_error": HallucinationType.REASONING,
    "candidate_miss": HallucinationType.OMISSION,
    "crs_contradiction": HallucinationType.REASONING,
    "evidence_incomplete": HallucinationType.FACTUAL,
    "boundary_hesitation": HallucinationType.CONTEXTUAL,
    "signal_unavailable": HallucinationType.UNKNOWN,
    "focused_certificate_quarantine": HallucinationType.TOOL,
    "appeal_prosecutor_failure": HallucinationType.TOOL,
    "appeal_defender_failure": HallucinationType.TOOL,
    "appeal_coverage_mismatch": HallucinationType.TOOL,
    "localization_ambiguous": HallucinationType.REASONING,
}

# Error types that carry NO information about the hallucination type.
# These are excluded entirely from Layer 2 scoring (not just down-weighted)
# because they represent "the classifier doesn't know" -- using them would
# be circular reasoning (classifying based on a label that says "unknown").
_NO_INFO_ERROR_TYPES: frozenset[str] = frozenset({
    "unresolved_unknown",
    "signal_unavailable",
})


@dataclass(frozen=True)
class TrajectoryFeatures:
    """Versioned, label-free features extracted from the detector trajectory."""

    schema_version: str
    candidate_count: int
    candidate_kinds: tuple[str, ...]
    axes: tuple[str, ...]
    module_tags: tuple[str, ...]
    has_tool_action: bool
    has_omission: bool
    has_adoption: bool
    has_claim: bool
    state: str
    prosecution_coverage_state: str | None
    defense_coverage_state: str | None
    crs_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_count": self.candidate_count,
            "candidate_kinds": list(self.candidate_kinds),
            "axes": list(self.axes),
            "module_tags": list(self.module_tags),
            "has_tool_action": self.has_tool_action,
            "has_omission": self.has_omission,
            "has_adoption": self.has_adoption,
            "has_claim": self.has_claim,
            "state": self.state,
            "prosecution_coverage_state": self.prosecution_coverage_state,
            "defense_coverage_state": self.defense_coverage_state,
            "crs_source": self.crs_source,
        }


@dataclass(frozen=True)
class TypeClassificationResult:
    """Result of hallucination type classification."""

    primary_type: HallucinationType
    secondary_type: HallucinationType | None
    confidence: float  # 0.0-1.0
    evidence: dict[str, Any]
    classification_method: str  # "rule_based" or "llm_enhanced"
    trajectory_features: TrajectoryFeatures | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_type": self.primary_type.value,
            "secondary_type": self.secondary_type.value if self.secondary_type else None,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "classification_method": self.classification_method,
            "trajectory_features": self.trajectory_features.to_dict() if self.trajectory_features else None,
        }


@dataclass
class HallucinationTypeClassifier:
    """Classifies agent hallucinations using a hierarchical architecture.

    Layer 1 (Structural): detection_result intrinsic features.
    Layer 2 (Attribution Enhancement): informative error_type only.
    Layer 3 (Evidence threshold): UNKNOWN when typed evidence is absent or
        insufficiently corroborated.

    No-information error types (UNRESOLVED_UNKNOWN, SIGNAL_UNAVAILABLE)
    are excluded from Layer 2 entirely, preventing circular reasoning.
    """

    _layer1_candidate_weight: float = 0.15
    _layer1_crs_weight: float = 0.15
    _layer1_state_weight: float = 0.10
    _layer2_error_type_weight: float = 0.40
    _layer2_module_weight: float = 0.20
    _min_confidence: float = 0.3
    _min_typed_score: float = 0.30

    def extract_features(
        self, detection_result: Mapping[str, Any], attribution_signal: Any = None,
    ) -> TrajectoryFeatures:
        candidates = [row for row in (detection_result.get("census") or []) if isinstance(row, Mapping)]
        raw_kinds = [str(row.get("candidate_kind") or "").lower() for row in candidates]
        kinds = tuple(sorted(set(raw_kinds)))
        axes = tuple(sorted({
            str(axis).lower()
            for row in candidates
            for axis in _as_string_sequence(row.get("axes"))
        }))
        tags = tuple(sorted({str(row.get("module_tag") or "").lower() for row in candidates if row.get("module_tag")}))
        claims = [str(row.get("claim_or_commitment") or "").lower() for row in candidates]
        signal = _normalize_signal(attribution_signal)
        return TrajectoryFeatures(
            schema_version="htc_tes.trajectory_features.v1",
            candidate_count=len(candidates), candidate_kinds=kinds, axes=axes,
            module_tags=tags,
            has_tool_action=any("tool" in kind or "tool" in claim for kind, claim in zip(raw_kinds, claims)),
            has_omission=any("omission" in kind for kind in kinds),
            has_adoption=any("adoption" in kind for kind in kinds),
            has_claim=any("claim" in kind or "assert" in kind for kind in kinds),
            state=resolve_evolution_state(detection_result),
            prosecution_coverage_state=signal.get("prosecution_coverage_state"),
            defense_coverage_state=signal.get("defense_coverage_state"),
            crs_source=str(signal.get("crs_source") or "unknown"),
        )

    def classify(
        self,
        detection_result: Mapping[str, Any],
        attribution_signal: Any = None,
    ) -> TypeClassificationResult:
        """Classify a hallucination case based on detection results.

        Args:
            detection_result: The detection pipeline output dict.
            attribution_signal: Optional attribution signal. Accepts either
                an attribution-signal dataclass (canonical) or a
                ``Mapping[str, Any]`` dict.

        Returns:
            TypeClassificationResult with primary and secondary types.
        """
        scores: dict[HallucinationType, float] = {t: 0.0 for t in HallucinationType}
        support_families: dict[HallucinationType, set[str]] = defaultdict(set)

        def add_score(
            hallucination_type: HallucinationType,
            score: float,
            family: str,
        ) -> None:
            scores[hallucination_type] += score
            support_families[hallucination_type].add(family)

        evidence: dict[str, Any] = {}
        trajectory_features = self.extract_features(detection_result, attribution_signal)

        # ================================================================
        # Layer 1: Structural signals from detection_result
        # ================================================================
        # These signals always carry information about WHAT went wrong,
        # independent of whether the attributor could determine WHY.

        # 1a. Candidate nature analysis (claim/action/omission patterns)
        candidates = [
            row for row in (detection_result.get("census") or [])
            if isinstance(row, Mapping)
        ]
        if candidates:
            candidate_types = self._analyze_candidates(candidates)
            for htype, count in candidate_types.items():
                add_score(
                    htype,
                    self._layer1_candidate_weight * min(count, 3),
                    "candidate",
                )
            evidence["candidate_analysis"] = {
                t.value: c for t, c in candidate_types.items()
            }

        # 1b. CRS score pattern analysis (which dimensions are anomalous)
        signal_dict = _normalize_signal(attribution_signal)
        crs_scores = signal_dict.get("crs_scores") or detection_result.get("evidential_scores_parsed") or {}
        if crs_scores:
            crs_type = self._analyze_crs_pattern(
                crs_scores, risk_semantics=bool(signal_dict.get("crs_scores"))
            )
            if crs_type:
                add_score(crs_type, self._layer1_crs_weight, "crs")
                evidence["crs_pattern"] = crs_type.value

        # 1c. State x coverage structural signal
        state = resolve_evolution_state(detection_result)
        if state == "unresolved":
            # The signal-side aggregate is trusted only for a COMPLETE CRS
            # reading: the 0.5-uncertainty sentinel (no native CRS and no
            # usable certificate -> every dimension 0.5, aggregate exactly
            # 0.5) carries no information and must not vote. A bare 0.0
            # aggregate (missing signal) must also never be treated as a
            # real low score. Only complete readings vote; otherwise the
            # parsed evidential scores, whose polarity is inverted (low
            # average = anomalous), decide.
            crs_complete = bool(signal_dict.get("crs_complete"))
            raw_aggregate = signal_dict.get("crs_aggregate")
            if crs_complete and isinstance(raw_aggregate, (int, float)):
                crs_agg: float | None = float(raw_aggregate)
                if crs_agg > 0.4:
                    add_score(
                        HallucinationType.FACTUAL,
                        self._layer1_state_weight,
                        "state_coverage",
                    )
                elif crs_agg < 0.2:
                    add_score(
                        HallucinationType.CONTEXTUAL,
                        self._layer1_state_weight,
                        "state_coverage",
                    )
            else:
                crs_agg = self._get_crs_aggregate(detection_result)
                if crs_agg is not None:
                    # evidential-scores source: a low aggregate is the anomaly
                    # signal, so the polarity flips relative to the signal side.
                    if crs_agg < 0.3:
                        add_score(
                            HallucinationType.FACTUAL,
                            self._layer1_state_weight,
                            "state_coverage",
                        )
                    elif crs_agg > 0.7:
                        add_score(
                            HallucinationType.CONTEXTUAL,
                            self._layer1_state_weight,
                            "state_coverage",
                        )

        # ================================================================
        # Layer 2: Attribution enhancement (informative signals only)
        # ================================================================
        if signal_dict:
            error_type = str(signal_dict.get("error_type") or "")

            # Only use error_type if it carries information.
            # No-information types are excluded entirely -- they represent
            # "the attributor doesn't know" and using them would be
            # circular (classifying based on "unknown").
            if error_type and error_type not in _NO_INFO_ERROR_TYPES:
                mapped = _ERROR_TYPE_TO_HALLUCINATION.get(error_type)
                if mapped and mapped != HallucinationType.UNKNOWN:
                    add_score(
                        mapped,
                        self._layer2_error_type_weight,
                        "error_type",
                    )
                    evidence["error_type_signal"] = error_type

            target_module = str(signal_dict.get("target_module") or "")
            module_type = self._map_module_to_type(target_module)
            if module_type:
                add_score(module_type, self._layer2_module_weight, "module")
                evidence["module_signal"] = target_module

        # ================================================================
        # Layer 3: Determine primary type
        # ================================================================
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        typed_scores = [
            (t, s) for t, s in sorted_scores if t != HallucinationType.UNKNOWN
        ]
        top_type, top_score = (
            typed_scores[0]
            if typed_scores
            else (HallucinationType.UNKNOWN, 0.0)
        )
        top_families = support_families.get(top_type, set())
        single_candidate_cue = top_families == {"candidate"}
        has_sufficient_evidence = (
            top_score >= self._min_typed_score
            and not single_candidate_cue
        )
        primary_type = top_type if has_sufficient_evidence else HallucinationType.UNKNOWN
        confidence = (
            self._normalize_confidence(scores, len(top_families))
            if has_sufficient_evidence
            else 0.0
        )

        secondary_type: HallucinationType | None = None
        secondary_threshold = max(0.12, top_score * 0.35)
        for t, s in typed_scores:
            if (
                primary_type != HallucinationType.UNKNOWN
                and t != primary_type
                and s >= secondary_threshold
                and support_families.get(t)
            ):
                secondary_type = t
                break

        # Conflicting or low-confidence evidence remains UNKNOWN even if one
        # weak typed cue exists; routing should not manufacture certainty.
        if confidence < self._min_confidence:
            primary_type = HallucinationType.UNKNOWN
            confidence = 0.0
            secondary_type = None

        evidence["raw_scores"] = {t.value: round(s, 3) for t, s in sorted_scores}
        evidence["support_families"] = {
            htype.value: sorted(families)
            for htype, families in support_families.items()
            if families
        }
        evidence["evidence_threshold"] = {
            "minimum_typed_score": self._min_typed_score,
            "top_score": round(top_score, 3),
            "single_candidate_cue_rejected": single_candidate_cue,
        }
        # Layer 2 is only "active" if it actually contributed a score
        layer2_active = bool(signal_dict) and bool(evidence.get("error_type_signal") or evidence.get("module_signal"))
        evidence["classification_layer"] = (
            "structural+attribution" if layer2_active else "structural_only"
        )

        return TypeClassificationResult(
            primary_type=primary_type,
            secondary_type=secondary_type,
            confidence=confidence,
            evidence=evidence,
            classification_method="rule_based",
            trajectory_features=trajectory_features,
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
        """Analyze candidate nature to infer hallucination types.

        The counts are cue-counts, not candidate-counts: the kind/axes rules
        and the claim-text pattern rules are independent evidence views of
        the same candidate, and both may legitimately vote for one type.
        """
        type_counts: dict[HallucinationType, int] = Counter()

        for candidate in candidates:
            kind = str(candidate.get("candidate_kind") or "").lower()
            claim = str(candidate.get("claim_or_commitment") or "").lower()

            # Candidate kind-based classification
            axes = {str(value).lower() for value in _as_string_sequence(candidate.get("axes"))}
            module_tag = str(candidate.get("module_tag") or "").lower()
            if "assertion" in kind or "claim" in kind or "factual" in kind or "f" in axes:
                type_counts[HallucinationType.FACTUAL] += 1
            if "tool" in kind or "tool" in module_tag or ("action" in kind and ("tool" in claim or "api" in claim)):
                type_counts[HallucinationType.TOOL] += 1
            elif "action" in kind:
                # action 但非 tool 相关，更可能是 REASONING 或 OMISSION
                type_counts[HallucinationType.REASONING] += 1
            if "omission" in kind:
                type_counts[HallucinationType.OMISSION] += 1
            if "adoption" in kind:
                type_counts[HallucinationType.ADOPTION] += 1
            if "faithfulness" in kind or "fa" in axes:
                type_counts[HallucinationType.INSTRUCTIONAL] += 1
            if "reason" in kind or "reason" in module_tag:
                type_counts[HallucinationType.REASONING] += 1

            # Pattern-based classification on claim text (use re.search)
            if any(re.search(p, claim) for p in _INSTRUCTIONAL_PATTERNS):
                type_counts[HallucinationType.INSTRUCTIONAL] += 1
            if any(re.search(p, claim) for p in _TOOL_PATTERNS):
                type_counts[HallucinationType.TOOL] += 1
            if any(re.search(p, claim) for p in _FACTUAL_PATTERNS):
                type_counts[HallucinationType.FACTUAL] += 1
            if any(re.search(p, claim) for p in _REASONING_PATTERNS):
                type_counts[HallucinationType.REASONING] += 1
            if any(re.search(p, claim) for p in _OMISSION_PATTERNS):
                type_counts[HallucinationType.OMISSION] += 1
            if any(re.search(p, claim) for p in _ADOPTION_PATTERNS):
                type_counts[HallucinationType.ADOPTION] += 1
            if any(re.search(p, claim) for p in _CONTEXTUAL_PATTERNS):
                type_counts[HallucinationType.CONTEXTUAL] += 1

        return dict(type_counts)

    def _analyze_crs_pattern(
        self, crs_scores: Mapping[str, Any], *, risk_semantics: bool = False
    ) -> HallucinationType | None:
        """Analyze CRS score patterns to infer hallucination type."""
        type_scores: dict[HallucinationType, float] = {}

        for dim, info in crs_scores.items():
            raw_score = info.get("score") if isinstance(info, Mapping) else info
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if not 0.0 <= score <= 1.0:
                continue
            anomalous = score > 0.7 if risk_semantics and dim != "recovery" else score < 0.3

            if dim == "factuality" and anomalous:
                type_scores[HallucinationType.FACTUAL] = (
                    type_scores.get(HallucinationType.FACTUAL, 0) + 0.4
                )
            if dim == "counterfactual" and anomalous:
                type_scores[HallucinationType.REASONING] = (
                    type_scores.get(HallucinationType.REASONING, 0) + 0.4
                )
            if dim == "recovery" and score < 0.3:
                type_scores[HallucinationType.INSTRUCTIONAL] = (
                    type_scores.get(HallucinationType.INSTRUCTIONAL, 0) + 0.3
                )
            if dim == "process" and anomalous:
                type_scores[HallucinationType.TOOL] = (
                    type_scores.get(HallucinationType.TOOL, 0) + 0.3
                )
            if dim == "scope" and anomalous:
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
                    try:
                        score = float(info.get("score") or 0)
                    except (TypeError, ValueError):
                        continue
                    total += score
                    count += 1
            if count > 0:
                return total / count
        return None

    def _normalize_confidence(
        self,
        scores: dict[HallucinationType, float],
        support_count: int = 0,
    ) -> float:
        """Normalize scores to confidence value.

        UNKNOWN is excluded from the denominator so that a weak but
        dominant typed signal is not diluted into UNKNOWN by the
        signal_unavailable fallback (which gives UNKNOWN a 0.4 floor).
        """
        if not scores:
            return 0.0
        # Exclude UNKNOWN from the dominance calculation
        typed_scores = {
            t: s for t, s in scores.items() if t != HallucinationType.UNKNOWN
        }
        ordered = sorted(typed_scores.values(), reverse=True)
        max_score = ordered[0] if ordered else 0.0
        second_score = ordered[1] if len(ordered) > 1 else 0.0
        total = sum(typed_scores.values())
        if total == 0 or max_score == 0:
            return 0.0
        dominance = max_score / total
        absolute_strength = min(max_score / 0.8, 1.0)
        margin = (max_score - second_score) / max_score
        confidence = (
            0.45 * absolute_strength
            + 0.30 * dominance
            + 0.25 * margin
        )
        if support_count <= 1:
            confidence = min(confidence, 0.55)
        elif support_count == 2:
            confidence = min(confidence, 0.78)
        return min(max(confidence, 0.0), 1.0)
