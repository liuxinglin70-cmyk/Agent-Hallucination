from __future__ import annotations

from types import SimpleNamespace

import pytest

from sehd_v6.core.bccse.action_spec import (
    compile_action_spec,
    evaluate_action_spec_postcondition,
)
from sehd_v6.core.bccse.experience_retriever import (
    ExperienceMatch,
    FailurePattern,
    format_experience_for_prompt,
)
from sehd_v6.core.bccse.failure_classifier import (
    ORIGIN_PROVIDER_GENERATION,
    ORIGIN_ROUTE_CONFIG,
    ORIGIN_TRANSPORT_PARSE,
    ORIGIN_VALIDATOR_CONTRACT,
    can_compile_repair_obligation,
    classify_failure,
    classify_malformed_origin,
    origin_layer,
)
from sehd_v6.core.bccse.step_attributor import attribute_failure
from sehd_v6.core.bccse.fitness import FitnessAssessment, observable_process_delta
from sehd_v6.core.bccse.genome import (
    SkillEdit,
    apply_edit,
    create_baseline_genome,
)
from sehd_v6.core.bccse.rule_contract import RuleSpec, render_rule
from sehd_v6.core.bccse.condition_spec import (
    evaluate_condition_spec,
    project_route_condition_features,
)
from sehd_v6.core.bccse.evolution_runner import (
    _allowed_l1_modules,
    _attribute_intervention_fitness,
    _enforce_expected_effect_observation,
    _expected_effect_audit,
    _prevalidate_candidate,
)
from sehd_v6.core.bccse.failure_episode import (
    UnaddressableFailure,
    build_failure_episode,
    project_provider_artifacts,
)
from sehd_v6.core.bccse.v6_contracts import FailureAttribution
from sehd_v6.core.bccse.v6_contracts import LAYER_DETECTOR, LAYER_L1, LAYER_NONE
from sehd_v6.core.paca_tcl.audit_contract import (
    ERROR_CHECK_FIELDS,
    ERROR_RELATIONAL_INVARIANTS,
    project_audit_failures,
)
from sehd_v6.core.paca_tcl.proof_contract import adjudicate_proof_court
from sehd_v6.core.paca_tcl.proof_engine import (
    _deep_validate_appeal_prosecution,
    _run_focused_prosecution_for_candidate,
    _validate_appeal_prosecution_deep_or_repair,
)
from sehd_v6.core.paca_tcl.skill_integration import (
    compile_genome_for_paca,
    make_skill_aware_provider,
)


def _signal(**overrides):
    values = {
        "gold_label": True,
        "state": "unresolved",
        "prosecution_coverage_state": "incomplete",
        "defense_coverage_state": "complete",
        "prosecution_coverage": None,
        "defense_coverage": None,
        "candidate_count": 1,
        "error_type": "unresolved_should_error",
        "trigger_source": "gold_mismatch",
        "target_module": "atomic_verifier_instruction",
        "secondary_module": "evidence_obligation",
        "attribution_confidence": 1.0,
        "should_evolve": True,
        "localization_status": "not_applicable",
        "crs_complete": True,
        "has_gold": True,
        "responsible_step_correct": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _condition_spec(feature: str, value, *, value_type: str = "string"):
    return {
        "schema_version": "sehd.condition_spec.v1",
        "feature_source": "provider_input",
        "feature": feature,
        "operator": "equals" if value_type == "boolean" else "contains",
        "value_type": value_type,
        "value": value,
        "scope": "action_spec_routes",
        "evidence_binding": "pre_call_projection",
    }


def _error_certificate(*, ordered: bool) -> dict:
    control = "event_2" if ordered else "event_3"
    impact = "event_3" if ordered else "event_2"
    return {
        "certificate_id": "appeal_error_001",
        "source_candidate_id": "candidate_1",
        "origin_event_id": "event_1",
        "claim_kind": "agent_assertion",
        "norm_source": "user_instruction",
        "norm_evidence_refs": ["event_1"],
        "breach_type": "contradiction",
        "violation_evidence_refs": ["event_1"],
        "agent_control_event_id": control,
        "agent_control_evidence_refs": [control],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact],
        "impact_evidence_refs": [impact],
        "recovery_state": "none",
        "recovery_evidence_refs": [],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact],
        "rationale": "A complete addressable causal chain.",
    }


def test_audit_predicate_is_not_mislabeled_as_a_json_field():
    projection = project_audit_failures(
        row={
            "origin_event_id": "event_3",
            "agent_control_event_id": "event_2",
            "impact_event_ids": ["event_1"],
        },
        failed_checks=["ordered_chain"],
        required_fields=(),
        check_fields=ERROR_CHECK_FIELDS,
        relational_invariants=ERROR_RELATIONAL_INVARIANTS,
    )

    assert projection["failed_invariants"] == ["ordered_chain"]
    assert projection["failed_fields"] == [
        "agent_control_event_id", "impact_event_ids", "origin_event_id",
    ]
    assert "ordered_chain" not in projection["failed_fields"]
    assert projection["missing_fields"] == []


def test_appeal_prosecution_repairs_validator_failure_before_evolution():
    built = {"events": [
        {"event_id": "event_1"},
        {"event_id": "event_2"},
        {"event_id": "event_3"},
    ]}
    required = [{
        "candidate_id": "candidate_1",
        "origin_hypothesis_event_id": "event_1",
    }]
    original = {
        "coverage_state": "complete",
        "reviewed_candidate_ids": ["candidate_1"],
        "candidates": [_error_certificate(ordered=False)],
    }
    _valid, invalid = _deep_validate_appeal_prosecution(
        original, built=built, required_candidates=required,
    )
    assert invalid[0]["failed_checks"] == ["ordered_chain"]
    assert invalid[0]["missing_fields"] == []
    assert invalid[0]["failed_invariants"] == ["ordered_chain"]

    repaired_response = {
        **original,
        "candidates": [_error_certificate(ordered=True)],
    }
    providers = {}
    result = _validate_appeal_prosecution_deep_or_repair(
        original,
        built=built,
        candidates=required,
        base={},
        prosecution={},
        defense={},
        proof={},
        required_candidates=required,
        allowed_ids={"candidate_1"},
        retry_call=lambda _audits: (repaired_response, {"cached": False}),
        providers=providers,
    )

    valid_rows, remaining = _deep_validate_appeal_prosecution(
        result, built=built, required_candidates=required,
    )
    assert remaining == []
    assert valid_rows[0]["agent_control_event_id"] == "event_2"
    assert providers["appeal_prosecutor_deep"]["outcome"] == "repaired"


