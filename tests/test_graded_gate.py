"""P4: graded promotion and the working/deployable scope views.

The evidence grade (how much support an artifact earned) is separated from
its activation (how far it may run).  These tests pin the §10.7 acceptance
contract: weak items are correctable, strong items are protected by default,
conditional artifacts never enter an active item scope, the registry
dependencies are validated at scope-projection time, and the pending queue
resumes idempotently.
"""

from __future__ import annotations

import pytest

from src.sehd_v6.core.bccse.policy_genome import (
    FITNESS_STATUS_CONDITIONAL,
    FITNESS_STATUS_NONE,
    FITNESS_STATUS_STRONG,
    FITNESS_STATUS_WEAK,
    PENDING_STATUS_PENDING,
    PendingValidationItem,
    PolicyEdit,
    PolicyGenome,
    PolicyOp,
    SCOPE_DEPLOYABLE,
    SCOPE_WORKING,
    apply_policy_edit,
    create_baseline_policy_genome,
    validate_policy_edit,
)
from src.sehd_v6.core.bccse.v6_contracts import PolicyItem
from src.sehd_v6.core.bccse.vocabulary_registry import apply_registry_edit


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


def add_genome_with_status(status: str, *, is_baseline: bool = False) -> PolicyGenome:
    genome = apply_policy_edit(
        create_baseline_policy_genome(),
        PolicyEdit(
            edit_id="e1", operation=PolicyOp.ADD_ITEM,
            new_item=make_item(is_baseline=is_baseline), rationale="r",
        ),
    )
    meta = genome.get_item_meta(0)
    meta["fitness_status"] = status
    if is_baseline:
        genome.items[0] = make_item(is_baseline=True)
    genome.set_item_meta(0, meta)
    return genome


# ============================================================
# §10.2 protection semantics
# ============================================================

def test_weak_item_is_modifiable_and_removable():
    genome = add_genome_with_status(FITNESS_STATUS_WEAK)
    modify = PolicyEdit(
        edit_id="m1", operation=PolicyOp.MODIFY_ITEM, item_index=0,
        new_item=make_item(
            item_id="weak_edit",
            params={"side": "defense", "to_state": "unresolved"},
        ),
    )
    remove = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=0)
    for edit in (modify, remove):
        valid, reason = validate_policy_edit(edit, genome)
        assert valid, reason


def test_strong_item_is_protected_by_default():
    genome = add_genome_with_status(FITNESS_STATUS_STRONG)
    modify = PolicyEdit(
        edit_id="m1", operation=PolicyOp.MODIFY_ITEM, item_index=0,
        new_item=make_item(item_id="strong_hack"),
    )
    remove = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=0)
    for edit in (modify, remove):
        valid, reason = validate_policy_edit(edit, genome)
        assert not valid
        assert reason.startswith("protected_item")


def test_baseline_item_is_protected():
    genome = add_genome_with_status(FITNESS_STATUS_NONE, is_baseline=True)
    remove = PolicyEdit(edit_id="r1", operation=PolicyOp.REMOVE_ITEM, item_index=0)
    valid, reason = validate_policy_edit(remove, genome)
    assert not valid
    assert reason == "immutable_baseline_item"


# ============================================================
# §10.2 scope projection
# ============================================================

def _statused_genome(statuses: list[str]) -> PolicyGenome:
    genome = create_baseline_policy_genome()
    combos = (
        {"side": "prosecution", "to_state": "incomplete"},
        {"side": "prosecution", "to_state": "unresolved"},
        {"side": "defense", "to_state": "incomplete"},
        {"side": "defense", "to_state": "unresolved"},
    )
    for index, status in enumerate(statuses):
        genome = apply_policy_edit(genome, PolicyEdit(
            edit_id=f"e{index}", operation=PolicyOp.ADD_ITEM,
            new_item=make_item(item_id=f"item_{index}", params=combos[index]),
            rationale="r",
        ))
        meta = genome.get_item_meta(genome.total_items - 1)
        meta["fitness_status"] = status
        genome.set_item_meta(genome.total_items - 1, meta)
    return genome


def test_working_scope_includes_weak_and_strong_not_conditional():
    genome = _statused_genome([
        FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG, FITNESS_STATUS_NONE,
        FITNESS_STATUS_CONDITIONAL,
    ])
    working_ids = [item.item_id for item in genome.items_for_scope(SCOPE_WORKING)]
    assert working_ids == ["item_0", "item_1"]


def test_deployable_scope_includes_strong_only():
    genome = _statused_genome([
        FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG, FITNESS_STATUS_NONE,
    ])
    deployable_ids = [
        item.item_id for item in genome.items_for_scope(SCOPE_DEPLOYABLE)
    ]
    assert deployable_ids == ["item_1"]


def test_conditional_never_enters_any_active_scope():
    genome = _statused_genome([FITNESS_STATUS_CONDITIONAL])
    assert genome.items_for_scope(SCOPE_WORKING) == []
    assert genome.items_for_scope(SCOPE_DEPLOYABLE) == []


def test_unknown_scope_rejected():
    genome = _statused_genome([FITNESS_STATUS_WEAK])
    with pytest.raises(ValueError):
        genome.items_for_scope("deployed")


# ============================================================
# §10.7 registry dependencies validated at scope projection
# ============================================================

