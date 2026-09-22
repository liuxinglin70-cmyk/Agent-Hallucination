"""Mechanism-level opportunity: what the frozen gain set can actually score.

``gain_opportunity`` counts by polarity, so any FN in the batch looks like an
opportunity for any recall claim. That is the instrument defect the plan calls
out (§2.4): a candidate aimed at mechanism A must not be credited for a
repairable case belonging to mechanism B. These tests pin the mechanism-level
reading and, just as important, the cases it refuses to call measurable --
ambiguity, missing evidence, unresolved runs, non-boolean labels, annotation
gaps and duplicate rows.
"""

from __future__ import annotations

import pytest

from evolution.metrics import mechanism_opportunity

MECHANISM = {
    "mechanism_id": "M-test-1",
    "objective": "detection",
    "target_polarity": "recall",
    "status": "confirmed",
}


def membership(case: str, **overrides) -> dict:
    base = {
        "mechanism_id": "M-test-1",
        "status": "confirmed",
        "evidence": [f"structural evidence for {case}"],
        "repairable": True,
    }
    base.update(overrides)
    return base


def row(case: str, gold, prediction, **overrides) -> dict:
    base = {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": "authoritative",
        "result": {
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "stage_status": {"judge": {"state": "ok"}},
        },
    }
    base.update(overrides)
    return base


def test_only_confirmed_members_of_this_mechanism_count():
    """A repairable FN of another mechanism is not this mechanism's opportunity."""
    rows = [
        row("A/001.json", True, False),
        row("A/002.json", True, False),
    ]
    members = {
        "A/001.json": membership("A/001.json"),
        "A/002.json": membership("A/002.json", mechanism_id="M-other"),
    }
    result = mechanism_opportunity(rows, MECHANISM, members)
    assert result["repairable_errors"] == 1
    assert result["opportunity_cases"] == ["A/001.json"]
    assert result["evaluable"] is True
    assert result["reason_code"] == "measurable"
    assert result["metric_role"] == "promotion-authoritative"


def test_ambiguous_or_evidence_free_membership_is_not_an_opportunity():
    """An ambiguous case is reported, never counted as a repair opportunity."""
    rows = [row("A/001.json", True, False), row("A/002.json", True, False)]
    members = {
        "A/001.json": membership("A/001.json", status="ambiguous"),
        "A/002.json": membership("A/002.json", evidence=[]),
    }
    result = mechanism_opportunity(rows, MECHANISM, members)
    assert result["repairable_errors"] == 0
    assert result["unconfirmed"] == 2
    assert result["evaluable"] is False
    assert result["reason_code"] == "no_mechanism_opportunity"


def test_membership_is_keyed_by_case_not_derived_from_identifiers():
    """Renaming cases and sources cannot move an opportunity across mechanisms.

    The reading must follow the evaluator binding, so two runs that differ only
    in case ids and sources -- with the binding mapped accordingly -- produce
    the same counts.
    """
    left = mechanism_opportunity(
        [row("BFCL/001.json", True, False), row("Camel/002.json", True, False)],
        MECHANISM,
        {
            "BFCL/001.json": membership("BFCL/001.json"),
            "Camel/002.json": membership("Camel/002.json", repairable=False),
        },
    )
    right = mechanism_opportunity(
        [row("SmolAgents/900.json", True, False),
         row("Octotools/901.json", True, False)],
        MECHANISM,
        {
            "SmolAgents/900.json": membership("SmolAgents/900.json"),
            "Octotools/901.json": membership("Octotools/901.json", repairable=False),
        },
    )
    assert left["repairable_errors"] == right["repairable_errors"] == 1
    assert left["ineligible"] == right["ineligible"] == 1


def test_an_explicit_non_repairable_assessment_is_respected():
    """An evaluator that judged a case unrepairable outranks the polarity count."""
    result = mechanism_opportunity(
        [row("A/001.json", True, False)],
        MECHANISM,
        {"A/001.json": membership("A/001.json", repairable=False)},
    )
    assert result["repairable_errors"] == 0
    assert result["ineligible"] == 1
    assert result["reason_code"] == "no_mechanism_opportunity"


def test_precision_claims_count_false_positives_only():
    rows = [
        row("A/001.json", False, True),
        row("A/002.json", False, True),
        row("A/003.json", True, False),
    ]
    members = {item["case"]: membership(item["case"]) for item in rows}
    precision = mechanism_opportunity(
        rows, {**MECHANISM, "target_polarity": "precision"}, members,
    )
    assert precision["repairable_errors"] == 2
    assert sorted(precision["opportunity_cases"]) == ["A/001.json", "A/002.json"]
    # The same batch scored as a recall claim has no opportunity at all.
    recall = mechanism_opportunity(rows, MECHANISM, members)
    assert recall["repairable_errors"] == 1
    assert recall["opportunity_cases"] == ["A/003.json"]
    # A boundary claim is direction-agnostic.
    boundary = mechanism_opportunity(
        rows, {**MECHANISM, "target_polarity": "boundary"}, members,
    )
    assert boundary["repairable_errors"] == 3


def test_already_correct_cases_are_counted_separately_from_opportunities():
    rows = [
        row("A/001.json", True, True),
        row("A/002.json", True, False),
    ]
    members = {item["case"]: membership(item["case"]) for item in rows}
    result = mechanism_opportunity(rows, MECHANISM, members)
    assert result["opportunity_cases"] == ["A/002.json"]
    assert result["already_correct_cases"] == ["A/001.json"]
    assert result["already_correct"] == 1


