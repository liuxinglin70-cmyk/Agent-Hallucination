from __future__ import annotations

from contextlib import contextmanager

from src.sehd_v6.core.bccse.cesv_contract import (
    CausalInterventionEngine,
    EdgeType,
    ProvenanceGraph,
    run_cesv_pipeline,
)
from src.sehd_v6.core.htc_tes.type_aware_fitness import (
    TypeAwareFitness,
    TypeExperienceMemory,
)
from src.sehd_v6.core.htc_tes.type_classifier import (
    HallucinationType,
    TypeClassificationResult,
)
from src.sehd_v6.core.htc_tes.type_conditioned_evolution import (
    TypeConditionedEvolution,
)
from src.sehd_v6.core.bccse.genome import create_baseline_genome
from src.sehd_v6.core.bccse.types import FailureEpisode
from src.sehd_v6.core.paca_tcl.proof_evaluator import (
    MetricSnapshot,
    PacaTclV35EvolutionEvaluator,
    experience_record_from_row,
)
from src.sehd_v6.core.paca_tcl.basic_runner import (
    _event_for_source_step,
    _parse_bool,
)
from src.sehd_v6.core.paca_tcl.skill_integration import (
    compile_genome_for_paca,
    make_skill_aware_provider,
)
from src.sehd_v6.core.bccse.genome import SkillEdit, SkillOp, apply_edit
from src.sehd_v6.core.bccse.reflection_engine import (
    build_causal_reflection_prompt,
    build_runtime_observation,
    extract_intervention_candidates,
    build_intervention_critic_prompt,
    select_intervention_candidate,
    build_failure_diagnosis_prompt,
    sanitize_diagnosis,
)
from src.sehd_v6.core.bccse.safety_gate import create_structural_gate
from src.sehd_v6.core.bccse.step_attributor import (
    AttributionSignal,
    ErrorType,
    ReflectionType,
    TriggerSource,
)


def _type_result() -> TypeClassificationResult:
    return TypeClassificationResult(
        primary_type=HallucinationType.FACTUAL,
        secondary_type=None,
        confidence=0.8,
        evidence={},
        classification_method="rule_based",
    )


def test_type_route_keeps_primary_module_first() -> None:
    evolution = TypeConditionedEvolution()
    allowed = evolution.intersect_allowed_modules(
        HallucinationType.FACTUAL,
        ("candidate_trigger", "evidence_obligation"),
    )
    assert allowed == ("candidate_trigger", "evidence_obligation")


def test_self_supervised_decisive_result_is_not_counted_as_accuracy() -> None:
    fitness = TypeAwareFitness()
    assessment = fitness.assess_edit(
        source_case_id="case",
        hallucination_type=HallucinationType.FACTUAL,
        evolution_direction="unresolved_to_decisive",
        detection_before={"state": "unresolved"},
        detection_after={"state": "verified_error"},
        type_result=_type_result(),
        target_module="evidence_obligation",
        override_success=True,
    )
    entry = fitness.get_experience_memory().get_type_experiences(
        HallucinationType.FACTUAL
    )[0]
    assert assessment.overall_improved is True
    assert assessment.decisive_accuracy == 0.0
    assert entry.metadata["decisive_correct"] is None
    assert entry.metadata["process_state"] == "directional_improvement"


def test_override_cannot_turn_typed_regression_into_success() -> None:
    fitness = TypeAwareFitness()
    assessment = fitness.assess_edit(
        source_case_id="case",
        hallucination_type=HallucinationType.FACTUAL,
        evolution_direction="unresolved_to_error",
        detection_before={"state": "unresolved"},
        detection_after={"state": "verified_clean"},
        type_result=_type_result(),
        target_module="evidence_obligation",
        override_success=True,
    )
    assert assessment.overall_improved is False