def test_focused_incomplete_coverage_is_retried_not_called_provider_error(
    tmp_path, monkeypatch,
):
    from sehd_v6.core.paca_tcl import proof_engine

    responses = iter([
        ({"coverage_state": "unresolved", "candidates": []}, {"attempt": 1}),
        ({"coverage_state": "complete", "candidates": []}, {"attempt": 2}),
    ])
    calls = []

    def provider_call(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(proof_engine, "provider_call", provider_call)
    certificates, audits, meta = _run_focused_prosecution_for_candidate(
        candidate={
            "candidate_id": "candidate_1",
            "origin_hypothesis_event_id": "event_1",
        },
        built={"events": [{"event_id": "event_1"}]},
        base={},
        config={
            "focused_prosecution_route": {},
            "focused_prosecution_retries": 1,
        },
        output_dir=tmp_path,
    )

    assert certificates == []
    assert audits == []
    assert len(calls) == 2
    # A retry is a fresh provider call. It must carry the complete certificate
    # language and the invalid parent response, rather than referring to an
    # unavailable "same schema as before" conversation state.
    assert '"certificate_id"' in calls[1]["system"]
    assert "PREVIOUS_INVALID_RESPONSE=" in calls[1]["user"]
    assert '"coverage_state":"unresolved"' in calls[1]["user"]
    assert meta["coverage_error"] == {
        "failure_category": "coverage_state_not_complete",
        "declared_coverage_state": "unresolved",
    }
    assert meta["terminal_status"] == "no_error"
    assert meta["no_error_declared_flag"] is True
    assert not any(key.endswith("_error") for key in meta if key != "coverage_error")


def test_gold_error_unresolved_routes_to_prosecution_not_clean_abstention():
    signal = attribute_failure({
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "gold_label": True,
        "census": [{"candidate_id": "candidate_1"}],
        "combined_prosecution": {
            "coverage_state": "incomplete",
            "candidates": [{"certificate_id": "error_1"}],
        },
        "defense": {
            "coverage_state": "complete",
            "candidates": [{"certificate_id": "defense_1"}],
        },
        "proof_adjudication": {
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": [],
        },
    }, use_gold=True)

    assert str(getattr(signal.error_type, "value", signal.error_type)) == (
        "unresolved_should_error"
    )
    assert signal.target_module == "evidence_obligation"
    assert signal.secondary_module == "atomic_verifier_instruction"


def test_invariant_action_has_a_court_measured_postcondition():
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    )
    assert spec.observable_field == "target_certificate_invariant_failure"
    assert spec.expected_direction == "decrease"
    before = {
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    after = {
        "proof_adjudication": {
            "error_audits": [{"failed_checks": []}],
        },
    }

    assert evaluate_action_spec_postcondition(
        spec, after, baseline_result=before,
    )[0] is True
    assert evaluate_action_spec_postcondition(
        spec, before, baseline_result=before,
    )[0] is False

    delta = observable_process_delta(
        _signal(),
        _signal(),
        target_module="atomic_verifier_instruction",
        before_result=before,
        after_result=after,
    )
    assert "certificate_invariant_failures:decreased" in delta

    edit = SkillEdit(
        edit_id="edit_invariant",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        action_spec=spec.to_dict(),
        expected_effect=spec.expected_effect_text(),
        expected_effect_contract=spec.effect_contract(
            "certificate events are out of causal order"
        ),
    )
    audit = _expected_effect_audit(
        edit,
        FitnessAssessment(
            "local_improvement",
            ("module_local_score_improved",),
            local_score_before=1.0,
            local_score_after=2.0,
        ),
        before=_signal(),
        after=_signal(),
        before_result=before,
        after_result=after,
    )
    assert audit["status"] == "observed"
    assert (
        "target_certificate_invariant_failure:decreased"
        in audit["observable_deltas"]
    )


def test_field_postcondition_measures_a_delta_not_preexisting_content():
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "require_evidence_field: impact_evidence_refs",
    )
    empty = {
        "combined_prosecution": {
            "candidates": [{"impact_evidence_refs": []}],
        },
    }
    populated = {
        "combined_prosecution": {
            "candidates": [{"impact_evidence_refs": ["event_1"]}],
        },
    }

    assert evaluate_action_spec_postcondition(
        spec, populated, baseline_result=empty,
    )[0] is True
    satisfied, detail = evaluate_action_spec_postcondition(
        spec, populated, baseline_result=populated,
    )
    assert satisfied is False
    assert detail == "field_obligation_not_added:impact_evidence_refs:1->1"


def test_object_postcondition_does_not_credit_preexisting_candidate():
    spec = compile_action_spec(
        "candidate_trigger", "emit_candidate: candidate",
    )
    unchanged = {"census": [{"candidate_id": "candidate_1"}]}

    satisfied, detail = evaluate_action_spec_postcondition(
        spec, unchanged, baseline_result=unchanged,
    )
    assert satisfied is False
    assert detail == "object_not_added:1->1"


def test_prosecution_coverage_action_ignores_foreign_defense_gap():
    spec = compile_action_spec(
        "evidence_obligation", "keep_coverage_incomplete: coverage_state",
    )
    unchanged = {
        "proof_adjudication": {
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
        },
    }

    satisfied, detail = evaluate_action_spec_postcondition(
        spec, unchanged, baseline_result=unchanged,
    )
    assert satisfied is False
    assert detail == "prosecution_coverage_not_opened:complete->complete"

    opened = {
        "proof_adjudication": {
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
        },
    }
    assert evaluate_action_spec_postcondition(
        spec, opened, baseline_result=unchanged,
    )[0] is True


def test_invariant_violation_routes_to_l1_provider_generation_nonconformance():
    """A malformed artifact carrying a registered relational invariant failure
    (``failed_invariants``) is a provider generation nonconformance: the
    provider emitted populated fields whose cross-field relation is wrong, so
    the verifier-instruction surface can be evolved. It routes to L1, not the
    detector boundary, and compiles an executable repair edge bound to that
    invariant via the ``enforce_certificate_invariant`` opcode.

    The three non-L1 malformed origins (validator_contract_fault,
    transport_or_parse_fault, route_or_config_fault) are covered by the
    parametrized classification-completeness test below.
    """
    detection = {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "paca_tcl",
        "census": [{
            "candidate_id": "candidate_1",
            "candidate_kind": "claim",
            "origin_hypothesis_event_id": "event_1",
        }],
        "combined_prosecution": {"candidates": [{
            "certificate_id": "error_1",
            "source_candidate_id": "candidate_1",
            "origin_event_id": "event_1",
        }]},
        "proof_adjudication": {
            "state": "unresolved",
            "required_candidate_count": 1,
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "surviving_error_certificate_ids": [],
            "prosecution_coverage": "incomplete",
            "defense_coverage": "complete",
            "error_audits": [{
                "decision": "quarantine",
                "certificate_id": "error_1",
                "failed_checks": ["ordered_chain"],
                "failed_fields": [
                    "origin_event_id", "agent_control_event_id",
                    "impact_event_ids",
                ],
                "missing_fields": [],
                "failed_invariants": ["ordered_chain"],
            }],
            "defense_audits": [],
        },
        "evidence_health": {
            "required_candidate_count": 1,
            "construct_accepted_candidate_count": 1,
            "construct_raw_candidate_count": 1,
        },
    }
    signal = _signal()
    artifacts = project_provider_artifacts(
        detection_result=detection,
        signal=signal,
        primary_type="claim",
    )
    attribution = classify_failure({
        "adjudication": detection["proof_adjudication"],
        "provider_artifacts": artifacts,
    })
    # provider_generation_nonconformance routes to L1 (not detector boundary):
    # the provider emitted fields that violate a registered relational
    # invariant, which is an evolvable provider-instruction gap.
    assert attribution.layer == LAYER_L1
    assert attribution.artifact == "atomic_verifier_instruction"
    assert attribution.reason_code == "provider_artifact_malformed"
    assert attribution.evidence["failure_origin"] == (
        "provider_generation_nonconformance"
    )
    assert attribution.evidence["failed_invariants"] == ["ordered_chain"]
    # The episode must compile an executable repair edge bound to the
    # invariant (enforce_certificate_invariant), not raise UnaddressableFailure.
    episode = build_failure_episode(
        detection_result=detection,
        signal=signal,
        attribution=attribution,
        evolution_direction="unresolved_to_error",
        record_key="opaque-record",
        allowed_modules=("atomic_verifier_instruction",),
    )
    assert len(episode.causal_slice.repair_frontier) == 1
    edge = episode.causal_slice.repair_frontier[0]
    assert edge.owner_module == "atomic_verifier_instruction"
    assert edge.field == "ordered_chain"
    assert edge.observed == "malformed"
    assert edge.required == "well_formed"


def test_semantically_missing_certificate_remains_learnable_l1_failure():
    attribution = classify_failure({
        "adjudication": {"state": "unresolved"},
        "provider_artifacts": [{
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": False,
            "missing_fields": ["certificate"],
        }],
    })

    assert attribution.layer == LAYER_L1
    assert attribution.artifact == "evidence_obligation"
    assert attribution.reason_code == "provider_artifact_missing"


def test_failure_episode_preserves_the_complete_proof_cut_frontier():
    attribution = classify_failure({
        "adjudication": {
            "state": "unresolved",
            "terminal_basis": {"blocking_facts": [
                {"predicate": "required_candidates_terminal"},
                {"predicate": "prosecution_coverage_complete"},
            ]},
        },
        "provider_artifacts": [
            {
                "kind": "candidate", "malformed": False,
                "missing_fields": ["candidate"],
            },
            {
                "kind": "error_certificate", "malformed": False,
                "missing_fields": ["certificate"],
            },
        ],
    })
    assert len(attribution.evidence["artifact_frontier"]) == 2

    episode = build_failure_episode(
        detection_result={
            "state": "unresolved",
            "proof_adjudication": {
                "state": "unresolved",
                "terminal_basis": {"blocking_facts": [
                    {"predicate": "required_candidates_terminal"},
                    {"predicate": "prosecution_coverage_complete"},
                ]},
            },
        },
        signal=_signal(),
        attribution=attribution,
        evolution_direction="unresolved_to_error",
        record_key="opaque-record",
        allowed_modules=("candidate_trigger", "evidence_obligation"),
    )

    assert {
        (edge.owner_module, edge.field, edge.blocking_predicate)
        for edge in episode.causal_slice.repair_frontier
    } == {
        ("candidate_trigger", "candidate", "required_candidates_terminal"),
        (
            "evidence_obligation", "certificate_id",
            "prosecution_coverage_complete",
        ),
    }
    assert all(edge.edge_id.startswith("edge_") for edge in episode.causal_slice.repair_frontier)
    assert episode.causal_slice.failure_edge in episode.causal_slice.repair_frontier


