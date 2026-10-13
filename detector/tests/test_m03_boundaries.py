from __future__ import annotations

import pytest

from detector.adjudication import adjudicate
from detector.replay import validate_frozen_stages
from detector.defense import _parse_defense, _validate_defense_evidence
from detector.evidence import EvidenceError, build_snapshot, citation_error
from detector.packet import build_packet
from detector.predicates import run_predicate


def _snapshot(resolutions=()):
    return build_snapshot(
        query="ReactorAlpha capacity", derivation="task",
        task_excerpt="ReactorAlpha capacity",
        sources=[{
            "url": f"https://synthetic.invalid/{index}", "title": f"Source {index}",
            "publisher": f"Publisher {index}", "retrieved_at": "2026-10-10T00:00:00+00:00",
            "excerpt": f"{entity} capacity is {value} units.",
            "verifies": {"entity": entity, "attribute": "capacity", "value": value},
            "applicable_context": f"{entity} design",
        } for index, (entity, value) in enumerate([
            ("ReactorAlpha", "10"), ("ReactorAlpha", "20"),
            ("ReactorBeta", "30"), ("ReactorBeta", "40"),
        ], start=1)], resolutions=resolutions,
    )


def _packet():
    return build_packet({
        "question": "Report ReactorAlpha and ReactorBeta capacities.",
        "history": [{"role": "assistant", "content": "ReactorAlpha capacity is 10 units."}],
    })


@pytest.mark.parametrize("boundary", ["citation", "live", "frozen"])
def test_a_conflict_is_settled_only_by_a_recorded_resolution(boundary):
    """Prose cannot settle a conflict; a recorded resolution can.

    The old contract accepted a ``conflict_note`` that merely *named* the
    disputed ids or values -- a substring match, so "I reviewed 4 unrelated
    things" settled a numeric conflict, and a note covering one conflict
    licensed a decisive finding that also cited a different unresolved one.
    A conflict is now settled by the same authority every other consumer
    reads: a resolution recorded in the snapshot, naming the applicable
    source. The note survives as audit context only.
    """
    note = (
        "E1 applies; E2 concerns an older Alpha revision. "
        "E3 applies instead of E4 for Beta."
    )
    unresolved_snapshot = _snapshot()
    resolved_snapshot = _snapshot(resolutions=[
        {"entity": "ReactorAlpha", "attribute": "capacity",
         "resolved_by": "E1", "reason": "E2 describes an older revision"},
        {"entity": "ReactorBeta", "attribute": "capacity",
         "resolved_by": "E3", "reason": "E4 describes an older revision"},
    ])
    row = {
        "verdict": "substantiated", "confirmed_step": 1, "evidence_refs": ["event_001"],
        "evidence_citations": ["E1", "E3"], "conflict_note": note, "reason": "capacity proof",
    }
    if boundary == "citation":
        assert citation_error(
            unresolved_snapshot, row["evidence_citations"], where="review",
            require_settled=True,
        ) is not None
        assert citation_error(
            resolved_snapshot, row["evidence_citations"], where="review",
            require_settled=True,
        ) is None
        return
    if boundary == "live":
        parsed, error = _parse_defense(row)
        assert parsed is not None, error
        parsed, error = _validate_defense_evidence(
            parsed, _packet(), {"step": 1}, unresolved_snapshot,
        )
        assert parsed is None and error
        parsed, error = _parse_defense(row)
        assert parsed is not None, error
        parsed, error = _validate_defense_evidence(
            parsed, _packet(), {"step": 1}, resolved_snapshot,
        )
        assert parsed is not None, error
        return
    judge = {
        "is_hallucination": False, "hallucination_step": None, "hallucination_reason": None,
        "delivered_result": "10 units", "correct_result": "10 units", "basis": None,
        "confidence": "high", "evidence_refs": [], "root_cause": None,
        "contracts": [{"kind": "exact_value", "requirement": "10 units"}],
    }
    stages = {
        "judge_result": {"ok": True, "parsed": judge}, "judge_status": {"state": "ok"},
        "hearings": [{"source": "judge", "positive": {"step": 1}, "candidate_id": None,
                      "defense_result": {"ok": True, "parsed": row},
                      "defense_status": {"state": "ok"}}],
    }
    with pytest.raises(ValueError, match="evidence error"):
        validate_frozen_stages(stages, _packet(), unresolved_snapshot)
    validate_frozen_stages(stages, _packet(), resolved_snapshot)


