"""预测的前缀隔离、预算、引用与正式检测接线。"""

from copy import deepcopy
import json

import pytest

from detector.action_forecast import action_comparisons, build_action_forecast, comparison_prompt, prefix_view
from detector.api import Detector
from detector.config import validate_config
from detector.judge import run_judge
from detector.packet import build_packet
from detector.provider import ProviderError
from detector.replay import build_replay_inputs
from detector.runtime import added_budget_scope
from detector.stage_retry import StageRetryCache, stage_retry_scope


def record():
    return {"question": "Compute 2+2 and report the result.", "history": [
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": ["5"]},
        {"role": "assistant", "content": "The answer is 5."},
    ]}


def config(mode="judge_context", **changes):
    return validate_config({"causal_audit": {"next_action_mode": mode,
                            "max_added_prompt_tokens_per_case": 60000,
                            "max_added_output_tokens_per_case": 4096,
                            "max_added_seconds_per_case": 180, **changes},
                            "judge_route": {"votes": 3}}, require_credentials=False)


def constraints():
    return {"constraints": [{"requirement": "Recalculate the incorrect arithmetic result before reporting it.",
                              "allowed_actions": ["Recalculate or explain the inconsistency"],
                              "forbidden_actions": ["Present 5 as the verified answer to 2+2"],
                              "evidence_ref": "event_001_tool_01", "quote": "5"}]}


def install_transport(monkeypatch, *, bad_prediction=None):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        purpose = kwargs["purpose"]
        if purpose == "action_forecast":
            if isinstance(bad_prediction, BaseException):
                raise bad_prediction
            parsed = constraints() if bad_prediction is None else bad_prediction
        elif purpose == "judge":
            parsed = {"is_hallucination": False, "hallucination_step": None,
                      "hallucination_reason": None, "confidence": "high", "evidence_refs": [],
                      "contracts": [{"kind": "exact_value", "requirement": "Report 2+2"}],
                      "root_cause": None,
                      "next_action_comparisons": [{"source_event_id": "event_001", "next_event_id": "event_002",
                                                   "state": "divergent", "reason": "Incorrect arithmetic was adopted.",
                                                   "evidence_refs": ["event_002"], "quote": "5"}]}
            if "next_action_comparisons" not in kwargs["system"]:
                parsed.pop("next_action_comparisons")
        else:
            raise AssertionError(purpose)
        return deepcopy(parsed), {"attempts": 1, "usage": {"total_tokens": 10}}

    monkeypatch.setattr("detector.provider._provider_call", transport)
    return calls


def test_prefix_prediction_is_invariant_to_future_and_does_not_send_it(monkeypatch):
    calls = install_transport(monkeypatch)
    packet = build_packet(record())
    changed = deepcopy(packet)
    changed["events"][1]["content"] = "FUTURE_SECRET"
    first = build_action_forecast(packet, config=config())
    second = build_action_forecast(changed, config=config())
    assert first["predictions"] == second["predictions"]
    assert all("FUTURE_SECRET" not in call["user"] and "event_002" not in call["user"] for call in calls)
    assert len(calls) == 2
    assert first["decision_authority"] == first["localization_authority"] == "none"


@pytest.mark.parametrize("bad", [
    {"constraints": [{**constraints()["constraints"][0], "evidence_ref": "event_002", "quote": "5"}]},
    {"constraints": [{**constraints()["constraints"][0], "quote": "fabricated"}]},
    {"constraints": [True]}, {"constraints": "invalid"},
])
def test_forecast_rejects_future_or_fabricated_references(monkeypatch, bad):
    install_transport(monkeypatch, bad_prediction=bad)
    forecast = build_action_forecast(build_packet(record()), config=config())
    assert forecast["health"] == "contract_error"
    assert forecast["predictions"] == []
    assert "rejected_prediction" in forecast
    assert comparison_prompt(forecast) == ""


