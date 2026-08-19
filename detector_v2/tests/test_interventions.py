from __future__ import annotations

import json
from pathlib import Path

import pytest

from detector_v2.api import Detector
from detector_v2.interventions import (
    InterventionPlan,
    build_intervention_registry,
    load_intervention_registry,
    validate_registry,
)
from detector_v2.predicates import (
    PREDICATE_VERDICTS,
    run_predicate,
)

CONFIG = {
    "judge_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "defense_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "recall_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
}


def _candidate(**overrides):
    payload = {
        "schema_version": "detector_v2.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "text_boundary",
        "hook": "scanner.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "directive": "Report every required action missing a tool call.",
            "not_when": "Never apply when all required actions are present.",
        },
        "claim": {
            "expected_behavior": "scanner candidates cover missing required actions",
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-test",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    payload.update(overrides)
    return payload


def test_registry_validation_and_prompt_injection(sample_packet):
    registry = build_intervention_registry([_candidate()])
    validate_registry(registry)
    plan = InterventionPlan.from_registry(registry)
    block = plan.prompt_block({"has_tool_calls"}, hook="scanner.system")
    assert "DEPLOYED INTERVENTIONS" in block
    assert "required action" in block
    assert plan.prompt_block(set(), hook="scanner.system") == ""
    assert plan.deployment_summary()["fired"][0]["event"] == "text_injected"


def test_stage_a_repair_registry_is_zero_call_text_only():
    root = Path(__file__).resolve().parents[2]
    registry = load_intervention_registry(
        root / "detector_v2" / "experiments" / "v2_stageA_repair_registry.json",
        require_executable_bounds=True,
    )
    entries = registry["interventions"]
    assert {entry["type"] for entry in entries} == {"text_boundary"}
    assert {entry["hook"] for entry in entries} == {
        "scanner.system", "upgrade.system",
    }
    plan = InterventionPlan.from_registry(registry)
    assert "grounding" in plan.prompt_block(
        {"has_tool_calls"}, hook="scanner.system",
    ).lower()
    assert "materiality" in plan.prompt_block(
        {"has_tool_calls"}, hook="upgrade.system",
    ).lower()

    config = json.loads((
        root / "detector_v2" / "configs"
        / "detector_v2_autodl_stageA_repair.json"
    ).read_text(encoding="utf-8"))
    assert config["judge_route"]["votes"] == 3
    assert config["defense_route"]["votes"] == 3
    assert "votes" not in config["recall_route"]


def test_mechanical_delivery_registry_is_deterministic_only():
    root = Path(__file__).resolve().parents[2]
    registry = load_intervention_registry(
        root / "detector_v2" / "experiments" / "v2_stageA_mechanical_registry.json",
        require_executable_bounds=True,
    )
    entries = registry["interventions"]
    assert {entry["type"] for entry in entries} == {
        "evidence_check", "decision_guard",
    }
    assert {entry["body"].get("runner") for entry in entries
            if entry["type"] == "evidence_check"} == {"deterministic"}


def test_evidence_check_only_append_evidence(sample_packet):
    entry = _candidate(
        intervention_id="IV-E",
        type="evidence_check",
        hook="judge.post",
        body={
            "predicate": "delivered_anchor_consistency",
            "runner": "deterministic",
            "output_verdicts": ["consistent", "inconsistent", "insufficient"],
        },
    )
    registry = build_intervention_registry([entry])
    plan = InterventionPlan.from_registry(registry)
    evidence = plan.evidence_for(
        "judge.post",
        sample_packet,
        {"delivered_result": "The answer is 4."},
    )
    assert evidence
    assert evidence[0]["predicate"] == "delivered_anchor_consistency"
    assert evidence[0]["verdict"] in PREDICATE_VERDICTS[
        "delivered_anchor_consistency"
    ]
    assert plan.deployment_summary()["evidence"]


def test_guard_conflict_disables_all_actions(sample_packet):
    first = _candidate(
        intervention_id="IV-G1",
        type="decision_guard",
        hook="adjudicate.guard",
        body={
            "action": "withhold_conviction",
            "action_not_when": "Never when defense substantiated with resolved refs.",
            "action_not_when_facts": ["defense_verdict=substantiated"],
        },
    )
    second = _candidate(
        intervention_id="IV-G2",
        type="decision_guard",
        hook="adjudicate.guard",
        body={
            "action": "restore_judge_conviction",
            "action_not_when": "Never when defense is substantiated.",
            "action_not_when_facts": ["defense_verdict=substantiated"],
        },
    )
    registry = build_intervention_registry([first, second])
    plan = InterventionPlan.from_registry(registry)
    actions = plan.guard_actions(
        sample_packet,
        {"judge_positive": "true", "positive_source": "judge"},
    )
    assert actions == []
    summary = plan.deployment_summary()
    assert summary["policy_conflict"] is True


def test_required_action_predicate_vocabulary(sample_packet):
    packet_with_contract = dict(sample_packet)
    packet_with_contract["question"] = (
        "Use the search tool and call the calculator tool, then answer."
    )
    result = run_predicate(
        "required_action_present", packet_with_contract, {},
    )
    assert result["verdict"] in {"satisfied", "violated"}
    assert result["predicate"] == "required_action_present"


def test_unknown_runner_or_hook_fails_closed():
    with pytest.raises(ValueError):
        validate_registry(build_intervention_registry([
            _candidate(intervention_id="bad", hook="not_a_hook.system"),
        ]))
    with pytest.raises(ValueError):
        validate_registry(build_intervention_registry([
            _candidate(
                intervention_id="bad",
                type="evidence_check",
                hook="judge.post",
                body={
                    "predicate": "required_action_present",
                    "runner": "llm_structured",
                    "output_verdicts": ["satisfied", "violated"],
                },
            ),
        ]))


def test_detector_detect_writes_proof_deployment(sample_record, monkeypatch):
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "The answer is 4.",
            "correct_result": "4",
            "basis": None,
            "confidence": "high",
            "evidence_refs": [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        return {"ok": True, "candidates": []}, {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        }

    def fake_upgrade(*, packet, candidates, config, plan):
        return {"ok": True, "upgrades": [], "upgraded_candidate": None}, {
            "stage": "causal_upgrade", "state": "not_needed", "attempts": 0,
            "view": "not_rendered", "warnings": [],
        }

    monkeypatch.setattr("detector_v2.api.run_judge", fake_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    registry = build_intervention_registry([
        _candidate(
            intervention_id="IV-E2",
            type="evidence_check",
            hook="judge.post",
            body={
                "predicate": "delivered_anchor_consistency",
                "runner": "deterministic",
                "output_verdicts": [
                    "consistent", "inconsistent", "insufficient",
                ],
            },
        ),
    ])
    result = Detector(CONFIG).detect(
        sample_record, case_id="x", interventions=registry,
    )
    deployment = result["proof"]["deployment"]
    assert deployment["active_interventions"] == ["IV-E2"]
    assert deployment["evidence"]
    assert deployment["evidence"][0]["intervention_id"] == "IV-E2"


def test_frozen_judge_replay_skips_judge_provider(sample_record, monkeypatch):
    frozen = {
        "judge_result": {
            "ok": True,
            "parsed": {
                "is_hallucination": False,
                "hallucination_step": None,
                "hallucination_reason": None,
                "delivered_result": "4",
                "correct_result": "4",
                "basis": None,
                "confidence": "high",
                "evidence_refs": [],
                "root_cause": None,
                "contracts": [{"kind": "exact_value", "requirement": "4"}],
            },
        },
        "judge_status": {
            "stage": "judge", "state": "ok", "attempts": 1,
            "view": "complete", "warnings": [],
        },
    }

    def fail_judge(*, packet, config, plan):
        raise AssertionError("frozen_judge replay must not call the judge provider")

    def fake_recall(*, packet, config, plan):
        return {"ok": True, "candidates": []}, {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        }

    def fake_upgrade(*, packet, candidates, config, plan):
        return {"ok": True, "upgrades": [], "upgraded_candidate": None}, {
            "stage": "causal_upgrade", "state": "not_needed", "attempts": 0,
            "view": "not_rendered", "warnings": [],
        }

    monkeypatch.setattr("detector_v2.api.run_judge", fail_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    result = Detector(CONFIG).detect(
        sample_record, case_id="x", frozen_judge=frozen,
    )
    assert result["state"] == "verified_clean"
    assert result["provenance"]["frozen_judge_replay"] is True


def test_frozen_judge_refuses_judge_hook_intervention(sample_record):
    frozen = {
        "judge_result": {
            "ok": True,
            "parsed": {
                "is_hallucination": False,
                "hallucination_step": None,
                "hallucination_reason": None,
                "delivered_result": "4",
                "correct_result": "4",
                "basis": None,
                "confidence": "high",
                "evidence_refs": [],
                "root_cause": None,
                "contracts": [{"kind": "exact_value", "requirement": "4"}],
            },
        },
        "judge_status": {
            "stage": "judge", "state": "ok", "attempts": 1,
            "view": "complete", "warnings": [],
        },
    }
    registry = build_intervention_registry([_candidate(
        intervention_id="IV-J",
        hook="judge.system",
    )])
    with pytest.raises(ValueError):
        Detector(CONFIG).detect(
            sample_record, case_id="x", interventions=registry,
            frozen_judge=frozen,
        )


def test_frozen_replay_refuses_masked_later_stage(sample_record):
    frozen = {
        "judge_result": {
            "ok": True,
            "parsed": {
                "is_hallucination": False,
                "hallucination_step": None,
                "hallucination_reason": None,
                "delivered_result": "4",
                "correct_result": "4",
                "basis": None,
                "confidence": "high",
                "evidence_refs": [],
                "root_cause": None,
                "contracts": [{"kind": "exact_value", "requirement": "4"}],
            },
        },
        "judge_status": {
            "stage": "judge", "state": "ok", "attempts": 1,
            "view": "complete", "warnings": [],
        },
        "recall_result": {"ok": True, "candidates": []},
        "recall_status": {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        },
    }
    registry = build_intervention_registry([_candidate(
        intervention_id="IV-S",
        hook="scanner.system",
    )])
    with pytest.raises(ValueError, match="mask"):
        Detector(CONFIG).detect(
            sample_record, case_id="x", interventions=registry,
            frozen_stages=frozen,
        )


def test_frozen_judge_rejects_invalid_contract(sample_record):
    frozen = {
        "judge_result": {
            "ok": True,
            "parsed": {
                "is_hallucination": "true",
            },
        },
        "judge_status": {"stage": "judge", "state": "ok"},
    }
    with pytest.raises(ValueError, match="frozen judge_result contract"):
        Detector(CONFIG).detect(sample_record, frozen_judge=frozen)
