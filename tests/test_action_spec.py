from src.sehd_v6.core.bccse.action_spec import (
    compile_action_spec,
    sanitize_action_spec,
)
from src.sehd_v6.core.bccse.genome import (
    SkillEdit,
    apply_edit,
    create_baseline_genome,
    validate_edit,
)
from src.sehd_v6.core.bccse.evolution_runner import (
    _attribute_intervention_fitness,
    _experience_intervention_trace,
    _structured_outcome,
)
from src.sehd_v6.core.bccse.fitness import FitnessAssessment
from src.sehd_v6.core.paca_tcl.skill_integration import compile_genome_for_paca


def test_action_spec_is_the_effect_contract_source() -> None:
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "require_evidence_field: norm_evidence_refs",
    )

    assert "focused_prosecution_route" in spec.route_ids
    assert spec.output_object == "verification_certificate"
    assert spec.output_field == "norm_evidence_refs"
    assert spec.postcondition == "field_nonempty"
    assert spec.effect_contract("a certificate lacks norm evidence") == {
        "observable_field": "causal_field_coverage",
        "expected_direction": "increase",
        "expected_scope": "all_cases",
        "trigger_condition": "a certificate lacks norm evidence",
    }


def test_opcode_cannot_target_an_unrelated_route_field() -> None:
    try:
        compile_action_spec(
            "atomic_verifier_instruction",
            "quarantine_certificate: evidence_refs",
        )
    except ValueError as exc:
        assert str(exc) == (
            "action_target_not_owned_by_operation:"
            "quarantine_certificate:evidence_refs"
        )
    else:
        raise AssertionError("quarantine accepted an evidence-array operand")


def test_action_spec_round_trips_with_the_rule_genome() -> None:
    baseline = create_baseline_genome()
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "quarantine_certificate: certificate_id",
    )
    edit = SkillEdit(
        edit_id="edit_action_spec",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content=(
            "When a certificate has no cited trajectory evidence, "
            "quarantine the certificate: certificate_id."
        ),
        rule_slots={
            "condition": "a certificate has no cited trajectory evidence",
            "action": "quarantine_certificate: certificate_id",
            "evidence_anchor": "certificate without cited evidence",
        },
        action_spec=spec.to_dict(),
        expected_effect=spec.expected_effect_text(),
        expected_effect_contract=spec.effect_contract(
            "a certificate has no cited trajectory evidence"
        ),
    )

    evolved = apply_edit(baseline, edit)
    restored = type(evolved).from_dict(evolved.to_dict())
    compiled = compile_genome_for_paca(restored, baseline=baseline)

    rule_id = next(iter(compiled.action_specs))
    assert compiled.action_specs[rule_id] == sanitize_action_spec(spec.to_dict())


def test_rule_slots_cannot_persist_a_different_action_spec() -> None:
    baseline = create_baseline_genome()
    actual = compile_action_spec(
        "atomic_verifier_instruction",
        "quarantine_certificate: certificate_id",
    )
    different = compile_action_spec(
        "atomic_verifier_instruction",
        "require_evidence_field: norm_evidence_refs",
    )
    edit = SkillEdit(
        edit_id="edit_mismatched_action_spec",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content=(
            "When a certificate has no cited trajectory evidence, "
            "quarantine the certificate: certificate_id."
        ),
        rule_slots={
            "condition": "a certificate has no cited trajectory evidence",
            "action": "quarantine_certificate: certificate_id",
            "evidence_anchor": "certificate without cited evidence",
        },
        action_spec=different.to_dict(),
    )

    valid, reason = validate_edit(edit, baseline)

    assert actual.to_dict() != different.to_dict()
    assert not valid
    assert reason == "action_spec_rule_slots_mismatch"


def test_rule_removal_derives_the_inverse_observable_effect() -> None:
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "quarantine_certificate: certificate_id",
    )

    assert spec.removal_effect_contract("certificate lacks evidence") == {
        "observable_field": "quarantined_certificates",
        "expected_direction": "decrease",
        "expected_scope": "all_cases",
        "trigger_condition": "certificate lacks evidence",
    }


def test_experience_preserves_the_executed_action_trajectory() -> None:
    spec = compile_action_spec(
        "clean_defense_boundary",
        "require_evidence_field: target_candidate_id",
    )
    trace = _experience_intervention_trace({
        "applied_rule_trace": {"defense_route": ["rule_123"]},
        "rule_effects_trace": {
            "defense_route": [{
                "rule_id": "rule_123",
                "output_fields": ["target_candidate_id"],
            }],
        },
    })

    outcome = _structured_outcome(
        outcome="improved",
        stage="batch_commit",
        operation="add_rule",
        expected_effect_status="observed",
        action_spec=spec.to_dict(),
        intervention_trace=trace,
    )

    assert outcome["action_spec"] == spec.to_dict()
    assert outcome["intervention_trace"] == {
        "applied_rule_ids": ["rule_123"],
        "output_fields": ["target_candidate_id"],
        "routes": ["defense_route"],
    }
    assert outcome["schema_version"] == "agenthallu.bccse_experience_outcome.v2"


