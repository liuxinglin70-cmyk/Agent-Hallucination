from __future__ import annotations

import threading
import time
from pathlib import Path

from src.sehd_v6.detector import batch, replica_aggregation
from src.sehd_v6.core.bccse.genome import create_baseline_genome
from src.sehd_v6.core.paca_tcl.detector import load_skill_genome


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


def test_batch_resume_does_not_reuse_prediction_for_new_genome(
    monkeypatch, tmp_path: Path,
) -> None:
    calls = []
    genome = create_baseline_genome()

    def detector(*, case_id, genome=None, **_kwargs):
        calls.append((case_id, genome.content_hash if genome is not None else None))
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    records = _records(1)
    batch.run_batch(records=records, config={}, output_dir=tmp_path)
    genome.modules["evidence_obligation"].rules.append(
        "When a cited event is absent, keep the evidence obligation incomplete."
    )
    second = batch.run_batch(
        records=records, config={}, output_dir=tmp_path, genome=genome,
    )
    assert second["resumed_case_count"] == 0
    assert len(calls) == 2
    assert calls[0][1] is None
    assert calls[1][1] == genome.content_hash
    completion = next((tmp_path / "cases").glob("*/completion.json"))
    import json
    payload = json.loads(completion.read_text(encoding="utf-8"))
    assert payload["genome_hash"] == genome.content_hash


def test_persisted_genome_loader_validates_hash_and_module_set(tmp_path: Path) -> None:
    import json

    genome = create_baseline_genome()
    path = tmp_path / "champion_genome.json"
    path.write_text(json.dumps(genome.to_dict()), encoding="utf-8")
    loaded = load_skill_genome(path)
    assert loaded.content_hash == genome.content_hash

    poisoned = genome.to_dict()
    poisoned["content_hash"] = "0" * 64
    path.write_text(json.dumps(poisoned), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="content_hash"):
        load_skill_genome(path)

    incomplete = genome.to_dict()
    incomplete["modules"].pop("abstention_rule")
    path.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly the registered modules"):
        load_skill_genome(path)


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


# --- W1: library-level replica orchestration (run_replicated_batch) ------


def _stochastic_config() -> dict:
    # At least one route with temperature > 0: replicas are meaningful and
    # the degradation guard must not fire.
    return {"boundary_route": {"model": "mock", "temperature": 0.7}}


def test_run_replicated_batch_isolates_env_and_restores_it(
    monkeypatch, tmp_path: Path
) -> None:
    import os

    seen_cache_roots: list[str] = []
    states = ["verified_error", "verified_error", "verified_clean"]
    counter = {"calls": 0}

    def detector(*, case_id, **_kwargs):
        seen_cache_roots.append(os.environ.get("PACA_GLOBAL_CACHE_ROOT", ""))
        state = states[counter["calls"] % len(states)]
        counter["calls"] += 1
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": state,
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", "original-root")

    report = batch.run_replicated_batch(
        records=_records(1),
        config=_stochastic_config(),
        output_root=tmp_path,
        replicas=3,
        replica_policy="majority",
        max_parallel=1,
    )

    # Every replica ran under its own isolated global cache root...
    assert len(seen_cache_roots) == 3
    assert len(set(seen_cache_roots)) == 3
    for index, root in enumerate(seen_cache_roots):
        assert f"replica_{index}" in root
    # ...and the original environment is restored afterwards.
    assert os.environ["PACA_GLOBAL_CACHE_ROOT"] == "original-root"

    # Majority aggregation: 2x verified_error beats 1x verified_clean.
    assert report["replicas_effective"] == 3
    assert report["replica_degradation"] is None
    case = report["cases"][0]
    assert case["aggregate"]["state"] == "verified_error"
    assert case["aggregate"]["unanimous"] is False
    assert (tmp_path / "aggregated_report.json").is_file()
    for index in range(3):
        assert (tmp_path / f"replica_{index}" / "predictions.json").is_file()


