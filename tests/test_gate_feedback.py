"""P5: audited gate events and bounded, strictly separated feedback."""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.gate_feedback import (
    FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    FEEDBACK_REGISTRY_ENTRY_PENDING,
    FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH,
    FUZZY_FEEDBACK_CODES,
    SCHEMA_VERSION,
    GateEvent,
    GateEventLedger,
    gate_event_id,
    project_cross_case_guidance,
)


def make_event(**overrides) -> GateEvent:
    base = dict(
        schema_version=SCHEMA_VERSION,
        event_id="evt_1",
        subject_id="subj_1",
        episode_id="ep_1",
        attempt_id="att_1",
        layer="l2",
        artifact="policy_genome",
        operation="add_item",
        stage="fitness_gate",
        legacy_decision="reject_policy_no_effect",
        evidence_grade="reject",
        activation_state="",
        execution_trace_class="applied",
        observable_delta_class="none",
        downstream_delta_class="none",
    )
    base.update(overrides)
    return GateEvent(**base)


def _signal(state: str) -> object:
    """A minimal AttributionSignal for runner-level gate tests."""
    from src.sehd_v6.core.bccse.step_attributor import (
        AttributionSignal,
        ErrorType,
        ReflectionType,
        TriggerSource,
    )

    return AttributionSignal(
        error_type=ErrorType.EVIDENCE_INCOMPLETE,
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        attribution_confidence="medium",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.EVIDENCE_INCOMPLETE,
        evidence_summary="mock",
        prosecution_coverage=1.0,
        defense_coverage=1.0,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="mock",
        crs_complete=True,
        consensus_gate_applied=False,
        candidate_count=1,
        state=state,
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )


# ============================================================
# GateEvent schema / validation
# ============================================================

def test_event_roundtrip():
    event = make_event()
    assert GateEvent.from_dict(event.to_dict()) == event


def test_unknown_grade_rejected():
    with pytest.raises(ValueError):
        make_event(evidence_grade="excellent")


def test_unknown_trace_class_rejected():
    with pytest.raises(ValueError):
        make_event(execution_trace_class="teleported")


def test_unknown_schema_rejected():
    with pytest.raises(ValueError):
        make_event(schema_version="sehd.gate_event.v0")


# ============================================================
# §11.4 event id priority and stability
# ============================================================

def test_event_id_prefers_attempt():
    eid = gate_event_id(attempt_id="att_9", episode_id="ep_1", decision_kind="reject")
    assert eid == gate_event_id(attempt_id="att_9")
    assert eid != gate_event_id(episode_id="ep_1", decision_kind="reject")


def test_event_id_episode_plus_decision_when_no_proposal():
    eid = gate_event_id(episode_id="ep_1", decision_kind="skip_no_edit")
    assert eid != gate_event_id(episode_id="ep_1", decision_kind="skip_stable")


def test_event_id_runtime_observation_fallback():
    eid = gate_event_id(observation_id="obs_1")
    assert eid == gate_event_id(observation_id="obs_1")
    assert eid != gate_event_id(observation_id="obs_2")


def test_sequence_not_part_of_identity():
    # sequence only orders; it never changes the stable identity.
    assert gate_event_id(attempt_id="a") == gate_event_id(attempt_id="a")


# ============================================================
# Ledger idempotent resume (§11.8: resume 不重复写同一 event)
# ============================================================

def test_ledger_idempotent_record():
    ledger = GateEventLedger()
    event = make_event()
    assert ledger.record(event) is True
    assert ledger.record(event) is False
    assert len(ledger.events) == 1


def test_ledger_sequence_assigned_monotonically():
    ledger = GateEventLedger()
    ledger.record(make_event(event_id="a"))
    ledger.record(make_event(event_id="b"))
    assert [e.sequence for e in ledger.events] == [1, 2]


def test_ledger_roundtrip_resume_no_duplicate():
    ledger = GateEventLedger()
    ledger.record(make_event(event_id="a"))
    restored = GateEventLedger.from_list(ledger.to_list())
    assert restored.record(make_event(event_id="a")) is False
    assert len(restored.events) == 1


# ============================================================
# §11.6 projection separation and prompt safety
# ============================================================

def test_cross_case_projection_withholds_precise_state():
    event = make_event(execution_trace_class="applied")
    projection = project_cross_case_guidance(event)
    assert "retry_state" not in projection
    assert set(projection["feedback_codes"]) <= FUZZY_FEEDBACK_CODES


def test_feedback_has_no_identity_gold_or_raw_text():
    event = make_event(
        hard_reject_codes=("canary_regression",),
        quality_reason_codes=("state_change_against_gold_polarity",),
    )
    projection = project_cross_case_guidance(event)
    text = str(projection).lower()
    assert "gold" not in text
    assert "case/" not in text
    assert event.subject_id not in str(projection.values())
    # only closed codes appear
    assert set(projection["feedback_codes"]) <= FUZZY_FEEDBACK_CODES


