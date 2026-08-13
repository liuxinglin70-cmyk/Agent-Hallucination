"""B5 localization-family acceptance: the earliest-origin transform writes
back the localization fact (closing the trigger source), and the effect
contract directions ``shift`` / ``opened`` observe the delta's real movement
tokens."""

from __future__ import annotations

from sehd_rise.causal.cut import (
    REASON_LOCALIZATION_FALLBACK,
    build_causal_cut,
)
from sehd_rise.causal.repairability import triggered_episode
from sehd_rise.causal.signals import project_detection
from sehd_rise.evaluation.effect import _expected_effect_audit
from sehd_rise.evaluation.fitness import (
    CONTRACT_DIRECTION_DELTA_TOKENS,
    FitnessAssessment,
)
from sehd_rise.skills.action import compile_action_spec
from sehd_rise.skills.binding import Binding
from sehd_rise.skills.genome import SkillEdit
from sehd_rise.skills.l1_execution import execute_l1_intervention
from sehd_rise.skills.vocabulary.contracts import LAYER_NONE


def _localization_result() -> dict:
    certificates = [
        {
            "certificate_id": "e2",
            "source_candidate_id": "c2",
            "origin_event_id": "event_002",
        },
        {
            "certificate_id": "e1",
            "source_candidate_id": "c1",
            "origin_event_id": "event_001",
        },
    ]
    return {
        "state": "verified_error",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_002",
        "responsible_step": 2,
        "census": [
            {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"},
            {"candidate_id": "c2", "origin_hypothesis_event_id": "event_002"},
        ],
        "prosecution": {"coverage_state": "complete", "candidates": certificates},
        "combined_prosecution": {
            "coverage_state": "complete", "candidates": certificates,
        },
        "defense": {"coverage_state": "incomplete", "candidates": []},
        "proof_adjudication": {
            "state": "verified_error",
            "decision_authority": "authoritative",
            "responsible_event_id": "event_002",
            "localization_status": "failed_temporal_fallback",
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": ["e2", "e1"],
            "valid_error_certificate_count": 2,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }


def _select_earliest(result: dict) -> dict:
    spec = compile_action_spec(
        "earliest_origin_tiebreak", "select_earliest_origin: responsible_event_id",
    )
    binding = Binding(
        domain="origin", entity_id="event_001",
        identifiers={"origin_event_id": "event_001"},
    )
    mutated, report = execute_l1_intervention(
        result, spec=spec, bindings=(binding,),
    )
    assert report["fired"] is True
    return mutated


def test_select_earliest_origin_writes_back_localization_status() -> None:
    mutated = _select_earliest(_localization_result())
    proof = mutated["proof_adjudication"]
    assert proof["responsible_event_id"] == "event_001"
    assert proof["localization_status"] == "complete"


def test_select_earliest_origin_closes_the_trigger_source() -> None:
    before = _localization_result()
    before_cut = build_causal_cut(project_detection(before, use_gold=False))
    assert before_cut.reason_code == REASON_LOCALIZATION_FALLBACK
    after = _select_earliest(before)
    after_cut = build_causal_cut(project_detection(after, use_gold=False))
    assert after_cut.reason_code != REASON_LOCALIZATION_FALLBACK
    assert after_cut.skill_layer == LAYER_NONE
    assert triggered_episode(after, use_gold=False, gold_label=None) is False


def _contract_edit(observable_field: str, expected_direction: str) -> SkillEdit:
    return SkillEdit(
        edit_id="edit_1",
        target_module="earliest_origin_tiebreak",
        operation="add_rule",
        expected_effect_contract={
            "observable_field": observable_field,
            "expected_direction": expected_direction,
            "expected_scope": "all_cases",
            "trigger_condition": "case_triggered",
        },
    )


def test_shift_contract_observes_origin_movement() -> None:
    before = _localization_result()
    after = _select_earliest(before)
    assert "shifted" in CONTRACT_DIRECTION_DELTA_TOKENS["shift"]
    audit = _expected_effect_audit(
        _contract_edit("responsible_step", "shift"),
        FitnessAssessment("directional_evidence", ()),
        before=project_detection(before, use_gold=False),
        after=project_detection(after, use_gold=False),
        before_result=before,
        after_result=after,
    )
    assert "responsible_step:shifted" in audit["observable_deltas"]
    assert audit["contract_status"] == "observed"


def test_opened_contract_observes_coverage_drop() -> None:
    before = _localization_result()
    after = _localization_result()
    after["proof_adjudication"]["prosecution_coverage"] = "incomplete"
    audit = _expected_effect_audit(
        _contract_edit("prosecution_coverage", "opened"),
        FitnessAssessment("directional_evidence", ()),
        before=project_detection(before, use_gold=False),
        after=project_detection(after, use_gold=False),
        before_result=before,
        after_result=after,
    )
    assert "prosecution_coverage:decreased" in audit["observable_deltas"]
    assert audit["contract_status"] == "observed"
