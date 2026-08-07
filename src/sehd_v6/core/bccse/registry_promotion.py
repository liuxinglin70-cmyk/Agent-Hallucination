"""SEHD v6 — audited pending-to-working promotion for registry entries (P3B).

A newly registered vocabulary entry is ``pending`` (shadow-only) on first
registration; this module owns the deterministic promotion decision that
lets it reach ``registered`` (working).  Promotion is evidence-gated, never
automatic: an entry earns promotion when its motivating failure pattern
recurrs on independent cases and the entry is demonstrably applicable on
the observed adjudication facts — a predicate must fire, an action instance
must resolve to an executable operator.  Everything else stays shadow or is
rejected, and every decision is returned as an audit row.

The promotion threshold mirrors the Meta review's commit evidence gate
(``meta_review.MIN_COMMIT_EVIDENCE``): one occurrence may motivate a
registration, but only a recurring, independently observed pattern may
change the executable vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .policy_genome import (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_REJECTED,
    PENDING_STATUS_VALIDATED,
    PendingValidationItem,
    PolicyEdit,
    PolicyGenome,
    PolicyOp,
)
from .registry_reflection import REGISTRATION_REASON_NO_ACTION
from .v6_contracts import PolicyItem, validate_policy_params
from .vocabulary_registry import (
    L2_REGISTRY,
    AdjudicationFacts,
    apply_registry_edit,
)

# Queue entries with this required_validation value are registry
# promotions; other pending artifacts (conditional policies, shadow meta
# candidates) are not considered here.
REGISTRY_REQUIRED_VALIDATION = "registry_enable"

# A registry entry must be observed on this many distinct cases before it
# may leave shadow mode.
PROMOTION_OBSERVATION_THRESHOLD = 3


def enqueue_registry_validation(
    *,
    genome: PolicyGenome,
    operation: str,
    target: str,
    facts: AdjudicationFacts,
    case_id: str,
    intended_edit: Mapping[str, Any] | None = None,
) -> PendingValidationItem:
    """Queue one freshly registered entry for evidence accumulation.

    The motivating case is the first observation: the registration was
    selected precisely because the addressable facts justified it, so the
    entry must demonstrably apply to those facts or it is rejected at once
    (a predicate that never fires on its own motivating case can never be
    promoted).  Idempotent on ``target`` via the genome's queue.

    ``intended_edit`` is the model-captured edit the registration would
    enable (precondition + operator parameters for an action instance); it
    is stored with the queue entry and becomes a dynamic policy candidate
    after promotion.
    """
    registry = genome.registry or L2_REGISTRY
    entry = PendingValidationItem(
        subject_id=str(target),
        layer="l2_registry",
        proposal={"operation": str(operation), "target": str(target)},
        registry_dependencies=(),
        source_evidence={"case_id": str(case_id)},
        required_validation=REGISTRY_REQUIRED_VALIDATION,
        created_sequence=len(genome.pending_queue),
        status=PENDING_STATUS_PENDING,
    )
    if intended_edit:
        entry.source_evidence["intended_edit"] = dict(intended_edit)
    if operation == "register_predicate":
        predicate = registry.raw_predicate(str(target))
        if predicate is None or not predicate.evaluate(facts):
            entry.status = PENDING_STATUS_REJECTED
            entry.source_evidence["reject_reason"] = (
                "predicate_never_fires_on_motivating_facts"
            )
    elif operation == "register_action":
        instance = registry.raw_action_instance(str(target))
        if (
            instance is None
            or registry.raw_operator(instance.operator) is None
        ):
            entry.status = PENDING_STATUS_REJECTED
            entry.source_evidence["reject_reason"] = (
                "action_instance_unresolvable"
            )
    if entry.status == PENDING_STATUS_PENDING:
        entry.observed_case_ids = (str(case_id),)
    genome.enqueue_pending(entry)
    return entry


def promote_pending_registry_entries(
    *,
    genome: PolicyGenome,
    facts: AdjudicationFacts,
    case_id: str,
    reason_code: str,
) -> list[dict[str, Any]]:
    """Advance pending registry entries that have earned working promotion.

    Fail-closed: only ``no_registered_runtime_action`` observations count
    (the only route that opens registrations), every counted case must be
    distinct, a predicate must fire on the current facts, an action
    instance must resolve to an executable operator, and the entry must
    still exist and be pending in the registry.  A promotion re-enables the
    entry (``registered``) and marks the queue entry validated; an entry
    that is no longer applicable is rejected.  Returns one audit row per
    considered entry.
    """
    audit: list[dict[str, Any]] = []
    if reason_code != REGISTRATION_REASON_NO_ACTION:
        return audit
    registry = genome.registry or L2_REGISTRY
    for entry in list(genome.pending_queue):
        if entry.required_validation != REGISTRY_REQUIRED_VALIDATION:
            continue
        if entry.status != PENDING_STATUS_PENDING:
            continue
        row: dict[str, Any] = {
            "subject_id": entry.subject_id,
            "outcome": "still_pending",
        }
        if str(case_id) not in entry.observed_case_ids:
            entry.observed_case_ids = (*entry.observed_case_ids, str(case_id))
        predicate = registry.raw_predicate(entry.subject_id)
        instance = registry.raw_action_instance(entry.subject_id)
        if predicate is not None:
            applicable = predicate.evaluate(facts)
        elif instance is not None:
            applicable = registry.raw_operator(instance.operator) is not None
        else:
            applicable = False
        if not applicable:
            entry.status = PENDING_STATUS_REJECTED
            row["outcome"] = "rejected"
            row["reason"] = "entry_not_applicable_to_observation"
            audit.append(row)
            continue
        observed = len(entry.observed_case_ids)
        if observed < PROMOTION_OBSERVATION_THRESHOLD:
            row["observed_cases"] = observed
            row["required"] = PROMOTION_OBSERVATION_THRESHOLD
            audit.append(row)
            continue
        try:
            promoted = apply_registry_edit(
                registry,
                operation="enable",
                target=entry.subject_id,
                reason_codes=(reason_code,),
            )
        except ValueError as exc:
            entry.status = PENDING_STATUS_REJECTED
            row["outcome"] = "rejected"
            row["reason"] = f"promotion_failed:{str(exc)[:120]}"
            audit.append(row)
            continue
        genome.registry = promoted
        entry.status = PENDING_STATUS_VALIDATED
        row["outcome"] = "promoted"
        row["observed_cases"] = observed
        row["edit_id"] = promoted.audit[-1].edit_id
        row["registry_hash"] = promoted.content_hash
        audit.append(row)
    return audit


def promoted_intent_candidates(
    genome: PolicyGenome,
    facts: AdjudicationFacts,
) -> tuple[PolicyEdit, ...]:
    """Turn promoted registration intents into executable policy candidates.

    A ``register_action`` entry promoted to working carries the intended
    edit the model captured at registration time.  When the same failure
    pattern recurs, that intent becomes a concrete add_item candidate so
    the promoted vocabulary is actually usable instead of merely
    executable-in-principle.  Fail-closed: the intent is re-validated
    against the genome's registry at proposal time (an entry whose
    vocabulary drifted, or whose intent never became active, contributes
    nothing), and the candidate is deduplicated against the existing items.
    """
    registry = genome.registry or L2_REGISTRY
    existing = {
        (item.precondition, item.action, tuple(sorted(item.params.items())))
        for item in genome.items
    }
    candidates: list[PolicyEdit] = []
    for entry in genome.pending_queue:
        if entry.required_validation != REGISTRY_REQUIRED_VALIDATION:
            continue
        if entry.status != PENDING_STATUS_VALIDATED:
            continue
        if entry.proposal.get("operation") != "register_action":
            continue
        target = str(entry.subject_id)
        if registry.status_of(target) != "registered":
            continue
        instance = registry.raw_action_instance(target)
        if instance is None or registry.raw_operator(instance.operator) is None:
            continue
        intent = entry.source_evidence.get("intended_edit")
        if not isinstance(intent, Mapping):
            continue
        precondition = str(intent.get("precondition") or "")
        params = dict(intent.get("params") or {})
        if precondition not in registry.predicate_names():
            continue
        params_ok, _ = validate_policy_params(
            instance.operator, params, registry=registry,
        )
        if not params_ok:
            continue
        identity = (
            precondition, target, tuple(sorted(params.items())),
        )
        if identity in existing:
            continue
        candidates.append(PolicyEdit(
            edit_id=f"policy_edit_promoted_{hashlib.sha256(
                json.dumps(
                    {"precondition": precondition, "action": target},
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:12]}",
            operation=PolicyOp.ADD_ITEM.value,
            new_item=PolicyItem(
                item_id="",
                precondition=precondition,
                action=target,
                params=params,
                rationale=(
                    "intended edit of the promoted vocabulary entry "
                    f"{target}"
                ),
            ),
            rationale=f"promoted entry {target} intent",
        ))
    return tuple(candidates)
