"""Phase 1 mechanism tests for Failure -> RepairPlan -> Skill closure."""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import FailureEdge
from sehd_rise.causal.plan import (
    EXECUTOR_L1_COURT_TRANSFORM,
    build_repair_plan,
)
from sehd_rise.loop.semantic_closure import validate_semantic_closure
from sehd_rise.skills.action import compile_action_spec
from sehd_rise.skills.capabilities import (
    SKILL_CAPABILITY_REGISTRY,
)
from sehd_rise.skills.l1_execution import evaluate_l1_rule
from sehd_rise.skills.vocabulary.charters import (
    MODULE_ACTION_OPCODES,
    MODULE_CHARTERS,
)
from sehd_rise.skills.vocabulary.contracts import L2_ACTIONS


def _court_result() -> dict:
    return {
        "state": "verified_error",
        "census": [
            {
                "candidate_id": "candidate_1",
                "origin_hypothesis_event_id": "event_1",
                "candidate_kind": "unsupported_claim",
                "module_tag": "Reasoning",
                "axes": ["F"],
            },
        ],
        "prosecution": {
            "candidates": [
                {
                    "certificate_id": "certificate_1",
                    "source_candidate_id": "candidate_1",
                    "origin_event_id": "event_1",
                },
            ],
        },
        "combined_prosecution": {
            "candidates": [
                {
                    "certificate_id": "certificate_1",
                    "source_candidate_id": "candidate_1",
                    "origin_event_id": "event_1",
                },
            ],
        },
        "proof_adjudication": {
            "state": "verified_error",
            "responsible_event_id": "event_1",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": ["certificate_1"],
            "error_audits": [
                {
                    "certificate_id": "certificate_1",
                    "decision": "quarantine",
                    "failed_checks": ["norm_evidence"],
                    "missing_fields": ["norm_evidence_refs"],
                    "failed_invariants": ["norm_evidence"],
                },
            ],
            "terminal_basis": {
                "decisive_facts": {
                    "coverage_complete": True,
                    "candidate_terminal": True,
                },
            },
        },
    }


def _condition() -> dict:
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "failed_invariant",
        "operator": "contains",
        "value_type": "string",
        "value": "norm_evidence",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def test_repairable_fp_keeps_one_operation_from_plan_through_runtime() -> None:
    """A: the same registered operation survives plan, compile, and execute."""
    edge = FailureEdge(
        producer_route="post_court_l1",
        output_object="verification_certificate",
        field="certificate_id",
        consumer="proof_court",
        observed="wrongly_surviving",
        required="withheld",
        owner_module="atomic_verifier_instruction",
        source_artifact_id="certificate_1",
        blocking_predicate="gold_polarity_mismatch",
    )
    episode = SimpleNamespace(
        episode_id="episode_fp",
        skill_layer="l1_provider_rule",
        causal_failure_edge_frontier=(edge,),
    )
    plans = build_repair_plan(
        episode,
        allowed_modules=("atomic_verifier_instruction",),
        evolution_direction="error_to_clean",
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.executor == EXECUTOR_L1_COURT_TRANSFORM
    assert plan.auto_selected_operation == "withhold_certificate"
    assert SKILL_CAPABILITY_REGISTRY.contains(plan.auto_selected_operation)
    assert plan.target_domain == "certificate"
    assert plan.grounding_query["source_artifact_id"] == "certificate_1"
    assert plan.required_binding_schema == {
        "domain": "certificate",
        "required_fields": ["certificate_id"],
        "cardinality": "exactly_one",
    }

    spec = compile_action_spec(
        plan.owner,
        f"{plan.auto_selected_operation}: {plan.target_causal_edge.field}",
    )
    transformed, report = evaluate_l1_rule(
        condition_spec=_condition(),
        action_spec=spec.to_dict(),
        result=_court_result(),
    )
    assert report["operation"] == plan.auto_selected_operation
    assert report["matched"] is True
    assert report["fired"] is True
    assert transformed["proof_adjudication"]["surviving_error_certificate_ids"] == []
    assert transformed["proof_adjudication"]["state"] == "verified_clean"


def test_every_capability_has_executor_and_fitness_postcondition() -> None:
    """C: CI fails if a registry operation lacks runtime or fitness closure."""
    assert validate_semantic_closure() == ()
    proposed_l1 = {
        operation
        for entries in MODULE_ACTION_OPCODES.values()
        for operation, _description in entries
    }
    registered_l1 = {
        capability.executable_operation
        for capability in SKILL_CAPABILITY_REGISTRY.for_layer("l1_provider_rule")
    }
    registered_l2 = {
        capability.executable_operation
        for capability in SKILL_CAPABILITY_REGISTRY.for_layer(
            "l2_adjudication_policy"
        )
    }
    assert proposed_l1 == registered_l1
    assert set(L2_ACTIONS) == registered_l2


def test_non_repair_runtime_actions_are_not_skill_actions() -> None:
    assert "emit_telemetry_only" not in L2_ACTIONS
    assert "escalate_to_appeal" not in L2_ACTIONS
    assert "mark_candidate_ambiguous" not in L2_ACTIONS


def test_charters_expose_negative_capability_facts() -> None:
    candidate = MODULE_CHARTERS["candidate_trigger"]
    verifier = MODULE_CHARTERS["atomic_verifier_instruction"]
    assert "author candidate verdict" in candidate.forbidden_surfaces
    assert "emit certificate" in candidate.forbidden_surfaces
    assert "construct_candidate" in {
        operation for operation, _text in MODULE_ACTION_OPCODES["candidate_trigger"]
    }
    assert "create missing certificate" in verifier.forbidden_surfaces
