"""SEHD v6 Core — Unified API for detection and evolution.

Import top-level components from here:
    from sehd_v6.core import run_online_evolution, adjudicate_case

Architecture:
    PACA-TCL v3.5 (frozen detector base)
        ↕ genome injection
    BCCSE v5 (self-evolution framework)
        ↕ type conditioning
    HTC-TES v3 (hallucination-typed conditioning layer)
"""

from .bccse import (
    run_online_evolution,
    SkillGenome,
    create_baseline_genome,
    ErrorType,
    attribute_failure,
    AttributionSignal,
    SkillEdit,
    SkillOp,
    apply_edit,
    validate_edit,
    StructuralGate,
    create_structural_gate,
)
from .paca_tcl import adjudicate_case, evaluate
from .htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeClassificationResult,
    TypeConditionedEvolution,
    TypeAwareFitness,
    classify_hallucination_type,
)

__all__ = [
    # BCCSE — Self-evolution
    "run_online_evolution",
    "SkillGenome",
    "create_baseline_genome",
    "SkillEdit",
    "SkillOp",
    "apply_edit",
    "validate_edit",
    "ErrorType",
    "AttributionSignal",
    "attribute_failure",
    "StructuralGate",
    "create_structural_gate",
    # PACA-TCL — Frozen detector
    "adjudicate_case",
    "evaluate",
    # HTC-TES — Type conditioning
    "HallucinationType",
    "HallucinationTypeClassifier",
    "TypeClassificationResult",
    "TypeConditionedEvolution",
    "TypeAwareFitness",
    "classify_hallucination_type",
]
