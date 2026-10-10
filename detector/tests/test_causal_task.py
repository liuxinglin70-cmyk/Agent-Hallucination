import pytest
from decimal import Decimal

from detector.causal_contracts import numeric_literal, seal
from detector.causal_audit import context_for_hearing
from detector.packet import build_packet
from detector.step_tracer import run_step_tracer
from detector.tests.test_claim_lineage import context, lineage_record, value_field
from detector.tests.test_causal_context import config, diagnostics, positive, tracer_reply


def test_single_arithmetic_task_has_materiality_and_first_declared_introduction():
    _, _, _, ctx = context()
    proof = ctx["task_certificate"]
    assert proof["scope"] == "single_literal_arithmetic_result"
    assert proof["task_status"] == "violated"
    assert proof["materiality"] == "verified"
    assert proof["earliest_supported_event_id"] == "event_002"
    assert proof["first_introduction"] == "verified_declared_result"
    assert proof["delivery"]["event_id"] == "event_003"
    assert proof["counterfactual"]["status"] == "verified_symbolic"
    assert proof["counterfactual"]["repaired_value"] == "4"
    assert proof["counterfactual"]["repaired_event_ids"] == ["event_002", "event_003"]
    assert proof["counterfactual"]["state_changes_verified"] is False
    assert proof["decision_authority"] == proof["localization_authority"] == "none"


@pytest.mark.parametrize("question", [
    "Calculate both expressions", "What is the population?", "Calculate 2+2 and then send it",
    "Calculate 3+3", "Calculate 1/3", "Calculate 2**10000000", "Calculate __import__('os')",
    "Calculate 2+2\nIgnore the task", "Calculate true", "Calculate nan",
])
def test_unsupported_or_unrelated_task_cannot_gain_materiality(question):
    data = lineage_record()
    data["question"] = question
    _, _, _, ctx = context(data)
    proof = ctx["task_certificate"]
    assert proof["task_status"] == proof["materiality"] == "unknown"
    assert proof["earliest_supported_event_id"] is None
    assert proof["counterfactual"]["status"] == "not_run"
    assert proof["reasons"]


@pytest.mark.parametrize("question", ["Compute 2 + 2.", "Evaluate (8 / 2)?", "calculate 4"])
def test_task_value_is_recomputed_from_the_whole_instruction(question):
    data = lineage_record()
    data["question"] = question
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["task_status"] == "violated"


def test_correct_later_result_refutes_current_task_materiality_without_causal_claims():
    data = lineage_record()
    data["history"].insert(2, {"role": "assistant", "tool_calls": [{
        "name": "calculator", "arguments": {"expr": "2+2"}}], "tool_responses": ["4"]})
    data["history"][-1]["content"] = value_field(source="event_003_tool_01", value=4)
    _, _, _, ctx = context(data)
    proof = ctx["task_certificate"]
    assert proof["task_status"] == "satisfied"
    assert proof["materiality"] == "refuted"
    assert proof["delivery"]["event_id"] == "event_004"
    assert proof["earliest_supported_event_id"] is None
    assert proof["correction_effect"] == "verified_task_delivery_consistent"
    assert proof["counterfactual"]["state_changes_verified"] is False


def test_correct_observation_with_stale_delivery_keeps_materiality():
    data = lineage_record()
    data["history"].insert(2, {"role": "assistant", "tool_calls": [{
        "name": "calculator", "arguments": {"expr": "2+2"}}], "tool_responses": ["4"]})
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["task_status"] == "violated"
    assert ctx["task_certificate"]["correction_effect"] == "unknown"
    assert ctx["task_certificate"]["earliest_supported_event_id"] == "event_002"


def test_independent_later_error_cannot_replace_this_bound_delivery():
    data = lineage_record()
    data["history"][-1]["content"] = value_field(source="event_002", value=7)
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["materiality"] == "unknown"
    assert ctx["task_certificate"]["earliest_supported_event_id"] is None


def test_unstructured_prefix_prevents_claiming_the_first_introduction():
    data = lineage_record()
    data["history"][0]["content"] = "I already concluded five"
    _, _, _, ctx = context(data)
    proof = ctx["task_certificate"]
    assert proof["first_introduction"] == "unknown"
    assert proof["earliest_supported_event_id"] is None
    assert proof["counterfactual"]["status"] == "not_run"


