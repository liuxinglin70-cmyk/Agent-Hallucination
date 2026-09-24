"""Repairs on a gate set must be recorded without being scored.

The 2026-09-21 promotion run produced a candidate that removed two false
positives on the protection set and lost one true positive on the benefit set.
HARMLESSNESS protects only baseline-correct rows, so the two repairs were
invisible; M reads only the benefit set, so the lost TP was the entire reading:
a net ``-1`` for a candidate whose true effect was "fixed two, broke one".

These tests lock the reading that closes that hole. What must hold:

* repairs and regressions are counted separately, both directions, and never
  summed — plan §5.2 forbids netting one against the other;
* a repair is a *decisive* flip. Resolving a case the control arm left
  undecided is calibration, not repair, and counting it would let a candidate
  manufacture repairs by simply deciding more;
* the reading is recorded in the promotion report whether or not it helps the
  candidate, and it changes no gate verdict.
"""

from __future__ import annotations

from evolution.metrics import protection_repair


def _row(case, gold, prediction):
    return {"case": case, "gold": gold, "prediction": prediction}


def test_both_directions_are_counted_separately_and_never_summed():
    baseline = [
        _row("fn_repaired", True, False),
        _row("fp_repaired", False, True),
        _row("fn_regressed", True, True),
        _row("fp_regressed", False, False),
        _row("untouched", True, True),
    ]
    active = [
        _row("fn_repaired", True, True),
        _row("fp_repaired", False, False),
        _row("fn_regressed", True, False),
        _row("fp_regressed", False, True),
        _row("untouched", True, True),
    ]
    repair = protection_repair(baseline, active)
    assert repair["repaired"] == 2
    assert repair["repaired_fn"] == 1
    assert repair["repaired_fp"] == 1
    assert repair["regressed"] == 2
    assert repair["regressed_fn"] == 1
    assert repair["regressed_fp"] == 1
    # The whole point: there is no field that combines them.
    assert "net" not in repair
    assert repair["repaired"] != repair["repaired"] - repair["regressed"]


def test_calibration_is_not_reported_as_a_repair():
    """``unresolved -> correct`` must not become a repair.

    This is the same loophole the boundary-claim scorer guards against: a
    candidate that merely decides more cases would otherwise bank repairs
    without correcting a single decision.
    """
    baseline = [_row("was_undecided", True, None), _row("was_wrong", True, False)]
    active = [_row("was_undecided", True, True), _row("was_wrong", True, True)]
    repair = protection_repair(baseline, active)
    assert repair["repaired"] == 1
    assert repair["repaired_fn"] == 1
    assert repair["unresolved_removed"] == 1
    assert {row["case"] for row in repair["rows"]} == {"was_wrong"}


def test_going_unresolved_is_not_a_regression_but_is_reported():
    """A candidate that stops deciding a case is not charged here.

    HARMLESSNESS and M both already treat ``unresolved_added`` as a failure;
    double-counting it as a regression would report one fault twice under two
    names. It stays visible as its own count.
    """
    baseline = [_row("was_right", True, True)]
    active = [_row("was_right", True, None)]
    repair = protection_repair(baseline, active)
    assert repair["regressed"] == 0
    assert repair["unresolved_added"] == 1


def test_repair_reading_matches_the_archived_run():
    """Pin the real reading that motivated the change.

    The archived promotion run put two control-arm false positives in the
    protection set and one control-arm true positive in the benefit set. The
    two repairs must be visible on the protection set while the lost TP is
    charged on the benefit set — the run's true shape, which no gate reported.
    """
    import json
    from pathlib import Path

    archive = (
        Path(__file__).resolve().parents[2] / "evolution" / "tests" / "fixtures" / "promotion"
    )
    if not (archive / "run" / "arms" / "control.json").is_file():
        import pytest

        pytest.skip("archived promotion run is not present")
    protocol = json.loads(
        (archive / "protocol" / "promotion-protocol-real.json").read_text(
            encoding="utf-8-sig"
        )
    )
    control = {
        row["case"]: row
        for row in json.loads(
            (archive / "run" / "arms" / "control.json").read_text(
                encoding="utf-8-sig"
            )
        )["rows"]
    }
    candidate = {
        row["case"]: row
        for row in json.loads(
            (archive / "run" / "arms" / "candidate.json").read_text(
                encoding="utf-8-sig"
            )
        )["rows"]
    }
    roles = protocol["roles"]
    protection = sorted(c for c, r in roles.items() if r == "protection")
    benefit = sorted(c for c, r in roles.items() if r == "benefit")

    on_protection = protection_repair(
        [control[c] for c in protection], [candidate[c] for c in protection],
    )
    on_benefit = protection_repair(
        [control[c] for c in benefit], [candidate[c] for c in benefit],
    )
    # The candidate removed the two control-arm false positives...
    assert on_protection["repaired_fp"] == 2
    assert on_protection["regressed"] == 0
    # ...and lost one true positive, on the set that is scored.
    assert on_benefit["regressed_fn"] == 1
    assert on_benefit["repaired"] == 0
