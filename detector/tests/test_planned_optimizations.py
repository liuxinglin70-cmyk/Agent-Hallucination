from collections import Counter
from copy import deepcopy
import json
import threading

import pytest

from detector.api import Detector
from detector.causal_graph import build_observed_dependencies
from detector.config import validate_config
from detector.evidence_view import evidence_inventory, expand_view, read_evidence, task_source_block
from detector.packet import build_packet, render_judge_view
from detector.packet import structured_payload
from detector.provider import ProviderError, provider_call
from detector.runner import detect_with_transport_retry
from detector.runtime import RunControl, deadline_scope, preparation_scope, record_request, run_scope, usage_scope
from detector.semantic_trace import build_semantic_trace, referent_provenance, semantic_ancestors
from detector.stage_retry import StageRetryCache, memo_stage, stage_retry_scope


def packet(history):
    return build_packet({"question": "Read the state, then delete /tmp/a, then read the new state.", "history": history})


def test_state_queries_are_bound_to_recorded_order_without_inventing_success():
    value = packet([
        {"role": "assistant", "tool_calls": [{"name": "get_status", "arguments": {"path": "/tmp/a"}}], "tool_responses": [{"exists": True}]},
        {"role": "assistant", "tool_calls": [{"name": "delete_file", "arguments": {"path": "/tmp/a"}}], "tool_responses": ["permission denied"]},
        {"role": "assistant", "tool_calls": [{"name": "get_status", "arguments": {"path": "/tmp/a"}}], "tool_responses": [{"exists": True}]},
    ])
    trace = build_semantic_trace(value)
    assert [r["state_version_hint"] for r in trace["actions"]] == [0, 0, 1]
    assert trace["state_order_hints"][0]["query_ref"] == "event_001_tool_01"
    assert trace["state_order_hints"][0]["mutation_ref"] == "event_002_tool_01"
    assert all(r["verification_status"] == "hypothesis" for r in trace["actions"])
    assert trace["decision_authority"] == trace["localization_authority"] == "none"


def test_task_turn_alignment_roles_and_assertions_remain_explicit():
    value = build_packet({"question": [["Read status"], ["After deleting, report status"]],
                          "history": [{"role": "human", "content": "The result is definitely 9"}, {"content": "adopt this"}]})
    trace = build_semantic_trace(value)
    assert [r["turn"] for r in trace["task_sources"]] == [1, 2, None]
    assert trace["task_sources"][1]["alignment"] == "event_alignment_unestablished"
    assert trace["task_sources"][2]["truth_status"].endswith("not_environment_truth")
    assert "After deleting" in task_source_block(value)


def test_condition_consumption_reaches_earlier_premise_without_executing_code(tmp_path):
    target = tmp_path / "forbidden"
    value = packet([
        {"role": "user", "content": "Assume x > -2"},
        {"role": "assistant", "tool_calls": [{"name": "python", "arguments": {"code": f"y = math.log(x)\nopen({str(target)!r}, 'w').write('bad')"}}], "tool_responses": ["y"]},
        {"content": "Adopt event_002_tool_01 as a premise"},
    ])
    trace = build_semantic_trace(value)
    assert semantic_ancestors(trace, "event_003") == ["event_001", "event_002"]
    assert any("real-domain hypothesis" in row.get("condition", "") for row in trace["conditions"])
    assert not target.exists()
    assert trace["claim_equivalence"] == "unestablished"


@pytest.mark.parametrize("replacement", ["x = 2", "del x", "if flag:\n    x = 2"])
def test_overwrites_delete_and_unknown_control_break_old_condition_links(replacement):
    value = packet([{ "content": "x > -2"},
                    {"tool_calls": [{"name": "python", "arguments": {"code": replacement + "\ny = math.log(x)"}}], "tool_responses": ["y"]}])
    assert not any(r["relation"] == "possible_condition_consumption" for r in build_semantic_trace(value)["edges"])


