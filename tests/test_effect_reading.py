from copy import deepcopy
import json

import pytest

from detector.config import load_config
from detector.interventions import build_intervention_registry
from detector.runner import run_batch
from experiments.plan import case_list_digest, load_plan
from scripts.paired_endpoint import endpoint
from tests.test_experiments_contract import _result


@pytest.fixture
def readings(tmp_path, monkeypatch):
    cases = [f"A/{index}.json" for index in range(6)]
    data, runs = tmp_path / "data", tmp_path / "runs"
    for case in cases:
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question": "2+2", "history": [{"content": "4"}],
                                    "is_hallucination": False}), encoding="utf-8")
    measurement = tmp_path / "measurement.json"
    measurement.write_text(json.dumps({"schema_version": "detector.measurement_manifest.v1",
        "cases": cases, "cases_digest": case_list_digest(cases), "gold_positive": 0,
        "gold_negative": 6}), encoding="utf-8")
    plan = load_plan(measurement_path=measurement)
    config = load_config(require_credentials=False)
    predictions = {}
    class FakeDetector:
        def __init__(self, config):
            self.config = deepcopy(config)
        def detect(self, record, *, case_id, **kwargs):
            return _result(case_id, state="verified_error" if predictions[case_id] else "verified_clean")
    monkeypatch.setattr("detector.runner.Detector", FakeDetector)
    def measure(arm, values, changed=False):
        predictions.update(dict(zip(cases, values)))
        cfg = deepcopy(config)
        if changed:
            cfg["causal_audit"]["mode"] = "shadow"
        run_batch(dataset_root=data, runs_root=runs, run_id=arm, cases=cases,
                  config=cfg, interventions=build_intervention_registry([]), workers=1)
        return runs / arm
    return cases, data, runs, plan, measure


@pytest.mark.parametrize("entry", ["endpoint", "paired"])
@pytest.mark.parametrize("scenario", ["unstable_only", "unmeasured", "same_root", "four", "five", "all_unstable"])
def test_effect_requires_independent_stable_significant_evidence(readings, monkeypatch, capsys, entry, scenario):
    cases, data, runs, plan, measure = readings
    baseline = measure("g0", [True] * 6)
    changed_n = {"four": 4, "five": 5, "all_unstable": 6}.get(scenario, 1)
    deployed = measure("g1", [False] * changed_n + [True] * (6 - changed_n), changed=True)
    roll = None
    if scenario != "unmeasured":
        flipped_n = 6 if scenario == "all_unstable" else 1 if scenario == "unstable_only" else 0
        roll = baseline if scenario == "same_root" else measure("g0_roll2", [False] * flipped_n + [True] * (6 - flipped_n))
    capsys.readouterr()
    def read():
        if entry == "endpoint":
            return endpoint(baseline=baseline, baseline_roll=roll, deployed=deployed,
                            dataset_root=data, json_out=None)["verdict"]
        from experiments.__main__ import main
        monkeypatch.setattr("experiments.__main__.load_plan", lambda **kwargs: plan)
        args = ["paired", "--runs-root", str(runs), "--dataset-root", str(data)]
        if roll is not None:
            args += ["--baseline-roll", roll.name]
        assert main(args) == 0
        return json.loads(capsys.readouterr().out)
    if scenario == "same_root":
        with pytest.raises((ValueError, SystemExit)):
            read()
    else:
        payload = read()
        expected = None if scenario in {"unmeasured", "all_unstable"} else scenario == "five"
        assert payload["effect_established"] is expected
        if scenario == "unstable_only":
            assert payload["decisive_fixed"] == 0
        if scenario == "four":
            assert payload["mcnemar_one_sided_p"] == 0.0625
        if scenario == "five":
            assert payload["mcnemar_one_sided_p"] == 0.03125


@pytest.mark.parametrize("level", [True, None, "0.05", 0, 1, -1, float("nan"), float("inf")])
def test_effect_threshold_requires_a_valid_registered_number(readings, tmp_path, level):
    _, _, _, plan, _ = readings
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"criteria": {"effect": {"significance_level": level}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="significance_level"):
        load_plan(plan_path=path, measurement_path=plan.measurement.source)
