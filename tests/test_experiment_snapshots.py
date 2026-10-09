from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from threading import Thread

import pytest

from detector.config import load_config
from detector.evaluation import evaluate_run
from detector.interventions import build_intervention_registry
from detector.runner import run_batch
from detector.runtime import exclusive_writer
from experiments.plan import load_plan
from experiments.runner import paired_sample
from tests.test_experiments_contract import _result
from tests.test_measurement_manifest import write, manifest


@pytest.fixture
def runs_fixture(tmp_path, monkeypatch):
    measurement = write(tmp_path, manifest())
    plan = load_plan(measurement_path=measurement)
    data, runs = tmp_path / "data", tmp_path / "runs"
    for case in plan.measurement.cases:
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question": "2+2", "history": [{"role": "assistant", "content": "4"}],
                                    "is_hallucination": False}), encoding="utf-8")
    config = load_config(require_credentials=False)
    class FakeDetector:
        def __init__(self, config):
            self.config = deepcopy(config)
        def detect(self, record, *, case_id, **kwargs):
            return _result(case_id)
    monkeypatch.setattr("detector.runner.Detector", FakeDetector)
    def measure(arm, config_override=None, cases=None):
        return run_batch(dataset_root=data, runs_root=runs, run_id=arm,
                         cases=plan.measurement.cases if cases is None else cases,
                         config=config if config_override is None else config_override,
                         interventions=build_intervention_registry([]), workers=1)
    return plan, data, runs, config, measure


def paired_payload(monkeypatch, capsys, plan, runs, data):
    from experiments.__main__ import main
    monkeypatch.setattr("experiments.__main__.load_plan", lambda **kwargs: plan)
    assert main(["paired", "--arm", "g0", "--arm", "g0_roll2", "--runs-root", str(runs),
                 "--dataset-root", str(data)]) == 0
    return json.loads(capsys.readouterr().out)


def test_pair_classification_uses_actual_configuration(runs_fixture, monkeypatch, capsys):
    plan, data, runs, config, measure = runs_fixture
    measure("g0")
    changed = deepcopy(config)
    changed["causal_audit"]["mode"] = "tracer_context"
    measure("g0_roll2", changed)
    payload = paired_payload(monkeypatch, capsys, plan, runs, data)
    assert payload["reading"] == "effect", "identical registry selectors hid an actual policy change"


def test_edited_plan_cannot_turn_actual_identical_runs_into_effect(runs_fixture, monkeypatch, capsys):
    plan, data, runs, config, measure = runs_fixture
    measure("g0")
    measure("g0_roll2")
    plan = replace(plan, arms={**plan.arms, "g0_roll2": {"role": "noise_floor", "registry": "active"}})
    payload = paired_payload(monkeypatch, capsys, plan, runs, data)
    assert payload["reading"] == "noise_floor", "a current plan selector replaced recorded execution identity"


def test_pair_sample_rejects_a_writer_on_either_run(runs_fixture):
    plan, data, runs, config, measure = runs_fixture
    measure("g0")
    measure("g0_roll2")
    errors = []
    def read():
        try:
            paired_sample(plan, runs, ["g0", "g0_roll2"])
        except RuntimeError as exc:
            errors.append(exc)
    with exclusive_writer(runs / "g0_roll2"):
        thread = Thread(target=read)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert errors, "standalone paired_sample read an actively owned run"


def test_paired_scoring_keeps_both_run_leases(runs_fixture, monkeypatch, capsys):
    plan, data, runs, config, measure = runs_fixture
    measure("g0")
    measure("g0_roll2")
    original = evaluate_run
    def score(**kwargs):
        acquired = []
        def contender():
            for arm in ("g0", "g0_roll2"):
                try:
                    with exclusive_writer(runs / arm):
                        acquired.append(arm)
                except RuntimeError:
                    pass
        thread = Thread(target=contender)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not acquired, "sampling released a run before the paired scoring finished"
        return original(**kwargs)
    monkeypatch.setattr("detector.evaluation.evaluate_run", score)
    paired_payload(monkeypatch, capsys, plan, runs, data)




def test_artifact_content_is_part_of_actual_pair_identity(runs_fixture, monkeypatch, capsys, tmp_path):
    plan, data, runs, config, measure = runs_fixture
    artifact = tmp_path / "scorer.bin"
    artifact.write_bytes(b"first")
    config["causal_audit"]["scorer_artifact"] = str(artifact)
    measure("g0")
    artifact.write_bytes(b"second")
    measure("g0_roll2")
    assert paired_payload(monkeypatch, capsys, plan, runs, data)["reading"] == "effect"


def test_snapshot_captures_absence_without_creating_a_run(tmp_path):
    from experiments.snapshot import run_snapshot
    root = tmp_path / "absent"
    with run_snapshot({"g0": root}) as snapshot:
        assert snapshot.roots["g0"] is None
        assert snapshot.rows["g0"] == []
        assert not root.exists()
    assert not root.exists()


@pytest.mark.parametrize("damage", ["manifest_missing", "unsupported", "execution_identity", "commit_input"])
def test_snapshot_rejects_unbound_run_evidence(runs_fixture, damage):
    from experiments.snapshot import run_snapshot
    from detector.identity import digest
    plan, data, runs, config, measure = runs_fixture
    measure("g0")
    root = runs / "g0"
    manifest_path = root / "run.json"
    if damage == "manifest_missing":
        manifest_path.unlink()
    elif damage == "unsupported":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "detector.run_manifest.v1"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        from detector.runner import safe_case_id
        path = root / "cases" / safe_case_id(plan.measurement.cases[0]) / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        if damage == "execution_identity":
            result["provenance"]["execution_identity"]["source"] = "different-source"
        else:
            commit = result["case_commit"]
            commit["input_record"]["question"] = "another task"
            commit["input_digest"] = digest(commit["input_record"])
        commit = result["case_commit"]
        commit["result_digest"] = digest({key: value for key, value in result.items() if key != "case_commit"})
        commit["commit_digest"] = digest({key: value for key, value in commit.items() if key != "commit_digest"})
        path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError):
        with run_snapshot({"g0": root}):
            pytest.fail("unbound run evidence was read")
