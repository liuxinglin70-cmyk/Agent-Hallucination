"""B6 L2 vocabulary-evolution acceptance: a real L2 cut reason routed to
``no_registered_runtime_action`` by the policy planner opens registry
reflection, registration enters the pending validation queue, promotion
needs independent observations, and a promoted ``<operator>_instance``
alias is proposable and contract-valid."""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.detection.policy import build_adjudication_facts
from sehd_rise.loop.runner import _run_registry_reflection_impl
from sehd_rise.planning.l2 import plan_policy_candidates
from sehd_rise.skills.policy import (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_VALIDATED,
    PolicyGenome,
    PolicyItem,
    _validate_item_contract,
)
from sehd_rise.skills.vocabulary.contracts import (
    ROUTE_EXECUTED,
    ROUTE_NO_REGISTERED_RUNTIME_ACTION,
)
from sehd_rise.skills.vocabulary.evolution import (
    REGISTRY_REQUIRED_VALIDATION,
    enqueue_registry_validation,
    plan_registry_candidates,
    promote_pending_registry_entries,
    promoted_intent_candidates,
)
from sehd_rise.skills.vocabulary.registry import L2_REGISTRY, apply_registry_edit


def _facts() -> object:
    return build_adjudication_facts(
        {}, boundary_path_state="", boundary_authority_valid=False,
    )


def _attribution() -> SimpleNamespace:
    # A real L2 cut reason the capability registry owns no runtime action
    # for, so the policy planner routes it to no_registered_runtime_action —
    # the same attribution shape the runner forwards in production.
    return SimpleNamespace(
        reason_code="appeal_pending_blocked",
        layer="l2_adjudication_policy",
        evidence={"appeal_status": "pending", "terminal_state": "unresolved"},
        to_dict=lambda: {
            "reason_code": "appeal_pending_blocked",
            "layer": "l2_adjudication_policy",
        },
    )


def test_real_cut_reason_opens_registry_reflection_through_route(tmp_path) -> None:
    attribution = _attribution()
    genome = PolicyGenome(genome_id="g1", registry=L2_REGISTRY)
    plan = plan_policy_candidates(attribution=attribution, genome=genome)
    assert plan.route_result == ROUTE_NO_REGISTERED_RUNTIME_ACTION
    candidates = plan_registry_candidates(
        attribution=attribution, registry=genome.registry,
        route_result=plan.route_result,
    )
    assert candidates
    action_index = next(
        index for index, candidate in enumerate(candidates)
        if (
            candidate.operation == "register_action"
            and candidate.operator == "downgrade_coverage"
        )
    )

    def fake_run_mutation_call(*, route, purpose, system, user):
        return {
            "candidate_index": action_index,
            "precondition": "localization_failed_with_temporal_fallback",
            "params": {"side": "prosecution", "to_state": "incomplete"},
        }, {}

    log: list[dict] = []
    _run_registry_reflection_impl(
        attribution=attribution,
        facts=_facts(),
        facts_summary={},
        case_id="case_1",
        route_result=plan.route_result,
        policy_genome=genome,
        mutation_route={},
        run_mutation_call=fake_run_mutation_call,
        output_dir=tmp_path,
        l2_registry_registrations=log,
    )
    assert log[0]["outcome"] == "registered"
    entries = [
        entry for entry in genome.pending_queue
        if entry.required_validation == REGISTRY_REQUIRED_VALIDATION
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.subject_id == "downgrade_coverage_instance"
    assert entry.status == PENDING_STATUS_PENDING
    assert entry.observed_case_ids == ("case_1",)
    assert entry.source_evidence["intended_edit"]["precondition"] == (
        "localization_failed_with_temporal_fallback"
    )
    # Pending is shadow-only: the instance is not proposable yet.
    assert "downgrade_coverage_instance" not in genome.registry.action_names()


def test_registry_planner_gates_on_route_not_attribution_reason() -> None:
    attribution = _attribution()
    assert plan_registry_candidates(
        attribution=attribution, registry=L2_REGISTRY,
        route_result=ROUTE_EXECUTED,
    ) == ()


def test_promoted_instance_becomes_proposable_and_contract_valid() -> None:
    facts = _facts()
    registered = apply_registry_edit(
        L2_REGISTRY,
        operation="register_action",
        target="downgrade_coverage_instance",
        operator="downgrade_coverage",
        reason_codes=(ROUTE_NO_REGISTERED_RUNTIME_ACTION,),
    )
    genome = PolicyGenome(genome_id="g2", registry=registered)
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=facts,
        case_id="case_1",
        intended_edit={
            "precondition": "localization_failed_with_temporal_fallback",
            "params": {"side": "prosecution", "to_state": "incomplete"},
        },
    )
    assert "downgrade_coverage_instance" not in genome.registry.action_names()

    second = promote_pending_registry_entries(
        genome=genome, facts=facts, case_id="case_2",
        route_result=ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    )
    assert second[0]["outcome"] == "still_pending"
    third = promote_pending_registry_entries(
        genome=genome, facts=facts, case_id="case_3",
        route_result=ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    )
    assert third[0]["outcome"] == "promoted"

    entry = next(
        entry for entry in genome.pending_queue
        if entry.subject_id == "downgrade_coverage_instance"
    )
    assert entry.status == PENDING_STATUS_VALIDATED
    assert "downgrade_coverage_instance" in genome.registry.action_names()

    item = PolicyItem(
        item_id="item_promoted",
        precondition="localization_failed_with_temporal_fallback",
        action="downgrade_coverage_instance",
        params={"side": "prosecution", "to_state": "incomplete"},
        rationale="promoted instance intent",
    )
    ok, reason = _validate_item_contract(item, genome.registry)
    assert ok, reason

    candidates = promoted_intent_candidates(genome, facts)
    assert len(candidates) == 1
    assert candidates[0].new_item.action == "downgrade_coverage_instance"


def test_telemetry_operator_stays_unproposable() -> None:
    assert "emit_telemetry_only" not in L2_REGISTRY.action_names()
    assert "downgrade_coverage" in L2_REGISTRY.action_names()
