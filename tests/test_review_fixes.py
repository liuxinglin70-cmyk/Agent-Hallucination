"""Regression tests for bugs found in the post-Phase-6 module review.

Each test pins one concrete defect fixed in the review round:
- the remove_rule NameError, the replay-no-effect classification order,
  the cross-case dedup truncation/module-scope gaps, the L2 fall-through
  episode grounding, the dead causal_link_test opcode, the phantom court
  candidate, the consensus-gate dedup erasure, the dead initial-positive
  preservation, the manifest hash/path hardening, and the reflection
  summary window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sehd_v6.core.bccse.action_spec import compile_action_spec
from src.sehd_v6.core.bccse.evolution_experience_memory import (
    EvolutionExperienceMemory,
    Representative,
)
from src.sehd_v6.core.bccse.failure_episode import (
    FailureAttempt,
    build_failure_episode,
)
from src.sehd_v6.core.bccse.module_charter import MODULE_OBSERVABLE_FIELDS
from src.sehd_v6.core.bccse.v6_contracts import LAYER_L1, FailureAttribution

from tests.fixtures.causal_evolution import (
    edit_payload,
    l2_policy_movement,
    missing_evidence_field,
    signal_for,
    stable_clean,
)


def test_remove_rule_proposal_reaches_gate_without_nameerror(
    tmp_path, monkeypatch,
) -> None:
    """The remove_rule assembly must read the working genome, not the
    undefined `champion` name: a removal proposal must fail with a real
    reason, never a NameError masquerading as a provider exception."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return stable_clean()
        return {
            "state": "unresolved",
            "runtime_valid": True,
            "decision_authority": "authoritative",
            "census": [],
        }

    def mutation(**kwargs):
        return {
            "target_module": "evidence_obligation",
            "operation": "remove_rule",
            "rule_index": 0,
            "rationale": "remove a rule",
        }, {}

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        canary_case_ids=("anchor",),
        records={"source": {}, "anchor": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id="remove_rule_review",
    )
    step = result.steps[0]
    assert step.gate_decision in {"retry_invalid_mutation", "skip_invalid_mutation"}
    reason = str(step.fitness_check.get("mutation_error") or "")
    assert "NameError" not in reason
    assert "single_reflection_runtime_error" not in reason
    assert "action_spec" in reason  # baseline rule has no executable spec


def test_classify_replay_no_effect_branch_order() -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def before(health=None, valid=None):
        return {
            "evidence_health": dict(health or {}),
            "proof_adjudication": dict(valid or {}),
        }

    base = before()
    health_only = before(
        health={"construct_raw_candidate_count": 2},
    )
    valid_only = before(
        valid={"valid_error_certificate_count": 1},
    )
    both = before(
        health={"construct_raw_candidate_count": 2},
        valid={"valid_error_certificate_count": 1},
    )
    assert runner._classify_replay_no_effect(base, base) == (
        "no_observable_change_any_layer"
    )
    assert runner._classify_replay_no_effect(base, health_only) == (
        "provider_output_changed_but_court_invariant"
    )
    assert runner._classify_replay_no_effect(base, valid_only) == (
        "output_changed_then_quarantined"
    )
    assert runner._classify_replay_no_effect(base, both) == (
        "output_changed_then_quarantined"
    )


def _episode_for_memory(*, record_key: str):
    return build_failure_episode(
        detection_result=missing_evidence_field(),
        signal=signal_for("unresolved"),
        attribution=FailureAttribution(
            layer=LAYER_L1,
            artifact="evidence_obligation",
            reason_code="provider_artifact_missing",
            evidence={
                "kind": "error_certificate",
                "missing_fields": ["certificate"],
            },
        ),
        evolution_direction="unresolved_to_decisive",
        record_key=record_key,
        allowed_modules=("evidence_obligation", "atomic_verifier_instruction"),
    )


def _attempt_for(*, proposal_content: str, decision: str = "reject_fitness_no_effect"):
    return FailureAttempt(
        attempt_id="a1",
        proposal_signature="s1",
        operation="add_rule",
        target_module="evidence_obligation",
        action_spec={},
        structural_result="accepted",
        compiled=True,
        injected=True,
        applied=False,
        observable_delta=(),
        downstream_court_delta=(),
        no_effect_reason="condition_not_matched",
        decision=decision,
        proposal_content=proposal_content,
    )