def test_resolution_must_belong_to_its_entity_and_attribute():
    with pytest.raises(EvidenceError, match="entity/attribute"):
        _snapshot([{"entity": "ReactorAlpha", "attribute": "capacity", "resolved_by": "E3",
                    "reason": "source of Beta"}])


@pytest.mark.parametrize("predicate,verdict", [
    ("evidence_refs_resolve", "unresolved"), ("contract_coverage", "uncovered"),
    ("delivered_anchor_consistency", "insufficient"), ("invented_predicate", "violated"),
])
@pytest.mark.parametrize("runner", ["deterministic", ""])
def test_custom_authority_table_cannot_promote_diagnostic_or_unknown_builtin(predicate, verdict, runner):
    evidence = {
        "action": "convict_mechanical_violation", "intervention_id": "guard",
        "evidence_intervention_id": "evidence", "failure_family": "synthetic",
        "predicate": predicate, "verdict": verdict, "runner": runner,
    }
    result = adjudicate(
        packet=_packet(), judge_result={"ok": True, "parsed": {"is_hallucination": False,
                            "basis": None, "confidence": "high", "contracts": []}},
        judge_status={"state": "ok"}, guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[evidence], predicate_conviction_verdicts={
            (predicate, runner): {verdict}, predicate: {verdict},
        },
    )
    assert result["state"] != "verified_error"


def test_answer_delivery_survives_a_closing_status_tool():
    packet = build_packet({"question": "Report the capacity.", "history": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_final_answer", "arguments": {"answer": "10 units"}}],
         "tool_responses": ["ok"]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "terminate", "arguments": {"status": "success"}}],
         "tool_responses": ["success"]},
    ]})
    assert run_predicate("final_delivery_present", packet)["verdict"] == "present"


@pytest.mark.parametrize("arguments,observation", [
    ({"status": "unsuccessful"}, "failed"),
    ({"status": "success=false"}, "failed"),
    ({"success": False}, "failed"),
    ({"query": "success"}, "failed"),
    ({}, "The interaction has been completed with status: unsuccessful"),
    ({}, '{"success": false}'),
    ({"status": False, "success": True}, "failed"),
])
def test_failure_or_ordinary_data_cannot_claim_terminal_success(arguments, observation):
    packet = build_packet({"question": "Search for the report.", "history": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "terminate", "arguments": arguments}],
         "tool_responses": [observation]},
    ]})
    assert run_predicate("final_delivery_present", packet)["verdict"] == "not_applicable"


@pytest.mark.parametrize("arguments,observation", [
    ({"status": "success"}, "ok"),
    ('{"status": "success"}', "ok"),
    ({"success": True}, "ok"),
    ({"status": " SUCCESS "}, "ok"),
    ({}, "The interaction has been completed with status: success"),
])
def test_explicit_success_still_requires_a_delivery_channel(arguments, observation):
    packet = build_packet({"question": "Report the result.", "history": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "terminate", "arguments": arguments}],
         "tool_responses": [observation]},
    ]})
    assert run_predicate("final_delivery_present", packet)["verdict"] == "missing"


def test_nested_status_data_is_not_a_success_report():
    depth = 10000
    packet = build_packet({"question": "Report the result.", "history": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "terminate", "arguments": {}}],
         "tool_responses": ['[' * depth + '"success"' + ']' * depth]},
    ]})
    assert run_predicate("final_delivery_present", packet)["verdict"] == "not_applicable"