def test_unexecuted_action_cannot_inherit_replay_improvement() -> None:
    spec = compile_action_spec(
        "atomic_verifier_instruction",
        "quarantine_certificate: certificate_id",
    )
    edit = SkillEdit(
        edit_id="edit_unexecuted",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        action_spec=spec.to_dict(),
    )
    assessment, audit = _attribute_intervention_fitness(
        FitnessAssessment("strong_improvement", ("task_score_improved",)),
        edit=edit,
        result={
            "compiled_rule_trace": {
                "atomic_verifier_instruction": [{"rule_id": "rule_123"}],
            },
            "injected_rule_trace": {"qualification_route": ["rule_123"]},
            "applied_rule_trace": {},
            "rule_effects_trace": {},
        },
    )

    assert assessment.decision == "no_effect"
    assert assessment.reasons == ("intervention_not_executed",)
    assert audit["status"] == "not_executed"


def test_executed_action_keeps_its_replay_fitness() -> None:
    spec = compile_action_spec(
        "clean_defense_boundary",
        "require_evidence_field: target_candidate_id",
    )
    edit = SkillEdit(
        edit_id="edit_executed",
        target_module="clean_defense_boundary",
        operation="add_rule",
        action_spec=spec.to_dict(),
    )
    original = FitnessAssessment("strong_improvement", ("task_score_improved",))
    assessment, audit = _attribute_intervention_fitness(
        original,
        edit=edit,
        result={
            "compiled_rule_trace": {
                "clean_defense_boundary": [{"rule_id": "rule_123"}],
            },
            "applied_rule_trace": {"defense_route": ["rule_123"]},
            "rule_effects_trace": {
                "defense_route": [{
                    "rule_id": "rule_123",
                    "output_fields": ["target_candidate_id"],
                }],
            },
        },
    )

    assert assessment is original
    assert audit["status"] == "executed"


def test_plural_output_field_binds_the_singular_action_spec() -> None:
    """The provider reports the JSON collection key it affected
    (``candidates``), while the ActionSpec's ``output_field`` is the singular
    object name (``candidate``). Both vocabularies must bind the same rule,
    or an executed intervention is misreported as ``intervention_not_executed``
    (observed in the 50-case batch on BFCL/028 attempt 1 and SmolAgents/068
    attempt 2: applied_rule_trace and observable deltas both proved execution,
    but the plural field name failed the exact-match binding)."""
    spec = compile_action_spec(
        "candidate_trigger",
        "emit_candidate: candidate",
    )
    edit = SkillEdit(
        edit_id="edit_plural_binding",
        target_module="candidate_trigger",
        operation="add_rule",
        action_spec=spec.to_dict(),
    )
    original = FitnessAssessment("local_improvement", ("task_score_improved",))
    assessment, audit = _attribute_intervention_fitness(
        original,
        edit=edit,
        result={
            "compiled_rule_trace": {
                "candidate_trigger": [{"rule_id": "rule_plural"}],
            },
            "applied_rule_trace": {"construct_census_route": ["rule_plural"]},
            "rule_effects_trace": {
                "construct_census_route": [{
                    "rule_id": "rule_plural",
                    "output_fields": ["candidates"],
                }],
            },
        },
    )

    assert assessment is original
    assert audit["status"] == "executed"
    assert audit["effect_rule_ids"] == ["rule_plural"]
    assert audit["executed_rule_ids"] == ["rule_plural"]


def test_field_binding_accepts_the_singular_plural_pair_in_both_directions() -> None:
    """The binding helper normalizes the singular/plural pair: the provider
    reports the JSON collection key (``candidates``) while the ActionSpec
    names the singular object (``candidate``); both directions must bind."""
    from src.sehd_v6.core.bccse.evolution_runner import (
        _field_binds_output_field,
    )

    assert _field_binds_output_field("candidates", "candidate")
    assert _field_binds_output_field("candidate", "candidates")
    assert _field_binds_output_field("certificate_id", "certificate_id")
    assert not _field_binds_output_field("candidate", "evidence")
    assert not _field_binds_output_field("", "candidate")
    assert not _field_binds_output_field("candidates", "")

# ============================================================
# D1: deterministic postcondition evaluation
# ============================================================

def test_postcondition_object_present_and_absent() -> None:
    from src.sehd_v6.core.bccse.action_spec import (
        evaluate_action_spec_postcondition,
    )

    present_spec = compile_action_spec("candidate_trigger", "emit_candidate: candidate")
    assert present_spec.postcondition == "object_present"
    result = {"paca_census": [{"candidate_id": "c1"}]}
    satisfied, detail = evaluate_action_spec_postcondition(present_spec, result)
    assert satisfied and detail == "object_present"

    absent_spec = compile_action_spec("candidate_trigger", "suppress_candidate: candidate")
    assert absent_spec.postcondition == "object_absent"
    satisfied, _ = evaluate_action_spec_postcondition(absent_spec, {})
    assert satisfied

    # A stale candidate contradicts the absence obligation.
    satisfied, detail = evaluate_action_spec_postcondition(
        absent_spec, {"paca_census": [{"candidate_id": "c1"}]},
    )
    assert not satisfied and detail == "object_present"