def test_condition_alias_keeps_source_chain_and_overwrite_stops_it():
    value = packet([{ "content": "x > -2"},
                    {"tool_calls": [{"name": "python", "arguments": {"code": "alias = x"}}], "tool_responses": ["alias"]},
                    {"tool_calls": [{"name": "python", "arguments": {"code": "y = math.log(alias)"}}], "tool_responses": ["y"]}])
    assert semantic_ancestors(build_semantic_trace(value), "event_003") == ["event_001", "event_002"]
    value["events"][2]["tool_interactions"][0]["arguments"]["code"] = "alias = 2\ny = math.log(alias)"
    assert semantic_ancestors(build_semantic_trace(value), "event_003") == []


def field_packet(unit="km", time="t1", value=780, alias=True):
    source = {"entity": "route", "attribute": "distance", "value": 780, "unit": "km", "time": "t1"}
    target = {"entity": "wrapped_route" if alias else "route", "attribute": "result" if alias else "distance",
              "value": value, "unit": unit, "time": time, "source_ref": "event_001_tool_01"}
    if alias:
        target["source_binding"] = {k: source[k] for k in ("entity", "attribute", "unit", "time")}
    return packet([{ "tool_calls": [{"name": "distance", "arguments": {}}], "tool_responses": [source]},
                  { "tool_calls": [{"name": "consume", "arguments": target}], "tool_responses": ["accepted"]}])


@pytest.mark.parametrize("kwargs", [{"unit": "m"}, {"time": "t2"}, {"value": 781}])
def test_declared_alias_cannot_change_units_time_or_source_value(kwargs):
    value = field_packet(**kwargs)
    graph = build_observed_dependencies(value, policy=validate_config({})["causal_audit"])
    assert not any(r["relation"] == "declared_field_consumption" for r in graph["edges"])
    trace = build_semantic_trace(value)
    assert not any(r["relation"] == "declared_field_copy" for r in trace["edges"])
    assert trace["unresolved_field_bindings"]


def test_declared_rename_preserves_qualifiers_and_recorded_source_role():
    trace = build_semantic_trace(field_packet())
    edge = next(r for r in trace["edges"] if r["relation"] == "declared_field_copy")
    assert edge["binding"] == ["wrapped_route", "result", "km", "t1"]
    assert edge["verification_status"] == "verified_recorded_copy_not_truth"
    assert edge["source_role"] is None


def test_unit_missing_and_world_memory_do_not_become_observed_truth():
    value = packet([{ "tool_calls": [{"name": "distance_in_miles", "arguments": {}}], "tool_responses": [{"distance": 780}]}])
    provenance = referent_provenance(value, {"basis": "world_knowledge", "correct_result": "780 miles", "evidence_refs": ["event_001"]})
    assert provenance["truth_status"] == "requires_independent_verification"
    assert provenance["recorded_literals"][0]["unit_state"] == "unknown"
    assert provenance["recorded_literals"][0]["value"] == 780


def test_recorded_json_strings_supply_real_field_and_code_sources_without_rewriting_packet():
    from detector.code_lineage import build_code_lineage, code_ancestor_events
    value = packet([{ "tool_calls": [{"name": "python", "arguments": json.dumps({"code": "x = 780"})}],
                      "tool_responses": [json.dumps({"distance": 780})]},
                    {"tool_calls": [{"name": "python", "arguments": json.dumps({"code": "alias = x"})}], "tool_responses": ["copied"]}])
    original = deepcopy(value)
    trace = build_semantic_trace(value)
    row = next(r for r in trace["literal_facts"] if r["field"] == "distance")
    assert row["value"] == 780 and row["unit_state"] == "unknown" and row["source_format"] == "json_string"
    assert code_ancestor_events(build_code_lineage(value), "event_002") == ["event_001"]
    assert value == original


