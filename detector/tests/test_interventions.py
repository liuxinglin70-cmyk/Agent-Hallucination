from __future__ import annotations

import json
from pathlib import Path

import pytest

from detector.api import Detector
from detector.interventions import (
    InterventionPlan,
    build_intervention_registry,
    load_intervention_registry,
    validate_registry,
)
from detector.predicates import (
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
        "schema_version": "detector.intervention.v1",
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


def test_text_boundary_fixture_is_zero_call_text_only():
    root = Path(__file__).resolve().parents[2]
    registry = load_intervention_registry(
        root / "detector" / "tests" / "fixtures" / "text_boundaries.json",
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
        root / "detector" / "configs"
        / "autodl.json"
    ).read_text(encoding="utf-8"))
    assert config["judge_route"]["votes"] == 3
    assert config["defense_route"]["votes"] == 3
    assert "votes" not in config["recall_route"]


def test_mechanical_delivery_registry_is_deterministic_only():
    root = Path(__file__).resolve().parents[2]
    registry = load_intervention_registry(
        root / "detector" / "tests" / "fixtures" / "mechanical_evidence.json",
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


def test_guard_matches_one_coherent_hearing_fact_row(sample_packet):
    restore = _candidate(
        intervention_id="IV-RESTORE",
        type="decision_guard",
        hook="adjudicate.guard",
        trigger={
            "packet_features": ["has_tool_calls"],
            "facts": [
                "positive_source=judge",
                "defense_verdict=refuted",
            ],
        },
        body={
            "action": "restore_judge_conviction",
            "action_not_when": "Never when defense substantiated.",
            "action_not_when_facts": ["defense_verdict=substantiated"],
        },
    )
    plan = InterventionPlan.from_registry(build_intervention_registry([restore]))
    actions = plan.guard_actions(
        sample_packet,
        fact_rows=[
            {"positive_source": "judge", "defense_verdict": "refuted"},
            {
                "positive_source": "recall_upgrade",
                "defense_verdict": "substantiated",
            },
        ],
    )
    assert actions == ["restore_judge_conviction"]


def test_mechanical_guard_requires_bound_violating_evidence(sample_packet):
    evidence = _candidate(
        intervention_id="IV-E-M",
        type="evidence_check",
        hook="judge.post",
        body={
            "predicate": "required_action_present",
            "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
        },
    )
    guard = _candidate(
        intervention_id="IV-G-M",
        type="decision_guard",
        hook="adjudicate.guard",
        trigger={
            "packet_features": ["has_tool_calls"],
            "facts": [
                "required_action_present=violated",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        body={
            "action": "convict_mechanical_violation",
            "evidence_predicate": "required_action_present",
            "action_not_when": "Never when the required action is satisfied.",
            "action_not_when_facts": ["required_action_present=satisfied"],
        },
    )
    plan = InterventionPlan.from_registry(
        build_intervention_registry([evidence, guard])
    )
    assert plan.guard_actions(sample_packet, {
        "required_action_present": "violated",
        "judge_positive": "false",
        "positive_source": "none",
    }) == []
    assert plan.deployment_summary()["fired"][0]["event"] == "guard_suppressed"

    evidence_rows = plan.evidence_for(
        "judge.post", sample_packet,
        {"contracts": [], "delivered_result": "The answer is 4."},
    )
    assert [row["predicate"] for row in evidence_rows] == [
        "required_action_present"
    ]


def test_mechanical_guard_without_evidence_partner_is_rejected_before_runtime():
    guard = _candidate(
        intervention_id="IV-G-M",
        type="decision_guard",
        hook="adjudicate.guard",
        trigger={
            "packet_features": ["has_tool_calls"],
            "facts": [
                "required_action_present=violated",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        body={
            "action": "convict_mechanical_violation",
            "evidence_predicate": "required_action_present",
            "action_not_when": "Never when the required action is satisfied.",
            "action_not_when_facts": ["required_action_present=satisfied"],
        },
    )
    with pytest.raises(ValueError, match="same-family evidence_check"):
        build_intervention_registry([guard])


def test_mechanical_guard_rejects_diagnostic_only_predicate():
    evidence = _candidate(
        intervention_id="IV-E-DIAG",
        type="evidence_check",
        hook="judge.post",
        body={
            "predicate": "delivered_anchor_consistency",
            "runner": "deterministic",
            "output_verdicts": ["consistent", "inconsistent", "insufficient"],
        },
    )
    guard = _candidate(
        intervention_id="IV-G-DIAG",
        type="decision_guard",
        hook="adjudicate.guard",
        trigger={
            "packet_features": ["has_tool_calls"],
            "facts": [
                "delivered_anchor_consistency=inconsistent",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        body={
            "action": "convict_mechanical_violation",
            "evidence_predicate": "delivered_anchor_consistency",
            "action_not_when": "Never when the anchor is consistent.",
            "action_not_when_facts": [
                "delivered_anchor_consistency=consistent",
            ],
        },
    )
    with pytest.raises(ValueError, match="conviction-authorized verdict"):
        build_intervention_registry([evidence, guard])


def test_mechanical_guard_cannot_consume_other_family_evidence(sample_packet):
    evidence = _candidate(
        intervention_id="IV-E-OTHER",
        type="evidence_check",
        hook="judge.post",
        body={
            "predicate": "required_action_present",
            "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
        },
        provenance={
            "cycle": 1,
            "failure_family": "FAM-other",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    )
    guard = _candidate(
        intervention_id="IV-G-THIS",
        type="decision_guard",
        hook="adjudicate.guard",
        trigger={
            "packet_features": ["has_tool_calls"],
            "facts": [
                "required_action_present=violated",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        body={
            "action": "convict_mechanical_violation",
            "evidence_predicate": "required_action_present",
            "action_not_when": "Never when the action is satisfied.",
            "action_not_when_facts": ["required_action_present=satisfied"],
        },
        provenance={
            "cycle": 1,
            "failure_family": "FAM-this",
            "failure_cases": ["A/003.json", "A/004.json"],
        },
    )
    with pytest.raises(ValueError, match="same-family evidence_check"):
        build_intervention_registry([evidence, guard])


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

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
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

    monkeypatch.setattr("detector.api.run_judge", fail_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
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


def test_loader_requires_content_hash(tmp_path):
    """A registry read from disk must carry a verifiable hash.

    The hash check was ``if persisted and persisted != ...``, so dropping or
    omitting the field loaded silently -- exactly the hand-edited registry the
    hash exists to catch.
    """
    registry = build_intervention_registry([_candidate()])
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    assert load_intervention_registry(path)

    registry.pop("content_hash")
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="no content_hash"):
        load_intervention_registry(path)


def test_loader_rejects_tampered_hash(tmp_path):
    registry = build_intervention_registry([_candidate()])
    registry["interventions"][0]["body"]["directive"] = "tampered directive"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="content_hash does not match"):
        load_intervention_registry(path)


def test_guard_eligibility_probe_records_nothing(sample_packet):
    """A read-only guard probe must not write the deployment trace.

    ``run_defense`` probed guards for eligibility as a side effect, recording
    a ``guard_fired`` event that the terminal adjudication later recorded
    again, so ``proof.deployment.fired`` listed a guard firing twice.
    """
    guard = _candidate(
        intervention_id="IV-PROBE",
        type="decision_guard",
        hook="adjudicate.guard",
        body={
            "action": "withhold_conviction",
            "action_not_when": "Never when defense substantiated.",
            "action_not_when_facts": ["defense_verdict=substantiated"],
        },
    )
    registry = build_intervention_registry([guard])
    plan = InterventionPlan.from_registry(registry)
    facts = {"judge_positive": "true", "positive_source": "judge"}

    actions = plan.guard_actions(sample_packet, facts, record=False)
    assert actions == ["withhold_conviction"]
    assert plan.fired == []
    assert plan.mechanical_guard_evidence == []
    assert plan.guard_actions_applied == []

    # The recording path still writes exactly one trace entry.
    actions = plan.guard_actions(sample_packet, facts)
    assert actions == ["withhold_conviction"]
    assert [item["event"] for item in plan.fired] == ["guard_fired"]
