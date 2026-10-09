import pytest

from detector.api import Detector
from detector.tests.test_causal_audit import record
from detector.tests.test_causal_context import config, tracer_reply


def feedback_config(**changes):
    return config(negative_feedback_enabled=True, negative_feedback_policy={"id": "review-v1"},
                  max_added_provider_requests_per_case=6, max_added_prompt_tokens_per_case=100000,
                  max_added_output_tokens_per_case=20000, **changes)


def install_base_judge(monkeypatch):
    from detector.runtime import record_request
    def judge(**kwargs):
        record_request({"purpose": "judge", "usage": {"total_tokens": 3}})
        return {"ok": True, "parsed": {"is_hallucination": False, "confidence": "high",
                "contracts": [{"kind": "exact_value", "requirement": "calculate the expression"}],
                "evidence_refs": []}, "provider": {"vote": {
                    "cast": 3, "positives": 0, "tie": False}}}, {
            "stage": "judge", "state": "ok", "attempts": 1, "view": "complete", "warnings": []}
    monkeypatch.setattr("detector.api.run_judge", judge)


def install_review(monkeypatch):
    calls = []
    def transport(**kwargs):
        from detector.runtime import record_request
        purpose = kwargs["purpose"]
        calls.append(kwargs)
        record_request({"purpose": purpose, "usage": {"total_tokens": 2}})
        if purpose == "recall_scanner":
            assert "CAUSAL ANOMALIES:" in kwargs["user"]
            assert "correct_result" not in kwargs["user"]
            return {"candidates": [{"candidate_id": "c1", "claim": "wrong arithmetic result",
                                     "locus_event_id": "event_001", "evidence_refs": ["event_001"], "quote": "5"}]}, {}
        if purpose == "causal_upgrade":
            return {"upgrades": [{"candidate_id": "c1", "content_error": True, "causal": True,
                                  "corrected_step": 1, "reason": "the wrong value is delivered"}]}, {}
        if purpose == "step_tracer":
            assert "CORRECT RESULT: \n" in kwargs["user"]
            return tracer_reply()
        if purpose == "defense":
            return {"verdict": "substantiated", "confirmed_step": 1, "evidence_refs": ["event_001"], "reason": "verified"}, {}
        raise AssertionError(purpose)
    monkeypatch.setattr("detector.provider._provider_call", transport)
    return calls


def offline_policy():
    from detector.causal_audit import OfflineFeedbackPolicy
    return OfflineFeedbackPolicy("test-review-v1", lambda anomalies: bool(anomalies))