def test_postcondition_field_obligation_on_certificate_rows() -> None:
    from src.sehd_v6.core.bccse.action_spec import (
        evaluate_action_spec_postcondition,
    )

    spec = compile_action_spec(
        "evidence_obligation", "require_evidence_field: impact_evidence_refs",
    )
    assert spec.postcondition == "field_nonempty"
    empty = {"prosecution": {"candidates": [{"certificate_id": "c1"}]}}
    satisfied, detail = evaluate_action_spec_postcondition(spec, empty)
    assert not satisfied and detail == "field_obligation_absent"
    filled = {
        "prosecution": {
            "candidates": [
                {"certificate_id": "c1", "impact_evidence_refs": ["ev_1"]},
            ],
        },
    }
    satisfied, detail = evaluate_action_spec_postcondition(spec, filled)
    assert satisfied and detail == "field_obligation_satisfied"


def test_postcondition_coverage_not_complete() -> None:
    from src.sehd_v6.core.bccse.action_spec import (
        evaluate_action_spec_postcondition,
    )

    spec = compile_action_spec(
        "evidence_obligation", "keep_coverage_incomplete: coverage_state",
    )
    assert spec.postcondition == "coverage_not_complete"
    open_result = {
        "proof_adjudication": {
            "prosecution_coverage": "incomplete",
            "defense_coverage": "complete",
        },
    }
    satisfied, _ = evaluate_action_spec_postcondition(spec, open_result)
    assert satisfied
    closed_result = {
        "proof_adjudication": {
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
        },
    }
    satisfied, detail = evaluate_action_spec_postcondition(spec, closed_result)
    assert not satisfied and detail == "coverage_complete"


def test_postcondition_quarantine_and_fail_closed_surfaces() -> None:
    from src.sehd_v6.core.bccse.action_spec import (
        evaluate_action_spec_postcondition,
    )

    # The quarantine obligation belongs to atomic_verifier_instruction,
    # whose output surface (verification_certificate) is provider-internal
    # and not exposed in the final result: it must fail closed, never
    # silently pass.
    quarantine_spec = compile_action_spec(
        "atomic_verifier_instruction", "quarantine_certificate: certificate_id",
    )
    assert quarantine_spec.postcondition == "object_quarantined"
    satisfied, detail = evaluate_action_spec_postcondition(
        quarantine_spec,
        {"proof_adjudication": {"quarantined_error_certificate_ids": ["c1"]}},
    )
    assert not satisfied
    assert detail.startswith("unmapped_postcondition")

    # A provider-internal surface is not observable in the final result and
    # must fail closed, never silently pass.
    unobservable_spec = compile_action_spec(
        "causal_link_test", "test_causal_link: is_root_cause",
    )
    satisfied, detail = evaluate_action_spec_postcondition(
        unobservable_spec, {},
    )
    assert not satisfied
    assert detail.startswith(("unmapped_postcondition", "unobservable_in_result"))


def test_classify_execution_evidence_taxonomy() -> None:
    from src.sehd_v6.core.bccse.action_spec import (
        ActionSpec,
        classify_execution_evidence,
    )

    spec = ActionSpec(
        module_id="candidate_trigger",
        route_ids=("candidate_fallback_route",),
        output_object="candidate",
        output_field="",
        operation="emit_candidate",
        postcondition="object_present",
        observable_field="candidate_count",
        expected_direction="increase",
    )
    moved = classify_execution_evidence(
        spec, {"paca_census": [{"candidate_id": "c1"}]}, True,
    )
    assert moved["execution_class"] == "postcondition_satisfied"
    assert moved["postcondition_satisfied"] is True

    unmet = classify_execution_evidence(spec, {}, True)
    assert unmet["execution_class"] == "postcondition_not_satisfied"
    assert unmet["postcondition_satisfied"] is False

    unmoved = classify_execution_evidence(spec, {}, False)
    assert unmoved["execution_class"] == "no_observable_movement"

    nospec = classify_execution_evidence(None, {}, True)
    assert nospec["execution_class"] == "action_spec_absent"
    assert nospec["postcondition_satisfied"] is False


def test_expected_effect_audit_carries_execution_evidence() -> None:
    from src.sehd_v6.core.bccse.evolution_runner import _expected_effect_audit
    from src.sehd_v6.core.bccse.fitness import FitnessAssessment
    from src.sehd_v6.core.bccse.genome import SkillEdit

    edit = SkillEdit(
        edit_id="e1", target_module="evidence_obligation",
        operation="add_rule",
        new_content="When x, require_evidence_field: impact_evidence_refs.",
        action_spec=compile_action_spec(
            "evidence_obligation", "require_evidence_field: impact_evidence_refs",
        ).to_dict(),
    )
    assessment = FitnessAssessment(
        decision="no_effect", reasons=(), task_score_before=0.0,
        task_score_after=0.0, local_score_before=0.0, local_score_after=0.0,
    )
    audit = _expected_effect_audit(edit, assessment)
    evidence = audit["execution_evidence"]
    assert evidence["execution_class"] == "action_spec_absent" or (
        evidence["postcondition_satisfied"] in (True, False)
    )