def test_experience_memory_deduplicates_repeated_observation() -> None:
    memory = TypeExperienceMemory()
    for _ in range(2):
        memory.add_experience(
            HallucinationType.FACTUAL,
            "unresolved_to_error",
            "evidence_obligation",
            "strict_verification",
            True,
            "case",
            feature_description="Same feature pattern",
        )
    entries = memory.get_type_experiences(HallucinationType.FACTUAL)
    assert len(entries) == 1
    assert entries[0].metadata["observation_count"] == 2


def test_evolution_experience_is_gold_free_by_default() -> None:
    record = experience_record_from_row(
        {
            "case_ref": "case_ref",
            "state": "verified_error",
            "gold_label": True,
            "gold_steps": [3],
            "runtime_valid": True,
        }
    )
    assert record.gold_positive is None
    assert record.gold_responsible_ref is None


def test_empty_cesv_evidence_abstains_without_fpr_claim() -> None:
    result = run_cesv_pipeline(packet={"events": []}, candidates=[], evidential_scores=[])
    assert result["state"] == "unresolved"
    sequential = result["cesv_diagnostics"]["sequential_test"]
    assert sequential["fpr_guarantee"] is None
    assert sequential["calibration_status"] == "uncalibrated_heuristic"


def test_graph_intervention_preserves_alternative_path() -> None:
    graph = ProvenanceGraph()
    graph.add_edge("root", "target", EdgeType.TRIGGER)
    graph.add_edge("target", "child", EdgeType.SUPPORT)
    graph.add_edge("root", "child", EdgeType.SUPPORT)
    result = CausalInterventionEngine(graph).do_intervention("target")
    assert result["affected_downstream"] == 0
    assert result["interventional_effect"] == 0.0


def test_paired_replay_uses_boundary_refs_contract(monkeypatch) -> None:
    parent = create_baseline_genome()
    child = parent.clone()
    evaluator = object.__new__(PacaTclV35EvolutionEvaluator)
    rows = (
        {
            "case_ref": "source",
            "state": "verified_error",
            "gold_label": False,
            "runtime_valid": True,
        },
        {
            "case_ref": "boundary",
            "state": "verified_clean",
            "gold_label": False,
            "runtime_valid": True,
        },
    )
    monkeypatch.setattr(evaluator, "_get_rows", lambda genome, keys: rows)
    monkeypatch.setattr(
        evaluator,
        "_anonymous_case_map",
        lambda values: {str(row["case_ref"]): row for row in values},
    )
    monkeypatch.setattr(
        "src.sehd_v6.core.paca_tcl.proof_evaluator.metric_snapshot",
        lambda values: MetricSnapshot(candidate_origin_coverage=1.0),
    )
    evidence = evaluator.paired_replay(
        parent=parent,
        child=child,
        case_keys=("source", "boundary"),
        episodes=(
            FailureEpisode(
                record_key="source",
                error_type="false_positive",
                support_refs=("source",),
                boundary_refs=("boundary",),
            ),
        ),
    )
    assert evidence.targeted_error_total == 1
    assert evidence.paired_boundary_total == 1


def test_frozen_census_adapter_normalizes_step_and_boolean_types() -> None:
    built = {
        "events": [
            {"event_id": "event_001", "source_step_id": 1},
            {"event_id": "event_002", "source_step_id": "2"},
        ]
    }
    assert _event_for_source_step(built, "1") == "event_001"
    assert _event_for_source_step(built, 2) == "event_002"
    assert _parse_bool("false") is False
    assert _parse_bool("true") is True


def test_abstention_rule_reaches_boundary_provider() -> None:
    baseline = create_baseline_genome()
    evolved = apply_edit(
        baseline,
        SkillEdit(
            edit_id="abstention",
            target_module="abstention_rule",
            operation=SkillOp.ADD_RULE,
            new_content=(
                "When every error candidate is defeated by cited defense evidence, "
                "return clean."
            ),
            source_case_id="case",
        ),
    )
    boundary_route = {"model": "mock"}
    config = {"boundary_route": boundary_route}
    captured = {}

    def provider(*, route, system, user, cache_dir):
        captured["system"] = system
        return {}, {}

    wrapped = make_skill_aware_provider(
        provider,
        compiled=compile_genome_for_paca(evolved),
        config=config,
    )
    wrapped(
        route=boundary_route,
        system="base",
        user="trace",
        cache_dir=None,
    )
    assert "return clean" in captured["system"]


