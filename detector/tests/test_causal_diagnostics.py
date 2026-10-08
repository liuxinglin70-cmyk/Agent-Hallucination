from copy import deepcopy

import pytest

from detector.causal_contracts import arithmetic_check
from detector.causal_graph import build_observed_dependencies, project_dependency_closure
from detector.config import validate_causal_config
from detector.packet import build_packet
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates


def policy(**changes):
    return validate_causal_config({"mode": "shadow", **changes}, base_dir=".")


def packet(*events, question="Report the recorded result"):
    return build_packet({"question": question, "history": list(events)})


def field(value=4, **changes):
    return {"entity": "order", "attribute": "amount", "value": value,
            "unit": "USD", "time": "2026-10-08", **changes}


def project(p, cfg, mandatory=()):
    profile = build_step_risk_profile(p, policy=cfg)
    candidates = select_causal_candidates(profile, mandatory_event_ids=mandatory, policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    return graph, project_dependency_closure(p, candidates=candidates, graph=graph, policy=cfg)


def test_risk_orders_local_verification_before_unverified_content():
    p = packet({"role": "assistant", "content": "Maybe four"},
               {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
                "tool_responses": ["5"]})
    original = deepcopy(p)
    cfg = policy()
    profile = build_step_risk_profile(p, policy=cfg)
    selected = select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)
    assert selected["top_k_event_ids"] == ["event_002", "event_001"]
    signal = profile["by_event"][1]["risk_signals"][0]
    assert signal["verification_status"] == "verified"
    assert signal["responsibility"] == "tool_observation"
    assert signal["certificate"]["scope"] == "local_result"
    assert p == original


@pytest.mark.parametrize("expression, observed, expected", [
    ("2+2", "4", "consistent"), ("2+2", "5", "deviation"),
    ("0.1+0.2", "0.3", "consistent"), ("1/8", "0.125", "consistent"),
    ("1/3", "0.333", None), ("1/0", "0", None),
    ("2**100", "5", None), ("__import__('os').getcwd()", "5", None),
    ("nan", "NaN", None), ("4", {"value": "4"}, None),
])
def test_arithmetic_checks_only_replayable_literal_results(expression, observed, expected):
    check = arithmetic_check({"interaction_id": "event_001_tool_01", "tool_name": "calculator",
                              "arguments": {"expr": expression}, "observation": observed})
    assert (check["status"] if check else None) == expected


def test_missing_action_is_task_signal_without_step_blame():
    p = packet({"role": "assistant", "content": "Completed"},
               {"role": "assistant", "content": "Done"}, question="You must call the search tool")
    profile = build_step_risk_profile(p, policy=policy())
    assert len(profile["task_signals"]) == 1
    assert profile["task_signals"][0]["locus_event_id"] is None
    assert all(signal["signal"] != "action_precondition" for row in profile["by_event"]
               for signal in row["risk_signals"])


def test_unknown_role_missing_content_and_ppl_remain_unknown():
    p = packet({"role": "unrecognized", "content": "4"}, {"role": "assistant"},
               {"role": "tool", "content": "4"})
    profile = build_step_risk_profile(p, policy=policy())
    assert [row["score_status"] for row in profile["by_event"]] == ["unknown"] * 3
    assert profile["by_event"][2]["step_kind"] == "tool_observation"
    profile = build_step_risk_profile(p, policy=policy(score_source="teacher_forced_ppl"))
    assert profile["health"]["state"] == "unsupported"
    assert all(row["score_status"] == "unsupported" for row in profile["by_event"])


@pytest.mark.parametrize("changes, conflict", [
    ({"value": 5}, True), ({"value": 5, "unit": "EUR"}, False),
    ({"value": 5, "time": "2026-10-09"}, False), ({"value": 5, "entity": "other"}, False),
])
def test_field_conflicts_require_same_context_and_do_not_verify_deviation(changes, conflict):
    p = packet({"role": "tool", "content": field()}, {"role": "assistant", "content": field(**changes)})
    profile = build_step_risk_profile(p, policy=policy())
    signals = [signal for row in profile["by_event"] for signal in row["risk_signals"]
               if signal["signal"] == "bound_field_conflict"]
    assert bool(signals) == conflict
    assert all(signal["verification_status"] == "hypothesis" for signal in signals)


