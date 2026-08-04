"""Stable label-free projection from PACA-TCL output to self-evolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EvolutionDetection:
    """Detector facts consumed by BCCSE and HTC-TES."""

    state: str
    runtime_valid: bool
    decision_authority: str
    responsible_event_id: str | None
    responsible_step: int | str | None
    census: tuple[Mapping[str, Any], ...]
    prosecution: Mapping[str, Any]
    defense: Mapping[str, Any]
    proof_adjudication: Mapping[str, Any]
    crs_scores: Mapping[str, float]
    crs_aggregate: float
    crs_source: str
    crs_complete: bool
    crs_projection_confidence: float
    diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the compatibility shape expected by existing evolution code."""
        return {
            "schema_version": "agenthallu.evolution_detection.v1",
            "state": self.state,
            "occurrence_state": self.state,
            "runtime_valid": self.runtime_valid,
            "decision_authority": self.decision_authority,
            "responsible_event_id": self.responsible_event_id,
            "responsible_step": self.responsible_step,
            "census": [dict(row) for row in self.census],
            "prosecution": dict(self.prosecution),
            "defense": dict(self.defense),
            "proof_adjudication": dict(self.proof_adjudication),
            "crs_scores": dict(self.crs_scores),
            "crs_aggregate": self.crs_aggregate,
            "crs_source": self.crs_source,
            "crs_complete": self.crs_complete,
            "crs_projection_confidence": self.crs_projection_confidence,
            "diagnostics": dict(self.diagnostics),
        }


def project_for_evolution(result: Mapping[str, Any]) -> EvolutionDetection:
    """Validate and normalize one detector result for label-free evolution."""
    from ..bccse.signal_adapter import extract_crs_signal, resolve_evolution_state

    runtime_valid = result.get("runtime_valid") is True
    authority = str(result.get("decision_authority") or "invalid")
    state = resolve_evolution_state(result)
    proof = result.get("proof_adjudication")
    proof = proof if isinstance(proof, Mapping) else {}
    prosecution = result.get("combined_prosecution") or result.get("prosecution") or {}
    defense = result.get("combined_defense") or result.get("defense") or {}
    census = result.get("census") or []
    if not isinstance(prosecution, Mapping) or not isinstance(defense, Mapping):
        raise ValueError("detector proof sides must be objects")
    if not isinstance(census, list) or not all(isinstance(row, Mapping) for row in census):
        raise ValueError("detector census must be a list of objects")
    crs = extract_crs_signal(result)
    health = result.get("evidence_health")
    health = health if isinstance(health, Mapping) else {}
    providers = result.get("providers")
    providers = providers if isinstance(providers, Mapping) else {}
    focused = providers.get("focused_prosecution")
    focused = focused if isinstance(focused, Mapping) else {}
    focused_quarantined = sorted(
        str(candidate_id) for candidate_id, meta in focused.items()
        if isinstance(meta, Mapping) and meta.get("all_quarantined")
    )
    appeal_error = providers.get("appeal_error")
    appeal_error = appeal_error if isinstance(appeal_error, Mapping) else {}
    authority_reasons = result.get("authority_failure_reasons") or []
    diagnostics = {
        "focused_quarantined_candidate_ids": focused_quarantined,
        "focused_quarantined_candidate_count": int(
            health.get("focused_prosecution_quarantined_candidate_count")
            or len(focused_quarantined)
        ),
        "appeal_required": bool(result.get("appeal_candidates")),
        "appeal_status": str(
            proof.get("appeal_status")
            or health.get("appeal_status")
            or "not_required"
        ),
        "appeal_candidate_count": int(
            health.get("appeal_candidate_count")
            or len(result.get("appeal_candidates") or [])
        ),
        "appeal_error_type": str(appeal_error.get("error_type") or ""),
        "appeal_error": str(appeal_error.get("error") or "")[:500],
        "consensus_gate_applied": bool(proof.get("consensus_gate_applied")),
        "consensus_gate_blocked_count": len(
            proof.get("consensus_gate_blocked_ids") or []
        ),
        "localization_status": str(
            proof.get("localization_status")
            or health.get("localization_status")
            or "not_applicable"
        ),
        "counterfactual_rankings_used": bool(
            proof.get("counterfactual_rankings_used")
        ),
        "required_candidate_count": int(
            proof.get("required_candidate_count") or 0
        ),
        "resolved_candidate_count": int(
            proof.get("resolved_candidate_count") or 0
        ),
        "authority_failure_reasons": [
            dict(reason) for reason in authority_reasons
            if isinstance(reason, Mapping)
        ],
        "verdict_authority_scope": str(
            proof.get("verdict_authority_scope") or authority
        ),
    }
    return EvolutionDetection(
        state=state,
        runtime_valid=runtime_valid,
        decision_authority=authority,
        responsible_event_id=(
            str(result["responsible_event_id"])
            if result.get("responsible_event_id") is not None else None
        ),
        responsible_step=result.get("responsible_step"),
        census=tuple(census),
        prosecution=prosecution,
        defense=defense,
        proof_adjudication=proof,
        crs_scores=crs.scores,
        crs_aggregate=crs.aggregate,
        crs_source=crs.source,
        crs_complete=crs.complete,
        crs_projection_confidence=crs.projection_confidence,
        diagnostics=diagnostics,
    )