def test_legacy_certificate_alias_is_normalized_only_at_execution_edge():
    episode = build_failure_episode(
        detection_result={"state": "unresolved"},
        signal=_signal(),
        attribution=FailureAttribution(
            layer=LAYER_L1,
            artifact="evidence_obligation",
            reason_code="provider_artifact_missing",
            evidence={"kind": "error_certificate", "missing_fields": ["certificate"]},
        ),
        evolution_direction="unresolved_to_error",
        record_key="opaque-record",
        allowed_modules=("evidence_obligation",),
    )

    assert episode.causal_slice.missing_fields == ("certificate",)
    assert episode.causal_slice.failure_edge is not None
    assert episode.causal_slice.failure_edge.field == "certificate_id"


def test_legacy_field_named_audit_uses_the_addressed_certificate_row():
    detection = {
        "state": "unresolved",
        "combined_prosecution": {"candidates": [{
            "certificate_id": "error_bad",
            "impact_evidence_refs": [],
        }]},
        "proof_adjudication": {
            "error_audits": [{
                "certificate_id": "error_bad",
                "decision": "quarantine",
                "failed_checks": ["impact_evidence_refs"],
            }],
        },
    }

    assert project_provider_artifacts(
        detection_result=detection,
        signal=_signal(),
        primary_type="factual",
    ) == [{
        "kind": "error_certificate",
        "module_hint": "",
        "malformed": True,
        "missing_fields": ["impact_evidence_refs"],
        "artifact_id": "error_bad",
        "source": "error_audits",
    }]


def test_unowned_failure_edge_never_reaches_reflection():
    with pytest.raises(
        UnaddressableFailure, match="failure_edge_outside_action_vocabulary",
    ):
        build_failure_episode(
            detection_result={"state": "unresolved"},
            signal=_signal(),
            attribution=FailureAttribution(
                layer=LAYER_L1,
                artifact="atomic_verifier_instruction",
                reason_code="provider_artifact_malformed",
                evidence={"missing_fields": ["not_a_real_surface"]},
            ),
            evolution_direction="unresolved_to_error",
            record_key="opaque-record",
            allowed_modules=("atomic_verifier_instruction",),
        )


def test_experience_prompt_preserves_the_mechanism_contract():
    pattern = FailurePattern(
        mechanism_signature=("L1", "atomic_verifier_instruction"),
        failure_shape="malformed_output",
        causal_position="producer",
        impact_scope="single_candidate",
    )
    text = format_experience_for_prompt([
        ExperienceMatch(
            pattern=pattern,
            similarity_score=1.0,
            successful_interventions=({
                "condition": "certificate events are out of causal order",
                "action_spec": {
                    "module_id": "atomic_verifier_instruction",
                    "operation": "enforce_certificate_invariant",
                    "output_field": "ordered_chain",
                    "postcondition": "certificate_invariant_repaired",
                    "observable_field": "causal_field_coverage",
                    "expected_direction": "increase",
                },
                "outcome": {"decision": "accept_local"},
            },),
        ),
    ])

    assert "<module>atomic_verifier_instruction</module>" in text
    assert "<condition>certificate events are out of causal order</condition>" in text
    assert "<output_field>ordered_chain</output_field>" in text
    assert "<postcondition>certificate_invariant_repaired</postcondition>" in text
    assert "<module>unknown</module>" not in text


def test_compiled_skill_preserves_typed_condition_without_prose_parsing():
    baseline = create_baseline_genome()
    condition = "a certificate cites an event, outside the observable trajectory"
    action = "enforce_certificate_invariant: ordered_chain"
    spec = compile_action_spec("atomic_verifier_instruction", action)
    slots = {
        "condition": condition,
        "action": action,
        "evidence_anchor": "certificate event is not trajectory-addressable",
    }
    rule = render_rule(RuleSpec(
        condition=condition,
        action=action,
        evidence_anchor=slots["evidence_anchor"],
        module_id="atomic_verifier_instruction",
        direction="error",
    ))
    evolved = apply_edit(baseline, SkillEdit(
        edit_id="edit_typed_condition",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content=rule,
        rule_slots=slots,
        action_spec=spec.to_dict(),
        expected_effect=spec.expected_effect_text(),
        expected_effect_contract=spec.effect_contract(condition),
    ))

    compiled = compile_genome_for_paca(evolved, baseline=baseline)
    rule_id = next(iter(compiled.action_specs))
    assert compiled.rule_conditions[rule_id] == condition


def test_execution_hash_covers_typed_runtime_semantics_only():
    baseline = create_baseline_genome()
    condition = "a certificate has an out-of-order causal chain"
    action = "enforce_certificate_invariant: ordered_chain"
    spec = compile_action_spec("atomic_verifier_instruction", action)
    slots = {
        "condition": condition,
        "action": action,
        "evidence_anchor": "certificate event ordering",
    }
    evolved = apply_edit(baseline, SkillEdit(
        edit_id="edit_execution_identity",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content=render_rule(RuleSpec(
            condition=condition,
            action=action,
            evidence_anchor=slots["evidence_anchor"],
            module_id="atomic_verifier_instruction",
            direction="error",
        )),
        rule_slots=slots,
        action_spec=spec.to_dict(),
    ))
    module = evolved.modules["atomic_verifier_instruction"]
    index = len(module.rules) - 1

    bookkeeping_only = evolved.clone()
    bookkeeping_meta = bookkeeping_only.modules[
        "atomic_verifier_instruction"
    ].get_rule_meta(index)
    bookkeeping_meta["fitness_status"] = "strong"
    bookkeeping_only.modules["atomic_verifier_instruction"].set_rule_meta(
        index, bookkeeping_meta,
    )
    assert bookkeeping_only.execution_hash == evolved.execution_hash

    behavior_changed = evolved.clone()
    changed_meta = behavior_changed.modules[
        "atomic_verifier_instruction"
    ].get_rule_meta(index)
    changed_meta["action_spec"] = compile_action_spec(
        "atomic_verifier_instruction",
        "require_evidence_field: impact_evidence_refs",
    ).to_dict()
    behavior_changed.modules["atomic_verifier_instruction"].set_rule_meta(
        index, changed_meta,
    )
    assert behavior_changed.content_hash == evolved.content_hash
    assert behavior_changed.execution_hash != evolved.execution_hash
    assert compile_genome_for_paca(evolved).genome_hash == evolved.execution_hash

    typed = evolved.clone()
    typed_meta = typed.modules["atomic_verifier_instruction"].get_rule_meta(index)
    typed_meta["condition_spec"] = _condition_spec(
        "failed_invariant", "ordered_chain",
    )
    typed.modules["atomic_verifier_instruction"].set_rule_meta(index, typed_meta)
    assert typed.content_hash == evolved.content_hash
    assert typed.execution_hash != evolved.execution_hash


def test_proof_court_exposes_terminal_basis_without_changing_verdict():
    result = adjudicate_proof_court(
        required_candidates=[],
        prosecution={"coverage_state": "incomplete", "candidates": []},
        defense={"coverage_state": "complete", "candidates": []},
        packet={"events": [{"event_id": "event_1"}]},
        apply_materiality_check=False,
        apply_commitment_check=False,
        apply_consensus_gate_flag=False,
    )

    assert result["state"] == "unresolved"
    assert result["terminal_basis"]["state"] == "unresolved"
    assert result["terminal_basis"]["blocking_facts"] == [{
        "predicate": "prosecution_coverage_complete",
        "observed": "incomplete",
        "required": "complete",
    }]


