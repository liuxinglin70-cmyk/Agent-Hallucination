import json
from pathlib import Path
from threading import Thread

import pytest

from detector.runner import safe_case_id
from detector.runtime import exclusive_writer
from tests.test_effect_reading import readings


def read(name, root, cases):
    if name == "status":
        from unittest.mock import patch
        from scripts import experiment_status
        with patch.object(experiment_status, "TREES", [(root.name, root, len(cases))]), patch.object(experiment_status, "ROOT", root.parent.parent):
            return experiment_status.main()
    if name == "roll":
        from scripts.measure_noise_floor import read_roll
        return read_roll(root, case_ids=cases)
    if name == "prefix":
        from scripts.freeze_measured_prefix import common_prefix
        return common_prefix(baselines=[root], order=cases)
    if name == "metrics":
        from scripts.real_path_check import read_metrics
        return read_metrics(root.parent, root.name, cases)
    from scripts.run_baseline_693 import timing_report, usage_report
    return {"timing": timing_report, "usage": usage_report}[name](
        runs_root=root.parent, run_id=root.name, cases=cases)


@pytest.mark.parametrize("reader", ["roll", "prefix", "metrics", "timing", "usage", "status"])
@pytest.mark.parametrize("boundary", ["owned", "foreign_result"])
def test_script_readers_require_owned_bound_snapshots(readings, reader, boundary):
    cases, _, _, _, measure = readings
    root = measure("g0", [False] * 6)
    if boundary == "foreign_result":
        foreign = measure("g1", [True] * 6, changed=True)
        case_path = Path("cases") / safe_case_id(cases[0]) / "result.json"
        (root / case_path).write_bytes((foreign / case_path).read_bytes())
        with pytest.raises(ValueError):
            read(reader, root, cases)
    else:
        errors = []
        def attempt():
            try:
                read(reader, root, cases)
            except RuntimeError as exc:
                errors.append(exc)
        with exclusive_writer(root):
            thread = Thread(target=attempt)
            thread.start()
            thread.join(5)
            assert not thread.is_alive()
        assert errors, f"{reader} bypassed an independently owned run"


def test_prefix_stops_at_a_failed_result(readings):
    from detector.identity import digest
    from scripts.freeze_measured_prefix import completed_prefix
    cases, _, _, _, measure = readings
    root = measure("g0", [False] * 6)
    path = root / "cases" / safe_case_id(cases[1]) / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result.update(state="unresolved", occurrence_state="unresolved", runtime_valid=False)
    commit = result["case_commit"]
    commit["result_digest"] = digest({key: value for key, value in result.items() if key != "case_commit"})
    commit["commit_digest"] = digest({key: value for key, value in commit.items() if key != "commit_digest"})
    path.write_text(json.dumps(result), encoding="utf-8")
    assert completed_prefix(baseline=root, order=cases) == (cases[:1], cases[1:2])


def test_noise_report_rejects_repeated_roots(readings):
    from scripts.measure_noise_floor import report_rolls
    _, _, _, _, measure = readings
    first = measure("g0", [False] * 6)
    second = measure("g0_roll2", [False] * 6)
    with pytest.raises(ValueError, match="independent"):
        report_rolls([first, second, second])


def test_noise_report_rejects_different_execution_inputs(readings):
    from scripts.measure_noise_floor import report_rolls
    _, _, _, _, measure = readings
    first = measure("g0", [False] * 6)
    second = measure("g1", [False] * 6, changed=True)
    with pytest.raises(ValueError, match="different recorded"):
        report_rolls([first, second])


def test_noise_empty_selection_is_not_all_cases(readings):
    from scripts.measure_noise_floor import read_roll
    _, _, _, _, measure = readings
    assert read_roll(measure("g0", [False] * 6), case_ids=[]) == []


def test_metrics_hold_one_snapshot_through_every_case(readings, monkeypatch):
    from scripts import real_path_check
    cases, _, _, _, measure = readings
    root = measure("g0", [False] * 6)
    original = real_path_check._case_metrics
    def format_case(case, result):
        acquired = []
        def compete():
            try:
                with exclusive_writer(root):
                    acquired.append(True)
            except RuntimeError:
                pass
        thread = Thread(target=compete)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert not acquired
        return original(case, result)
    monkeypatch.setattr(real_path_check, "_case_metrics", format_case)
    assert len(real_path_check.read_metrics(root.parent, root.name, cases)) == 6
