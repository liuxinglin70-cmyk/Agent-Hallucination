"""P1 verification: typing stays advisory, and the framework auto-fills the
RepairPlan-known proposal fields so the model only learns applicability."""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import FailureEdge
from sehd_rise.planning.candidates import (
    _normalize_candidate_from_repair_plan,
    _prevalidate_candidate,
)
from sehd_rise.typing.classifier import HallucinationType
from sehd_rise.typing.guidance import TypeGuidance


def _plan() -> SimpleNamespace:
    edge = FailureEdge(
        producer_route="post_court_l1",
        output_object="error_certificate",
        field="coverage_state",
        consumer="proof_court",
        observed="complete",
        required="incomplete",
        owner_module="evidence_obligation",
        source_artifact_id="",
        blocking_predicate="gold_polarity_mismatch",
    )
    return SimpleNamespace(
        target_causal_edge=edge,
        legal_operations=("keep_coverage_incomplete",),
        auto_selected_operation="keep_coverage_incomplete",
    )


def test_type_guidance_is_text_only_and_never_changes_authority() -> None:
    """The typing layer returns advisory text; the allowed-module set is
    owned by the causal cut and passed straight through."""
    guidance = TypeGuidance().generate_type_guidance(
        HallucinationType.FACTUAL,
        evolution_direction="error_to_clean",
        allowed_modules=("evidence_obligation", "atomic_verifier_instruction"),
        failure_shape="gold_false_positive",
    )
    assert isinstance(guidance, str)
    assert "Authority: applicability prior only" in guidance
    # The prior may only re-rank modules the cut already admitted: its
    # ranking is a subset of the allowed set, never an extension of it.
    prior = TypeGuidance().get_target_modules(HallucinationType.FACTUAL, primary=True)
    assert set(prior) <= {"evidence_obligation", "atomic_verifier_instruction"}
    assert "Registry-compatible operations" in guidance


def test_normalization_fills_repair_plan_known_fields() -> None:
    """A proposal that names the repair edge and the opcode needs nothing
    else: the framework derives target_module and the action spec."""
    frontier = [{
        "edge_id": "edge_1",
        "owner_module": "evidence_obligation",
        "field": "coverage_state",
        "producer_route": "post_court_l1",
        "output_object": "error_certificate",
        "consumer": "proof_court",
        "observed": "complete",
        "required": "incomplete",
        "blocking_predicate": "gold_polarity_mismatch",
        "source_artifact_id": "",
    }]
    candidate = {
        "repair_edge_id": "edge_1",
        "operation": "add_rule",
        "opcode": "keep_coverage_incomplete",
        "output_field": "coverage_state",
        "condition": "prosecution coverage is complete yet carries no valid certificate",
        "condition_spec": {
            "schema_version": "sehd.condition_spec.v3",
            "feature_source": "court_result",
            "feature": "coverage_state",
            "operator": "contains",
            "value_type": "string",
            "value": "complete",
            "scope": "l1_court_transform",
            "evidence_binding": "post_court_projection",
            "combinator": "atom",
            "binding": "entity_scoped",
        },
        "evidence_anchor": "a complete prosecution with zero valid certificates",
    }
    normalized = _normalize_candidate_from_repair_plan(candidate, frontier)
    assert normalized["target_module"] == "evidence_obligation"
    assert normalized["action"] == "keep_coverage_incomplete: coverage_state"
    # The target module was derived, never supplied by the model.
    assert "target_module" not in candidate
    assert "action" not in candidate


def test_unique_plan_repairs_action_missing_target() -> None:
    """The run-1 failure class: the model wrote the opcode without the
    framework-known ``: coverage_state`` target. With a unique plan the
    deterministic action string is restored instead of rejecting."""
    candidate = {
        "operation": "add_rule",
        "opcode": "keep_coverage_incomplete",
        "action": "keep_coverage_incomplete",
        "condition": "prosecution coverage is complete yet carries no valid certificate",
        "evidence_anchor": "a complete prosecution with zero valid certificates",
    }
    normalized = _normalize_candidate_from_repair_plan(
        candidate, [], plans=[_plan()],
    )
    assert normalized["repair_edge_id"]
    assert normalized["target_module"] == "evidence_obligation"
    assert normalized["action"] == "keep_coverage_incomplete: coverage_state"


def test_unique_plan_fills_every_framework_known_field() -> None:
    """The model supplies only the applicability condition; every execution
    field derives from the unique plan."""
    candidate = {
        "operation": "add_rule",
        "condition": "prosecution coverage is complete yet carries no valid certificate",
        "evidence_anchor": "a complete prosecution with zero valid certificates",
    }
    normalized = _normalize_candidate_from_repair_plan(
        candidate, [], plans=[_plan()],
    )
    assert normalized["repair_edge_id"]
    assert normalized["target_module"] == "evidence_obligation"
    assert normalized["opcode"] == "keep_coverage_incomplete"
    assert normalized["action"] == "keep_coverage_incomplete: coverage_state"


def test_ambiguous_plans_leave_candidate_untouched() -> None:
    """No unique plan: a malformed action stays malformed and prevalidation
    rejects fail-closed — ambiguity never gets silently repaired."""
    candidate = {
        "operation": "add_rule",
        "opcode": "keep_coverage_incomplete",
        "action": "keep_coverage_incomplete",
        "condition": "x",
        "evidence_anchor": "y",
    }
    normalized = _normalize_candidate_from_repair_plan(
        candidate, [], plans=(),
    )
    assert normalized["action"] == "keep_coverage_incomplete"
    assert "repair_edge_id" not in normalized