def test_formal_evolution_rejects_multi_edit_credit_assignment(tmp_path) -> None:
    from src.sehd_v6.core.bccse.evolution_runner import run_online_evolution

    try:
        run_online_evolution(
            evolution_case_ids=("case",),
            records={"case": {}},
            config={},
            output_dir=tmp_path,
            census_root=tmp_path,
            fitness_batch_size=2,
        )
    except ValueError as exc:
        assert "fitness_batch_size must be 1" in str(exc)
    else:
        raise AssertionError("multi-edit batches must be rejected")


def test_self_supervised_reflection_prompt_does_not_claim_gold_mode() -> None:
    signal = AttributionSignal(
        error_type=ErrorType.UNRESOLVED_UNKNOWN,
        target_module="abstention_rule",
        secondary_module="causal_link_test",
        attribution_confidence="medium",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.UNRESOLVED,
        evidence_summary="incomplete court",
        prosecution_coverage=None,
        defense_coverage=None,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="unavailable",
        crs_complete=False,
        consensus_gate_applied=False,
        candidate_count=1,
        state="unresolved",
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )
    system, user = build_causal_reflection_prompt(
        signal,
        current_skill_text="rules",
        detection_context="trace",
    )
    assert "uses gold-assisted selection" not in system
    assert "<gold_diagnostic_reference>" not in user


def test_reflection_receives_structured_runtime_output_ownership() -> None:
    signal = AttributionSignal(
        error_type=ErrorType.EVIDENCE_INCOMPLETE,
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        attribution_confidence="high",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.EVIDENCE_INCOMPLETE,
        evidence_summary="missing prosecution evidence",
        prosecution_coverage=0.0,
        defense_coverage=1.0,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={}, crs_aggregate=0.0, crs_source="mock",
        crs_complete=True, consensus_gate_applied=True,
        candidate_count=1, state="unresolved", gold_label=None,
        has_gold=False, gold_enhanced=False,
        responsible_step_correct=False,
    )
    result = {
        "state": "unresolved", "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [{"candidate_id": "c1"}],
        "combined_prosecution": {"candidates": []},
        "combined_defense": {"certificates": [{"candidate_id": "c1"}]},
        "proof_adjudication": {
            "prosecution_coverage": "incomplete",
            "defense_coverage": "complete",
        },
        "evidence_health": {"quarantined_error_certificate_count": 0},
    }
    observation = build_runtime_observation(
        result, signal,
        allowed_modules=("evidence_obligation", "atomic_verifier_instruction"),
    )
    _, user = build_causal_reflection_prompt(
        signal, current_skill_text="rules", detection_context="trace",
        runtime_observation=observation,
    )
    assert observation["observed_fields"]["prosecution_certificates"] == 0
    assert observation["observed_fields"]["defense_certificates"] == 1
    assert observation["module_output_ownership"]["evidence_obligation"]
    assert "<structured_runtime_observation>" in user
    assert "gold_label" not in user


