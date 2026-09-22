"""Calibration audit: a discrete reliability table, and its stated limits.

The detector reports confidence as an ordinal level, so the audit must report
levels -- and must refuse to dress them up as probabilities. These tests pin
both halves: the counts have to be exact, and the unimplemented continuous
metrics have to stay explicitly unimplemented with a reason attached.
"""

from __future__ import annotations

from evolution.calibration import (
    CALIBRATION_SCHEMA,
    CONTINUOUS_METRICS_UNAVAILABLE,
    METRIC_ROLE,
    calibration_report,
    confidence_levels,
    monotonicity,
    reliability_table,
)


def row(
    case: str,
    *,
    confidence: str | None = "high",
    gold: bool | None = True,
    prediction: bool | None = True,
    authority: str = "authoritative",
) -> dict:
    proof: dict = {}
    if confidence is not None:
        proof["judge"] = {"confidence": confidence}
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "result": {"proof": proof, "decision_authority": authority},
    }


def test_counts_are_exact_per_level():
    rows = [
        row("A/001.json", confidence="high", gold=True, prediction=True),
        row("A/002.json", confidence="high", gold=False, prediction=True),
        row("A/003.json", confidence="medium", gold=True, prediction=False),
    ]
    table = reliability_table(rows)
    by_level = {entry["level"]: entry for entry in table["levels"]}
    assert by_level["high"]["n"] == 2
    assert by_level["high"]["correct"] == 1
    assert by_level["high"]["wrong"] == 1
    assert by_level["high"]["accuracy"] == 0.5
    assert by_level["medium"]["n"] == 1
    assert by_level["medium"]["wrong"] == 1
    assert by_level["medium"]["accuracy"] == 0.0


def test_abstention_is_its_own_outcome_not_a_miss():
    """A row the detector declined to decide says nothing about confidence.

    Folding it into "wrong" would make a cautious system look miscalibrated
    and a reckless one look sharp.
    """
    rows = [
        row("A/001.json", confidence="high", gold=True, prediction=True),
        row("A/002.json", confidence="high", gold=True, prediction=None),
    ]
    table = reliability_table(rows)
    entry = table["levels"][0]
    assert entry["correct"] == 1
    assert entry["wrong"] == 0
    assert entry["abstained"] == 1
    # Accuracy is over decided rows only, so the abstention does not depress it.
    assert entry["accuracy"] == 1.0
    assert entry["abstention_rate"] == 0.5


def test_an_empty_level_reports_none_not_zero():
    """"No cases at this level" and "every case wrong" are different findings."""
    rows = [row("A/001.json", confidence="high", gold=True, prediction=True)]
    table = reliability_table(rows)
    levels = {entry["level"] for entry in table["levels"]}
    assert levels == {"high"}
    # A level with no rows is absent entirely, and a level whose rows all
    # abstained reports accuracy None -- both distinguishable from a 0.0 that
    # means "committed and was wrong every time".
    abstained = reliability_table([
        row("A/001.json", confidence="low", gold=True, prediction=None),
    ])
    entry = abstained["levels"][0]
    assert entry["level"] == "low"
    assert entry["accuracy"] is None
    assert entry["abstained"] == 1
    wrong = reliability_table([
        row("A/002.json", confidence="low", gold=True, prediction=False),
    ])
    assert wrong["levels"][0]["accuracy"] == 0.0


def test_non_authoritative_rows_abstain():
    """A degraded decision is not a confident decision either way."""
    rows = [row("A/001.json", confidence="high", authority="degraded")]
    table = reliability_table(rows)
    entry = table["levels"][0]
    assert entry["abstained"] == 1
    assert entry["accuracy"] is None


def test_unlabelled_rows_do_not_enter_the_table():
    rows = [
        row("A/001.json", confidence="high", gold=None, prediction=True),
        row("A/002.json", confidence="high", gold=True, prediction=True),
    ]
    table = reliability_table(rows)
    entry = table["levels"][0]
    assert entry["unlabelled"] == 1
    assert entry["n"] == 1
    assert entry["accuracy"] == 1.0


def test_rows_without_a_stated_confidence_are_counted_separately():
    rows = [
        row("A/001.json", confidence=None),
        row("A/002.json", confidence="high"),
    ]
    table = reliability_table(rows)
    assert table["unstated_level_rows"] == 1
    assert sum(entry["n"] for entry in table["levels"]) == 1


def test_monotonicity_reports_violations_instead_of_hiding_them():
    """An unusable ordering is a finding, not something to smooth over."""
    rows = [
        row("A/001.json", confidence="high", gold=True, prediction=False),
        row("A/002.json", confidence="low", gold=True, prediction=True),
    ]
    table = reliability_table(rows)
    verdict = monotonicity(table)
    assert verdict["monotone"] is False
    assert verdict["violations"]
    assert "high" in verdict["violations"][0]

    healthy = reliability_table([
        row("A/001.json", confidence="high", gold=True, prediction=True),
        row("A/002.json", confidence="medium", gold=True, prediction=False),
    ])
    assert monotonicity(healthy)["monotone"] is True


def test_per_source_split_survives_into_the_report():
    rows = [
        row("Alpha/001.json", confidence="high", gold=True, prediction=True),
        row("Beta/001.json", confidence="high", gold=True, prediction=False),
        row("Beta/002.json", confidence="high", gold=True, prediction=False),
    ]
    report = calibration_report(rows)
    sources = report["per_source"]["sources"]
    assert set(sources) == {"Alpha", "Beta"}
    alpha = {entry["level"]: entry for entry in sources["Alpha"]["levels"]}
    beta = {entry["level"]: entry for entry in sources["Beta"]["levels"]}
    # The overall table would have called "high" 1/3 accurate; the split shows
    # the level is fine on one source and wrong on the other.
    assert alpha["high"]["accuracy"] == 1.0
    assert beta["high"]["accuracy"] == 0.0


def test_the_report_declares_itself_calibration_only_and_names_its_gaps():
    """§19 rule 12: authority is labelled, and absent metrics are explained."""
    report = calibration_report([row("A/001.json")])
    assert report["schema_version"] == CALIBRATION_SCHEMA
    assert report["metric_role"] == METRIC_ROLE == "calibration-only"
    assert report["continuous_metrics_unavailable"] == CONTINUOUS_METRICS_UNAVAILABLE
    # The reason has to name the metrics and the cause, not just say "skipped".
    for metric in ("Brier", "log loss", "AUROC"):
        assert metric in report["continuous_metrics_unavailable"]
    assert "ordinal" in report["continuous_metrics_unavailable"]
    # And the claim that this scores nothing is made where a reader will see it.
    assert "scores nothing" in report["note"]


def test_confidence_levels_come_from_the_runtime_vocabulary():
    """A level the detector can emit must be a level the audit knows about."""
    levels = confidence_levels()
    assert set(levels) == {"high", "medium", "low"}


def test_a_custom_level_reader_reuses_the_same_table():
    """A learned runner's score can be audited without a second implementation."""
    rows = [
        {"case": "A/001.json", "gold": True, "prediction": True,
         "authority": "authoritative", "result": {"score": 0.9}},
    ]
    table = reliability_table(rows, level_of=lambda r: f"{r['result']['score']:.1f}")
    assert table["levels"][0]["level"] == "0.9"
    assert table["levels"][0]["accuracy"] == 1.0
