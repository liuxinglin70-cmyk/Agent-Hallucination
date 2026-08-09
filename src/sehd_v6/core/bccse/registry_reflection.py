"""SEHD v6 — L2 vocabulary registration planner (P3B online evolution).

When the deterministic failure classifier attributes a failure to the L2
layer but no registered runtime action can repair it
(``no_registered_runtime_action``), the vocabulary itself may be extended.
This module owns the constrained registration path:

- ``plan_registry_candidates`` enumerates deterministic, precomputed
  registration templates (a new predicate over the facts whitelist, or a new
  action instance bound to an existing court-local operator). The model may
  only select one candidate or abstain — it never composes free DSL text or
  new operator semantics (§9.5: no free-text adjudication logic).
- ``compile_registration_candidate`` shadow-compiles a selected candidate
  into a ``RegistryEdit``-ready payload, failing closed on any whitelist or
  schema violation so an invalid registration is never applied.
- ``build_registry_selection_prompt`` / ``select_registry_candidate`` mirror
  the policy-selection contract: the model ranks precomputed candidates by
  index, keeping runtime semantics owned by deterministic code.

Newly registered entries are always ``pending`` (shadow-only) on first
registration and never enter the active evaluator until a later audited
promotion, per the §9.6 first-registration state rule.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .v6_contracts import FailureAttribution, validate_policy_params
from .vocabulary_registry import (
    L2_REGISTRY,
    POLICY_STAGE_POST_LEDGER,
    RegistryDslError,
    VocabularyRegistry,
    compile_predicate_dsl,
)

# Prompt-facing purpose for the registration reflection call. The runner
# routes it through the mutation route under its own cache namespace so
# registration calls are counted and replayed independently of policy
# reflection.
PURPOSE_REGISTRY_REFLECTION = "registry_reflection"

# Closed registration reasons that can open the vocabulary planner.
REGISTRATION_REASON_NO_ACTION = "no_registered_runtime_action"


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
) -> tuple[RegistryCandidate, ...]:
    """Enumerate deterministic registration candidates for one attribution.

    Only an L2 attribution with no registered runtime action opens the
    vocabulary planner. Candidates are confined to the registry's existing
    court-local operators (for actions) and the facts whitelist (for
    predicates); the model cannot invent operator semantics or reach outside
    the addressable facts.
    """
    if attribution.reason_code != REGISTRATION_REASON_NO_ACTION:
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
