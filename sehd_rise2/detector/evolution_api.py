"""Evolution-facing interfaces of the frozen minimal detector.

The future ``sehd_rise2.evolution`` framework touches the detector ONLY
through this module and the surfaces it re-exports:

1. capability deployment (skill loading)
   - ``load_skill_genome`` / ``load_policy_genome``: persisted genomes load
     explicitly and fail-closed; ``api.detect`` deploys them per call.
2. data provision (Detection Truth)
   - ``project_for_evolution``: read-only projection of a detection result
     for the evolution framework's causal analysis.

Authority split (design contract): the detector owns NO evolution
authority — no causal attribution, no repairability, no fitness, no gold.
The evolution framework re-derives all of that from the projection. Gold
labels are evaluator-only: ``EVALUATOR_ONLY_FIELDS`` is the input firewall
both sides read.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise2.detector import api
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS
from sehd_rise2.detector.result import (
    decision_authority_invalid,
    resolve_evolution_state,
)
from sehd_rise2.detector.skills import load_policy_genome, load_skill_genome

# Top-level result blocks the evolution framework may read. The list is the
# frozen contract: an evolution layer must not reach for fields outside it.
EVOLUTION_RELEVANT_FIELDS: tuple[str, ...] = (
    "state",
    "decision_authority",
    "runtime_valid",
    "proof_adjudication",
    "responsible_step",
    "responsible_event_id",
    "occurrence_state",
    "incident_occurred",
    "providers",
    "authority_failure_reasons",
    "evidence_health",
    "provenance",
)


def project_for_evolution(result: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only Detection Truth projection for the evolution framework.

    Lossless: copies the frozen ``EVOLUTION_RELEVANT_FIELDS`` blocks verbatim
    and adds two resolved readings (terminal state, authority validity).
    No causal semantics are derived here — the evolution framework owns that.
    """
    projection = {
        key: result[key]
        for key in EVOLUTION_RELEVANT_FIELDS
        if key in result
    }
    projection["terminal_state"] = resolve_evolution_state(result)
    projection["decision_authority_valid"] = not decision_authority_invalid(
        result
    )
    return projection


__all__ = [
    "EVALUATOR_ONLY_FIELDS",
    "EVOLUTION_RELEVANT_FIELDS",
    "api",
    "load_policy_genome",
    "load_skill_genome",
    "project_for_evolution",
]
