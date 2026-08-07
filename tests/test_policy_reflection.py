"""Tests for the L2 adjudication-policy reflection loop.

Covers the prompt/parse contract of ``policy_reflection`` and the
detection-path passthrough in ``proof_engine.adjudicate_case``: empty
policy input must leave the default court calls byte-identical, and a
supplied policy genome must reach the proof-court hook with normalized
items and the engine-level boundary fact.
"""

from __future__ import annotations

from pathlib import Path

from src.sehd_v6.core.bccse.policy_genome import (
    PolicyEdit,
    PolicyOp,
    apply_policy_edit,
    create_baseline_policy_genome,
)
from src.sehd_v6.core.bccse.policy_reflection import (
    build_policy_reflection_prompt,
    current_policy_text,
    parse_policy_proposal,
    plan_policy_candidates,
    select_policy_candidate,
)
from src.sehd_v6.core.bccse.v6_contracts import (
    ACTION_PARAMS,
    L2_ACTIONS,
    L2_PREDICATE_NAMES,
    LAYER_L2,
    FailureAttribution,
    PolicyItem,
)


def _attribution() -> FailureAttribution:
    return FailureAttribution(
        layer=LAYER_L2,
        artifact="coverage_validity_policy",
        reason_code="hollow_complete_defense",
        evidence={"defense_coverage": "complete"},
    )


def test_prompt_carries_full_executable_closed_vocabulary() -> None:
    system, _user = build_policy_reflection_prompt(
        attribution=_attribution(),
        facts_summary={"defense_coverage": "complete"},
        current_policy_text="(empty policy set)",
    )
    for predicate in L2_PREDICATE_NAMES:
        assert predicate in system
    for action in L2_ACTIONS:
        assert action in system
        for param in ACTION_PARAMS[action]:
            assert param in system
    # Engine-level actions are reserved but not proposable: the prompt must
    # not surface vocabulary the runtime cannot execute.
    assert "reclassify_candidate_state" not in system
    assert "require_route_replay" not in system
    assert "no_proposal" in system


def test_prompt_carries_attribution_triple_and_context() -> None:
    attribution = _attribution()
    _system, user = build_policy_reflection_prompt(
        attribution=attribution,
        facts_summary={
            "defense_coverage": "complete",
            "valid_defense_certificate_count": 0,
        },
        current_policy_text=(
            "consensus_gate_blocked_nonempty -> emit_telemetry_only()"
        ),
        rejected_summary="case_x: reject_policy_no_effect (no movement)",
    )
    assert attribution.layer in user
    assert attribution.artifact in user
    assert attribution.reason_code in user
    assert '"defense_coverage": "complete"' in user
    assert "consensus_gate_blocked_nonempty -> emit_telemetry_only()" in user
    assert "case_x" not in user
    assert "reject_policy_no_effect (no movement)" in user


def test_parse_policy_proposal_valid_item_leaves_identity_to_caller() -> None:
    edit, reason = parse_policy_proposal({
        "precondition": "defense_coverage_complete_and_zero_certificates",
        "action": "invalidate_coverage_claim",
        "params": {"side": "defense"},
        "rationale": "hollow complete defense",
    })
    assert reason == ""
    assert edit is not None
    # Legacy responses without an operation field stay add_item.
    assert edit.operation == PolicyOp.ADD_ITEM.value
    assert edit.item_index is None
    assert edit.new_item is not None
    assert edit.new_item.item_id == ""
    assert edit.new_item.precondition == (
        "defense_coverage_complete_and_zero_certificates"
    )
    assert edit.new_item.action == "invalidate_coverage_claim"
    assert edit.new_item.params == {"side": "defense"}

    telemetry_edit, telemetry_reason = parse_policy_proposal({
        "operation": "add_item",
        "precondition": "consensus_gate_blocked_nonempty",
        "action": "emit_telemetry_only",
        "params": {},
        "rationale": "observe",
    })
    assert telemetry_reason == ""
    assert telemetry_edit is not None
    assert telemetry_edit.operation == PolicyOp.ADD_ITEM.value


