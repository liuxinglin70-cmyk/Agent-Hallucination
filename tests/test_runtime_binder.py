"""Runtime binder contract: label-free inputs, vocabulary, abstention."""

from __future__ import annotations

from pathlib import Path

import pytest

from sehd_rise2.evolution.binder import (
    assert_label_free,
    binding_inputs,
    normalize_binding,
)
from sehd_rise2.evolution.policy import CERTIFICATE_CLASSES
from sehd_rise2.evolution.replay import (
    RUNTIME_BINDINGS_SCHEMA,
    replay_case,
    validate_bindings,
)

REAL_RUN = (
    Path(__file__).resolve().parents[1]
    / "runs" / "random100_20260815_142446"
)


def _tree():
    result = {
        "state": "verified_error",
        "proof_adjudication": {
            "census": [{
                "candidate_id": "c1", "claim": "the agent did X",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_001", "event_002"],
            }],
            "certificates": [{
                "candidate_id": "c1", "verdict": "supported",
                "rationale": "r",
            }],
            "defense_decisions": {
                "c1": {"verdict": "upheld", "rationale": "r"},
            },
        },
    }
    packet = {
        "events": [
            {"event_id": "event_001", "role": "user",
             "content": "do the task"},
            {"event_id": "event_002", "role": "assistant",
             "content": "done", "tool_interactions": [
                 {"tool_name": "t", "arguments": {"a": 1}},
             ]},
        ],
    }
    return {"A/001.json": {"result": result, "packet": packet}}


def test_binding_inputs_carry_only_allowlisted_fields():
    inputs = binding_inputs(_tree())
    assert len(inputs) == 1
    case_input = inputs[0]
    assert set(case_input) == {"case_id", "certificates"}
    entry = case_input["certificates"][0]
    assert set(entry) == {
        "candidate_id", "candidate_claim", "certificate_verdict",
        "certificate_rationale", "evidence_refs", "defense_outcome",
        "packet_events",
    }
    assert entry["packet_events"][0]["event_id"] == "event_001"


def test_label_free_scan_rejects_gold_bearing_fields():
    assert_label_free(binding_inputs(_tree()))
    tainted = binding_inputs(_tree())
    tainted[0]["gold_label"] = False
    with pytest.raises(ValueError, match="allowlist"):
        assert_label_free(tainted)
    tainted = binding_inputs(_tree())
    tainted[0]["certificates"][0]["gold"] = True
    with pytest.raises(ValueError, match="allowlist"):
        assert_label_free(tainted)
    tainted = binding_inputs(_tree())
    tainted[0]["certificates"][0]["packet_events"][0][
        "hallucination_step"
    ] = 3
    with pytest.raises(ValueError, match="forbidden key"):
        assert_label_free(tainted)


def test_normalize_binding_accepts_vocabulary_and_abstention():
    records = normalize_binding(
        {"bindings": [
            {"candidate_id": "c1", "class": "content_contradiction",
             "evidence_basis": "b"},
            {"candidate_id": "c2", "class": "unclassified"},
        ]},
        case_id="A", expected_ids=["c1", "c2"],
    )
    assert records[0]["binding_status"] == "classified"
    assert records[1]["binding_status"] == "unclassified"
    with pytest.raises(ValueError, match="closed vocabulary"):
        normalize_binding(
            {"bindings": [
                {"candidate_id": "c1", "class": "vibe_based"},
            ]},
            case_id="A", expected_ids=["c1"],
        )
    with pytest.raises(ValueError, match="without basis"):
        normalize_binding(
            {"bindings": [
                {"candidate_id": "c1", "class": "premise_conflict",
                 "evidence_basis": ""},
            ]},
            case_id="A", expected_ids=["c1"],
        )
    with pytest.raises(ValueError, match="unanswered"):
        normalize_binding(
            {"bindings": [
                {"candidate_id": "c1", "class": "unclassified"},
            ]},
            case_id="A", expected_ids=["c1", "c2"],
        )
    with pytest.raises(ValueError, match="unknown certificate"):
        normalize_binding(
            {"bindings": [
                {"candidate_id": "c9", "class": "unclassified"},
            ]},
            case_id="A", expected_ids=["c1"],
        )


def test_unclassified_certificate_keeps_baseline_behaviour():
    actions = {
        "requirement_compliance": "require_corroborating_content_error",
        "presentational_addition": "withhold_conviction",
    }
    outcome = replay_case(["unclassified"], actions)
    assert outcome["state"] == "verified_error"
    assert outcome["policy_evaluation"]["fired"] == []


def test_runtime_bindings_validate_with_abstention():
    tree = _tree()
    surviving = {("A/001.json", "c1")}
    runtime = {("A/001.json", "c1"): {
        "certificate_class": "unclassified",
        "basis": "binder abstained",
        "binding_status": "unclassified",
    }}
    payload = {"schema_version": RUNTIME_BINDINGS_SCHEMA}
    checks = validate_bindings(
        payload, runtime, tree, allow_unclassified=True,
    )
    assert all(checks.values())
    checks = validate_bindings(payload, runtime, tree)
    assert not checks["classes_in_vocabulary"]


def test_binder_vocabulary_includes_abstain_and_delivery_classes():
    classes = set(CERTIFICATE_CLASSES) | {"unclassified"}
    assert "delivery_form" in classes
    assert "content_contradiction" in classes


@pytest.mark.skipif(
    not REAL_RUN.exists(), reason="run tree not present",
)
def test_real_binding_inputs_stay_label_free():
    from sehd_rise2.evolution.binder import load_run_packets
    inputs = binding_inputs(load_run_packets(REAL_RUN))
    assert len(inputs) == 62
    certificates = sum(
        len(case_input["certificates"]) for case_input in inputs
    )
    assert certificates == 162
    assert_label_free(inputs)