def test_mandatory_candidates_are_separate_and_budget_omission_is_unknown():
    p = packet(*[{"role": "assistant", "content": str(index)} for index in range(4)])
    cfg = policy(top_k=1, max_projection_events=1)
    profile = build_step_risk_profile(p, policy=cfg)
    selected = select_causal_candidates(profile, mandatory_event_ids=["event_004", "event_003"], policy=cfg)
    assert selected["selected_event_ids"] == ["event_004"]
    assert selected["mandatory_event_ids"] == ["event_004", "event_003"]
    assert "event_003" in selected["omitted_event_ids"]
    assert selected["state"] == "unknown"


def test_dependencies_follow_distant_observation_with_actual_bound_value():
    p = packet({"role": "assistant", "tool_calls": [{"name": "read", "arguments": {}}],
                "tool_responses": [field()]},
               {"role": "assistant"}, {"role": "assistant"},
               {"role": "assistant", "content": field(source_ref="event_001_tool_01")})
    graph, projection = project(p, policy(top_k=1), ["event_004"])
    edges = [edge for edge in graph["edges"] if edge["relation"] == "declared_field_consumption"]
    assert len(edges) == 1
    assert edges[0]["source"] == "event_001_tool_01:observation"
    assert graph["complete"] is True
    assert projection["ancestor_event_ids"] == ["event_001"]
    assert projection["selected_event_ids"] == ["event_001", "event_002", "event_003", "event_004"]
    assert projection["closure_state"] == "complete"
    projection["events"][0]["tool_interactions"][0]["observation"]["value"] = 99
    assert p["events"][0]["tool_interactions"][0]["observation"]["value"] == 4


def test_arguments_cannot_consume_the_same_calls_later_observation():
    p = packet({"role": "assistant", "tool_calls": [{"name": "read", "arguments": field(
        source_ref="event_001_tool_01")}], "tool_responses": [field()]})
    graph = build_observed_dependencies(p, policy=policy())
    assert not any(edge["relation"] == "declared_field_consumption" for edge in graph["edges"])
    assert "source_not_preceding" in {row["reason"] for row in graph["unresolved_dependencies"]}


def test_matching_words_and_separate_tool_role_do_not_create_consumption():
    p = packet({"role": "tool", "content": "price 4"},
               {"role": "assistant", "content": "price 4"})
    graph, projection = project(p, policy(), ["event_002"])
    assert graph["edges"] == []
    assert graph["nodes"][0]["kind"] == "observation"
    assert projection["closure_state"] == "unknown"


@pytest.mark.parametrize("source_ref, changes", [
    ("not_real", {}), ("event_001", {"value": 9}), ("event_001", {"unit": "EUR"}),
    ("event_001", {"time": "tomorrow"}), ("event_002", {}),
])
def test_unresolved_sources_cannot_be_complete(source_ref, changes):
    p = packet({"role": "tool", "content": field()},
               {"role": "assistant", "content": field(source_ref=source_ref, **changes)})
    graph, projection = project(p, policy(), ["event_002"])
    assert not graph["complete"]
    assert projection["closure_state"] == "unknown"


def test_projection_budgets_preserve_mandatory_omissions_and_depth():
    p = packet({"role": "tool", "content": field()},
               {"role": "tool", "content": field(source_ref="event_001")},
               {"role": "tool", "content": field(source_ref="event_002")},
               {"role": "tool", "content": field(source_ref="event_003")})
    graph, projection = project(p, policy(top_k=1, max_dependency_depth=1), ["event_004"])
    assert "dependency_depth_budget" in projection["reasons"]
    assert projection["closure_state"] == "unknown"
    graph, projection = project(p, policy(top_k=1, max_projection_events=2), ["event_004"])
    assert len(projection["events"]) <= 2
    assert projection["omitted_event_ids"]


def test_graph_budget_and_missing_observation_are_explicit():
    p = packet({"role": "assistant", "tool_calls": [{"name": "read", "arguments": {}}]},
               {"role": "assistant", "tool_calls": [{"name": "read", "arguments": {}}], "tool_responses": ["4"]},
               {"role": "assistant", "tool_calls": [{"name": "read", "arguments": {}}], "tool_responses": ["5"]})
    graph = build_observed_dependencies(p, policy=policy(max_graph_edges=1))
    assert len(graph["edges"]) == 1
    assert len(graph["omitted_edges"]) == 1
    assert "missing_observation" in {row["reason"] for row in graph["unresolved_dependencies"]}
    assert not graph["complete"]


