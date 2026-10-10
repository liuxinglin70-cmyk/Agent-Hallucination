from copy import deepcopy

import pytest

from detector.api import Detector
from detector.tests.test_api import CONFIG
from detector.tests.test_causal_audit import record
from evolution.backends import LiveDetectorBackend


def setup_stages(monkeypatch):
    calls = {"judge": 0, "tracer": 0, "defense": 0}

    def judge(**kwargs):
        calls["judge"] += 1
        return {"ok": True, "parsed": {
            "is_hallucination": True, "hallucination_step": 1,
            "hallucination_reason": "wrong arithmetic", "confidence": "high",
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
            "delivered_result": "5", "correct_result": "4", "basis": "derivation",
            "root_cause": {"step": 1, "quote": "5", "referent": "4"},
            "evidence_refs": ["event_001"],
        }}, {"state": "ok"}

    def tracer(**kwargs):
        calls["tracer"] += 1
        return {"ok": True, "parsed": {
            "step": 1, "quote": "5", "referent": "4", "reason": "wrong arithmetic",
            "evidence_refs": ["event_001"],
        }}, {"state": "ok"}

    def defense(**kwargs):
        calls["defense"] += 1
        return {"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 1,
            "reason": "wrong arithmetic", "evidence_refs": ["event_001"],
        }}, {"state": "ok"}

    monkeypatch.setattr("detector.api.run_judge", judge)
    monkeypatch.setattr("detector.hearing.run_step_tracer", tracer)
    monkeypatch.setattr("detector.hearing.run_defense", defense)
    return calls


@pytest.mark.parametrize("change", ["missing_identity", "source", "judge_route", "judge_prompt"])
def test_judge_replay_requires_current_inputs(monkeypatch, change):
    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record()), set())
    cfg = deepcopy(CONFIG)
    if change == "missing_identity":
        frozen.pop("replay_inputs", None)
    elif change == "source":
        monkeypatch.setattr("detector.api.source_identity", lambda *args: "source-drift")
    elif change == "judge_route":
        cfg["judge_route"]["model"] = "changed"
    else:
        monkeypatch.setattr("detector.judge.JUDGE_SYSTEM", "changed prompt")
    with pytest.raises(ValueError, match="frozen.*identity"):
        Detector(cfg, require_credentials=False).detect(record(), frozen_stages=frozen)
    assert calls == {"judge": 1, "tracer": 1, "defense": 1}


@pytest.mark.parametrize("change,stage", [
    ("defense_route", "defense"), ("defense_prompt", "defense"),
    ("tracer_prompt", "tracer"),
])
def test_downstream_input_drift_reruns_affected_stage(monkeypatch, change, stage):
    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record()), set())
    cfg = deepcopy(CONFIG)
    if change == "defense_route":
        cfg["defense_route"]["model"] = "changed"
    elif change == "defense_prompt":
        monkeypatch.setattr("detector.defense.DEFENSE_SYSTEM", "changed prompt")
    else:
        monkeypatch.setattr("detector.step_tracer.TRACER_SYSTEM", "changed prompt")
    result = Detector(cfg, require_credentials=False).detect(record(), frozen_stages=frozen)
    assert result["state"] == "verified_error"
    expected = {"judge": 1, "tracer": 1, "defense": 1}
    expected[stage] = 2
    assert calls == expected


def test_identical_inputs_reuse_all_stages_without_mutating_envelope(monkeypatch):
    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record()), set())
    original = deepcopy(frozen)
    assert detector.detect(record(), frozen_stages=frozen)["state"] == "verified_error"
    assert calls == {"judge": 1, "tracer": 1, "defense": 1}
    assert frozen == original


@pytest.mark.parametrize("malformation", ["seal", "missing_stage", "extra_field", "hearings"])
def test_malformed_replay_inputs_are_rejected_before_requests(monkeypatch, malformation):
    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = LiveDetectorBackend._frozen_stages(detector.detect(record()), set())
    if malformation == "seal":
        frozen["replay_inputs"]["digest"] = "0" * 64
    elif malformation == "missing_stage":
        frozen["replay_inputs"]["stage_digests"].pop("defense")
    elif malformation == "extra_field":
        frozen["replay_inputs"]["unbound"] = True
    else:
        frozen["hearings"] = None
    with pytest.raises(ValueError, match="frozen.*(identity|hearings)"):
        detector.detect(record(), frozen_stages=frozen)
    assert calls == {"judge": 1, "tracer": 1, "defense": 1}


