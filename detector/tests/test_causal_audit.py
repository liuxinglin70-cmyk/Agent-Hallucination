from copy import deepcopy

import pytest

from detector.api import Detector
from detector.causal_audit import run_causal_audit
from detector.causal_graph import build_observed_dependencies, project_dependency_closure
from detector.config import validate_causal_config
from detector.measurement import fatal_failure
from detector.packet import build_packet
from detector.provider import ProviderError
from detector.runner import detect_with_transport_retry
from detector.runtime import (
    AddedBudget, AddedBudgetExceeded, RunControl, RunStopped, added_budget_scope,
    current_added_budget, deadline_scope, record_request, run_scope, usage_scope,
)
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates
from detector.tests.test_api import CONFIG


def policy(**changes):
    return validate_causal_config({"mode": "shadow", **changes}, base_dir=".")


def record(observed="5"):
    return {"question": "Calculate 2+2", "history": [
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": [observed]}, {"role": "assistant", "content": "The answer is five"}]}


def inputs(cfg=None):
    cfg = cfg or policy()
    p = build_packet(record())
    profile = build_step_risk_profile(p, policy=cfg)
    candidates = select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    projection = project_dependency_closure(p, candidates=candidates, graph=graph, policy=cfg)
    return {"packet": p, "profile": profile, "projection": projection,
            "config": {**CONFIG, "causal_audit": cfg}}


def fake_hypotheses(**kwargs):
    record_request({"purpose": "causal_audit", "usage": {"total_tokens": 7}})
    return {"candidates": [{"event_id": "event_002", "role": "introduction",
                            "claim_status": "supported", "evidence_refs": ["event_002"],
                            "reason": "The model believes this is the first error"}]}, {"attempts": 1}


def install_base_judge(monkeypatch):
    def fake_judge(**kwargs):
        record_request({"purpose": "judge", "usage": {"total_tokens": 3}})
        return {"ok": True, "parsed": {"is_hallucination": False, "confidence": "high",
                "contracts": [], "evidence_refs": []}, "provider": {"vote": {
                    "cast": 3, "positives": 0, "tie": False}}}, {
            "stage": "judge", "state": "ok", "attempts": 1, "view": "complete", "warnings": []}
    monkeypatch.setattr("detector.api.run_judge", fake_judge)


def detector_config(**changes):
    return {**CONFIG, "judge_route": {**CONFIG["judge_route"], "votes": 3},
            "causal_audit": {"mode": "shadow", **changes}}


def test_deterministic_check_proves_only_local_result_without_provider(monkeypatch):
    data = inputs()
    def unexpected(**kwargs):
        raise AssertionError("deterministic audit must not call a provider")
    monkeypatch.setattr("detector.provider._provider_call", unexpected)
    audit, health = run_causal_audit(**data, budget=AddedBudget(data["config"]["causal_audit"]))
    assert health["state"] == "ok"
    checked = audit["candidates"][0]
    assert checked["claim_status"] == "supported"
    assert checked["scope"] == "local_arithmetic_result"
    assert checked["materiality"] == "unknown"
    assert checked["role"] == "unknown"
    assert checked["counterfactual"]["status"] == "verified_local"
    assert not checked["counterfactual"]["state_changes_verified"]
    assert audit["candidates"][1]["claim_status"] == "inconclusive"
    assert audit["cost"]["provider_attempts"] == 0


def test_model_opinion_and_valid_refs_do_not_upgrade_causal_truth(monkeypatch):
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000)
    data = inputs(cfg)
    seen = []
    def provider(**kwargs):
        seen.append(kwargs)
        return fake_hypotheses(**kwargs)
    monkeypatch.setattr("detector.provider._provider_call", provider)
    with usage_scope() as requests:
        audit, _ = run_causal_audit(**data, budget=AddedBudget(cfg))
    assert len(requests) == audit["cost"]["provider_attempts"] == 1
    assert audit["cost"]["usage"]["total_tokens"] == 7
    assert audit["hypotheses"][0]["verification_status"] == "hypothesis"
    assert audit["candidates"][1]["claim_status"] == "inconclusive"
    assert audit["candidates"][1]["role"] == "unknown"
    assert seen[0]["route"]["transport_retries"] == 0
    assert seen[0]["route"]["max_tokens"] == cfg["max_added_output_tokens_per_case"]


@pytest.mark.parametrize("bad", [
    None, {"candidates": "wrong"}, {"candidates": [{"event_id": "event_002"}]},
    {"candidates": [{"event_id": "event_002", "role": [], "claim_status": "supported",
                     "evidence_refs": ["event_002"], "reason": "x"}]},
    {"candidates": [{"event_id": "event_002", "role": "introduction", "claim_status": "supported",
                     "evidence_refs": None, "reason": "x"}]},
    {"candidates": [{"event_id": "event_002", "role": "introduction", "claim_status": "supported",
                     "evidence_refs": ["E1"], "reason": "x"}]},
])
def test_model_schema_errors_are_diagnostic_health_only(monkeypatch, bad):
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000)
    monkeypatch.setattr("detector.provider._provider_call", lambda **kwargs: (bad, {}))
    audit, health = run_causal_audit(**inputs(cfg), budget=AddedBudget(cfg))
    assert health["state"] == "contract_error"
    assert audit["hypotheses"] == []
    assert audit["candidates"][0]["claim_status"] == "supported"


