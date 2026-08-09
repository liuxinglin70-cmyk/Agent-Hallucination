"""BCCSE v5 — Promotion metric structures.

Defines MetricSnapshot and ReplayEvidence dataclasses used by the
evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MetricSnapshot:
    """Snapshot of evaluation metrics for one genome against one partition."""

    macro_f1: float = 0.0
    precision: float = 0.0
    hallucination_recall: float = 0.0
    clean_fpr: float = 0.0
    responsible_step_accuracy: float = 0.0
    candidate_origin_coverage: float = 0.0
    valid_output_rate: float = 0.0
    causal_chain_validity: float = 0.0
    unresolved_rate: float = 0.0
    invalid_rate: float = 0.0
    model_calls: int = 0
    latency_seconds: float = 0.0
    cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "macro_f1": self.macro_f1,
            "precision": self.precision,
            "hallucination_recall": self.hallucination_recall,
            "clean_fpr": self.clean_fpr,
            "responsible_step_accuracy": self.responsible_step_accuracy,
            "candidate_origin_coverage": self.candidate_origin_coverage,
            "valid_output_rate": self.valid_output_rate,
            "causal_chain_validity": self.causal_chain_validity,
            "unresolved_rate": self.unresolved_rate,
            "invalid_rate": self.invalid_rate,
            "model_calls": self.model_calls,
            "latency_seconds": self.latency_seconds,
            "cost": self.cost,
        }


@dataclass
class ReplayEvidence:
    """Evidence from a paired parent/child replay evaluation."""

    targeted_error_total: int = 0
    targeted_error_corrected: int = 0
    paired_boundary_total: int = 0
    paired_boundary_regressions: int = 0
    parent_candidate_origin_coverage: float = 0.0
    child_candidate_origin_coverage: float = 0.0
    total_outputs: int = 0
    valid_outputs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "targeted_error_total": self.targeted_error_total,
            "targeted_error_corrected": self.targeted_error_corrected,
            "paired_boundary_total": self.paired_boundary_total,
            "paired_boundary_regressions": self.paired_boundary_regressions,
            "parent_candidate_origin_coverage": self.parent_candidate_origin_coverage,
            "child_candidate_origin_coverage": self.child_candidate_origin_coverage,
            "total_outputs": self.total_outputs,
            "valid_outputs": self.valid_outputs,
        }