def test_runtime_observation_court_balance_locates_verdict_carrier() -> None:
    """v5.9: court_balance must expose who carried the verdict so reflection
    can target the executable owner instead of a downstream symptom."""
    signal = AttributionSignal(
        error_type=ErrorType.FALSE_NEGATIVE_CLEAN,
        target_module="abstention_rule",
        secondary_module="causal_link_test",
        attribution_confidence="high",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.GOLD_MISMATCH,
        evidence_summary="clean verdict carried by complete defense",
        prosecution_coverage=1.0,
        defense_coverage=1.0,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={}, crs_aggregate=0.0, crs_source="mock",
        crs_complete=True, consensus_gate_applied=False,
        candidate_count=7, state="verified_clean", gold_label=None,
        has_gold=False, gold_enhanced=False,
        responsible_step_correct=False,
    )
    result = {
        "state": "verified_clean", "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [{"candidate_id": f"c{i}"} for i in range(7)],
        "combined_prosecution": {"candidates": [
            {"candidate_id": "c1"}, {"candidate_id": "c2"},
        ]},
        "combined_defense": {"certificates": [
            {"target_candidate_id": "c1"}, {"target_candidate_id": "c2"},
        ]},
        "proof_adjudication": {
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "quarantined_error_certificate_ids": ["cert_x"],
            "clean_covered_candidate_ids": ["c1", "c2"],
            "positive_candidate_ids": [],
            "consensus_gate_blocked_ids": [],
        },
        "evidence_health": {"quarantined_error_certificate_count": 1},
    }
    observation = build_runtime_observation(
        result, signal, allowed_modules=("clean_defense_boundary",),
    )
    balance = observation["court_balance"]
    assert balance["defense_certificates"] == 2
    assert balance["quarantined_error_certificates"] == 1
    assert balance["surviving_error_certificates"] == 1
    assert balance["candidates_covered_by_clean_defense"] == 2
    assert balance["verdict_state"] == "verified_clean"


def test_collaborative_reflection_normalizes_and_critics_candidates() -> None:
    candidates = [
        {"target_module": "evidence_obligation", "operation": "add_rule", "new_content": "one"},
        {"target_module": "atomic_verifier_instruction", "operation": "add_rule", "new_content": "two"},
    ]
    normalized, reason = extract_intervention_candidates({"candidates": candidates})
    assert len(normalized) == 2
    system, user = build_intervention_critic_prompt(
        candidates=normalized,
        runtime_observation={"observed_fields": {"prosecution_certificates": 0}},
        current_skill_text="rules",
        allowed_target_modules=("evidence_obligation", "atomic_verifier_instruction"),
    )
    assert "do not generate" in system.lower()
    assert "CANDIDATES" in user
    selected, audit = select_intervention_candidate(
        normalized, {"decision": "select", "selected_index": 1, "reason": "owned output"},
    )
    assert selected == candidates[1]
    assert audit["selected_index"] == 1
    rejected, _ = select_intervention_candidate(
        normalized, {"decision": "select", "selected_index": 9},
    )
    assert rejected is None


def _fn_clean_signal() -> AttributionSignal:
    return AttributionSignal(
        error_type=ErrorType.FALSE_NEGATIVE_CLEAN,
        target_module="abstention_rule",
        secondary_module="causal_link_test",
        attribution_confidence="high",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.GOLD_MISMATCH,
        evidence_summary="clean verdict carried by complete defense",
        prosecution_coverage=1.0,
        defense_coverage=1.0,
        prosecution_coverage_state="complete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={}, crs_aggregate=0.0, crs_source="mock",
        crs_complete=True, consensus_gate_applied=False,
        candidate_count=7, state="verified_clean", gold_label=None,
        has_gold=False, gold_enhanced=False,
        responsible_step_correct=False,
    )


def test_failure_diagnosis_prompt_bounded_and_label_free() -> None:
    system, user = build_failure_diagnosis_prompt(
        signal=_fn_clean_signal(),
        runtime_observation={"court_balance": {"defense_certificates": 2}},
        detection_context="trace",
        current_skill_text="rules",
        allowed_target_modules=("clean_defense_boundary", "abstention_rule"),
    )
    assert "diagnose only" in system
    assert "clean_defense_boundary" in user
    assert "owner_executable" in user
    assert "gold" not in user.lower().replace("gold_mismatch", "")


def test_sanitize_diagnosis_downgrades_illegal_owner() -> None:
    bounded = sanitize_diagnosis(
        {
            "owner_executable": True,
            "owner_module": "candidate_trigger",
            "failure_field": "defense_coverage",
            "observed_gap": "x" * 900,
            "reasoning": "y",
            "intervention_hint": {"condition": "c", "action": "a"},
        },
        allowed_modules=("clean_defense_boundary",),
    )
    assert bounded["owner_executable"] is False
    assert bounded["owner_module"] is None
    assert "intervention_hint" not in bounded
    assert len(bounded["observed_gap"]) <= 300


