from copy import deepcopy
import json

import pytest

from evolution.tests.test_cycle_publication import _engine


@pytest.mark.parametrize("source", ["aggregate", "history"])
@pytest.mark.parametrize("mutation", ["nested", "append", "clear"])
def test_public_snapshots_cannot_change_committed_history_or_recovery(tmp_path, source, mutation):
    with _engine(tmp_path) as engine:
        first = engine.run_experiment(1)
        expected = deepcopy(first["cycles"])
        path = tmp_path / "work/checkpoints/cycle001/checkpoint.json"
        before = path.read_bytes()
        exposed = first["cycles"] if source == "aggregate" else engine.cycle_reports
        if mutation == "nested":
            exposed[0]["state"] = "caller_modified"
            exposed[0]["evidence"]["caller_modified"] = True
        elif mutation == "append":
            exposed.append({"cycle": 999, "state": "completed"})
        else:
            exposed.clear()
        assert engine.cycle_reports == expected
        resumed = engine.run_experiment(1, resume=True)
        assert resumed["cycles"] == expected
        assert resumed["cycles_completed"] == 1
        assert resumed["stopped_early"] is False
        assert path.read_bytes() == before
        assert json.loads(before)["completion"]["report"] == expected[0]


def test_public_history_cannot_be_replaced(tmp_path):
    with _engine(tmp_path) as engine:
        engine.run_experiment(1)
        expected = engine.cycle_reports
        with pytest.raises(AttributeError):
            engine.cycle_reports = [{"cycle": 999, "state": "completed"}]
        assert engine.cycle_reports == expected


@pytest.mark.parametrize("requested", [0, 1])
def test_aggregate_contains_only_requested_committed_cycles(tmp_path, requested):
    with _engine(tmp_path) as engine:
        engine.run_experiment(2)
        before = [(tmp_path / f"work/checkpoints/cycle{cycle:03d}/checkpoint.json").read_bytes()
                  for cycle in (1, 2)]
        aggregate = engine.run_experiment(requested, resume=True)
        assert [row["cycle"] for row in aggregate["cycles"]] == list(range(1, requested + 1))
        assert aggregate["cycles_completed"] == requested
        assert aggregate["cycles_requested"] == requested
        assert aggregate["stopped_early"] is False
        assert aggregate["stop_reason"] == ""
        assert [row["cycle"] for row in engine.cycle_reports] == [1, 2]
        assert [(tmp_path / f"work/checkpoints/cycle{cycle:03d}/checkpoint.json").read_bytes()
                for cycle in (1, 2)] == before


@pytest.mark.parametrize("terminal", ["blocked_evaluation_preflight", "frozen_after_defect_injection_audit"])
@pytest.mark.parametrize("source", ["aggregate", "history"])
def test_mutation_cannot_turn_actual_early_stop_into_completion(tmp_path, monkeypatch, terminal, source):
    with _engine(tmp_path) as engine:
        if terminal == "blocked_evaluation_preflight":
            monkeypatch.setattr("evolution.engine.sentinel_coverage",
                                lambda *args: {"evaluable": False, "reason": "controlled coverage failure"})
        else:
            monkeypatch.setattr(engine, "_audit", lambda *args: {"frozen": True, "passed": False})
        first = engine.run_experiment(3)
        path = tmp_path / "work/checkpoints/cycle001/checkpoint.json"
        before = path.read_bytes()
        exposed = first["cycles"] if source == "aggregate" else engine.cycle_reports
        exposed[0]["state"] = "completed"
        exposed.extend([{"cycle": 2, "state": "completed"}, {"cycle": 3, "state": "completed"}])
        resumed = engine.run_experiment(3, resume=True)
        assert resumed["cycles_completed"] == 0
        assert resumed["stopped_early"] is True
        assert resumed["stop_reason"] == terminal
        assert [row["cycle"] for row in resumed["cycles"]] == [1]
        assert resumed["cycles"][0]["state"] == terminal
        assert path.read_bytes() == before


def _validation_engine(tmp_path):
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_engine_smoke import _config, _make_dataset

    cases = _make_dataset(tmp_path / "data", n=24)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    split = EvolutionSplit(name="synthetic", pool=cases[:20], sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:20], validation=cases[20:], mini=True)
    return EvolutionEngine(config, split=split, mini=True, backend=MockDetectorBackend())


def test_unrequested_frozen_history_cannot_suppress_requested_validation(tmp_path, monkeypatch):
    with _validation_engine(tmp_path) as engine:
        monkeypatch.setattr(engine, "_audit", lambda cycle, *args: {"frozen": cycle == 2, "passed": cycle != 2})
        full = engine.run_experiment(2)
        assert full["mini_effect"]["status"] == "not_run"
        calls = []

        def effect():
            calls.append("controlled validation")
            return {"status": "controlled", "passed": True}

        monkeypatch.setattr(engine, "_evaluate_mini_effect", effect)
        prefix = engine.run_experiment(1, resume=True)
        assert prefix["cycles_completed"] == 1
        assert prefix["mini_effect"] == {"status": "controlled", "passed": True}
        assert calls == ["controlled validation"]
        assert [row["state"] for row in engine.cycle_reports] == ["completed", "frozen_after_defect_injection_audit"]


def test_zero_cycle_request_does_not_measure_validation(tmp_path, monkeypatch):
    with _validation_engine(tmp_path) as engine:
        def forbidden():
            pytest.fail("zero cycle request measured validation")

        monkeypatch.setattr(engine, "_evaluate_mini_effect", forbidden)
        report = engine.run_experiment(0)
        assert report["cycles"] == []
        assert report["mini_effect"]["status"] == "not_run"
        assert report["mini_effect"]["passed"] is None
