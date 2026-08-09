"""BCCSE v5 — Shared type definitions.

Contains dataclasses and type aliases shared across the BCCSE evolution
package. These are kept here to avoid circular imports.

Note: EvolutionStep is defined in evolution_runner.py (not here) because
it has fields specific to the evolution loop that are not shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExperienceRecord:
    """Compiler-only view of one detection experience.

    Stores the anonymized record of a single case's detection outcome
    for the evolution compiler, without raw LLM response text.
    """

    record_key: str
    partition: str
    gold_positive: bool | None
    predicted_positive: bool | None
    gold_responsible_ref: str | None
    predicted_responsible_ref: str | None
    candidate_origin_refs: tuple = ()
    analysis_dimension: str = ""
    origin_relation: str = ""
    evidence_obligation: str = ""
    clean_boundary: str = ""
    certificate_valid: bool = False
    unresolved: bool = False
    model_calls: int = 0
    latency_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_key": self.record_key,
            "partition": self.partition,
            "gold_positive": self.gold_positive,
            "predicted_positive": self.predicted_positive,
            "gold_responsible_ref": self.gold_responsible_ref,
            "predicted_responsible_ref": self.predicted_responsible_ref,
            "candidate_origin_refs": list(self.candidate_origin_refs),
            "analysis_dimension": self.analysis_dimension,
            "origin_relation": self.origin_relation,
            "evidence_obligation": self.evidence_obligation,
            "clean_boundary": self.clean_boundary,
            "certificate_valid": self.certificate_valid,
            "unresolved": self.unresolved,
            "model_calls": self.model_calls,
            "latency_seconds": self.latency_seconds,
            "metadata": self.metadata,
        }
