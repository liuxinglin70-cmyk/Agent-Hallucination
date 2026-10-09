import json

import pytest

from detector.runner import record_case
from detector.tests.test_runner import _envelope
from experiments.runner import _load_rows


def commit(tmp_path, **kwargs):
    return record_case(runs_root=tmp_path, run_id="r", case_id="a.json",
        input_record={"question": "q", "history": [{"content": "4"}]},
        result=_envelope("a.json"), elapsed_seconds=kwargs.pop("elapsed_seconds", 4),
        attempt_id=kwargs.pop("attempt_id", "001"), **kwargs)


def test_result_and_timing_have_one_atomic_commit(tmp_path):
    directory = commit(tmp_path)
    payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert payload["case_commit"]["elapsed_seconds"] == 4
    assert payload["case_commit"]["attempt_id"] == "001"
    assert payload["case_commit"]["input_record"]["question"] == "q"
    assert not (directory / "elapsed_seconds.json").exists()
    assert not (directory / "input.json").exists()
    assert (directory / "attempts/001/result.json").read_bytes() == (directory / "result.json").read_bytes()


def test_completed_attempt_cannot_be_overwritten(tmp_path):
    directory = commit(tmp_path)
    original = (directory / "result.json").read_bytes()
    with pytest.raises(FileExistsError):
        commit(tmp_path, elapsed_seconds=9)
    assert (directory / "result.json").read_bytes() == original


def test_interrupted_canonical_replace_keeps_previous_result_and_timing(tmp_path, monkeypatch):
    directory = commit(tmp_path)
    original = (directory / "result.json").read_bytes()
    from detector.runner import _write_json
    def fail_replace(path, payload):
        if path == directory / "result.json":
            raise OSError("canonical replacement interrupted")
        _write_json(path, payload)
    monkeypatch.setattr("detector.runner._write_json", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        commit(tmp_path, attempt_id="002", elapsed_seconds=9)
    assert (directory / "result.json").read_bytes() == original
    assert _load_rows(tmp_path, "r", ["a.json"])[0]["elapsed_seconds"] == 4


def test_report_does_not_pair_result_with_unrelated_timing(tmp_path):
    directory = commit(tmp_path)
    (directory / "elapsed_seconds.json").write_text('{"elapsed_seconds": 999}', encoding="utf-8")
    assert _load_rows(tmp_path, "r", ["a.json"])[0]["elapsed_seconds"] == 4


def test_tampered_case_commit_is_refused_by_current_reader(tmp_path):
    directory = commit(tmp_path)
    path = directory / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = "verified_error"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        _load_rows(tmp_path, "r", ["a.json"])


def test_stage_reads_and_evaluates_under_the_same_writer_lease(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from detector.runtime import exclusive_writer
    from experiments.plan import load_plan
    from experiments.runner import run_measurement_stage
    plan = load_plan()
    case = plan.measurement.cases[0]
    calls = []
    run_dir = tmp_path / "runs/r"
    def other_writer():
        with exclusive_writer(run_dir):
            return "acquired"
    def assert_lease(stage):
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(RuntimeError, match="writer"):
                pool.submit(other_writer).result()
        calls.append(stage)
    def read(*args):
        assert_lease("read")
        return [{"case": case, "result": _envelope(case), "elapsed_seconds": 1}]
    def evaluate(**kwargs):
        assert_lease("evaluate")
        return {}
    monkeypatch.setattr("experiments.runner.run_batch", lambda **kwargs: {})
    monkeypatch.setattr("experiments.runner._load_rows", read)
    monkeypatch.setattr("experiments.runner.evaluate_run", evaluate)
    run_measurement_stage(plan=plan, stage="baseline", arm="g0", cases=[case],
        runs_root=tmp_path / "runs", run_id="r", dataset_root=tmp_path / "data")
    assert calls == ["read", "evaluate"]


def test_public_case_writer_cannot_bypass_the_run_lease(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from detector.runtime import exclusive_writer
    commit(tmp_path)
    with exclusive_writer(tmp_path / "r"):
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(RuntimeError, match="writer"):
                pool.submit(commit, tmp_path, attempt_id="002").result()


def test_current_reader_rejects_a_removed_commit(tmp_path):
    directory = commit(tmp_path)
    path = directory / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result.pop("case_commit")
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="commit"):
        _load_rows(tmp_path, "r", ["a.json"])


def test_evaluator_obeys_the_run_lease(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from detector.evaluation import evaluate_run
    from detector.runtime import exclusive_writer
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "a.json").write_text(json.dumps({"question": "q", "history": [],
                                                  "is_hallucination": False}), encoding="utf-8")
    commit(tmp_path)
    with exclusive_writer(tmp_path / "r"):
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(RuntimeError, match="writer"):
                pool.submit(evaluate_run, runs_root=tmp_path, run_id="r",
                            dataset_root=directory, cases=["a.json"]).result()
