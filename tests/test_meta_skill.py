"""P7: MetaSkillState identity, lineage and boxed-in MetaEdit operations."""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.meta_skill import (
    GATE_PROFILES,
    GUIDANCE_TOKENS,
    PROFILE_CONSERVATIVE,
    PROFILE_STEADY,
    RANKING_PREFERENCES,
    MetaEdit,
    MetaSkillState,
    OP_ADD_GUIDANCE,
    OP_DISABLE_RANKING,
    OP_ENABLE_RANKING,
    OP_REMOVE_GUIDANCE,
    OP_SWITCH_PROFILE,
    apply_meta_edit,
    create_baseline_meta_skill,
    validate_meta_edit,
)


def make_edit(operation: str, target: str, edit_id: str = "me1") -> MetaEdit:
    return MetaEdit(edit_id=edit_id, operation=operation, target=target)


# ============================================================
# Baseline, identity, lineage (§13.19 MetaSkillState 稳定 hash + lineage)
# ============================================================

def test_baseline_is_conservative_and_empty():
    state = create_baseline_meta_skill()
    assert state.gate_profile == PROFILE_CONSERVATIVE
    assert state.guidance_tokens == ()
    assert state.ranking_preferences == ()


def test_content_hash_stable_and_lineage():
    state = create_baseline_meta_skill()
    assert state.content_hash == create_baseline_meta_skill().content_hash
    child = apply_meta_edit(state, make_edit(OP_ADD_GUIDANCE, "require_independent_anchor"))
    assert child.content_hash != state.content_hash
    assert child.parent_hash == state.content_hash
    assert child.generation == 1
    assert state.guidance_tokens == ()  # parent unchanged (immutable)


def test_roundtrip():
    state = apply_meta_edit(
        create_baseline_meta_skill(),
        make_edit(OP_ADD_GUIDANCE, "prefer_observable_discriminator"),
    )
    restored = MetaSkillState.from_dict(state.to_dict())
    assert restored.to_dict() == state.to_dict()
    assert restored.content_hash == state.content_hash


# ============================================================
# §13.4 MetaEdit add/remove guidance and ranking
# ============================================================

def test_add_and_remove_guidance_token():
    state = create_baseline_meta_skill()
    state = apply_meta_edit(state, make_edit(OP_ADD_GUIDANCE, "reuse_effective_action_shape"))
    assert "reuse_effective_action_shape" in state.guidance_tokens
    state = apply_meta_edit(state, make_edit(OP_REMOVE_GUIDANCE, "reuse_effective_action_shape"))
    assert "reuse_effective_action_shape" not in state.guidance_tokens


def test_enable_and_disable_ranking_preference():
    state = create_baseline_meta_skill()
    state = apply_meta_edit(state, make_edit(OP_ENABLE_RANKING, "owned_output_match"))
    assert "owned_output_match" in state.ranking_preferences
    state = apply_meta_edit(state, make_edit(OP_DISABLE_RANKING, "owned_output_match"))
    assert "owned_output_match" not in state.ranking_preferences


def test_switch_gate_profile():
    state = create_baseline_meta_skill()
    child = apply_meta_edit(state, make_edit(OP_SWITCH_PROFILE, PROFILE_STEADY))
    assert child.gate_profile == PROFILE_STEADY


# ============================================================
# §13.4 forbidden operations / closed vocabulary (fail-closed)
# ============================================================

def test_unknown_guidance_token_rejected():
    state = create_baseline_meta_skill()
    valid, reason = validate_meta_edit(make_edit(OP_ADD_GUIDANCE, "free_text_rule"), state)
    assert not valid
    assert reason.startswith("unknown_guidance_token")


def test_unknown_ranking_preference_rejected():
    state = create_baseline_meta_skill()
    valid, reason = validate_meta_edit(make_edit(OP_ENABLE_RANKING, "arbitrary_threshold"), state)
    assert not valid
    assert reason.startswith("unknown_ranking_preference")


def test_unknown_profile_rejected():
    state = create_baseline_meta_skill()
    valid, reason = validate_meta_edit(make_edit(OP_SWITCH_PROFILE, "aggressive"), state)
    assert not valid
    assert reason.startswith("unknown_gate_profile")


def test_unknown_operation_rejected():
    state = create_baseline_meta_skill()
    valid, reason = validate_meta_edit(make_edit("modify_hard_gate", "x"), state)
    assert not valid
    assert reason.startswith("invalid_operation")


def test_duplicate_guidance_rejected():
    state = apply_meta_edit(
        create_baseline_meta_skill(),
        make_edit(OP_ADD_GUIDANCE, "require_independent_anchor"),
    )
    valid, reason = validate_meta_edit(
        make_edit(OP_ADD_GUIDANCE, "require_independent_anchor"), state,
    )
    assert not valid
    assert reason == "duplicate_guidance_token"


def test_closed_vocabularies_are_bounded():
    # The vocabularies are closed and non-empty; a MetaEdit can never create
    # an operator or a policy item because no such operation exists.
    # v6.1: expanded with negative-feedback tokens and weighted ranking preferences.
    assert len(GUIDANCE_TOKENS) == 15
    assert len(RANKING_PREFERENCES) == 13
    assert GATE_PROFILES == {PROFILE_CONSERVATIVE, PROFILE_STEADY}


def test_unknown_tokens_dropped_on_construction():
    state = MetaSkillState(
        state_id="s",
        guidance_tokens=("require_independent_anchor", "rogue"),
        ranking_preferences=("owned_output_match", "rogue"),
        gate_profile="bogus",
    )
    assert state.guidance_tokens == ("require_independent_anchor",)
    assert state.ranking_preferences == ("owned_output_match",)
    assert state.gate_profile == PROFILE_CONSERVATIVE
