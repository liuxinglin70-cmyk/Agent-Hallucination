"""Independent calls overlap without changing authority, inputs or accounting."""

from copy import deepcopy
import subprocess
import sys
import threading
import time

import pytest

from detector.hearing import run_hearing
from detector.interventions import empty_plan
from detector.runtime import RunControl, RunStopped, current_control, deadline_scope, run_scope, usage_scope


def test_hearing_runs_independent_stages_concurrently(monkeypatch, sample_packet):
    from detector import hearing

    barrier = threading.Barrier(2, timeout=2)
    control = RunControl(2)
    plan = empty_plan()
    plan.facts["contract_coverage"] = "covered"
    prompts = []

    def tracer(**kwargs):
        assert current_control() is control
        assert kwargs["intervention_prompt"] == ""
        prompts.append(kwargs["positive"])
        barrier.wait()
        return {"parsed": {"responsible_step": 2}}, {"stage": "step_tracer"}

    def defense(**kwargs):
        assert current_control() is control
        kwargs["plan"].facts["contract_coverage"] = "uncovered"
        barrier.wait()
        return {"parsed": {"verdict": "substantiated"}}, {"stage": "defense"}

    monkeypatch.setattr(hearing, "run_step_tracer", tracer)
    monkeypatch.setattr(hearing, "run_defense", defense)
    positive = {"source": "judge", "step": 2, "reason": "same deviation"}
    with run_scope(control), deadline_scope(seconds=5), usage_scope():
        traced, tracer_status, defended, defense_status = run_hearing(
            packet=sample_packet, positive=positive, config={}, plan=plan)
    assert prompts == [positive]
    assert traced["parsed"]["responsible_step"] == 2
    assert defended["parsed"]["verdict"] == "substantiated"
    assert tracer_status["stage"] == "step_tracer" and defense_status["stage"] == "defense"


def test_diagnostic_preparation_overlaps_defense(monkeypatch, sample_packet):
    from detector import hearing
    barrier = threading.Barrier(2, timeout=2)
    calls = []

    def prepare():
        calls.append("diagnostics")
        barrier.wait()
        return None

    def defense(**kwargs):
        calls.append("defense")
        barrier.wait()
        return {}, {"state": "ok"}

    monkeypatch.setattr(hearing, "run_defense", defense)
    monkeypatch.setattr(hearing, "run_step_tracer", lambda **kwargs: ({}, {"state": "ok"}))
    run_hearing(packet=sample_packet, positive={"step": 2}, config={}, plan=empty_plan(),
                diagnostics_factory=prepare)
    assert sorted(calls) == ["defense", "diagnostics"]


def test_nested_usage_scopes_share_one_timeline():
    from detector.runtime import record_request
    with usage_scope() as outer:
        time.sleep(0.01)
        with usage_scope() as inner:
            record_request({"elapsed_seconds": 0.002, "purpose": "judge"})
        record_request({"elapsed_seconds": 0.001, "purpose": "defense"})
    assert len(outer) == 2 and outer[0] == inner[0]
    assert outer[0]["started_seconds"] >= 0.008
    assert outer[1]["finished_seconds"] >= outer[0]["finished_seconds"]


def test_upgrade_ballots_overlap_and_keep_order(monkeypatch, wrong_answer_packet):
    from detector import recaller
    from detector.tests.test_voting_hearings import CONFIG, _candidates

    config = deepcopy(CONFIG)
    config["judge_route"].update(votes=3, vote_concurrency=3)
    barrier = threading.Barrier(3, timeout=2)
    calls = []

    def sample(**kwargs):
        barrier.wait()
        calls.append(kwargs["candidates"])
        rows = [{"candidate_id": c["candidate_id"], "content_error": True, "causal": True,
                 "corrected_step": 2, "reason": "same deviation", "coverage": "answered"}
                for c in kwargs["candidates"]]
        return rows, {"attempts": 1, "usage": {"total_tokens": 7}}, [], None, []

    monkeypatch.setattr(recaller, "_upgrade_sample", sample)
    result, status = recaller.run_causal_upgrade(packet=wrong_answer_packet,
                                               candidates=_candidates(), config=config)
    assert len(calls) == 3
    assert result["ok"] is True
    assert status["attempts"] == 3
    assert result["provider"]["usage_total"]["total_tokens"] == 21