def test_unresolved_or_unhealthy_rows_are_not_opportunities():
    """An outage is not a repair opportunity, and it is not a silent zero."""
    rows = [
        row("A/001.json", True, None),
        row("A/002.json", None, False),
        row("A/003.json", True, False,
            authority="degraded_judge_only",
            result={"runtime_valid": True, "decision_authority": "degraded_judge_only"}),
        row("A/004.json", True, False,
            result={"runtime_valid": False, "decision_authority": "invalid"}),
        row("A/005.json", True, False,
            result={"runtime_valid": True, "decision_authority": "authoritative",
                    "stage_status": {"recall_scanner": {"state": "no_quorum"}}}),
    ]
    members = {item["case"]: membership(item["case"]) for item in rows}
    result = mechanism_opportunity(rows, MECHANISM, members)
    assert result["repairable_errors"] == 0
    assert result["unresolved"] == 5


def test_non_boolean_labels_are_unresolved_rather_than_coerced():
    rows = [
        row("A/001.json", "true", False),
        row("A/002.json", True, "false"),
        row("A/003.json", True, False),
    ]
    members = {item["case"]: membership(item["case"]) for item in rows}
    result = mechanism_opportunity(rows, MECHANISM, members)
    assert result["repairable_errors"] == 1
    assert result["unresolved"] == 2
    assert result["reason_code"] == "mechanism_measurement_unresolved"


def test_localization_opportunity_needs_annotation_and_viability():
    """A step cannot be called repairable without an annotation and viability."""
    localization = {
        "mechanism_id": "M-test-1", "objective": "localization",
        "status": "confirmed",
    }
    rows = [
        row("A/001.json", True, True, responsible_step=4),
        row("A/002.json", True, True, responsible_step=4),
        row("A/003.json", True, True, responsible_step=4),
    ]
    members = {
        "A/001.json": membership("A/001.json", localization_viable=True),
        "A/002.json": membership("A/002.json", localization_viable=False),
        "A/003.json": membership("A/003.json", localization_viable=True),
    }
    result = mechanism_opportunity(
        rows, localization, members, gold_steps={"A/001.json": 7, "A/002.json": 7},
    )
    assert result["localization_repairable_errors"] == 1
    assert result["opportunity_cases"] == ["A/001.json"]
    # A case with no annotation cannot be scored, so it is unresolved, not zero.
    assert "A/003.json" in result["unresolved_cases"]


def test_a_step_equal_to_gold_is_already_correct_not_an_opportunity():
    localization = {
        "mechanism_id": "M-test-1", "objective": "localization",
        "status": "confirmed",
    }
    result = mechanism_opportunity(
        [row("A/001.json", True, True, responsible_step=7)],
        localization,
        {"A/001.json": membership("A/001.json", localization_viable=True)},
        gold_steps={"A/001.json": 7},
    )
    assert result["repairable_errors"] == 0
    assert result["already_correct_cases"] == ["A/001.json"]


def test_joint_objective_reports_both_directions_without_double_counting():
    joint = {
        "mechanism_id": "M-test-1", "objective": "joint", "status": "confirmed",
    }
    rows = [
        row("A/001.json", True, False),           # detection error only
        row("A/002.json", True, True, responsible_step=4),  # localization only
    ]
    members = {
        case: membership(case, localization_viable=True) for case in
        ("A/001.json", "A/002.json")
    }
    result = mechanism_opportunity(
        rows, joint, members, gold_steps={"A/001.json": 7, "A/002.json": 7},
    )
    assert result["detection_repairable_errors"] == 1
    assert result["localization_repairable_errors"] == 1
    # One case is one opportunity, however many directions it satisfies.
    assert result["repairable_errors"] == 2


def test_duplicate_or_missing_case_ids_are_rejected():
    """A duplicated row must not be able to inflate an opportunity count."""
    members = {
        "A/001.json": membership("A/001.json"),
        "A/002.json": membership("A/002.json"),
    }
    with pytest.raises(ValueError, match="duplicate"):
        mechanism_opportunity(
            [row("A/001.json", True, False), row("A/001.json", True, False)],
            MECHANISM, members,
        )
    with pytest.raises(ValueError, match="case ids"):
        mechanism_opportunity([row("", True, False)], MECHANISM, {"": members["A/001.json"]})


def test_unconfirmed_or_invalid_mechanism_is_not_evaluable():
    rows = [row("A/001.json", True, False)]
    members = {"A/001.json": membership("A/001.json")}
    hypothesis = mechanism_opportunity(
        rows, {**MECHANISM, "status": "hypothesis"}, members,
    )
    assert hypothesis["evaluable"] is False
    assert hypothesis["reason_code"] == "mechanism_unconfirmed"
    invalid = mechanism_opportunity(rows, {**MECHANISM, "objective": "vibes"}, members)
    assert invalid["evaluable"] is False
    assert invalid["reason_code"] == "mechanism_invalid"
    unbound = mechanism_opportunity(rows, {}, members)
    assert unbound["reason_code"] == "mechanism_invalid"


def test_case_absent_from_the_membership_map_is_unconfirmed():
    result = mechanism_opportunity(
        [row("A/001.json", True, False)], MECHANISM, {},
    )
    assert result["unconfirmed"] == 1
    assert result["repairable_errors"] == 0
    assert result["unconfirmed_cases"] == ["A/001.json"]
