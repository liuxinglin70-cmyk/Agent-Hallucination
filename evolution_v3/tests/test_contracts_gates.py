from __future__ import annotations

import pytest

from evolution_v3.config import load_config
from evolution_v3.contracts import (
    candidate_contains_case_id,
    make_intervention,
    make_registry,
)
from evolution_v3.gates import defect_candidates, v1_structural
from detector_v2.interventions import validate_registry


def _candidate(intervention_id="IV-1-001"):
    return make_intervention(
        intervention_id=intervention_id,
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report every explicit required action without a tool call.",
            "not_when": "Never apply when all required actions have tool calls.",
        },
        expected_behavior="recall scanner gains required-action candidates",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-test",
        failure_cases=["A/001.json", "A/002.json"],
    )


def test_make_intervention_and_registry_roundtrip():
    candidate = _candidate()
    registry = make_registry([candidate])
    validate_registry(registry)
    assert registry["interventions"][0]["content_hash"]


def test_v1_rejects_case_id_leak():
    config = load_config()
    leak = _candidate()
    leak.pop("content_hash")
    leak["body"]["directive"] = "Only apply to BFCL/001.json and answer false."
    verdict = v1_structural(leak, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "case id" in verdict["reason"]


def test_v1_rejects_unregistered_executor():
    config = load_config()
    candidate = _candidate()
    candidate.pop("content_hash")
    candidate["hook"] = "does_not_exist.system"
    verdict = v1_structural(candidate, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "schema/executor" in verdict["reason"]


def test_v1_rejects_redundant_active_text():
    config = load_config()
    first = _candidate("IV-1-001")
    duplicate = _candidate("IV-1-002")
    verdict = v1_structural(
        duplicate,
        active_entries=[first],
        config=config,
    )
    assert verdict["passed"] is False
    assert "redundant" in verdict["reason"]


def test_template_proposer_diversifies_across_mixed_families():
    from evolution_v3.proposer import TemplateProposer

    batch = TemplateProposer().propose(
        [
            {
                "failure_family": "FAM-fn-judge_false_no_recall_candidates",
                "count": 2,
                "failure_cases": ["A/001.json", "A/002.json"],
            },
            {
                "failure_family": "FAM-fp-judge_conviction",
                "count": 2,
                "failure_cases": ["A/003.json", "A/004.json"],
            },
        ],
        cycle=1,
        max_proposals=3,
    )
    assert batch.candidates
    assert any(c["type"] == "decision_guard" for c in batch.candidates)
    assert any(str(c.get("hook") or "").startswith("scanner.") for c in batch.candidates)
    assert batch.joint_groups


def test_defect_candidates_have_expected_shapes():
    candidates = defect_candidates(1)
    assert len(candidates) == 2
    leak, noise = candidates
    assert candidate_contains_case_id(leak) == "BFCL/001.json"
    assert candidate_contains_case_id(noise) is None
    validate_registry(make_registry(candidates))
