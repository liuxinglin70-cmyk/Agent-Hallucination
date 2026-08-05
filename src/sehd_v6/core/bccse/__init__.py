"""BCCSE v5 — Behavior-Constrained Causal Skill Evolution.

Public API for the self-evolution framework.
Import from this package rather than individual modules.

Import-cycle note (v5.6): ``bccse`` and ``htc_tes`` are mutually dependent
at package level — ``htc_tes`` needs the ``bccse`` leaf modules
(``genome``, ``signal_adapter``) while ``bccse.evolution_runner`` needs the
``htc_tes`` type layer. Importing ``evolution_runner`` eagerly here made
that cycle order-dependent: ``import ...core.htc_tes`` as the very first
import raised ``ImportError`` (partially initialized module), while
importing ``bccse`` first happened to work. ``run_online_evolution`` and
friends are therefore resolved lazily via PEP 562 ``__getattr__``, which
keeps this package's public API unchanged and makes both orders valid.
"""

from typing import Any

from .types import (
    EvaluationOutcome,
    ExperienceRecord,
    ReplayEpisode,
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
# NOTE: ``evolution_runner`` is NOT imported here. It imports ``htc_tes``,
# which imports back into this package; importing it eagerly would make the
# package cycle order-dependent (see module docstring). Its public symbols
# are exposed via ``__getattr__`` below.

__all__ = [
    # Types
    "EvaluationOutcome",
    "ExperienceRecord",
    "ReplayEpisode",
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


def __getattr__(name: str) -> Any:
    """Lazily import the evolution runner's public symbols.

    ``evolution_runner`` is the one module in this package that depends on
    ``htc_tes``, so it is imported on first access (not at package import)
    to keep the ``bccse``/``htc_tes`` package cycle order-independent.
    """
    if name in {"EvolutionStep", "OnlineEvolutionResult", "ProvisionalEdit",
                "run_online_evolution"}:
        from . import evolution_runner as runner

        return getattr(runner, name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
