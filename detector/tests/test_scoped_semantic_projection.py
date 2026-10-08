"""Tests for packet scoped projection and scoped semantic executor (P2-PR05/PR06 / C09, C10)."""

from __future__ import annotations

import pytest

from detector.interventions import (
    INTERVENTION_SCHEMA,
    SCOPED_SEMANTIC_EVIDENCE_SCHEMA,
    InterventionPlan,
    SemanticExecutor,
    TERMINAL_EVIDENCE_ONLY,
    validate_intervention,
)
from detector.packet import (
    EVENT_WINDOW_PROJECTION_SCHEMA,
    PacketError,
    build_packet,
    project_event_window,
)


class DummySemanticExecutor(SemanticExecutor):
    def __init__(self, artifact_id: str = "art-1", artifact_digest: str = "dig-1"):
        self._id = artifact_id
        self._digest = artifact_digest
        self.calls = []

    def identity(self) -> dict[str, str]:
        return {"id": self._id, "digest": self._digest}

    def judge(self, predicate: str, packet: dict, context: dict) -> dict:
        self.calls.append((predicate, context))
        return {
            "verdict": "supported",
            "detail": "semantic check passed",
            "evidence_refs": ["event_002"],
        }


def _sample_packet() -> dict:
    history = [
        {"role": "user", "content": "Fetch the balance and calculate taxes."},
        {"role": "assistant", "tool_calls": [{"name": "fetch_balance", "arguments": "{}"}]},
        {"role": "tool", "content": '{"balance": 1000}'},
        {"role": "assistant", "tool_calls": [{"name": "compute_tax", "arguments": '{"rate": 0.2}'}]},
        {"role": "tool", "content": '{"tax": 200}'},
        {"role": "assistant", "content": "Your balance is 1000 and taxes are 200."},
    ]
    return build_packet({"question": "Calculate taxes", "history": history})


def test_project_event_window_causal_boundaries():
    packet = _sample_packet()
    # Anchor at event_004 (compute_tax), window_before=1, window_after=1
    proj = project_event_window(
        packet,
        anchor_event_id="event_004",
        window_before=1,
        window_after=1,
    )
    assert proj["schema_version"] == EVENT_WINDOW_PROJECTION_SCHEMA
    assert proj["anchor_event_id"] == "event_004"
    assert proj["anchor_position"] == 4
    # Event 003, 004, 005
    assert proj["event_ids"] == ["event_003", "event_004", "event_005"]
    assert proj["is_window_complete"] is True
    assert proj["has_subsequent_activity"] is True
    assert "has_subsequent_recovery" not in proj
    assert proj["delivered_result"] is not None
    assert isinstance(proj["projection_hash"], str)
    assert len(proj["projection_hash"]) == 64


def test_project_event_window_with_target():
    packet = _sample_packet()
    proj = project_event_window(
        packet,
        anchor_event_id="event_002",
        target_event_id="event_004",
        window_before=1,
        window_after=1,
    )
    assert proj["anchor_event_id"] == "event_002"
    assert proj["target_event_id"] == "event_004"
    assert proj["event_ids"] == ["event_001", "event_002", "event_003", "event_004", "event_005"]
    assert proj["is_window_complete"] is True


def test_project_event_window_incomplete_bounds():
    packet = _sample_packet()
    # Anchor at event_001 with window_before=2 (clips at start)
    proj = project_event_window(
        packet,
        anchor_event_id="event_001",
        window_before=2,
        window_after=1,
    )
    assert proj["is_window_complete"] is False
    assert proj["event_ids"] == ["event_001", "event_002"]


def test_project_event_window_missing_anchor():
    packet = _sample_packet()
    with pytest.raises(PacketError, match="anchor event 'non_existent' not found"):
        project_event_window(packet, anchor_event_id="non_existent")


def test_window_includes_target_before_anchor():
    proj = project_event_window(
        _sample_packet(), anchor_event_id="event_005", target_event_id="event_002",
        window_before=1, window_after=1,
    )
    assert proj["event_ids"] == [f"event_{index:03d}" for index in range(1, 7)]
    assert proj["is_window_complete"] is True


@pytest.mark.parametrize("change", ["question", "selected_content", "omitted_content"])
def test_projection_identity_covers_actual_packet_content(change):
    packet = _sample_packet()
    before = project_event_window(packet, anchor_event_id="event_003",
                                  window_before=0, window_after=0)
    if change == "question":
        packet["question"] = "Calculate a different tax"
    else:
        index = 2 if change == "selected_content" else 5
        packet["events"][index]["content"] = "changed observation"
    after = project_event_window(packet, anchor_event_id="event_003",
                                 window_before=0, window_after=0)
    assert before["event_ids"] == after["event_ids"]
    assert before["projection_hash"] != after["projection_hash"]


def test_projection_has_independent_nested_content():
    packet = _sample_packet()
    proj = project_event_window(packet, anchor_event_id="event_002",
                                window_before=0, window_after=0)
    proj["events"][0]["tool_interactions"][0]["arguments"] = "changed"
    assert packet["events"][1]["tool_interactions"][0]["arguments"] == "{}"


@pytest.mark.parametrize("field", ["window_before", "window_after"])
@pytest.mark.parametrize("value", [-1, True, 0.5, float("nan")])
def test_projection_rejects_invalid_window_sizes(field, value):
    with pytest.raises(PacketError, match=field):
        project_event_window(_sample_packet(), anchor_event_id="event_003",
                             **{field: value})