@pytest.mark.parametrize("text", ['{"x": NaN}', '{"x": 1e5000}', '{"x": 1, "x": 2}', '{broken', 'plain source text'])
def test_malformed_or_nonfinite_source_json_stays_unparsed_and_does_not_crash_trace(text):
    assert structured_payload(text) == text
    value = packet([{ "tool_calls": [{"name": "read", "arguments": text}], "tool_responses": [text]}])
    trace = build_semantic_trace(value)
    assert trace["literal_facts"][0]["source_format"] == "source_text"
    assert "value" not in trace["literal_facts"][0]


def test_json_string_declared_field_bindings_preserve_units_and_time():
    value = field_packet()
    for event in value["events"]:
        for item in event["tool_interactions"]:
            for key in ("arguments", "observation"):
                if isinstance(item.get(key), dict):
                    item[key] = json.dumps(item[key])
    trace = build_semantic_trace(value)
    assert any(e["relation"] == "declared_field_copy" for e in trace["edges"])


def test_symbol_domain_changes_and_named_consumption_preserve_unverified_premises():
    value = build_packet({"question": "Count the integer values of k for real solutions x.", "history": [
        {"tool_calls": [{"name": "python", "arguments": json.dumps({"code": "x, k = symbols('x k', real=True)"})}], "tool_responses": ["defined"]},
        {"tool_calls": [{"name": "python", "arguments": json.dumps({"code": "x, k = symbols('x k', real=True, positive=True)"})}], "tool_responses": ["defined"]},
        {"tool_calls": [{"name": "python", "arguments": json.dumps({"code": "values_of_k = list(range(-500, 501))"})}], "tool_responses": ["1001"]},
    ]})
    trace = build_semantic_trace(value)
    assert semantic_ancestors(trace, "event_003") == ["event_001", "event_002"]
    assert any(e["relation"] == "recorded_symbol_domain_change" for e in trace["edges"])
    assert all(e["verification_status"] == "hypothesis" for e in trace["edges"])
    value["events"][2]["tool_interactions"][0]["arguments"] = {"code": "k = 2\nvalues_of_k = [k]"}
    assert semantic_ancestors(build_semantic_trace(value), "event_003") == []


def test_note_wrappers_and_exact_document_identifiers_link_recorded_sources_without_truth_authority():
    value = packet([
        {"tool_calls": [{"name": "append_note", "arguments": json.dumps({"note_name": "n", "content": "Source https://example.org/a is a claim."})}], "tool_responses": ["created"]},
        {"tool_calls": [{"name": "read_note", "arguments": json.dumps({"note_name": "n"})}], "tool_responses": ["Source https://example.org/a is a claim."]},
        {"tool_calls": [{"name": "append_note", "arguments": json.dumps({"note_name": "analysis", "content": "Adopt https://example.org/a as a premise."})}], "tool_responses": ["created"]},
    ])
    trace = build_semantic_trace(value)
    assert semantic_ancestors(trace, "event_003") == ["event_001", "event_002"]
    assert trace["decision_authority"] == "none" and trace["claim_equivalence"] == "unestablished"
    value["events"][2]["tool_interactions"][0]["arguments"] = {"note_name": "analysis", "content": "Adopt https://example.org/a/other as a premise."}
    assert semantic_ancestors(build_semantic_trace(value), "event_003") == []


def test_inventory_and_lossless_reader_restore_middle_of_long_observation():
    observation = "a" * 30000 + "x > -2; middle evidence" + "z" * 30000
    value = packet([{ "tool_calls": [{"name": "read", "arguments": {}}], "tool_responses": [observation]}])
    base, meta = render_judge_view(value)
    assert "middle evidence" not in base
    text, expanded = expand_view(value, base, meta, positive={"step": 1, "evidence_refs": ["event_001_tool_01"]})
    assert "middle evidence" in text
    assert expanded["degraded"] is True
    assert expanded["evidence_expansion"]["expanded_channels_complete"]
    assert evidence_inventory(value)["inventory_complete"]
    read = read_evidence(value, ["event_001_tool_01"])
    assert read["events"][0]["tool_interactions"][0]["observation"] == observation
    read["events"][0]["tool_interactions"][0]["observation"] = "tampered"
    assert read_evidence(value, ["event_001_tool_01"])["events"][0]["tool_interactions"][0]["observation"] == observation
    with pytest.raises(ValueError):
        read_evidence(value, ["event_999"])