def test_item_with_pending_predicate_is_excluded_from_scope():
    # An item is constructed against a registry where the predicate is
    # already registered+enabled; the dependency then re-enters pending on a
    # follow-up registration cycle.  The pending entry is not in the active
    # vocabulary, so a strong item depending on it drops out of every scope.
    genome = _statused_genome([FITNESS_STATUS_STRONG])
    genome.registry = apply_registry_edit(
        genome.registry,
        operation="register_predicate",
        target="novel_predicate",
        dsl="surviving_error_count > 0",
    )
    genome.registry = apply_registry_edit(
        genome.registry, operation="enable", target="novel_predicate",
    )
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="e_dep", operation=PolicyOp.ADD_ITEM,
        new_item=make_item(item_id="dep_item", precondition="novel_predicate"),
        rationale="r",
    ))
    meta = genome.get_item_meta(genome.total_items - 1)
    meta["fitness_status"] = FITNESS_STATUS_STRONG
    genome.set_item_meta(genome.total_items - 1, meta)
    assert "dep_item" in [
        item.item_id for item in genome.items_for_scope(SCOPE_WORKING)
    ]
    # Re-disable the predicate (retire): the dependent item is projected out.
    genome.registry = apply_registry_edit(
        genome.registry, operation="disable", target="novel_predicate",
    )
    assert "dep_item" not in [
        item.item_id for item in genome.items_for_scope(SCOPE_WORKING)
    ]


def test_item_with_disabled_dependency_is_excluded_from_scope():
    genome = _statused_genome([FITNESS_STATUS_STRONG])
    # Disable a baseline predicate is forbidden; instead register + enable +
    # then disable a court action the item depends on.
    genome.registry = apply_registry_edit(
        genome.registry,
        operation="register_action",
        target="emit_telemetry_only_v2",
        operator="emit_telemetry_only",
    )
    genome.registry = apply_registry_edit(
        genome.registry, operation="enable", target="emit_telemetry_only_v2",
    )
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="e2", operation=PolicyOp.ADD_ITEM,
        new_item=PolicyItem(
            item_id="telemetry_item",
            precondition="consensus_gate_blocked_nonempty",
            action="emit_telemetry_only_v2",
            params={},
            rationale="r",
        ),
        rationale="r",
    ))
    meta = genome.get_item_meta(genome.total_items - 1)
    meta["fitness_status"] = FITNESS_STATUS_STRONG
    genome.set_item_meta(genome.total_items - 1, meta)
    assert "telemetry_item" in [
        item.item_id for item in genome.items_for_scope(SCOPE_WORKING)
    ]
    # Retire the dependency: the item is dropped from every scope even
    # though its own grade is strong.
    genome.registry = apply_registry_edit(
        genome.registry, operation="disable", target="emit_telemetry_only_v2",
    )
    assert "telemetry_item" not in [
        item.item_id for item in genome.items_for_scope(SCOPE_WORKING)
    ]
    assert "telemetry_item" not in [
        item.item_id for item in genome.items_for_scope(SCOPE_DEPLOYABLE)
    ]


# ============================================================
# §10.3 / §10.7 pending queue
# ============================================================

def make_pending(subject_id: str = "subj_1", sequence: int = 1) -> PendingValidationItem:
    return PendingValidationItem(
        subject_id=subject_id,
        layer="l2",
        proposal={"operation": "add_item"},
        registry_dependencies=("consensus_gate_blocked_nonempty",),
        source_evidence={"case_id": "case/x"},
        required_validation="independent_effect",
        created_sequence=sequence,
    )


def test_pending_queue_persisted_but_not_in_content_hash():
    genome = _statused_genome([FITNESS_STATUS_STRONG])
    before = genome.content_hash
    genome.enqueue_pending(make_pending())
    # A pending artifact is not part of the executable policy identity.
    assert genome.content_hash == before
    restored = PolicyGenome.from_dict(genome.to_dict())
    assert [e.subject_id for e in restored.pending_queue] == ["subj_1"]
    assert restored.pending_queue[0].status == PENDING_STATUS_PENDING


def test_pending_queue_resume_is_idempotent():
    genome = create_baseline_policy_genome()
    genome.enqueue_pending(make_pending())
    genome.enqueue_pending(make_pending())  # duplicate subject_id
    assert len(genome.pending_queue) == 1
    restored = PolicyGenome.from_dict(genome.to_dict())
    restored.enqueue_pending(make_pending())
    assert len(restored.pending_queue) == 1


def test_pending_items_returns_only_pending_in_created_order():
    genome = create_baseline_policy_genome()
    genome.enqueue_pending(make_pending("a", sequence=2))
    genome.enqueue_pending(make_pending("b", sequence=1))
    validated = make_pending("c", sequence=0)
    validated.status = "validated"
    genome.enqueue_pending(validated)
    assert [e.subject_id for e in genome.pending_items()] == ["b", "a"]


def test_pending_item_rejects_unknown_status():
    entry = PendingValidationItem(subject_id="x", status="bogus")
    assert entry.status == PENDING_STATUS_PENDING


# ============================================================
# §10.7 hard violation always rejects (promotion contract)
# ============================================================

def test_hard_violation_always_rejects():
    from src.sehd_v6.core.bccse.promotion_contract import (
        AUTHORITY_L2_POLICY,
        GRADE_REJECT,
        promote,
    )
    decision = promote(
        layer="l2",
        artifact="policy_genome",
        legacy_decision="accept_policy",
        decision_authority=AUTHORITY_L2_POLICY,
        reasons=["canary_regression:case/1"],
    )
    assert decision.evidence_grade == GRADE_REJECT
    assert decision.activation_state == ""
    assert "canary_regression" in decision.hard_reject_codes