def test_successful_forecast_is_reused_within_case_retry(monkeypatch):
    calls = install_transport(monkeypatch)
    cfg, packet = config(), build_packet(record())
    cache = StageRetryCache()
    cache.bind({"packet": packet, "config": cfg})
    with added_budget_scope(cfg["causal_audit"]) as budget, stage_retry_scope(cache):
        cache.begin_attempt(1)
        first = build_action_forecast(packet, config=cfg)
        cache.begin_attempt(2)
        second = build_action_forecast(packet, config=cfg)
        assert budget.requests == 1
    assert first == second
    assert len(calls) == 1
    assert cache.reused[0]["stage"] == "action_forecast"


@pytest.mark.parametrize("limits,resource", [
    ({"max_added_provider_requests_per_case": 0}, "requests"),
    ({"max_added_output_tokens_per_case": 0}, "output"),
    ({"max_added_prompt_tokens_per_case": 5}, "input"),
    ({"max_added_seconds_per_case": 0}, "time"),
])
def test_budget_exhaustion_does_not_start_provider(monkeypatch, limits, resource):
    calls = install_transport(monkeypatch)
    forecast = build_action_forecast(build_packet(record()), config=config(**limits))
    assert forecast["health"] == "budget_exhausted"
    assert forecast["warnings"] == [resource]
    assert not calls


def test_optional_truncation_falls_back_but_authentication_still_propagates(monkeypatch):
    install_transport(monkeypatch, bad_prediction=ProviderError("action_forecast", "truncated", retryable=False,
                                                              error_type="invalid_json"))
    assert build_action_forecast(build_packet(record()), config=config())["health"] == "provider_error"
    install_transport(monkeypatch, bad_prediction=ProviderError("action_forecast", "rejected",
                                                              error_type="authentication_error"))
    with pytest.raises(ProviderError):
        build_action_forecast(build_packet(record()), config=config())


@pytest.mark.parametrize("mode,requests,used", [("off", 0, False), ("shadow", 1, False), ("judge_context", 1, True)])
def test_detector_retains_forecast_and_independent_comparison(monkeypatch, mode, requests, used):
    calls = install_transport(monkeypatch)
    result = Detector(config(mode), require_credentials=False).detect(record())
    assert sum(call["purpose"] == "action_forecast" for call in calls) == requests
    judges = [call for call in calls if call["purpose"] == "judge"]
    assert len(judges) == 3
    assert all(("next_action_comparisons 数组" in call["user"]) is used for call in judges)
    assert result["state"] == "verified_clean"
    assert result["decision_authority"] == "authoritative"
    if mode != "off":
        assert result["proof"]["action_forecast_used"] is used
    if used:
        assert result["proof"]["judge"]["next_action_comparisons"][0]["state"] == "divergent"


def test_judge_replay_identity_binds_forecast_policy():
    packet = build_packet(record())
    on = build_replay_inputs(packet=packet, config=config(), source="same")
    off = build_replay_inputs(packet=packet, config=config("off"), source="same")
    assert on["stage_digests"]["judge"] != off["stage_digests"]["judge"]


def test_comparison_requires_next_action_quote_not_only_observation(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    actual = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "divergent",
              "reason": "Adopted result", "evidence_refs": ["event_002"], "quote": "5"}
    assert action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]["state"] == "divergent"
    actual["evidence_refs"] = ["event_001_tool_01"]
    assert action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]["state"] == "unknown"
    actual.update(evidence_refs=["event_002"], quote="fabricated")
    assert action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]["state"] == "unknown"


def test_no_next_step_or_low_risk_prose_has_no_prediction_request(monkeypatch):
    calls = install_transport(monkeypatch)
    single = record()
    single["history"].pop()
    assert build_action_forecast(build_packet(single), config=config())["predictions"] == []
    prose = {"question": "Summarize", "history": [{"content": "Thinking"}, {"content": "Done"}]}
    assert build_action_forecast(build_packet(prose), config=config())["predictions"] == []
    assert not calls