def test_run_replicated_batch_degrades_when_all_routes_deterministic(
    monkeypatch, tmp_path: Path
) -> None:
    counter = {"calls": 0}

    def detector(*, case_id, **_kwargs):
        counter["calls"] += 1
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_clean",
        }

    monkeypatch.setattr(batch, "detect_record", detector)

    # Temperature 0 (and unset) routes make k replicas identical cache
    # copies: the guard degrades to one replica instead of burning k calls.
    report = batch.run_replicated_batch(
        records=_records(2),
        config={"boundary_route": {"model": "mock", "temperature": 0}},
        output_root=tmp_path,
        replicas=3,
        max_parallel=1,
    )
    assert counter["calls"] == 2  # exactly one detection per case
    assert report["replicas_effective"] == 1
    assert report["replica_degradation"] == {
        "reason": "temperature_zero", "requested": 3,
    }
    assert report["cases"][0]["aggregate"]["unanimous"] is True
    assert not (tmp_path / "replica_1").exists()


def test_run_replicated_batch_disagreement_is_fail_closed(
    monkeypatch, tmp_path: Path
) -> None:
    states = iter(["verified_error", "verified_clean", "unresolved"])

    def detector(**_kwargs):
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": next(states),
        }

    monkeypatch.setattr(batch, "detect_record", detector)
    report = batch.run_replicated_batch(
        records=_records(1),
        config=_stochastic_config(),
        output_root=tmp_path,
        replicas=3,
        max_parallel=1,
    )
    assert report["cases"][0]["aggregate"]["state"] == "unresolved"


def test_run_replicated_batch_incomplete_replicas_are_fail_closed(
    monkeypatch, tmp_path: Path
) -> None:
    replica_index = {"value": 0}
    original_run_batch = batch.run_batch

    def detector(**_kwargs):
        # Replica 0 succeeds; later replicas crash -> no predictions rows.
        # The stub increments the counter *before* delegating to the real
        # run_batch, so the first replica observes value 1 here.
        if replica_index["value"] > 1:
            raise RuntimeError("replica provider failure")
        return {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "state": "verified_error",
        }

    def run_batch_stub(*, records, config, output_dir, max_parallel, resume):
        replica_index["value"] += 1
        return original_run_batch(
            records=records, config=config, output_dir=output_dir,
            max_parallel=max_parallel, resume=resume,
        )

    monkeypatch.setattr(batch, "detect_record", detector)
    monkeypatch.setattr(batch, "run_batch", run_batch_stub)
    report = batch.run_replicated_batch(
        records=_records(1),
        config=_stochastic_config(),
        output_root=tmp_path,
        replicas=3,
        max_parallel=1,
    )
    aggregate = report["cases"][0]["aggregate"]
    assert aggregate["state"] == "unresolved"
    assert aggregate["note"] == "incomplete_replicas"
    assert aggregate["runtime_valid"] is False
    assert aggregate["replica_count"] == 1


def test_run_replicated_batch_validates_arguments(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        batch.run_replicated_batch(
            records=_records(1), config={}, output_root=tmp_path, replicas=0,
        )
    with pytest.raises(ValueError):
        batch.run_replicated_batch(
            records=_records(1), config={}, output_root=tmp_path,
            replicas=batch.MAX_REPLICAS + 1,
        )
    with pytest.raises(ValueError):
        batch.run_replicated_batch(
            records=_records(1), config={}, output_root=tmp_path,
            replica_policy="weighted",
        )


def test_detector_boundary_exports_replica_api() -> None:
    # The stable application-facing boundary must expose the aggregation
    # and orchestration entry points (previously only the experiment script
    # could reach them).
    from src.sehd_v6 import detector

    for name in (
        "aggregate_states",
        "aggregate_replicas",
        "POLICIES",
        "run_replicated_batch",
        "collect_replica_predictions",
    ):
        assert name in detector.__all__
        assert hasattr(detector, name)