def test_expanded_structured_channels_use_the_quote_validators_source_representation():
    from detector.packet import packet_quote_error
    observed = {"note": "a" * 60000, "distance": 780, "unit": "km"}
    value = packet([{ "tool_calls": [{"name": "read", "arguments": {}}], "tool_responses": [observed]}])
    base, meta = render_judge_view(value)
    text, _ = expand_view(value, base, meta, positive={"step": 1, "evidence_refs": ["event_001_tool_01"]})
    quote = "'distance': 780, 'unit': 'km'"
    assert quote in text
    assert packet_quote_error(value, quote, locus="event_001", evidence_refs=["event_001_tool_01"]) is None


def test_optional_packet_metadata_and_non_numeric_event_ids_do_not_crash_expansion():
    value = {"events": [{"event_id": "origin", "position": 1, "content": "x > 0"},
                        {"event_id": "consumer", "position": 2, "tool_interactions": [
                            {"interaction_id": "consumer_tool_01", "arguments": {"code": "print(x)"}}]}]}
    trace = build_semantic_trace(value)
    assert trace["task_sources"] == []
    assert semantic_ancestors(trace, "consumer") == ["origin"]
    inventory = evidence_inventory(value)
    assert all(row["source_step_id"] is None for row in inventory["channels"])
    text, meta = expand_view(value, "base", {"degraded": True}, positive={"step": 2, "evidence_refs": ["consumer_tool_01"]}, include_ancestors=True)
    assert "print(x)" in text and meta["evidence_expansion"]["expanded_channels_complete"]


def test_preparation_reuse_is_owned_and_invalidated_by_actual_packet(monkeypatch):
    import detector.packet as module
    calls = []
    original = module.render_full_view
    monkeypatch.setattr(module, "render_full_view", lambda p: (calls.append(1), original(p))[1])
    value = packet([{ "content": "state"}])
    with preparation_scope():
        a = render_judge_view(value)
        a[1]["rendered_event_ids"].clear()
        b = render_judge_view(value)
        assert b[1]["rendered_event_ids"] == ["event_001"]
        value["events"][0]["content"] = "new state"
        assert "new state" in render_judge_view(value)[0]
    assert len(calls) == 2


def test_stage_retry_preserves_prior_measurement_and_counts_requests_once():
    cache = StageRetryCache()
    calls = []

    def stage():
        calls.append(1)
        record_request({"request_id": "r1", "usage": {"total_tokens": 7}})
        return {"ok": True, "parsed": {"value": 4}}, {"state": "ok"}

    with stage_retry_scope(cache), usage_scope() as requests:
        cache.bind({"source": "same", "packet": "p"})
        cache.begin_attempt(1)
        first = memo_stage("judge", {"votes": 3}, stage)
        first[0]["parsed"]["value"] = 99
        cache.begin_attempt(2)
        second = memo_stage("judge", {"votes": 3}, stage)
    assert len(calls) == len(requests) == 1 and second[0]["parsed"]["value"] == 4
    assert cache.reused[0]["origin_case_attempt"] == 1
    assert cache.reused[0]["request_ids"] == ["r1"]
    with pytest.raises(ValueError, match="identity changed"):
        cache.bind({"source": "changed", "packet": "p"})


def test_failed_stage_and_changed_hearing_are_not_reused():
    cache = StageRetryCache()
    calls = []
    def stage():
        calls.append(1)
        return {"ok": len(calls) > 1}, {"state": "ok" if len(calls) > 1 else "provider_error"}
    with stage_retry_scope(cache):
        cache.bind({"case": 1})
        cache.begin_attempt(1)
        memo_stage("defense", {"target": 1}, stage)
        cache.begin_attempt(2)
        memo_stage("defense", {"target": 1}, stage)
        memo_stage("defense", {"target": 2}, stage)
    assert len(calls) == 3


