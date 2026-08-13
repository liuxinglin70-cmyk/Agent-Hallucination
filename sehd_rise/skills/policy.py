"""L2 adjudication policy genome partition.

L2 artifacts are closed-vocabulary policy items (observable precondition +
enumerated action) evaluated deterministically by the proof court, in
contrast to the L1 post-court Skill genome whose conditions retain text.
This partition is deliberately independent of the L1 genome internals: it
reuses only the shared vocabulary contracts and the versioned registry, so
the two layers can evolve on separate schedules without cross-layer
coupling.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from sehd_rise.skills.vocabulary.contracts import (
    POLICY_GENOME_CAPACITY,
    PolicyItem,
    validate_policy_params,
)
from sehd_rise.skills.vocabulary.registry import L2_REGISTRY, VocabularyRegistry

FITNESS_STATUS_NONE = "none"
FITNESS_STATUS_WEAK = "weak"
FITNESS_STATUS_STRONG = "strong"
FITNESS_STATUS_CONDITIONAL = "conditional"
_FITNESS_STATUSES = frozenset(
    {
        FITNESS_STATUS_NONE,
        FITNESS_STATUS_WEAK,
        FITNESS_STATUS_STRONG,
        FITNESS_STATUS_CONDITIONAL,
    }
)

# Runtime scope vocabulary for ``items_for_scope`` (closed). ``working`` is
# the evolution view: baseline + weak + strong. ``deployable`` is the
# standalone-detector view: baseline + strong only — a weak item has not
# earned measured, independent correctness evidence, so it must not change a
# deployed verdict. ``conditional`` items never enter any runtime scope;
# they live in the genome's pending validation queue until promoted.
SCOPE_WORKING = "working"
SCOPE_DEPLOYABLE = "deployable"
SCOPES = frozenset({SCOPE_WORKING, SCOPE_DEPLOYABLE})

# Pending validation queue item statuses (closed). ``pending`` awaits its
# required validation; ``validated`` / ``rejected`` are terminal outcomes
# recorded so the queue's resume stays auditable.
PENDING_STATUS_PENDING = "pending"
PENDING_STATUS_VALIDATED = "validated"
PENDING_STATUS_REJECTED = "rejected"
PENDING_STATUSES = frozenset(
    {PENDING_STATUS_PENDING, PENDING_STATUS_VALIDATED, PENDING_STATUS_REJECTED}
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
# Pending validation queue item
# ============================================================

@dataclass
class PendingValidationItem:
    """One conditional L2 artifact awaiting its required validation.

    Conditional artifacts (telemetry-only proposals, first-use registry
    entries, shadow Meta/registry candidates, unobservable effects) are
    carried here — persisted with the checkpoint and experience memory — but
    never enter the detector runtime. ``registry_dependencies`` names the
    vocabulary entries the proposal relies on; they are re-validated at
    scope-projection time so a proposal whose dependency was retired is
    dropped instead of silently activating. ``created_sequence`` orders the
    queue deterministically; ``status`` is one of the closed
    ``PENDING_STATUSES``.
    """

    subject_id: str
    layer: str = ""
    proposal: dict[str, Any] = field(default_factory=dict)
    registry_dependencies: tuple[str, ...] = ()
    source_evidence: dict[str, Any] = field(default_factory=dict)
    required_validation: str = ""
    created_sequence: int = 0
    status: str = PENDING_STATUS_PENDING
    observed_case_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in PENDING_STATUSES:
            self.status = PENDING_STATUS_PENDING
        self.registry_dependencies = tuple(self.registry_dependencies)
        self.observed_case_ids = tuple(self.observed_case_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "layer": self.layer,
            "proposal": dict(self.proposal),
            "registry_dependencies": list(self.registry_dependencies),
            "source_evidence": dict(self.source_evidence),
            "required_validation": self.required_validation,
            "created_sequence": self.created_sequence,
            "status": self.status,
            "observed_case_ids": list(self.observed_case_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PendingValidationItem":
        if not isinstance(data, Mapping):
            raise ValueError("pending validation item must be an object")
        return cls(
            subject_id=str(data.get("subject_id") or ""),
            layer=str(data.get("layer") or ""),
            proposal=dict(data.get("proposal") or {}),
            registry_dependencies=tuple(
                str(value)
                for value in (data.get("registry_dependencies") or ())
            ),
            source_evidence=dict(data.get("source_evidence") or {}),
            required_validation=str(data.get("required_validation") or ""),
            created_sequence=int(data.get("created_sequence") or 0),
            status=str(data.get("status") or PENDING_STATUS_PENDING),
            observed_case_ids=tuple(
                str(value)
                for value in (data.get("observed_case_ids") or ())
            ),
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

    ``pending_queue`` carries conditional artifacts awaiting validation.  It
    is persisted with the genome but, like ``item_meta``, never participates
    in ``content_hash`` — a pending artifact is not part of the executable
    policy identity.
    """
    genome_id: str
    items: list[PolicyItem] = field(default_factory=list)
    item_meta: list[dict[str, Any]] = field(default_factory=list)
    parent_hash: str | None = None
    generation: int = 0
    edits_applied: list[PolicyEdit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_queue: list[PendingValidationItem] = field(default_factory=list)
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
        """Protected against modify/remove.

        - ``baseline`` items are always protected;
        - ``strong`` items are protected by default: they carry measured,
          independent correctness evidence and a later reflection must not
          erode a verified win;
        - ``weak`` items are correctable: they entered the working scope on
          coverage-only / local-effect evidence and may be modified or
          removed while they re-earn promotion;
        - ``conditional`` artifacts are never in ``items`` (they live in the
          pending queue), so they cannot be addressed here at all.
        """
        if 0 <= index < len(self.items) and self.items[index].is_baseline:
            return True
        meta = self.get_item_meta(index)
        return meta.get("fitness_status") == FITNESS_STATUS_STRONG

    def _item_in_scope(self, index: int, scope: str) -> bool:
        """Scope membership: baseline always active; weak only in the
        evolution (working) view; strong in both; conditional never."""
        if not (0 <= index < len(self.items)):
            return False
        if self.items[index].is_baseline:
            return True
        status = self.get_item_meta(index).get(
            "fitness_status", FITNESS_STATUS_NONE
        )
        if status == FITNESS_STATUS_STRONG:
            return True
        if status == FITNESS_STATUS_WEAK:
            return scope == SCOPE_WORKING
        # none / conditional: not part of any executable scope.
        return False

    def _dependencies_active(self, index: int) -> bool:
        """True when every registry dependency of item ``index`` resolves in
        the genome's registry active view.  An item whose vocabulary entry was
        retired (disabled) or is still pending is excluded from every scope at
        projection time instead of failing closed inside the evaluator."""
        registry = self.registry or L2_REGISTRY
        item = self.items[index]
        return (
            registry.predicate_entry(item.precondition) is not None
            and item.action in registry.action_names()
        )

    def items_for_scope(self, scope: str) -> list[PolicyItem]:
        """Return the deterministic policy items active under ``scope``.

        The projection validates each item's registry dependencies so a
        vocabulary drift can never smuggle an unexecutable item into the
        runtime: an item referencing a pending/disabled predicate or action
        is dropped from the returned view (the genome itself is unchanged).
        """
        scope = str(scope or "")
        if scope not in SCOPES:
            raise ValueError(f"unknown policy scope: {scope!r}")
        return [
            item
            for index, item in enumerate(self.items)
            if self._item_in_scope(index, scope)
            and self._dependencies_active(index)
        ]

    def pending_items(self) -> list[PendingValidationItem]:
        """Queue entries still awaiting validation, in created order."""
        return [
            entry
            for entry in sorted(
                self.pending_queue, key=lambda entry: entry.created_sequence
            )
            if entry.status == PENDING_STATUS_PENDING
        ]

    def enqueue_pending(self, entry: PendingValidationItem) -> None:
        """Append one conditional artifact idempotently.

        Resume is idempotent: an entry whose ``subject_id`` is already queued
        is not duplicated, so replaying a checkpoint never double-counts a
        pending validation."""
        if any(
            existing.subject_id == entry.subject_id
            for existing in self.pending_queue
        ):
            return
        self.pending_queue.append(entry)

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
            # The pending queue is persisted with the checkpoint/experience
            # memory but is not part of the executable identity (see
            # ``content_hash``); it is the carrier for conditional artifacts.
            "pending_queue": [entry.to_dict() for entry in self.pending_queue],
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
            pending_queue=[
                PendingValidationItem.from_dict(entry)
                for entry in data.get("pending_queue", [])
                if isinstance(entry, Mapping)
            ],
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
            return False, "missing_item_payload"
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
            return False, "missing_item_payload"
        valid, reason = _validate_item_contract(edit.new_item, genome.registry)
        if not valid:
            return False, reason
        replacement_key = _identity_key(edit.new_item)
        # A modify that reproduces the item it replaces is a no-op edit that
        # would still reset its fitness metadata; reject it up front.
        if replacement_key == _identity_key(genome.items[edit.item_index]):
            return False, "semantically_equivalent_item"
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