def test_sanitize_diagnosis_keeps_allowed_owner_and_hint() -> None:
    bounded = sanitize_diagnosis(
        {
            "owner_executable": True,
            "owner_module": "clean_defense_boundary",
            "failure_field": "defense_coverage",
            "observed_gap": "certificates never assembled",
            "reasoning": "y",
            "intervention_hint": {"condition": "c", "action": "a"},
        },
        allowed_modules=("clean_defense_boundary",),
    )
    assert bounded["owner_executable"] is True
    assert bounded["owner_module"] == "clean_defense_boundary"
    assert bounded["intervention_hint"]["action"] == "a"


def test_sanitize_diagnosis_remaps_owner_without_field_ownership() -> None:
    """v5.9b: a diagnosis asking evidence_obligation to repair
    defense_coverage is inert (the module owns prosecution-side fields).
    The owner is deterministically remapped to an allowed module that owns
    the field (observed failure mode on Camel/061)."""
    bounded = sanitize_diagnosis(
        {
            "owner_executable": True,
            "owner_module": "evidence_obligation",
            "failure_field": "defense_coverage",
            "observed_gap": "defense certificates never assembled",
            "reasoning": "y",
            "intervention_hint": {"condition": "c", "action": "a"},
        },
        allowed_modules=("evidence_obligation", "clean_defense_boundary"),
    )
    assert bounded["owner_executable"] is True
    assert bounded["owner_module"] == "clean_defense_boundary"
    assert bounded["remapped_from"] == "evidence_obligation"


def test_sanitize_diagnosis_downgrades_when_no_owner_holds_field() -> None:
    bounded = sanitize_diagnosis(
        {
            "owner_executable": True,
            "owner_module": "evidence_obligation",
            "failure_field": "defense_coverage",
            "observed_gap": "gap",
            "reasoning": "y",
        },
        allowed_modules=("evidence_obligation",),
    )
    assert bounded["owner_executable"] is False
    assert bounded["owner_module"] is None
    assert bounded["remap_reason"] == "no_allowed_module_owns_defense_coverage"


def test_targeted_diagnosis_directive_enters_reflection_prompt() -> None:
    diagnosis = sanitize_diagnosis(
        {
            "owner_executable": True,
            "owner_module": "abstention_rule",
            "failure_field": "state",
            "observed_gap": "unresolved despite complete defense",
            "reasoning": "y",
            "intervention_hint": {"condition": "c", "action": "a"},
        },
        allowed_modules=("abstention_rule",),
    )
    _, user = build_causal_reflection_prompt(
        _fn_clean_signal(),
        current_skill_text="rules",
        detection_context="trace",
        allowed_target_modules=("abstention_rule",),
        targeted_diagnosis=diagnosis,
    )
    assert "<targeted_diagnosis" in user
    assert "abstention_rule" in user


def test_candidate_normalization_deduplicates_repeated_samples() -> None:
    from src.sehd_v6.core.bccse.reflection_engine import (
        extract_intervention_candidates,
    )

    candidate = {
        "target_module": "evidence_obligation",
        "operation": "add_rule",
        "new_content": "Require one cited event-backed certificate.",
    }
    values, _ = extract_intervention_candidates(
        {"candidates": [candidate, dict(candidate)]}, limit=3,
    )
    assert values == [candidate]


def test_collaborative_prompt_has_one_consistent_json_protocol() -> None:
    signal = _reflection_signal_for_test()
    system, user = build_causal_reflection_prompt(
        signal,
        current_skill_text="rules",
        detection_context="trace",
        allowed_target_modules=("evidence_obligation",),
        candidate_count=3,
    )
    assert "candidates" in system
    assert "single-edit-schema" not in user
    assert "candidates" in user
    assert user.rstrip().endswith("</task>")