def test_cross_case_dedup_survives_long_rules_and_is_module_scoped() -> None:
    from src.sehd_v6.core.bccse.evolution_runner import (
        _cross_case_no_effect_duplicate,
    )

    long_rule = (
        "When a factual claim relies on a cited trajectory event that was "
        "itself produced by an earlier tool call whose output was later "
        "corrected, and the corrected output no longer supports the claim, "
        "and no independent event corroborates the claim, require the cited "
        "evidence field impact_evidence_refs before accepting the claim as "
        "verified error material for the proof court ledger."
    )
    assert len(long_rule) > 240

    memory = EvolutionExperienceMemory()
    episode = _episode_for_memory(record_key="case_a")
    memory.upsert_observation(episode)
    entry = memory.entries()[0]
    entry.representatives["no_effect"] = Representative(
        completeness="complete",
        evidence_fields=4,
        sequence=2,
        episode_id=episode.episode_id,
        causal_slice={},
        action_spec={},
        outcome={"decision": "reject_fitness_no_effect"},
        rule_text=long_rule,
    )

    assert _cross_case_no_effect_duplicate(
        target_module="evidence_obligation",
        new_content=long_rule,
        entries=memory.entries(),
        exclude_episode_id="case_b",
    ) is True

    # A no-effect representative of a DIFFERENT module must not block the
    # same text in the current module.
    memory.entries()[0].representatives["no_effect"] = Representative(
        completeness="complete",
        evidence_fields=4,
        sequence=3,
        episode_id="other",
        causal_slice={},
        action_spec={},
        outcome={"decision": "reject_fitness_no_effect"},
        rule_text=long_rule,
    )
    assert _cross_case_no_effect_duplicate(
        target_module="candidate_trigger",
        new_content=long_rule,
        entries=memory.entries(),
        exclude_episode_id="case_b",
    ) is False


def test_l2_fallthrough_grounds_l1_episode(tmp_path, monkeypatch) -> None:
    """After an L2 attribution falls through to the L1 skill path, the L1
    steps must carry an L1 episode (not the L2 episode whose policy
    evaluator has no L1 legal surface)."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector(*, genome, case_id, **kwargs):
        if case_id == "anchor":
            return stable_clean()
        return l2_policy_movement()

    def mutation(**kwargs):
        if kwargs.get("purpose") == "policy_reflection":
            return {}, {}
        return {"operation": "no_edit", "rationale": "declined"}, {}

    def signal_for_case(result, use_gold=False, gold_reflection=False):
        # Complete coverage on both sides keeps the L1 artifact projection
        # empty so the consensus-gate attribution stays the L2 layer.
        return signal_for(
            str(result["state"]),
            prosecution_coverage=1.0,
            defense_coverage=1.0,
            prosecution_coverage_state="complete",
            defense_coverage_state="complete",
        )

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(runner, "attribute_failure", signal_for_case)
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)

    result = runner.run_online_evolution(
        evolution_case_ids=("source",),
        canary_case_ids=("anchor",),
        records={"source": {}, "anchor": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id="l2_fallthrough_review",
    )
    decisions = [step.gate_decision for step in result.steps]
    assert decisions[0] == "skip_no_policy"
    assert result.steps[0].episode["attribution"]["layer"] == (
        "l2_adjudication_policy"
    )
    l1_steps = [step for step in result.steps[1:] if step.episode]
    assert l1_steps
    assert l1_steps[0].episode["attribution"]["layer"] == LAYER_L1
    assert l1_steps[0].episode["attribution"]["artifact"] == "evidence_obligation"


def test_causal_link_test_reject_non_causal_origin_is_admissible() -> None:
    """The opcode advertised to the model must be admissible: its observable
    effect (responsible_step) must be an owned field of the module."""
    spec = compile_action_spec(
        "causal_link_test", "reject_non_causal_origin: responsible_event_id",
    )
    assert spec.observable_field == "responsible_step"
    assert "responsible_step" in MODULE_OBSERVABLE_FIELDS["causal_link_test"]
    contract = spec.effect_contract("a supported origin is not causal")
    assert contract["observable_field"] == "responsible_step"
    assert contract["expected_direction"] == "removed"


def test_select_candidate_skips_unknown_court_id() -> None:
    from src.sehd_v6.core.bccse.failure_episode import _select_candidate
    from tests.fixtures.causal_evolution import detection_result

    detection = detection_result(
        state="unresolved",
        census=[
            {"candidate_id": "candidate_1", "origin_refs": ["event_1"]},
            {"candidate_id": "candidate_2", "origin_refs": ["event_9"]},
        ],
        adjudication={
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "positive_candidate_ids": ["ghost_id", "candidate_2"],
        },
    )
    candidate_id, kind, origin_refs, _ = _select_candidate(
        detection_result=detection,
        attribution=FailureAttribution(
            layer=LAYER_L1,
            artifact="evidence_obligation",
            reason_code="provider_artifact_missing",
            evidence={"kind": "error_certificate", "missing_fields": ["certificate"]},
        ),
    )
    assert candidate_id == "candidate_2"
    assert origin_refs == ("event_9",)


def _packet() -> dict:
    return {
        "events": [
            {"event_id": "event_001", "tool_interactions": []},
            {"event_id": "event_002", "tool_interactions": []},
        ]
    }


def _candidate() -> dict:
    return {
        "candidate_id": "candidate_001",
        "origin_hypothesis_event_id": "event_001",
    }


def _error_certificate(*, certificate_id: str = "error_001") -> dict:
    return {
        "certificate_id": certificate_id,
        "source_candidate_id": "candidate_001",
        "origin_event_id": "event_001",
        "claim_kind": "agent_assertion",
        "norm_source": "prior_trajectory_evidence",
        "norm_evidence_refs": ["event_001"],
        "breach_type": "contradiction",
        "violation_evidence_refs": ["event_001"],
        "agent_control_event_id": "event_001",
        "agent_control_evidence_refs": ["event_001"],
        "impact_kind": "final_answer",
        "impact_event_ids": ["event_002"],
        "impact_evidence_refs": ["event_002"],
        "recovery_state": "post_impact_residual",
        "recovery_evidence_refs": ["event_002"],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": ["event_002"],
        "rationale": "caused an incorrect final answer with no repair before impact",
    }


def test_consensus_gate_keeps_independently_verified_positive() -> None:
    """When the merged prosecution contains only the appeal row (the initial
    certificate was deduplicated away), the consensus gate must still see
    the initial prosecution's independent support and keep the positive."""
    from src.sehd_v6.core.paca_tcl import proof_contract

    initial = {"coverage_state": "complete", "candidates": [_error_certificate()]}
    merged = {
        "coverage_state": "complete",
        "candidates": [
            {**_error_certificate(), "certificate_id": "appeal_error_001"},
        ],
    }
    proof = proof_contract.adjudicate_proof_court(
        required_candidates=[_candidate()],
        prosecution=merged,
        initial_prosecution=initial,
        defense={"coverage_state": "complete", "candidates": []},
        appeal_defense={"coverage_state": "complete", "candidates": []},
        packet=_packet(),
        apply_materiality_check=True,
        apply_commitment_check=True,
        apply_consensus_gate_flag=True,
    )
    assert proof["state"] == "verified_error"
    assert proof["consensus_gate_blocked_ids"] == []


