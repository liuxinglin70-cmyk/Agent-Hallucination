from copy import deepcopy
from dataclasses import replace

import pytest

from detector.next_action import build_next_action_audit, compare_next_action, observation_failed, predict_action_boundary
from detector.packet import build_packet


def failure_record(content="The operation was completed.", *, retry=None):
    action = {"role": "assistant", "content": content}
    if retry is not None:
        action.update(tool_calls=[{"name": "launch", "arguments": {"mode": "START"}}], tool_responses=[retry])
    return {"question": "Start the service and report its state.", "history": [
        {"role": "assistant", "tool_calls": [{"name": "launch", "arguments": {}}],
         "tool_responses": [{"error": "invalid mode"}]}, action,
    ]}


@pytest.mark.parametrize("observation,failed", [
    ({"error": "invalid input"}, True), ('{"success": false}', True),
    ("Error: invalid input", True), ({"status": "failed"}, True),
    ({"status": "success"}, False), ({"error": None}, False),
    ("No error was encountered", False), (None, False),
    ("Documentation describes an Error: field", False), ("{}", False),
    ("Execution logs:\nError during execution: invalid currency", True),
    ("Error fetching the webpage: 403 Forbidden", True),
])
def test_explicit_failure_boundary(observation, failed):
    assert observation_failed(observation) is failed


@pytest.mark.parametrize("content,retry,state,pattern", [
    ("The operation was completed.", None, "divergent", "success_claim_without_observed_recovery"),
    ("The operation was completed.", {"success": True}, "compliant", "observed_retry_or_repair"),
    ("Retrying", {"error": "still unavailable"}, "compliant", "observed_retry_or_repair"),
    ("The operation was completed.", {"status": "pending"}, "divergent", "success_claim_without_observed_recovery"),
    ("Unable to start because the tool returned an error.", None, "compliant", "honest_failure_report"),
    ("Not successfully completed; the error remains.", None, "compliant", "honest_failure_report"),
    ("If the task is completed, I will report it.", None, "unknown", "unrelated_or_unrecognized_action"),
    ("Let me inspect another item.", None, "unknown", "unrelated_or_unrecognized_action"),
])
def test_recorded_single_next_action(content, retry, state, pattern):
    packet = build_packet(failure_record(content, retry=retry))
    before = deepcopy(packet)
    audit = build_next_action_audit(packet, ["event_001"])
    row = audit["comparisons"][0]
    assert row["action_state"] == state
    assert row["divergence_pattern"] == pattern
    assert row["next_position"] == 2
    assert row["decision_authority"] == row["localization_authority"] == "none"
    assert audit["provider_requests"] == 0
    assert packet == before


def test_prediction_never_reads_future_and_comparison_checks_prefix():
    packet = build_packet(failure_record())
    boundary = predict_action_boundary(packet, "event_001")
    changed = deepcopy(packet)
    changed["events"][1]["content"] = "I cannot proceed because of an error."
    assert predict_action_boundary(changed, "event_001") == boundary
    assert compare_next_action(changed, boundary)["action_state"] == "compliant"
    changed["question"] = "Different task"
    with pytest.raises(ValueError, match="prefix"):
        compare_next_action(changed, boundary)
    with pytest.raises(ValueError, match="prefix"):
        compare_next_action(packet, replace(boundary, failed_tools=("forged",)))


def test_terminal_failure_has_no_invented_observation():
    record = failure_record()
    record["history"].pop()
    row = build_next_action_audit(build_packet(record), ["event_001"])["comparisons"][0]
    assert row["next_event_id"] is None
    assert row["action_state"] == "unknown"


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="absent"):
        build_next_action_audit(build_packet(failure_record()), ["event_999"])


def test_tracer_context_recomputes_next_action_evidence():
    from detector.causal_contracts import seal
    from detector.config import validate_causal_config
    from detector.risk_context import build_risk_context, validate_risk_context
    policy = validate_causal_config({"max_added_prompt_tokens_per_case": 24000}, base_dir=".")
    packet = build_packet(failure_record())
    positive = {"step": 2, "evidence_refs": ["event_002"], "quote": "The operation was completed."}
    context = build_risk_context(packet, positive=positive, policy=policy)
    assert context is not None
    comparisons = [candidate["next_action_audit"]["comparisons"][0]
                   for candidate in context["candidates"] if "next_action_audit" in candidate]
    assert len(comparisons) == 1
    assert comparisons[0]["action_state"] == "divergent"
    validate_risk_context(context, packet=packet, positive=positive, policy=policy)
    changed = deepcopy(context)
    candidate = next(candidate for candidate in changed["candidates"] if "next_action_audit" in candidate)
    candidate["next_action_audit"]["comparisons"][0]["action_state"] = "compliant"
    changed = seal({key: value for key, value in changed.items() if key != "digest"})
    with pytest.raises(ValueError, match="differs"):
        validate_risk_context(changed, packet=packet, positive=positive, policy=policy)


def test_static_screen_reaches_failure_after_the_projection_capacity():
    from detector.config import validate_causal_config
    from detector.next_action import scan_static_action_risks
    from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates
    record = failure_record()
    record["history"] = [{"role": "assistant", "content": "continue"} for _ in range(130)] + record["history"]
    packet = build_packet(record)
    scan = scan_static_action_risks(packet, top_k=1)
    assert scan["scanned_events"] == 132
    assert [row["event_id"] for row in scan["candidate_rows"]] == ["event_131"]
    policy = validate_causal_config({"top_k": 1}, base_dir=".")
    profile = build_step_risk_profile(packet, policy=policy)
    selection = select_causal_candidates(profile, mandatory_event_ids=[], policy=policy)
    assert selection["top_k_event_ids"] == ["event_131"]


def test_terminal_success_action_after_failure_is_a_diagnostic_divergence():
    record = failure_record("")
    record["history"][1].update(tool_calls=[{"name": "terminate", "arguments": {"status": "success"}}],
                                tool_responses=["The interaction has been completed with status: success"])
    row = build_next_action_audit(build_packet(record), ["event_001"])["comparisons"][0]
    assert row["action_state"] == "divergent"
    assert row["decision_authority"] == "none"


def test_observed_alternative_success_does_not_establish_fallback_illegality():
    record = failure_record()
    record["history"][1].update(tool_calls=[{"name": "alternative", "arguments": {}}],
                                tool_responses=[{"success": True}])
    row = build_next_action_audit(build_packet(record), ["event_001"])["comparisons"][0]
    assert row["action_state"] == "unknown"
    assert row["divergence_pattern"] == "possible_fallback_unverified"