def test_prefix_excerpts_preserve_identity_without_mutating_packet():
    packet = build_packet(record())
    original = deepcopy(packet)
    view = prefix_view(packet, 1)
    assert len(view["events"]) == 1
    assert view["channels_may_be_excerpted"]
    assert packet == original
    assert "event_002" not in json.dumps(view)


def test_malformed_comparison_cannot_abort_valid_judgment(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    actual = {"source_event_id": "event_001", "next_event_id": "event_002", "state": [],
              "reason": "Invalid sidecar", "evidence_refs": ["event_002"], "quote": "5"}
    assert action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]["state"] == "unknown"


def test_explicit_failure_reuses_zero_request_boundary(monkeypatch):
    calls = install_transport(monkeypatch)
    value = record()
    value["history"][0]["tool_responses"] = [{"error": "invalid arguments"}]
    forecast = build_action_forecast(build_packet(value), config=config())
    assert [row["producer"] for row in forecast["predictions"]] == ["deterministic"]
    assert not calls


def test_comparison_context_budget_is_reserved_before_ballots(monkeypatch):
    calls = install_transport(monkeypatch)
    cfg = config(max_added_prompt_tokens_per_case=4000)
    result = Detector(cfg, require_credentials=False).detect(record())
    assert any(call["purpose"] == "action_forecast" for call in calls)
    assert result["proof"]["action_forecast_used"] is False
    assert result["state"] == "verified_clean"


def test_cached_judge_does_not_reserve_comparison_context_twice(monkeypatch):
    calls = install_transport(monkeypatch)
    cfg, packet = config(), build_packet(record())
    cache = StageRetryCache()
    cache.bind({"config": cfg, "packet": packet})
    with added_budget_scope(cfg["causal_audit"]) as budget, stage_retry_scope(cache):
        cache.begin_attempt(1)
        first = run_judge(packet=packet, config=cfg)
        initial = budget.input_bytes
        cache.begin_attempt(2)
        second = run_judge(packet=packet, config=cfg)
        assert budget.input_bytes == initial
        assert budget.requests == 1
    assert first == second
    assert len(calls) == 4


def test_comparison_cannot_use_future_references(monkeypatch):
    install_transport(monkeypatch)
    value = record()
    value["history"].append({"role": "assistant", "content": "Later evidence"})
    packet = build_packet(value)
    forecast = build_action_forecast(packet, config=config())
    actual = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "divergent",
              "reason": "Uses later evidence", "evidence_refs": ["event_002", "event_003"], "quote": "5"}
    assert action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]["state"] == "unknown"


@pytest.mark.parametrize("observation", ['The result is "ABCD".', "First line\nSecond line", r"C:\Users\example"])
def test_literal_quote_is_checked_in_channels_before_json_escaping(monkeypatch, observation):
    value = record()
    value["history"][0]["tool_responses"] = [observation]
    parsed = constraints()
    parsed["constraints"][0]["quote"] = observation
    install_transport(monkeypatch, bad_prediction=parsed)
    forecast = build_action_forecast(build_packet(value), config=config())
    assert forecast["health"] == "ok"
    assert forecast["predictions"][0]["constraints"][0]["quote"] == observation


def test_structured_channels_use_the_same_quote_representation_as_packet_verifier(monkeypatch):
    value = record()
    arguments = {"symbol": "ABCD", "confirmed": True}
    value["history"][0]["tool_calls"][0]["arguments"] = arguments
    parsed = constraints()
    parsed["constraints"][0]["quote"] = str(arguments)
    install_transport(monkeypatch, bad_prediction=parsed)
    forecast = build_action_forecast(build_packet(value), config=config())
    assert forecast["health"] == "ok"
    assert forecast["predictions"][0]["constraints"][0]["quote"] == str(arguments)