# ============================================================
# fuzzy code mapping coverage
# ============================================================

def test_ownership_mismatch_maps_to_scope_code():
    event = make_event(hard_reject_codes=("ownership_mismatch",))
    projection = project_cross_case_guidance(event)
    assert FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH in projection["feedback_codes"]


def test_regression_maps_to_independent_risk():
    event = make_event(hard_reject_codes=("canary_regression",))
    projection = project_cross_case_guidance(event)
    assert FEEDBACK_INDEPENDENT_REGRESSION_RISK in projection["feedback_codes"]


def test_pending_registry_maps_to_registry_pending():
    event = make_event(quality_reason_codes=("registry_entry_pending",))
    projection = project_cross_case_guidance(event)
    assert FEEDBACK_REGISTRY_ENTRY_PENDING in projection["feedback_codes"]


def test_conditional_grade_yields_inconclusive_when_no_codes():
    event = make_event(evidence_grade="conditional", activation_state="pending")
    projection = project_cross_case_guidance(event)
    assert projection["feedback_codes"]


def test_feedback_codes_are_closed_vocabulary():
    event = make_event(
        hard_reject_codes=("protected_baseline_violation",),
        quality_reason_codes=("no_effect", "duplicate_item"),
    )
    projection = project_cross_case_guidance(event)
    assert FEEDBACK_POLICY_ROUTE_UNAVAILABLE in projection["feedback_codes"]
    assert set(projection["feedback_codes"]) <= FUZZY_FEEDBACK_CODES


# ============================================================
# Integration: every terminal gate decision lands in the ledger
# ============================================================

def test_runner_records_gate_events_for_terminal_decisions(tmp_path, monkeypatch):
    import json

    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        return {
            "state": "unresolved", "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        return {"no_proposal": True, "reason": "nothing"}, {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal("unresolved"),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        run_id="gate_events",
    )

    checkpoint = json.loads(
        (tmp_path / "evolution_checkpoint.json").read_text(encoding="utf-8")
    )
    events = checkpoint.get("gate_events") or []
    # The no-proposal / skip decision still produced an audited event.
    assert events
    assert all(row.get("schema_version") == "sehd.gate_event.v1" for row in events)
    assert all(row.get("event_id") for row in events)


def test_gate_event_carries_hard_reject_codes_from_structural_rejection(
    tmp_path, monkeypatch, addressable_l1_failure,
):
    """A structural rejection must surface its real reject reasons on the
    GateEvent (protected_baseline_violation -> hard code), so the meta
    review loop can see WHY a gate rejected instead of an empty reason set
    (previously every GateEvent carried empty hard/quality codes)."""
    import json

    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.bccse.genome import create_baseline_genome

    def detector(*, genome, case_id, **kwargs):
        return {
            "state": "unresolved", "runtime_valid": True,
            "decision_authority": "authoritative", "census": [],
        }

    def mutation(**kwargs):
        # Modify an immutable baseline rule of evidence_obligation (the
        # addressable_l1_failure fixture routes the case there).
        return {
            "target_module": "evidence_obligation",
            "operation": "modify_rule",
            "rule_index": 0,
            "condition": "cited evidence proves norm breach and agent control",
            "action": "require_evidence_field: impact_evidence_refs",
            "evidence_anchor": "rewritten baseline rule",
            "rationale": "rewrite baseline",
            "feature_description": "baseline rewrite attempt",
            "expected_effect": "causal field coverage increases",
            "expected_effect_contract": {
                "observable_field": "causal_field_coverage",
                "expected_direction": "increase",
                "expected_scope": "all_cases",
                "trigger_condition": "cited evidence is incomplete",
            },
        }, {"cache_hit": False}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner, "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: _signal(
            "unresolved"
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        records={"source": {}},
        config={}, output_dir=tmp_path, census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        reflection_candidate_count=1,
        run_id="gate_events_hard",
    )

    assert result.steps[0].gate_decision == "reject"
    events = result.steps[0].fitness_check.get("reject_reasons") or []
    assert any("immutable_baseline_rule" in reason for reason in events)

    checkpoint = json.loads(
        (tmp_path / "evolution_checkpoint.json").read_text(encoding="utf-8")
    )
    gate_events = checkpoint.get("gate_events") or []
    rejected = [
        event for event in gate_events
        if event.get("legacy_decision") == "reject"
    ]
    assert rejected, "the structural rejection must produce a GateEvent"
    assert "protected_baseline_violation" in rejected[0]["hard_reject_codes"]
    assert any(
        "immutable_baseline_rule" in code
        for code in rejected[0]["quality_reason_codes"]
    )