def test_parse_policy_proposal_modify_and_remove_address_items() -> None:
    modify_edit, modify_reason = parse_policy_proposal({
        "operation": "modify_item",
        "item_index": 2,
        "precondition": "defense_coverage_complete_and_zero_certificates",
        "action": "downgrade_coverage",
        "params": {"side": "defense", "to_state": "incomplete"},
        "rationale": "softer correction",
    })
    assert modify_reason == ""
    assert modify_edit is not None
    assert modify_edit.operation == PolicyOp.MODIFY_ITEM.value
    assert modify_edit.item_index == 2
    assert modify_edit.new_item is not None

    remove_edit, remove_reason = parse_policy_proposal({
        "operation": "remove_item",
        "item_index": 0,
        "rationale": "the item is dead",
    })
    assert remove_reason == ""
    assert remove_edit is not None
    assert remove_edit.operation == PolicyOp.REMOVE_ITEM.value
    assert remove_edit.item_index == 0
    assert remove_edit.new_item is None

    for broken in (
        {"operation": "modify_item"},
        {"operation": "remove_item"},
        {"operation": "modify_item", "item_index": -1},
        {"operation": "remove_item", "item_index": "0"},
        {"operation": "rewrite_policy"},
    ):
        edit, reason = parse_policy_proposal(broken)
        assert edit is None, broken
        assert reason, broken


def test_parse_policy_proposal_rejects_out_of_vocabulary_responses() -> None:
    cases = (
        {
            "precondition": "some_freeform_condition",
            "action": "emit_telemetry_only",
            "params": {},
        },
        {
            "precondition": "consensus_gate_blocked_nonempty",
            "action": "rewrite_verdict",
            "params": {},
        },
        {
            "precondition": "defense_coverage_complete_and_zero_certificates",
            "action": "invalidate_coverage_claim",
            "params": {},
        },
        {
            "precondition": "defense_coverage_complete_and_zero_certificates",
            "action": "downgrade_coverage",
            "params": {"side": "both", "to_state": "unresolved"},
        },
        {
            "precondition": "defense_coverage_complete_and_zero_certificates",
            "action": "invalidate_coverage_claim",
            "params": ["defense"],
        },
    )
    for response in cases:
        edit, reason = parse_policy_proposal(response)
        assert edit is None, response
        assert reason, response


def test_parse_policy_proposal_no_proposal_and_empty() -> None:
    edit, reason = parse_policy_proposal(
        {"no_proposal": True, "reason": "no vocabulary item is justified"},
    )
    assert edit is None
    assert reason == "no vocabulary item is justified"

    edit, reason = parse_policy_proposal({})
    assert edit is None
    assert reason == "empty_response"


def test_policy_planner_proposes_real_boundary_consumption() -> None:
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="boundary_consumption_policy",
        reason_code="boundary_terminal_conflict",
        evidence={"boundary_path_state": "verified_clean", "terminal_state": "unresolved"},
    )

    plan = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )

    assert plan.route_result == "executed"
    assert plan.feedback_code == ""
    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert candidate.operation == PolicyOp.ADD_ITEM.value
    item = candidate.new_item
    assert item is not None
    assert item.precondition == "boundary_state_conflicts_with_terminal"
    assert item.action == "consume_verified_clean_boundary"
    assert item.params == {}


def test_policy_planner_routes_consensus_block_to_engine_level() -> None:
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="consensus_gate_policy",
        reason_code="consensus_gate_blocked",
        evidence={"consensus_gate_blocked_count": 1, "terminal_state": "unresolved"},
    )

    plan = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )

    assert plan.candidates == ()
    assert plan.route_result == "engine_level_intervention_required"
    assert plan.feedback_code == "policy_route_unavailable"


def test_policy_planner_consensus_block_with_verdict_is_not_engine_route() -> None:
    # The engine-level route is defined for `consensus_gate_blocked` AND
    # terminal unresolved. A consensus attribution that already carries a
    # terminal verdict must not claim a pre-ledger engine repair; it falls
    # through to the fail-closed no-registered-action route instead.
    for terminal_state in ("verified_clean", "verified_error", ""):
        attribution = FailureAttribution(
            layer="l2_adjudication_policy",
            artifact="consensus_gate_policy",
            reason_code="consensus_gate_blocked",
            evidence={
                "consensus_gate_blocked_count": 1,
                "terminal_state": terminal_state,
            },
        )

        plan = plan_policy_candidates(
            attribution=attribution,
            genome=create_baseline_policy_genome(),
        )

        assert plan.candidates == ()
        assert plan.route_result == "no_registered_runtime_action"
        assert plan.feedback_code == "policy_route_unavailable"


def test_policy_planner_routes_unknown_reason_to_no_registered_action() -> None:
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="coverage_validity_policy",
        reason_code="reason_without_template",
        evidence={},
    )

    plan = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )

    assert plan.candidates == ()
    assert plan.route_result == "no_registered_runtime_action"
    assert plan.feedback_code == "policy_route_unavailable"


