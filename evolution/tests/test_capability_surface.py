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
