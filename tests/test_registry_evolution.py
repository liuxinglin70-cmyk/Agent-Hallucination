"""End-to-end tests for P3B online vocabulary evolution.

A ``no_registered_runtime_action`` L2 attribution opens the constrained
registration planner; a registered entry is pending (shadow-only) on first
registration, persists with the policy genome's registry through checkpoint
and resume, and never silently enters the active evaluator.
"""

from __future__ import annotations

from src.sehd_v6.core.bccse.policy_genome import (
    PolicyGenome,
    create_baseline_policy_genome,
)
from src.sehd_v6.core.bccse.vocabulary_registry import (
    L2_REGISTRY,
    STATUS_PENDING,
    apply_registry_edit,
)


def _run_loop(tmp_path, output_dir, monkeypatch, *, detector, mutation,
              signal_for, records, run_id, resume_from=None):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(runner, "attribute_failure", signal_for)
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)
    kwargs = dict(
        evolution_case_ids=("source",),
        canary_case_ids=("anchor",),
        records=records,
        config={},
        output_dir=output_dir,
        census_root=tmp_path,
        mutation_route={"model": "DeepSeek-V4-Flash"},
        run_id=run_id,
    )
    if resume_from is not None:
        kwargs["resume_from"] = resume_from
    return runner.run_online_evolution(**kwargs)


def _anchor_clean():
    return {"state": "verified_clean", "runtime_valid": True, "census": []}