def test_policy_planner_routes_deduped_candidate_to_already_present() -> None:
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="boundary_consumption_policy",
        reason_code="boundary_terminal_conflict",
        evidence={},
    )
    genome = apply_policy_edit(create_baseline_policy_genome(), PolicyEdit(
        edit_id="edit_consume",
        operation=PolicyOp.ADD_ITEM.value,
        new_item=PolicyItem(
            item_id="policy_consume",
            precondition="boundary_state_conflicts_with_terminal",
            action="consume_verified_clean_boundary",
            params={},
        ),
        rationale="seed",
        source_case_id="case_seed",
    ))

    plan = plan_policy_candidates(attribution=attribution, genome=genome)

    # The add candidate is deduplicated; the existing item still offers a
    # removal candidate, so the plan stays executable.
    assert plan.route_result == "executed"
    assert [c.operation for c in plan.candidates] == [PolicyOp.REMOVE_ITEM.value]

    # With the baseline-protection case the removal also disappears when the
    # item is baseline, leaving an explicit already-present route.
    baseline_item = PolicyItem(
        item_id="policy_consume_baseline",
        precondition="boundary_state_conflicts_with_terminal",
        action="consume_verified_clean_boundary",
        params={},
        is_baseline=True,
    )
    baseline_genome = create_baseline_policy_genome()
    baseline_genome.items.append(baseline_item)
    baseline_plan = plan_policy_candidates(
        attribution=attribution, genome=baseline_genome,
    )
    assert baseline_plan.candidates == ()
    assert baseline_plan.route_result == "candidate_already_present"
    assert baseline_plan.feedback_code == "policy_route_unavailable"


def test_policy_planner_owns_predicate_action_pairing() -> None:
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="coverage_validity_policy",
        reason_code="hollow_complete_prosecution",
        evidence={},
    )
    plan = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )
    candidates = plan.candidates

    assert plan.route_result == "executed"
    assert len(candidates) == 1
    item = candidates[0].new_item
    assert item is not None
    assert item.precondition == "prosecution_coverage_complete_and_zero_certificates"
    assert item.action == "invalidate_coverage_claim"
    assert item.params == {"side": "prosecution"}
    selected, reason = select_policy_candidate({"candidate_index": 0}, candidates)
    assert reason == ""
    assert selected is candidates[0]


def test_policy_planner_identity_is_content_addressed() -> None:
    attribution = FailureAttribution(
        layer=LAYER_L2,
        artifact="coverage_validity_policy",
        reason_code="hollow_complete_defense",
        evidence={},
    )

    first = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )
    second = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )

    assert first.candidates[0].edit_id == second.candidates[0].edit_id
    assert first.to_dict() == second.to_dict()


def _genome_with_telemetry_item():
    genome = create_baseline_policy_genome()
    edit = PolicyEdit(
        edit_id="edit_seed",
        operation=PolicyOp.ADD_ITEM.value,
        new_item=PolicyItem(
            item_id="policy_seed",
            precondition="consensus_gate_blocked_nonempty",
            action="emit_telemetry_only",
            params={},
            rationale="observe gate blocking",
        ),
        rationale="seed",
        source_case_id="case_seed",
    )
    return apply_policy_edit(genome, edit)


def test_current_policy_text_renders_baseline_and_items() -> None:
    assert current_policy_text(create_baseline_policy_genome()) == (
        "(empty policy set)"
    )
    genome = _genome_with_telemetry_item()
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="edit_second",
        operation=PolicyOp.ADD_ITEM.value,
        new_item=PolicyItem(
            item_id="policy_second",
            precondition="defense_coverage_complete_and_zero_certificates",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "unresolved"},
            rationale="hollow defense",
        ),
        rationale="second",
        source_case_id="case_seed",
    ))
    lines = current_policy_text(genome).splitlines()
    assert lines == [
        "[0] consensus_gate_blocked_nonempty -> emit_telemetry_only()",
        "[1] defense_coverage_complete_and_zero_certificates -> "
        "downgrade_coverage(side=defense, to_state=unresolved)",
    ]


# ---------------------------------------------------------------------------
# Detection-path passthrough (proof_engine.adjudicate_case).
# ---------------------------------------------------------------------------


def _minimal_record() -> dict:
    return {
        "question": "test",
        "history": [{"step": 1, "role": "assistant", "content": "answer"}],
    }


def _minimal_config() -> dict:
    return {
        "construct_census_route": {"name": "construct"},
        "defense_route": {"name": "defense"},
    }


