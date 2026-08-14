"""L2 vocabulary evolution: constrained registration planning and promotion.

When the L2 policy planner finds no registered runtime action for an
attribution (route ``no_registered_runtime_action``), the vocabulary itself
may be extended. The open/close decision lives solely in that route word
owned by the policy planner; this module consumes it verbatim and never
re-derives it from the causal attribution's reason vocabulary. This module
owns the constrained registration path and the audited pending-to-working
promotion:

- ``plan_registry_candidates`` enumerates deterministic, precomputed
  registration templates (a new predicate over the facts whitelist, or a new
  action instance bound to an existing court-local operator). The model may
  only select one candidate or abstain — it never composes free DSL text or
  new operator semantics (no free-text adjudication logic).
- ``compile_registration_candidate`` shadow-compiles a selected candidate
  into a ``RegistryEdit``-ready payload, failing closed on any whitelist or
  schema violation so an invalid registration is never applied.
- ``build_registry_selection_prompt`` / ``select_registry_candidate`` mirror
  the policy-selection contract: the model ranks precomputed candidates by
  index, keeping runtime semantics owned by deterministic code.
- ``enqueue_registry_validation`` / ``promote_pending_registry_entries`` own
  the promotion decision. Promotion is evidence-gated, never automatic: an
  entry earns promotion when its motivating failure pattern recurs on
  independent cases and the entry is demonstrably applicable on the observed
  adjudication facts. The threshold mirrors the Meta review's commit
  evidence gate: one occurrence may motivate a registration, but only a
  recurring, independently observed pattern may change the executable
  vocabulary.
- ``promoted_intent_candidates`` turns promoted registration intents into
  executable add_item policy candidates.

Newly registered entries are always ``pending`` (shadow-only) on first
registration and never enter the active evaluator until a later audited
promotion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from sehd_rise.skills.policy import (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_REJECTED,
    PENDING_STATUS_VALIDATED,
    PendingValidationItem,
    PolicyEdit,
    PolicyGenome,
    PolicyOp,
)
from sehd_rise.skills.vocabulary.contracts import (
    POLICY_GENOME_CAPACITY,
    PolicyItem,
    validate_policy_params,
)
from sehd_rise.skills.vocabulary.registry import (
    L2_REGISTRY,
    POLICY_STAGE_POST_LEDGER,
    AdjudicationFacts,
    RegistryDslError,
    VocabularyRegistry,
    apply_registry_edit,
    compile_predicate_dsl,
)


class FailureAttribution(Protocol):
    """Structural view of an L2 failure classification.

    Only the shape this module consumes is declared. The unified causal
    object that owns attribution semantics lands with the causal layer;
    skills declares the shape it needs and stays below it.
    """

    def to_dict(self) -> dict[str, Any]: ...


# Prompt-facing purpose for the registration reflection call. The runner
# routes it through the mutation route under its own cache namespace so
# registration calls are counted and replayed independently of policy
# reflection.
PURPOSE_REGISTRY_REFLECTION = "registry_reflection"

# Closed registration reasons that can open the vocabulary planner.
REGISTRATION_REASON_NO_ACTION = "no_registered_runtime_action"

# Queue entries with this required_validation value are registry
# promotions; other pending artifacts (conditional policies, shadow meta
# candidates) are not considered here.
REGISTRY_REQUIRED_VALIDATION = "registry_enable"

# A registry entry must be observed on this many distinct cases before it
# may leave shadow mode.
PROMOTION_OBSERVATION_THRESHOLD = 3


# ============================================================
# Registration planning
# ============================================================

@dataclass(frozen=True)
class RegistryCandidate:
    """One precomputed, deterministic registration candidate."""
    candidate_id: str
    operation: str  # register_predicate | register_action
    target: str
    dsl: str = ""
    operator: str = ""
    rationale: str = ""
    # Intended edit captured at registration time (register_action only):
    # the model names the precondition and the operator parameters it
    # believes repair the observed failure.  Stored with the pending
    # validation entry and reused as a dynamic policy candidate after the
    # entry is promoted, so a promoted vocabulary entry is actually usable.
    precondition: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "operation": self.operation,
            "target": self.target,
            "dsl": self.dsl,
            "operator": self.operator,
            "rationale": self.rationale,
            "precondition": self.precondition,
            "params": dict(self.params),
        }


def _candidate_id(operation: str, target: str, dsl: str, operator: str) -> str:
    payload = json.dumps(
        {
            "operation": operation,
            "target": target,
            "dsl": dsl,
            "operator": operator,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"registry_candidate_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def plan_registry_candidates(
    *,
    attribution: FailureAttribution,
    registry: VocabularyRegistry,
    route_result: str,
) -> tuple[RegistryCandidate, ...]:
    """Enumerate deterministic registration candidates for one attribution.

    ``route_result`` is the policy planner's route word for the same
    attribution and is the single authority that opens the vocabulary
    planner: only ``no_registered_runtime_action`` does. The causal
    attribution's own reason vocabulary plays no part in that decision.
    Candidates are confined to the registry's existing court-local operators
    (for actions) and the facts whitelist (for predicates); the model cannot
    invent operator semantics or reach outside the addressable facts.
    """
    if route_result != REGISTRATION_REASON_NO_ACTION:
        return ()
    candidates: list[RegistryCandidate] = []

    # Candidate family 1: register a new predicate that exposes an existing
    # addressable fact the current vocabulary does not yet name, so a future
    # policy can precondition on it. The template is deterministic and
    # whitelist-checked at shadow-compile time.
    for fact_field in (
        "quarantined_error_count",
        "quarantined_defense_count",
        "defeated_error_count",
        "required_candidate_count",
        # ``quarantine_reason_other`` is the one remaining quarantine-reason
        # bucket the baseline predicate set never references (the schema /
        # evidence / causal buckets are already named by the dominance
        # predicates, so the dedup below skips them). Exposing it lets a
        # future policy react to a quarantine whose dominant cause is none of
        # the three recognized buckets.
        "quarantine_reason_other",
    ):
        if fact_field in {
            field
            for name in registry.all_predicate_names()
            for field in (
                registry.raw_predicate(name).facts_whitelist
                if registry.raw_predicate(name) is not None else ()
            )
        }:
            continue
        dsl = f"{fact_field} > 0"
        target = f"{fact_field}_nonzero"
        if registry.raw_predicate(target) is not None:
            continue
        candidates.append(RegistryCandidate(
            candidate_id=_candidate_id("register_predicate", target, dsl, ""),
            operation="register_predicate",
            target=target,
            dsl=dsl,
            rationale=(
                f"Expose the addressable fact {fact_field} as a registered "
                "precondition so an L2 policy can react to it."
            ),
        ))

    # Candidate family 2: register a new action instance bound to an existing
    # court-local operator (the runtime already owns its semantics). This
    # never creates a new operator — only a new proposable instance.
    for name, operator in sorted(_court_operators(registry).items()):
        instance_name = f"{name}_instance"
        if registry.raw_action_instance(instance_name) is not None:
            continue
        candidates.append(RegistryCandidate(
            candidate_id=_candidate_id(
                "register_action", instance_name, "", name,
            ),
            operation="register_action",
            target=instance_name,
            operator=name,
            rationale=(
                f"Register a proposable instance of the existing court-local "
                f"operator {name}."
            ),
        ))
    return tuple(candidates)


def _court_operators(registry: VocabularyRegistry) -> dict[str, Any]:
    return {
        name: registry.raw_operator(name)
        for name in registry.all_operator_names()
        if registry.raw_operator(name) is not None
        and registry.raw_operator(name).runtime_owner == "court"
        and registry.raw_operator(name).policy_stage == POLICY_STAGE_POST_LEDGER
    }


def compile_registration_candidate(
    candidate: RegistryCandidate,
    registry: VocabularyRegistry,
) -> dict[str, Any]:
    """Shadow-compile a selected candidate into a RegistryEdit-ready payload.

    Fail-closed: any whitelist violation, unknown operator, duplicate target
    or invalid DSL raises so the registration is never applied. Returns the
    keyword arguments for ``apply_registry_edit``.
    """
    if candidate.operation == "register_predicate":
        # Shadow compile: validate the DSL against the facts whitelist now so
        # an invalid source never reaches the registry.
        try:
            compile_predicate_dsl(candidate.target, candidate.dsl)
        except RegistryDslError:
            raise
        if registry.raw_predicate(candidate.target) is not None:
            raise ValueError(f"predicate already registered: {candidate.target}")
        return {
            "operation": "register_predicate",
            "target": candidate.target,
            "dsl": candidate.dsl,
        }
    if candidate.operation == "register_action":
        operator = registry.raw_operator(candidate.operator)
        if operator is None:
            raise ValueError(f"unknown action operator: {candidate.operator}")
        if operator.runtime_owner != "court":
            raise ValueError(
                f"operator not court-local: {candidate.operator}"
            )
        if registry.raw_action_instance(candidate.target) is not None:
            raise ValueError(
                f"action instance already registered: {candidate.target}"
            )
        if candidate.precondition and (
            candidate.precondition not in registry.predicate_names()
        ):
            raise ValueError(
                f"intent precondition not active: {candidate.precondition}"
            )
        if candidate.params:
            params_ok, params_reason = validate_policy_params(
                candidate.operator, dict(candidate.params), registry=registry,
            )
            if not params_ok:
                raise ValueError(f"intent params invalid: {params_reason}")
        return {
            "operation": "register_action",
            "target": candidate.target,
            "operator": candidate.operator,
        }
    raise ValueError(f"unknown registration operation: {candidate.operation}")


def build_registry_selection_prompt(
    *,
    attribution: FailureAttribution,
    facts_summary: Mapping[str, Any],
    candidates: tuple[RegistryCandidate, ...],
) -> tuple[str, str]:
    """Ask the model only to select a precomputed registration or abstain."""
    system = (
        "Select at most one precomputed L2 vocabulary registration. The "
        "registration semantics, DSL whitelist and operator bindings are "
        "fixed by the framework; you only rank candidates, you never compose "
        "free DSL or new operator semantics. Return JSON only: "
        "{\"candidate_index\": <int>, \"reason\": \"...\"} or "
        "{\"no_proposal\": true, \"reason\": \"...\"}.\n"
        "For a register_action candidate you MAY additionally supply the "
        "intended edit the registration would enable: "
        "\"precondition\": <one active predicate name>, "
        "\"params\": {<required operator parameter values>}. Both are "
        "re-validated against the closed vocabulary; if you omit them the "
        "entry is registered without an intended edit and can only be used "
        "by a later policy reflection."
    )
    payload = {
        "attribution": attribution.to_dict(),
        "facts": dict(facts_summary),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def select_registry_candidate(
    response: Mapping[str, Any],
    candidates: tuple[RegistryCandidate, ...],
    registry: VocabularyRegistry | None = None,
) -> tuple[RegistryCandidate | None, str]:
    """Select one candidate, carrying an optional intended edit.

    For a ``register_action`` candidate the response may supply
    ``precondition`` and ``params``; both are validated against the closed
    vocabulary (registry defaults to the baseline).  Fail-closed: an
    invalid precondition or parameter set drops the intent (the
    registration itself is unaffected), never silently stores a broken
    edit.
    """
    if not isinstance(response, Mapping) or not response:
        return None, "empty_response"
    if response.get("no_proposal"):
        return None, str(response.get("reason") or "no_proposal")
    index = response.get("candidate_index")
    if isinstance(index, int) and not isinstance(index, bool):
        if not 0 <= index < len(candidates):
            return None, "candidate_index_out_of_range"
        candidate = candidates[index]
        if candidate.operation != "register_action":
            return candidate, ""
        precondition = str(response.get("precondition") or "").strip()
        raw_params = response.get("params")
        params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        active = registry if registry is not None else L2_REGISTRY
        if precondition and precondition not in active.predicate_names():
            return candidate, f"intent_precondition_unknown:{precondition}"
        if params:
            operator_name = candidate.operator
            params_ok, reason = validate_policy_params(
                operator_name, params, registry=active,
            )
            if not params_ok:
                return candidate, f"intent_params_invalid:{reason}"
        return replace(candidate, precondition=precondition, params=params), ""
    return None, "missing_candidate_index"


# ============================================================
# Promotion (evidence-gated pending -> registered)
# ============================================================

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
    route_result: str,
) -> list[dict[str, Any]]:
    """Advance pending registry entries that have earned working promotion.

    Fail-closed: only ``no_registered_runtime_action`` route observations
    count (the only route that opens registrations), every counted case must
    be distinct, a predicate must fire on the current facts, an action
    instance must resolve to an executable operator, and the entry must
    still exist and be pending in the registry.  A promotion re-enables the
    entry (``registered``) and marks the queue entry validated; an entry
    that is no longer applicable is rejected.  Returns one audit row per
    considered entry.
    """
    audit: list[dict[str, Any]] = []
    if route_result != REGISTRATION_REASON_NO_ACTION:
        return audit
    for entry in list(genome.pending_queue):
        # Re-read the genome's registry each iteration: a promotion rebinds
        # ``genome.registry``, and the next candidate must be evaluated
        # against the promoted state or the second enable would be based on
        # a stale registry (its audit and content hash would silently drop
        # the first promotion).
        registry = genome.registry or L2_REGISTRY
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
                reason_codes=(route_result,),
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
    A full genome surfaces no candidate at all: an add_item the capacity
    gate could only reject must never reach the reflection prompt, so the
    capacity check lives here at proposal time instead of being paid for
    with a replayed-and-rejected reflection round.
    """
    if genome.total_items >= POLICY_GENOME_CAPACITY:
        return ()
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
