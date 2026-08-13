"""Invariant tests for the P0 consistency fixes (review audit 2026-08-13).

Each test pins one deterministic contract so the framework cannot regress to
the "code supports it, prompt/planner disagrees" drift the review identified:

- P0-2: L1 RepairPlan is exact-capability resolved on failure_shape.
- P0-3: the condition DSL is domain-scoped; a predicate that cannot bind is
  rejected before replay and never rendered to the model.
- P0-1/3: the direction-action contract and the capability contracts agree
  with the registry for a constructive failure shape.
"""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import FailureEdge
from sehd_rise.causal.plan import build_repair_plan
from sehd_rise.skills.action import render_direction_action_contract
from sehd_rise.skills.capabilities import (
    L1_CONSTRUCTIVE_HARNESS_EXECUTOR,
    SKILL_CAPABILITY_REGISTRY,
    render_capability_contracts,
)
from sehd_rise.skills.rule import (
    render_condition_spec_contract,
    validate_condition_spec_for_domain,
)


def _anchor_edge() -> FailureEdge:
    return FailureEdge(
        producer_route="local_constructive_harness",
        output_object="candidate",
        field="anchor_id",
        consumer="proof_court",
        observed="absent",
        required="present",
        owner_module="candidate_trigger",
        source_artifact_id="",
        blocking_predicate="gold_polarity_mismatch",
    )


def _episode(reason_code: str) -> SimpleNamespace:
    return SimpleNamespace(
        episode_id="episode_p0",
        skill_layer="l1_provider_rule",
        reason_code=reason_code,
        causal_failure_edge_frontier=(_anchor_edge(),),
    )


# --- P0-2: exact capability resolution on failure_shape -------------------

def test_repair_plan_matches_declared_failure_shape() -> None:
    """A gold_false_negative_clean cut may compile construct_candidate."""
    plans = build_repair_plan(
        _episode("gold_false_negative_clean"),
        allowed_modules=("candidate_trigger",),
        evolution_direction="clean_to_error",
    )
    assert [plan.executor for plan in plans] == [L1_CONSTRUCTIVE_HARNESS_EXECUTOR]
    assert plans[0].legal_operations == ("construct_candidate",)


def test_repair_plan_does_not_borrow_foreign_capability() -> None:
    """A provider_artifact_missing cut must not borrow construct_candidate.

    ``construct_candidate`` declares gold_false_negative_clean only, so a
    provider_artifact_missing episode on candidate_trigger compiles zero
    plans instead of a REPAIRABLE plan the planner could never execute.
    """
    plans = build_repair_plan(
        _episode("provider_artifact_missing"),
        allowed_modules=("candidate_trigger",),
        evolution_direction="clean_to_error",
    )
    assert plans == ()


# --- P0-3: domain-scoped condition DSL ------------------------------------

def test_condition_domain_rejects_unbindable_feature() -> None:
    """candidate_kind cannot bind on a claim_anchor capability."""
    spec = {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "candidate_kind",
        "operator": "contains",
        "value_type": "string",
        "value": "unsupported_claim",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }
    ok, reason = validate_condition_spec_for_domain(spec, "claim_anchor")
    assert ok is False
    assert reason.startswith("condition_feature_not_bindable_in_domain:claim_anchor:")


def test_condition_domain_accepts_bindable_feature() -> None:
    """anchor_has_number is a claim_anchor feature."""
    spec = {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "anchor_has_number",
        "operator": "equals",
        "value_type": "boolean",
        "value": True,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }
    ok, _reason = validate_condition_spec_for_domain(spec, "claim_anchor")
    assert ok is True


def test_condition_contract_render_is_domain_scoped() -> None:
    """The claim_anchor contract never renders candidate_kind to the model."""
    text = render_condition_spec_contract(binding_domains=("claim_anchor",))
    assert "anchor_has_number" in text
    assert "candidate_kind" not in text


# --- P0-1/3: prompt contract primitives agree with the registry ------------

def test_direction_contract_agrees_with_registry_for_constructive_shape() -> None:
    """The direction contract lists construct_candidate only for its shape."""
    text = render_direction_action_contract(
        "clean_to_error", ("candidate_trigger",),
        failure_shape="gold_false_negative_clean",
    )
    assert "construct_candidate" in text
    empty = render_direction_action_contract(
        "clean_to_error", ("candidate_trigger",),
        failure_shape="provider_artifact_missing",
    )
    assert "construct_candidate" not in empty


def test_capability_contracts_agree_with_registry_for_constructive_shape() -> None:
    """render_capability_contracts reflects the registry's failure_shape filter."""
    capabilities = SKILL_CAPABILITY_REGISTRY.query(
        skill_layer="l1_provider_rule",
        modules=("candidate_trigger",),
        direction="clean_to_error",
        failure_shape="gold_false_negative_clean",
    )
    assert {capability.executable_operation for capability in capabilities} == {
        "construct_candidate"
    }
    text = render_capability_contracts(
        ("candidate_trigger",),
        direction="clean_to_error",
        failure_shape="gold_false_negative_clean",
    )
    assert "construct_candidate" in text