def test_cached_stage_cannot_bypass_expired_deadline_cancellation_or_tamper():
    from detector.runtime import CaseDeadlineExceeded, RunStopped
    cache = StageRetryCache()
    with stage_retry_scope(cache):
        cache.bind({"case": 1})
        cache.begin_attempt(1)
        memo_stage("judge", {}, lambda: ({"ok": True}, {"state": "ok"}))
        with deadline_scope(deadline=0), pytest.raises(CaseDeadlineExceeded):
            memo_stage("judge", {}, lambda: pytest.fail("expired stage cannot execute"))
        control = RunControl(1)
        control.stop({"error_type": "cancelled"})
        with run_scope(control), pytest.raises(RunStopped):
            memo_stage("judge", {}, lambda: pytest.fail("cancelled stage cannot execute"))
        next(iter(cache._entries.values()))["outcome"][0]["ok"] = False
        with pytest.raises(ValueError, match="stored retry stage changed"):
            memo_stage("judge", {}, lambda: pytest.fail("tampered stage cannot execute"))


@pytest.mark.parametrize("field", ["source", "config", "registry", "snapshot", "packet", "authority"])
def test_reuse_refuses_all_case_identity_drift(field):
    cache = StageRetryCache()
    identity = {name: "same" for name in ("source", "config", "registry", "snapshot", "packet", "authority")}
    cache.bind(identity)
    identity[field] = "changed"
    with pytest.raises(ValueError, match="identity changed"):
        cache.bind(identity)


