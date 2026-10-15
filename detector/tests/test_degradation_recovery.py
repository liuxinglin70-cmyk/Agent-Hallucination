"""Runtime failures remain failures through voting, retry and hearing projection."""

from collections import Counter
from copy import deepcopy
import json

import pytest

from detector.api import Detector
from detector.config import load_config
from detector.defense import run_defense
from detector.evidence_view import referent_source_block
from detector.hearing import judge_target
from detector.measurement import measurement_status, needs_remeasurement, retry_in_run
from detector.provider import ProviderError
from detector.runner import detect_with_transport_retry
from detector.runtime import record_request
from detector.stage_retry import StageRetryCache
from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE


def test_default_detector_output_uses_provider_capacity():
    config = load_config(require_credentials=False)
    for name in ("judge_route", "defense_route", "recall_route"):
        assert config[name].get("max_tokens") is None
        assert config[name].get("max_output_tokens_per_request") is None
        assert config[name].get("long_output_tokens_per_request") is None


@pytest.mark.parametrize("failed_stage", ["judge", "defense"])
def test_partial_quorum_outage_recovers_without_repeating_successful_stages(
    monkeypatch, sample_record, failed_stage,
):
    counts = Counter()

    def transport(**kwargs):
        purpose = kwargs["purpose"]
        counts[purpose] += 1
        record_request({"request_id": purpose + str(counts[purpose]), "purpose": purpose,
                        "usage": {"total_tokens": 5}})
        if purpose == failed_stage and counts[purpose] in (2, 3):
            raise ProviderError(purpose, "offline transport failure", attempts=1)
        if purpose == "judge":
            parsed = deepcopy(JUDGE_TRUE)
        elif purpose == "step_tracer":
            parsed = {"step": 2, "quote": "5", "referent": "4", "reason": "wrong sum",
                      "evidence_refs": ["event_002"]}
        elif purpose == "defense":
            parsed = {"verdict": "substantiated", "confirmed_step": 2, "reason": "wrong sum",
                      "evidence_refs": ["event_002"]}
        else:
            raise AssertionError(purpose)
        return parsed, {"attempts": 1, "usage": {"total_tokens": 5}}

    monkeypatch.setattr("detector.provider._provider_call", transport)
    config = deepcopy(CONFIG)
    for route in ("judge_route", "defense_route"):
        config[route].update(votes=3, vote_topup=0, vote_concurrency=1)
    record = deepcopy(sample_record)
    record["history"][-1]["content"] = "The answer is 5."
    result = detect_with_transport_retry(Detector(config, require_credentials=False), record, case_id="outage")
    assert result["decision_authority"] == "authoritative"
    assert len(result["execution"]["case_attempts"]) == 2
    first = result["execution"]["case_attempts"][0]["result"]
    assert measurement_status(first) == "infrastructure_failure"
    assert needs_remeasurement(first)
    failure = next(r for r in first["authority_failure_reasons"] if r["component"] == failed_stage)
    assert len(failure["vote_failures"]) == 2
    assert counts == {"judge": 6 if failed_stage == "judge" else 3,
                      "defense": 6 if failed_stage == "defense" else 3, "step_tracer": 1}
    assert result["execution"]["provider_attempts"] == 10
    assert result["execution"]["usage"]["total_tokens"] == 50


def test_retry_cache_does_not_store_incomplete_votes():
    cache = StageRetryCache()
    cache.bind({"case": "same"})
    calls = []

    def produce():
        calls.append(1)
        return {"ok": True, "provider": {"vote": {"quorum_met": False}}}, {"state": "ok"}

    cache.begin_attempt(1)
    cache.evaluate("defense", {}, produce)
    cache.begin_attempt(2)
    cache.evaluate("defense", {}, produce)
    assert len(calls) == 2
    assert cache.reused == []


