"""Generalized boundaries behind the nine audited regressions; no benchmark identifiers."""

from detector import semantics
from detector.causal_graph import build_observed_dependencies
from detector.config import validate_causal_config
from detector.packet import build_packet
from detector.predicates import run_predicate
from detector.trajectory_budget import bound_request_route


def packet(question, content, *, tool=None):
    event = {"role": "assistant", "content": content}
    if tool:
        event.update(tool_calls=[{"name": tool, "arguments": {}}], tool_responses=["success"])
    return build_packet({"question": question, "history": [event]})


def test_approximate_message_template_does_not_create_a_literal_conviction():
    view = packet("The message should be like 'Balance in my account is $1000.'", "Balance is $1000.")
    assert run_predicate("verbatim_fidelity", view, {})["verdict"] == "not_applicable"


def test_separate_required_directory_query_is_not_satisfied_by_a_deletion():
    view = packet('Delete the document. Then required_action: "list_directory".', "Deleted.", tool="remove_document")
    assert run_predicate("required_action_present", view, {})["verdict"] == "violated"


def test_unfinished_explicit_start_request_remains_an_obligation():
    view = packet("Start the engine.", "Would you like me to lock all the doors for you now?")
    assert run_predicate("required_action_present", view, {})["verdict"] == "violated"


def test_name_identity_question_does_not_require_an_unspecified_literal_spelling():
    view = packet("Identify the five panel members.", "A. Doe; B. Smith; C. Smith; D. Lee; E. Li")
    assert run_predicate("verbatim_fidelity", view, {})["verdict"] == "not_applicable"


def test_closed_literal_field_cannot_be_extended():
    view = packet("Use exactly 'Cancelled due to a schedule change.'", "Cancelled due to a schedule change. Also urgent.")
    # A contains-style check cannot by itself enforce closure; that is the shared stage law.
    assert run_predicate("verbatim_fidelity", view, {})["verdict"] == "satisfied"
    assert "explicitly closed literal field cannot be silently extended" in ' '.join(semantics.MATERIALITY.split())
    assert "put description" in semantics.MATERIALITY


def test_partial_viewport_does_not_establish_whole_page_absence():
    for stage in (semantics.JUDGE_SYSTEM, semantics.UPGRADE_SYSTEM, semantics.DEFENSE_SYSTEM):
        assert "partial viewport or excerpt cannot prove" in stage
        assert "Missing corroboration alone does not prove" in stage


def test_uncertain_public_fact_recollection_cannot_be_presented_as_external_evidence():
    for stage in (semantics.JUDGE_SYSTEM, semantics.UPGRADE_SYSTEM, semantics.DEFENSE_SYSTEM):
        assert "uncertain alternative is unestablished" in stage
        assert "model's memory as an observed source" in stage


def test_unknown_units_cannot_verify_a_declared_conversion_by_numeric_similarity():
    view = build_packet({"question": "Check the journey distance", "history": [
        {"tool_calls": [{"name": "measure", "arguments": {}}], "tool_responses": [
            {"entity": "distance", "attribute": "value", "value": 780}]},
        {"content": {"entity": "distance", "attribute": "value", "value": 484.67, "unit": "miles",
                     "source_ref": "event_001_tool_01"}},
    ]})
    graph = build_observed_dependencies(view, policy=validate_causal_config({}, base_dir="."))
    assert not any(edge["relation"] == "declared_field_consumption" for edge in graph["edges"])


def test_long_reasoning_request_cannot_request_eighty_thousand_output_tokens():
    route, _ = bound_request_route({"max_tokens": 80000}, system="s", user="x" * 50000)
    assert route["max_tokens"] == 8192
