"""Failure-stage derivation rules and the 24-case audited regression."""

from __future__ import annotations

import pytest

from sehd_rise2.evolution.diagnosis import (
    classify_failure,
    derive_failure_stage,
    localization_facts,
    validate_capability_diagnosis,
)

# Audited against runs/random100_20260815_142446 (decisive facts read from
# terminal_basis; stages derived by the rule table). Any detector change
# that shifts these numbers must be a conscious decision, not drift.
FN_ROWS = [
    ("BFCL/104.json", 0, 0, 0, "census"),
    ("OpenManus/060.json", 0, 0, 0, "census"),
    ("BFCL/159.json", 1, 0, 0, "verification"),
    ("OpenDeepSearch/061.json", 2, 0, 0, "verification"),
    ("OpenManus/093.json", 1, 0, 0, "verification"),
    ("SmolAgents/002.json", 1, 0, 0, "verification"),
    ("BFCL/067.json", 1, 1, 0, "defense"),
    ("BFCL/071.json", 2, 1, 0, "defense"),
    ("BFCL/103.json", 3, 2, 0, "defense"),
    ("Camel/053.json", 1, 1, 0, "defense"),
    ("Magentic_One/024.json", 1, 1, 0, "defense"),
    ("SmolAgents/031.json", 2, 1, 0, "defense"),
]

FP_ROWS = [
    ("BFCL/114.json", 2, 1, 1),
    ("BFCL/127.json", 3, 2, 2),
    ("BFCL/142.json", 2, 1, 1),
    ("Camel/061.json", 1, 1, 1),
    ("Camel/064.json", 4, 2, 1),
    ("Magentic_One/014.json", 3, 3, 1),
    ("Magentic_One/065.json", 2, 2, 2),
    ("Magentic_One/066.json", 3, 2, 1),
    ("Octotools/016.json", 2, 2, 1),
    ("Octotools/028.json", 4, 2, 2),
    ("OpenManus/048.json", 1, 1, 1),
    ("SmolAgents/082.json", 1, 1, 1),
]


def _facts(candidates: int, supported: int, errors: int) -> dict:
    return {
        "candidate_count": candidates,
        "certificate_count": candidates,
        "supported_certificate_count": supported,
        "error_certificate_count": errors,
        "complete_chain_certificate_count": errors,
    }


@pytest.mark.parametrize(
    "case_id, candidates, supported, errors, expected", FN_ROWS,
)
def test_fn_regression(case_id, candidates, supported, errors, expected):
    stage, fact = derive_failure_stage(
        failure_type="FN", decisive_facts=_facts(candidates, supported, errors),
    )
    assert stage == expected
    assert fact


@pytest.mark.parametrize(
    "case_id, candidates, supported, errors", FP_ROWS,
)
def test_fp_regression(case_id, candidates, supported, errors):
    stage, _fact = derive_failure_stage(
        failure_type="FP", decisive_facts=_facts(candidates, supported, errors),
    )
    assert stage == "undetermined"


def test_fn_stage_distribution_snapshot():
    stages = {
        row[4] for row in FN_ROWS
    }
    counts = {stage: sum(1 for row in FN_ROWS if row[4] == stage) for stage in stages}
    assert counts == {
        "census": 2, "verification": 4, "defense": 6,
    }


def test_infra_and_correct():
    assert derive_failure_stage(
        failure_type="INFRA", decisive_facts={},
    )[0] == "infrastructural"
    assert derive_failure_stage(
        failure_type="CORRECT", decisive_facts={},
    )[0] == "none"


def test_localization_stage():
    assert derive_failure_stage(
        failure_type="LOCALIZATION", decisive_facts={},
    )[0] == "adjudication"


def test_classify_failure_matrix():
    assert classify_failure(
        gold=True, prediction=False, gold_step=3, responsible_step=None,
    ) == "FN"
    assert classify_failure(
        gold=False, prediction=True, gold_step=None, responsible_step=7,
    ) == "FP"
    assert classify_failure(
        gold=True, prediction=True, gold_step=3, responsible_step=5,
    ) == "LOCALIZATION"
    assert classify_failure(
        gold=True, prediction=True, gold_step=3, responsible_step=3,
    ) == "CORRECT"
    assert classify_failure(
        gold=True, prediction=None, gold_step=3, responsible_step=None,
    ) == "INFRA"


def test_localization_facts_three_branches():
    upheld_elsewhere = [{"candidate_id": "c1", "position": 9, "upheld": True}]
    assert "no candidate ever carried" in localization_facts(
        gold_step=3, loci=upheld_elsewhere,
    )
    died_early = [
        {"candidate_id": "c1", "position": 3, "upheld": False},
        {"candidate_id": "c2", "position": 9, "upheld": True},
    ]
    assert "did not survive the chain" in localization_facts(
        gold_step=3, loci=died_early,
    )
    lost_selection = [
        {"candidate_id": "c1", "position": 3, "upheld": True},
        {"candidate_id": "c2", "position": 9, "upheld": True},
    ]
    assert "earliest-locus selection picked another step" in (
        localization_facts(gold_step=9, loci=lost_selection)
    )
    assert "cannot be scored" in localization_facts(
        gold_step=None, loci=[],
    )


def _diagnosis(**overrides):
    diagnosis = {
        "schema_version": "sehd2.capability_diagnosis.v1",
        "case_id": "BFCL/067.json",
        "failure_type": "FN",
        "failed_stage": "defense",
        "diagnosis_source": "mixed",
        "capability_gap": {
            "category": "policy",
            "name": "adversarial_defense_overturn",
            "status": "known",
        },
        "generalization": "cross_case",
        "recommended_evolution_target": "l2_policy",
    }
    diagnosis.update(overrides)
    return diagnosis


def test_capability_diagnosis_protocol_valid():
    assert all(validate_capability_diagnosis(_diagnosis()).values())


def test_capability_diagnosis_has_no_confidence_field():
    assert "confidence" not in _diagnosis()


def test_capability_diagnosis_rejects_wrong_target():
    checks = validate_capability_diagnosis(
        _diagnosis(recommended_evolution_target="l1_candidate"),
    )
    assert checks["evolution_target_consistent"] is False


def test_capability_diagnosis_rejects_unknown_source():
    checks = validate_capability_diagnosis(
        _diagnosis(diagnosis_source="gut_feeling"),
    )
    assert checks["diagnosis_source_in_vocabulary"] is False


def test_capability_diagnosis_proposal_channel_closed_when_frozen(
    monkeypatch,
):
    from sehd_rise2.evolution import gap_taxonomy

    proposed = _diagnosis(
        capability_gap={
            "category": "policy", "name": "novel_gap", "status": "proposed",
        },
    )
    assert not validate_capability_diagnosis(proposed)[
        "capability_name_vocabulary"
    ]
    monkeypatch.setattr(gap_taxonomy, "TAXONOMY_FROZEN", False)
    assert validate_capability_diagnosis(proposed)[
        "capability_name_vocabulary"
    ]
