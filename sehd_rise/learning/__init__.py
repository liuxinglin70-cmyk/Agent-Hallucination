"""SEHD-v7 learning protocol package: the five first-class learning types.

The public surface is ``protocol``; nothing else in this package imports the
causal/skills/loop layers beyond the episode record, so the learning view stays
a projection over the framework rather than a second authority.
"""

from __future__ import annotations

from sehd_rise.learning.protocol import (
    ArchivedCandidate,
    CANDIDATE_STAGES,
    CandidateArchive,
    EvaluationEvidence,
    EvolutionMemory,
    ExecutableCandidate,
    FAILURE_CLASSES,
    FAILURE_CLASS_BINDING_AMBIGUOUS,
    FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
    FAILURE_CLASS_ENTITY_NOT_FOUND,
    FAILURE_CLASS_EXECUTABLE,
    FAILURE_CLASS_SEARCH_FAILURE,
    FAILURE_CLASS_STRUCTURAL_INVALID,
    FailureObservation,
    RepairIntent,
    SCHEMA_VERSION,
    STAGE_BINDABLE,
    STAGE_EXECUTABLE,
    STAGE_RAW_HYPOTHESIS,
    STAGE_SCHEMA_VALID,
    classify_candidate_failure,
)

__all__ = [
    "ArchivedCandidate",
    "CANDIDATE_STAGES",
    "CandidateArchive",
    "EvaluationEvidence",
    "EvolutionMemory",
    "ExecutableCandidate",
    "FAILURE_CLASSES",
    "FAILURE_CLASS_BINDING_AMBIGUOUS",
    "FAILURE_CLASS_DSL_NOT_REPRESENTABLE",
    "FAILURE_CLASS_ENTITY_NOT_FOUND",
    "FAILURE_CLASS_EXECUTABLE",
    "FAILURE_CLASS_SEARCH_FAILURE",
    "FAILURE_CLASS_STRUCTURAL_INVALID",
    "FailureObservation",
    "RepairIntent",
    "SCHEMA_VERSION",
    "STAGE_BINDABLE",
    "STAGE_EXECUTABLE",
    "STAGE_RAW_HYPOTHESIS",
    "STAGE_SCHEMA_VALID",
    "classify_candidate_failure",
]
