from copy import deepcopy

import pytest

import detector.api as api
from detector.api import Detector
from detector.tests.test_replay_inputs import setup_recall_stages
from detector.tests.test_api import CONFIG
from detector.recaller import assemble_upgraded_candidates
from evolution.backends import LiveDetectorBackend


@pytest.mark.parametrize("component,field", [
    ("step_tracer", "proof"), ("defense", "proof"),
    ("step_tracer", "stage_status"), ("defense", "stage_status"),
    ("step_tracer", "providers"), ("defense", "providers"),
])
def test_recall_first_hearing_reaches_every_envelope_mirror(monkeypatch, component, field):
    _, cfg, record = setup_recall_stages(monkeypatch)
    for name in ("step_tracer", "defense"):
        original = getattr(api, f"run_{name}")

        def run(original=original, name=name, **kwargs):
            result, status = original(**kwargs)
            result["provider"] = {"model": f"offline-{name}"}
            status["stage"] = name
            return result, status

        monkeypatch.setattr(f"detector.api.run_{name}", run)
    result = Detector(cfg, require_credentials=False).detect(record)
    first = result["proof"]["hearings"][0]
    prefix = "tracer" if component == "step_tracer" else "defense"
    expected = (first[f"{prefix}_result"]["parsed"] if field == "proof" else
                first[f"{prefix}_status"] if field == "stage_status" else
                first[f"{prefix}_result"]["provider"])
    assert result[field][component] == expected


def multi_hearing_stages(monkeypatch, judge_positive):
    record = {"question": "What is 2+2?", "history": [
        {"role": "assistant", "content": "I read 5."},
        {"role": "assistant", "content": "I read 6."},
        {"role": "assistant", "content": "The answer is 7."},
    ]}
    parsed = {"is_hallucination": judge_positive,
              "hallucination_step": 3 if judge_positive else None,
              "hallucination_reason": "judge allegation" if judge_positive else None,
              "delivered_result": "7", "correct_result": "4" if judge_positive else "7",
              "confidence": "high", "basis": "derivation" if judge_positive else None,
              "root_cause": {"step": 3, "quote": "7", "referent": "4"} if judge_positive else None,
              "evidence_refs": ["event_003"] if judge_positive else [],
              "contracts": [{"kind": "exact_value", "requirement": "4"}]}
    candidates = [{"candidate_id": f"c{step}", "claim": f"claim {step}",
                   "locus_event_id": f"event_{step:03d}", "evidence_refs": [f"event_{step:03d}"],
                   "quote": str(step + 4)} for step in (1, 2)]
    rows = [{"candidate_id": f"c{step}", "content_error": True, "causal": True,
             "corrected_step": step, "coverage": "answered", "reason": "wrong value",
             "evidence_refs": []} for step in (1, 2)]
    upgraded = assemble_upgraded_candidates(candidates, rows)
    calls = {"judge": 0, "scanner": 0, "upgrade": 0, "tracer": 0, "defense": 0}

    def stage(name, result):
        def run(**kwargs):
            calls[name] += 1
            return deepcopy(result), {"state": "ok", "view": "complete"}
        return run

    monkeypatch.setattr(api, "run_judge", stage("judge", {"ok": True, "parsed": parsed}))
    monkeypatch.setattr(api, "run_recall_scan", stage("scanner", {"ok": True, "candidates": candidates}))
    monkeypatch.setattr(api, "run_causal_upgrade", stage("upgrade", {
        "ok": True, "upgrades": rows, "upgraded_candidates": upgraded, "upgraded_candidate": upgraded[0],
    }))

    def tracer(**kwargs):
        calls["tracer"] += 1
        step = kwargs["positive"]["step"]
        return {"ok": True, "parsed": {
            "step": step, "quote": str(step + 4), "referent": "4", "reason": "trace",
            "evidence_refs": [f"event_{step:03d}"],
        }, "provider": {"model": f"trace-{step}"}}, {"state": "ok", "view": "complete"}

    def defense(**kwargs):
        calls["defense"] += 1
        positive = kwargs["positive"]
        substantiated = positive["candidate_id"] == "c2"
        return {"ok": True, "parsed": {
            "verdict": "substantiated" if substantiated else "refuted",
            "confirmed_step": 2 if substantiated else None, "reason": "independent verdict",
            "evidence_refs": [f"event_{positive['step']:03d}"],
        }, "provider": {"model": f"defense-{positive['step']}"}}, {"state": "ok", "view": "complete"}

    monkeypatch.setattr(api, "run_step_tracer", tracer)
    monkeypatch.setattr(api, "run_defense", defense)
    return record, calls


