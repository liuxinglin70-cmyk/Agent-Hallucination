from detector.replay import partial_replay_plan


def test_full_view_action_changes_actual_replay_boundary_identity():
    ordinary = {"hook": "adjudicate.guard", "type": "decision_guard",
                "body": {"action": "reject"}}
    full_view = {**ordinary, "body": {"action": "require_full_view_defense"}}
    partial = partial_replay_plan([ordinary])
    fresh = partial_replay_plan([full_view])
    assert not partial["fresh_required"] and fresh["fresh_required"]
    assert partial["rerun_stages"] != fresh["rerun_stages"]
    assert partial["boundary_key"] != fresh["boundary_key"]


def test_unknown_hook_cannot_declare_any_stage_reusable():
    plan = partial_replay_plan([{"hook": "unknown.system"}])
    assert plan["fresh_required"]
    assert plan["unknown_hooks"] == ["unknown.system"]
    assert plan["reusable_stages"] == []


def test_runtime_hook_vocabulary_has_complete_replay_policies():
    from detector.interventions import HOOKS
    from detector.replay import HOOK_POLICIES
    assert set(HOOK_POLICIES) == set(HOOKS)


def test_unknown_hook_refuses_top_level_and_hearing_stage_reuse():
    from detector.replay import frozen_masked_stages
    frozen = {"judge_result": {}, "hearings": [{"tracer_result": {}, "defense_result": {}}]}
    assert frozen_masked_stages(frozen, ["unknown.system"]) == ["defense", "judge", "tracer"]


def test_active_full_view_guard_changes_candidate_boundary():
    from detector.identity import digest
    candidate = {"hook": "tracer.system"}
    active = {"hook": "adjudicate.guard", "type": "decision_guard",
              "body": {"action": "require_full_view_defense"}}
    plan = partial_replay_plan([candidate], active_entries=[active])
    assert plan["fresh_required"] and not plan["reusable_stages"]
    assert len(plan["boundary_key"]) == 64
    assert plan["boundary_key"] == digest({key: value for key, value in plan.items() if key != "boundary_key"})
