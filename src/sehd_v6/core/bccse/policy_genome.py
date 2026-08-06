"""SEHD v6 — L2 adjudication policy genome partition.

L2 artifacts are closed-vocabulary policy items (observable precondition +
enumerated action) evaluated deterministically by the proof court, in
contrast to the L1 provider-rule genome whose rules are natural language.
This partition is deliberately independent of ``genome.py`` internals: it
reuses only the shared vocabulary in ``v6_contracts`` so the two layers can
evolve on separate schedules without cross-layer coupling.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .v6_contracts import (
    POLICY_GENOME_CAPACITY,
    PolicyItem,
    validate_policy_params,
)
from .vocabulary_registry import L2_REGISTRY, VocabularyRegistry

FITNESS_STATUS_NONE = "none"
FITNESS_STATUS_WEAK = "weak"
FITNESS_STATUS_STRONG = "strong"
_FITNESS_STATUSES = frozenset(
    {FITNESS_STATUS_NONE, FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG}
)


class PolicyOp(str, Enum):
    """Allowed operations on the L2 policy genome."""
    ADD_ITEM = "add_item"
    MODIFY_ITEM = "modify_item"
    REMOVE_ITEM = "remove_item"


def _operation_value(operation: Any) -> str:
    return operation.value if isinstance(operation, PolicyOp) else str(operation)


def _identity_key(item: PolicyItem) -> tuple:
    # Params are normalized to a sorted tuple so semantic identity is
    # independent of dict insertion order.
    return (item.precondition, item.action, tuple(sorted(item.params.items())))


# ============================================================
# Edit record
# ============================================================

@dataclass
class PolicyEdit:
    """A single edit to the L2 policy genome."""
    edit_id: str
    operation: str
    item_index: int | None = None
    new_item: PolicyItem | None = None
    rationale: str = ""
    source_case_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "operation": _operation_value(self.operation),
            "item_index": self.item_index,
            "new_item": self.new_item.to_dict() if self.new_item else None,
            "rationale": self.rationale,
            "source_case_id": self.source_case_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyEdit":
        raw_item = data.get("new_item")
        return cls(
            edit_id=str(data.get("edit_id") or ""),
            operation=str(data.get("operation") or ""),
            item_index=data.get("item_index"),
            new_item=PolicyItem.from_dict(raw_item) if raw_item else None,
            rationale=str(data.get("rationale") or ""),
            source_case_id=str(data.get("source_case_id") or ""),
        )


# ============================================================
# Genome
# ============================================================

@dataclass
class PolicyGenome:
    """L2 policy genome: an ordered list of closed-vocabulary policy items.

    ``item_meta`` is a parallel list tracking per-item bookkeeping
    (fitness_status / source_case_id / created_at_generation). Like the L1
    genome it is written AFTER batch commit, so it never participates in
    genome identity.
    """
    genome_id: str
    items: list[PolicyItem] = field(default_factory=list)
    item_meta: list[dict[str, Any]] = field(default_factory=list)
    parent_hash: str | None = None
    generation: int = 0
    edits_applied: list[PolicyEdit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # The versioned L2 vocabulary registry this genome's items are evaluated
    # against. The registry is the single authority persisted with the genome
    # (no second copy in experience memory); it participates in content_hash
    # so a genome's identity binds the exact vocabulary it was built on.
    registry: VocabularyRegistry | None = None

    def __post_init__(self) -> None:
        while len(self.item_meta) < len(self.items):
            self.item_meta.append({})
        self.item_meta = [dict(meta) for meta in self.item_meta[: len(self.items)]]
        if self.registry is None:
            self.registry = L2_REGISTRY

    @property
    def content_hash(self) -> str:
        # Meta is post-commit bookkeeping (fitness outcomes, provenance);
        # including it would change genome identity after every commit and
        # break lineage replay and cache keys. The hash covers the policy
        # items AND the registry's active canonical state (plus compiler and
        # facts schema versions), so a genome built on a different vocabulary
        # has a different identity — usage counts and timestamps never enter.
        state = {
            "items": [
                {
                    "precondition": item.precondition,
                    "action": item.action,
                    "params": dict(item.params),
                }
                for item in self.items
            ],
            "registry": self.registry.to_dict(),
        }
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def total_items(self) -> int:
        return len(self.items)

    def get_item_meta(self, index: int) -> dict[str, Any]:
        if 0 <= index < len(self.item_meta):
            return dict(self.item_meta[index])
        return {}

    def set_item_meta(self, index: int, meta: dict[str, Any]) -> None:
        while len(self.item_meta) <= index:
            self.item_meta.append({})
        normalized = dict(meta)
        if normalized.get("fitness_status") not in _FITNESS_STATUSES:
            normalized["fitness_status"] = FITNESS_STATUS_NONE
        self.item_meta[index] = normalized

    def is_item_protected(self, index: int) -> bool:
        """Protected if baseline or passed fitness (weak/strong)."""
        if 0 <= index < len(self.items) and self.items[index].is_baseline:
            return True
        meta = self.get_item_meta(index)
        return meta.get("fitness_status") in (
            FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "genome_id": self.genome_id,
            "items": [item.to_dict() for item in self.items],
            "item_meta": [dict(meta) for meta in self.item_meta],
            "parent_hash": self.parent_hash,
            "generation": self.generation,
            "edits_applied": [edit.to_dict() for edit in self.edits_applied],
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
            # The registry is the single vocabulary authority persisted with
            # the genome; its audit trail lives inside the registry payload
            # (``registry.audit``), never as a second copy beside it.
            "registry": self.registry.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyGenome":
        registry_payload = data.get("registry")
        registry = (
            VocabularyRegistry.from_dict(registry_payload)
            if isinstance(registry_payload, dict)
            else None
        )
        return cls(
            genome_id=str(data.get("genome_id") or ""),
            items=[PolicyItem.from_dict(d) for d in data.get("items", [])],
            item_meta=[dict(meta) for meta in data.get("item_meta", [])],
            parent_hash=data.get("parent_hash"),
            generation=int(data.get("generation") or 0),
            edits_applied=[
                PolicyEdit.from_dict(edit)
                for edit in data.get("edits_applied", [])
            ],
            metadata=dict(data.get("metadata", {})),
            registry=registry,
        )

    def clone(self) -> "PolicyGenome":
        return PolicyGenome.from_dict(self.to_dict())


# ============================================================
# Validation and application
# ============================================================

def _validate_item_contract(
    item: PolicyItem, registry=None,
) -> tuple[bool, str]:
    # The registry the item is validated against defaults to the baseline
    # vocabulary; a genome carrying an evolved registry validates against
    # that registry so its registered entries are recognized as legal.
    active = registry if registry is not None else L2_REGISTRY
    if not str(item.item_id or "").strip():
        return False, "empty_item_id"
    if item.precondition not in active.predicate_names():
        return False, f"unknown_precondition:{item.precondition}"
    if item.action not in active.action_names():
        return False, f"unknown_action:{item.action}"
    params_valid, params_reason = validate_policy_params(
        item.action, item.params, registry=active,
    )
    if not params_valid:
        return False, params_reason
    return True, ""


def validate_policy_genome_items(genome: PolicyGenome) -> tuple[bool, str]:
    """Validate every item of a loaded genome against the closed vocabulary.

    Loading is fail-closed: a payload that drifted from the genome's own
    registry vocabulary is rejected here instead of being silently skipped
    by the evaluator at runtime.
    """
    for item in genome.items:
        item_ok, item_reason = _validate_item_contract(item, genome.registry)
        if not item_ok:
            return False, f"{item.item_id}:{item_reason}"
    return True, ""


def validate_policy_edit(
    edit: PolicyEdit, genome: PolicyGenome,
) -> tuple[bool, str]:
    """Validate an edit against the closed L2 vocabulary and protection rules."""
    operation = _operation_value(edit.operation)
    allowed_operations = {op.value for op in PolicyOp}
    if operation not in allowed_operations:
        return False, f"invalid_operation:{operation}"
    edit.operation = operation

    if operation == PolicyOp.ADD_ITEM.value:
        if edit.new_item is None:
            return False, "missing_new_item"
        valid, reason = _validate_item_contract(edit.new_item, genome.registry)
        if not valid:
            return False, reason
        if genome.total_items >= POLICY_GENOME_CAPACITY:
            return False, (
                f"genome_full:{genome.total_items} >= {POLICY_GENOME_CAPACITY}"
            )
        if _identity_key(edit.new_item) in {
            _identity_key(item) for item in genome.items
        }:
            return False, "duplicate_item"
        return True, ""

    if edit.item_index is None or edit.item_index < 0:
        return False, "invalid_item_index"
    if edit.item_index >= genome.total_items:
        return False, (
            f"item_index_out_of_range:{edit.item_index} >= {genome.total_items}"
        )
    if genome.items[edit.item_index].is_baseline:
        return False, "immutable_baseline_item"
    if genome.is_item_protected(edit.item_index):
        status = genome.get_item_meta(edit.item_index).get(
            "fitness_status", "unknown"
        )
        return False, f"protected_item: fitness_status={status}"

    if operation == PolicyOp.MODIFY_ITEM.value:
        if edit.new_item is None:
            return False, "missing_new_item"
        valid, reason = _validate_item_contract(edit.new_item, genome.registry)
        if not valid:
            return False, reason
        replacement_key = _identity_key(edit.new_item)
        if replacement_key in {
            _identity_key(item)
            for index, item in enumerate(genome.items)
            if index != edit.item_index
        }:
            return False, "duplicate_item"
    return True, ""


def apply_policy_edit(genome: PolicyGenome, edit: PolicyEdit) -> PolicyGenome:
    """Apply one edit, returning a NEW genome (immutable lineage style)."""
    valid, reason = validate_policy_edit(edit, genome)
    if not valid:
        raise ValueError(f"Invalid policy edit: {reason}")

    child = genome.clone()
    new_generation = genome.generation + 1
    fresh_meta = {
        "source_case_id": edit.source_case_id,
        "fitness_status": FITNESS_STATUS_NONE,
        "created_at_generation": new_generation,
    }

    if edit.operation == PolicyOp.ADD_ITEM.value:
        child.items.append(edit.new_item)
        child.set_item_meta(len(child.items) - 1, fresh_meta)
    elif edit.operation == PolicyOp.MODIFY_ITEM.value:
        # Fitness outcomes belong to the old item content; a modified item
        # must re-earn protection from scratch.
        child.items[edit.item_index] = edit.new_item
        child.set_item_meta(edit.item_index, fresh_meta)
    elif edit.operation == PolicyOp.REMOVE_ITEM.value:
        child.items.pop(edit.item_index)
        if edit.item_index < len(child.item_meta):
            child.item_meta.pop(edit.item_index)

    child.parent_hash = genome.content_hash
    child.generation = new_generation
    child.edits_applied = genome.edits_applied + [edit]
    child.genome_id = f"policy_genome_g{child.generation}_{uuid.uuid4().hex[:8]}"
    return child


# ============================================================
# Baseline factory
# ============================================================

def create_baseline_policy_genome() -> PolicyGenome:
    """The L2 baseline is the empty policy set.

    Evolved items must earn every behavior change; an empty baseline keeps
    the deterministic evaluator at its contract-defined default so L2
    evolution cannot silently inherit adjudication semantics.
    """
    return PolicyGenome(
        genome_id="policy_genome_baseline_empty_v1",
        items=[],
        item_meta=[],
        metadata={
            "baseline": "l2_policy_genome_baseline_empty",
            "baseline_items_immutable": True,
        },
    )