@pytest.mark.parametrize("limits, resource", [
    ({"max_added_provider_requests_per_case": 0}, "requests"),
    ({"max_added_prompt_tokens_per_case": 1500}, "input"),
    ({"max_added_output_tokens_per_case": 0}, "output"),
])
def test_budget_refuses_request_before_provider(monkeypatch, limits, resource):
    cfg = policy(counterfactual_mode="model_hypothesis", **limits)
    def unexpected(**kwargs):
        raise AssertionError("budget must refuse before provider")
    monkeypatch.setattr("detector.provider._provider_call", unexpected)
    audit, health = run_causal_audit(**inputs(cfg), budget=AddedBudget(cfg))
    assert health == {"state": "budget_exhausted", "warnings": [resource]}
    assert audit["cost"]["provider_attempts"] == 0


def test_nested_budget_and_hearings_share_admissions():
    cfg = policy(max_added_provider_requests_per_case=2, max_added_output_tokens_per_case=10)
    with added_budget_scope(cfg) as budget:
        assert budget.reserve(system="a", user="b", output_tokens=6) == 6
        with added_budget_scope(deepcopy(cfg)) as nested:
            assert nested is budget
            assert nested.reserve(system="a", user="b", output_tokens=6) == 4
        with pytest.raises(AddedBudgetExceeded, match="requests"):
            budget.reserve(system="a", user="b", output_tokens=1)
    assert current_added_budget() is None


def test_nested_policy_change_cannot_disable_or_reset_case_budget():
    cfg = policy()
    with added_budget_scope(cfg):
        for changed in (None, {**cfg, "mode": "off"}, {**cfg, "max_added_provider_requests_per_case": 2}):
            with pytest.raises(ValueError, match="policy changed"):
                with added_budget_scope(changed):
                    raise AssertionError("changed policy cannot enter the existing case")


def test_utf8_input_budget_accumulates_across_calls():
    cfg = policy(max_added_provider_requests_per_case=3, max_added_prompt_tokens_per_case=6,
                 max_added_output_tokens_per_case=3)
    budget = AddedBudget(cfg)
    budget.reserve(system="", user="中", output_tokens=1)
    budget.reserve(system="", user="文", output_tokens=1)
    with pytest.raises(AddedBudgetExceeded, match="input"):
        budget.reserve(system="", user="a", output_tokens=1)
    assert budget.snapshot()["reserved_input_bytes"] == 6


def test_provider_declared_tool_inputs_also_consume_added_input_budget(monkeypatch):
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000)
    data = inputs(cfg)
    data["config"]["judge_route"] = {**CONFIG["judge_route"], "extra_body": {"tools": [{"description": "x" * 20000}]}}
    def unexpected(**kwargs):
        raise AssertionError("declared tool descriptions must consume input budget")
    monkeypatch.setattr("detector.provider._provider_call", unexpected)
    audit, health = run_causal_audit(**data, budget=AddedBudget(cfg))
    assert health == {"state": "budget_exhausted", "warnings": ["input"]}
    assert audit["cost"]["provider_attempts"] == 0