def test_complete_initial_positive_preserved_after_appeal_failure() -> None:
    """The production flow marks the complete initial court result locally
    authoritative, which makes _resolve_incomplete_appeal preserve it."""
    from src.sehd_v6.core.paca_tcl import proof_contract, proof_engine

    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    initial = proof_contract.adjudicate_proof_court(
        required_candidates=[_candidate()],
        prosecution=prosecution,
        defense={"coverage_state": "complete", "candidates": []},
        packet=_packet(),
        apply_materiality_check=True,
        apply_commitment_check=True,
        apply_consensus_gate_flag=False,
    )
    assert initial["state"] == "verified_error"
    assert initial.get("decision_authority") is None
    # Mirrors adjudicate_case: the base court verdict is locally
    # authoritative before the appeal augmentation stage.
    initial["decision_authority"] = "authoritative"
    assert proof_engine._initial_error_has_local_authority(initial) is True
    preserved = proof_engine._resolve_incomplete_appeal(
        initial, RuntimeError("appeal transport failed"),
    )
    assert preserved["state"] == "verified_error"
    assert preserved["verdict_authority_scope"] == "initial_local_positive"
    assert preserved["responsible_event_id"] == "event_001"


def test_state_manifest_requires_hash_and_confines_paths(tmp_path) -> None:
    from src.sehd_v6.evolution.state_manifest import (
        MANIFEST_NAME,
        load_state_manifest,
    )

    (tmp_path / "champion_genome.json").write_text("{}", encoding="utf-8")
    (tmp_path / "policy_genome.json").write_text("{}", encoding="utf-8")
    (tmp_path / "htc_tes_experience_memory.json").write_text("{}", encoding="utf-8")
    manifest = {
        "schema_version": "sehd.evolution_state.v1",
        "source_run_id": "x",
        "lineage": [],
        "provider_rule_genome": {"path": "champion_genome.json", "sha256": ""},
        "adjudication_policy_genome": {
            "path": "policy_genome.json", "sha256": "0" * 64,
        },
        "experience_memory": {
            "path": "htc_tes_experience_memory.json", "sha256": "0" * 64,
        },
    }
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lacks sha256"):
        load_state_manifest(tmp_path)

    manifest["provider_rule_genome"]["sha256"] = "0" * 64
    manifest["provider_rule_genome"]["path"] = "../outside.json"
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes"):
        load_state_manifest(tmp_path)


def test_reflection_summary_window_and_successes_consistent() -> None:
    from src.sehd_v6.core.htc_tes.type_aware_fitness import (
        TypeExperienceMemory,
    )
    from src.sehd_v6.core.htc_tes.type_classifier import HallucinationType

    memory = TypeExperienceMemory()
    for index in range(2):
        memory.add_experience(
            hallucination_type=HallucinationType.FACTUAL,
            evolution_direction="unresolved_to_decisive",
            module_targeted="evidence_obligation",
            strategy_used=f"operation:add_rule:{index}",
            detection_improved=index == 0,
            source_case_id=f"case_{index}",
        )
    summary = memory.get_reflection_summary(
        HallucinationType.FACTUAL, limit=0,
    )
    assert summary["total"] == 2
    assert summary["successes"] == 1
    summary = memory.get_reflection_summary(
        HallucinationType.FACTUAL, limit=1,
    )
    assert summary["total"] == 2
    assert summary["successes"] == 1
    assert len(summary["experiences"]) == 1
