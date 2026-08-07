"""HTC-TES v3 — Hallucination-Typed Conditioning for Taxonomy-conditioned Evolution of Skills.

Three-layer conditioning framework for agent hallucination detection:
- Macro: Taxonomy-conditioned evolution (input/output type routing)
- Meso: Process-level fitness evaluation (stage-aware scoring)
- Micro: Structured rule spec with typed experience memory

This module implements the HTC-TES v3 methodology that addresses:
1. Evolution direction conflict (error vs clean) via bidirectional learning
2. Hallucination-type-specific detection experience learning
3. Type-aware fitness that measures progress per hallucination category
4. Decisive Accuracy × Coverage Rate metric for comprehensive evaluation
"""

from .type_classifier import (
    HallucinationType,
    HallucinationTypeClassifier,
    TrajectoryFeatures,
    TypeClassificationResult,
)

from .type_conditioned_evolution import TypeConditionedEvolution

from .type_aware_fitness import (
    TypeAwareFitness,
    TypeAwareFitnessAssessment,
    TypeConditionedExperience,
    TypeExperienceMemory,
)

__all__ = [
    "HallucinationType",
    "HallucinationTypeClassifier",
    "TrajectoryFeatures",
    "TypeClassificationResult",
    "TypeConditionedEvolution",
    "TypeAwareFitness",
    "TypeAwareFitnessAssessment",
    "TypeConditionedExperience",
    "TypeExperienceMemory",
]