def test_added_deadline_is_not_reset_or_longer_than_parent(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("detector.runtime.time.monotonic", lambda: now[0])
    budget = AddedBudget(policy(max_added_seconds_per_case=2))
    with deadline_scope(deadline=1):
        budget.begin()
        assert budget.deadline == 1
        now[0] = 1.1
        with pytest.raises(AddedBudgetExceeded, match="time"):
            budget.begin()
    now[0] = 2.1
    with pytest.raises(AddedBudgetExceeded, match="time"):
        budget.check()


def test_stopped_control_propagates_before_audit_or_reservation():
    control = RunControl()
    control.stop({"error_type": "cancelled"})
    with run_scope(control), pytest.raises(RunStopped):
        AddedBudget(policy()).reserve(system="a", user="b", output_tokens=1)


def test_budget_is_shared_across_official_whole_case_retry(monkeypatch):
    from detector.tests.test_runner import _envelope
    cfg = detector_config(max_added_output_tokens_per_case=10)
    detector = Detector(cfg, require_credentials=False)
    budgets = []
    def detect(*args, case_id, **kwargs):
        budget = current_added_budget()
        budgets.append(budget)
        if len(budgets) == 1:
            budget.reserve(system="a", user="b", output_tokens=5)
            return _envelope(case_id, state="unresolved", authority="invalid")
        with pytest.raises(AddedBudgetExceeded, match="requests"):
            budget.reserve(system="a", user="b", output_tokens=5)
        return _envelope(case_id)
    monkeypatch.setattr(detector, "_detect", detect)
    result = detect_with_transport_retry(detector, {}, case_id="test")
    assert len(result["execution"]["case_attempts"]) == 2
    assert budgets[0] is budgets[1]
    assert budgets[0].snapshot()["reserved_requests"] == 1


def test_shadow_provider_failure_preserves_base_result(monkeypatch):
    install_base_judge(monkeypatch)
    def unavailable(**kwargs):
        record_request({"purpose": "causal_audit", "usage": None})
        raise ProviderError("causal_audit", "offline failure")
    monkeypatch.setattr("detector.provider._provider_call", unavailable)
    result = Detector(detector_config(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000),
                      require_credentials=False).detect(record())
    assert result["state"] == "verified_clean"
    assert result["runtime_valid"]
    assert "causal_audit" not in result["stage_status"]
    assert result["proof"]["causal_diagnostics"]["health"]["state"] == "provider_error"
    assert result["execution"]["provider_attempts"] == 2
    assert result["execution"]["usage_missing_attempts"] == 1


def test_shadow_fatal_retains_base_proof_and_existing_stop_classification(monkeypatch):
    install_base_judge(monkeypatch)
    fatal = ProviderError("causal_audit", "bad key", error_type="authentication_error", retryable=False)
    def refused(**kwargs):
        record_request({"purpose": "causal_audit", "usage": None})
        raise fatal
    monkeypatch.setattr("detector.provider._provider_call", refused)
    result = Detector(detector_config(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000),
                      require_credentials=False).detect(record())
    assert result["state"] == "unresolved"
    assert not result["runtime_valid"]
    assert fatal_failure(result)["error_type"] == "authentication_error"
    assert result["base_detection_result"]["proof"]["state"] == "verified_clean"
    assert result["audit_health"]["state"] == "execution_stopped"
    assert result["execution"]["provider_attempts"] == 2


def test_shadow_cancel_propagates_same_exception_and_journals_base_proof(monkeypatch):
    install_base_judge(monkeypatch)
    cancel = KeyboardInterrupt("cancelled")
    def interrupted(**kwargs):
        record_request({"purpose": "causal_audit", "usage": None})
        raise cancel
    monkeypatch.setattr("detector.provider._provider_call", interrupted)
    detector = Detector(detector_config(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000),
                        require_credentials=False)
    attempts = []
    with pytest.raises(KeyboardInterrupt) as failure:
        detect_with_transport_retry(detector, record(), case_id="test", attempt_observer=attempts.append)
    assert failure.value is cancel
    assert len(attempts) == 1
    assert attempts[0]["result"]["proof"]["state"] == "verified_clean"
    assert attempts[0]["error"]["type"] == "KeyboardInterrupt"
    assert attempts[0]["execution"]["provider_attempts"] == 2


def test_real_provider_wrapper_preserves_run_stop_and_attempt_error(monkeypatch):
    install_base_judge(monkeypatch)
    stop = RunStopped({"error_type": "run_cancelled", "message": "cancelled"})
    def transport(**kwargs):
        record_request({"purpose": "causal_audit", "usage": None})
        raise stop
    monkeypatch.setattr("detector.provider._provider_call", transport)
    detector = Detector(detector_config(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000),
                        require_credentials=False)
    attempts = []
    with pytest.raises(RunStopped) as failure:
        detect_with_transport_retry(detector, record(), case_id="test", attempt_observer=attempts.append)
    assert failure.value is stop
    assert attempts[0]["error"]["type"] == "RunStopped"
    assert attempts[0]["result"]["proof"]["state"] == "verified_clean"
    assert attempts[0]["execution"]["provider_attempts"] == 2


@pytest.mark.parametrize("votes", [1, 3])
def test_base_judge_cancellation_also_bypasses_provider_failure_handlers(monkeypatch, votes):
    stop = RunStopped({"error_type": "run_cancelled"})
    def transport(**kwargs):
        raise stop
    monkeypatch.setattr("detector.provider._provider_call", transport)
    cfg = detector_config()
    cfg["judge_route"]["votes"] = votes
    with pytest.raises(RunStopped) as failure:
        Detector(cfg, require_credentials=False).detect(record())
    assert failure.value is stop


def test_zero_added_time_skips_diagnostics_but_keeps_base_proof(monkeypatch):
    install_base_judge(monkeypatch)
    result = Detector(detector_config(max_added_seconds_per_case=0), require_credentials=False).detect(record())
    assert result["state"] == "verified_clean"
    assert result["proof"]["causal_diagnostics"]["health"]["state"] == "budget_exhausted"
    assert result["execution"]["provider_attempts"] == 1


def test_multiple_audits_share_one_case_budget(monkeypatch):
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000)
    monkeypatch.setattr("detector.provider._provider_call", fake_hypotheses)
    budget = AddedBudget(cfg)
    with usage_scope() as requests:
        first, _ = run_causal_audit(**inputs(cfg), budget=budget)
        second, health = run_causal_audit(**inputs(cfg), budget=budget)
    assert len(requests) == 1
    assert first["cost"]["usage"]["total_tokens"] == 7
    assert second["cost"]["provider_attempts"] == 0
    assert health == {"state": "budget_exhausted", "warnings": ["requests"]}


