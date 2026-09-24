"""Tests for atomic unit publishing, corruption recovery, and longitudinal tracking (PR-10 / C16, C17, C18)."""

from __future__ import annotations

import json
import pytest

from evolution.contracts import empty_registry, make_intervention
from evolution.memory import (
    UNIT_DRIFT_SCHEMA,
    load_unit_drift,
    record_unit_drift,
)
from evolution.registry import (
    ActiveRegistry,
    LibraryLimitError,
)
from evolution.repair_unit import (
    REPAIR_UNIT_SCHEMA,
)


def _make_sample_repair_unit(unit_id: str = "RU-TEST-01") -> dict:
    return {
        "unit_id": unit_id,
        "schema_version": REPAIR_UNIT_SCHEMA,
        "target": {
            "mechanism_id": "M_TEST_HASH",
            "objective": "detection",
            "polarity": "recall",
            "repair_operation": "require_tool_prerequisite",
        },
        "scope": {
            "contract_kind": "required_tool_action",
            "anchor_selector": "task_instruction",
            "target_selector": "terminal_delivery",
            "event_window": "causal_slice",
            "must_include": ["final_delivery"],
            "on_incomplete": "abstain",
        },
        "observation": {
            "runner": "typed_dsl",
            "predicate_name": f"evo_tool_prereq_{unit_id.lower().replace('-', '_')}",
            "input_views": ["tool_names"],
            "rules": [{
                "field": "tool_names",
                "operator": "none_contains",
                "value": "calculator",
                "verdict": "violated",
                "detail": "missing required tool",
            }],
            "default_verdict": "satisfied",
            "conviction_verdicts": ["violated"],
            "verdicts": ["satisfied", "violated", "not_applicable"],
        },
        "decision": {
            "requested_action": "convict_mechanical_violation",
            "required_evidence": [f"evo_tool_prereq_{unit_id.lower().replace('-', '_')}=violated"],
            "not_when": ["calculator_not_requested"],
            "on_uncertain": "no_new_action",
        },
        "claim": {
            "expected_behavior": "convict when required calculator tool is absent",
            "target_kind": "detection",
        },
    }


def test_publish_unit_transaction_success(tmp_path):
    reg = ActiveRegistry(tmp_path / "registry")
    unit = _make_sample_repair_unit("RU-ORDER-001")

    res = reg.publish_unit_transaction(
        unit,
        cycle=1,
        reason="initial admit",
        protocol_hash="proto_hash_v3_abc",
        approved_policy_version="v3",
    )

    assert res["content_hash"] == reg.registry_hash
    assert len(reg.entries) >= 1

    # Verify state tracking
    state = reg.state
    assert "units" in state
    assert "RU-ORDER-001" in state["units"]
    unit_rec = state["units"]["RU-ORDER-001"]
    assert unit_rec["status"] == "active"
    assert unit_rec["admitted_cycle"] == 1
    assert unit_rec["protocol_hash"] == "proto_hash_v3_abc"
    assert unit_rec["policy_version"] == "v3"

    # Verify snapshot with manifest exists
    snapshots = state.get("snapshots", [])
    assert len(snapshots) >= 1
    latest_snap = snapshots[-1]
    assert "manifest" in latest_snap
    manifest = latest_snap["manifest"]
    assert manifest["unit_id"] == "RU-ORDER-001"
    assert manifest["protocol_hash"] == "proto_hash_v3_abc"
    assert manifest["approved_policy_version"] == "v3"


def test_publish_unit_transaction_limits(tmp_path):
    reg = ActiveRegistry(tmp_path / "registry")
    unit = _make_sample_repair_unit("RU-LIMITS-001")

    # With max_width=0, should reject without modifying registry
    with pytest.raises(LibraryLimitError, match="library width"):
        reg.publish_unit_transaction(
            unit,
            cycle=1,
            reason="should fail",
            library_cfg={"max_width": 0},
        )

    assert len(reg.entries) == 0


def test_retire_unit_atomic(tmp_path):
    reg = ActiveRegistry(tmp_path / "registry")
    unit = _make_sample_repair_unit("RU-RETIRE-001")

    reg.publish_unit_transaction(unit, cycle=1, reason="admit")
    assert len(reg.entries) >= 1

    retired = reg.retire_unit("RU-RETIRE-001", cycle=2)
    assert len(retired) >= 1
    assert len(reg.entries) == 0

    unit_rec = reg.state["units"]["RU-RETIRE-001"]
    assert unit_rec["status"] == "retired"
    assert unit_rec["retired_cycle"] == 2


