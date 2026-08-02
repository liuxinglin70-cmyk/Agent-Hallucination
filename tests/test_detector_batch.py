from __future__ import annotations

import threading
import time
from pathlib import Path

from src.sehd_v6.detector import batch


def _records(count: int = 4) -> dict[str, dict]:
    return {
        f"case/{index}": {
            "history": [{"role": "assistant", "content": str(index)}],
            "gold_label": bool(index % 2),
        }
        for index in range(count)
    }


def test_batch_runs_concurrently_with_isolated_outputs(monkeypatch, tmp_path: Path) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def detector(*, record, case_id, output_dir, **_kwargs):
        nonlocal active, peak
        assert "gold_label" not in record
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
            "case_id_seen": case_id,
            "output_dir_seen": str(output_dir),
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    summary = batch.run_batch(
        records=_records(), config={"detector_mode": "standalone"},
        output_dir=tmp_path, max_parallel=4,
    )
    assert peak >= 2
    assert summary["completed_case_count"] == 4
    assert summary["failed_case_count"] == 0
    case_dirs = {row["case_dir"] for row in summary["cases"].values()}
    assert len(case_dirs) == 4
    assert all((Path(path) / "completion.json").is_file() for path in case_dirs)


def test_batch_resume_requires_matching_hashes(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def detector(*, case_id, **_kwargs):
        calls.append(case_id)
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    records = _records(2)
    first = batch.run_batch(
        records=records, config={"version": 1}, output_dir=tmp_path,
        max_parallel=2,
    )
    assert first["completed_case_count"] == 2
    assert len(calls) == 2

    second = batch.run_batch(
        records=records, config={"version": 1}, output_dir=tmp_path,
        max_parallel=2,
    )
    assert second["resumed_case_count"] == 2
    assert len(calls) == 2

    records["case/0"]["history"][0]["content"] = "changed"
    third = batch.run_batch(
        records=records, config={"version": 1}, output_dir=tmp_path,
        max_parallel=2,
    )
    assert third["resumed_case_count"] == 1
    assert calls[-1] == "case/0"


def test_failed_case_is_recorded_and_retried(monkeypatch, tmp_path: Path) -> None:
    attempts = 0

    def detector(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary provider failure")
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    records = _records(1)
    first = batch.run_batch(
        records=records, config={}, output_dir=tmp_path, max_parallel=1,
    )
    assert first["failed_case_count"] == 1
    case_dir = Path(first["cases"]["case/0"]["case_dir"])
    assert (case_dir / "failure.json").is_file()
    assert not (case_dir / "completion.json").is_file()

    second = batch.run_batch(
        records=records, config={}, output_dir=tmp_path, max_parallel=1,
    )
    assert second["completed_case_count"] == 1
    assert second["resumed_case_count"] == 0
    assert (case_dir / "completion.json").is_file()