def test_branch_and_join_preserve_both_observed_sources():
    p = packet({"role": "tool", "content": [field(entity="a"), field(entity="b")]},
               {"role": "assistant", "content": field(entity="a", source_ref="event_001")},
               {"role": "assistant", "content": field(entity="b", source_ref="event_001")},
               {"role": "assistant", "content": [field(entity="a", source_ref="event_002"),
                                                     field(entity="b", source_ref="event_003")]})
    graph, projection = project(p, policy(top_k=1), ["event_004"])
    assert {edge["source"] for edge in graph["edges"] if edge["target"] == "event_004"} == {
        "event_002", "event_003"}
    assert set(projection["ancestor_event_ids"]) == {"event_001", "event_003"}
    assert graph["complete"]


def test_corrected_value_does_not_erase_actual_reuse_of_old_source():
    p = packet({"role": "tool", "content": field(value=5)},
               {"role": "tool", "content": field(value=4)},
               {"role": "assistant", "content": field(value=5, source_ref="event_001")})
    graph, projection = project(p, policy(top_k=1), ["event_003"])
    edge = next(edge for edge in graph["edges"] if edge["target"] == "event_003")
    assert edge["source"] == "event_001"
    assert edge["relation"] == "declared_field_consumption"
    assert "event_001" in projection["ancestor_event_ids"]
    assert not any(edge["source"] == "event_002" for edge in graph["edges"])


def test_char_budget_and_unscanned_candidates_remain_explicit():
    p = packet({"role": "assistant", "content": "x" * 1000}, question="q" * 1000)
    cfg = policy(max_added_prompt_tokens_per_case=100)
    profile = build_step_risk_profile(p, policy=cfg)
    assert profile["by_event"][0]["score_status"] == "truncated"
    _, projection = project(p, cfg, ["event_001"])
    assert projection["question"] == ""
    assert "question_budget" in projection["reasons"]
    assert projection["omitted_event_ids"] == ["event_001"]
    assert projection["budget"]["characters"] <= 100
    assert projection["closure_state"] == "unknown"


def test_cycle_of_claimed_sources_has_no_verified_back_edge():
    p = packet({"role": "assistant", "content": field(source_ref="event_002")},
               {"role": "assistant", "content": field(source_ref="event_001")})
    graph, projection = project(p, policy(), ["event_002"])
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [("event_001", "event_002")]
    assert not graph["complete"]
    assert projection["closure_state"] == "unknown"


def test_projection_rejects_tampering_and_different_inputs():
    p = packet({"role": "assistant", "content": "4"})
    cfg = policy()
    profile = build_step_risk_profile(p, policy=cfg)
    selected = select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    graph["complete"] = True
    with pytest.raises(ValueError, match="identity"):
        project_dependency_closure(p, candidates=selected, graph=graph, policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    p["events"][0]["content"] = "5"
    with pytest.raises(ValueError, match="different packet"):
        project_dependency_closure(p, candidates=selected, graph=graph, policy=cfg)


def test_production_shadow_diagnostics_leave_base_verdict_and_calls_unchanged(monkeypatch):
    from detector.api import Detector
    from detector.tests.test_api import CONFIG

    calls = []
    def fake_judge(**kwargs):
        calls.append("judge")
        return {"ok": True, "parsed": {"is_hallucination": False, "confidence": "high",
                "contracts": [], "evidence_refs": []}, "provider": {"vote": {
                    "cast": 3, "positives": 0, "tie": False}}}, {
            "stage": "judge", "state": "ok", "attempts": 1, "view": "complete", "warnings": []}

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    record = {"question": "Report four", "history": [{"role": "assistant", "content": "Four"}]}
    config = {**CONFIG, "judge_route": {**CONFIG["judge_route"], "votes": 3}}
    base = Detector(config, require_credentials=False).detect(record)
    shadow = Detector({**config, "causal_audit": {"mode": "shadow"}}, require_credentials=False).detect(record)
    assert calls == ["judge", "judge"]
    assert "causal_diagnostics" not in base["proof"]
    assert shadow["proof"]["causal_diagnostics"]["projection"]["closure_state"] == "unknown"
    for key in ("state", "decision_authority", "responsible_step", "runtime_valid", "stage_status"):
        assert shadow[key] == base[key]
    assert {key: value for key, value in shadow["proof"].items() if key != "causal_diagnostics"} == base["proof"]
