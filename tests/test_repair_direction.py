"""Regression for the no_evolution direction bug: a gold-correct verdict can
still carry a process defect (temporal-fallback localization, hollow
coverage), and those cuts must repair toward process_repair instead of
reading as CAPABILITY_GAP."""

from __future__ import annotations

from typing import Mapping

from sehd_rise.causal.cut import (
    build_causal_cut,
    infer_evolution_direction,
    repair_direction,
)
from sehd_rise.causal.episode import build_causal_episode
from sehd_rise.causal.plan import build_repair_plan
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
    triggered_episode,
)
from sehd_rise.causal.signals import project_detection
from sehd_rise.skills.vocabulary.contracts import LAYER_L1


def _localization_result(gold_label: bool) -> dict:
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
        "gold_label": gold_label,
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


def _hollow_defense_result(gold_label: bool) -> dict:
    return {
        "state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": None,
        "responsible_step": None,
        "census": [{"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"}],
        "prosecution": {"coverage_state": "incomplete", "candidates": []},
        "defense": {"coverage_state": "complete", "candidates": []},
        "gold_label": gold_label,
        "proof_adjudication": {
            "state": "verified_clean",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": [],
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_blocked_ids": [],
            "consensus_gate_applied": False,
            "error_audits": [],
            "defense_audits": [],
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }


def _verdict(result: dict, *, use_gold: bool):
    from sehd_rise.causal.cut import allowed_l1_modules

    signal = project_detection(result, use_gold=use_gold)
    cut = build_causal_cut(signal)
    direction = repair_direction(signal, cut)
    frontier = (
        cut.evidence.get("artifact_frontier", ())
        if isinstance(cut.evidence, Mapping) else ()
    )
    modules = allowed_l1_modules(signal, cut.owner, frontier, cut.secondary_owner)
    episode = build_causal_episode(
        signal=signal, cut=cut, record_key="c",
        evolution_direction=direction, allowed_modules=modules,
    )
    verdict = analyze_repairability(
        episode,
        allowed_modules=(modules if cut.skill_layer == LAYER_L1 else ()),
        evolution_direction=direction,
    )
    plans = build_repair_plan(
        episode, allowed_modules=modules, evolution_direction=direction,
    )
    return signal, cut, verdict, plans


def test_repair_direction_upgrades_actionable_no_evolution() -> None:
    result = _localization_result(gold_label=True)
    signal = project_detection(result, use_gold=True)
    assert infer_evolution_direction(signal.state, signal.gold_label) == "no_evolution"
    cut = build_causal_cut(signal)
    assert cut.actionable is True
    assert repair_direction(signal, cut) == "process_repair"


def test_gold_correct_localization_fallback_is_repairable() -> None:
    """Magentic_One_011's shape: gold=error + correct verdict + temporal
    fallback must compile the localization ops, not a capability gap."""
    result = _localization_result(gold_label=True)
    _signal, cut, verdict, plans = _verdict(result, use_gold=True)
    assert cut.reason_code == "localization_temporal_fallback"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert plans and set(plans[0].legal_operations) == {
        "select_earliest_origin", "suppress_later_origin",
    }
    assert triggered_episode(result, use_gold=True, gold_label=True) is True


def test_gold_correct_hollow_defense_is_repairable() -> None:
    """OpenDeepSearch_002's shape: gold=clean + correct verdict + hollow
    defense coverage must compile the L2 policy repair."""
    result = _hollow_defense_result(gold_label=False)
    _signal, cut, verdict, plans = _verdict(result, use_gold=True)
    assert cut.reason_code == "hollow_complete_defense"
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert plans and plans[0].skill_layer == "l2_adjudication_policy"
    assert "invalidate_coverage_claim" in plans[0].legal_operations
    assert triggered_episode(result, use_gold=True, gold_label=False) is True


def test_gold_correct_defect_free_case_stays_non_evolvable() -> None:
    """No defect + gold-correct verdict must stay NON_EVOLVABLE: the
    no_evolution direction is preserved for non-actionable cuts."""
    result = _localization_result(gold_label=True)
    result["proof_adjudication"]["localization_status"] = "complete"
    result["proof_adjudication"]["responsible_event_id"] = "event_001"
    signal = project_detection(result, use_gold=True)
    cut = build_causal_cut(signal)
    assert cut.actionable is False
    assert repair_direction(signal, cut) == "no_evolution"
    assert triggered_episode(result, use_gold=True, gold_label=True) is False
