import json
from types import SimpleNamespace

import pytest

from evolution.backends import MockDetectorBackend
from evolution.contracts import empty_registry
from evolution.engine import EvolutionEngine, _ArmRunner
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset, _scanner_candidate, _scripted_proposer


@pytest.mark.parametrize("drift", ["config", "scorer", "record", "backend"])
@pytest.mark.parametrize("when", ["before", "after", "cached", "completion"])
def test_actual_cycle_refuses_changed_inputs_before_observation_commit(tmp_path, monkeypatch, drift, when):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["proposer"]["mode"] = "offline"
    config["audit"]["enabled"] = False
    scorer = tmp_path / "scorer.txt"
    scorer.write_text("first", encoding="utf-8")
    backend = MockDetectorBackend()
    backend.detector = SimpleNamespace(config={"causal_audit": {"scorer_artifact": str(scorer)}})
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    calls = []
    committed = {}
    with EvolutionEngine(config, backend=backend, split=split, mini=True) as engine:
        def change():
            if drift == "config":
                engine.config["cycles"] += 1
            elif drift == "scorer":
                scorer.write_text("second", encoding="utf-8")
            elif drift == "backend":
                backend.base_predictions[cases[0]] = False
            else:
                path = tmp_path / "data" / cases[0]
                record = json.loads(path.read_text(encoding="utf-8"))
                record["question"] = "changed synthetic task"
                path.write_text(json.dumps(record), encoding="utf-8")

        evaluate = backend.evaluate

        def observe(selected, **kwargs):
            calls.extend(selected)
            rows = evaluate(selected, **kwargs)
            if when == "after":
                change()
            return rows

        monkeypatch.setattr(backend, "evaluate", observe)

        def run(cycle, **kwargs):
            if when == "before":
                change()
            _ArmRunner(engine, None).run([cases[0]], registry=empty_registry())
            if when in {"cached", "completion"}:
                arms = tmp_path / "work/checkpoints/cycle001/arms"
                committed.update({path: path.read_bytes() for path in arms.rglob("*.json")})
                change()
                if when == "cached":
                    _ArmRunner(engine, None).run([cases[0]], registry=empty_registry())
            return {"cycle": cycle, "state": "completed"}

        monkeypatch.setattr(engine, "_run_cycle", run)
        with pytest.raises(RuntimeError, match="cycle execution inputs changed"):
            engine.run_cycle(1)
        assert calls == ([] if when == "before" else [cases[0]])
        checkpoint = tmp_path / "work/checkpoints/cycle001"
        assert {path: path.read_bytes() for path in (checkpoint / "arms").rglob("*.json")} == committed
        assert "completion" not in json.loads((checkpoint / "checkpoint.json").read_text(encoding="utf-8"))
        assert not (tmp_path / "work/cycles/cycle001.json").exists()
        assert not any(row["event"] == "cycle_completed" for row in engine.ledger.read())


def test_drift_before_admission_does_not_publish_registry(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["proposer"]["mode"] = "offline"
    patrol = [case for case in cases if case.endswith(("012.json", "014.json"))]
    gain = [case for case in cases if case.endswith(("004.json", "006.json"))]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in patrol + gain},
        overrides={"IV-1-001": {case: True for case in gain}},
    )
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    with EvolutionEngine(config, backend=backend, split=split, mini=True,
                         proposer=_scripted_proposer(_scanner_candidate())) as engine:
        before = engine.registry.active_path.read_bytes()
        original = engine.registry.check_limits
        changed = []

        def check(entries, **kwargs):
            result = original(entries, **kwargs)
            engine.config["cycles"] += 1
            changed.append(True)
            return result

        monkeypatch.setattr(engine.registry, "check_limits", check)
        with pytest.raises(RuntimeError, match="cycle execution inputs changed"):
            engine.run_cycle(1)
        assert changed
        assert engine.registry.active_path.read_bytes() == before
        assert engine.registry.entries == []
