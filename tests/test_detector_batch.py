from __future__ import annotations

import threading
import time
from pathlib import Path

from src.sehd_v6.detector import batch, replica_aggregation


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


# --- WF4: fail-closed replica aggregation (pure functions) ---------------


def _prediction(state: str, runtime_valid: bool = True) -> dict:
    return {"runtime_valid": runtime_valid, "state": state}


def test_aggregate_states_unanimous_and_majority() -> None:
    assert replica_aggregation.aggregate_states(
        ["verified_error", "verified_error", "verified_error"]
    ) == "verified_error"
    assert replica_aggregation.aggregate_states(
        ["verified_clean", "verified_clean", "unresolved"]
    ) == "verified_clean"


def test_aggregate_states_disagreement_and_tie_stay_unresolved() -> None:
    assert replica_aggregation.aggregate_states(
        ["verified_error", "verified_clean", "unresolved"]
    ) == "unresolved"
    assert replica_aggregation.aggregate_states(
        ["verified_error", "verified_error", "verified_clean", "verified_clean"]
    ) == "unresolved"


def test_aggregate_states_consensus_policy_requires_full_agreement() -> None:
    assert replica_aggregation.aggregate_states(
        ["verified_clean", "verified_clean"], policy="consensus"
    ) == "verified_clean"
    assert replica_aggregation.aggregate_states(
        ["verified_clean", "verified_clean", "unresolved"], policy="consensus"
    ) == "unresolved"


def test_aggregate_states_unknown_state_votes_as_unresolved() -> None:
    # Unknown values are downgraded to unresolved votes, never upgraded.
    assert replica_aggregation.aggregate_states(
        ["verified_error", "verified_error", "banana"]
    ) == "verified_error"
    assert replica_aggregation.aggregate_states(
        ["verified_error", "banana", ""]
    ) == "unresolved"


def test_aggregate_states_rejects_empty_input_and_unknown_policy() -> None:
    import pytest

    with pytest.raises(ValueError):
        replica_aggregation.aggregate_states([])
    with pytest.raises(ValueError):
        replica_aggregation.aggregate_states(["unresolved"], policy="weighted")


def test_aggregate_replicas_single_replica_passthrough() -> None:
    result = replica_aggregation.aggregate_replicas([_prediction("verified_error")])
    assert result["state"] == "verified_error"
    assert result["unanimous"] is True
    assert result["runtime_valid"] is True
    assert result["replica_count"] == 1


def test_aggregate_replicas_runtime_invalid_replica_downgrades_whole_run() -> None:
    # Even with a unanimous verdict among healthy replicas, one crashed
    # replica means an incomplete adversarial test: fail-closed to unresolved.
    result = replica_aggregation.aggregate_replicas(
        [
            _prediction("verified_clean"),
            _prediction("verified_clean"),
            _prediction("verified_clean", runtime_valid=False),
        ]
    )
    assert result["state"] == "unresolved"
    assert result["runtime_valid"] is False
    assert result["invalid_replica_indexes"] == [2]
    assert result["unanimous"] is False
    assert result["replica_states"] == ["verified_clean"] * 3


def test_aggregate_replicas_rejects_empty_input() -> None:
    import pytest

    with pytest.raises(ValueError):
        replica_aggregation.aggregate_replicas([])


# --- WF4 regression: batch summary carries no predictions key ------------
# run_decision_replicas.py once consumed summary["predictions"], which never
# exists; rows live in predictions.json. These tests pin both sides of that
# contract so the silent all-unresolved regression cannot return.


def test_batch_summary_has_no_predictions_key_and_writes_predictions_file(
    monkeypatch, tmp_path: Path
) -> None:
    def detector(*, case_id, **_kwargs):
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
            "case_id_seen": case_id,
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    summary = batch.run_batch(
        records=_records(2), config={}, output_dir=tmp_path, max_parallel=2,
    )
    assert "predictions" not in summary
    predictions_path = tmp_path / "predictions.json"
    assert predictions_path.is_file()
    import json

    rows = json.loads(predictions_path.read_text(encoding="utf-8"))
    assert {row["case_id"] for row in rows} == set(_records(2))
    assert all(row["prediction"]["state"] == "verified_clean" for row in rows)


def test_collect_replica_predictions_reads_predictions_files(tmp_path: Path) -> None:
    import json

    from experiments.run_decision_replicas import collect_replica_predictions

    case_ids = ["case_a", "case_b"]
    roots = []
    for index in range(3):
        root = tmp_path / f"replica_{index}"
        root.mkdir()
        rows = [
            {
                "case_id": case_id,
                "prediction": {"state": "verified_error", "replica": index},
            }
            for case_id in case_ids
        ]
        (root / "predictions.json").write_text(
            json.dumps(rows), encoding="utf-8"
        )
        roots.append(root)

    collected = collect_replica_predictions(roots, case_ids)
    assert all(len(collected[case_id]) == 3 for case_id in case_ids)
    assert [row["replica"] for row in collected["case_a"]] == [0, 1, 2]


def test_collect_replica_predictions_missing_file_yields_incomplete(
    tmp_path: Path,
) -> None:
    import json

    from experiments.run_decision_replicas import collect_replica_predictions

    present = tmp_path / "replica_0"
    present.mkdir()
    (present / "predictions.json").write_text(
        json.dumps([{"case_id": "case_a", "prediction": {"state": "unresolved"}}]),
        encoding="utf-8",
    )
    absent = tmp_path / "replica_1"
    absent.mkdir()

    collected = collect_replica_predictions([present, absent], ["case_a"])
    # One replica produced no predictions.json: the case stays below k so the
    # caller routes it to the fail-closed incomplete_replicas branch.
    assert len(collected["case_a"]) == 1
