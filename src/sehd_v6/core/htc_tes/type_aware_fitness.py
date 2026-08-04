"""HTC-TES v3 — Type-Aware Fitness Evaluation.

Implements type-aware fitness assessment that evaluates skill edits
against specific hallucination types rather than a monolithic score.
This enables the framework to measure progress per hallucination
category and compute Decisive Accuracy × Coverage Rate.

Key concepts:
1. Type-specific fitness: Each edit is evaluated for fitness improvement
   only on cases of the same hallucination type
2. Coverage tracking: Track what percentage of hallucination types
   have meaningful detection experience
3. Decisive Accuracy: Accuracy on cases where the skill made a decisive
   judgment (not abstained/skipped)
4. Experience memory: Persist type-conditioned detection experiences
   for use in future reflection prompts
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .type_classifier import HallucinationType, TypeClassificationResult
from ..bccse.genome import (
    normalize_type_confidence,
    sanitize_feature_description,
)
from ..bccse.signal_adapter import resolve_evolution_state


def _safe_category(value: Any, *, limit: int = 80) -> str:
    """Keep persisted feedback categorical before it re-enters a prompt."""
    text = str(value or "")[:limit]
    return "".join(
        character
        for character in text
        if character.isalnum() or character in "_:-.>"
    )


@dataclass
class TypeConditionedExperience:
    """A single type-conditioned detection experience entry.

    Records what detection approach worked for a specific hallucination type,
    enabling the framework to accumulate type-specific detection knowledge.
    """

    hallucination_type: HallucinationType
    evolution_direction: str  # "error_to_clean" / "clean_to_error"
    module_targeted: str
    strategy_used: str
    detection_improved: bool
    source_case_id: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    feature_description: str = ""
    type_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hallucination_type": self.hallucination_type.value,
            "evolution_direction": self.evolution_direction,
            "module_targeted": self.module_targeted,
            "strategy_used": self.strategy_used,
            "detection_improved": self.detection_improved,
            "source_case_id": self.source_case_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "feature_description": sanitize_feature_description(
                self.feature_description
            ),
            "type_confidence": normalize_type_confidence(self.type_confidence),
        }


@dataclass
class TypeExperienceMemory:
    """Persistent memory of type-conditioned detection experiences.

    Organizes experiences by hallucination type and evolution direction,
    enabling the reflection engine to reference past successes/failures
    when generating new edits.
    """

    _experiences: dict[HallucinationType, list[TypeConditionedExperience]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _max_per_type: int = 50

    def add_experience(
        self,
        hallucination_type: HallucinationType,
        evolution_direction: str,
        module_targeted: str,
        strategy_used: str,
        detection_improved: bool,
        source_case_id: str,
        metadata: dict[str, Any] | None = None,
        feature_description: str = "",
        type_confidence: float = 0.0,
    ) -> None:
        """Record a detection experience.

        Args:
            hallucination_type: The type of hallucination involved.
            evolution_direction: The direction of evolution attempted.
            module_targeted: Which module was edited.
            strategy_used: What editing strategy was used.
            detection_improved: Whether detection improved for this case.
            source_case_id: The case ID.
            metadata: Additional context.
        """
        feature_description = sanitize_feature_description(feature_description)
        normalized_feature = " ".join(feature_description.lower().split())
        entries = self._experiences[hallucination_type]
        duplicate = next(
            (
                entry for entry in reversed(entries)
                if entry.evolution_direction == evolution_direction
                and entry.module_targeted == module_targeted
                and " ".join(entry.feature_description.lower().split()) == normalized_feature
                and entry.detection_improved == detection_improved
            ),
            None,
        )
        if duplicate is not None:
            duplicate.metadata["observation_count"] = int(
                duplicate.metadata.get("observation_count", 1)
            ) + 1
            duplicate.timestamp = time.time()
            duplicate.type_confidence = max(
                duplicate.type_confidence,
                normalize_type_confidence(type_confidence),
            )
            return

        experience = TypeConditionedExperience(
            hallucination_type=hallucination_type,
            evolution_direction=evolution_direction,
            module_targeted=module_targeted,
            strategy_used=strategy_used,
            detection_improved=detection_improved,
            source_case_id=source_case_id,
            metadata={**(metadata or {}), "observation_count": 1},
            feature_description=feature_description,
            type_confidence=normalize_type_confidence(type_confidence),
        )
        entries.append(experience)

        # Trim to max per type
        if len(entries) > self._max_per_type:
            self._experiences[hallucination_type] = entries[-self._max_per_type:]

    def get_type_experiences(
        self,
        hallucination_type: HallucinationType,
        direction: str | None = None,
    ) -> list[TypeConditionedExperience]:
        """Get experiences for a specific hallucination type.

        Args:
            hallucination_type: The type to query.
            direction: Optional direction filter.

        Returns:
            List of TypeConditionedExperience entries.
        """
        entries = self._experiences.get(hallucination_type, [])
        if direction:
            return [e for e in entries if e.evolution_direction == direction]
        return list(entries)

    def get_success_rate(
        self,
        hallucination_type: HallucinationType,
        direction: str | None = None,
    ) -> float:
        """Get the success rate for a hallucination type.

        Args:
            hallucination_type: The type to query.
            direction: Optional direction filter.

        Returns:
            Success rate (0.0-1.0).
        """
        entries = self.get_type_experiences(hallucination_type, direction)
        if not entries:
            return 0.0
        successes = sum(1 for e in entries if e.detection_improved)
        return successes / len(entries)

    def get_strategy_effectiveness(
        self,
        hallucination_type: HallucinationType,
    ) -> dict[str, dict[str, int]]:
        """Get effectiveness of different strategies for a type.

        Returns:
            Dict mapping strategy -> {"success": N, "total": M}.
        """
        entries = self.get_type_experiences(hallucination_type)
        strategy_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "total": 0}
        )
        for entry in entries:
            stats = strategy_stats[entry.strategy_used]
            stats["total"] += 1
            if entry.detection_improved:
                stats["success"] += 1
        return dict(strategy_stats)

    def get_reflection_summary(
        self,
        hallucination_type: HallucinationType,
        direction: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Return compact, concrete evidence for the next reflection.

        This is deliberately an audit summary rather than a verdict source:
        only previously observed edits and their replay metadata are exposed.
        """
        all_entries = self.get_type_experiences(hallucination_type, direction)
        entries = all_entries[-max(0, int(limit)):]
        # Aggregate over the closed outcome vocabulary so reflection can see
        # WHERE attempts keep failing, not just that they failed.
        outcome_counts: Counter[str] = Counter()
        stage_counts: Counter[str] = Counter()
        for entry in all_entries:
            outcome = _safe_category(entry.metadata.get("outcome"))
            if outcome:
                outcome_counts[outcome] += 1
            stage = _safe_category(entry.metadata.get("outcome_stage"))
            if stage:
                stage_counts[stage] += 1
        return {
            "type": hallucination_type.value,
            "direction": direction,
            "total": len(all_entries),
            "successes": sum(bool(e.detection_improved) for e in entries),
            "outcome_counts": dict(outcome_counts),
            "outcome_stage_counts": dict(stage_counts),
            "promotable_evidence_count": sum(
                bool(e.metadata.get("promotable_evidence")) for e in all_entries
            ),
            "experiences": [
                {
                    "module": e.module_targeted,
                    "strategy": e.strategy_used,
                    "success": e.detection_improved,
                    "feature": e.feature_description,
                    "before": e.metadata.get("before_state"),
                    "after": e.metadata.get("after_state"),
                    "process_state": e.metadata.get("process_state"),
                    "operation": _safe_category(e.metadata.get("operation")),
                    "outcome": _safe_category(e.metadata.get("outcome")),
                    # v5.5 structured outcome: the stage an attempt stopped at
                    # and whether it may be cited as real improvement.
                    "outcome_stage": _safe_category(
                        e.metadata.get("outcome_stage")
                    ),
                    "promotable_evidence": bool(
                        e.metadata.get("promotable_evidence", False)
                    ),
                    "diagnostic_only": bool(
                        e.metadata.get("diagnostic_only", False)
                    ),
                    "reason_categories": [
                        _safe_category(value)
                        for value in list(
                            e.metadata.get("reason_categories") or []
                        )[:5]
                        if _safe_category(value)
                    ],
                    "observable_deltas": [
                        _safe_category(value)
                        for value in list(
                            e.metadata.get("observable_deltas") or []
                        )[:8]
                        if _safe_category(value)
                    ],
                    "expected_effect_status": _safe_category(
                        e.metadata.get("expected_effect_status")
                    ),
                    "type_confidence": e.type_confidence,
                }
                for e in entries
            ],
        }

    def get_top_modules(
        self,
        hallucination_type: HallucinationType,
        limit: int = 3,
    ) -> list[tuple[str, float]]:
        """Get the most effective modules for a hallucination type.

        Returns:
            List of (module_name, success_rate) sorted by success rate.
        """
        entries = self.get_type_experiences(hallucination_type)
        if not entries:
            return []

        module_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "total": 0}
        )
        for entry in entries:
            stats = module_stats[entry.module_targeted]
            stats["total"] += 1
            if entry.detection_improved:
                stats["success"] += 1

        module_rates = [
            (mod, stats["success"] / stats["total"])
            for mod, stats in module_stats.items()
            if stats["total"] >= 2
        ]
        module_rates.sort(key=lambda x: x[1], reverse=True)
        return module_rates[:limit]

    def get_type_coverage(self) -> dict[str, Any]:
        """Get the coverage of hallucination types with experience.

        UNKNOWN is excluded from the denominator because it is a fallback
        type, not a real type to learn. This fixes the DACR scope issue
        where max real coverage was capped at 7/8.
        """
        real_types = [t for t in HallucinationType if t != HallucinationType.UNKNOWN]
        total_types = len(real_types)
        types_with_experience = sum(
            1
            for t in real_types
            if len(self._experiences.get(t, [])) > 0
        )
        types_with_success = sum(
            1
            for t in real_types
            if self.get_success_rate(t) > 0
        )

        per_type: dict[str, Any] = {}
        for htype in real_types:
            entries = self._experiences.get(htype, [])
            successes = sum(1 for e in entries if e.detection_improved)
            per_type[htype.value] = {
                "total_edits": len(entries),
                "successes": successes,
                "success_rate": successes / len(entries) if entries else 0.0,
            }

        return {
            "types_with_experience": types_with_experience,
            "total_types": total_types,
            "coverage_rate": types_with_experience / total_types,
            "types_with_success": types_with_success,
            "effective_coverage": types_with_success / total_types,
            "per_type": per_type,
        }

    def clear(self) -> None:
        """Clear all experiences."""
        self._experiences.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the experience memory to a dict for persistence.

        Each experience is stored under its hallucination type key.
        """
        result: dict[str, Any] = {
            "max_per_type": self._max_per_type,
            "types": {},
        }
        for htype, entries in self._experiences.items():
            result["types"][htype.value] = [e.to_dict() for e in entries]
        return result

    def from_dict(self, data: Mapping[str, Any]) -> None:
        """Load experiences from a serialized dict (replaces current state).

        Args:
            data: A dict previously produced by ``to_dict()``.
        """
        self.clear()
        self._max_per_type = int(data.get("max_per_type", 50))
        types = data.get("types") or {}
        for type_name, entry_list in types.items():
            try:
                htype = HallucinationType(type_name)
            except ValueError:
                continue
            for entry_data in entry_list:
                if not isinstance(entry_data, Mapping):
                    continue
                experience = TypeConditionedExperience(
                    hallucination_type=htype,
                    evolution_direction=str(entry_data.get("evolution_direction") or ""),
                    module_targeted=str(entry_data.get("module_targeted") or "unknown"),
                    strategy_used=str(entry_data.get("strategy_used") or "generic"),
                    detection_improved=bool(entry_data.get("detection_improved", False)),
                    source_case_id=str(entry_data.get("source_case_id") or ""),
                    timestamp=float(entry_data.get("timestamp", 0.0)),
                    metadata=dict(entry_data.get("metadata") or {}),
                    feature_description=sanitize_feature_description(
                        str(entry_data.get("feature_description") or "")
                    ),
                    type_confidence=normalize_type_confidence(
                        entry_data.get("type_confidence")
                    ),
                )
                self._experiences[htype].append(experience)


@dataclass
class TypeAwareFitnessAssessment:
    """Assessment result for type-aware fitness evaluation.

    Contains per-type fitness scores, Decisive Accuracy × Coverage Rate,
    and recommendations for further evolution.
    """

    overall_improved: bool
    decisive_accuracy: float
    coverage_rate: float
    dacr_score: float  # Decisive Accuracy × Coverage Rate
    per_type_fitness: dict[str, dict[str, Any]]
    type_specific_improvements: dict[str, list[str]]
    type_specific_regressions: dict[str, list[str]]
    global_fitness: str  # "strong" / "weak" / "none"
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_improved": self.overall_improved,
            "decisive_accuracy": self.decisive_accuracy,
            "coverage_rate": self.coverage_rate,
            "dacr_score": self.dacr_score,
            "per_type_fitness": self.per_type_fitness,
            "type_specific_improvements": self.type_specific_improvements,
            "type_specific_regressions": self.type_specific_regressions,
            "global_fitness": self.global_fitness,
            "recommendation": self.recommendation,
        }


@dataclass
class TypeAwareFitness:
    """Type-aware fitness evaluation for HTC-TES v3.

    Evaluates skill edits with per-hallucination-type resolution,
    producing Decisive Accuracy × Coverage Rate as the primary metric.

    Key evaluation dimensions:
    1. Decisive Accuracy: Accuracy only on cases where the skill made
       a decisive judgment (not abstained/skipped)
    2. Coverage Rate: What fraction of hallucination types have
       detection experience
    3. Per-type fitness: Improvement tracked per hallucination type
    4. Global fitness: Overall improvement assessment
    """

    _experience_memory: TypeExperienceMemory = field(
        default_factory=TypeExperienceMemory
    )

    def assess_edit(
        self,
        source_case_id: str,
        hallucination_type: HallucinationType,
        evolution_direction: str,
        detection_before: Mapping[str, Any],
        detection_after: Mapping[str, Any],
        type_result: TypeClassificationResult,
        target_module: str | None = None,
        override_success: bool | None = None,
        feature_description: str | None = None,
        attempt_metadata: Mapping[str, Any] | None = None,
    ) -> TypeAwareFitnessAssessment:
        """Assess a skill edit's fitness for a specific hallucination type.

        Args:
            source_case_id: The case ID being tested.
            hallucination_type: The hallucination type classified.
            evolution_direction: The direction of evolution.
            detection_before: Detection result before the edit.
            detection_after: Detection result after the edit.
            type_result: The type classification result.
            target_module: The module that was edited. If None, falls back
                to ``type_result`` evidence or "unknown". The detection_after
                dict does NOT contain target_module (it is a PACA-TCL output),
                so this parameter is the correct source.
            override_success: When provided, overrides the computed ``improved``
                flag. This allows the caller (BCCSE) to pass its own fitness
                decision (based on source-level assessment) as the success
                signal, ensuring HTC-TES experience memory records consistent
                success/failure with the BCCSE batch commit decision.

        Returns:
            TypeAwareFitnessAssessment with fitness evaluation.
        """
        # Compute per-type improvement
        before_state = resolve_evolution_state(detection_before)
        after_state = resolve_evolution_state(detection_after)

        direction_correct = self._check_direction_improvement(
            before_state, after_state, evolution_direction
        )

        # Decisive accuracy: was the after-result decisive?
        is_decisive = after_state in ("verified_error", "verified_clean")
        decisive_correct = self._check_decisive_correctness(after_state, evolution_direction)

        # ================================================================
        # Three-state process evaluation (replaces boolean improved)
        # ================================================================
        # Instead of a single boolean "improved", we classify the edit's
        # effect into three states:
        #   - "improvement": the edit moved toward a decisive, correct state
        #   - "regression": the edit moved toward unresolved or wrong state
        #   - "no_effect": the state didn't change
        #
        # This is architecturally superior to boolean improved because:
        # 1. no_effect is distinguished from regression (allowing the
        #    evolution loop to allow no_effect add_rules into shadow)
        # 2. regression requires active worsening, not just "not improvement"
        # 3. The fitness level integrates the process state holistically
        if direction_correct and decisive_correct is True:
            process_state = "improvement"
        elif direction_correct and decisive_correct is None:
            # Self-supervised resolution is useful directional evidence, but
            # it is not correctness evidence and must not inflate accuracy.
            process_state = "directional_improvement"
        elif after_state == "unresolved" and before_state != "unresolved":
            process_state = "regression"  # decisive → indecisive
        elif after_state != before_state and not decisive_correct:
            process_state = "regression"  # wrong direction
        elif after_state == before_state:
            process_state = "no_effect"
        else:
            # Reaching here requires after_state != before_state AND a truthy
            # decisive_correct (the preceding branch already claimed every
            # falsy case), so the old ``"improvement" if decisive_correct
            # else "regression"`` ternary had a provably dead else-half.
            #
            # What survives is the direction/state bookkeeping disagreement:
            # the edit landed on the polarity the gold-implied direction was
            # aiming for, but the recorded direction implies a different
            # before-state than the one actually replayed. In production the
            # direction is derived from the same row as ``detection_before``,
            # so the two agree and this branch is unreachable; it is kept as
            # a defensive classification for callers that pass a direction
            # inferred from a different snapshot.
            process_state = "improvement"

        improved = process_state == "improvement"

        # FIX (C1): when the caller (BCCSE) provides its own fitness decision,
        # use it as the authoritative success flag so HTC-TES experience
        # memory stays consistent with the batch commit decision.
        if override_success is not None:
            # A caller may confirm a directional/no-effect source improvement,
            # but it cannot turn an independently observed regression into a
            # successful typed experience.
            improved = bool(override_success) and process_state != "regression"

        # Determine the targeted module from the correct source.
        if target_module:
            module_targeted = target_module
        else:
            module_targeted = str(
                type_result.evidence.get("module_signal")
                or type_result.evidence.get("target_module")
                or "unknown"
            )
        strategy = self._infer_strategy(detection_after, module_targeted)
        feature_description = (
            feature_description.strip()
            if feature_description and feature_description.strip()
            else self._describe_features(type_result)
        )

        self._experience_memory.add_experience(
            hallucination_type=hallucination_type,
            evolution_direction=evolution_direction,
            module_targeted=module_targeted,
            strategy_used=strategy,
            detection_improved=improved,
            source_case_id=source_case_id,
            metadata={
                "before_state": before_state,
                "after_state": after_state,
                "type_confidence": normalize_type_confidence(
                    type_result.confidence
                ),
                "decisive": is_decisive,
                "decisive_correct": decisive_correct,
                "direction_correct": direction_correct,
                "process_state": process_state,
                **dict(attempt_metadata or {}),
            },
            feature_description=feature_description,
            type_confidence=type_result.confidence,
        )

        # Compute metrics
        coverage = self._experience_memory.get_type_coverage()
        decisive_accuracy = self._compute_decisive_accuracy(hallucination_type)
        coverage_rate = coverage["effective_coverage"]
        dacr_score = decisive_accuracy * coverage_rate

        # Per-type fitness
        per_type_fitness = self._compute_per_type_fitness(hallucination_type, improved)

        # Determine global fitness level using three-state process
        global_fitness = self._determine_global_fitness_v2(
            process_state, decisive_accuracy, coverage_rate
        )

        # Generate recommendation
        recommendation = self._generate_recommendation(
            global_fitness, hallucination_type, coverage
        )

        return TypeAwareFitnessAssessment(
            overall_improved=improved,
            decisive_accuracy=decisive_accuracy,
            coverage_rate=coverage_rate,
            dacr_score=dacr_score,
            per_type_fitness={hallucination_type.value: per_type_fitness},
            type_specific_improvements={
                hallucination_type.value: [
                    f"Direction: {evolution_direction}",
                    f"State: {before_state} → {after_state}",
                    f"Decisive: {is_decisive}",
                    f"Process: {process_state}",
                ]
                if improved
                else []
            },
            type_specific_regressions={
                hallucination_type.value: [
                    f"Direction: {evolution_direction}",
                    f"State: {before_state} → {after_state}",
                    f"Process: {process_state}",
                ]
                if process_state == "regression"
                else []
            },
            global_fitness=global_fitness,
            recommendation=recommendation,
        )

    @staticmethod
    def _describe_features(type_result: TypeClassificationResult) -> str:
        features = type_result.trajectory_features
        if features is None:
            return "features_unavailable"
        parts = [
            f"state={features.state}",
            f"candidates={features.candidate_count}",
            f"kinds={','.join(features.candidate_kinds) or 'none'}",
            f"axes={','.join(features.axes) or 'none'}",
            f"tool_action={features.has_tool_action}",
            f"omission={features.has_omission}",
            f"adoption={features.has_adoption}",
            f"coverage={features.prosecution_coverage_state}/{features.defense_coverage_state}",
            f"crs_source={features.crs_source}",
        ]
        return " | ".join(parts)

    def _check_direction_improvement(
        self, before_state: str, after_state: str, direction: str
    ) -> bool | None:
        """Check if the edit moved detection in the intended direction.

        A direction improvement requires the after-state to be a DECISIVE
        judgment in the intended direction. Moving to ``unresolved`` is
        never an improvement — it is a regression from decisive to
        indecisive (the original bug treated unresolved as improvement).
        """
        if direction == "error_to_clean":
            return before_state == "verified_error" and after_state == "verified_clean"
        elif direction == "clean_to_error":
            return before_state == "verified_clean" and after_state == "verified_error"
        elif direction == "unresolved_to_clean":
            return before_state == "unresolved" and after_state == "verified_clean"
        elif direction == "unresolved_to_error":
            return before_state == "unresolved" and after_state == "verified_error"
        elif direction == "unresolved_to_decisive":
            return before_state == "unresolved" and after_state in {
                "verified_error", "verified_clean",
            }
        # no_evolution or unknown direction: only count as improvement if
        # the state became decisive (non-unresolved) and changed.
        if direction == "no_evolution":
            return False
        return before_state != after_state and after_state != "unresolved"

    def _check_decisive_correctness(
        self, after_state: str, direction: str
    ) -> bool | None:
        """Check if the after-state is a correct decisive judgment.

        For ``no_evolution`` direction, there is no intended target state,
        so we cannot call the after-state "correct" — return False to be
        conservative. The three-state process evaluation will then classify
        state changes under no_evolution as regression, not improvement.
        """
        if after_state == "unresolved":
            return False
        if direction == "error_to_clean":
            return after_state == "verified_clean"
        elif direction == "clean_to_error":
            return after_state == "verified_error"
        elif direction == "unresolved_to_clean":
            return after_state == "verified_clean"
        elif direction == "unresolved_to_error":
            return after_state == "verified_error"
        elif direction == "unresolved_to_decisive":
            return None
        # no_evolution or unknown direction: conservative — not correct.
        return False

    def _compute_decisive_accuracy(
        self, hallucination_type: HallucinationType
    ) -> float:
        """Compute Decisive Accuracy for a hallucination type.

        Decisive Accuracy = correct_decisive_judgments / total_decisive_judgments
        where a decisive judgment is a verified_error or verified_clean result.

        FIX: "correct" is determined by ``decisive_correct`` in metadata,
        NOT by ``detection_improved`` (which is the AND of direction_correct
        and decisive_correct and can be False even when the decisive
        judgment itself is correct, e.g. for no_evolution cases).
        """
        entries = self._experience_memory.get_type_experiences(hallucination_type)
        decisive_entries = [
            e
            for e in entries
            if e.metadata.get("decisive", False)
        ]
        if not decisive_entries:
            return 0.0
        scored = [
            e for e in decisive_entries
            if e.metadata.get("decisive_correct") is not None
        ]
        if not scored:
            return 0.0
        correct = sum(
            bool(e.metadata.get("decisive_correct")) for e in scored
        )
        return correct / len(scored)

    def _compute_per_type_fitness(
        self,
        hallucination_type: HallucinationType,
        improved: bool,
    ) -> dict[str, Any]:
        """Compute per-type fitness details."""
        entries = self._experience_memory.get_type_experiences(hallucination_type)
        success_rate = self._experience_memory.get_success_rate(hallucination_type)
        top_modules = self._experience_memory.get_top_modules(hallucination_type)
        strategy_effectiveness = (
            self._experience_memory.get_strategy_effectiveness(hallucination_type)
        )

        return {
            "total_experiences": len(entries),
            "success_rate": success_rate,
            "current_improved": improved,
            "top_modules": top_modules,
            "strategy_effectiveness": strategy_effectiveness,
        }

    def _determine_global_fitness_v2(
        self,
        process_state: str,
        decisive_accuracy: float,
        coverage_rate: float,
    ) -> str:
        """Determine global fitness from three-state process evaluation.

        Architectural principle: fitness is determined by the PROCESS STATE
        (improvement / regression / no_effect) combined with the type's
        historical decisive_accuracy and coverage. This replaces the old
        boolean improved logic.

        - "improvement" + high accuracy + high coverage → "strong"
        - "improvement" or "no_effect" with decent accuracy → "weak"
          (no_effect is acceptable for add_rule: the rule didn't hurt
          existing detection but may help future cases)
        - "regression" or very low accuracy → "none"
        """
        if process_state == "regression":
            return "none"
        if process_state == "directional_improvement":
            # Label-free decisiveness is process evidence only. It may be
            # retained in typed memory but cannot bootstrap strong/weak
            # correctness fitness by itself.
            return "none"
        if process_state == "no_effect":
            # no_effect is acceptable when the type has some experience
            # (the rule may help future cases even if this case didn't change)
            return "weak" if decisive_accuracy >= 0.3 else "none"
        # process_state == "improvement"
        if decisive_accuracy > 0.6 and coverage_rate > 0.5:
            return "strong"
        # An improving edit should never be "none" — accept it as "weak"
        # even if the type's history is sparse, so the type can bootstrap
        # out of the cold-start trap.
        return "weak"

    def _generate_recommendation(
        self,
        fitness_level: str,
        hallucination_type: HallucinationType,
        coverage: dict[str, Any],
    ) -> str:
        """Generate evolution recommendation."""
        if fitness_level == "strong":
            return (
                f"Strong improvement for type '{hallucination_type.value}'. "
                f"Continue exploring related types. Coverage: {coverage['coverage_rate']:.1%}"
            )
        elif fitness_level == "weak":
            return (
                f"Weak improvement for type '{hallucination_type.value}'. "
                f"Try alternative editing strategies. Coverage: {coverage['coverage_rate']:.1%}"
            )
        else:
            return (
                f"No improvement for type '{hallucination_type.value}'. "
                f"Consider structural changes to rules. Coverage: {coverage['coverage_rate']:.1%}"
            )

    def _infer_strategy(
        self, detection_result: Mapping[str, Any], target_module: str = ""
    ) -> str:
        """Infer the editing strategy from detection result changes.

        ``target_module`` is passed explicitly because ``detection_result``
        (a PACA-TCL output) does not contain it.
        """
        strategy_signals: list[str] = []

        # Check for module attribution changes
        if target_module:
            strategy_signals.append(f"module:{target_module}")

        # Check for state transition
        state = resolve_evolution_state(detection_result)
        if state == "verified_error":
            strategy_signals.append("error_detection")
        elif state == "verified_clean":
            strategy_signals.append("clean_detection")
        elif state == "unresolved":
            strategy_signals.append("uncertain")

        return "_".join(strategy_signals) if strategy_signals else "generic"

    def get_experience_memory(self) -> TypeExperienceMemory:
        """Get the underlying experience memory for direct access."""
        return self._experience_memory
