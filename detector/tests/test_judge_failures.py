from copy import deepcopy

import pytest

from detector.judge import run_judge
from detector.provider import ProviderError
from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE


@pytest.mark.parametrize("mode,expected", [
    ("contract", "contract_error"),
    ("timeout", "request_timeout"),
    ("partial", "no_quorum"),
])
def test_judge_preserves_failure_classification(wrong_answer_packet, monkeypatch, mode, expected):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs["purpose"])
        if mode == "timeout":
            raise ProviderError("judge", "deadline", error_type="request_timeout", attempts=1)
        if mode == "partial" and len(calls) == 1:
            return deepcopy(JUDGE_TRUE), {"attempts": 1, "usage": {}}
        return {"is_hallucination": "yes"}, {"attempts": 1, "usage": {}}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = deepcopy(CONFIG)
    config["judge_route"].update(votes=3 if mode == "partial" else 1, vote_concurrency=1)
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert not result["ok"]
    assert result["error_type"] == expected
    assert status["state"] == expected
