"""Deterministic fixtures for the causal self-evolution loop.

Frozen by Phase 0 of the causal-experience contract.  Every preset here is
rebuilt from scratch per call (no shared mutable state) so tests can replay
them without any external provider, and repeated construction of the same
preset must yield byte-identical JSON.  The presets cover the six fixture
classes the causal loop needs:

- stable_clean: correct stable detection (no trigger, no failure shape);
- wrong_owner_field: attribution resolves to an owner whose emitted output
  field exists but is never consumed by the downstream court;
- invalid_mutation: a mutation payload that fails deterministic compile;
- compiled_no_effect: a legal edit whose source replay moves nothing;
- accepted_local_movement: a legal edit whose source replay moves an owned
  observable and closes the trigger;
- l2_policy_movement: an adjudication result whose policy evaluation moved
  the terminal edge.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.sehd_v6.core.bccse.failure_classifier import classify_failure
from src.sehd_v6.core.bccse.step_attributor import (
    AttributionSignal,
    ErrorType,
    ReflectionType,
    TriggerSource,
)
from src.sehd_v6.core.htc_tes.type_classifier import (
    HallucinationType,
    TypeClassificationResult,
)


def court_result(**overrides: Any) -> dict[str, Any]:
    value = {
        "state": "unresolved",
        "prosecution_coverage": "incomplete",
        "defense_coverage": "none",
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": 0,
        "surviving_error_certificate_ids": [],
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": [],
        "consensus_gate_applied": False,
        "consensus_gate_blocked_ids": [],
        "required_candidate_count": 1,
        "error_audits": [],
        "defense_audits": [],
        "positive_candidate_ids": [],
        "clean_covered_candidate_ids": [],
    }
    value.update(overrides)
    return value


def health_result(**overrides: Any) -> dict[str, Any]:
    value = {
        "construct_raw_candidate_count": 1,
        "construct_accepted_candidate_count": 1,
        "required_candidate_count": 1,
        "quarantined_error_certificate_count": 0,
        "quarantined_defense_certificate_count": 0,
        "focused_prosecution_quarantined_candidate_count": 0,
        "focused_prosecution_certificate_count": 0,
        "appeal_candidate_count": 0,
        "appeal_status": "",
        "construct_authority_failure_reasons": [],
    }
    value.update(overrides)
    return value


def detection_result(
    *,
    state: str,
    adjudication: Mapping[str, Any] | None = None,
    health: Mapping[str, Any] | None = None,
    census: list[dict[str, Any]] | None = None,
    prosecution: list[dict[str, Any]] | None = None,
    defense: list[dict[str, Any]] | None = None,
    responsible_step: str | None = None,
    boundary_path_state: str = "",
    runtime_valid: bool = True,
    decision_authority: str = "authoritative",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "state": state,
        "runtime_valid": runtime_valid,
        "decision_authority": decision_authority,
        "census": census or [],
        "responsible_step": responsible_step,
        "evidence_health": health_result(**dict(health or {})),
        "proof_adjudication": court_result(**dict(adjudication or {})),
        "combined_prosecution": {"candidates": prosecution or []},
        "combined_defense": {"candidates": defense or []},
        "boundary_path": {"state": boundary_path_state},
    }
    if boundary_path_state:
        value["boundary_path"] = {"state": boundary_path_state}
    return value


def stable_clean() -> dict[str, Any]:
    """Correct stable detection: a clean verdict carried by valid defense
    certificates, with no addressable failure shape."""
    adjudication = {
        "state": "verified_clean",
        "prosecution_coverage": "incomplete",
        "defense_coverage": "complete",
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": 1,
        "clean_covered_candidate_ids": ["candidate_1"],
    }
    return detection_result(
        state="verified_clean",
        adjudication=adjudication,
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        defense=[{"certificate_id": "defense_1", "candidate_id": "candidate_1"}],
        responsible_step="event_3",
    )


def missing_evidence_field() -> dict[str, Any]:
    """Unresolved terminal with incomplete prosecution: the evidence
    obligation was never established, so the classifier routes to
    evidence_obligation with reason provider_artifact_missing."""
    adjudication = {
        "state": "unresolved",
        "prosecution_coverage": "incomplete",
        "defense_coverage": "incomplete",
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": 0,
        "required_candidate_count": 1,
    }
    return detection_result(
        state="unresolved",
        adjudication=adjudication,
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_4",
    )


def malformed_certificate() -> dict[str, Any]:
    """Provider emitted error certificates the court quarantined: the
    verifier surface is malformed, so the classifier routes to
    atomic_verifier_instruction (or the module hint) with reason
    provider_artifact_malformed."""
    adjudication = {
        "state": "unresolved",
        "prosecution_coverage": "complete",
        "defense_coverage": "none",
        "valid_error_certificate_count": 0,
        "valid_defense_certificate_count": 0,
        "quarantined_error_certificate_ids": ["error_1"],
        "error_audits": [
            {
                "certificate_id": "error_1",
                "decision": "fail",
                "failed_checks": ["evidence_refs"],
            }
        ],
        "required_candidate_count": 1,
    }
    return detection_result(
        state="unresolved",
        adjudication=adjudication,
        health={"quarantined_error_certificate_count": 1},
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_4",
    )


def unconsumed_provider_output() -> dict[str, Any]:
    """Provider output exists and is valid but the court terminal stayed
    unresolved: the production-consume edge never moved, so attribution
    stays on the emitted artifact while the observable delta is zero."""
    adjudication = {
        "state": "unresolved",
        "prosecution_coverage": "complete",
        "defense_coverage": "complete",
        "valid_error_certificate_count": 1,
        "valid_defense_certificate_count": 1,
        "surviving_error_certificate_ids": ["error_1"],
        "required_candidate_count": 1,
    }
    return detection_result(
        state="unresolved",
        adjudication=adjudication,
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        defense=[{"certificate_id": "defense_1"}],
        responsible_step="event_5",
    )


def l2_policy_movement() -> dict[str, Any]:
    """Adjudication-layer failure resolved by a policy evaluation: the
    consensus gate blocked a live candidate and the policy run moved the
    terminal edge from unresolved to verified_error."""
    adjudication = {
        "state": "unresolved",
        "prosecution_coverage": "complete",
        "defense_coverage": "none",
        "valid_error_certificate_count": 1,
        "consensus_gate_applied": True,
        "consensus_gate_blocked_ids": ["error_1"],
        "required_candidate_count": 1,
        "policy_evaluation": {
            "fired": [{"item_id": "policy_a", "action": "open_prosecution"}],
            "effective_prosecution_coverage": "complete",
            "effective_defense_coverage": "none",
        },
    }
    return detection_result(
        state="unresolved",
        adjudication=adjudication,
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_4",
    )


def signal_for(state: str, **overrides: Any) -> AttributionSignal:
    stable = state == "verified_clean"
    value: dict[str, Any] = {
        "error_type": (
            ErrorType.CORRECT if stable else ErrorType.EVIDENCE_INCOMPLETE
        ),
        "target_module": "evidence_obligation",
        "secondary_module": "atomic_verifier_instruction",
        "attribution_confidence": "medium",
        "reflection_type": (
            ReflectionType.STABLE_SUCCESS
            if stable
            else ReflectionType.OPTIMIZATION
        ),
        "trigger_source": (
            TriggerSource.NONE if stable else TriggerSource.EVIDENCE_INCOMPLETE
        ),
        "evidence_summary": "fixture evidence process",
        "prosecution_coverage": 0.5,
        "defense_coverage": 0.5,
        "prosecution_coverage_state": "incomplete",
        "defense_coverage_state": "incomplete",
        "coverage_quantitative": False,
        "crs_scores": {},
        "crs_aggregate": 0.0,
        "crs_source": "fixture",
        "crs_complete": True,
        "consensus_gate_applied": False,
        "candidate_count": 1,
        "state": state,
        "gold_label": None,
        "has_gold": False,
        "gold_enhanced": False,
        "responsible_step_correct": False,
    }
    value.update(overrides)
    return AttributionSignal(**value)


def type_result(
    primary: HallucinationType = HallucinationType.UNKNOWN,
    confidence: float = 0.5,
) -> TypeClassificationResult:
    return TypeClassificationResult(
        primary_type=primary,
        secondary_type=None,
        confidence=confidence,
        evidence={},
        classification_method="rule_based",
    )


def edit_payload(**overrides: Any) -> dict[str, Any]:
    """A mutation-provider response in the slot-contract schema."""
    value: dict[str, Any] = {
        "target_module": "evidence_obligation",
        "operation": "add_rule",
        "rule_index": None,
        "condition": "a factual claim lacks a cited trajectory event",
        "action": "require_evidence_field: impact_evidence_refs",
        "evidence_anchor": "uncited factual claim accepted without trajectory evidence",
        "feature_description": "factual claim without cited trajectory evidence",
        "rationale": "Tighten the evidence obligation for unsupported claims.",
    }
    value.update(overrides)
    return value


def invalid_edit_payload() -> dict[str, Any]:
    """An invalid mutation: an opcode the module does not own."""
    return edit_payload(
        target_module="evidence_obligation",
        operation="add_rule",
        condition="a claim lacks a cited trajectory event",
        action="quarantine_certificate: evidence_refs",
    )


def replay_pair(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return dict(before), dict(after)


def classifier_contract(
    detection: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one fixture into the classifier's input contract."""
    adjudication = detection.get("proof_adjudication") or {}
    health = detection.get("evidence_health") or {}
    artifacts: list[dict[str, Any]] = []
    if int(health.get("quarantined_error_certificate_count") or 0) > 0:
        artifacts.append({
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": True,
            "missing_fields": [],
        })
    audits = adjudication.get("error_audits") or []
    missing_fields = [
        str(name)
        for audit in audits
        if isinstance(audit, Mapping) and str(audit.get("decision") or "") != "pass"
        for name in (audit.get("failed_checks") or [])
        if name != "required_fields"
    ]
    if missing_fields:
        artifacts.append({
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": True,
            "missing_fields": sorted(set(missing_fields)),
        })
    if (
        str(detection.get("state") or "") == "unresolved"
        and str(adjudication.get("prosecution_coverage") or "") == "incomplete"
        and int(adjudication.get("valid_error_certificate_count") or 0) == 0
        and int(adjudication.get("required_candidate_count") or 0) > 0
    ):
        artifacts.append({
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": False,
            "missing_fields": ["certificate"],
        })
    boundary = detection.get("boundary_path") or {}
    return {
        "adjudication": dict(adjudication),
        "boundary_path_state": str(boundary.get("state") or ""),
        "provider_artifacts": artifacts,
    }


def frozen_attribution(
    detection: Mapping[str, Any],
) -> dict[str, str]:
    attribution = classify_failure(classifier_contract(detection))
    return {
        "layer": attribution.layer,
        "artifact": attribution.artifact,
        "reason_code": attribution.reason_code,
    }
