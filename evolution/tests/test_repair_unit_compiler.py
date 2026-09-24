"""Tests for RepairUnit schema and pure deterministic compiler."""

from __future__ import annotations

import json
import pytest

from evolution.repair_compiler import compile_repair_unit
from evolution.repair_unit import (
    ERROR_MECHANISM_UNBOUND,
    ERROR_SCHEMA_INVALID,
    ERROR_SCOPE_UNSAFE,
    ERROR_UNSAFE_TERMINAL_ACTION,
    ERROR_UNSUPPORTED_CAPABILITY,
    REPAIR_UNIT_SCHEMA,
    RepairUnit,
    RepairUnitError,
    repair_unit_hash,
)


def _valid_unit_dict() -> dict:
    return {
        "schema_version": REPAIR_UNIT_SCHEMA,
        "target": {
            "mechanism_id": "test-mech-001",
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
            "predicate_name": "evo_tool_prereq_check",
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
            "required_evidence": ["evo_tool_prereq_check=violated"],
            "not_when": ["calculator_not_requested"],
            "on_uncertain": "no_new_action",
        },
        "claim": {
            "expected_behavior": "convict when required calculator tool is absent",
            "target_kind": "detection",
        },
    }


def test_valid_repair_unit_compilation():
    data = _valid_unit_dict()
    confirmed = {
        "mechanism_id": "test-mech-001",
        "objective": "detection",
        "failure_family": "missing_calculator",
    }
    compiled = compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert compiled.unit_hash
    assert len(compiled.entries) == 2
    assert compiled.dependency_graph
    # Verify evidence and guard entries
    types = [e["type"] for e in compiled.entries]
    assert types == ["evidence_check", "decision_guard"]
    assert compiled.to_dict()["schema_version"] == "evolution.compiled_unit.v1"


def test_compilation_determinism_under_field_reordering():
    data1 = _valid_unit_dict()
    # Re-order dict keys
    data2 = {k: data1[k] for k in reversed(list(data1.keys()))}
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    c1 = compile_repair_unit(data1, confirmed_mechanism=confirmed)
    c2 = compile_repair_unit(data2, confirmed_mechanism=confirmed)
    assert c1.unit_hash == c2.unit_hash
    assert c1.entries == c2.entries


def test_schema_invalid_on_malformed_input():
    data = _valid_unit_dict()
    data["schema_version"] = "wrong.schema"
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_SCHEMA_INVALID


def test_mechanism_unbound_mismatch():
    data = _valid_unit_dict()
    confirmed = {"mechanism_id": "other-mechanism-id", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_MECHANISM_UNBOUND


def test_unsupported_capability_unknown_runner():
    data = _valid_unit_dict()
    data["observation"]["runner"] = "magic_unknown_runner"
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_UNSUPPORTED_CAPABILITY


def test_unsafe_terminal_action_learned_runner_conviction():
    data = _valid_unit_dict()
    data["observation"]["runner"] = "typed_semantic"
    data["artifact"] = {"id": "art1", "digest": "sha256-mock"}
    # typed_semantic is evidence-only, cannot convict_mechanical_violation
    data["decision"]["requested_action"] = "convict_mechanical_violation"
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_UNSAFE_TERMINAL_ACTION


def test_scope_unsafe_catches_case_id_leakage():
    data = _valid_unit_dict()
    # Leak a case ID in rules
    data["observation"]["rules"][0]["detail"] = "Fails specifically on BFCL/001.json"
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_SCOPE_UNSAFE


def test_scope_unsafe_catches_gold_leakage():
    data = _valid_unit_dict()
    # Leak gold field in scope
    data["scope"]["contract_kind"] = "is_hallucination_true"
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}

    with pytest.raises(RepairUnitError) as exc_info:
        compile_repair_unit(data, confirmed_mechanism=confirmed)
    assert exc_info.value.code == ERROR_SCOPE_UNSAFE


def test_repair_unit_dataclass_roundtrip():
    data = _valid_unit_dict()
    unit = RepairUnit.from_dict(data)
    serialized = unit.to_dict()
    unit2 = RepairUnit.from_dict(serialized)
    assert unit == unit2
    assert repair_unit_hash(unit) == repair_unit_hash(unit2)


def test_proposal_batch_registers_compiled_unit():
    from evolution.proposer import ProposalBatch

    data = _valid_unit_dict()
    confirmed = {"mechanism_id": "test-mech-001", "objective": "detection"}
    compiled = compile_repair_unit(data, confirmed_mechanism=confirmed)

    batch = ProposalBatch()
    batch.add_compiled_unit(compiled)

    assert len(batch.units) == 1
    assert len(batch.candidates) == 2
    assert len(batch.joint_groups) == 1
    assert batch.joint_groups[0] == [e["intervention_id"] for e in compiled.entries]