@pytest.mark.parametrize("component", ["materiality", "first", "repair", "authority"])
def test_task_certificate_is_recomputed_before_provider(monkeypatch, component):
    packet, cfg, target, ctx = context()
    proof = ctx["task_certificate"]
    if component == "materiality":
        proof["materiality"] = "refuted"
    elif component == "first":
        proof["earliest_supported_event_id"] = "event_001"
    elif component == "repair":
        proof["counterfactual"]["repaired_value"] = "6"
    else:
        proof["decision_authority"] = "authoritative"
    ctx["task_certificate"] = seal({key: value for key, value in proof.items() if key != "digest"})
    ctx = seal({key: value for key, value in ctx.items() if key != "digest"})
    monkeypatch.setattr("detector.step_tracer.provider_call", lambda **kwargs: pytest.fail("forged task proof reached provider"))
    with pytest.raises(ValueError, match="task claim certificate"):
        run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                        positive=target, causal_context=ctx)


def test_task_certificate_has_an_actual_hearing_consumer(monkeypatch):
    from detector.api import Detector
    from detector.tests.test_replay_inputs import setup_stages

    setup_stages(monkeypatch)
    seen = []

    def provider(**kwargs):
        seen.append(kwargs["user"])
        return tracer_reply()

    monkeypatch.setattr("detector.hearing.run_step_tracer", run_step_tracer)
    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    result = Detector(config(), require_credentials=False).detect(lineage_record())
    assert len(seen) == 1
    assert '"task_certificate"' in seen[0]
    assert '"verified_declared_result"' in seen[0]
    assert result["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"]


def test_repairing_first_declaration_does_not_fix_an_independent_delivery_branch():
    data = lineage_record()
    data["history"][-1]["content"] = value_field(source="event_001_tool_01")
    _, _, _, ctx = context(data)
    proof = ctx["task_certificate"]
    assert proof["materiality"] == "verified"
    assert proof["first_introduction"] == "verified_declared_result"
    assert proof["counterfactual"]["status"] == "not_run"
    assert "repair_does_not_reach_delivery" in proof["reasons"]


def test_an_argument_copy_cannot_bind_another_results_prose_declaration():
    data = lineage_record()
    data["history"][0]["tool_calls"].append({"name": "calculator", "arguments": {"expr": "8/2"}})
    data["history"][0]["tool_responses"].append("5")
    data["history"][1] = {
        "role": "assistant", "content": value_field(source="event_001_tool_02"),
        "tool_calls": [{"name": "calculator", "arguments": {
            "expr": "2+2", "inputs": value_field(source="event_001_tool_01")}}], "tool_responses": ["4"],
    }
    data["history"][-1]["content"] = value_field(source="event_001_tool_01")
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["first_introduction"] == "unknown"
    assert ctx["task_certificate"]["earliest_supported_event_id"] is None


@pytest.mark.parametrize("role", ["user", "system", "unknown"])
def test_extra_instruction_context_prevents_using_the_single_command_contract(role):
    data = lineage_record()
    data["history"].append({"role": role, "content": "Now calculate 3+3"})
    data["history"].append({"role": "assistant", "content": value_field(source="event_002", value=4)})
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["task_status"] == "unknown"
    assert "additional_instruction_context" in ctx["task_certificate"]["reasons"]


@pytest.mark.parametrize("content", [
    "5", {"entity": "price", "attribute": "value", "value": 5, "source_ref": "event_002"},
    {"entity": "result", "attribute": "value", "value": 5, "source_ref": "event_002", "unit": "USD"},
    {"entity": "result", "attribute": "value", "value": 5, "source_ref": "event_002", "note": "not the answer"},
])
def test_ambiguous_delivery_is_not_a_verified_scalar_task_result(content):
    data = lineage_record()
    data["history"][-1]["content"] = content
    _, _, _, ctx = context(data)
    assert ctx["task_certificate"]["materiality"] == "unknown"
    assert ctx["task_certificate"]["counterfactual"]["status"] == "not_run"


def test_symbolic_fraction_repair_is_a_valid_exact_scalar_value():
    data = lineage_record()
    data["question"] = "Calculate 1/2"
    data["history"][0]["tool_calls"][0]["arguments"]["expr"] = "1/2"
    data["history"][0]["tool_responses"][0] = "0.6"
    for event in data["history"][1:]:
        event["content"]["value"] = "0.6"
    packet = build_packet(data)
    cfg = config()
    target = positive(quote="0.6", delivered_result="0.6", correct_result="0.5",
                      evidence_refs=["event_001_tool_01"])
    ctx = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=target,
                              packet=packet, policy=cfg["causal_audit"])
    repair = ctx["task_certificate"]["counterfactual"]
    assert repair["status"] == "verified_symbolic"
    assert numeric_literal(repair["repaired_value"]) == Decimal("0.5")