def _reflection_signal_for_test() -> AttributionSignal:
    return AttributionSignal(
        error_type=ErrorType.EVIDENCE_INCOMPLETE,
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        attribution_confidence="medium",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.EVIDENCE_INCOMPLETE,
        evidence_summary="missing output", prosecution_coverage=0.0,
        defense_coverage=1.0, prosecution_coverage_state="incomplete",
        defense_coverage_state="complete", coverage_quantitative=False,
        crs_scores={}, crs_aggregate=0.0, crs_source="test",
        crs_complete=True, consensus_gate_applied=True, candidate_count=1,
        state="unresolved", gold_label=None, has_gold=False,
        gold_enhanced=False, responsible_step_correct=False,
    )


POLICY_SKILL_EXAMPLE = (
    "When the defense evidence is fully established and no error certificate "
    "survives, conclude clean instead of abstaining."
)


def test_policy_skill_guidance_present_and_rule_passes_structural_gate() -> None:
    """The conditional-evidence-standard channel must be prompt-visible and
    gate-survivable: the advertised example rule has to pass the real
    structural gate for abstention_rule, otherwise the guidance is dead."""
    signal = AttributionSignal(
        error_type=ErrorType.UNRESOLVED_UNKNOWN,
        target_module="abstention_rule",
        secondary_module="clean_defense_boundary",
        attribution_confidence="medium",
        reflection_type=ReflectionType.OPTIMIZATION,
        trigger_source=TriggerSource.UNRESOLVED,
        evidence_summary="defense established, court abstained",
        prosecution_coverage=None,
        defense_coverage=None,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="complete",
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="unavailable",
        crs_complete=False,
        consensus_gate_applied=False,
        candidate_count=1,
        state="unresolved",
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )
    system, _ = build_causal_reflection_prompt(
        signal,
        current_skill_text="rules",
        detection_context="trace",
    )
    assert "Conditional evidence standards" in system

    gate = create_structural_gate()
    accepted, _, result = gate.evaluate(
        case_id="case",
        case_index=0,
        target_module="abstention_rule",
        operation="add_rule",
        content=POLICY_SKILL_EXAMPLE,
        preconditions=(),
        current_genome=create_baseline_genome(),
        expected_module="abstention_rule",
    )
    assert accepted, f"policy example rejected by structural gate: {result}"


def test_replay_rejection_does_not_fake_multiple_convergence_cases() -> None:
    gate = create_structural_gate(convergence_window=2)
    accepted, certificate, _ = gate.evaluate(
        case_id="case",
        case_index=0,
        target_module="evidence_obligation",
        operation="add_rule",
        content=(
            "When a claim lacks an addressable evidence event, require one "
            "event-backed certificate."
        ),
        preconditions=(),
        current_genome=create_baseline_genome(),
        expected_module="evidence_obligation",
    )
    assert accepted
    gate.revoke_structural_accept(certificate, ["fitness_regression"])
    assert gate.summary()["consecutive_no_edit"] == 0


