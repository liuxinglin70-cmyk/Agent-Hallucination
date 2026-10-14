"""Ensemble uncertainty must not depend on the representative ballot."""

from copy import deepcopy
from itertools import permutations

import pytest

from detector.api import _judge_negative_needs_recall
from detector.judge import run_judge
from detector.tests.test_voting_hearings import CONFIG, JUDGE_FALSE


@pytest.mark.parametrize("confidences", sorted(set(permutations(("high", "medium", "high")))))
def test_weak_negative_ballot_always_requests_recall(wrong_answer_packet, monkeypatch, confidences):
    ballots = [{**deepcopy(JUDGE_FALSE), "confidence": confidence} for confidence in confidences]
    expected_raw = deepcopy(ballots)
    monkeypatch.setattr("detector.judge.provider_call", lambda **kwargs: (ballots.pop(0), {"attempts": 1}))
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3, "vote_concurrency": 1}}
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert status["state"] == "ok"
    assert result["parsed"]["confidence"] == "medium"
    assert result["parsed"]["is_hallucination"] is False
    assert result["raw"] == expected_raw
    assert _judge_negative_needs_recall(result, config) is True


@pytest.mark.parametrize("confidences,expected_recall", [
    (("high", "high", "high"), False),
    (("high", "high", "low"), True),
    (("low", "medium", "high"), True),
])
def test_negative_confidence_preserves_consensus_boundary(
    wrong_answer_packet, monkeypatch, confidences, expected_recall,
):
    ballots = [{**deepcopy(JUDGE_FALSE), "confidence": confidence} for confidence in confidences]
    monkeypatch.setattr("detector.judge.provider_call", lambda **kwargs: (ballots.pop(0), {"attempts": 1}))
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3, "vote_concurrency": 1}}
    result, _ = run_judge(packet=wrong_answer_packet, config=config)
    assert _judge_negative_needs_recall(result, config) is expected_recall
