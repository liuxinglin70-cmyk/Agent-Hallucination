"""SEHD v6 Core — Unified API for detection and evolution.

Import top-level components from here:
    from sehd_v6.core import run_online_evolution, adjudicate_case
"""

from .bccse import (
    run_online_evolution,
    SkillGenome,
    create_baseline_genome,
    ErrorType,
    attribute_failure,
)
from .paca_tcl import adjudicate_case, evaluate
from .htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeConditionedEvolution,
    TypeAwareFitness,
)

__all__ = [
    # BCCSE
    "run_online_evolution",
    "SkillGenome",
    "create_baseline_genome",
    "ErrorType",
    "attribute_failure",
    # PACA-TCL
    "adjudicate_case",
    "evaluate",
    # HTC-TES
    "HallucinationType",
    "HallucinationTypeClassifier",
    "TypeConditionedEvolution",
    "TypeAwareFitness",
]
