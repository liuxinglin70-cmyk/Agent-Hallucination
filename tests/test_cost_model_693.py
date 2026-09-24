"""Regression coverage for the 693-case cost model's calibration.

The model's whole claim is "calibrated on real readings, not estimated". That
is only checkable if the readings it harvests are pinned, so these tests pin
them: which files feed the sample, that a timeout row is not counted as a
duration, that the prose-only rows are visible as such, and that the estimators
keep their stated relationship to the sample.

They run fast and offline: ``cost_model_693`` reads JSON, it never calls out.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cost_model_693 as model  # noqa: E402


@pytest.fixture(scope="module")
def built():
    return model.build_model()


def test_model_is_calibrated_on_real_repository_readings(built):
    """Every measured row must come from a file that exists in the repo."""
    readings = built["sample"]["readings"]
    assert readings, "the model must not be built from an empty sample"
    sources = {
        row["source"].split("#")[0].replace("\\", "/") for row in readings
    }
    assert sources, "no provenance at all"
    for source in sources:
        assert (ROOT / source).exists(), f"missing calibration source: {source}"
    # The three archives the task names must all be represented.
    joined = "\n".join(row.get("original_source", "").replace("\\", "/") for row in readings)
    assert "reports/real/summary.json" in joined
    assert "real_evolution_20260923" in joined
    assert "verification_20260917" in joined


def test_capped_rows_are_flagged_and_excluded_from_the_distribution(built):
    """A case killed at a deadline is a lower bound, never a duration.

    ``Magentic_One/036`` was cut at exactly the 900-second provider timeout.
    Counting 900.0 as an ordinary reading would both misstate that case and
    hide the fact that the run never finished it.
    """
    capped = built["sample"]["capped_rows"]
    assert capped, "the archived material contains at least one capped row"
    for row in capped:
        assert row["reason"]
        assert row["seconds"] > 0
    capped_seconds = {row["seconds"] for row in capped}
    measured_seconds = {row["seconds"] for row in built["sample"]["readings"]}
    assert not (capped_seconds & measured_seconds)


def test_prose_only_rows_are_kept_apart_and_drive_the_sensitivity(built):
    """The four prose readings decide most of the tail; that must be visible."""
    prose = built["sample"]["prose_only_rows"]
    assert len(prose) == 4
    assert {row["case"] for row in prose} == {
        "Magentic_One/049.json", "Camel/018.json", "Magentic_One/050.json",
    }
    assert all(row["source"].endswith(".md") for row in prose)
    # 049 was measured twice under the same config; both readings are recorded.
    readings_049 = sorted(
        round(row["seconds"] / 60, 1) for row in prose
        if row["case"] == "Magentic_One/049.json"
    )
    assert readings_049 == [36.0, 93.8]

    sensitivity = built["sensitivity"]
    assert sensitivity["available"] is True
    assert sensitivity["mean_minutes"] < built["sample"]["mean_minutes"], (
        "dropping the slowest, prose-sourced rows must lower the mean"
    )
    assert set(sensitivity["estimates"]) == {"W1", "W4", "W8", "W16"}


def test_estimators_keep_their_stated_relationship_to_the_sample(built):
    """The three estimators must bracket each other as documented.

    ``work`` is a floor on a well-behaved corpus; ``simulated`` keeps the
    observed tail; ``efficiency-adjusted`` divides the simulation by the audit
    batch's measured packing efficiency, so it must be the largest at every
    worker count. A model whose estimates silently crossed would be reporting
    one of them under another's name.
    """
    for _key, block in built["estimates"].items():
        sim = block["simulated_makespan"]
        floor = block["work_lower_bound_minutes"]
        adjusted = block["efficiency_adjusted_minutes"]
        assert floor is not None and sim["schedulable"] and adjusted is not None
        assert sim["p10_minutes"] <= sim["p50_minutes"] <= sim["p90_minutes"]
        assert sim["p10_minutes"] * 0.95 <= floor <= sim["p90_minutes"] * 1.05, (
            "the work floor and the simulation disagree by more than sampling"
            " noise; one of them is computing something else"
        )
        assert adjusted >= sim["p50_minutes"], (
            "the packing efficiency must slow the estimate down, not speed it up"
        )


def test_more_workers_never_costs_more_time(built):
    floors = [
        block["work_lower_bound_minutes"]
        for _key, block in sorted(
            built["estimates"].items(), key=lambda item: item[1]["workers"],
        )
    ]
    assert floors == sorted(floors, reverse=True)


def test_tail_bound_is_reported_against_the_work_bound(built):
    """The tail narrative must be computed, not asserted.

    The model claims the corpus total binds before the 94-minute tail does. That
    is a comparison of two numbers, so the test recomputes both.
    """
    for _key, block in built["estimates"].items():
        tail = block["tail"]
        assert tail["single_slow_case_bound_minutes"] == 93.8
        expected = (
            -(-tail["extrapolated_slow_cases_in_693"] // tail["workers"])
            * 93.8
        )
        assert tail["tail_bound_minutes"] == pytest.approx(expected, abs=0.1)
        floor = tail["work_floor_minutes"]
        assert tail["binding_bound"] == (
            "work" if floor >= tail["tail_bound_minutes"] else "tail"
        )
        assert tail["slowest_reading_minutes"] >= 93.8, (
            "the slowest real reading cannot be below the documented tail"
        )


def test_audit_anchor_efficiency_is_computed_from_the_batchs_own_cases(built):
    """The packing efficiency needs the batch's per-case costs, not a guess."""
    audit = built["observed_batch_throughput"]["audit"]
    assert audit["per_case_costs_known"] == 12
    assert audit["cases_without_a_reading"] == []
    ideal = max(
        audit["sum_case_minutes"] / audit["workers"],
        audit["longest_case_minutes"],
    )
    assert audit["ideal_wall_minutes"] == pytest.approx(ideal, abs=0.2)
    assert audit["packing_efficiency"] == pytest.approx(
        audit["ideal_wall_minutes"] / audit["wall_minutes"], abs=0.002,
    )
    assert audit["packing_efficiency"] < 1.0, (
        "a batch that finished faster than its ideal would make the"
        " extrapolation optimistic, which has not been observed"
    )
    patrol = built["observed_batch_throughput"]["patrol"]
    assert "packing_efficiency" not in patrol, (
        "the patrol batch has no per-case archive, so it cannot carry one"
    )
    assert patrol["naive_projection_caveat"]


