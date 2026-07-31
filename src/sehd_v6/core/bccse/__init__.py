"""BCCSE v5 — Behavior-Constrained Causal Skill Evolution.

Public API for the self-evolution framework.
Import from this package rather than individual modules.
"""

from .types import (
    EvaluationOutcome,
    ExperienceRecord,
    FailureEpisode,
    anonymous_ref,
)
from .genome import (
    MODULE_IDS,
    MODULE_DISPLAY_NAMES,
    MODULE_STAGE,
    SkillGenome,
    SkillModule,
    SkillEdit,
    SkillOp,
    create_baseline_genome,
    apply_edit,
    validate_edit,
    semantic_overlap,
    FITNESS_STATUS_NONE,
    FITNESS_STATUS_WEAK,
    FITNESS_STATUS_STRONG,
)
from .step_attributor import (
    ErrorType,
    ReflectionType,
    TriggerSource,
    AttributionSignal,
    attribute_failure,
)
from .fitness import FitnessAssessment, assess_source_fitness, task_state_score
from .safety_gate import StructuralGate, create_structural_gate
from .promotion_gates import G1_CHECKS, MetricSnapshot, ReplayEvidence
from .evolution_runner import (
    EvolutionStep,
    OnlineEvolutionResult,
    ProvisionalEdit,
    run_online_evolution,
)

__all__ = [
    # Types
    "EvaluationOutcome",
    "ExperienceRecord",
    "FailureEpisode",
    "anonymous_ref",
    # Genome
    "MODULE_IDS",
    "MODULE_DISPLAY_NAMES",
    "MODULE_STAGE",
    "SkillGenome",
    "SkillModule",
    "SkillEdit",
    "SkillOp",
    "create_baseline_genome",
    "apply_edit",
    "validate_edit",
    "semantic_overlap",
    "FITNESS_STATUS_NONE",
    "FITNESS_STATUS_WEAK",
    "FITNESS_STATUS_STRONG",
    # Attribution
    "ErrorType",
    "ReflectionType",
    "TriggerSource",
    "AttributionSignal",
    "attribute_failure",
    # Fitness
    "FitnessAssessment",
    "assess_source_fitness",
    "task_state_score",
    # Gate
    "StructuralGate",
    "create_structural_gate",
    # Promotion
    "G1_CHECKS",
    "MetricSnapshot",
    "ReplayEvidence",
    # Evolution
    "EvolutionStep",
    "OnlineEvolutionResult",
    "ProvisionalEdit",
    "run_online_evolution",
]
