"""Tests for the L2 adjudication policy genome partition."""

import pytest

from src.sehd_v6.core.bccse.policy_genome import (
    FITNESS_STATUS_NONE,
    PolicyEdit,
    PolicyGenome,
    PolicyOp,
    apply_policy_edit,
    create_baseline_policy_genome,
    validate_policy_edit,
)
from src.sehd_v6.core.bccse.v6_contracts import (
    L2_PREDICATE_NAMES,
    POLICY_GENOME_CAPACITY,
    PolicyItem,
)

_DISTINCT_PARAM_COMBOS = (
    {"side": "prosecution", "to_state": "incomplete"},
    {"side": "prosecution", "to_state": "unresolved"},
    {"side": "defense", "to_state": "incomplete"},
    {"side": "defense", "to_state": "unresolved"},
)


def make_distinct_item(index: int) -> PolicyItem:
    # Vary both predicate and params so identity keys stay disjoint across
    # the capacity sweep.
    return PolicyItem(
        item_id=f"policy_{index}",
        precondition=L2_PREDICATE_NAMES[index % len(L2_PREDICATE_NAMES)],
        action="downgrade_coverage",
        params=_DISTINCT_PARAM_COMBOS[index % len(_DISTINCT_PARAM_COMBOS)],
        rationale="test rationale",
        source_case_id="case/x",
    )


def make_item(
    item_id: str = "policy_1",
    precondition: str = "consensus_gate_blocked_nonempty",
    action: str = "downgrade_coverage",
    params: dict | None = None,
    is_baseline: bool = False,
) -> PolicyItem:
    return PolicyItem(
        item_id=item_id,
        precondition=precondition,
        action=action,
        params=(
            params if params is not None
            else {"side": "prosecution", "to_state": "incomplete"}
        ),
        rationale="test rationale",
        source_case_id="case/x",
        is_baseline=is_baseline,
    )


def make_add_edit(item: PolicyItem, edit_id: str = "e1") -> PolicyEdit:
    return PolicyEdit(
        edit_id=edit_id,
        operation=PolicyOp.ADD_ITEM,
        new_item=item,
        rationale="r",
        source_case_id="case/x",
    )


# ============================================================
# Baseline and serialization
# ============================================================

def test_baseline_is_empty_and_roundtrips():
    genome = create_baseline_policy_genome()
    assert genome.total_items == 0
    assert genome.items == []
    assert genome.metadata["baseline"] == "l2_policy_genome_baseline_empty"
    restored = PolicyGenome.from_dict(genome.to_dict())
    assert restored.to_dict() == genome.to_dict()
    assert restored.content_hash == genome.content_hash


def test_edit_roundtrip():
    edit = make_add_edit(make_item())
    restored = PolicyEdit.from_dict(edit.to_dict())
    assert restored.to_dict() == edit.to_dict()


def test_add_item_success_records_meta():
    genome = create_baseline_policy_genome()
    child = apply_policy_edit(genome, make_add_edit(make_item()))
    assert child.total_items == 1
    assert child.items[0].item_id == "policy_1"
    meta = child.get_item_meta(0)
    assert meta["fitness_status"] == FITNESS_STATUS_NONE
    assert meta["source_case_id"] == "case/x"
    assert meta["created_at_generation"] == 1


# ============================================================
# Rejections
# ============================================================

def test_unknown_precondition_rejected():
    genome = create_baseline_policy_genome()
    edit = make_add_edit(make_item(precondition="not_a_predicate"))
    valid, reason = validate_policy_edit(edit, genome)
    assert not valid
    assert reason.startswith("unknown_precondition")


def test_unknown_action_rejected():
    genome = create_baseline_policy_genome()
    edit = make_add_edit(make_item(action="not_an_action", params={}))
    valid, reason = validate_policy_edit(edit, genome)
    assert not valid
    assert reason.startswith("unknown_action")


def test_invalid_params_rejected():
    genome = create_baseline_policy_genome()
    edit = make_add_edit(
        make_item(params={"side": "sidelines", "to_state": "incomplete"})
    )
    valid, reason = validate_policy_edit(edit, genome)
    assert not valid
    assert reason.startswith("invalid_side")


def test_empty_item_id_rejected():
    genome = create_baseline_policy_genome()
    edit = make_add_edit(make_item(item_id="   "))
    valid, reason = validate_policy_edit(edit, genome)
    assert not valid
    assert reason == "empty_item_id"


def test_capacity_rejected():
    assert POLICY_GENOME_CAPACITY <= len(L2_PREDICATE_NAMES) * len(_DISTINCT_PARAM_COMBOS)
    genome = create_baseline_policy_genome()
    for index in range(POLICY_GENOME_CAPACITY):
        genome = apply_policy_edit(
            genome,
            make_add_edit(make_distinct_item(index), edit_id=f"e{index}"),
        )
    assert genome.total_items == POLICY_GENOME_CAPACITY
    extra = make_add_edit(make_item(item_id="overflow"), edit_id="overflow")
    valid, reason = validate_policy_edit(extra, genome)
    assert not valid
    assert reason.startswith("genome_full")