@pytest.mark.parametrize("requirement", [
    "Start the engine.", "Please go to the nearest tire shop.",
])
def test_model_contract_cannot_add_an_action_obligation(requirement):
    packet = build_packet({
        "question": "Ask whether I want the engine activated. Do not activate it.",
        "history": [{"role": "assistant", "content": "Would you like me to start the engine?"}],
    })
    context = {"contracts": [{"kind": "required_action", "requirement": requirement}]}
    assert run_predicate("required_action_present", packet)["verdict"] == "not_applicable"
    assert run_predicate("required_action_present", packet, context)["verdict"] == "not_applicable"


def test_evidence_plan_does_not_turn_a_model_contract_into_an_obligation():
    from detector.interventions import InterventionPlan, build_intervention_registry
    from detector.predicate_context import judge_predicate_context
    from detector.tests.test_interventions import _candidate

    packet = build_packet({
        "question": "Ask whether I want the engine activated. Do not activate it.",
        "history": [
            {"role": "assistant", "tool_calls": [{"name": "inspect_vehicle", "arguments": {}}],
             "tool_responses": ["parked"]},
            {"role": "assistant", "content": "Would you like me to start the engine?"},
        ],
    })
    entry = _candidate(type="evidence_check", hook="judge.post", body={
        "predicate": "required_action_present", "runner": "deterministic",
        "output_verdicts": ["satisfied", "violated", "not_applicable"],
    })
    plan = InterventionPlan.from_registry(build_intervention_registry([entry]))
    context = judge_predicate_context({
        "is_hallucination": False,
        "contracts": [{"kind": "required_action", "requirement": "Start the engine."}],
    })
    rows = plan.evidence_for("judge.post", packet, context)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "not_applicable"
    assert plan.facts["required_action_present"] == "not_applicable"


@pytest.mark.parametrize("question", [
    "Ask whether I want the engine activated. Do not activate it.",
    "Do not start the engine.", "Explain how to use the search tool.",
    "Do not go to the nearest tire shop.",
])
def test_causal_audit_does_not_certify_a_model_supplied_action(question):
    from detector.causal_audit import run_causal_audit
    from detector.causal_graph import build_observed_dependencies, project_dependency_closure
    from detector.runtime import AddedBudget
    from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates
    from detector.tests.test_causal_audit import policy

    packet = build_packet({
        "question": question,
        "history": [{"role": "assistant", "content": "Would you like me to start the engine?"}],
    })
    context = {"contracts": [{"kind": "required_action", "requirement": "Start the engine."}]}
    cfg = policy()
    profile = build_step_risk_profile(packet, policy=cfg, context=context)
    candidates = select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)
    graph = build_observed_dependencies(packet, policy=cfg)
    projection = project_dependency_closure(packet, candidates=candidates, graph=graph, policy=cfg)
    audit, _ = run_causal_audit(packet=packet, profile=profile, projection=projection,
                              context=context, config={"causal_audit": cfg}, budget=AddedBudget(cfg))
    assert profile["task_signals"] == []
    assert audit["task_checks"] == []


@pytest.mark.parametrize("question", [
    "Start the engine.", "Please go to the nearest tire shop.",
    "You must use the search tool before answering.",
])
def test_real_action_obligations_survive_an_unrelated_model_contract(question):
    packet = build_packet({"question": question, "history": [
        {"role": "assistant", "content": "Would you like me to start the engine?"},
    ]})
    context = {"contracts": [{"kind": "required_action", "requirement": "Check the weather."}]}
    assert run_predicate("required_action_present", packet)["verdict"] == "violated"
    assert run_predicate("required_action_present", packet, context)["verdict"] == "violated"


