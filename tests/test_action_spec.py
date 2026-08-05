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