def test_online_evolution_keeps_label_free_directional_edit_provisional(
    tmp_path, monkeypatch,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def signal(state: str) -> AttributionSignal:
        stable = state == "verified_clean"
        return AttributionSignal(
            error_type=(ErrorType.CORRECT if stable else ErrorType.EVIDENCE_INCOMPLETE),
            target_module="evidence_obligation",
            secondary_module="atomic_verifier_instruction",
            attribution_confidence="medium",
            reflection_type=(
                ReflectionType.STABLE_SUCCESS if stable else ReflectionType.OPTIMIZATION
            ),
            trigger_source=(TriggerSource.NONE if stable else TriggerSource.EVIDENCE_INCOMPLETE),
            evidence_summary="mock evidence process",
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
        state = (
            "verified_clean"
            if case_id == "stable" or genome.generation > 0
            else "unresolved"
        )
        return {
            "state": state,
            "runtime_valid": True,
            "census": [{
                "candidate_kind": "factual_claim",
                "claim_or_commitment": "claim lacking cited evidence",
                "axes": ["F"],
            }],
        }

    @contextmanager
    def provider_context(**kwargs):
        yield None

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal(str(result["state"])),
    )
    monkeypatch.setattr(runner, "integrated_v35_provider", provider_context)
    monkeypatch.setattr(
        runner,
        "json_object_provider_call",
        lambda **kwargs: ({
            "target_module": "evidence_obligation",
            "operation": "add_rule",
            "rule_index": None,
            "new_content": (
                "When a factual claim lacks a cited trajectory event, require "
                "one event-backed certificate."
            ),
            "rationale": "Require observable support.",
            "feature_description": "factual claim without cited trajectory evidence",
        }, {}),
    )

    result = runner.run_online_evolution(
        evolution_case_ids=("stable", "source"),
        records={"stable": {}, "source": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        convergence_window=20,
        run_id="mock_e2e",
    )

    assert result.edits_accepted == 1
    assert result.edits_rejected == 0
    assert result.champion_genome.generation == 1
    assert result.steps[0].gate_decision == "skip_stable"
    assert result.steps[1].gate_decision == "accept_batch"
    assert result.evolution_audit["final_champion_claim"] is False
    assert result.steps[1].fitness_check["batch"]["promotion_eligible"] is False
    memory = (tmp_path / "htc_tes_experience_memory.json").read_text("utf-8")
    assert '"detection_improved": false' in memory
    assert '"source_case_id": "source"' in memory


# ============================================================
# abstention_rule indirect channel: boundary finding -> proof court
# ============================================================


def test_boundary_finding_feeds_proof_court_neutral_evidence() -> None:
    """Second hop of the abstention_rule channel (pure function, no LLM).

    The boundary-route output (the only provider an abstention_rule edit can
    reach) enters the final adjudication deterministically: its finding is
    embedded as ``case_boundary_finding`` inside NEUTRAL_EVIDENCE of the
    proof prompt.
    """
    import json as _json

    from src.sehd_v6.core.paca_tcl import proof_engine_v3_4 as v34

    base = {
        "census": [],
        "boundary_path": {
            "state": "verified_clean",
            "responsible_event_id": "event_007",
            "evidence_refs": ["event_005", "event_006"],
            "rationale": "defense evidence fully established, no surviving "
                          "error certificate",
        },
    }
    value = v34._proof_user({"events": []}, base)
    assert "NEUTRAL_EVIDENCE=" in value
    neutral = _json.loads(value.split("NEUTRAL_EVIDENCE=\n", 1)[1])
    finding = neutral["case_boundary_finding"]
    assert finding["state"] == "verified_clean"
    assert finding["responsible_event_id"] == "event_007"
    assert finding["evidence_refs"] == ["event_005", "event_006"]
    assert "fully established" in finding["rationale"]


def test_boundary_responsible_event_becomes_required_proof_input() -> None:
    """The boundary finding also constrains the court's required evidence."""
    from src.sehd_v6.core.paca_tcl import proof_engine_v3_4 as v34

    base = {
        "census": [],
        "boundary_path": {
            "state": "unresolved",
            "responsible_event_id": "event_007",
        },
    }
    assert "event_007" in v34._required_origins(base)
    candidates = v34._required_candidates(base)
    boundary_rows = [
        row for row in candidates
        if row.get("origin_hypothesis_event_id") == "event_007"
    ]
    assert boundary_rows
    assert boundary_rows[0]["candidate_kind"] == "independent_boundary_finding"


def test_abstention_rule_guidance_describes_indirect_channel_only() -> None:
    """Reflection guidance must not promise direct verdict overwrites."""
    from src.sehd_v6.core.bccse.reflection_engine import (
        MODULE_OBSERVABLE_EFFECTS,
        MODULE_RUNTIME_CONTRACTS,
    )

    contract = MODULE_RUNTIME_CONTRACTS["abstention_rule"]
    assert "case_boundary_finding" in contract
    assert "NEUTRAL_EVIDENCE" in contract
    assert "never claim to overwrite" in contract
    effects = MODULE_OBSERVABLE_EFFECTS["abstention_rule"]
    assert "NEUTRAL_EVIDENCE" in effects
    assert "authoritative state" not in effects
