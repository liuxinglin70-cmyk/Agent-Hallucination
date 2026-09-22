from __future__ import annotations

from evolution.capability_surface import capability_surface_report
from evolution.contracts import candidate_contains_case_id
from evolution.proposer import _draft_system_prompt


def test_capability_surface_classifies_current_runtime_honestly():
    report = capability_surface_report()
    assert report["classification"] == (
        "typed_program_synthesis_with_runtime_registration_and_control_flow_policy"
    )
    assert report["open_ended_self_evolution"] is False
    assert report["executable_inventory"]["static_predicate_count"] == 6
    assert report["generative_freedom"]["new_predicate_program"] is True
    assert report["generative_freedom"]["new_executor"] is True
    assert report["generative_freedom"]["new_fact_type"] is True
    assert report["generative_freedom"]["new_control_flow_policy"] is True
    assert report["generative_freedom"]["new_hook"] is False
    assert report["generative_freedom"]["execution_graph_rewrite"] is False
    assert report["capability_creation"]["typed_field_count"] > 10
    assert report["capability_creation"]["typed_operator_count"] > 10
    assert report["capability_creation"]["max_conditions_per_rule"] == 4
    assert (
        report["causal_limits"]["capability_gap_can_expand_observation"]
        is True
    )


def test_proposer_contract_exposes_text_boundary_schema():
    prompt = _draft_system_prompt()
    assert '"type":"text_boundary"' in prompt
    assert "recall_directive maps to scanner.system" in prompt
    assert "upgrade_directive maps to" in prompt
    assert '"type":"evidence_check"' in prompt
    assert '"type":"recall_trigger"' in prompt
    assert "recall_trigger maps to recall.trigger" in prompt
    assert "typed_dsl" in prompt
    assert "predicate_definition" in prompt
    assert "Typed predicate-program fields" in prompt


def test_case_id_hidden_in_evolved_program_literal_is_leak():
    candidate = {
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "evo_leak_check",
            "runner": "typed_dsl",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
            "predicate_definition": {
                "schema_version": "detector.predicate_program.v1",
                "name": "evo_leak_check",
                "description": "leak",
                "verdicts": ["satisfied", "violated", "not_applicable"],
                "conviction_verdicts": ["violated"],
                "default_verdict": "not_applicable",
                "rules": [{
                    "field": "question_text",
                    "operator": "equals",
                    "value": "A/001.json",
                    "verdict": "violated",
                }],
            },
        },
        "claim": {
            "expected_behavior": "reject hidden case id",
            "target_polarity": "recall",
        },
    }
    assert candidate_contains_case_id(candidate) == "A/001.json"


def test_the_surface_reports_the_runners_and_does_not_overstate_them():
    """The runner space is part of "what can be executed", so it is reported.

    And the distinction between *naming* a learned executor and *generating*
    one is kept explicit: the framework binds an injected executor, it does
    not contain one. Reporting the second as true would claim a capability
    that does not exist.
    """
    report = capability_surface_report()
    runners = {
        entry["name"]: entry
        for entry in report["executable_inventory"]["runners"]
    }
    assert set(runners) == {"deterministic", "typed_dsl", "typed_semantic"}
    assert runners["typed_semantic"]["learned"] is True
    assert runners["typed_semantic"]["terminal_authority"] == "evidence_only"
    assert runners["typed_semantic"]["requires_artifact"] is True
    assert runners["deterministic"]["terminal_authority"] == "conviction"

    freedom = report["generative_freedom"]
    assert freedom["name_a_learned_executor"] is True
    assert freedom["generate_a_learned_executor"] is False
    # Still closed, and the change to the runner space does not open them.
    assert freedom["new_hook"] is False
    assert freedom["sandboxed_code_generation"] is False
    assert freedom["model_weight_update"] is False