def test_duplicate_item_rejected():
    genome = create_baseline_policy_genome()
    genome = apply_policy_edit(genome, make_add_edit(make_item()))
    duplicate = make_add_edit(
        make_item(item_id="different_id"), edit_id="dup",
    )
    valid, reason = validate_policy_edit(duplicate, genome)
    assert not valid
    assert reason == "duplicate_item"


def test_duplicate_detection_ignores_param_order():
    genome = create_baseline_policy_genome()
    genome = apply_policy_edit(
        genome,
        make_add_edit(make_item(params={"to_state": "incomplete", "side": "prosecution"})),
    )
    duplicate = make_add_edit(
        make_item(item_id="other", params={"side": "prosecution", "to_state": "incomplete"}),
        edit_id="dup",
    )
    valid, _ = validate_policy_edit(duplicate, genome)
    assert not valid


def _genome_with_baseline_and_evolved() -> PolicyGenome:
    baseline_item = make_item(item_id="base_1", is_baseline=True)
    genome = PolicyGenome(
        genome_id="seed",
        items=[baseline_item],
        item_meta=[{"is_baseline": True, "fitness_status": "none"}],
    )
    return apply_policy_edit(
        genome,
        make_add_edit(
            make_item(
                item_id="evolved_1",
                params={"side": "defense", "to_state": "unresolved"},
            )
        ),
    )


def test_baseline_item_modify_and_remove_rejected():
    genome = _genome_with_baseline_and_evolved()
    modify = PolicyEdit(
        edit_id="m1", operation=PolicyOp.MODIFY_ITEM,
        item_index=0, new_item=make_item(item_id="base_1_hack"),
    )
    remove = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=0)
    for edit in (modify, remove):
        valid, reason = validate_policy_edit(edit, genome)
        assert not valid
        assert reason == "immutable_baseline_item"


def test_protected_item_modify_and_remove_rejected():
    genome = _genome_with_baseline_and_evolved()
    genome.set_item_meta(1, {"fitness_status": "strong", "source_case_id": "case/x"})
    modify = PolicyEdit(
        edit_id="m1", operation=PolicyOp.MODIFY_ITEM,
        item_index=1,
        new_item=make_item(
            item_id="evolved_hack",
            params={"side": "prosecution", "to_state": "incomplete"},
        ),
    )
    remove = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=1)
    for edit in (modify, remove):
        valid, reason = validate_policy_edit(edit, genome)
        assert not valid
        assert reason.startswith("protected_item")


def test_index_out_of_range_rejected():
    genome = create_baseline_policy_genome()
    edit = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=3)
    valid, reason = validate_policy_edit(edit, genome)
    assert not valid
    assert reason.startswith("item_index_out_of_range")


# ============================================================
# Immutability, hashing, lineage
# ============================================================

def test_apply_does_not_mutate_parent():
    genome = create_baseline_policy_genome()
    before_hash = genome.content_hash
    child = apply_policy_edit(genome, make_add_edit(make_item()))
    assert genome.total_items == 0
    assert genome.content_hash == before_hash
    assert genome.edits_applied == []
    assert child.total_items == 1
    assert child.parent_hash == before_hash
    assert child.generation == 1
    assert child.genome_id != genome.genome_id


def test_content_hash_ignores_meta_but_tracks_content():
    genome = apply_policy_edit(
        create_baseline_policy_genome(), make_add_edit(make_item())
    )
    before = genome.content_hash
    genome.set_item_meta(0, {"fitness_status": "none", "source_case_id": "case/y"})
    assert genome.content_hash == before

    modified = PolicyEdit(
        edit_id="m1", operation=PolicyOp.MODIFY_ITEM, item_index=0,
        new_item=make_item(params={"side": "defense", "to_state": "unresolved"}),
    )
    child = apply_policy_edit(genome, modified)
    assert child.content_hash != before


def test_content_hash_ignores_policy_item_identity():
    first = apply_policy_edit(
        create_baseline_policy_genome(), make_add_edit(make_item(item_id="a"))
    )
    second = apply_policy_edit(
        create_baseline_policy_genome(), make_add_edit(make_item(item_id="b"))
    )
    assert first.content_hash == second.content_hash


def test_remove_item_works_on_unprotected_item():
    genome = apply_policy_edit(
        create_baseline_policy_genome(), make_add_edit(make_item())
    )
    child = apply_policy_edit(
        genome,
        PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=0),
    )
    assert child.total_items == 0
    assert child.item_meta == []


def test_edits_applied_accumulate():
    genome = create_baseline_policy_genome()
    first = make_add_edit(make_item(item_id="a"), edit_id="e1")
    second = make_add_edit(
        make_item(item_id="b", params={"side": "defense", "to_state": "unresolved"}),
        edit_id="e2",
    )
    child = apply_policy_edit(apply_policy_edit(genome, first), second)
    assert [e.edit_id for e in child.edits_applied] == ["e1", "e2"]
    assert child.generation == 2


def test_apply_invalid_edit_raises():
    genome = create_baseline_policy_genome()
    with pytest.raises(ValueError):
        apply_policy_edit(genome, make_add_edit(make_item(action="nope", params={})))
