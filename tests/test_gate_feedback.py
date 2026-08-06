"""P5: audited gate events and bounded, strictly separated feedback."""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.gate_feedback import (
    FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
    FEEDBACK_REGISTRY_ENTRY_PENDING,
    FEEDBACK_SCOPE_OR_OWNERSHIP_MISMATCH,
    FUZZY_FEEDBACK_CODES,
    RETRY_APPLIED_WITHOUT_LOCAL_EFFECT,
    RETRY_COMPILED_NOT_APPLIED,
    RETRY_LOCAL_EFFECT_WITHOUT_DOWNSTREAM,
    SCHEMA_VERSION,
    SAME_SOURCE_RETRY_STATES,
    GateEvent,
    GateEventLedger,
    gate_event_id,
    project_cross_case_guidance,
    project_same_source_retry,
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

def test_same_source_projection_has_precise_state():
    event = make_event(
        execution_trace_class="applied", observable_delta_class="none",
    )
    projection = project_same_source_retry(event)
    assert projection["retry_state"] == RETRY_APPLIED_WITHOUT_LOCAL_EFFECT
    assert projection["retry_state"] in SAME_SOURCE_RETRY_STATES


def test_same_source_state_mapping():
    assert project_same_source_retry(make_event(
        execution_trace_class="compiled", observable_delta_class="none",
    ))["retry_state"] == RETRY_COMPILED_NOT_APPLIED
    assert project_same_source_retry(make_event(
        execution_trace_class="fired", observable_delta_class="local",
        downstream_delta_class="downstream",
    ))["retry_state"] == RETRY_LOCAL_EFFECT_WITHOUT_DOWNSTREAM


def test_cross_case_projection_withholds_precise_state():
    event = make_event(execution_trace_class="applied")
    projection = project_cross_case_guidance(event)
    assert "retry_state" not in projection
    assert set(projection["feedback_codes"]) <= FUZZY_FEEDBACK_CODES


def test_projections_strictly_separated():
    event = make_event(
        quality_reason_codes=("policy_route_unavailable",),
        execution_trace_class="applied",
    )
    same = project_same_source_retry(event)
    cross = project_cross_case_guidance(event)
    assert "retry_state" in same
    assert "retry_state" not in cross
    assert same["feedback_codes"] == cross["feedback_codes"]


def test_feedback_has_no_identity_gold_or_raw_text():
    event = make_event(
        hard_reject_codes=("canary_regression",),
        quality_reason_codes=("state_change_against_gold_polarity",),
    )
    for projection in (
        project_same_source_retry(event),
        project_cross_case_guidance(event),
    ):
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
    from src.sehd_v6.core.bccse.step_attributor import (
        AttributionSignal,
        ErrorType,
        ReflectionType,
        TriggerSource,
    )

    def _signal(state: str) -> AttributionSignal:
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
