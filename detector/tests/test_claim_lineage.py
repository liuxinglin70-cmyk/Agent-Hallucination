from copy import deepcopy
import json

import pytest

from detector.causal_audit import context_for_hearing
from detector.causal_contracts import seal
from detector.causal_graph import build_observed_dependencies
from detector.identity import digest
from detector.packet import build_packet
from detector.runtime import added_budget_scope
from detector.step_tracer import run_step_tracer
from detector.tests.test_causal_context import config, diagnostics, positive, tracer_reply


def value_field(entity="result", source="event_001_tool_01", value=5):
    return {"entity": entity, "attribute": "value", "value": value, "source_ref": source}


def lineage_record():
    return {"question": "Calculate 2+2", "history": [
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": ["5"]},
        {"role": "assistant", "content": value_field()},
        {"role": "assistant", "content": value_field(source="event_002")},
    ]}


def context(data=None, cfg=None):
    packet = build_packet(data or lineage_record())
    cfg = cfg or config()
    target = positive(evidence_refs=["event_001_tool_01"])
    ctx = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=target,
                              packet=packet, policy=cfg["causal_audit"])
    return packet, cfg, target, ctx


def test_graph_edges_identify_the_exact_source_and_target_fields():
    packet = build_packet(lineage_record())
    graph = build_observed_dependencies(packet, policy=config()["causal_audit"])
    copied = [edge for edge in graph["edges"] if edge["relation"].startswith("declared_")]
    assert copied[0]["field"] == {
        "binding": ["result", "value", None, None], "value": 5,
        "source_path": None, "target_path": [],
    }
    assert copied[1]["field"]["source_path"] == []
    assert copied[1]["field"]["target_path"] == []


def test_context_binds_observed_path_to_the_actual_hearing():
    packet, cfg, target, ctx = context()
    trace = ctx["observed_lineage"]
    assert trace["packet_digest"] == digest(packet)
    assert trace["policy_digest"] == digest(cfg["causal_audit"])
    assert trace["target_digest"] == digest(target)
    assert trace["claim_binding_digest"] == ctx["claim_binding"]["digest"]
    assert trace["observed_event_ids"] == ["event_002", "event_003"]
    assert trace["first_observed_assistant_event_id"] == "event_002"
    assert trace["delivery_event_ids"] == ["event_003"]
    assert trace["materiality"] == trace["first_causal_introduction"] == "unknown"
    assert trace["decision_authority"] == trace["localization_authority"] == "none"


def test_shared_event_with_same_values_cannot_join_unrelated_fields():
    data = lineage_record()
    data["history"][1]["content"] = [value_field(), value_field("other")]
    data["history"][2]["content"] = value_field("other", source="event_002")
    _, _, _, ctx = context(data)
    # Both declarations reference this same result, but each propagation edge keeps its binding.
    edges = ctx["observed_lineage"]["edges"]
    propagated = [edge for edge in edges if edge["relation"] == "declared_field_consumption"]
    assert len(propagated) == 1
    assert propagated[0]["field"]["binding"][0] == "other"
    assert propagated[0]["field"]["source_path"] == ["1"]


def test_shared_event_cannot_bridge_from_one_arithmetic_result_to_another():
    data = lineage_record()
    data["history"][0]["tool_calls"].append({"name": "calculator", "arguments": {"expr": "8/2"}})
    data["history"][0]["tool_responses"].append("5")
    data["history"][1]["content"] = [value_field(), value_field("other", source="event_001_tool_02")]
    data["history"][2]["content"] = value_field("other", source="event_002")
    _, _, _, ctx = context(data)
    trace = ctx["observed_lineage"]
    assert trace["observed_event_ids"] == ["event_002"]
    assert trace["delivery_event_ids"] == []
    assert len(trace["edges"]) == 1
    assert trace["edges"][0]["field"]["target_path"] == ["0"]


@pytest.mark.parametrize("part", ["edges", "first", "delivery", "binding", "materiality", "authority"])
def test_resealed_lineage_must_be_recomputed_before_provider(monkeypatch, part):
    packet, cfg, target, ctx = context()
    trace = ctx["observed_lineage"]
    if part == "edges":
        trace["edges"].clear()
    elif part == "first":
        trace["first_observed_assistant_event_id"] = "event_001"
    elif part == "delivery":
        trace["delivery_event_ids"] = []
    elif part == "binding":
        trace["edges"][0]["field"]["binding"][0] = "other"
    elif part == "materiality":
        trace["materiality"] = "verified"
    else:
        trace["localization_authority"] = "authoritative"
    ctx["observed_lineage"] = seal({key: value for key, value in trace.items() if key != "digest"})
    ctx = seal({key: value for key, value in ctx.items() if key != "digest"})
    monkeypatch.setattr("detector.step_tracer.provider_call", lambda **kwargs: pytest.fail("forged lineage reached provider"))
    with pytest.raises(ValueError, match="observed claim lineage"):
        run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                        positive=target, causal_context=ctx)