def setup_recall_stages(monkeypatch):
    from detector.tests.test_evidence_flow import (
        CONFIG as recall_config, LISETTE_RECORD, _frozen_recall,
    )

    envelope = _frozen_recall()
    from detector.recaller import assemble_upgraded_candidates
    envelope["upgrade_result"]["upgrades"][0]["coverage"] = "answered"
    upgraded = assemble_upgraded_candidates(
        envelope["recall_result"]["candidates"], envelope["upgrade_result"]["upgrades"],
    )
    envelope["upgrade_result"]["upgraded_candidates"] = upgraded
    envelope["upgrade_result"]["upgraded_candidate"] = upgraded[0]
    calls = {"judge": 0, "scanner": 0, "upgrade": 0, "tracer": 0, "defense": 0}

    def stage(name, result, status):
        def run(**kwargs):
            calls[name] += 1
            return deepcopy(result), deepcopy(status)
        return run

    monkeypatch.setattr("detector.api.run_judge", stage(
        "judge", envelope["judge_result"], envelope["judge_status"]))
    monkeypatch.setattr("detector.api.run_recall_scan", stage(
        "scanner", envelope["recall_result"], envelope["recall_status"]))
    monkeypatch.setattr("detector.api.run_causal_upgrade", stage(
        "upgrade", envelope["upgrade_result"], envelope["upgrade_status"]))
    monkeypatch.setattr("detector.hearing.run_step_tracer", stage("tracer", {
        "ok": True, "parsed": {"step": 2, "quote": "76 tons", "referent": "116 tons",
                                "evidence_refs": ["event_002"], "reason": "wrong value"},
    }, {"state": "ok"}))
    monkeypatch.setattr("detector.hearing.run_defense", stage("defense", {
        "ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 2,
                                "evidence_refs": ["event_002"], "reason": "wrong value"},
    }, {"state": "ok"}))
    return calls, recall_config, LISETTE_RECORD


@pytest.mark.parametrize("change", ["recall_route", "upgrade_prompt"])
def test_recall_input_drift_invalidates_dependent_hearings(monkeypatch, change):
    calls, recall_config, recall_record = setup_recall_stages(monkeypatch)
    detector = Detector(recall_config, require_credentials=False)
    baseline = detector.detect(recall_record)
    assert baseline["state"] == "verified_error"
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    cfg = deepcopy(recall_config)
    if change == "recall_route":
        cfg["recall_route"]["model"] = "changed"
    else:
        monkeypatch.setattr("detector.recaller.UPGRADE_SYSTEM", "changed prompt")
    result = Detector(cfg, require_credentials=False).detect(recall_record, frozen_stages=frozen)
    assert result["state"] == "verified_error"
    assert calls == {"judge": 1, "scanner": 2 if change == "recall_route" else 1,
                     "upgrade": 2, "tracer": 2, "defense": 2}


@pytest.mark.parametrize("hook,stage", [
    ("judge.system", "judge"), ("tracer.system", "tracer"),
    ("defense.system", "defense"),
])
def test_removing_a_stage_intervention_cannot_reuse_its_result(monkeypatch, hook, stage):
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import _candidate

    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    registry = build_intervention_registry([_candidate(hook=hook)])
    baseline = detector.detect(record(), interventions=registry)
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    if stage == "judge":
        with pytest.raises(ValueError, match="frozen.*identity"):
            detector.detect(record(), frozen_stages=frozen)
        assert calls == {"judge": 1, "tracer": 1, "defense": 1}
    else:
        assert detector.detect(record(), frozen_stages=frozen)["state"] == "verified_error"
        expected = {"judge": 1, "tracer": 1, "defense": 1}
        expected[stage] = 2
        assert calls == expected


@pytest.mark.parametrize("hook", ["scanner.system", "upgrade.system"])
def test_removing_a_recall_intervention_invalidates_dependent_hearings(monkeypatch, hook):
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import _candidate

    calls, cfg, recall_record = setup_recall_stages(monkeypatch)
    detector = Detector(cfg, require_credentials=False)
    registry = build_intervention_registry([_candidate(hook=hook)])
    baseline = detector.detect(recall_record, interventions=registry)
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    assert detector.detect(recall_record, frozen_stages=frozen)["state"] == "verified_error"
    assert calls == {"judge": 1, "scanner": 2 if hook == "scanner.system" else 1,
                     "upgrade": 2, "tracer": 2, "defense": 2}


@pytest.mark.parametrize("hook", ["judge.system", "tracer.system", "defense.system"])
def test_changed_active_intervention_is_checked_before_replay_invalidation(monkeypatch, hook):
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import _candidate

    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    entry = _candidate(hook=hook)
    baseline = detector.detect(record(), interventions=build_intervention_registry([entry]))
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    changed = deepcopy(entry)
    changed["body"]["directive"] = "Require an exact arithmetic check."
    with pytest.raises(ValueError, match="mask"):
        detector.detect(record(), frozen_stages=frozen,
                        interventions=build_intervention_registry([changed]))
    assert calls == {"judge": 1, "tracer": 1, "defense": 1}


@pytest.mark.parametrize("hook,stage", [("tracer.system", "tracer"), ("defense.system", "defense")])
def test_backend_excludes_intervened_stage_and_reuses_unaffected_inputs(monkeypatch, hook, stage):
    from detector.interventions import build_intervention_registry
    from detector.tests.test_interventions import _candidate

    calls = setup_stages(monkeypatch)
    detector = Detector(CONFIG, require_credentials=False)
    baseline = detector.detect(record())
    frozen = LiveDetectorBackend._frozen_stages(baseline, {hook})
    registry = build_intervention_registry([_candidate(hook=hook)])
    assert detector.detect(record(), frozen_stages=frozen, interventions=registry)["state"] == "verified_error"
    expected = {"judge": 1, "tracer": 1, "defense": 1}
    expected[stage] = 2
    assert calls == expected
