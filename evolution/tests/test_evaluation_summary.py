"""Diagnostic states derived from formal gate evidence without changing gates."""

from evolution.engine import evaluation_summary


def _report(*, unmeasured=(), behavior_passed=False, mechanism=None):
    return {
        "candidate": {"claim": {"target_polarity": "recall"}},
        "structural": {"passed": True},
        "harmlessness": {"passed": True},
        "behavior": {
            "passed": behavior_passed,
            "fired_rate": 0.2,
            "behavior_events": 1,
            "delta": {
                "flips": {"T->F": 1, "F->T": 0},
                "marginal_cases": 1,
                "new_errors": 0,
                "health_regressed": 0,
            },
        },
        "m": None,
        "candidate_rows": [{"case": "gain_1"}],
        "unmeasured_cases": list(unmeasured),
        "mechanism_opportunity": mechanism,
    }


def test_precision_gain_under_recall_claim_is_a_secondary_hypothesis():
    summary = evaluation_summary(_report())
    assert summary["state"] == "secondary_hypothesis"
    assert summary["mechanism_supported"] is False
    assert summary["protected"] is True
    assert summary["utility_delta"]["fp_removed"] == 1
    assert summary["next_action"] == "validate_secondary_benefit"


def test_unmeasured_arm_preserves_candidate_for_retest():
    summary = evaluation_summary(_report(unmeasured=["gain_1"]))
    assert summary["state"] == "measurement_pending"
    assert summary["measurable"] is False
    assert summary["utility_delta"] is None
    assert summary["next_action"] == "retest_frozen_candidate"


def test_missing_mechanism_coverage_does_not_claim_a_failed_repair():
    summary = evaluation_summary(_report(
        mechanism={"evaluable": False, "reason_code": "no_member"},
    ))
    assert summary["state"] == "coverage_missing"
    assert summary["mechanism_supported"] is None
    assert summary["next_action"] == "add_mechanism_matched_validation"