def test_production_config_cannot_activate_test_calibration(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    result = Detector(cfg, require_credentials=False).detect(record())
    assert result["state"] == "verified_clean"
    assert result["proof"]["negative_feedback"]["reason"] == "calibration_unavailable"


def test_valid_offline_policy_reopens_full_chain_once(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert [call["purpose"] for call in calls] == ["recall_scanner", *(["causal_upgrade"] * 3), "step_tracer", "defense"]
    assert result["state"] == "verified_error", result["proof"]["negative_feedback"]
    assert result["proof"]["negative_feedback"]["state"] == "reviewed", result["proof"]["negative_feedback"]
    assert result["execution"]["provider_attempts"] == 7


def test_no_verified_anomaly_does_not_reopen_high_risk_claim(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record("4"))
    assert result["state"] == "verified_clean"
    assert calls == []
    assert result["proof"]["negative_feedback"]["reason"] == "no_verified_anomaly"


def test_budget_exhausted_review_keeps_base_result_and_actual_cost(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["causal_audit"]["max_added_provider_requests_per_case"] = 1
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert [call["purpose"] for call in calls] == ["recall_scanner"]
    assert result["state"] == "verified_clean"
    assert result["proof"]["negative_feedback"]["state"] == "budget_exhausted"
    assert result["execution"]["provider_attempts"] == 2


def test_shadow_stays_observational_with_injected_policy(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["causal_audit"]["mode"] = "shadow"
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert result["state"] == "verified_clean"
    assert calls == []


def test_test_policy_is_rejected_by_credentialed_detector():
    with pytest.raises(ValueError, match="offline"):
        Detector(feedback_config(), offline_feedback_policy=offline_policy())


@pytest.mark.parametrize("outcome", [False, "allow"])
def test_policy_refusal_and_invalid_return_do_not_run_review(monkeypatch, outcome):
    from detector.causal_audit import OfflineFeedbackPolicy
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    detector = Detector(cfg, require_credentials=False,
                        offline_feedback_policy=OfflineFeedbackPolicy("test-refused", lambda rows: outcome))
    if outcome is False:
        result = detector.detect(record())
        assert result["proof"]["negative_feedback"]["reason"] == "policy_refused"
        assert result["state"] == "verified_clean"
    else:
        with pytest.raises(ValueError, match="boolean"):
            detector.detect(record())
    assert calls == []


def test_review_run_stop_propagates_and_journals_base_proof(monkeypatch):
    from detector.runtime import RunStopped, record_request
    from detector.runner import detect_with_transport_retry
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    stop = RunStopped({"error_type": "cancelled"})
    def transport(**kwargs):
        record_request({"purpose": kwargs["purpose"], "usage": None})
        raise stop
    monkeypatch.setattr("detector.provider._provider_call", transport)
    attempts = []
    detector = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy())
    with pytest.raises(RunStopped) as failure:
        detect_with_transport_retry(detector, record(), case_id="test", attempt_observer=attempts.append)
    assert failure.value is stop
    assert attempts[0]["result"]["state"] == "verified_clean"
    assert attempts[0]["error"]["type"] == "RunStopped"
    assert attempts[0]["execution"]["provider_attempts"] == 2


def test_fatal_review_preserves_base_proof_and_classification(monkeypatch):
    from detector.provider import ProviderError
    from detector.measurement import fatal_failure
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    def refused(**kwargs):
        raise ProviderError("recall_scanner", "bad credentials", error_type="authentication_error", retryable=False)
    monkeypatch.setattr("detector.provider._provider_call", refused)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert result["state"] == "unresolved"
    assert fatal_failure(result)["error_type"] == "authentication_error"
    assert result["base_detection_result"]["state"] == "verified_clean"


def test_optional_request_cannot_finish_past_added_deadline(monkeypatch):
    from detector.runtime import AddedBudgetExceeded, added_budget_scope, added_request_scope
    from detector.provider import provider_call
    now = [0.0]
    monkeypatch.setattr("detector.runtime.time.monotonic", lambda: now[0])
    cfg = feedback_config()
    def slow(**kwargs):
        now[0] = 31
        return {}, {}
    monkeypatch.setattr("detector.provider._provider_call", slow)
    with added_budget_scope(cfg["causal_audit"]) as budget:
        budget.begin()
        with added_request_scope(budget), pytest.raises(AddedBudgetExceeded, match="time"):
            provider_call(route=cfg["judge_route"], system="s", user="u", purpose="test")


def test_internal_review_reuses_judge_with_an_active_judge_system_hook(monkeypatch):
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import _candidate
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(
        record(), interventions=build_intervention_registry([_candidate(hook="judge.system")]))
    assert result["proof"]["negative_feedback"]["state"] == "reviewed", result["proof"]["negative_feedback"]
    assert result["execution"]["provider_attempts"] == 1 + len(calls)


def test_internal_review_refuses_changed_record(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    detector = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy())
    original = detector._detect
    def changed(value, **kwargs):
        if kwargs.get("causal_review") is not None:
            value = {**value, "question": "Changed question"}
        return original(value, **kwargs)
    monkeypatch.setattr(detector, "_detect", changed)
    with pytest.raises(ValueError, match="continuation input identity"):
        detector.detect(record())


def test_optional_review_is_not_reopened_by_whole_case_retries(monkeypatch):
    from detector.provider import ProviderError
    from detector.runtime import record_request
    from detector.runner import detect_with_transport_retry
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs["purpose"])
        record_request({"purpose": kwargs["purpose"], "usage": None})
        raise ProviderError(kwargs["purpose"], "offline transient failure", error_type="provider_error")
    monkeypatch.setattr("detector.provider._provider_call", unavailable)
    result = detect_with_transport_retry(
        Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()),
        record(), case_id="test")
    assert calls == ["recall_scanner"]
    assert result["proof"]["negative_feedback"]["reason"] == "review_already_attempted"
    assert result["execution"]["provider_attempts"] == 3
    assert len(result["execution"]["case_attempts"]) == 2


def test_separate_cases_each_receive_one_optional_review(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["judge_route"]["votes"] = 3
    calls = install_review(monkeypatch)
    detector = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy())
    for case_id in ("first", "second"):
        result = detector.detect(record(), case_id=case_id)
        assert result["proof"]["negative_feedback"]["state"] == "reviewed", result["proof"]["negative_feedback"]
    assert len([call for call in calls if call["purpose"] == "recall_scanner"]) == 2


@pytest.mark.parametrize("mode", ["shadow", "tracer_context"])
def test_optional_audit_fatal_preserves_completed_clean_proof(monkeypatch, mode):
    from detector.provider import ProviderError
    from detector.runtime import record_request
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["causal_audit"].update(mode=mode, counterfactual_mode="model_hypothesis")
    cfg["judge_route"]["votes"] = 3
    def refused(**kwargs):
        record_request({"purpose": kwargs["purpose"], "usage": None})
        raise ProviderError(kwargs["purpose"], "bad credentials", error_type="authentication_error", retryable=False)
    monkeypatch.setattr("detector.provider._provider_call", refused)
    result = Detector(cfg, require_credentials=False).detect(record())
    assert result["state"] == "unresolved"
    assert result["execution"]["provider_attempts"] == 2
    assert result["base_detection_result"]["state"] == "verified_clean"
    assert result["base_detection_result"]["proof"]["judge"]["is_hallucination"] is False


def test_optional_audit_cancellation_journals_completed_clean_proof(monkeypatch):
    from detector.runtime import RunStopped, record_request
    from detector.runner import detect_with_transport_retry
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg["causal_audit"]["counterfactual_mode"] = "model_hypothesis"
    cfg["judge_route"]["votes"] = 3
    stop = RunStopped({"error_type": "cancelled"})
    def cancelled(**kwargs):
        record_request({"purpose": kwargs["purpose"], "usage": None})
        raise stop
    monkeypatch.setattr("detector.provider._provider_call", cancelled)
    attempts = []
    with pytest.raises(RunStopped) as failure:
        detect_with_transport_retry(Detector(cfg, require_credentials=False), record(),
                                    case_id="test", attempt_observer=attempts.append)
    assert failure.value is stop
    assert len(attempts) == 1
    assert attempts[0]["result"]["state"] == "verified_clean"
    assert attempts[0]["execution"]["provider_attempts"] == 2