def test_gold_improvement_cannot_credit_an_unexecuted_skill():
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_test",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content=(
            "When certificate events are out of causal order, enforce the "
            "certificate validation invariant: ordered_chain."
        ),
        action_spec=action_spec,
    )
    before = {
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    replay = {
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    credited, audit = _attribute_intervention_fitness(
        FitnessAssessment(
            "strong_improvement",
            ("gold_assisted_task_score_improved",),
            task_score_before=1,
            task_score_after=2,
            local_score_before=1.0,
            local_score_after=2.0,
        ),
        edit=edit,
        result=replay,
        baseline_result=before,
    )

    assert audit["status"] == "not_executed"
    assert credited.decision == "no_effect"
    assert credited.reasons == ("intervention_not_executed",)


def test_provider_self_report_cannot_replace_action_transition():
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_self_report",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        action_spec=action_spec,
    )
    unchanged = {
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "applied_rule_trace": {
            "focused_prosecution_route": ["rule_1"],
        },
        "rule_effects_trace": {
            "focused_prosecution_route": [{
                "rule_id": "rule_1",
                "output_fields": ["ordered_chain"],
            }],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    credited, audit = _attribute_intervention_fitness(
        FitnessAssessment(
            "strong_improvement",
            ("gold_assisted_task_score_improved",),
            task_score_before=1,
            task_score_after=2,
        ),
        edit=edit,
        result=unchanged,
        baseline_result=unchanged,
    )

    assert audit["applied_rule_ids"] == ["rule_1"]
    assert audit["effect_rule_ids"] == ["rule_1"]
    assert audit["postcondition_satisfied"] is False
    assert audit["status"] == "not_executed"
    assert credited.reasons == ("intervention_not_executed",)


def test_proxy_effect_metric_cannot_override_causal_execution_owner():
    local = FitnessAssessment(
        "local_improvement",
        ("module_local_score_improved:3.300->4.300",),
        task_score_before=1,
        task_score_after=1,
        local_score_before=3.3,
        local_score_after=4.3,
    )
    retained = _enforce_expected_effect_observation(
        local,
        {"status": "not_observed", "observable_deltas": ["other:increased"]},
        "executed",
    )
    assert retained == local

    unexecuted = FitnessAssessment(
        "no_effect",
        ("intervention_not_executed",),
        local_score_before=1.0,
        local_score_after=1.0,
    )
    not_rescued = _enforce_expected_effect_observation(
        unexecuted,
        {"status": "observed", "target_output_changed": True},
        "not_executed",
    )
    assert not_rescued == unexecuted


def test_candidate_action_must_bind_to_the_diagnosed_failure_edge():
    common = {
        "target_module": "atomic_verifier_instruction",
        "operation": "add_rule",
        "condition": "certificate events are out of causal order",
        "evidence_anchor": "certificate event order validation",
        "condition_spec": _condition_spec(
            "failed_invariant", "ordered_chain",
        ),
    }
    assert _prevalidate_candidate(
        {
            **common,
            "action": "enforce_certificate_invariant: ordered_chain",
        },
        ("atomic_verifier_instruction",),
        failure_edge_field="ordered_chain",
    ) is None

    reason = _prevalidate_candidate(
        {
            **common,
            "action": "require_evidence_field: impact_evidence_refs",
        },
        ("atomic_verifier_instruction",),
        failure_edge_field="ordered_chain",
    )
    assert reason == (
        "action_not_bound_to_failure_edge:"
        "ordered_chain->impact_evidence_refs"
    )


def test_candidate_selects_one_owned_frontier_edge_and_matches_source():
    edge = {
        "edge_id": "edge_ordered",
        "owner_module": "atomic_verifier_instruction",
        "field": "ordered_chain",
    }
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "add_rule",
        "condition": "the certificate causal chain is out of order",
        "condition_spec": _condition_spec(
            "failed_invariant", "ordered_chain",
        ),
        "repair_edge_id": "edge_ordered",
        "action": "enforce_certificate_invariant: ordered_chain",
        "evidence_anchor": "certificate causal ordering",
    }
    source_features = {
        "focused_prosecution_route": [{
            "failed_invariant": ["ordered_chain"],
        }],
    }
    assert _prevalidate_candidate(
        candidate,
        ("atomic_verifier_instruction",),
        repair_frontier=(edge,),
        runtime_condition_features=source_features,
    ) is None

    assert _prevalidate_candidate(
        {
            **candidate,
            "condition_spec": {
                **candidate["condition_spec"],
                "value": "impact_evidence",
            },
        },
        ("atomic_verifier_instruction",),
        repair_frontier=(edge,),
        runtime_condition_features=source_features,
    ) == "condition_not_observed_on_source_route"

    assert _prevalidate_candidate(
        {**candidate, "repair_edge_id": "edge_unknown"},
        ("atomic_verifier_instruction",),
        repair_frontier=(edge,),
        runtime_condition_features=source_features,
    ) == "repair_edge_id_unknown:edge_unknown"


def test_typed_condition_projector_decides_before_provider_call(tmp_path):
    baseline = create_baseline_genome()
    condition = "the focused candidate is an omission"
    action = "emit_error_certificate: certificate_id"
    action_spec = compile_action_spec("evidence_obligation", action)
    condition_spec = _condition_spec("candidate_kind", "omission")
    slots = {
        "condition": condition,
        "action": action,
        "evidence_anchor": "focused candidate kind",
    }
    edit = SkillEdit(
        edit_id="edit_typed_applicability",
        target_module="evidence_obligation",
        operation="add_rule",
        new_content=render_rule(RuleSpec(
            condition=condition,
            action=action,
            evidence_anchor=slots["evidence_anchor"],
            module_id="evidence_obligation",
            direction="process",
        )),
        rule_slots=slots,
        action_spec=action_spec.to_dict(),
        condition_spec=condition_spec,
    )
    evolved = apply_edit(baseline, edit)
    compiled = compile_genome_for_paca(evolved, baseline=baseline)
    rule_id = next(iter(compiled.condition_specs))
    route = {}
    calls = []

    def base_provider(**kwargs):
        calls.append(kwargs)
        return {"coverage_state": "complete", "candidates": []}, {}

    wrapped = make_skill_aware_provider(
        base_provider,
        compiled=compiled,
        config={"focused_prosecution_route": route},
    )
    _value, nonmatch = wrapped(
        route=route,
        system="BASE",
        user='FOCUSED_CANDIDATE={"candidate_kind":"unsupported claim"}',
        cache_dir=tmp_path / "nonmatch",
    )
    assert calls[-1]["system"] == "BASE"
    assert nonmatch["bccse_condition_not_matched_rule_ids"] == [rule_id]
    assert nonmatch["bccse_injected_rule_ids"] == []

    _value, matched = wrapped(
        route=route,
        system="BASE",
        user='FOCUSED_CANDIDATE={"candidate_kind":"omission"}',
        cache_dir=tmp_path / "match",
    )
    assert calls[-1]["system"] != "BASE"
    assert matched["bccse_condition_matched_rule_ids"] == [rule_id]
    assert matched["bccse_injected_rule_ids"] == [rule_id]

    features = project_route_condition_features(
        "focused_prosecution_route",
        'FOCUSED_CANDIDATE={"candidate_kind":"omission"}',
    )
    assert evaluate_condition_spec(condition_spec, features).matched is True


def test_causal_audit_distinguishes_nonmatch_from_model_ignore():
    condition_spec = _condition_spec("failed_invariant", "ordered_chain")
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_condition_audit",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="typed rule",
        action_spec=action_spec,
        condition_spec=condition_spec,
    )
    base = {
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    _credited, nonmatch = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result={
            **base,
            "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]},
            "condition_not_matched_rule_trace": {
                "focused_prosecution_route": ["rule_1"],
            },
        },
        baseline_result=base,
    )
    assert nonmatch["status"] == "condition_not_matched"

    _credited, ignored = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result={
            **base,
            "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]},
            "condition_matched_rule_trace": {
                "focused_prosecution_route": ["rule_1"],
            },
            "injected_rule_trace": {"focused_prosecution_route": ["rule_1"]},
        },
        baseline_result=base,
    )
    assert ignored["status"] == "model_ignored_or_action_failed"


