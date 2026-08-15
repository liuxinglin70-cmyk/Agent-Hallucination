"""L2 policy candidate set validation: schema, vocabulary and coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sehd_rise2.evolution.policy import (
    CERTIFICATE_CLASSES,
    POLICY_ACTIONS,
    POLICY_CANDIDATES_SCHEMA,
    load_case_facts,
    policy_fp_case_ids,
    validate_policy_set,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SET = (
    REPO_ROOT / "datasets/policy_candidates/v1/policy_candidates.json"
)
REAL_CASES = REPO_ROOT / "datasets/learning_cases/v1/cases"


def _facts() -> dict[str, dict]:
    return {
        "BFCL/114.json": {
            "failure_type": "FP",
            "gold": False,
            "decision": "verified_error",
            "gap_category": "policy",
        },
        "BFCL/127.json": {
            "failure_type": "FP",
            "gold": False,
            "decision": "verified_error",
            "gap_category": "policy",
        },
        "BFCL/142.json": {
            "failure_type": "FP",
            "gold": False,
            "decision": "verified_error",
            "gap_category": "policy",
        },
        "BFCL/067.json": {
            "failure_type": "FN",
            "gold": True,
            "decision": "verified_clean",
            "gap_category": "observation",
        },
        "Camel/039.json": {
            "failure_type": None,
            "gold": True,
            "decision": "verified_error",
            "gap_category": None,
        },
        "Camel/053.json": {
            "failure_type": "FN",
            "gold": True,
            "decision": "verified_clean",
            "gap_category": "policy",
        },
    }


def _policy(**overrides):
    policy = {
        "policy_id": "L2-P1",
        "title": "t",
        "hypothesis": {
            "statement": "s",
            "source_gap_names": ["strictness_miscalibration"],
            "case_evidence": [
                {"case_id": "BFCL/114.json", "certificate_id": "c1"},
                {"case_id": "BFCL/127.json", "certificate_id": "c1"},
                {"case_id": "BFCL/142.json", "certificate_id": "c1"},
            ],
            "cross_case_mechanism": "m",
        },
        "trigger": {
            "certificate_class": "expression_variance",
            "definition": "a prose restatement differs only in wording",
            "must_not_match": [],
        },
        "action": "withhold_conviction",
        "expected_effect": {
            "fp_cases": [
                "BFCL/114.json", "BFCL/127.json", "BFCL/142.json",
            ],
            "estimated_fp_reduction": 3,
            "fn_risk": "r",
            "tp_must_survive": [
                {"case_id": "Camel/039.json", "why": "w"},
            ],
        },
        "risk_boundary": {
            "statement": "b",
            "do_not_apply": ["x"],
        },
        "status": "candidate",
    }
    policy.update(overrides)
    return policy


def _payload(policies, **overrides):
    payload = {
        "schema_version": POLICY_CANDIDATES_SCHEMA,
        "review_status": "first_pass",
        "policies": policies,
    }
    payload.update(overrides)
    return payload


def _check(payload, name):
    checks = validate_policy_set(payload, _facts())
    return checks[name]


def test_minimal_set_passes_all_checks():
    checks = validate_policy_set(_payload([_policy()]), _facts())
    failed = [name for name, ok in checks.items() if not ok]
    assert failed == []


def test_fp_ids_come_from_policy_gap_false_positives_only():
    assert policy_fp_case_ids(_facts()) == {
        "BFCL/114.json", "BFCL/127.json", "BFCL/142.json",
    }


def test_unknown_certificate_class_rejected():
    policy = _policy(
        trigger={
            "certificate_class": "vibe_based",
            "definition": "d",
            "must_not_match": [],
        },
    )
    assert not _check(_payload([policy]), "classes_in_vocabulary")


def test_unknown_action_and_status_rejected():
    assert not _check(
        _payload([_policy(action="ignore")]), "actions_in_vocabulary",
    )
    assert not _check(
        _payload([_policy(status="draft")]), "statuses_in_vocabulary",
    )


def test_gap_name_from_another_category_rejected():
    policy = _policy(
        hypothesis={
            "statement": "s",
            "source_gap_names": ["missing_required_action"],
            "case_evidence": [
                {"case_id": "BFCL/114.json"},
                {"case_id": "BFCL/127.json"},
            ],
            "cross_case_mechanism": "m",
        },
    )
    assert not _check(
        _payload([policy]), "gap_names_from_policy_category",
    )


def test_single_case_policy_rejected():
    policy = _policy(
        hypothesis={
            "statement": "s",
            "source_gap_names": ["strictness_miscalibration"],
            "case_evidence": [{"case_id": "BFCL/114.json"}],
            "cross_case_mechanism": "m",
        },
    )
    assert not _check(_payload([policy]), "multi_case_support")


def test_evidence_on_non_policy_case_rejected():
    policy = _policy(
        hypothesis={
            "statement": "s",
            "source_gap_names": ["strictness_miscalibration"],
            "case_evidence": [
                {"case_id": "BFCL/114.json"},
                {"case_id": "BFCL/067.json"},
            ],
            "cross_case_mechanism": "m",
        },
    )
    assert not _check(_payload([policy]), "evidence_cases_are_policy_fp")


def test_fp_target_must_be_a_policy_false_positive():
    policy = _policy(
        expected_effect={
            "fp_cases": ["BFCL/114.json", "Camel/053.json"],
            "estimated_fp_reduction": 2,
            "fn_risk": "r",
            "tp_must_survive": [],
        },
    )
    assert not _check(_payload([policy]), "fp_targets_are_policy_fp")


def test_estimate_must_match_target_count():
    policy = _policy(
        expected_effect={
            "fp_cases": ["BFCL/114.json", "BFCL/127.json"],
            "estimated_fp_reduction": 3,
            "fn_risk": "r",
            "tp_must_survive": [],
        },
    )
    assert not _check(_payload([policy]), "fp_estimates_consistent")


def test_fn_risk_and_boundary_required():
    policy = _policy(
        expected_effect={
            "fp_cases": ["BFCL/114.json", "BFCL/127.json"],
            "estimated_fp_reduction": 2,
            "fn_risk": "",
            "tp_must_survive": [],
        },
    )
    assert not _check(_payload([policy]), "fn_risk_stated")
    policy = _policy(risk_boundary={"statement": "", "do_not_apply": ["x"]})
    assert not _check(_payload([policy]), "risk_boundary_stated")
    policy = _policy(
        risk_boundary={"statement": "b", "do_not_apply": []},
    )
    assert not _check(_payload([policy]), "risk_boundary_stated")


def test_trigger_definition_must_stay_class_level():
    policy = _policy(
        trigger={
            "certificate_class": "expression_variance",
            "definition": (
                "fires when the case is BFCL/114.json exactly"
            ),
            "must_not_match": [],
        },
    )
    assert not _check(_payload([policy]), "trigger_is_class_level")


def test_unknown_case_reference_rejected():
    policy = _policy(
        risk_boundary={
            "statement": "b",
            "do_not_apply": ["BFCL/999.json"],
        },
    )
    assert not _check(_payload([policy]), "referenced_cases_known")


def test_uncovered_false_positive_needs_unaddressed_entry():
    partial = _policy(
        hypothesis={
            "statement": "s",
            "source_gap_names": ["strictness_miscalibration"],
            "case_evidence": [
                {"case_id": "BFCL/114.json"},
                {"case_id": "BFCL/127.json"},
            ],
            "cross_case_mechanism": "m",
        },
        expected_effect={
            "fp_cases": ["BFCL/114.json", "BFCL/127.json"],
            "estimated_fp_reduction": 2,
            "fn_risk": "r",
            "tp_must_survive": [],
        },
    )
    payload = _payload([partial])
    assert not _check(payload, "fp_coverage_complete")
    payload = _payload(
        [partial],
        unaddressed_cases=[
            {"case_id": "BFCL/142.json", "reason": "no shared mechanism"},
        ],
    )
    assert _check(payload, "fp_coverage_complete")


def test_tp_example_must_be_a_convicted_gold_case():
    policy = _policy(
        expected_effect={
            "fp_cases": ["BFCL/114.json", "BFCL/127.json"],
            "estimated_fp_reduction": 2,
            "fn_risk": "r",
            "tp_must_survive": [{"case_id": "BFCL/067.json", "why": "w"}],
        },
    )
    assert not _check(_payload([policy]), "tp_examples_convicted")


def test_duplicate_policy_ids_rejected():
    payload = _payload([_policy(), _policy(policy_id="L2-P1")])
    assert not _check(payload, "policy_ids_unique")


@pytest.mark.skipif(
    not REAL_SET.exists() or not REAL_CASES.exists(),
    reason="policy candidate dataset not assembled",
)
def test_real_candidate_set_validates():
    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    checks = validate_policy_set(payload, load_case_facts(REAL_CASES))
    failed = [name for name, ok in checks.items() if not ok]
    assert failed == []


@pytest.mark.skipif(
    not REAL_SET.exists() or not REAL_CASES.exists(),
    reason="policy candidate dataset not assembled",
)
def test_real_candidate_set_accounts_for_all_twelve_false_positives():
    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    fp_ids = policy_fp_case_ids(load_case_facts(REAL_CASES))
    assert len(fp_ids) == 12
    covered = {
        case_id
        for policy in payload["policies"]
        for case_id in policy["expected_effect"]["fp_cases"]
    }
    abstained = {
        case_id
        for finding in payload["non_separable_findings"]
        for case_id in finding["evidence_cases"]
    }
    assert fp_ids <= covered | abstained
    assert "OpenManus/048.json" in abstained
    assert "OpenManus/048.json" not in covered


def test_vocabulary_covers_declared_classes_and_actions():
    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    for policy in payload["policies"]:
        assert (
            policy["trigger"]["certificate_class"] in CERTIFICATE_CLASSES
        )
        assert policy["action"] in POLICY_ACTIONS


def test_genome_builds_and_loads_through_detector_reader(tmp_path):
    from sehd_rise2.detector.skills import load_policy_genome
    from sehd_rise2.evolution.policy import build_policy_genome

    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    genome = build_policy_genome(payload, ["L2-P2", "L2-P3", "L2-P4", "L2-P5"])
    assert genome["schema_version"] == "sehd2.policy_genome.v1"
    assert [item["policy_id"] for item in genome["items"]] == [
        "L2-P2", "L2-P3", "L2-P4", "L2-P5",
    ]
    for item in genome["items"]:
        assert item["action"] in POLICY_ACTIONS
        assert item["target_certificate_class"] in CERTIFICATE_CLASSES
        assert item["precondition"] and item["risk_boundary"]["statement"]
        assert item["provenance"]["failure_cases"]
    path = tmp_path / "policy_genome.json"
    path.write_text(json.dumps(genome), encoding="utf-8")
    loaded = load_policy_genome(path)
    assert loaded["content_hash"] == genome["content_hash"]


def test_genome_defaults_to_replayed_policies_only():
    from sehd_rise2.evolution.policy import build_policy_genome

    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    genome = build_policy_genome(payload)
    assert [
        item["policy_id"] for item in genome["items"]
    ] == ["L2-P1", "L2-P2", "L2-P3", "L2-P4", "L2-P5"]
    promoted_only = build_policy_genome(
        payload, ["L2-P2", "L2-P3", "L2-P4", "L2-P5"],
    )
    assert "L2-P1" not in {
        item["policy_id"] for item in promoted_only["items"]
    }


def test_genome_rejects_unknown_policy_ids():
    from sehd_rise2.evolution.policy import build_policy_genome

    payload = json.loads(REAL_SET.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="unknown policy ids"):
        build_policy_genome(payload, ["L2-P9"])


def test_findings_must_span_both_gold_polarities():
    checks = validate_policy_set(
        _payload([_policy()], non_separable_findings=[
            {
                "finding_id": "NS-x",
                "mechanism": "m",
                "evidence_cases": ["BFCL/114.json", "BFCL/127.json"],
                "finding": "f",
                "disposition": "d",
                "status": "non_separable_under_current_representation",
            },
        ]),
        _facts(),
    )
    assert not checks["findings_span_polarities"]
    assert checks["findings_reference_known"]
    checks = validate_policy_set(
        _payload([_policy()], non_separable_findings=[
            {
                "finding_id": "NS-x",
                "mechanism": "m",
                "evidence_cases": ["BFCL/114.json", "Camel/039.json"],
                "finding": "f",
                "disposition": "d",
                "status": "non_separable_under_current_representation",
            },
        ]),
        _facts(),
    )
    assert checks["findings_span_polarities"]
    assert checks["findings_status_vocabulary"]