@pytest.mark.parametrize("votes", [1, 3])
def test_actual_detector_retries_only_failed_defense_and_reapplies_prechecks(monkeypatch, sample_record, votes):
    from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE
    from detector import api

    counts = Counter()
    prechecks = []
    original_plan = api.compile_runtime_plan
    def make_plan(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        original = plan.evidence_for
        def evidence(hook, *args, **kwargs):
            prechecks.append(hook)
            return original(hook, *args, **kwargs)
        plan.evidence_for = evidence
        return plan
    monkeypatch.setattr(api, "compile_runtime_plan", make_plan)
    def transport(**kwargs):
        purpose = kwargs["purpose"]
        counts[purpose] += 1
        record_request({"request_id": purpose + str(counts[purpose]), "purpose": purpose, "usage": {"total_tokens": 5}})
        if purpose == "judge":
            parsed = deepcopy(JUDGE_TRUE)
        elif purpose == "step_tracer":
            parsed = {"step": 2, "quote": "5", "referent": "4", "reason": "wrong sum", "evidence_refs": ["event_002"]}
        elif purpose == "defense":
            if counts[purpose] <= votes:
                raise ProviderError("defense", "offline outage", attempts=1)
            parsed = {"verdict": "substantiated", "confirmed_step": 2, "reason": "wrong sum", "evidence_refs": ["event_002"]}
        else:
            raise AssertionError(purpose)
        return parsed, {"attempts": 1, "usage": {"total_tokens": 5}}
    monkeypatch.setattr("detector.provider._provider_call", transport)
    config = deepcopy(CONFIG)
    for route in ("judge_route", "defense_route"):
        config[route].update(votes=votes, vote_topup=0)
    detector = Detector(config, require_credentials=False)
    record = deepcopy(sample_record)
    record["history"][-1]["content"] = "The answer is 5."
    result = detect_with_transport_retry(detector, record, case_id="retry")
    assert result["state"] == "verified_error"
    assert counts == {"judge": votes, "step_tracer": 1, "defense": votes * 2}
    assert prechecks.count("defense.pre") == prechecks.count("judge.post") == 2
    assert result["execution"]["provider_attempts"] == votes * 3 + 1
    assert result["execution"]["usage"]["total_tokens"] == (votes * 3 + 1) * 5
    assert {r["stage"] for r in result["provenance"]["stage_retry_reuse"]} == {"judge", "step_tracer"}


def test_provider_context_limit_falls_back_once_without_stopping_other_cases(monkeypatch):
    from detector import provider
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderError("trace", "large", error_type="context_length_exceeded", attempts=1, retryable=False,
                                usage_total={"total_tokens": 3})
        return {"ok": True}, {"attempts": 1, "usage": {"total_tokens": 5}}
    monkeypatch.setattr(provider, "_provider_call", transport)
    control = RunControl(2)
    with run_scope(control), deadline_scope(5):
        _, meta = provider_call(route={}, system="s", user="expanded", fallback_user="base", purpose="trace")
    assert [c["user"] for c in calls] == ["expanded", "base"]
    assert meta["attempts"] == 2 and not control.stopped.is_set()
    assert meta["usage_total"]["total_tokens"] == 8
    assert meta["evidence_view_fallback"] == "provider_context_limit"


@pytest.mark.parametrize("error", [{"code": "context_length_exceeded"},
                                  {"code": None, "message": "This model's maximum context length is exceeded"}])
def test_real_provider_error_shape_classifies_context_as_case_failure(monkeypatch, error):
    from detector import provider
    monkeypatch.setenv("FAKE_CONTEXT_KEY", "offline")
    monkeypatch.setattr(provider, "_isolated_transport", lambda **kwargs: (400, json.dumps({"error": error}).encode()))
    control = RunControl(1)
    with run_scope(control), pytest.raises(ProviderError) as failure:
        provider_call(route={"endpoint": "http://127.0.0.1:1", "model": "fake", "api_key_env": "FAKE_CONTEXT_KEY"}, system="s", user="u", purpose="case")
    assert failure.value.error_type == "context_length_exceeded" and not control.stopped.is_set()


def test_expansion_fallback_preserves_evidence_and_repair_inputs(monkeypatch):
    from detector import provider, step_tracer
    from detector.tests.test_judge_defense import CONFIG
    value = packet([{ "tool_calls": [{"name": "read", "arguments": {}}], "tool_responses": ["a" * 60000]}, {"content": "5"}])
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        if "LOSSLESS REQUESTED" in kwargs["user"]:
            raise ProviderError("trace", "large", error_type="context_length_exceeded", attempts=1, retryable=False)
        return {"step": 2, "quote": "5", "referent": "4", "reason": "wrong", "evidence_refs": ["event_002"]}, {"attempts": 1}
    monkeypatch.setattr(provider, "_provider_call", transport)
    result, status = step_tracer.run_step_tracer(packet=value, delivered_result="5", correct_result="4",
                positive={"step": 2, "quote": "5", "evidence_refs": ["event_002"]}, config=CONFIG)
    assert result["ok"] and status["state"] == "ok"
    assert result["view_meta"]["evidence_expansion"]["actual_view"] == "base_projection"
    assert len(calls) == 2
    claims = json.JSONDecoder().raw_decode(calls[1]["user"].split("MODEL RESULT CLAIMS (unverified):\n", 1)[1])[0]
    assert "HEARING TARGET" in calls[1]["user"] and claims["correct_result"] == "4"
    assert "REFERENT SOURCE INDEX" not in calls[1]["user"]


def test_context_fallback_reserves_each_context_copy_under_its_actual_budget_owner(monkeypatch, wrong_answer_packet):
    from detector import provider, step_tracer
    from detector.config import validate_config
    from detector.hearing import judge_target
    from detector.risk_context import build_risk_context
    from detector.runtime import AddedBudget, added_budget_scope
    from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE
    config = validate_config({**CONFIG, "causal_audit": {"mode": "tracer_context", "max_added_prompt_tokens_per_case": 24000}})
    positive = judge_target(JUDGE_TRUE)
    context = build_risk_context(wrong_answer_packet, positive=positive, policy=config["causal_audit"])
    assert context is not None
    reservations = []
    original = AddedBudget.reserve_context
    monkeypatch.setattr(AddedBudget, "reserve_context", lambda self, block: (reservations.append(block), original(self, block))[1])
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderError("trace", "large", error_type="context_length_exceeded", attempts=1, retryable=False)
        return {"step": 2, "quote": "5", "referent": "4", "reason": "wrong", "evidence_refs": ["event_002"]}, {"attempts": 1}
    monkeypatch.setattr(provider, "_provider_call", transport)
    with added_budget_scope(config["causal_audit"]):
        result, status = step_tracer.run_step_tracer(packet=wrong_answer_packet, delivered_result="5", correct_result="4",
                                                   config=config, positive=positive, causal_context=context)
    assert result["ok"] and status["state"] == "ok"
    assert len(reservations) == 2 and reservations[0] == reservations[1]
    assert result["causal_context_digest"] == context["digest"]
    assert "RISK CONTEXT" in calls[1]["user"] and "REFERENT SOURCE INDEX" not in calls[1]["user"]


def test_defense_refutation_starts_scanner_before_tracer_completes(monkeypatch):
    from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE
    scanner_started = threading.Event()
    calls = Counter()
    def transport(**kwargs):
        purpose = kwargs["purpose"]
        calls[purpose] += 1
        if purpose == "judge":
            parsed = deepcopy(JUDGE_TRUE)
        elif purpose == "step_tracer":
            assert scanner_started.wait(2), "Scanner must overlap this independent pending Tracer"
            parsed = {"step": 2, "quote": "5", "referent": "4", "reason": "allegation", "evidence_refs": ["event_002"]}
        elif purpose == "defense":
            parsed = {"verdict": "refuted", "confirmed_step": None, "reason": "valid observed result", "evidence_refs": ["event_002"]}
        elif purpose == "recall_scanner":
            scanner_started.set()
            parsed = {"candidates": []}
        else:
            raise AssertionError(purpose)
        return parsed, {"attempts": 1}
    monkeypatch.setattr("detector.provider._provider_call", transport)
    config = deepcopy(CONFIG)
    config["judge_route"].update(votes=1, vote_topup=0)
    config["defense_route"].update(votes=1, vote_topup=0)
    result = Detector(config, require_credentials=False).detect({"question": "What is 2+2?", "history": [{"content": "Compute 2+2"}, {"content": "5"}]})
    assert calls == {"judge": 1, "step_tracer": 1, "defense": 1, "recall_scanner": 1}
    assert result["proof"]["hearings"][0]["tracer_result"]["ok"] is True
    assert result["state"] == "verified_clean"


def test_early_scanner_failure_and_pending_tracer_both_remain_in_proof(monkeypatch):
    from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE
    scanner_started = threading.Event()
    def transport(**kwargs):
        purpose = kwargs["purpose"]
        if purpose == "judge":
            return deepcopy(JUDGE_TRUE), {"attempts": 1}
        if purpose == "step_tracer":
            assert scanner_started.wait(2)
            return {"step": 2, "quote": "5", "referent": "4", "reason": "allegation", "evidence_refs": ["event_002"]}, {"attempts": 1}
        if purpose == "defense":
            return {"verdict": "refuted", "reason": "observed", "confirmed_step": None, "evidence_refs": ["event_002"]}, {"attempts": 1}
        if purpose == "recall_scanner":
            scanner_started.set()
            raise ProviderError("recall", "offline outage", attempts=1)
        raise AssertionError(purpose)
    monkeypatch.setattr("detector.provider._provider_call", transport)
    config = deepcopy(CONFIG)
    for route in ("judge_route", "defense_route"):
        config[route].update(votes=1, vote_topup=0)
    result = Detector(config, require_credentials=False).detect({"question": "What is 2+2?", "history": [{"content": "Compute"}, {"content": "5"}]})
    assert result["proof"]["hearings"][0]["tracer_result"]["ok"]
    assert result["stage_status"]["recall_scanner"]["state"] == "provider_error"
    assert result["decision_authority"] != "authoritative"