# ============================================================
# Fix #1: malformed failure-origin taxonomy (acceptance #1 + #2)
# ============================================================


def _malformed_artifact(**overrides):
    """Build a malformed provider artifact with typed audit evidence."""
    base = {
        "kind": "error_certificate",
        "module_hint": "",
        "malformed": True,
        "missing_fields": [],
        "failed_fields": [],
        "failed_invariants": [],
        "artifact_id": "error_1",
        "source": "error_audits",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "artifact, expected_origin",
    [
        # provider_generation nonconformance: registered relational invariant
        # failed on populated provider-emitted fields -> L1-addressable.
        (
            _malformed_artifact(
                failed_fields=["origin_event_id", "impact_event_ids"],
                failed_invariants=["ordered_chain"],
            ),
            ORIGIN_PROVIDER_GENERATION,
        ),
        # validator contract fault: only field-level failures, no invariant,
        # no missing fields -> the validator's own mapping rejected them.
        (
            _malformed_artifact(
                failed_fields=["claim_kind"],
                failed_invariants=[],
            ),
            ORIGIN_VALIDATOR_CONTRACT,
        ),
        # transport or parse fault: provider error / appeal crash with no
        # field-level audit.
        (
            _malformed_artifact(
                failed_fields=[],
                failed_invariants=[],
                source="appeal_status",
            ),
            ORIGIN_TRANSPORT_PARSE,
        ),
        # route or config fault: no typed audit evidence at all -> fail-closed.
        (
            _malformed_artifact(
                failed_fields=[],
                failed_invariants=[],
                source="",
            ),
            ORIGIN_ROUTE_CONFIG,
        ),
    ],
    ids=[
        "provider_generation_nonconformance",
        "validator_contract_fault",
        "transport_or_parse_fault",
        "route_or_config_fault",
    ],
)
def test_malformed_origin_classification(artifact, expected_origin):
    """Each malformed artifact shape maps to exactly one typed origin."""
    assert classify_malformed_origin(artifact) == expected_origin


@pytest.mark.parametrize(
    "origin, expected_layer",
    [
        (ORIGIN_PROVIDER_GENERATION, LAYER_L1),
        (ORIGIN_VALIDATOR_CONTRACT, LAYER_DETECTOR),
        (ORIGIN_TRANSPORT_PARSE, LAYER_DETECTOR),
        (ORIGIN_ROUTE_CONFIG, LAYER_DETECTOR),
    ],
    ids=[
        "provider_generation_to_l1",
        "validator_contract_to_detector",
        "transport_parse_to_detector",
        "route_config_to_detector",
    ],
)
def test_malformed_origin_layer_routing(origin, expected_layer):
    """Only provider_generation_nonconformance routes to L1; the rest stop at
    the detector / inference-contract boundary."""
    assert origin_layer(origin) == expected_layer


def test_malformed_origin_does_not_use_single_boolean():
    """A single ``malformed`` boolean must not decide the layer. Two artifacts
    both malformed=True but with different typed evidence route to different
    layers: the invariant violation to L1, the bare one to the boundary."""
    invariant_artifact = _malformed_artifact(
        failed_invariants=["ordered_chain"],
        failed_fields=["origin_event_id"],
    )
    bare_artifact = _malformed_artifact(
        failed_invariants=[], failed_fields=[], source="",
    )
    rec_invariant = {
        "adjudication": {},
        "provider_artifacts": [invariant_artifact],
    }
    rec_bare = {
        "adjudication": {},
        "provider_artifacts": [bare_artifact],
    }
    a_inv = classify_failure(rec_invariant)
    a_bare = classify_failure(rec_bare)
    assert a_inv.layer == LAYER_L1
    assert a_inv.evidence["failure_origin"] == ORIGIN_PROVIDER_GENERATION
    assert a_bare.layer == LAYER_DETECTOR
    assert a_bare.evidence["failure_origin"] == ORIGIN_ROUTE_CONFIG


def test_malformed_origin_precedence_l1_over_boundary():
    """When a provider-generation (L1) malformed artifact and a boundary
    malformed artifact co-occur, the L1-addressable origin takes precedence
    so an evolvable provider gap is not masked by a co-occurring transport
    fault."""
    invariant_artifact = _malformed_artifact(
        failed_invariants=["ordered_chain"],
        failed_fields=["origin_event_id"],
    )
    transport_artifact = _malformed_artifact(
        failed_invariants=[], failed_fields=[], source="appeal_status",
    )
    rec = {
        "adjudication": {},
        "provider_artifacts": [transport_artifact, invariant_artifact],
    }
    attr = classify_failure(rec)
    assert attr.layer == LAYER_L1
    assert attr.evidence["failure_origin"] == ORIGIN_PROVIDER_GENERATION
    # The frontier only carries the L1-addressable artifact, not the
    # co-occurring transport artifact.
    frontier = attr.evidence.get("artifact_frontier") or []
    assert len(frontier) == 1
    assert frontier[0]["failed_invariants"] == ["ordered_chain"]


# ============================================================
# Fix #2: L1 executable-completeness strong invariant (acceptance #1)
# ============================================================


def _loc_signal(owner="earliest_origin_tiebreak"):
    return SimpleNamespace(
        gold_label=True, state="unresolved", prosecution_coverage_state="incomplete",
        defense_coverage_state="complete", prosecution_coverage=None,
        defense_coverage=None, candidate_count=1,
        error_type="unresolved_should_error", trigger_source="gold_mismatch",
        target_module=owner, secondary_module="", attribution_confidence=1.0,
        should_evolve=True, localization_status="failed_temporal_fallback",
        crs_complete=True, has_gold=True, responsible_step_correct=False,
    )


def _verdict_signal(owner="atomic_verifier_instruction"):
    return SimpleNamespace(
        gold_label=True, state="verified_error", prosecution_coverage_state="complete",
        defense_coverage_state="complete", prosecution_coverage=None,
        defense_coverage=None, candidate_count=1,
        error_type="unresolved_should_error", trigger_source="gold_mismatch",
        target_module=owner, secondary_module="", attribution_confidence=1.0,
        should_evolve=True, localization_status="not_applicable",
        crs_complete=True, has_gold=True, responsible_step_correct=False,
    )


# Each case: (record, allowed_modules, signal_factory, description).
# For every L1 attribution, build_failure_episode must compile a non-empty
# repair_frontier (the strong invariant). If it cannot, classify_failure must
# not return LAYER_L1 in the first place.
_L1_REASON_CASES = [
    pytest.param(
        {
            "adjudication": {},
            "provider_artifacts": [
                {"kind": "candidate", "malformed": False, "missing_fields": ["candidate"]}
            ],
        },
        ("candidate_trigger",),
        lambda: _loc_signal("candidate_trigger"),
        "provider_artifact_missing",
        id="provider_artifact_missing",
    ),
    pytest.param(
        {
            "adjudication": {},
            "provider_artifacts": [
                _malformed_artifact(
                    failed_fields=["origin_event_id"],
                    failed_invariants=["ordered_chain"],
                )
            ],
        },
        ("atomic_verifier_instruction",),
        lambda: _verdict_signal("atomic_verifier_instruction"),
        "provider_artifact_malformed_invariant",
        id="provider_artifact_malformed_invariant",
    ),
    pytest.param(
        {
            "adjudication": {
                "state": "unresolved",
                "localization_status": "failed_temporal_fallback",
            },
            "provider_artifacts": [],
        },
        ("earliest_origin_tiebreak",),
        _loc_signal,
        "localization_temporal_fallback",
        id="localization_temporal_fallback",
    ),
    pytest.param(
        {
            "adjudication": {
                "state": "verified_error",
                "localization_status": "not_applicable",
            },
            "provider_artifacts": [],
        },
        ("earliest_origin_tiebreak",),
        lambda: _verdict_signal("earliest_origin_tiebreak"),
        "responsible_step_missing",
        id="responsible_step_missing",
    ),
    pytest.param(
        {
            "adjudication": {
                "state": "verified_error",
                "error_audits": [
                    {"decision": "quarantine", "failed_checks": ["certificate_id"]}
                ],
            },
            "provider_artifacts": [],
        },
        ("atomic_verifier_instruction",),
        lambda: _verdict_signal("atomic_verifier_instruction"),
        "quarantine_schema_mismatch",
        id="quarantine_schema_mismatch",
    ),
    pytest.param(
        {
            "adjudication": {
                "state": "verified_error",
                "error_audits": [
                    {"decision": "quarantine", "failed_checks": ["norm_evidence"]}
                ],
            },
            "provider_artifacts": [],
        },
        ("evidence_obligation",),
        lambda: _verdict_signal("evidence_obligation"),
        "quarantine_evidence_missing",
        id="quarantine_evidence_missing",
    ),
]


