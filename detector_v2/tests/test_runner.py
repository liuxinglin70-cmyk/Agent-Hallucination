from __future__ import annotations

import json

import pytest

from detector_v2.runner import run_batch, safe_case_id


class _FakeDetector:
    def __init__(self, config):
        self.config = config

    def detect(self, record, *, case_id, artifacts=None):
        return {
            "schema_version": "detector_v2.detection_result.v1",
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


def test_safe_case_id():
    assert safe_case_id("BFCL/001.json") == "BFCL__001.json"


def test_run_batch_rejects_case_path_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr("detector_v2.runner.Detector", _FakeDetector)
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
    monkeypatch.setattr("detector_v2.runner.Detector", _FakeDetector)
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
    monkeypatch.setattr("detector_v2.runner.Detector", _FakeDetector)
    run_batch(
        dataset_root=root, cases=["a.json"], run_id="r",
        runs_root=tmp_path / "runs", config={"version": 1}, workers=1,
    )
    with pytest.raises(ValueError, match="different manifest"):
        run_batch(
            dataset_root=root, cases=["a.json"], run_id="r",
            runs_root=tmp_path / "runs", config={"version": 2}, workers=1,
        )
