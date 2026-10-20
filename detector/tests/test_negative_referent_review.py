"""An agreed clean answer is not an independent check of its asserted referent."""
from collections import Counter
from copy import deepcopy
from itertools import permutations

import pytest

from detector.api import Detector
from detector.tests.test_voting_hearings import CONFIG, JUDGE_FALSE


def run_review(monkeypatch, bases, *, upgrade=True, defense="substantiated"):
    calls = []
    bases = iter(bases)

    def provider(**kwargs):
        purpose = kwargs["purpose"]
        calls.append(purpose)
        if purpose == "judge":
            output = {**deepcopy(JUDGE_FALSE), "basis": next(bases),
                      "delivered_result": "5", "correct_result": "5"}
        elif purpose == "recall_scanner":
            output = {"candidates": [{"candidate_id": "c1", "claim": "The answer is 5 instead of the recorded 4.",
                "locus_event_id": "event_002", "evidence_refs": ["event_001_tool_01", "event_002"],
                "evidence_citations": [], "quote": "5"}]}
        elif purpose == "causal_upgrade":
            output = {"upgrades": [{"candidate_id": "c1", "content_error": upgrade, "causal": upgrade,
                "corrected_step": 2 if upgrade else None, "evidence_refs": [], "reason": "Check actual arithmetic and delivery."}]}
        elif purpose == "step_tracer":
            output = {"step": 2, "quote": "5", "referent": "4", "evidence_refs": ["event_002"], "reason": "Wrong delivered sum."}
        elif purpose == "defense":
            output = {"verdict": defense, "confirmed_step": 2 if defense == "substantiated" else None,
                "evidence_refs": ["event_001", "event_002"], "reason": "Independently checked the allegation."}
        else:
            raise AssertionError(purpose)
        return output, {"attempts": 1}

    monkeypatch.setattr("detector.provider._provider_call", provider)
    config = {**CONFIG, "causal_audit": {"mode": "off"},
              "judge_route": {**CONFIG["judge_route"], "votes": 3, "vote_concurrency": 1},
              "defense_route": {**CONFIG["defense_route"], "votes": 3, "vote_concurrency": 1}}
    result = Detector(config, require_credentials=False).detect({"question": "What is 2 + 2?", "history": [
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expression": "2+2"}}],
         "tool_responses": [{"result": 4}]},
        {"role": "assistant", "content": "The answer is 5."},
    ]})
    return result, Counter(calls)


@pytest.mark.parametrize("basis", ["derivation", "world_knowledge"])
def test_high_confidence_referent_is_checked_before_a_clean_exit(monkeypatch, basis):
    result, calls = run_review(monkeypatch, [basis] * 3)
    assert calls["judge"] == calls["causal_upgrade"] == calls["defense"] == 3
    assert calls["recall_scanner"] == calls["step_tracer"] == 1
    assert result["state"] == "verified_error" and result["decision_authority"] == "authoritative"
    metadata = result["proof"]["recall_trigger_metadata"]
    assert metadata["triggered_by_unverified_referent"] is True
    assert metadata["unverified_referent_bases"] == [basis]
    assert metadata["triggered_by_judge_uncertainty"] is False


@pytest.mark.parametrize("bases", sorted(set(permutations(("observation", "derivation", "observation")))))
def test_any_negative_ballot_requiring_independent_check_reopens_review(monkeypatch, bases):
    result, calls = run_review(monkeypatch, bases)
    assert calls["recall_scanner"] == 1
    assert result["state"] == "verified_error"


def test_unanimous_observation_based_clean_keeps_the_existing_fast_path(monkeypatch):
    result, calls = run_review(monkeypatch, ["observation"] * 3)
    assert calls == Counter(judge=3)
    assert result["state"] == "verified_clean"


def test_review_candidate_cannot_convict_without_upgrade(monkeypatch):
    result, calls = run_review(monkeypatch, ["world_knowledge"] * 3, upgrade=False)
    assert calls["recall_scanner"] == 1 and calls["causal_upgrade"] == 3
    assert calls["defense"] == 0 and result["state"] == "verified_clean"
    assert result["decision_authority"] == "authoritative"


@pytest.mark.parametrize("verdict,authority", [("refuted", "authoritative"), ("inconclusive", "degraded_judge_only")])
def test_review_candidate_still_requires_defense(monkeypatch, verdict, authority):
    result, calls = run_review(monkeypatch, ["derivation"] * 3, defense=verdict)
    assert calls["defense"] == 3
    assert result["decision_authority"] == authority
    if verdict == "refuted":
        assert result["state"] == "verified_clean"