@pytest.mark.parametrize(
    "record, allowed_modules, signal_factory, expected_reason",
    _L1_REASON_CASES,
)
def test_l1_attribution_compiles_executable_repair_frontier(
    record, allowed_modules, signal_factory, expected_reason,
):
    """Acceptance #1: every LAYER_L1 reason code must either compile a
    non-empty executable repair frontier or not be classified as L1.

    This is the strong invariant: ``classify_failure`` returns LAYER_L1 =>
    the attribution can compile at least one edge with owner / route /
    object / field / operation / postcondition. No "nominally L1 but no
    execution semantics" intermediate state may reach the evolution runner.
    """
    attribution = classify_failure(record)
    # The gate inside classify_failure guarantees: if layer == L1, the
    # attribution can compile an executable obligation.
    if attribution.layer == LAYER_L1:
        assert can_compile_repair_obligation(attribution) is True
        # And the actual episode compilation must succeed with a non-empty
        # repair frontier — the gate's promise is realized downstream.
        signal = signal_factory()
        episode = build_failure_episode(
            detection_result=record,
            signal=signal,
            attribution=attribution,
            evolution_direction="unresolved_to_error",
            record_key="test",
            allowed_modules=allowed_modules,
        )
        assert len(episode.causal_slice.repair_frontier) >= 1, (
            f"L1 attribution {attribution.reason_code} compiled no repair edge"
        )
        edge = episode.causal_slice.repair_frontier[0]
        assert edge.owner_module in allowed_modules
        assert edge.field
        assert edge.observed
        assert edge.required
    else:
        # If demoted, it must not be L1 and must carry the demotion reason.
        assert attribution.layer != LAYER_L1
        assert attribution.reason_code == "demoted_l1_no_executable_obligation"


def test_demoted_l1_does_not_reach_episode_builder():
    """A record that would produce a 'nominally L1' attribution with no
    executable field is demoted to NONE by the gate, so the evolution runner
    never calls build_failure_episode on it (which would raise
    UnaddressableFailure and skip reflection)."""
    # An L1 attribution whose owner has opcodes but whose evidence carries no
    # field at all (not even a synthesized one): e.g. a hypothetical reason
    # code outside both _REASON_CARRIES_ARTIFACT_FIELDS and
    # _SYNTHESIZED_FIELD_REASONS.
    attribution = FailureAttribution(
        layer=LAYER_L1,
        artifact="candidate_trigger",
        reason_code="unknown_future_reason",
        evidence={"failure_origin": ""},
    )
    assert can_compile_repair_obligation(attribution) is False


# ============================================================
# Fix #3: owner ext/del consistency (acceptance: proof-cut owns owners)
# ============================================================


def _defense_signal(**overrides):
    values = {
        "gold_label": None,
        "state": "unresolved",
        "prosecution_coverage_state": "complete",
        "defense_coverage_state": "complete",
        "prosecution_coverage": None,
        "defense_coverage": None,
        "candidate_count": 1,
        "error_type": None,
        "trigger_source": "self_supervision",
        "target_module": "clean_defense_boundary",
        "secondary_module": "",
        "attribution_confidence": 1.0,
        "should_evolve": True,
        "localization_status": "not_applicable",
        "crs_complete": True,
        "has_gold": False,
        "responsible_step_correct": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_frontier_named_clean_defense_boundary_not_deleted_when_coverage_complete():
    """When defense coverage is complete, ``_allowed_l1_modules`` must not
    delete ``clean_defense_boundary`` if the proof-cut frontier names it as
    an owner. Deleting it would orphan a legitimate repair edge."""
    signal = _defense_signal()
    frontier = [{"owner_module": "clean_defense_boundary", "kind": "defense_certificate"}]
    modules = _allowed_l1_modules(signal, "clean_defense_boundary", frontier)
    assert "clean_defense_boundary" in modules


def test_clean_defense_boundary_deleted_when_not_in_frontier_and_coverage_complete():
    """When defense coverage is complete and the frontier does NOT name
    ``clean_defense_boundary``, it is correctly removed (saturation point is
    abstention_rule, not a further boundary finding)."""
    signal = _defense_signal()
    # attribution owner is evidence_obligation, frontier has no defense owner
    frontier = [{"owner_module": "evidence_obligation", "kind": "error_certificate"}]
    modules = _allowed_l1_modules(signal, "evidence_obligation", frontier)
    assert "clean_defense_boundary" not in modules


def test_process_signal_not_generically_added_without_gold():
    """Process-signal owners (polarity-based) must not be added as a general
    state heuristic when there is no gold label. Only the proof-cut
    attribution + frontier owners should appear."""
    signal = _defense_signal(
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
    )
    frontier = [{"owner_module": "evidence_obligation", "kind": "error_certificate"}]
    modules = _allowed_l1_modules(signal, "evidence_obligation", frontier)
    # No gold => process-signal modules not added
    assert "atomic_verifier_instruction" not in modules
    assert "evidence_obligation" in modules


def test_process_signal_added_on_gold_polarity_path():
    """On the gold-polarity path (gold label present), process-signal owners
    are a legitimate exploration surface and ARE added."""
    signal = _defense_signal(
        gold_label=True,
        has_gold=True,
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        error_type="unresolved_should_error",
    )
    frontier = [{"owner_module": "evidence_obligation", "kind": "error_certificate"}]
    modules = _allowed_l1_modules(signal, "evidence_obligation", frontier)
    # Gold-polarity => secondary module is added as exploration surface
    assert "evidence_obligation" in modules
    assert "atomic_verifier_instruction" in modules


# ============================================================
# Fix #5: ConditionSpec expressive power (all_of/any_of/not + binding)
# ============================================================

from sehd_v6.core.bccse.condition_spec import (
    SCHEMA_VERSION as COND_SPEC_V2,
    SCHEMA_VERSION_V1 as COND_SPEC_V1,
    evaluate_condition_spec,
    validate_condition_spec,
)


def _v2_atom(feature, value, *, value_type="string", binding="route_scoped"):
    return {
        "schema_version": COND_SPEC_V2,
        "feature_source": "provider_input",
        "feature": feature,
        "operator": "equals" if value_type == "boolean" else "contains",
        "value_type": value_type,
        "value": value,
        "scope": "action_spec_routes",
        "evidence_binding": "pre_call_projection",
        "combinator": "atom",
        "binding": binding,
    }


def _v2_combine(combinator, children, *, binding="route_scoped"):
    return {
        "schema_version": COND_SPEC_V2,
        "feature_source": "provider_input",
        "feature": "",
        "operator": "combine",
        "value_type": "string",
        "value": "",
        "scope": "action_spec_routes",
        "evidence_binding": "pre_call_projection",
        "combinator": combinator,
        "children": children,
        "binding": binding,
    }


def test_condition_spec_v1_backward_compatible():
    """v1 schema specs (no combinator/binding fields) still validate."""
    ok, _ = validate_condition_spec(_condition_spec("candidate_kind", "omission"))
    assert ok


def test_condition_spec_v2_atom_validates():
    ok, _ = validate_condition_spec(_v2_atom("candidate_kind", "omission"))
    assert ok


def test_condition_spec_all_of_evaluates():
    spec = _v2_combine("all_of", [
        _v2_atom("candidate_kind", "omission"),
        _v2_atom("has_tool_call", True, value_type="boolean"),
    ])
    ok, _ = validate_condition_spec(spec)
    assert ok
    # Both true -> matched
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["omission"], "has_tool_call": True})
    assert ev.matched
    # One false -> not matched
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["contradiction"], "has_tool_call": True})
    assert not ev.matched


def test_condition_spec_any_of_evaluates():
    spec = _v2_combine("any_of", [
        _v2_atom("candidate_kind", "omission"),
        _v2_atom("candidate_kind", "contradiction"),
    ])
    ok, _ = validate_condition_spec(spec)
    assert ok
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["contradiction"]})
    assert ev.matched
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["other"]})
    assert not ev.matched