def test_validated_lineage_reaches_tracer_within_the_existing_case_budget(monkeypatch):
    packet, cfg, target, ctx = context()
    seen = []

    def provider(**kwargs):
        seen.append(kwargs["user"])
        return tracer_reply()

    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    with added_budget_scope(cfg["causal_audit"]) as budget:
        result, status = run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                                        positive=target, causal_context=ctx)
        assert budget.snapshot()["reserved_input_bytes"] > len(ctx["digest"])
    assert result["ok"] and status["state"] == "ok"
    assert result["causal_context_digest"] == ctx["digest"]
    assert len(seen) == 1 and '"observed_lineage"' in seen[0]


def test_correct_observation_does_not_clear_a_stale_declared_delivery():
    data = lineage_record()
    data["history"].insert(2, {
        "role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
        "tool_responses": ["4"],
    })
    _, _, _, ctx = context(data)
    trace = ctx["observed_lineage"]
    assert trace["consistent_observation_refs"] == ["event_003_tool_01"]
    assert trace["delivery_event_ids"] == ["event_004"]
    assert trace["correction_effect"] == "unknown"


@pytest.mark.parametrize("limit,value,reason", [
    ("max_dependency_depth", 1, "lineage_depth_budget"),
    ("max_graph_edges", 1, "graph_budget"),
])
def test_incomplete_lineage_retains_budget_reasons(limit, value, reason):
    _, _, _, ctx = context(cfg=config(**{limit: value}))
    trace = ctx["observed_lineage"]
    assert trace["closure_state"] == "unknown"
    assert reason in trace["reasons"]


def test_unstructured_repetition_has_no_verified_lineage():
    data = lineage_record()
    data["history"][1]["content"] = "The answer is 5"
    data["history"][2]["content"] = "5"
    _, _, _, ctx = context(data)
    trace = ctx["observed_lineage"]
    assert trace["edges"] == trace["observed_event_ids"] == trace["delivery_event_ids"] == []
    assert trace["first_observed_assistant_event_id"] is None
    assert trace["first_causal_introduction"] == "unknown"


def test_status_tool_is_not_a_delivered_value_anchor():
    data = lineage_record()
    data["history"].append({"role": "assistant", "tool_calls": [{
        "name": "terminate", "arguments": value_field(source="event_003"),
    }], "tool_responses": ["done"]})
    _, _, _, ctx = context(data)
    trace = ctx["observed_lineage"]
    assert "event_004" in trace["observed_event_ids"]
    assert trace["delivery_event_ids"] == ["event_003"]


@pytest.mark.parametrize("change", [
    {"scope": "complete_causal_proof"}, {"closure_state": "verified"},
    {"deployment_instruction": "use the earliest numeric match"},
])
def test_context_fields_and_scope_are_closed_before_provider(monkeypatch, change):
    packet, cfg, target, ctx = context()
    ctx.update(change)
    ctx = seal({key: value for key, value in ctx.items() if key != "digest"})
    monkeypatch.setattr("detector.step_tracer.provider_call", lambda **kwargs: pytest.fail("unknown context fields reached provider"))
    with pytest.raises(ValueError, match="contract fields"):
        run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                        positive=target, causal_context=ctx)


def test_hearing_context_cannot_reconstruct_lineage_with_another_policy():
    packet = build_packet(lineage_record())
    cfg = config()
    audit = diagnostics(packet, cfg)["audit"]
    with pytest.raises(ValueError, match="policy identity"):
        context_for_hearing(audit, positive=positive(), packet=packet,
                            policy=config(max_dependency_depth=1)["causal_audit"])


def test_detector_hearing_delivers_and_replays_the_actual_lineage_context(monkeypatch):
    from detector.api import Detector
    from detector.tests.test_replay_inputs import setup_stages
    from evolution.backends import LiveDetectorBackend

    setup_stages(monkeypatch)
    seen = []

    def provider(**kwargs):
        seen.append(json.loads(kwargs["user"].split("CAUSAL CONTEXT: ", 1)[1].split("\n", 1)[0]))
        return tracer_reply()

    monkeypatch.setattr("detector.hearing.run_step_tracer", run_step_tracer)
    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    detector = Detector(config(), require_credentials=False)
    first = detector.detect(lineage_record())
    frozen = LiveDetectorBackend._frozen_stages(first, set())
    preserved = deepcopy(frozen)
    second = detector.detect(lineage_record(), frozen_stages=frozen)
    assert len(seen) == 1
    assert seen[0]["observed_lineage"]["delivery_event_ids"] == ["event_003"]
    assert first["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"] == seen[0]["digest"]
    assert second["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"] == seen[0]["digest"]
    assert frozen == preserved


def test_dictionary_key_order_does_not_change_lineage_or_context_identity():
    data = lineage_record()
    data["history"][1]["content"] = {
        "primary": value_field(), "secondary": value_field("other"),
    }
    changed = deepcopy(data)
    changed["history"][1]["content"] = dict(reversed(list(data["history"][1]["content"].items())))
    packet, _, _, ctx = context(data)
    other_packet, _, _, other_ctx = context(changed)
    assert digest(packet) == digest(other_packet)
    assert ctx["observed_lineage"] == other_ctx["observed_lineage"]
    assert ctx["digest"] == other_ctx["digest"]