def _base_adjudication(**_kwargs) -> dict:
    return {
        "runtime_valid": True,
        "state": "verified_clean",
        "census": [],
        "qualifications": [],
        "propagations": [],
        "boundary_path": {
            "state": "verified_clean",
            "responsible_event_id": None,
            "evidence_refs": [],
            "rationale": "No base candidate.",
            "contract_validation": {"decision": "pass", "failed_checks": []},
        },
        "adjudication": {"state": "verified_clean"},
        "providers": {},
    }


def _provider_stub(*, route, **_kwargs):
    return {"coverage_state": "complete", "candidates": []}, {}


def _install_engine_stubs(monkeypatch, tmp_path: Path) -> list[dict]:
    """Stub the detector dependencies and record every proof-court call."""
    from src.sehd_v6.core.paca_tcl import proof_engine

    calls: list[dict] = []
    real_court = proof_engine.contract.adjudicate_proof_court

    def recording_court(**kwargs):
        calls.append(kwargs)
        return real_court(**kwargs)

    monkeypatch.setattr(proof_engine.v2, "adjudicate_case", _base_adjudication)
    monkeypatch.setattr(proof_engine, "provider_call", _provider_stub)
    monkeypatch.setattr(
        proof_engine.contract, "adjudicate_proof_court", recording_court,
    )
    return calls


def test_adjudicate_case_without_policy_items_keeps_default_path(
    monkeypatch, tmp_path: Path,
) -> None:
    from src.sehd_v6.core.paca_tcl import proof_engine

    calls = _install_engine_stubs(monkeypatch, tmp_path)
    proof_engine.adjudicate_case(
        case_id="case_plain",
        record=_minimal_record(),
        config=_minimal_config(),
        output_dir=tmp_path,
        census_root=tmp_path,
    )
    assert calls, "the proof court was never called"
    for kwargs in calls:
        assert "policy_items" not in kwargs
        assert "boundary_path_state" not in kwargs


def test_adjudicate_case_forwards_policy_items_to_proof_court(
    monkeypatch, tmp_path: Path,
) -> None:
    from src.sehd_v6.core.paca_tcl import proof_engine

    calls = _install_engine_stubs(monkeypatch, tmp_path)
    policy_dict = {
        "item_id": "policy_hook_probe",
        "precondition": "consensus_gate_blocked_nonempty",
        "action": "emit_telemetry_only",
        "params": {},
        "rationale": "probe",
        "source_case_id": "case_policy",
        "is_baseline": False,
    }
    result = proof_engine.adjudicate_case(
        case_id="case_policy",
        record=_minimal_record(),
        config=_minimal_config(),
        output_dir=tmp_path,
        census_root=tmp_path,
        policy_items=[policy_dict],
    )

    policy_calls = [kwargs for kwargs in calls if "policy_items" in kwargs]
    assert len(policy_calls) == 1
    hook_kwargs = policy_calls[0]
    # The policy re-adjudication replays the exact evidence bundle of the
    # court call that produced the current proof, plus the five hook inputs.
    prior_bundle = {
        key: value for key, value in hook_kwargs.items()
        if key not in (
            "policy_items", "boundary_path_state", "boundary_authority_valid",
            "policy_registry", "pre_ledger_policy_items",
        )
    }
    default_calls = [kwargs for kwargs in calls if "policy_items" not in kwargs]
    assert prior_bundle == default_calls[-1]
    items = hook_kwargs["policy_items"]
    assert len(items) == 1
    assert isinstance(items[0], PolicyItem)
    assert items[0].item_id == "policy_hook_probe"
    # A post-ledger action routes to the post-ledger surface only; the
    # pre-ledger surface stays inert (P9 stage routing).
    assert hook_kwargs["pre_ledger_policy_items"] is None
    assert hook_kwargs["boundary_path_state"] == "verified_clean"
    # No policy genome was supplied to the detection entry point, so the
    # vocabulary registry defaults to the baseline (None -> evaluator default).
    assert hook_kwargs["policy_registry"] is None
    # The boundary authority is derived from the deterministic boundary
    # path's contract validation, not from the boundary state alone.
    assert hook_kwargs["boundary_authority_valid"] is True
    # The deterministic evaluator ran inside the court hook.
    evaluation = result["proof_adjudication"]["policy_evaluation"]
    assert evaluation["fired"] == []