def test_condition_spec_not_evaluates():
    spec = _v2_combine("not", [_v2_atom("candidate_kind", "omission")])
    ok, _ = validate_condition_spec(spec)
    assert ok
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["contradiction"]})
    assert ev.matched
    ev = evaluate_condition_spec(spec, {"candidate_kind": ["omission"]})
    assert not ev.matched


def test_condition_spec_not_requires_single_child():
    spec = _v2_combine("not", [
        _v2_atom("candidate_kind", "omission"),
        _v2_atom("candidate_kind", "contradiction"),
    ])
    ok, reason = validate_condition_spec(spec)
    assert not ok
    assert "single_child" in reason


def test_condition_spec_combinator_requires_children():
    spec = _v2_combine("all_of", [])
    ok, reason = validate_condition_spec(spec)
    assert not ok
    assert "no_children" in reason


def test_condition_spec_binding_validated():
    ok, _ = validate_condition_spec(_v2_atom("candidate_kind", "omission", binding="candidate_bound"))
    assert ok
    ok, reason = validate_condition_spec(_v2_atom("candidate_kind", "omission", binding="invalid"))
    assert not ok
    assert "binding_invalid" in reason


def test_condition_spec_combinator_invalid_rejected():
    spec = _v2_combine("xor", [_v2_atom("candidate_kind", "omission")])
    ok, reason = validate_condition_spec(spec)
    assert not ok
    assert "combinator_invalid" in reason


# ============================================================
# Fix #6: four-event distinction in causal audit
# ============================================================


def test_causal_audit_carries_four_event_tokens():
    """The causal audit must distinguish four independently-observable events:
    action_dispatched, provider_complied, target_edge_changed, terminal_changed.
    These must not be conflated into a single status string."""
    condition_spec = _condition_spec("failed_invariant", "ordered_chain")
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_four_event",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="typed rule",
        action_spec=action_spec,
        condition_spec=condition_spec,
    )
    base = {
        "state": "unresolved",
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    _credited, audit = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result={
            **base,
            "state": "verified_error",
            "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]},
            "injected_rule_trace": {"focused_prosecution_route": ["rule_1"]},
        },
        baseline_result=base,
    )
    # Four-event tokens must be present and independently observable.
    assert "action_dispatched" in audit
    assert "provider_complied" in audit
    assert "target_edge_changed" in audit
    assert "terminal_changed" in audit
    # action_dispatched: rule was injected
    assert audit["action_dispatched"] is True
    # terminal_changed: state moved unresolved -> verified_error
    assert audit["terminal_changed"] is True
    assert audit["baseline_terminal"] == "unresolved"
    assert audit["replay_terminal"] == "verified_error"


# ============================================================
# Fix #7: edge-level counterfactual causal credit
# ============================================================


def test_edge_counterfactual_blocking_predicate_still_present_blocks_credit():
    """When the selected edge's blocking_predicate is still present in the
    replay's terminal_basis.blocking_facts, the intervention does NOT receive
    causal credit — even if the aggregate postcondition passed. The edge's
    downstream predicate did not clear."""
    condition_spec = _condition_spec("failed_invariant", "ordered_chain")
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_edge_blocked",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="typed rule",
        action_spec=action_spec,
        condition_spec=condition_spec,
        repair_edge={
            "edge_id": "edge_1",
            "owner_module": "atomic_verifier_instruction",
            "field": "ordered_chain",
            "observed": "malformed",
            "required": "well_formed",
            "source_artifact_id": "error_1",
            "blocking_predicate": "ordered_chain",
        },
    )
    base = {
        "state": "unresolved",
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
            "terminal_basis": {
                "state": "unresolved",
                "blocking_facts": [{"predicate": "ordered_chain"}],
            },
        },
    }
    # Replay: blocking_predicate still present -> edge counterfactual fails
    replay = {
        **base,
        "state": "unresolved",
        "proof_adjudication": {
            **base["proof_adjudication"],
            "terminal_basis": {
                "state": "unresolved",
                "blocking_facts": [{"predicate": "ordered_chain"}],
            },
        },
        "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]},
        "injected_rule_trace": {"focused_prosecution_route": ["rule_1"]},
    }
    _credited, audit = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result=replay,
        baseline_result=base,
    )
    assert audit["edge_counterfactual_met"] is False
    assert "blocking_predicate_still_present" in audit["edge_counterfactual_reason"]
    # No executed_rule_ids because edge counterfactual failed
    assert audit["executed_rule_ids"] == []


def test_edge_counterfactual_cleared_grants_credit():
    """When the blocking_predicate clears in the replay, the edge
    counterfactual passes — the downstream predicate disappeared."""
    condition_spec = _condition_spec("failed_invariant", "ordered_chain")
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_edge_cleared",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="typed rule",
        action_spec=action_spec,
        condition_spec=condition_spec,
        repair_edge={
            "edge_id": "edge_1",
            "owner_module": "atomic_verifier_instruction",
            "field": "ordered_chain",
            "observed": "malformed",
            "required": "well_formed",
            "source_artifact_id": "error_1",
            "blocking_predicate": "ordered_chain",
        },
    )
    base = {
        "state": "unresolved",
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
            "terminal_basis": {
                "state": "unresolved",
                "blocking_facts": [{"predicate": "ordered_chain"}],
            },
        },
    }
    # Replay: blocking_predicate cleared -> edge counterfactual passes
    replay = {
        **base,
        "state": "verified_error",
        "proof_adjudication": {
            **base["proof_adjudication"],
            "terminal_basis": {
                "state": "verified_error",
                "blocking_facts": [],
            },
        },
        "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]},
        "injected_rule_trace": {"focused_prosecution_route": ["rule_1"]},
    }
    _credited, audit = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result=replay,
        baseline_result=base,
    )
    assert audit["edge_counterfactual_met"] is True
    assert audit["edge_counterfactual_reason"] == "edge_counterfactual_verified"


def test_edge_counterfactual_no_repair_edge_passes_vacuously():
    """When no repair_edge is present (e.g. remove_rule or legacy edits), the
    edge-level check passes vacuously — it only constrains add/modify
    candidates that selected a repair edge."""
    condition_spec = _condition_spec("failed_invariant", "ordered_chain")
    action_spec = compile_action_spec(
        "atomic_verifier_instruction",
        "enforce_certificate_invariant: ordered_chain",
    ).to_dict()
    edit = SkillEdit(
        edit_id="edit_no_edge",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="typed rule",
        action_spec=action_spec,
        condition_spec=condition_spec,
        # No repair_edge
    )
    base = {
        "state": "unresolved",
        "compiled_rule_trace": {
            "atomic_verifier_instruction": [{"rule_id": "rule_1"}],
        },
        "proof_adjudication": {
            "error_audits": [{"failed_checks": ["ordered_chain"]}],
        },
    }
    _credited, audit = _attribute_intervention_fitness(
        FitnessAssessment("no_effect", ("unchanged",)),
        edit=edit,
        result={**base, "candidate_rule_trace": {"focused_prosecution_route": ["rule_1"]}},
        baseline_result=base,
    )
    assert audit["edge_counterfactual_met"] is True
    assert audit["edge_counterfactual_reason"] == "no_repair_edge_selected"


# ============================================================
# Fix #8: remove_rule unblocked for FP-direction evolution
# ============================================================


def test_remove_rule_admissible_with_edge_binding_and_rule_index():
    """remove_rule is admissible when it carries a rule_index and binds to a
    frontier edge (the FP's location). This unblocks FP-direction evolution:
    removing/narrowing an over-triggering historical Skill may be the minimal
    causal fix."""
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "remove_rule",
        "rule_index": 0,
        "repair_edge_id": "edge_fp_1",
        "expected_effect_contract": {
            "observable_field": "surviving_certificates",
            "expected_direction": "decrease",
            "expected_scope": "positive_cases",
            "trigger_condition": "over-conviction on clean boundary",
        },
    }
    repair_frontier = [
        {"edge_id": "edge_fp_1", "owner_module": "atomic_verifier_instruction",
         "field": "certificate_id"},
    ]
    reason = _prevalidate_candidate(
        candidate,
        ["atomic_verifier_instruction"],
        repair_frontier=repair_frontier,
    )
    assert reason is None


