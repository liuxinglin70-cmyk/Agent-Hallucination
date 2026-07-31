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
        experience = TypeConditionedExperience(
            hallucination_type=hallucination_type,
            evolution_direction=evolution_direction,
            module_targeted=module_targeted,
            strategy_used=strategy_used,
            detection_improved=detection_improved,
            source_case_id=source_case_id,
            metadata=metadata or {},
        )
        entries = self._experiences[hallucination_type]
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

        Returns:
            Coverage statistics including which types have experience.
        """
        total_types = len(HallucinationType)
        types_with_experience = sum(
            1
            for t in HallucinationType
            if len(self._experiences.get(t, [])) > 0
        )
        types_with_success = sum(
            1
            for t in HallucinationType
            if self.get_success_rate(t) > 0
        )

        per_type: dict[str, Any] = {}
        for htype in HallucinationType:
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
    ) -> TypeAwareFitnessAssessment:
        """Assess a skill edit's fitness for a specific hallucination type.

        Args:
            source_case_id: The case ID being tested.
            hallucination_type: The hallucination type classified.
            evolution_direction: The direction of evolution.
            detection_before: Detection result before the edit.
            detection_after: Detection result after the edit.
            type_result: The type classification result.

        Returns:
            TypeAwareFitnessAssessment with fitness evaluation.
        """
        # Compute per-type improvement
        before_state = str(detection_before.get("state") or "unresolved")
        after_state = str(detection_after.get("state") or "unresolved")

        direction_correct = self._check_direction_improvement(
            before_state, after_state, evolution_direction
        )

        # Decisive accuracy: was the after-result decisive?
        is_decisive = after_state in ("verified_error", "verified_clean")
        decisive_correct = self._check_decisive_correctness(after_state, evolution_direction)

        # Record experience
        improved = direction_correct or decisive_correct
        strategy = self._infer_strategy(detection_after)

        self._experience_memory.add_experience(
            hallucination_type=hallucination_type,
            evolution_direction=evolution_direction,
            module_targeted=str(detection_after.get("target_module") or "unknown"),
            strategy_used=strategy,
            detection_improved=improved,
            source_case_id=source_case_id,
            metadata={
                "before_state": before_state,
                "after_state": after_state,
                "type_confidence": type_result.confidence,
                "decisive": is_decisive,
            },
        )

        # Compute metrics
        coverage = self._experience_memory.get_type_coverage()
        decisive_accuracy = self._compute_decisive_accuracy(hallucination_type)
        coverage_rate = coverage["effective_coverage"]
        dacr_score = decisive_accuracy * coverage_rate

        # Per-type fitness
        per_type_fitness = self._compute_per_type_fitness(hallucination_type, improved)

        # Determine global fitness level
        global_fitness = self._determine_global_fitness(
            improved, decisive_accuracy, coverage_rate
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
                ]
                if improved
                else []
            },
            type_specific_regressions={
                hallucination_type.value: [
                    f"Direction: {evolution_direction}",
                    f"State: {before_state} → {after_state}",
                ]
                if not improved
                else []
            },
            global_fitness=global_fitness,
            recommendation=recommendation,
        )

    def _check_direction_improvement(
        self, before_state: str, after_state: str, direction: str
    ) -> bool:
        """Check if the edit moved detection in the intended direction."""
        if direction == "error_to_clean":
            return before_state == "verified_error" and after_state in (
                "verified_clean",
                "unresolved",
            )
        elif direction == "clean_to_error":
            return before_state == "verified_clean" and after_state in (
                "verified_error",
                "unresolved",
            )
        elif direction == "unresolved_to_clean":
            return before_state == "unresolved" and after_state == "verified_clean"
        elif direction == "unresolved_to_error":
            return before_state == "unresolved" and after_state == "verified_error"
        return before_state != after_state and after_state != "unresolved"

    def _check_decisive_correctness(
        self, after_state: str, direction: str
    ) -> bool:
        """Check if the after-state is a correct decisive judgment."""
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
        return True

    def _compute_decisive_accuracy(
        self, hallucination_type: HallucinationType
    ) -> float:
        """Compute Decisive Accuracy for a hallucination type.

        Decisive Accuracy = correct_decisive_judgments / total_decisive_judgments
        where a decisive judgment is a verified_error or verified_clean result.
        """
        entries = self._experience_memory.get_type_experiences(hallucination_type)
        decisive_entries = [
            e
            for e in entries
            if e.metadata.get("decisive", False)
        ]
        if not decisive_entries:
            return 0.0
        correct = sum(
            1 for e in decisive_entries if e.detection_improved
        )
        return correct / len(decisive_entries)

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

    def _determine_global_fitness(
        self,
        improved: bool,
        decisive_accuracy: float,
        coverage_rate: float,
    ) -> str:
        """Determine global fitness level."""
        if not improved and decisive_accuracy < 0.3:
            return "none"
        if improved and decisive_accuracy > 0.6 and coverage_rate > 0.5:
            return "strong"
        if improved or decisive_accuracy > 0.4:
            return "weak"
        return "none"

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

    def _infer_strategy(self, detection_result: Mapping[str, Any]) -> str:
        """Infer the editing strategy from detection result changes."""
        strategy_signals: list[str] = []

        # Check for module attribution changes
        old_module = str(detection_result.get("target_module") or "")
        if old_module:
            strategy_signals.append(f"module:{old_module}")

        # Check for state transition
        state = str(detection_result.get("state") or "")
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