def test_hearing_preserves_declared_basis_without_promoting_it_to_evidence(wrong_answer_packet):
    parsed = {**deepcopy(JUDGE_TRUE), "basis": "world_knowledge"}
    target = judge_target(parsed)
    block = referent_source_block(wrong_answer_packet, target)
    index = json.JSONDecoder().raw_decode(block[block.index("{"):])[0]
    assert index["basis"] == parsed["basis"]
    assert index["truth_status"] == "requires_independent_verification"
    assert index["decision_authority"] == "none"


def test_valid_inconclusive_votes_are_not_retried(monkeypatch, wrong_answer_packet):
    monkeypatch.setattr("detector.defense.provider_call", lambda **kwargs: (
        {"verdict": "inconclusive", "confirmed_step": None, "evidence_refs": [],
         "reason": "independent evidence is missing"}, {"attempts": 1}))
    result, status = run_defense(packet=wrong_answer_packet,
        positive={"step": 2, "evidence_refs": ["event_002"]},
        config={"defense_route": {"votes": 3, "vote_concurrency": 1}})
    assert result["ok"] and status["state"] == "ok"
    from detector.tests.test_batch_failure_handling import _envelope
    envelope = _envelope("uncertain", authority="degraded_judge_only", reasons=[{
        "component": "defense", "code": "inconclusive", "vote_failures": result.get("vote_failures", [])}])
    assert measurement_status(envelope) == "degraded"
    assert not retry_in_run(envelope)


@pytest.mark.parametrize("error_type", ["truncated_output", "contract_error", "no_quorum"])
def test_failed_stage_is_remeasurable_but_fixed_output_cap_is_not_retried(error_type):
    from detector.tests.test_batch_failure_handling import _envelope
    envelope = _envelope("failed", authority="degraded_judge_only", reasons=[{
        "component": "defense", "code": error_type, "error_type": error_type}])
    assert measurement_status(envelope) == "infrastructure_failure"
    assert needs_remeasurement(envelope)
    assert retry_in_run(envelope) is (error_type != "truncated_output")


def test_nested_terminal_failure_cannot_reopen_a_case_budget():
    from detector.tests.test_batch_failure_handling import _envelope
    envelope = _envelope("expired", authority="invalid", state="unresolved", reasons=[{
        "component": "judge", "code": "no_quorum", "vote_failures": [{
            "error_type": "case_deadline_exceeded", "retryable": False}]}])
    assert measurement_status(envelope) == "infrastructure_failure"
    assert needs_remeasurement(envelope)
    assert not retry_in_run(envelope)


def test_persistent_partial_quorum_stops_at_existing_case_attempt_limit(monkeypatch, sample_record):
    from detector.tests.test_batch_failure_handling import _envelope

    class FailingDetector:
        config = {"case_timeout_seconds": 5}
        calls = 0

        def detect(self, *args, **kwargs):
            self.calls += 1
            return _envelope("bounded", authority="invalid", state="unresolved", reasons=[{
                "component": "judge", "code": "no_quorum", "vote_failures": [{
                    "error_type": "provider_error", "message": "offline outage"}]}])

    detector = FailingDetector()
    result = detect_with_transport_retry(detector, sample_record, case_id="bounded")
    assert detector.calls == len(result["execution"]["case_attempts"]) == 3
    assert result["decision_authority"] == "invalid"


def test_default_request_omits_output_ceiling_and_preserves_timeout(monkeypatch):
    from detector import provider
    monkeypatch.setenv("AUTODL_API_KEY", "offline-fixture")
    requests = []

    def transport(**kwargs):
        requests.append({"payload": json.loads(kwargs["body"]), "timeout": kwargs["timeout"]})
        return 200, json.dumps({"choices": [{"message": {"content": '{"ok": true}'},
                                            "finish_reason": "stop"}]}).encode()

    monkeypatch.setattr(provider, "_isolated_transport", transport)
    config = load_config(require_credentials=False)
    for name in ("judge_route", "defense_route", "recall_route"):
        provider.provider_call(route=config[name], system="fixture", user="fixture", purpose=name)
    assert len(requests) == 3
    assert all("max_tokens" not in r["payload"] for r in requests)
    assert all(0 < r["timeout"] <= 900 for r in requests)
