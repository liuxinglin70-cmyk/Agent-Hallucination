import json
import time
from unittest.mock import patch

import pytest

from detector.api import Detector
from detector.defense import run_defense
from detector.packet import build_packet
from detector.runner import run_batch
from detector.runtime import RunControl, deadline_scope, run_scope
from detector.tests.test_judge_defense import POSITIVE
from detector.tests.test_causal_feedback import (
    feedback_config, install_base_judge, install_review, offline_policy,
)
from detector.tests.test_causal_audit import record

def test_optional_parallel_budget_exhaustion_preserves_run(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config()
    cfg['judge_route']['votes'] = 3
    cfg['defense_route']['votes'] = 3
    calls = install_review(monkeypatch)
    control = RunControl(3)
    with run_scope(control):
        result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert len(calls) == 6
    assert result['execution']['provider_attempts'] == 7
    assert result['state'] == 'verified_clean'
    assert result['proof']['negative_feedback']['state'] == 'budget_exhausted'
    assert not control.stopped.is_set(), 'optional per-case budget must not cancel the run'


def test_optional_transport_added_deadline_preserves_clean_base(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config(max_added_seconds_per_case=1)
    cfg['judge_route']['votes'] = 3
    cfg['recall_route'] = dict(cfg['judge_route'])
    cfg['case_timeout_seconds'] = 10
    monkeypatch.setenv('K', 'offline-dummy')
    def exhausted(**kwargs):
        time.sleep(kwargs['timeout'] + 0.01)
        raise TimeoutError('controlled added time exhaustion')
    control = RunControl(3)
    with patch('detector.provider._isolated_transport', side_effect=exhausted), run_scope(control):
        result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert result['state'] == 'verified_clean', 'added deadline must leave the still-valid base decision authoritative'
    assert result['proof']['negative_feedback']['state'] == 'budget_exhausted'
    assert not control.stopped.is_set()


def test_optional_capacity_added_deadline_preserves_clean_base(monkeypatch):
    install_base_judge(monkeypatch)
    cfg = feedback_config(max_added_seconds_per_case=1)
    cfg['judge_route']['votes'] = 3
    cfg['recall_route'] = dict(cfg['judge_route'])
    cfg['case_timeout_seconds'] = 10
    monkeypatch.setenv('K', 'offline-dummy')
    control = RunControl(1)
    with patch('detector.provider._isolated_transport', side_effect=AssertionError('must not dispatch')) as dispatch:
        with control.request_slot(), run_scope(control):
            result = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy()).detect(record())
    assert dispatch.call_count == 0
    assert result['state'] == 'verified_clean'
    assert result['proof']['negative_feedback']['state'] == 'budget_exhausted'
    assert not control.stopped.is_set()


def test_optional_parallel_budget_exhaustion_batch_keeps_other_cases(tmp_path, monkeypatch):
    from detector.runtime import added_budget_scope, added_request_scope, AddedBudgetExceeded
    from detector.tests.test_batch_failure_handling import _envelope
    cfg = feedback_config()
    cfg['defense_route']['votes'] = 3
    cfg['causal_audit']['max_added_provider_requests_per_case'] = 1
    def transport(**kwargs):
        return {'verdict': 'refuted', 'confirmed_step': None, 'evidence_refs': ['event_002'], 'reason': 'verified'}, {'attempts': 1}
    monkeypatch.setattr('detector.provider._provider_call', transport)
    class OptionalDefenseDetector:
        def __init__(self, config):
            self.config = config
        def detect(self, value, *, case_id, interventions=None):
            with added_budget_scope(self.config['causal_audit']) as budget:
                budget.begin()
                try:
                    with added_request_scope(budget):
                        run_defense(packet=build_packet(value), positive=POSITIVE, config=self.config)
                except AddedBudgetExceeded:
                    pass
            return _envelope(case_id)
    cases = ['a.json', 'b.json', 'c.json']
    data = tmp_path / 'data'
    data.mkdir()
    for case in cases:
        (data / case).write_text(json.dumps(record()), encoding='utf-8')
    monkeypatch.setattr('detector.runner.Detector', OptionalDefenseDetector)
    summary = run_batch(dataset_root=data, cases=cases, run_id='r', runs_root=tmp_path / 'runs', config=cfg, workers=1)
    assert summary['stop'] == {}
    assert summary['completed'] == 3


@pytest.mark.parametrize("queued", [False, True])
def test_case_deadline_keeps_priority_over_added_deadline(monkeypatch, queued):
    install_base_judge(monkeypatch)
    cfg = feedback_config(max_added_seconds_per_case=2)
    cfg["judge_route"]["votes"] = 3
    cfg["recall_route"] = dict(cfg["judge_route"])
    cfg["case_timeout_seconds"] = 10
    monkeypatch.setenv("K", "offline-dummy")
    control = RunControl(1)
    def transport(**kwargs):
        assert not queued
        time.sleep(kwargs["timeout"] + 0.01)
        raise TimeoutError("case timeout")
    detector = Detector(cfg, require_credentials=False, offline_feedback_policy=offline_policy())
    with patch("detector.provider._isolated_transport", side_effect=transport) as dispatch:
        with run_scope(control), deadline_scope(seconds=1):
            if queued:
                with control.request_slot():
                    result = detector.detect(record())
            else:
                result = detector.detect(record())
    assert dispatch.call_count == (0 if queued else 1)
    assert result["state"] == "unresolved"
    assert result["base_detection_result"]["state"] == "verified_clean"
    reason = result["authority_failure_reasons"][0]
    assert reason["error_type"] == "case_deadline_exceeded"
    assert reason["retryable"] is False
    assert not control.stopped.is_set()


@pytest.mark.parametrize('phase', ['profile', 'arithmetic'])
def test_true_case_deadline_during_deterministic_audit_is_terminal(monkeypatch, phase):
    import detector.causal_audit as audit
    from detector.tests.test_causal_audit import detector_config, install_base_judge
    install_base_judge(monkeypatch)
    now = [0.0]
    monkeypatch.setattr('detector.runtime.time.monotonic', lambda: now[0])
    attr = 'build_step_risk_profile' if phase == 'profile' else 'arithmetic_check'
    original = getattr(audit, attr)
    def expire(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 1.1
        return result
    monkeypatch.setattr(audit, attr, expire)
    control = RunControl(1)
    cfg = detector_config(max_added_seconds_per_case=100)
    with run_scope(control), deadline_scope(deadline=1.0):
        result = Detector(cfg, require_credentials=False).detect(record())
    assert result['state'] == 'unresolved'
    assert result['authority_failure_reasons'][0]['error_type'] == 'case_deadline_exceeded'
    assert result['base_detection_result']['state'] == 'verified_clean'
    assert result["execution"]["provider_attempts"] == 1
    assert not control.stopped.is_set()
