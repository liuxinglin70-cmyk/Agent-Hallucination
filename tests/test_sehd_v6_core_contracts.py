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
from src.sehd_v6.core.bccse.reflection_engine import build_causal_reflection_prompt
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
    assert allowed == ("evidence_obligation", "candidate_trigger")


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


def test_online_evolution_does_not_commit_label_free_directional_evidence(
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
        lambda result, use_gold=False: signal(str(result["state"])),
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

    assert result.edits_accepted == 0
    assert result.edits_rejected == 1
    assert result.champion_genome.generation == 0
    assert result.steps[0].gate_decision == "skip_stable"
    assert result.steps[1].gate_decision == "rollback_batch"
    memory = (tmp_path / "htc_tes_experience_memory.json").read_text("utf-8")
    assert '"detection_improved": false' in memory
    assert '"source_case_id": "source"' in memory