@pytest.mark.parametrize("stage", ["judge", "defense", "tracer", "scanner", "upgrade"])
@pytest.mark.parametrize("repair_fails", [False, True])
def test_stage_repair_preserves_all_known_usage(monkeypatch, wrong_answer_packet, stage, repair_fails):
    from detector import defense, judge, recaller, step_tracer
    from detector.provider import ProviderError
    from detector.tests.test_judge_defense import CONFIG, JUDGE_TRUE, POSITIVE
    from detector.tests.test_voting_hearings import _candidates

    replies = {
        "judge": JUDGE_TRUE,
        "defense": {"verdict": "substantiated", "confirmed_step": 2, "evidence_refs": ["event_002"], "reason": "wrong value"},
        "tracer": {"step": 2, "quote": "5", "referent": "4", "reason": "wrong value", "evidence_refs": ["event_002"]},
        "scanner": {"candidates": _candidates()},
        "upgrade": {"upgrades": [{"candidate_id": c["candidate_id"], "content_error": True, "causal": True,
                                    "corrected_step": 2 - i, "reason": "wrong value"} for i, c in enumerate(_candidates())]},
    }
    calls = []

    def provider(**kwargs):
        calls.append(kwargs["purpose"])
        if len(calls) == 1:
            return {}, {"attempts": 2, "usage": {"total_tokens": 6}, "usage_total": {"total_tokens": 11}}
        if repair_fails:
            raise ProviderError(stage, "offline repair outage", attempts=1)
        return deepcopy(replies[stage]), {"attempts": 1, "usage": {"total_tokens": 5}}

    modules = {"judge": judge, "defense": defense, "tracer": step_tracer, "scanner": recaller, "upgrade": recaller}
    monkeypatch.setattr(modules[stage], "provider_call", provider)
    config = deepcopy(CONFIG)
    config["recall_route"] = dict(config["judge_route"])
    for route in (config["judge_route"], config["defense_route"]):
        route.update(votes=1, vote_topup=0)
    kwargs = {"packet": wrong_answer_packet, "config": config}
    if stage == "judge":
        result, status = judge.run_judge(**kwargs)
    elif stage == "defense":
        result, status = defense.run_defense(**kwargs, positive=POSITIVE)
    elif stage == "tracer":
        result, status = step_tracer.run_step_tracer(**kwargs, delivered_result="5", correct_result="4", positive=POSITIVE)
    elif stage == "scanner":
        result, status = recaller.run_recall_scan(**kwargs)
    else:
        result, status = recaller.run_causal_upgrade(**kwargs, candidates=_candidates())
    assert len(calls) == 2
    assert status["attempts"] == 3
    assert result["provider"]["usage_total"]["total_tokens"] == (11 if repair_fails else 16)
    assert result["ok"] is (not repair_fails)


@pytest.mark.platform
def test_running_transport_is_killed_on_run_cancellation(monkeypatch):
    from detector import provider

    original = subprocess.Popen
    children = []
    control = RunControl(2)

    def local_child(*args, **kwargs):
        child = original([sys.executable, "-c", "import sys,time;sys.stdin.buffer.read();time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(provider.subprocess, "Popen", local_child)
    timer = threading.Timer(0.2, lambda: control.stop({"error_type": "cancelled"}))
    started = time.monotonic()
    timer.start()
    try:
        with run_scope(control), pytest.raises(RunStopped):
            provider._isolated_transport(url="http://127.0.0.1/unused", headers={}, body=b"{}", timeout=20)
    finally:
        timer.cancel()
        timer.join()
    assert time.monotonic() - started < 3
    assert children and children[0].poll() is not None