def test_projection_records_clipping_and_delivery_outside_local_window():
    proj = project_event_window(_sample_packet(), anchor_event_id="event_001",
                                window_before=2, window_after=0)
    assert proj["coverage_reasons"] == ["window_before_clipped"]
    assert proj["omitted_event_ids"] == [f"event_{index:03d}" for index in range(2, 7)]
    assert proj["omitted_event_count"] == len(proj["omitted_event_ids"])
    assert proj["delivery_event_ids"] == ["event_006"]
    assert "event_006" not in proj["event_ids"]
    assert proj["question"] == "Calculate taxes"


def test_subsequent_activity_does_not_certify_recovery():
    packet = build_packet({"question": "What is the balance?", "history": [
        {"role": "assistant", "content": "The balance is incorrect."},
        {"role": "assistant", "content": "I will discuss an unrelated subject."},
    ]})
    proj = project_event_window(packet, anchor_event_id="event_001",
                                window_before=0, window_after=0)
    assert proj["has_subsequent_activity"] is True
    assert "has_subsequent_recovery" not in proj


def test_scoped_semantic_layer1_static_applicability_not_met():
    packet = _sample_packet()
    executor = DummySemanticExecutor()
    entry = {
        "schema_version": INTERVENTION_SCHEMA,
        "intervention_id": "IV-SEM-001",
        "type": "evidence_check",
        "hook": "defense.pre",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "check_retrieval",
            "runner": "typed_semantic",
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "scope": {
                "required_features": ["question_has_numbers"],  # packet question has no numbers
                "anchor_event": "event_002",
                "window_before": 1,
            },
        },
        "artifact": {"id": "art-1", "digest": "dig-1"},
        "claim": {"expected_behavior": "test", "target_polarity": "boundary"},
        "provenance": {"cycle": 1, "failure_family": "fam", "failure_cases": ["c1", "c2"]},
    }
    validate_intervention(entry)
    plan = InterventionPlan([entry], semantic_executor=executor)
    evidence = plan.evidence_for("defense.pre", packet=packet, context={})
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["schema_version"] == SCOPED_SEMANTIC_EVIDENCE_SCHEMA
    assert ev["scope_status"] == "not_applicable"
    assert ev["verdict"] == "abstained"
    assert ev["terminal_authority"] == TERMINAL_EVIDENCE_ONLY
    assert ev["blocking_reasons"] == ["static_features_not_met"]
    # Crucial: executor must NOT have been called!
    assert len(executor.calls) == 0


def test_scoped_semantic_layer2_incomplete_window():
    packet = _sample_packet()
    executor = DummySemanticExecutor()
    entry = {
        "schema_version": INTERVENTION_SCHEMA,
        "intervention_id": "IV-SEM-002",
        "type": "evidence_check",
        "hook": "defense.pre",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "check_taxes",
            "runner": "typed_semantic",
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "scope": {
                "required_features": ["has_tool_calls"],
                "anchor_event": "event_001",
                "window_before": 5,  # Too large for start of packet
                "window_after": 1,
            },
        },
        "artifact": {"id": "art-1", "digest": "dig-1"},
        "claim": {"expected_behavior": "test", "target_polarity": "boundary"},
        "provenance": {"cycle": 1, "failure_family": "fam", "failure_cases": ["c1", "c2"]},
    }
    validate_intervention(entry)
    plan = InterventionPlan([entry], semantic_executor=executor)
    evidence = plan.evidence_for("defense.pre", packet=packet, context={})
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["scope_status"] == "window_incomplete"
    assert ev["verdict"] == "abstained"
    assert ev["terminal_authority"] == TERMINAL_EVIDENCE_ONLY
    assert ev["blocking_reasons"] == ["window_incomplete"]
    # Executor must NOT have been called!
    assert len(executor.calls) == 0


def test_scoped_semantic_layer3_applicable_execution():
    packet = _sample_packet()
    executor = DummySemanticExecutor()
    entry = {
        "schema_version": INTERVENTION_SCHEMA,
        "intervention_id": "IV-SEM-003",
        "type": "evidence_check",
        "hook": "defense.pre",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "check_taxes",
            "runner": "typed_semantic",
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "scope": {
                "required_features": ["has_tool_calls"],
                "anchor_event": "event_004",
                "window_before": 1,
                "window_after": 1,
            },
        },
        "artifact": {"id": "art-1", "digest": "dig-1"},
        "claim": {"expected_behavior": "test", "target_polarity": "boundary"},
        "provenance": {"cycle": 1, "failure_family": "fam", "failure_cases": ["c1", "c2"]},
    }
    validate_intervention(entry)
    plan = InterventionPlan([entry], semantic_executor=executor)
    evidence = plan.evidence_for("defense.pre", packet=packet, context={})
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["schema_version"] == SCOPED_SEMANTIC_EVIDENCE_SCHEMA
    assert ev["scope_status"] == "applicable"
    assert ev["verdict"] == "supported"
    assert ev["terminal_authority"] == TERMINAL_EVIDENCE_ONLY
    assert ev["anchor_event_ids"] == ["event_004"]
    assert len(executor.calls) == 1
    # Verify window projection was delivered in context
    _, ctx = executor.calls[0]
    assert "window_projection" in ctx
    assert ctx["window_projection"]["event_ids"] == ["event_003", "event_004", "event_005"]
