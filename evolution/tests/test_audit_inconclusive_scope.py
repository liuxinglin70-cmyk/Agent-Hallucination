"""The defect audit must not freeze a run over an unrelated unmeasured case.

What happened: a three-cycle real run froze on cycle 1 because one *sentinel*
case hit its 3600s case deadline. That case was in neither the verdict's
evidence nor its cause. The audit's guard swept the whole batch for any
unmeasurable row, found that one, marked the audit inconclusive, and the
frozen verdict ended the entire experiment.

The audit exists to prove the gate *logic* rejects a known bad candidate. The
honest test is whether the rejection survives removing rows nobody could
measure -- not whether such rows exist at all. A genuinely leaking defect still
leaks on the healthy cases, so excluding the unmeasurable ones weakens nothing.

These tests lock both directions:

* a rejection that survives the exclusion still certifies the audit;
* a rejection that *disappears* once the unmeasurable rows are removed is
  inconclusive, because the surviving cases were never actually measured as
  rejected;
* too few measurable cases left is inconclusive rather than a silent pass.
"""

from __future__ import annotations

from evolution.engine import (
    _defect_rejection_after_exclusions,
    _unmeasurable_cases,
)

CONFIG = {
    "gates": {
        "m_min_marginal_cases": 1,
        "m_new_errors_allowed": 0,
        "harmlessness_min_protected_tp": 2,
        "harmlessness_min_protected_tn": 2,
        "harmlessness_new_fn_allowed": 0,
        "harmlessness_new_fp_allowed": 0,
        "harmlessness_new_unresolved_allowed": 0,
    },
}

SENTINEL = [
    "S/001.json", "S/002.json", "S/003.json",
    "S/004.json", "S/005.json", "S/006.json",
]
GAIN = ["G/001.json", "G/002.json"]


def _row(case, gold, prediction, *, authority="authoritative"):
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "state": (
            "verified_error" if prediction is True
            else ("verified_clean" if prediction is False else "unresolved")
        ),
    }


def _healthy_baseline():
    """A control arm above the HARMLESSNESS floor with room to lose one case.

    Three correct positives and three correct negatives, so excluding a single
    unmeasured case still leaves the 2 TP / 2 TN floor satisfiable. That is the
    real split's shape (12 sentinel cases, 4 TP / 4 TN); a smaller fixture
    would make the floor itself the reason for stopping and hide what these
    tests are about.
    """
    return [
        _row("S/001.json", True, True),
        _row("S/002.json", False, False),
        _row("S/003.json", True, True),
        _row("S/004.json", False, False),
        _row("S/005.json", True, True),
        _row("S/006.json", False, False),
        _row("G/001.json", True, True),
        _row("G/002.json", False, False),
    ]


def _degrade(rows, case):
    return [
        _row(r["case"], r["gold"], r["prediction"],
             authority=("degraded_judge_only" if r["case"] == case
                        else "authoritative"))
        for r in rows
    ]


def test_an_undecided_case_is_recognised_as_unmeasurable():
    rows = [
        _row("A/1.json", True, True),
        _row("B/1.json", True, None, authority="degraded_judge_only"),
    ]
    assert _unmeasurable_cases(rows) == {"B/1.json"}


def test_a_zero_gain_defect_still_certifies_when_an_unrelated_case_degraded():
    """The regression: one irrelevant deadline must not freeze the audit.

    The defect's candidate arm is a no-op, so it wins nothing on the gain set
    and M rejects it. That rejection is real evidence the gates work, and it
    must survive the presence of an unrelated degraded sentinel case -- which
    is exactly the situation that froze the real three-cycle run.
    """
    baseline = _degrade(_healthy_baseline(), "S/003.json")
    candidate = _degrade(baseline, "S/003.json")
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": candidate},
        baseline_rows=baseline,
        sentinel_cases=SENTINEL,
        gain_cases=GAIN,
        config=CONFIG,
    )
    assert reason == "", reason


def test_a_defect_that_only_fails_because_of_the_unmeasured_case_is_inconclusive():
    """If the rejection depends on the missing row, the gates were not tested.

    The sentinel is healthy and adequate; the gain set is where the defect
    looks good. Once the unmeasurable gain case is dropped, M passes -- so the
    earlier rejection came from the unmeasured row, not from the gate logic.
    """
    baseline = [
        *_healthy_baseline()[:6],
        _row("G/001.json", True, False),   # the defect repairs this one
        _row("G/002.json", False, False),
    ]
    candidate = [
        *_healthy_baseline()[:6],
        _row("G/001.json", True, True),    # repaired -> M would pass
        _row("G/002.json", False, None, authority="degraded_judge_only"),
    ]
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": candidate},
        baseline_rows=baseline,
        sentinel_cases=SENTINEL,
        gain_cases=GAIN,
        config=CONFIG,
    )
    assert "does not survive" in reason


def test_too_few_measurable_cases_is_inconclusive_not_a_pass():
    baseline = [_row("S/001.json", True, None, authority="degraded_judge_only")]
    candidate = [_row("S/001.json", True, None, authority="degraded_judge_only")]
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": candidate},
        baseline_rows=baseline,
        sentinel_cases=["S/001.json"],
        gain_cases=[],
        config=CONFIG,
    )
    assert "no measurable case left" in reason


def test_harmlessness_failure_on_healthy_sentinel_certifies():
    """A defect that breaks a protected case is rejected on real evidence."""
    baseline = [
        _row("S/001.json", True, True),
        _row("S/002.json", False, False),
        _row("S/003.json", True, True),
        _row("S/004.json", False, False),
    ]
    candidate = [
        _row("S/001.json", True, False),   # breaks a protected positive
        _row("S/002.json", False, False),
        _row("S/003.json", True, None, authority="degraded_judge_only"),
        _row("S/004.json", False, False),
    ]
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": candidate},
        baseline_rows=baseline,
        sentinel_cases=SENTINEL,
        gain_cases=[],
        config=CONFIG,
    )
    assert reason == "", reason


def test_no_unmeasurable_rows_leaves_the_verdict_alone():
    """Without exclusions the guard must not re-derive anything."""
    baseline = _healthy_baseline()
    candidate = _healthy_baseline()
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": candidate},
        baseline_rows=baseline,
        sentinel_cases=SENTINEL,
        gain_cases=GAIN,
        config=CONFIG,
    )
    assert reason == ""