def test_token_model_excludes_rows_without_usage(built):
    """A row with no usage reading is absent, not free."""
    tokens = built["tokens"]
    assert tokens["measured_cases"] > 0
    for row in tokens["rows"]:
        assert row["tokens"] > 0
    expected = sum(row["tokens"] for row in tokens["rows"]) / len(tokens["rows"])
    assert tokens["mean_per_case"] == pytest.approx(expected, abs=0.1)
    assert tokens["corpus_estimate"] == pytest.approx(
        tokens["mean_per_case"] * model.CORPUS_SIZE, rel=1e-3,
    )


def test_zero_second_rows_are_recorded_rather_than_dropped(tmp_path, monkeypatch):
    """A cache hit or a batch-deadline skip is an event, not a duration.

    The classifier must name them. Dropping them silently would let a run whose
    whole tail was skipped look like a run with no slow cases.
    """
    rows = [
        {"case": "A/1.json", "seconds": 0.0, "source": "x"},
        {"case": "A/2.json", "seconds": 120.0, "source": "x"},
        {"case": "A/3.json", "seconds": 900.0, "source": "x"},
    ]
    classified = model.classify(rows)
    assert classified["zero_second_rows"] == ["A/1.json"]
    assert [row["case"] for row in classified["measured"]] == ["A/2.json"]
    assert classified["capped"][0]["case"] == "A/3.json"


def test_simulation_is_deterministic_and_worker_count_sensitive():
    """Same seed, same answer: a cost model that moves on re-run is useless."""
    seconds = [60.0, 120.0, 900.0, 30.0, 3600.0]
    first = model.simulate_makespan(seconds, corpus_size=50, workers=4, draws=200)
    second = model.simulate_makespan(seconds, corpus_size=50, workers=4, draws=200)
    assert first == second
    more = model.simulate_makespan(seconds, corpus_size=50, workers=8, draws=200)
    assert more["p50_minutes"] < first["p50_minutes"]
    # The longest job cannot be scheduled away.
    assert first["p50_minutes"] >= max(seconds) / 60.0


def test_cli_writes_a_model_that_parses(tmp_path):
    out = tmp_path / "model.json"
    assert model.main(["--json", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "detector.cost_model.v1"
    assert payload["corpus_size"] == 693
    assert payload["provider"]["model"] == "GLM-5.3-flash"
    assert payload["provider"]["case_timeout_seconds"] == 10800
    assert payload["provider"]["provider_timeout_seconds"] == 900
    assert payload["provider"]["total_timeout_seconds"] == 2400
    assert payload["provider"]["judge_votes"] == 3
    assert payload["provider"]["judge_vote_topup"] == 2
    assert payload["caveats"], "a model without its caveats is a forecast"