def test_deadline_after_provider_is_diagnostic_failure_with_actual_cost(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("detector.runtime.time.monotonic", lambda: now[0])
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000,
                 max_added_seconds_per_case=1)
    def slow(**kwargs):
        now[0] = 2
        return fake_hypotheses(**kwargs)
    monkeypatch.setattr("detector.provider._provider_call", slow)
    audit, health = run_causal_audit(**inputs(cfg), budget=AddedBudget(cfg))
    assert health["state"] == "budget_exhausted"
    assert audit["hypotheses"] == []
    assert audit["cost"]["usage"]["total_tokens"] == 7


def test_task_missing_action_audit_keeps_null_locus_without_authority():
    cfg = policy()
    data = inputs(cfg)
    p = build_packet({"question": "You must call the search tool", "history": [{"role": "assistant", "content": "Done"}]})
    profile = build_step_risk_profile(p, policy=cfg)
    candidates = select_causal_candidates(profile, mandatory_event_ids=[], policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    data.update(packet=p, profile=profile, projection=project_dependency_closure(p, candidates=candidates, graph=graph, policy=cfg))
    audit, _ = run_causal_audit(**data, budget=AddedBudget(cfg))
    assert audit["task_checks"][0]["locus_event_id"] is None
    assert audit["task_checks"][0]["decision_authority"] == "none"
    assert all(candidate["claim_status"] != "supported" for candidate in audit["candidates"])


def test_partial_consistent_calculator_checks_do_not_refute_unknown_expression():
    cfg = policy()
    data = inputs(cfg)
    p = build_packet({"question": "calculate", "history": [{"role": "assistant", "tool_calls": [
        {"name": "calculator", "arguments": {"expr": "2+2"}},
        {"name": "calculator", "arguments": {"expr": "unknown(x)"}},
    ], "tool_responses": ["4", "5"]}]})
    profile = build_step_risk_profile(p, policy=cfg)
    candidates = select_causal_candidates(profile, mandatory_event_ids=["event_001"], policy=cfg)
    graph = build_observed_dependencies(p, policy=cfg)
    data.update(packet=p, profile=profile, projection=project_dependency_closure(p, candidates=candidates, graph=graph, policy=cfg))
    audit, _ = run_causal_audit(**data, budget=AddedBudget(cfg))
    assert audit["candidates"][0]["claim_status"] == "inconclusive"


def test_applied_snapshot_reaches_model_and_identity(monkeypatch):
    import json
    from detector.tests.test_evidence import snapshot
    cfg = policy(counterfactual_mode="model_hypothesis", max_added_prompt_tokens_per_case=20000)
    data = inputs(cfg)
    snap = snapshot()
    data.update(snapshot=snap, profile=build_step_risk_profile(data["packet"], policy=cfg, snapshot=snap))
    seen = []
    def read(**kwargs):
        seen.append(json.loads(kwargs["user"]))
        return fake_hypotheses(**kwargs)
    monkeypatch.setattr("detector.provider._provider_call", read)
    audit, _ = run_causal_audit(**data, budget=AddedBudget(cfg))
    assert seen[0]["snapshot"] == snap
    assert audit["snapshot_digest"] == data["profile"]["snapshot_digest"]


def test_snapshot_or_context_change_is_refused_before_call(monkeypatch):
    data = inputs()
    with pytest.raises(ValueError, match="snapshot identity"):
        run_causal_audit(**data, snapshot={"new": "facts"}, budget=AddedBudget(data["config"]["causal_audit"]))
    with pytest.raises(ValueError, match="context identity"):
        run_causal_audit(**data, context={"contracts": []}, budget=AddedBudget(data["config"]["causal_audit"]))
