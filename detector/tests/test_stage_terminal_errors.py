import pytest

from detector.config import validate_config
from detector.defense import run_defense
from detector.judge import run_judge
from detector.provider import ProviderError
from detector.recaller import run_causal_upgrade, run_recall_scan
from detector.step_tracer import run_step_tracer
from detector.tests.test_judge_defense import POSITIVE


@pytest.mark.parametrize("stage", ["judge", "scanner", "upgrade", "tracer", "defense"])
@pytest.mark.parametrize("error_type", ["case_deadline_exceeded", "missing_credential"])
def test_terminal_error_cannot_become_a_vote_abstention(sample_packet, monkeypatch, stage, error_type):
    calls = []
    failure = ProviderError(stage, "terminal test failure", error_type=error_type, retryable=False)
    def terminal(**kwargs):
        calls.append(kwargs["purpose"])
        raise failure
    monkeypatch.setattr("detector.provider._provider_call", terminal)
    config = validate_config({"judge_route": {"votes": 1, "vote_topup": 2},
                              "defense_route": {"votes": 1, "vote_topup": 2}}, require_credentials=False)
    with pytest.raises(ProviderError) as outcome:
        if stage == "judge":
            run_judge(packet=sample_packet, config=config)
        elif stage == "scanner":
            run_recall_scan(packet=sample_packet, config=config)
        elif stage == "upgrade":
            run_causal_upgrade(packet=sample_packet, config=config, candidates=[{
                "candidate_id": "c1", "claim": "wrong value", "locus_event_id": "event_002",
                "evidence_refs": ["event_002"], "quote": "5"}])
        elif stage == "tracer":
            run_step_tracer(packet=sample_packet, config=config, positive=POSITIVE,
                            delivered_result="5", correct_result="4")
        else:
            run_defense(packet=sample_packet, config=config, positive=POSITIVE)
    assert outcome.value is failure
    assert len(calls) == 1