def test_retire_unit_dependency_protection(tmp_path):
    reg = ActiveRegistry(tmp_path / "registry")

    # Entry 1: Evidence check providing 'evidence_refs_resolve'
    ev_entry = make_intervention(
        intervention_id="IV-EV-01",
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "evidence_refs_resolve",
            "runner": "deterministic",
            "output_verdicts": ["not_applicable", "resolved", "unresolved"],
        },
        expected_behavior="verify evidence refs resolve",
        target_polarity="boundary",
        cycle=1,
        failure_family="FAM-01",
        failure_cases=["A/001.json", "A/002.json"],
    )

    # Entry 2: Decision guard consuming 'evidence_refs_resolve=resolved'
    guard_entry = make_intervention(
        intervention_id="IV-GD-01",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        body={"action": "withhold_conviction", "action_not_when": "defense view degraded"},
        facts=["evidence_refs_resolve=resolved"],
        expected_behavior="withhold conviction when evidence resolves",
        target_polarity="boundary",
        cycle=1,
        failure_family="FAM-01",
        failure_cases=["A/001.json", "A/002.json"],
    )

    reg.write([ev_entry, guard_entry], cycle=1, reason="setup pair")

    # Manually register unit for the evidence entry
    reg._state.setdefault("units", {})["RU-DEP-TEST"] = {
        "unit_id": "RU-DEP-TEST",
        "member_ids": ["IV-EV-01"],
        "status": "active",
    }
    reg._save_state()

    # Retiring unit RU-DEP-TEST alone orphans IV-GD-01 -> must be rejected
    with pytest.raises(ValueError, match="remaining guard"):
        reg.retire_unit("RU-DEP-TEST", cycle=2)


def test_recover_consistent_state(tmp_path):
    reg_dir = tmp_path / "registry"
    reg = ActiveRegistry(reg_dir)
    unit = _make_sample_repair_unit("RU-HEAL-001")
    reg.publish_unit_transaction(unit, cycle=1, reason="admit")

    valid_hash = reg.registry_hash
    assert valid_hash

    # Simulate corruption of active.json (e.g. truncated or replaced by empty registry)
    active_path = reg_dir / "active.json"
    active_path.write_text(json.dumps(empty_registry()), encoding="utf-8")

    # Verifying integrity detects inconsistency
    check = reg.verify_integrity()
    assert check["consistent"] is False

    # Calling recover_consistent_state heals active.json from the latest snapshot
    restored = reg.recover_consistent_state()
    assert restored["content_hash"] == valid_hash
    assert reg.registry_hash == valid_hash

    # Check recovery event was logged
    state = reg.state
    assert "recovery_events" in state
    assert len(state["recovery_events"]) >= 1
    assert state["recovery_events"][-1]["restored_hash"] == valid_hash

    # Starting a new ActiveRegistry with auto_recover=True handles corruption cleanly
    active_path.write_text(json.dumps(empty_registry()), encoding="utf-8")
    reg_healed = ActiveRegistry(reg_dir, auto_recover=True)
    assert reg_healed.registry_hash == valid_hash


def test_unit_longitudinal_contribution_tracking(tmp_path):
    reg = ActiveRegistry(tmp_path / "registry")
    unit_id = "RU-TRACK-001"

    reg.record_unit_contribution(unit_id, net_correct=3, cycle=1, marginal=2, fired_rate=0.75)
    reg.record_unit_contribution(unit_id, net_correct=2, cycle=2, marginal=1, fired_rate=0.80)

    summary = reg.get_unit_contribution_summary(unit_id)
    assert summary is not None
    assert summary["unit_id"] == unit_id
    assert summary["cycles_tracked"] == 2
    assert summary["cumulative_net_correct"] == 5
    assert summary["cumulative_marginal"] == 3


def test_unit_drift_memory_store(tmp_path):
    mem_root = tmp_path / "memory"

    drift_event = record_unit_drift(
        mem_root,
        unit_id="RU-DRIFT-001",
        cycle=3,
        status="active",
        delta_net=-1,
        marginal_cases=0,
        fired_rate=0.50,
        drift_detected=True,
        reason="degraded marginal net benefit on holdout",
    )
    assert drift_event["schema_version"] == UNIT_DRIFT_SCHEMA
    assert drift_event["drift_detected"] is True

    loaded = load_unit_drift(mem_root)
    assert len(loaded) == 1
    assert loaded[0]["unit_id"] == "RU-DRIFT-001"
    assert loaded[0]["delta_net"] == -1
    assert loaded[0]["drift_detected"] is True
