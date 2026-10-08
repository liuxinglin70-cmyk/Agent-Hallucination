from __future__ import annotations

import json

import pytest

from detector.result import RESULT_SCHEMA
from detector.runner import run_batch, safe_case_id


class _FakeDetector:
    def __init__(self, config):
        self.config = config
        self.calls = 0

    def detect(self, record, *, case_id, interventions=None):
        self.calls += 1
        return {
            "schema_version": RESULT_SCHEMA,
            "case_id": case_id,
            "state": "verified_clean",
            "decision_authority": "authoritative",
            "runtime_valid": True,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": False,
            "occurrence_state": "verified_clean",
            "proof": {},
            "stage_status": {},
            "providers": {},
            "authority_failure_reasons": [],
            "evidence_health": {},
            "provenance": {},
        }


class _CountingDetector(_FakeDetector):
    """Records every construction so a test can prove *who* ran the case."""

    instances: list["_CountingDetector"] = []

    def __init__(self, config):
        super().__init__(config)
        _CountingDetector.instances.append(self)


def _write_dataset(root, case_ids=("a.json",)):
    root.mkdir(parents=True, exist_ok=True)
    for case_id in case_ids:
        (root / case_id).write_text(
            json.dumps({"question": "q", "history": [{"content": "a"}]}),
            encoding="utf-8",
        )
    return root


def _envelope(case_id, *, state="verified_clean", authority="authoritative"):
    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": state,
        "decision_authority": authority,
        "runtime_valid": True,
        "responsible_event_id": None,
        "responsible_step": None,
        "incident_occurred": state == "verified_error",
        "occurrence_state": state,
        "proof": {},
        "stage_status": {},
        "providers": {},
        "authority_failure_reasons": (
            [{"error_type": "provider_error", "message": "outage"}]
            if state == "unresolved" else []
        ),
        "evidence_health": {},
        "provenance": {},
    }


def test_safe_case_id():
    assert safe_case_id("BFCL/001.json") == "BFCL__001.json"


def test_run_batch_rejects_case_path_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    with pytest.raises(ValueError, match="collide"):
        run_batch(
            dataset_root=tmp_path,
            cases=["A/B.json", "A__B.json"],
            run_id="r",
            runs_root=tmp_path / "runs",
            config={},
            workers=1,
        )