def test_remove_rule_rejected_without_rule_index():
    """remove_rule without a rule_index cannot identify which rule to remove."""
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "remove_rule",
        "repair_edge_id": "edge_fp_1",
    }
    reason = _prevalidate_candidate(
        candidate,
        ["atomic_verifier_instruction"],
        repair_frontier=[{"edge_id": "edge_fp_1", "owner_module": "atomic_verifier_instruction"}],
    )
    assert reason == "remove_rule_missing_rule_index"


def test_remove_rule_rejected_without_edge_id_when_frontier_present():
    """remove_rule with a frontier but no repair_edge_id does not bind to a
    proof-cut edge — it must name which edge the FP lives on."""
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "remove_rule",
        "rule_index": 0,
    }
    reason = _prevalidate_candidate(
        candidate,
        ["atomic_verifier_instruction"],
        repair_frontier=[{"edge_id": "edge_fp_1", "owner_module": "atomic_verifier_instruction"}],
    )
    assert reason == "remove_rule_edge_id_missing"


def test_remove_rule_rejected_on_owner_mismatch():
    """remove_rule bound to an edge owned by a different module is rejected."""
    candidate = {
        "target_module": "evidence_obligation",
        "operation": "remove_rule",
        "rule_index": 0,
        "repair_edge_id": "edge_fp_1",
    }
    reason = _prevalidate_candidate(
        candidate,
        ["evidence_obligation"],
        repair_frontier=[{"edge_id": "edge_fp_1", "owner_module": "atomic_verifier_instruction"}],
    )
    assert "owner_mismatch" in reason


def test_remove_rule_admissible_without_frontier():
    """When no repair_frontier is present (no active L1 episode), remove_rule
    is still admissible for legacy rule cleanup, as long as rule_index is
    provided."""
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "remove_rule",
        "rule_index": 0,
    }
    reason = _prevalidate_candidate(
        candidate,
        ["atomic_verifier_instruction"],
        repair_frontier=[],
    )
    assert reason is None


# ============================================================
# Fix #4: MechanismRepairPlan (framework-compiled execution semantics)
# ============================================================

from sehd_v6.core.bccse.evolution_runner import (
    _normalize_candidate_from_repair_plan,
)
from sehd_v6.core.bccse.failure_episode import (
    compile_mechanism_repair_plans,
)


def test_mechanism_plan_single_action_auto_selected():
    """A repair edge with exactly one legal operation auto-selects it; the
    model must not override it (no action string to generate)."""
    ep = build_failure_episode(
        detection_result={
            "adjudication": {
                "state": "unresolved",
                "localization_status": "failed_temporal_fallback",
            },
            "provider_artifacts": [],
        },
        signal=_loc_signal("earliest_origin_tiebreak"),
        attribution=classify_failure({
            "adjudication": {
                "state": "unresolved",
                "localization_status": "failed_temporal_fallback",
            },
            "provider_artifacts": [],
        }),
        evolution_direction="unresolved_to_error",
        record_key="test",
        allowed_modules=("earliest_origin_tiebreak",),
    )
    plans = compile_mechanism_repair_plans(
        ep, ["earliest_origin_tiebreak"], "unresolved_to_error",
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.owner_module == "earliest_origin_tiebreak"
    assert plan.output_field == "responsible_event_id"
    assert plan.auto_selected_operation == "select_earliest_origin"
    assert plan.legal_operations == ("select_earliest_origin",)


def test_mechanism_plan_multi_action_requires_model_choice():
    """A repair edge with multiple legal operations requires the model to
    choose one from the legal enum (no auto-selection)."""
    ep = build_failure_episode(
        detection_result={
            "adjudication": {
                "state": "verified_error",
                "error_audits": [
                    {"decision": "quarantine", "failed_checks": ["certificate_id"]}
                ],
            },
            "provider_artifacts": [],
            "proof_adjudication": {
                "state": "verified_error",
                "error_audits": [
                    {"decision": "quarantine", "failed_checks": ["certificate_id"]}
                ],
            },
        },
        signal=_verdict_signal("atomic_verifier_instruction"),
        attribution=classify_failure({
            "adjudication": {
                "state": "verified_error",
                "error_audits": [
                    {"decision": "quarantine", "failed_checks": ["certificate_id"]}
                ],
            },
            "provider_artifacts": [],
        }),
        evolution_direction="unresolved_to_error",
        record_key="test",
        allowed_modules=("atomic_verifier_instruction",),
    )
    plans = compile_mechanism_repair_plans(
        ep, ["atomic_verifier_instruction"], "unresolved_to_error",
    )
    assert len(plans) == 1
    plan = plans[0]
    # certificate_id is the target of both emit_certificate and
    # enforce_certificate_invariant (via ERROR_CHECK_FIELDS), so the model
    # must choose.
    assert len(plan.legal_operations) >= 2
    assert plan.auto_selected_operation == ""


def test_mechanism_plan_skips_non_legal_module():
    """Edges whose owner is not in allowed_modules are skipped — no plan is
    compiled for a module the reflection cannot edit."""
    ep = build_failure_episode(
        detection_result={
            "adjudication": {
                "state": "unresolved",
                "localization_status": "failed_temporal_fallback",
            },
            "provider_artifacts": [],
        },
        signal=_loc_signal("earliest_origin_tiebreak"),
        attribution=classify_failure({
            "adjudication": {
                "state": "unresolved",
                "localization_status": "failed_temporal_fallback",
            },
            "provider_artifacts": [],
        }),
        evolution_direction="unresolved_to_error",
        record_key="test",
        allowed_modules=("earliest_origin_tiebreak",),
    )
    plans = compile_mechanism_repair_plans(
        ep, ["atomic_verifier_instruction"], "unresolved_to_error",
    )
    assert plans == ()


def test_plan_form_candidate_normalized_by_framework():
    """A plan-form candidate (repair_edge_id + opcode, no target_module/action)
    is normalized: the framework fills target_module from the edge's owner and
    action from the opcode + edge field."""
    frontier = [{
        "edge_id": "edge_fp_1",
        "owner_module": "atomic_verifier_instruction",
        "field": "ordered_chain",
    }]
    candidate = {
        "operation": "add_rule",
        "repair_edge_id": "edge_fp_1",
        "opcode": "enforce_certificate_invariant",
        "condition": "when certificate cites events not in trajectory",
    }
    normalized = _normalize_candidate_from_repair_plan(candidate, frontier)
    assert normalized["target_module"] == "atomic_verifier_instruction"
    assert normalized["action"] == "enforce_certificate_invariant: ordered_chain"


def test_legacy_full_form_candidate_preserved():
    """A legacy full-form candidate (already carrying target_module + action)
    is returned unchanged by the normalizer."""
    frontier = [{
        "edge_id": "edge_fp_1",
        "owner_module": "atomic_verifier_instruction",
        "field": "ordered_chain",
    }]
    candidate = {
        "operation": "add_rule",
        "target_module": "atomic_verifier_instruction",
        "repair_edge_id": "edge_fp_1",
        "action": "withhold_certificate: certificate_id",
        "condition": "c",
    }
    normalized = _normalize_candidate_from_repair_plan(candidate, frontier)
    assert normalized["target_module"] == "atomic_verifier_instruction"
    assert normalized["action"] == "withhold_certificate: certificate_id"


def test_plan_form_candidate_passes_prevalidation():
    """A plan-form candidate normalized by the framework passes
    _prevalidate_candidate — the compiled action binds to the selected edge."""
    frontier = [{
        "edge_id": "edge_fp_1",
        "owner_module": "atomic_verifier_instruction",
        "field": "ordered_chain",
        "observed": "malformed",
        "required": "well_formed",
    }]
    candidate = {
        "operation": "add_rule",
        "repair_edge_id": "edge_fp_1",
        "opcode": "enforce_certificate_invariant",
        "condition": "when certificate cites events not in trajectory",
        "condition_spec": _condition_spec("failed_invariant", "ordered_chain"),
        "evidence_anchor": "certificate audit",
    }
    normalized = _normalize_candidate_from_repair_plan(candidate, frontier)
    reason = _prevalidate_candidate(
        normalized,
        ["atomic_verifier_instruction"],
        repair_frontier=frontier,
    )
    assert reason is None