@pytest.mark.parametrize("question", [
    "Ask whether I want the engine activated. Do not activate it.",
    "Do not start the engine.", "Explain how to use the search tool.",
    "Do not go to the nearest tire shop.",
])
def test_detector_records_no_mechanical_violation_from_a_model_contract(monkeypatch, question):
    from detector.api import Detector
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import CONFIG, _candidate

    record = {
        "question": question,
        "history": [
            {"role": "assistant", "tool_calls": [{"name": "inspect_vehicle", "arguments": {}}],
             "tool_responses": ["parked"]},
            {"role": "assistant", "content": "Would you like me to start the engine?"},
        ],
    }
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high", "evidence_refs": [],
            "contracts": [{"kind": "required_action", "requirement": "Start the engine."}],
        }, "provider": {}},
        {"stage": "judge", "state": "ok", "attempts": 0, "view": "complete", "warnings": []},
    ))
    monkeypatch.setattr("detector.api.run_recall_scan", lambda **kwargs: (
        {"ok": True, "candidates": []},
        {"stage": "recall_scanner", "state": "ok", "attempts": 0, "view": "complete", "warnings": []},
    ))
    monkeypatch.setattr("detector.api.run_causal_upgrade", lambda **kwargs: (
        {"ok": True, "upgrades": [], "upgraded_candidate": None},
        {"stage": "causal_upgrade", "state": "not_needed", "attempts": 0,
         "view": "not_rendered", "warnings": []},
    ))
    registry = build_intervention_registry([_candidate(
        type="evidence_check", hook="judge.post", body={
            "predicate": "required_action_present", "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
        },
    )])
    result = Detector(CONFIG, require_credentials=False).detect(record, interventions=registry)
    evidence = result["proof"]["deployment"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["verdict"] == "not_applicable"
    assert result["state"] == "verified_clean"


@pytest.mark.parametrize("question", [
    "Do not start the engine. Ask for permission first.",
    "Ask whether I want you to start the engine.",
    "Explain how to start the engine without doing it.",
    "Could you start the engine?",
    "The engine failed to start. Ask for permission.",
    "Don't use the search tool.",
    "You mustn't use the search tool.",
    "Don’t go to the nearest tire shop.",
    "You shouldn’t start the engine.",
    "Explain how to use the search tool.",
    "Should I use the search tool?",
    "Do not go to the nearest tire shop.",
    "Explain how to go to the nearest tire shop.",
    "Could you go to the nearest tire shop?",
    "Never go to the nearest tire shop.",
    'Print the sentence "Start the engine.".',
    'Print the sentence "Use the search tool.".',
    'Print the sentence "Go to the nearest tire shop.".',
    "The manual mentions start the engine.",
    "The manual says use the search tool.",
    "The itinerary mentions go to the nearest tire shop.",
])
def test_non_execution_task_does_not_require_a_mechanical_action(question):
    packet = build_packet({"question": question, "history": [
        {"role": "assistant", "content": "Would you like me to start the engine?"},
    ]})
    assert run_predicate("required_action_present", packet)["verdict"] == "not_applicable"


@pytest.mark.parametrize("question", [
    "Start the engine.",
    "Once fueled, proceed to start the engine confidently.",
    "Start my car's engine.",
    "I would like you to start the engine.",
    "First unlock the doors and then start the engine.",
    "I want to go to the nearest tire shop.",
    "You must use the search tool before answering.",
    "You must use the search tool to determine whether it will rain.",
    "Please call the calculator function with the two operands.",
    'required_action: "lookup_customer"',
    "Please go to the nearest tire shop.",
    "Do not use the weather tool. You must use the search tool.",
    "Explain the route. Then go to the nearest tire shop.",
    "Ask about the engine; then start the engine.",
])
def test_explicit_execution_task_keeps_its_mechanical_obligation(question):
    packet = build_packet({"question": question, "history": [
        {"role": "assistant", "content": "Would you like me to start the engine?"},
    ]})
    assert run_predicate("required_action_present", packet)["verdict"] == "violated"