def test_run_batch_writes_run_tree(tmp_path, monkeypatch):
    root = tmp_path / "data"
    case_dir = root / "BFCL"
    case_dir.mkdir(parents=True)
    (case_dir / "001.json").write_text(
        json.dumps({
            "question": "q",
            "history": [{"step": 1, "role": "assistant", "content": "a"}],
            "is_hallucination": "false",
            "true_answer": "a",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    summary = run_batch(
        dataset_root=root,
        cases=["BFCL/001.json"],
        run_id="r",
        runs_root=tmp_path / "runs",
        config={},
        workers=1,
    )
    assert summary["completed"] == 1
    result = json.loads(
        (tmp_path / "runs" / "r" / "cases" / "BFCL__001.json" / "result.json")
        .read_text(encoding="utf-8")
    )
    assert result["state"] == "verified_clean"
    input_record = json.loads(
        (tmp_path / "runs" / "r" / "cases" / "BFCL__001.json" / "input.json")
        .read_text(encoding="utf-8")
    )
    assert set(input_record) == {"question", "history"}


def test_run_batch_rejects_manifest_drift(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.json").write_text(
        json.dumps({"question": "q", "history": [{"content": "a"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    run_batch(
        dataset_root=root, cases=["a.json"], run_id="r",
        runs_root=tmp_path / "runs", config={"version": 1}, workers=1,
    )
    cached = (
        tmp_path / "runs" / "r" / "cases" / "a.json" / "result.json"
    )
    assert cached.exists(), "the refusal must be tested with results on disk"
    with pytest.raises(ValueError, match="different manifest"):
        run_batch(
            dataset_root=root, cases=["a.json"], run_id="r",
            runs_root=tmp_path / "runs", config={"version": 2}, workers=1,
        )


def test_run_batch_refuses_results_whose_manifest_is_missing(tmp_path, monkeypatch):
    """A result file without ``run.json`` is not resumable state.

    The manifest is the only record of *which* configuration produced the rows
    in a run tree. Deleting it used to turn the run directory into one whose
    cached results were silently reused under whatever config was passed next,
    and the freshly written manifest then claimed that config for rows it had
    never produced. Refusing is the only answer that keeps a measurement
    attributable to the code that made it.
    """
    runs_root = tmp_path / "runs"
    results = runs_root / "r" / "cases" / safe_case_id("a.json")
    results.mkdir(parents=True)
    (results / "result.json").write_text(
        json.dumps(_envelope("a.json")), encoding="utf-8",
    )
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    with pytest.raises(ValueError, match="no run.json manifest"):
        run_batch(
            dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
            run_id="r", runs_root=runs_root, config={}, workers=1,
        )
    assert not (runs_root / "r" / "run.json").exists()


def test_run_batch_refuses_a_foreign_envelope(tmp_path, monkeypatch):
    """A cached row from another schema is not this runner's measurement.

    The manifest can match while the row inside it came from another tool
    entirely (a hand-written file, another detector build's envelope), so the
    resume path answers "is this ours?" as well as "is the config the same?".
    """
    runs_root = tmp_path / "runs"
    data = _write_dataset(tmp_path / "data")
    monkeypatch.setattr("detector.runner.Detector", _CountingDetector)
    _CountingDetector.instances.clear()
    kwargs = dict(
        dataset_root=data, cases=["a.json"], run_id="r",
        runs_root=runs_root, config={}, workers=1,
    )
    run_batch(**kwargs)
    (runs_root / "r" / "cases" / "a.json" / "result.json").write_text(
        json.dumps({"schema_version": "someone.else.v1", "case_id": "a.json"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="detector.detection_result.v1"):
        run_batch(**kwargs)


def test_run_batch_resume_skips_completed_cases(tmp_path, monkeypatch):
    monkeypatch.setattr("detector.runner.Detector", _CountingDetector)
    _CountingDetector.instances.clear()
    kwargs = dict(
        dataset_root=_write_dataset(tmp_path / "data", ("a.json", "b.json")),
        cases=["a.json", "b.json"],
        run_id="r",
        runs_root=tmp_path / "runs",
        config={},
        workers=1,
    )
    first = run_batch(**kwargs)
    assert first["completed"] == 2
    assert _CountingDetector.instances[-1].calls == 2
    second = run_batch(**kwargs)
    assert second["completed"] == 2
    assert _CountingDetector.instances[-1].calls == 0, (
        "a resume inside a verified manifest must not re-run finished cases"
    )


def test_run_batch_resume_retries_unresolved_cases_only_when_asked(
    tmp_path, monkeypatch,
):
    """An unresolved row is an outage, not a verdict -- but retrying is opt-in."""
    class _OutageDetector(_CountingDetector):
        def detect(self, record, *, case_id, interventions=None):
            self.calls += 1
            return _envelope(case_id, state="unresolved", authority=None)

    runs_root = tmp_path / "runs"
    data = _write_dataset(tmp_path / "data")
    monkeypatch.setattr("detector.runner.Detector", _OutageDetector)
    _CountingDetector.instances.clear()
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1,
    )
    with pytest.raises(ValueError, match="different manifest"):
        # The manifest is what pins the retry to the same configuration.
        run_batch(
            dataset_root=data, cases=["a.json"], run_id="r",
            runs_root=runs_root, config={"other": 1}, workers=1,
        )
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1,
    )
    assert _CountingDetector.instances[-1].calls == 0, (
        "by default an unresolved row is reused: the outage is a reading"
    )
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1, retry_unresolved=True,
    )
    assert _CountingDetector.instances[-1].calls == 3, (
        "retry_unresolved must send the case to the detector again, and because"
        " the cached row names a provider_error the whole-detection transport"
        " retry runs too (3 attempts)"
    )
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1,
    )
    cached = json.loads(
        (runs_root / "r" / "cases" / "a.json" / "result.json")
        .read_text(encoding="utf-8")
    )
    assert cached["state"] == "unresolved", (
        "the retry re-runs the case; the replacement verdict is the detector's,"
        " so a still-failing provider keeps writing unresolved rows"
    )


def test_run_batch_retry_unresolved_replaces_the_row(tmp_path, monkeypatch):
    """After a retry under a working provider the row is the new verdict."""
    class _FlakyDetector(_CountingDetector):
        def detect(self, record, *, case_id, interventions=None):
            self.calls += 1
            if self.calls == 1:
                return _envelope(case_id, state="unresolved", authority=None)
            return _envelope(case_id)

    runs_root = tmp_path / "runs"
    data = _write_dataset(tmp_path / "data")
    monkeypatch.setattr("detector.runner.Detector", _FlakyDetector)
    _CountingDetector.instances.clear()
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1,
    )
    run_batch(
        dataset_root=data, cases=["a.json"], run_id="r", runs_root=runs_root,
        config={}, workers=1, retry_unresolved=True,
    )
    cached = json.loads(
        (runs_root / "r" / "cases" / "a.json" / "result.json")
        .read_text(encoding="utf-8")
    )
    assert cached["state"] == "verified_clean"
    assert cached["decision_authority"] == "authoritative"


@pytest.mark.parametrize("changed", ["source", "record"])
def test_resume_refuses_changed_execution_inputs(tmp_path, monkeypatch, changed):
    monkeypatch.setattr("detector.runner.Detector", _CountingDetector)
    data = _write_dataset(tmp_path / "data")
    kwargs = dict(dataset_root=data, cases=["a.json"], run_id="r",
                  runs_root=tmp_path / "runs", config={"version": 1}, workers=1)
    monkeypatch.setattr("detector.runner.source_identity", lambda *args: "source-one")
    run_batch(**kwargs)
    manifest = (tmp_path / "runs/r/run.json").read_bytes()
    result = (tmp_path / "runs/r/cases/a.json/result.json").read_bytes()
    if changed == "source":
        monkeypatch.setattr("detector.runner.source_identity", lambda *args: "source-two")
    else:
        (data / "a.json").write_text(json.dumps({"question": "different task",
                                               "history": [{"content": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="different manifest"):
        run_batch(**kwargs)
    assert (tmp_path / "runs/r/run.json").read_bytes() == manifest
    assert (tmp_path / "runs/r/cases/a.json/result.json").read_bytes() == result


def test_resume_refuses_a_cached_row_without_execution_identity(tmp_path, monkeypatch):
    monkeypatch.setattr("detector.runner.Detector", _FakeDetector)
    kwargs = dict(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                  run_id="r", runs_root=tmp_path / "runs", config={"version": 1}, workers=1)
    run_batch(**kwargs)
    cached = tmp_path / "runs/r/cases/a.json/result.json"
    cached.write_text(json.dumps(_envelope("a.json")), encoding="utf-8")
    with pytest.raises(ValueError, match="execution identity"):
        run_batch(**kwargs)


def test_resume_binds_effective_config_and_artifact_content(tmp_path, monkeypatch):
    class EffectiveDetector(_FakeDetector):
        def __init__(self, config):
            super().__init__({**config, "resolved_model": "model-one"})
    monkeypatch.setattr("detector.runner.Detector", EffectiveDetector)
    scorer = tmp_path / "scorer.bin"
    scorer.write_bytes(b"one")
    kwargs = dict(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                  run_id="r", runs_root=tmp_path / "runs",
                  config={"causal_audit": {"scorer_artifact": str(scorer)}}, workers=1)
    run_batch(**kwargs)
    scorer.write_bytes(b"two")
    with pytest.raises(ValueError, match="different manifest"):
        run_batch(**kwargs)


def test_batch_does_not_overwrite_a_detector_identity_mismatch(tmp_path, monkeypatch):
    class MismatchedDetector(_FakeDetector):
        def detect(self, record, *, case_id, interventions=None):
            result = super().detect(record, case_id=case_id, interventions=interventions)
            result["provenance"] = {"execution_identity": {"digest": "different"}}
            return result
    monkeypatch.setattr("detector.runner.Detector", MismatchedDetector)
    with pytest.raises(ValueError, match="execution identity changed"):
        run_batch(dataset_root=_write_dataset(tmp_path / "data"), cases=["a.json"],
                  run_id="r", runs_root=tmp_path / "runs", config={}, workers=1)
    assert not (tmp_path / "runs/r/cases/a.json/result.json").exists()