@pytest.mark.parametrize("judge_positive", [False, True])
@pytest.mark.parametrize("replay", [False, True])
def test_multiple_hearings_keep_first_mirror_and_adopt_later_conviction(monkeypatch, judge_positive, replay):
    record, calls = multi_hearing_stages(monkeypatch, judge_positive)
    detector = Detector(CONFIG, require_credentials=False)
    result = detector.detect(record)
    if replay:
        frozen = LiveDetectorBackend._frozen_stages(result, set())
        previous = deepcopy(calls)
        result = detector.detect(record, frozen_stages=frozen)
        assert calls == previous
    proof = result["proof"]
    hearings = proof["hearings"]
    assert result["responsible_step"] == 2
    assert result["decision_authority"] == "authoritative"
    assert result["localization_authority"] == "degraded_localization_conflict"
    assert proof["step_tracer"] == hearings[0]["tracer_result"]["parsed"]
    assert proof["defense"]["verdict"] == "refuted"
    assert proof["localization_sources"]["first_hearing_tracer_step"] == (3 if judge_positive else 1)
    assert proof["terminal_basis"]["decisive_facts"]["traced_step"] == 2
    assert hearings[-1]["candidate_id"] == "c2"


@pytest.mark.parametrize("judge_positive", [False, True])
def test_decisive_facts_belong_to_the_convicted_hearing(monkeypatch, judge_positive):
    record, _ = multi_hearing_stages(monkeypatch, judge_positive)
    result = Detector(CONFIG, require_credentials=False).detect(record)
    facts = result["proof"]["terminal_basis"]["decisive_facts"]
    convicted = result["proof"]["hearings"][-1]["positive"]
    assert facts["delivered_result"] == convicted["delivered_result"]
    assert facts["correct_result"] == convicted["correct_result"]
    assert facts["candidate_id"] == convicted["candidate_id"]


def test_no_hearing_keeps_mirrors_empty_and_calls_no_hearing_stage(monkeypatch):
    from detector.tests.test_evidence_flow import _judge
    calls, cfg, record = setup_recall_stages(monkeypatch)
    cfg = deepcopy(cfg)
    cfg["judge_route"]["votes"] = 3
    judge = _judge()
    judge["provider"] = {"vote": {"votes": 3, "cast": 3, "positives": 0,
                                  "winner": False, "tie": False}}
    monkeypatch.setattr(api, "run_judge", lambda **kwargs: (judge, {"state": "ok"}))
    result = Detector(cfg, require_credentials=False).detect(record)
    assert result["proof"]["hearings"] == []
    assert result["proof"]["step_tracer"] is None
    assert result["proof"]["defense"] is None
    assert calls["scanner"] == calls["upgrade"] == calls["tracer"] == calls["defense"] == 0


@pytest.mark.parametrize("stage", ["judge", "tracer", "defense"])
def test_failed_frozen_stage_cannot_supply_a_parsed_verdict(monkeypatch, stage):
    record, calls = multi_hearing_stages(monkeypatch, True)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record), set())
    owner = frozen if stage == "judge" else frozen["hearings"][0]
    owner[f"{stage}_result"]["ok"] = False
    owner[f"{stage}_status"]["state"] = "provider_error"
    if stage == "defense":
        owner["defense_result"]["parsed"].update(verdict="substantiated", confirmed_step=3)
    previous = deepcopy(calls)
    with pytest.raises(ValueError, match="failed.*parsed"):
        detector.detect(record, frozen_stages=frozen)
    assert calls == previous


@pytest.mark.parametrize("stage", ["judge", "tracer", "defense"])
def test_settled_failure_without_a_parsed_verdict_remains_replayable(monkeypatch, stage):
    record, calls = multi_hearing_stages(monkeypatch, True)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record), set())
    owner = frozen if stage == "judge" else frozen["hearings"][0]
    owner[f"{stage}_result"].update(ok=False, parsed=None, error="offline outage", error_type="provider_error")
    owner[f"{stage}_status"]["state"] = "provider_error"
    previous = deepcopy(calls)
    result = detector.detect(record, frozen_stages=frozen)
    assert calls == previous
    if stage == "judge":
        assert result["state"] == "unresolved"
    elif stage == "defense":
        assert result["decision_authority"] == "degraded_judge_only"
    else:
        assert result["responsible_step"] == 2
    assert result["stage_status"]["step_tracer" if stage == "tracer" else stage]["state"] == "provider_error"


@pytest.mark.parametrize("flag", [None, 0, 1, "false"])
def test_frozen_stage_authority_flag_must_be_boolean(monkeypatch, flag):
    record, _ = multi_hearing_stages(monkeypatch, True)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record), set())
    frozen["judge_result"]["ok"] = flag
    frozen["judge_status"]["state"] = "ok" if flag else "provider_error"
    frozen["judge_result"]["parsed"] = frozen["judge_result"]["parsed"] if flag else None
    with pytest.raises(ValueError, match="ok must be a boolean"):
        detector.detect(record, frozen_stages=frozen)