def _unknown_shape_detection():
    """A minimal unresolved detection payload for the L2 registration path;
    the L2 attribution itself is stubbed in the test to focus on the
    registration mechanism."""
    return {
        "state": "unresolved",
        "runtime_valid": True,
        "census": [{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        "proof_adjudication": {
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "surviving_error_certificate_ids": [],
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_applied": False,
            "consensus_gate_blocked_ids": [],
            "required_candidate_count": 1,
        },
    }


def _install_no_action_l2_path(monkeypatch):
    """Force an L2 attribution with no registered runtime action so the run
    opens the registry planner, and stub the policy planner to the
    no-registered-action route. Returns the runner module for further stubs."""
    from src.sehd_v6.core.bccse import evolution_runner as runner_module
    from src.sehd_v6.core.bccse.policy_reflection import PolicyPlan
    from src.sehd_v6.core.bccse.v6_contracts import (
        FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
        ROUTE_NO_REGISTERED_RUNTIME_ACTION,
        FailureAttribution as _FA,
    )

    def l2_attribution(**kwargs):
        attribution = _FA(
            layer="l2_adjudication_policy",
            artifact="coverage_validity_policy",
            reason_code="no_registered_runtime_action",
            evidence={},
        )
        kwargs["non_l1_failure_attributions"].append({
            "case_id": kwargs["case_id"],
            **attribution.to_dict(),
        })
        return "", "no_registered_runtime_action", attribution

    def no_action_plan(*, attribution, genome):
        return PolicyPlan(
            candidates=(),
            route_result=ROUTE_NO_REGISTERED_RUNTIME_ACTION,
            feedback_code=FEEDBACK_POLICY_ROUTE_UNAVAILABLE,
        )

    monkeypatch.setattr(runner_module, "_attribute_failure", l2_attribution)
    monkeypatch.setattr(
        runner_module, "plan_policy_candidates", no_action_plan,
    )
    return runner_module


def _signal_for(result, use_gold=False, gold_reflection=False):
    from src.sehd_v6.core.bccse.step_attributor import (
        AttributionSignal, ErrorType, ReflectionType, TriggerSource,
    )
    stable = str(result["state"]) == "verified_clean"
    return AttributionSignal(
        error_type=(
            ErrorType.CORRECT if stable else ErrorType.EVIDENCE_INCOMPLETE
        ),
        target_module="evidence_obligation",
        secondary_module="atomic_verifier_instruction",
        attribution_confidence="medium",
        reflection_type=(
            ReflectionType.STABLE_SUCCESS if stable
            else ReflectionType.OPTIMIZATION
        ),
        trigger_source=(
            TriggerSource.NONE if stable
            else TriggerSource.EVIDENCE_INCOMPLETE
        ),
        evidence_summary="x",
        prosecution_coverage=0.5,
        defense_coverage=0.5,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="incomplete",
        coverage_quantitative=False,
        crs_scores={},
        crs_aggregate=0.0,
        crs_source="test",
        crs_complete=True,
        consensus_gate_applied=False,
        candidate_count=1,
        state=str(result["state"]),
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )


def test_no_registered_action_registers_pending_vocabulary_entry(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """An unactionable L2 attribution registers a pending (shadow-only)
    vocabulary entry; it never enters the active evaluator this run."""
    _install_no_action_l2_path(monkeypatch)

    def detector(*, genome, case_id, policy_genome=None, **kwargs):
        if case_id == "anchor":
            return _anchor_clean()
        return _unknown_shape_detection()

    mutation_calls: list[dict] = []

    def mutation(**kwargs):
        mutation_calls.append(kwargs)
        return {"candidate_index": 0, "reason": "expose the fact"}, {}

    result = _run_loop(
        tmp_path, tmp_path / "out", monkeypatch,
        detector=detector, mutation=mutation, signal_for=_signal_for,
        records={"source": {}, "anchor": {}},
        run_id="registry_register",
    )

    # The registration planner was consulted (one registry_reflection call).
    registry_calls = [
        c for c in mutation_calls
        if c.get("purpose") == "registry_reflection"
    ]
    assert len(registry_calls) == 1
    # One registration was applied and audited as pending (shadow-only).
    registrations = [
        row for row in result.evolution_audit["l2_registry_registrations"]
        if row.get("outcome") == "registered"
    ]
    assert len(registrations) == 1
    assert registrations[0]["status"] == STATUS_PENDING
    target = registrations[0]["target"]
    # The genome's registry carries the entry but it is NOT in the active
    # evaluator vocabulary (pending is shadow-only).
    registry = result.policy_genome.registry
    assert registry.raw_predicate(target) is not None or (
        registry.raw_action_instance(target) is not None
    )
    assert target not in registry.predicate_names()
    assert target not in registry.action_names()


def test_failed_registration_is_audited_not_silent(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A registration whose model returns no candidate (or whose shadow
    compile rejects) is recorded in the audit trail, never silently dropped."""
    _install_no_action_l2_path(monkeypatch)

    def detector(*, genome, case_id, policy_genome=None, **kwargs):
        if case_id == "anchor":
            return _anchor_clean()
        return _unknown_shape_detection()

    def mutation(**kwargs):
        # Model abstains from every registration candidate.
        return {"no_proposal": True, "reason": "no extension justified"}, {}

    result = _run_loop(
        tmp_path, tmp_path / "out", monkeypatch,
        detector=detector, mutation=mutation, signal_for=_signal_for,
        records={"source": {}, "anchor": {}},
        run_id="registry_abstain",
    )

    registrations = result.evolution_audit["l2_registry_registrations"]
    assert len(registrations) == 1
    assert registrations[0]["outcome"] == "no_registration"
    assert registrations[0]["reason"] == "no extension justified"
    # Abstention never mutates the registry.
    assert result.policy_genome.registry.content_hash == (
        L2_REGISTRY.content_hash
    )


def test_registry_state_persists_through_checkpoint_and_resume(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    """A registered registry travels with the policy genome through the
    checkpoint; resume restores it and its audit trail intact."""
    from src.sehd_v6.core.bccse.step_attributor import (
        AttributionSignal, ErrorType, ReflectionType, TriggerSource,
    )

    # Build a genome whose registry has one pending registered predicate.
    registry = apply_registry_edit(
        L2_REGISTRY, operation="register_predicate",
        target="quarantined_error_count_nonzero",
        dsl="quarantined_error_count > 0",
        reason_codes=("no_registered_runtime_action",),
    )
    genome = create_baseline_policy_genome()
    genome.registry = registry

    # Round-trip through the checkpoint persistence shape.
    payload = genome.to_dict()
    restored = PolicyGenome.from_dict(payload)
    assert restored.content_hash == genome.content_hash
    assert restored.registry.content_hash == registry.content_hash
    # The pending entry survived with its status and audit trail.
    assert restored.registry.status_of(
        "quarantined_error_count_nonzero"
    ) == STATUS_PENDING
    assert [e.target for e in restored.registry.audit] == [
        "quarantined_error_count_nonzero"
    ]
    # The recompiled predicate is a pure function again.
    fn = restored.registry.predicate_fn  # not active (pending) -> None
    assert fn("quarantined_error_count_nonzero") is None
