"""Independent review-round-5 verification tests.

Written from a code-level review of the framework, not from the repository's
existing test suite.  Every test pins one verified defect or one verified
contract of the detector/evolution framework and must pass against the
fixed code without any provider call, cache or dataset dependency.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from src.sehd_v6.core.bccse import evolution_runner as runner
from src.sehd_v6.core.bccse import gate_feedback as gf
from src.sehd_v6.core.bccse import genome as genome_mod
from src.sehd_v6.core.bccse import signal_adapter as sa
from src.sehd_v6.core.bccse import vocabulary_registry as vreg
from src.sehd_v6.core.bccse.evolution_runner import (
    _restore_checkpoint_state,
)
from src.sehd_v6.core.bccse.failure_episode import (
    CausalSlice,
    FailureEpisode,
    build_mechanism_signature,
)
from src.sehd_v6.core.bccse.policy_genome import PolicyGenome
from src.sehd_v6.core.htc_tes.type_classifier import (
    HallucinationType,
    HallucinationTypeClassifier,
)
from src.sehd_v6.core.htc_tes.type_aware_fitness import TypeAwareFitness
from src.sehd_v6.detector import batch as detector_batch
from src.sehd_v6.detector import replica_aggregation as rep
from src.sehd_v6.evolution import runner as evolution_api
from src.sehd_v6.evolution.state_manifest import load_state_manifest, write_state_manifest


# ============================================================
# PACA-TCL detector engine
# ============================================================

def test_census_load_survives_corrupt_cache(tmp_path: pathlib.Path) -> None:
    from src.sehd_v6.core.paca_tcl import basic_runner

    case_ref = "case_r5_corrupt"
    cache_dir = tmp_path / "census"
    source = cache_dir / basic_runner.private_ref(case_ref, "p0_")
    source.mkdir(parents=True)
    (source / "paca_v1_canary_results.json").write_text(
        "{ not json at all", encoding="utf-8"
    )
    built = {
        "events": [],
        "question": "",
        "schema_version": "agenthallu.ocse_packet.v1",
    }
    census, audit = basic_runner.load_paca_census(
        case_id=case_ref, built=built, cache_root=cache_dir,
    )
    assert census == []
    assert audit["status"] == "invalid"


def test_census_load_survives_non_dict_stage1(tmp_path: pathlib.Path) -> None:
    from src.sehd_v6.core.paca_tcl import basic_runner

    case_ref = "case_r5_odd_stage"
    cache_dir = tmp_path / "census"
    source = cache_dir / basic_runner.private_ref(case_ref, "p0_")
    source.mkdir(parents=True)
    (source / "paca_v1_canary_results.json").write_text(
        json.dumps({"outputs": {"stage1_scanning": "not a dict"}}),
        encoding="utf-8",
    )
    built = {"events": [], "question": "", "schema_version": "x"}
    census, audit = basic_runner.load_paca_census(
        case_id=case_ref, built=built, cache_root=cache_dir,
    )
    assert census == []
    assert audit["status"] == "invalid"


def test_all_routes_deterministic_unset_temperature() -> None:
    config = {
        "census_route": {},
        "defense_route": {"temperature": 0.7},
        "construct_census_route": {},
    }
    assert detector_batch._all_routes_deterministic(config) is False
    config["defense_route"] = {}
    assert detector_batch._all_routes_deterministic(config) is True
    config["defense_route"] = {"temperature": 0}
    assert detector_batch._all_routes_deterministic(config) is True


def test_batch_threads_census_root(tmp_path: pathlib.Path) -> None:
    """run_batch must forward census_root into every detection call."""
    from src.sehd_v6.detector import batch as batch_mod

    calls: list[dict[str, Any]] = []

    def fake_detect_record(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "state": "verified_clean",
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "proof_adjudication": {"decision_authority": "authoritative"},
            "providers": {},
        }

    original = batch_mod.detect_record
    batch_mod.detect_record = fake_detect_record  # type: ignore[assignment]
    try:
        records = {
            "c1": {"history": [{"role": "user", "content": "hi"}], "question": "q"}
        }
        config = {"construct_census_route": {"endpoint": "http://x"}}
        census = tmp_path / "census_root"
        result = detector_batch.run_batch(
            records=records, config=config, output_dir=tmp_path / "out",
            census_root=census,
        )
        assert result["completed_case_count"] == 1
        assert calls[0]["census_root"] == census
    finally:
        batch_mod.detect_record = original  # type: ignore[assignment]


def test_replica_unanimity_requires_decisive_state() -> None:
    unresolved = {
        "state": "unresolved", "runtime_valid": True, "providers": {},
    }
    agg = rep.aggregate_replicas([unresolved, unresolved, unresolved])
    assert agg["state"] == "unresolved"
    assert agg["unanimous"] is False

    decisive = {
        "state": "verified_clean", "runtime_valid": True, "providers": {},
    }
    agg = rep.aggregate_replicas([decisive, decisive])
    assert agg["state"] == "verified_clean"
    assert agg["unanimous"] is True


def test_focused_prosecution_explicit_no_error_flag(tmp_path: pathlib.Path) -> None:
    """Provider-empty candidates must carry the explicit_no_error flag."""
    from src.sehd_v6.core.paca_tcl import proof_engine

    built = {
        "events": [{"event_id": "event_001", "position": 1, "content": "x"}],
        "question": "q",
    }
    base = {"qualifications": [], "propagations": [], "boundary_path": {}}
    candidate = {
        "candidate_id": "candidate_001",
        "origin_hypothesis_event_id": "event_001",
    }
    config = {"focused_prosecution_route": {"endpoint": "http://localhost:1"}}

    def provider_returning_empty(*, route: Any, system: str, user: str,
                                 cache_dir: pathlib.Path):
        return {"coverage_state": "complete", "candidates": []}, {
            "calls": 1, "source": "test",
        }

    original = proof_engine.provider_call
    proof_engine.provider_call = provider_returning_empty  # type: ignore[assignment]
    try:
        certs, audits, meta = proof_engine._run_focused_prosecution_for_candidate(
            candidate=candidate, built=built, base=base,
            config=config, output_dir=tmp_path,
        )
        assert certs == []
        assert meta.get("no_error_declared_flag") is True
    finally:
        proof_engine.provider_call = original  # type: ignore[assignment]


def test_evaluate_zero_denominator_rates_are_none() -> None:
    from src.sehd_v6.core.paca_tcl import proof_engine

    row: dict[str, Any] = {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "proof_adjudication": {
            "decision_authority": "authoritative",
            "state": "unresolved",
            "error_audits": [],
            "defense_audits": [],
        },
        "evidence_health": {
            "construct_raw_candidate_count": 0,
            "construct_accepted_candidate_count": 0,
        },
        "providers": {},
    }
    metrics = proof_engine.evaluate([row])
    assert metrics["construct_candidate_acceptance_rate"] is None
    assert metrics["error_certificate_acceptance_rate"] is None
    assert metrics["defense_certificate_acceptance_rate"] is None


def test_metric_snapshot_uses_gold_side_denominators() -> None:
    def row(state: str, gold: bool) -> dict[str, Any]:
        return {
            "state": state, "gold_label": gold, "providers": {},
            "runtime_valid": True, "decision_authority": "authoritative",
            "proof_adjudication": {"decision_authority": "authoritative"},
        }

    tp, fp = row("verified_error", True), row("verified_error", False)
    fn, tn = row("verified_clean", True), row("verified_clean", False)
    snap = sa.metric_snapshot([tp, fp, fn, tn])
    assert snap.precision == 0.5
    assert snap.hallucination_recall == 0.5          # tp/(tp+fn)
    assert snap.clean_fpr == 0.5                     # fp/(fp+tn)
    snap = sa.metric_snapshot([tp, tn])
    assert snap.hallucination_recall == 1.0
    assert snap.clean_fpr == 0.0
    snap = sa.metric_snapshot([tp, fp])
    assert snap.hallucination_recall == 1.0
    assert snap.clean_fpr == 1.0


# ============================================================
# BCCSE evolution core
# ============================================================

def test_baseline_rule_text_is_single_source() -> None:
    baseline = genome_mod.create_baseline_genome()
    for module_id, rules in genome_mod.BASELINE_RULE_TEXT.items():
        assert list(baseline.modules[module_id].rules) == list(rules)


def _empty_policy_genome() -> PolicyGenome:
    from src.sehd_v6.core.bccse.policy_genome import (
        create_baseline_policy_genome,
    )

    return create_baseline_policy_genome()


def test_policy_genome_restore_with_empty_items(tmp_path: pathlib.Path) -> None:
    """A checkpoint whose policy genome has zero items must restore it."""
    baseline_genome = genome_mod.create_baseline_genome()
    champion_payload = baseline_genome.to_dict()
    from src.sehd_v6.utils.ocse_packet import stable_hash

    records = {"c1": {"history": [{"role": "user", "content": "x"}]}}
    checkpoint = {
        "schema_version": "agenthallu.bccse_v5_checkpoint.v5",
        "evolution_case_ids": ["c1"],
        "input_content_hash": stable_hash(records),
        "config_hash": stable_hash({}),
        "champion_genome_payload": champion_payload,
        "stable_genome_payload": {},
        "policy_genome": {**_empty_policy_genome().to_dict(), "items": []},
        "stable_policy_genome_payload": {
            **_empty_policy_genome().to_dict(), "items": [],
        },
        "steps": [],
        "gate_summary": {},
        "gate_events": [],
        "counters": {},
        "budget_usage": {},
        "next_case_index": 0,
        "active_attempt_index": 0,
        "rejected_buffer": [],
        "mutation_retry_feedback": {},
        "rejected_source_proposals": {},
        "pending_probe_commits": [],
        "pending_probe_step_indexes": [],
        "stable_memory_payload": {},
        "accepted_edits_history": [],
        "batch_history": [],
        "probe_history": [],
        "baseline_canary_audit": [],
        "stable_canaries": {},
        "stable_canaries_checkpoint": {},
        "l2_policy_audit": [],
        "l2_policy_accepts": 0,
        "stable_l2_policy_accepts": 0,
        "abstention_ladder_used": [],
        "contrast_registry": {},
        "rejected_policy_summaries": [],
        "localization_bias_signals": [],
        "non_l1_failure_attributions": [],
        "runtime_invalid_cases": 0,
        "runtime_error_categories": {},
        "rollback_reason": "",
        "stable_history_length": 0,
        "last_probe_accepted_count": 0,
        "last_probe_triggered_count": 0,
        "provisional_edit_count": 0,
        "shadow_genome_hash": "",
        "champion_genome_hash": baseline_genome.content_hash,
        "experience_memory_path": "",
        "run_id": "",
        "updated_at": 0.0,
        "output_dir": str(tmp_path),
    }
    (tmp_path / "evolution_checkpoint.json").write_text(
        json.dumps(checkpoint), encoding="utf-8"
    )
    (tmp_path / "champion_checkpoint.json").write_text(
        json.dumps(champion_payload), encoding="utf-8"
    )
    (tmp_path / "shadow_checkpoint.json").write_text(
        json.dumps(champion_payload), encoding="utf-8"
    )

    gate = runner.create_structural_gate()
    restored = _restore_checkpoint_state(
        resume_from=tmp_path,
        evolution_case_ids=["c1"],
        records=records,
        config={},
        gate=gate,
        budget_usage={k: 0 for k in (
            "source_replays", "canary_cases", "contrast_replays",
            "policy_replays", "probe_cases", "provider_calls",
            "detector_calls", "mutation_calls", "mutation_errors",
            "mutation_cache_hits", "mutation_transport_attempts",
            "mutation_repairs", "mutation_retry_attempts",
            "mutation_budget_exhausted",
        )},
        stable_canaries={},
        stable_canaries_checkpoint={},
        rejected_buffer=[],
        champion_genome=genome_mod.create_baseline_genome(),
        current_genome=genome_mod.create_baseline_genome(),
        stable_genome=genome_mod.create_baseline_genome(),
        policy_genome=_empty_policy_genome(),
        stable_policy_genome=_empty_policy_genome(),
        causal_memory=runner.EvolutionExperienceMemory(),
    )
    assert restored["policy_genome"].items == []


def test_registry_pre_ledger_operator_not_proposable() -> None:
    registry = vreg.L2_REGISTRY
    from src.sehd_v6.core.bccse import registry_reflection as regref

    operators = regref._court_operators(registry)
    assert "restore_audited_consensus" not in operators
    for name in operators:
        op = registry.raw_operator(name)
        assert op.policy_stage == vreg.POLICY_STAGE_POST_LEDGER

    # The reflection planner must never propose a pre-ledger operator
    # instance as a candidate.
    from src.sehd_v6.core.bccse.v6_contracts import FailureAttribution

    plans = regref.plan_registry_candidates(
        attribution=FailureAttribution(
            layer="l2", artifact="adjudication_policy",
            reason_code="no_registered_runtime_action",
            evidence={},
        ),
        registry=registry,
    )
    assert all(
        candidate.operator != "restore_audited_consensus"
        for candidate in plans
    )


def test_causal_backtrack_prefers_earliest_event() -> None:
    from src.sehd_v6.core.bccse.cesv_contract import (
        CausalInterventionEngine,
        EdgeType,
        ProvenanceGraph,
    )

    graph = ProvenanceGraph()
    graph.add_node("event_001")
    graph.add_node("event_002")
    graph.add_node("event_003")
    graph.add_edge("event_001", "event_003", EdgeType.PROPAGATE)
    graph.add_edge("event_002", "event_003", EdgeType.PROPAGATE)
    engine = CausalInterventionEngine(graph)
    path = engine.causal_backtrack("event_003")
    assert path[0] == "event_001"


def test_gate_ledger_drop_event() -> None:
    ledger = gf.GateEventLedger()
    event = gf.GateEvent(
        schema_version=gf.SCHEMA_VERSION,
        event_id=gf.gate_event_id(observation_id="c:0:1"),
        subject_id="c",
        episode_id="",
        attempt_id="",
        layer="l1",
        artifact="candidate_trigger",
        operation="add_rule",
        stage="fitness_gate",
        legacy_decision="reject_fitness",
        evidence_grade="reject",
        activation_state="",
    )
    assert ledger.record(event) is True
    assert ledger.record(event) is False
    assert ledger.drop_event(event.event_id) is True
    assert ledger.drop_event(event.event_id) is False
    assert ledger.events == []


def test_localization_bias_signal_requires_terminal_step() -> None:
    base: dict[str, Any] = {
        "state": "verified_error",
        "localization_status": "failed_temporal_fallback",
        "responsible_step": 3,
    }
    assert runner._localization_bias_signal(base) == ""
    assert runner._localization_bias_signal(
        base, last_audited_step=3,
    ) == "terminal_step_bias"
    assert runner._localization_bias_signal(
        base, last_audited_step=5,
    ) == ""


def test_convergence_surfaces_in_result() -> None:
    gate = runner.create_structural_gate()
    for _ in range(gate.config.convergence_window):
        gate.record_no_edit()
    assert gate.converged is True


# ============================================================
# HTC-TES type layer
# ============================================================

def test_error_type_mapping_has_no_gaps() -> None:
    from src.sehd_v6.core.bccse.step_attributor import ErrorType
    from src.sehd_v6.core.htc_tes import type_classifier as tc

    for error_type in ErrorType:
        if error_type == ErrorType.CORRECT:
            continue
        assert error_type.value in tc._ERROR_TYPE_TO_HALLUCINATION, (
            f"unmapped ErrorType: {error_type.value}"
        )


def test_candidate_analysis_cue_counts() -> None:
    classifier = HallucinationTypeClassifier()
    candidates = [
        {
            "candidate_id": "c1",
            "candidate_kind": "claim",
            "claim_or_commitment": (
                "an unsupported factual claim about the API output"
            ),
            "axes": ["F"],
        },
    ]
    counts = classifier._analyze_candidates(candidates)
    # Kind-based and pattern-based rules are independent evidence views of
    # the same candidate; both may vote for one type (cue-count semantics).
    assert counts[HallucinationType.FACTUAL] == 2


def test_type_fitness_dedup_keeps_detection_polarity() -> None:
    fitness = TypeAwareFitness()
    memory = fitness.get_experience_memory()
    signature = {
        "layer": "l1", "artifact": "candidate_trigger",
        "reason_code": "candidate_miss", "operation": "add_rule",
    }
    memory.add_experience(
        hallucination_type=HallucinationType.OMISSION,
        evolution_direction="unresolved_to_decisive",
        module_targeted="candidate_trigger",
        strategy_used="trigger_sensitivity",
        detection_improved=True,
        source_case_id="c1",
        feature_description="tighten census trigger",
        metadata={**signature, "outcome": "accepted"},
    )
    memory.add_experience(
        hallucination_type=HallucinationType.OMISSION,
        evolution_direction="unresolved_to_decisive",
        module_targeted="candidate_trigger",
        strategy_used="trigger_sensitivity",
        detection_improved=False,
        source_case_id="c2",
        feature_description="tighten census trigger",
        metadata={**signature, "outcome": "rejected"},
    )
    entries = memory.get_type_experiences(HallucinationType.OMISSION)
    # detection_improved is part of the experience identity: a flipped
    # detection polarity is a different experience, not a merge candidate.
    assert len(entries) == 2
    memory.add_experience(
        hallucination_type=HallucinationType.OMISSION,
        evolution_direction="unresolved_to_decisive",
        module_targeted="candidate_trigger",
        strategy_used="trigger_sensitivity",
        detection_improved=True,
        source_case_id="c3",
        feature_description="tighten census trigger",
        metadata={**signature, "outcome": "rejected"},
    )
    entries = memory.get_type_experiences(HallucinationType.OMISSION)
    assert len(entries) == 2
    outcome_counts = entries[0].metadata.get("outcome_counts") or {}
    assert outcome_counts.get("accepted") == 1
    assert outcome_counts.get("rejected") == 1


# ============================================================
# Standalone evolution API
# ============================================================

def test_load_records_accepts_string_path(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps({"c1": {"history": [{"role": "user", "content": "x"}]}}),
        encoding="utf-8",
    )
    records = evolution_api.load_records(str(path))
    assert set(records) == {"c1"}


def test_load_records_wrapper_object_not_mapping(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps({
            "case_id": "c1",
            "record": {"history": [{"role": "user", "content": "x"}]},
        }),
        encoding="utf-8",
    )
    records = evolution_api.load_records(path)
    assert set(records) == {"c1"}
    assert "history" in records["c1"]


def test_inherit_conflict_compares_resolved_paths(tmp_path: pathlib.Path) -> None:
    first = tmp_path / "run1"
    first.mkdir(parents=True)
    (first / "champion_genome.json").write_text(
        json.dumps(genome_mod.create_baseline_genome().to_dict()),
        encoding="utf-8",
    )
    (first / "policy_genome.json").write_text(
        json.dumps(_empty_policy_genome().to_dict()), encoding="utf-8"
    )
    (first / "htc_tes_experience_memory.json").write_text(
        json.dumps({"schema_version": "x", "entries": {}}), encoding="utf-8"
    )
    write_state_manifest(
        first, run_id="r1",
        lineage=[{"run_id": "", "dir": str(tmp_path)}],
    )
    load_state_manifest(first)
    os_chdir_backup = pathlib.Path.cwd()
    try:
        import os
        os.chdir(tmp_path)
        records = {"c1": {"history": [{"role": "user", "content": "x"}]}}
        config_path = (
            os_chdir_backup
            / "src/sehd_v6/configs/paca_tcl_detector_v3_5.json"
        )

        with pytest.raises(ValueError, match="mutually exclusive"):
            evolution_api.run_evolution(
                records=records,
                detector_config=config_path,
                output_dir=tmp_path / "run2",
                inherit_from=first,
                resume_from=tmp_path / "run1",
            )
    finally:
        import os
        os.chdir(os_chdir_backup)


# ============================================================
# Experience retriever / reflection wiring
# ============================================================

def test_experience_pattern_uses_shared_signature() -> None:
    from src.sehd_v6.core.bccse.experience_retriever import extract_failure_pattern

    slice_payload = CausalSlice(
        candidate_id="c1",
        candidate_kind="claim",
        origin_refs=(),
        dependency_refs=(),
        required_fields=(),
        required_evidence_refs=(),
        present_fields=(),
        missing_fields=("evidence",),
        malformed_fields=(),
        consumer="proof_court",
        qualification_state="",
        certificate_state="",
        quarantine_state="",
        coverage_state="",
        court_effect="",
        failure_edge=None,
        evidence_refs=(),
        completeness="partial",
    )
    episode = FailureEpisode(
        schema_version="sehd.failure_episode.v1",
        episode_id="ep1",
        record_key="c1",
        detection={"state": "unresolved", "evolution_direction": "x",
                   "trigger_source": "unresolved"},
        attribution={"layer": "l1", "artifact": "candidate_trigger",
                     "reason_code": "candidate_miss",
                     "owner_module": "", "fallback_owner_module": ""},
        causal_slice=slice_payload,
        action_envelope=genome_mod_module_action_envelope(),
    )
    pattern = extract_failure_pattern(episode)
    expected = build_mechanism_signature(
        layer="l1",
        artifact="candidate_trigger",
        reason_code="candidate_miss",
        evolution_direction="x",
        candidate_kind="claim",
        failure_edge_consumer="proof_court",
        missing_or_malformed_fields=("evidence",),
    )
    assert pattern.mechanism_signature == expected
    assert pattern.failure_shape == "missing_field"


def genome_mod_module_action_envelope():
    from src.sehd_v6.core.bccse.failure_episode import ActionEnvelope

    return ActionEnvelope(
        legal_modules=("candidate_trigger",),
        legal_operations=(),
        legal_output_fields=(),
        legal_observables=(),
    )