def test_adjudicate_case_fails_closed_on_quarantined_boundary(
    monkeypatch, tmp_path: Path,
) -> None:
    from src.sehd_v6.core.paca_tcl import proof_engine

    def quarantined_boundary_adjudication(**kwargs) -> dict:
        base = _base_adjudication(**kwargs)
        base["boundary_path"]["contract_validation"] = {
            "decision": "quarantine", "failed_checks": ["rationale"],
        }
        return base

    calls: list[dict] = []
    real_court = proof_engine.contract.adjudicate_proof_court

    def recording_court(**kwargs):
        calls.append(kwargs)
        return real_court(**kwargs)

    monkeypatch.setattr(
        proof_engine.v2, "adjudicate_case", quarantined_boundary_adjudication,
    )
    monkeypatch.setattr(proof_engine, "provider_call", _provider_stub)
    monkeypatch.setattr(
        proof_engine.contract, "adjudicate_proof_court", recording_court,
    )
    policy_dict = {
        "item_id": "policy_consume_probe",
        "precondition": "boundary_state_conflicts_with_terminal",
        "action": "consume_verified_clean_boundary",
        "params": {},
        "rationale": "probe",
        "source_case_id": "case_policy",
        "is_baseline": False,
    }
    proof_engine.adjudicate_case(
        case_id="case_quarantined_boundary",
        record=_minimal_record(),
        config=_minimal_config(),
        output_dir=tmp_path,
        census_root=tmp_path,
        policy_items=[policy_dict],
    )
    hook_kwargs = [
        kwargs for kwargs in calls if "policy_items" in kwargs
    ][0]
    assert hook_kwargs["boundary_authority_valid"] is False


def test_adjudicate_case_routes_pre_ledger_items_to_pre_ledger_surface(
    monkeypatch, tmp_path: Path,
) -> None:
    """G4: a pre-ledger action reaches the court's pre-ledger surface.

    Before the stage split the engine forwarded every item to
    ``policy_items`` only, leaving the pre-ledger consensus-recovery path
    unreachable outside its own unit test.
    """
    from src.sehd_v6.core.paca_tcl import proof_engine

    calls = _install_engine_stubs(monkeypatch, tmp_path)
    policy_dict = {
        "item_id": "policy_pre_ledger_probe",
        "precondition": "consensus_gate_blocked_nonempty",
        "action": "restore_audited_consensus",
        "params": {},
        "rationale": "probe",
        "source_case_id": "case_policy",
        "is_baseline": False,
    }
    proof_engine.adjudicate_case(
        case_id="case_pre_ledger",
        record=_minimal_record(),
        config=_minimal_config(),
        output_dir=tmp_path,
        census_root=tmp_path,
        policy_items=[policy_dict],
    )

    hook_kwargs = [kwargs for kwargs in calls if "policy_items" in kwargs][0]
    pre_items = hook_kwargs["pre_ledger_policy_items"]
    assert pre_items is not None, "pre-ledger surface never received the item"
    assert [item.item_id for item in pre_items] == ["policy_pre_ledger_probe"]
    assert all(isinstance(item, PolicyItem) for item in pre_items)
    # Stage routing is exclusive: a pre-ledger action must not also appear on
    # the post-ledger surface, which would double-apply one authority.
    assert hook_kwargs["policy_items"] == []


def test_adjudicate_case_splits_mixed_stage_policy_items(
    monkeypatch, tmp_path: Path,
) -> None:
    """A mixed batch is partitioned by the registry's declared stage."""
    from src.sehd_v6.core.paca_tcl import proof_engine

    calls = _install_engine_stubs(monkeypatch, tmp_path)
    pre_item = {
        "item_id": "policy_pre",
        "precondition": "consensus_gate_blocked_nonempty",
        "action": "restore_audited_consensus",
        "params": {},
        "rationale": "probe",
        "source_case_id": "case_policy",
        "is_baseline": False,
    }
    post_item = {
        "item_id": "policy_post",
        "precondition": "consensus_gate_blocked_nonempty",
        "action": "emit_telemetry_only",
        "params": {},
        "rationale": "probe",
        "source_case_id": "case_policy",
        "is_baseline": False,
    }
    proof_engine.adjudicate_case(
        case_id="case_mixed_stage",
        record=_minimal_record(),
        config=_minimal_config(),
        output_dir=tmp_path,
        census_root=tmp_path,
        policy_items=[pre_item, post_item],
    )

    hook_kwargs = [kwargs for kwargs in calls if "policy_items" in kwargs][0]
    assert [i.item_id for i in hook_kwargs["pre_ledger_policy_items"]] == [
        "policy_pre",
    ]
    assert [i.item_id for i in hook_kwargs["policy_items"]] == ["policy_post"]
