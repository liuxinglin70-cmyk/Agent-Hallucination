"""Capability planning: does the harness hold what this repair needs?

``worksheet.classify_family_actionability`` answers "which treatment routes
exist for this family". That is an enumeration of the action space, and it is
necessary but not sufficient: a route can exist, be reachable, fire, and still
be unable to repair anything, because the runtime cannot read the value the
repair has to compare, or because the repair is a semantic judgment the
deterministic operators cannot express, or because the only route to it is
prose.

Attempt 5 is the recorded case: a tracer directive was generated, the hook
fired, the reasoning changed, and the responsible step stayed wrong. Nothing
about the route's *existence* predicted that.

So this module asks the three separate questions the plan distinguishes:

    Generatable  ->  Triggerable  ->  Repair-capable

Only the third decides actionability, and it is answered against the real
runtime: the typed field vocabulary (``detector.predicate_dsl.FIELD_TYPES``),
which hook can read which field (``detector.predicate_context``), the closed
guard vocabulary, the closed hook vocabulary, and the route-falsification
memory (``evolution.route_failures``).

A mechanism whose repair is out of reach becomes a recorded capability gap with
a named missing capability, not a silently empty proposal batch.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from detector.interventions import GUARD_ACTIONS, HOOKS, INTERVENTION_TYPES
from detector.predicate_dsl import FIELD_TYPES, MAX_CONDITIONS
from detector.predicate_context import HOOK_CONTEXT_FIELDS

from evolution.mechanisms import repair_requirements
from evolution.route_failures import TREATMENT_ROUTES, route_failure_state

CAPABILITY_SCHEMA = "evolution.capability_requirement.v1"

# The plan's taxonomy (§5.1). ``prompt_only`` and ``already_supported`` are
# readable verdicts rather than gaps: the first says the only thing available
# is a prose instruction (and says so, instead of pretending it is a mechanism),
# the second says a route exists that has not been falsified.
CAPABILITY_KINDS = (
    "information_missing",
    "representation_missing",
    "semantic_judgment_missing",
    "control_flow_missing",
    "executor_missing",
    "localization_attribution_missing",
    "prompt_only",
    "already_supported",
)

CLASSIFICATIONS = ("actionable", "capability_gap", "research_only")

# Everything the runtime can currently do, in the capability vocabulary the
# analyses below use. Kept next to the runtime constants it mirrors so a
# divergence shows up in one place.
CAPABILITY_SURFACE = {
    "observation": (
        "evidence_check + inline typed-DSL program: a deterministic comparison "
        "over typed fields, conjoined to at most "
        f"{MAX_CONDITIONS} conditions"
    ),
    "guard": (
        "decision_guard: one of the closed guard actions "
        f"{sorted(GUARD_ACTIONS)}"
    ),
    "control_flow": "recall_trigger: forces the existing recall net for a case",
    "prompt": "text_boundary: a prose directive injected into an LLM stage",
}

# Where each treatment route acts, and which capability it therefore provides.
ROUTE_CAPABILITY = {
    "recall_directive": ("prompt", ("scanner.system",)),
    "recall_trigger": ("control_flow", ("recall.trigger",)),
    "upgrade_directive": ("prompt", ("upgrade.system",)),
    "judge_directive": ("prompt", ("judge.system",)),
    "tracer_directive": ("prompt", ("tracer.system",)),
    "defense_directive": ("prompt", ("defense.system",)),
    "defense_requirement": ("guard", ("adjudicate.guard",)),
    "restore_guard": ("guard", ("adjudicate.guard",)),
    "withhold_guard": ("guard", ("adjudicate.guard",)),
    "mechanical_conviction": ("guard", ("judge.post", "adjudicate.guard")),
    "evolved_predicate": ("observation", ("judge.post", "adjudicate.guard")),
}

# The fields each repair operation needs to read are owned by
# ``evolution.mechanisms`` and reached through ``repair_requirements``: one
# table, one owner, so the repair vocabulary cannot diverge between the module
# that names an operation and the module that checks it is reachable.

# What kind of missing capability to report when the operation's inputs cannot
# be read anywhere. ``localization_attribution_missing`` is separate because
# the value is not missing from the trace: the runtime simply has no operator
# that can constrain which step an LLM names.
_OPERATION_GAP_KIND = {
    "route_recall": "control_flow_missing",
    "observe_deviation": "representation_missing",
    "repair_causal_upgrade": "representation_missing",
    "repair_defense_boundary": "semantic_judgment_missing",
    "withhold_conviction": "executor_missing",
    "tighten_recall_flow": "control_flow_missing",
    "tighten_predicate": "representation_missing",
    "constrain_localization": "localization_attribution_missing",
    "repair_localization_source": "localization_attribution_missing",
    "unavailable": "executor_missing",
}


def capability_surface_report() -> dict[str, Any]:
    """What the runtime can currently express, for a planner or a report."""
    return {
        "schema_version": CAPABILITY_SCHEMA,
        "capabilities": dict(CAPABILITY_SURFACE),
        "hooks": sorted(HOOKS),
        "intervention_types": sorted(INTERVENTION_TYPES),
        "guard_actions": sorted(GUARD_ACTIONS),
        "treatment_routes": sorted(TREATMENT_ROUTES),
        "typed_field_count": len(FIELD_TYPES),
        "max_conditions_per_rule": MAX_CONDITIONS,
        "hook_reads_context_fields": {
            hook: sorted(fields)
            for hook, fields in sorted(HOOK_CONTEXT_FIELDS.items())
        },
    }


def _fields_readable_by(capability: str, hooks: Sequence[str]) -> set[str]:
    """Typed fields one capability can read when acting through these hooks.

    A prompt capability reads nothing: a text boundary fires on packet features
    and guard facts and its body is prose, so no typed field reaches it. That
    is the honest answer, and it is what makes a semantic repair report
    ``semantic_judgment_missing`` instead of looking like an observation.
    """
    if capability != "observation":
        return set()
    readable: set[str] = set()
    for hook in hooks:
        readable |= {
            field for field in FIELD_TYPES
            if field.startswith("context_")
            and field in HOOK_CONTEXT_FIELDS.get(hook, frozenset())
        }
        # Non-context fields come from the packet projection and are available
        # wherever the program can be attached.
        readable |= {field for field in FIELD_TYPES if field in _PACKET_FIELDS}
    return readable


def _packet_fields() -> frozenset[str]:
    return frozenset(
        field for field in FIELD_TYPES if not field.startswith("context_")
    )


_PACKET_FIELDS = _packet_fields()

# Fields whose value is prose written by the model. A repair that must compare
# two of these cannot be a deterministic equality test, however it is attached.
_SEMANTIC_FIELDS = frozenset({
    "question_text", "last_prose_text", "prose_texts", "event_texts",
    "interaction_texts", "context_quote", "context_contract_requirements",
    "context_judge_hallucination_reason", "tool_observations",
})

# Whether a deviation can be decided mechanically is a fact about the
# obligation, not about the plumbing: it depends on whether the runtime already
# holds a predicate that reads this obligation. ``exact_value``, ``exact_form``
# and ``required_channel`` have no such predicate -- ``detector.predicates``
# deliberately reports ``delivered_anchor_consistency == "insufficient"`` rather
# than convicting on a value mismatch, and it cannot mechanically detect a
# channel requirement at all. An observation route cannot express a relation the
# operator set cannot decide, however it is attached.
_MECHANICAL_OBLIGATIONS = frozenset({
    "required_action",
    "verbatim_content",
    "completion_claim",
})

# Operations whose required relation is a semantic judgment by nature: they ask
# whether a claim is supported by what was observed, which literal containment
# cannot settle.
_INHERENTLY_SEMANTIC_OPERATIONS = frozenset({
    "repair_defense_boundary",
})

# Operations that are semantic only when the obligation is undecidable.
_OBLIGATION_DEPENDENT_OPERATIONS = frozenset({
    "observe_deviation",
    "repair_causal_upgrade",
    "tighten_predicate",
})


def _route_status(
    route: str,
    *,
    operation: str,
    required: tuple[str, ...],
    required_capabilities: tuple[str, ...],
    semantic_relation: bool,
    falsified: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Whether one route can reach and use what the operation needs.

    Three separate questions, and all three must hold:

    * **fit** -- the capability the route provides is one the operation needs.
      An observation cannot start a recall net that never ran: it reports, it
      does not control flow. Reading the right fields is not the same as being
      able to act on them.
    * **reach** -- an observation must be able to read the values it compares,
      at the hook it is attached to.
    * **express** -- an observation cannot decide a semantic relation. The
      typed operator set tests literal containment; it cannot settle whether two
      values mean the same thing. Routes that engage an LLM (control flow,
      prompt) are not limited this way, which is exactly why they are a
      different capability level rather than a weaker one.
    """
    capability, hooks = ROUTE_CAPABILITY.get(route, ("", ()))
    missing_fields = [
        field for field in required if field not in FIELD_TYPES
    ]
    readable = _fields_readable_by(capability, hooks)
    unreadable = [
        field for field in required
        if field in FIELD_TYPES and field not in readable
    ]
    semantic = [
        field for field in required if field in _SEMANTIC_FIELDS
    ]
    falsification = falsified.get(route) or {}
    reasons: list[str] = []
    if capability == "":
        reasons.append(f"route {route!r} is not in the capability map")
    elif capability not in required_capabilities:
        reasons.append(
            f"route {route!r} provides {capability!r}, but the repair needs "
            f"{sorted(required_capabilities)}; {capability} cannot act on this "
            "failure"
        )
    if missing_fields:
        reasons.append(
            "required values have no typed field: " + ", ".join(missing_fields)
        )
    if unreadable and capability == "observation":
        reasons.append(
            f"{capability} at {list(hooks)} cannot read: " + ", ".join(unreadable)
        )
    if falsification.get("insufficient"):
        reasons.append(
            "route previously fired and changed nothing on this mechanism "
            f"({falsification.get('basis')}, "
            f"{falsification.get('observations')} observation(s))"
        )
    relation_is_semantic = capability == "observation" and semantic_relation
    return {
        "route": route,
        "capability": capability,
        "hooks": list(hooks),
        "capability_fits": (
            capability in required_capabilities if capability else False
        ),
        "readable_fields": sorted(readable),
        "unreadable_fields": sorted(unreadable),
        "semantic_fields": sorted(semantic),
        "semantic_relation": relation_is_semantic,
        "falsification": dict(falsification),
        "sufficient": not reasons and not relation_is_semantic,
        "reasons": reasons,
    }


def analyze_capability_requirement(
    mechanism: Mapping[str, Any],
    *,
    routes: Sequence[str] = (),
    route_failure_records: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Whether the harness can repair one mechanism, and if not, what is missing.

    ``routes`` are the treatment routes the worksheet enumerated for the family
    (``classify_family_actionability``); they are treated as *candidates*, not
    as capabilities. ``route_failure_records`` is the falsification memory:
    a route that already fired on this mechanism without moving the outcome is
    not evidence that the mechanism is repairable.
    """
    mechanism_id = str(mechanism.get("mechanism_id") or "")
    requirement = repair_requirements(mechanism)
    operation = str(requirement.get("operation") or "unavailable")
    required = tuple(requirement.get("required_inputs") or ())
    required_capabilities = tuple(
        requirement.get("required_capabilities") or ()
    )
    semantic_relation = bool(requirement.get("semantic_relation"))
    state = route_failure_state(list(route_failure_records))
    falsified = {
        route: entry for (mid, route), entry in state.items()
        if mid == mechanism_id
    }
    candidates = [route for route in routes if route in ROUTE_CAPABILITY]
    statuses = [
        _route_status(
            route,
            operation=operation,
            required=required,
            required_capabilities=required_capabilities,
            semantic_relation=semantic_relation,
            falsified=falsified,
        )
        for route in sorted(set(candidates))
    ]
    sufficient = [status for status in statuses if status["sufficient"]]
    insufficient = [status for status in statuses if not status["sufficient"]]
    declared_missing = [
        field for field in required if field not in FIELD_TYPES
    ]
    evidence: list[str] = []
    if not candidates:
        evidence.append(
            "no enumerated treatment route maps to a runtime capability"
        )
    for status in insufficient:
        for reason in status["reasons"]:
            evidence.append(f"{status['route']}: {reason}")
        if status["semantic_relation"]:
            evidence.append(
                f"{status['route']}: operation {operation!r} needs a semantic "
                "judgment the deterministic operator set cannot express"
            )
    if declared_missing:
        evidence.append(
            "repair declares values with no typed field: "
            + ", ".join(sorted(set(declared_missing)))
        )
    semantic_only = bool(insufficient) and all(
        status["semantic_relation"] for status in insufficient
    )
    fit_missing = bool(insufficient) and not any(
        status["capability_fits"] for status in insufficient
    )
    if sufficient:
        classification = "actionable"
        missing = ""
        if all(status["capability"] == "prompt" for status in sufficient):
            # Reachable, but only by prose. Reported as its own kind because a
            # prose route is a real and different capability level: it cannot
            # be shown to compare anything.
            missing = "prompt_only"
    elif insufficient and fit_missing:
        # Nothing on offer can act on this failure at all. Named after the
        # capability the operation needs rather than after the route that
        # happened to be enumerated.
        classification = "capability_gap"
        missing = _OPERATION_GAP_KIND.get(operation, "executor_missing")
    elif insufficient:
        # Every enumerated route is either unusable or already falsified. When
        # the reason is a semantic relation, that is the capability to name.
        classification = "research_only" if not semantic_only else "capability_gap"
        missing = (
            "semantic_judgment_missing" if semantic_only
            else "already_supported_but_insufficient"
        )
    else:
        classification = "capability_gap"
        missing = _OPERATION_GAP_KIND.get(operation, "executor_missing")
    if classification == "capability_gap" and not evidence:
        evidence.append(
            f"operation {operation!r} needs a capability the runtime lacks "
            f"({missing})"
        )
    return {
        "schema_version": CAPABILITY_SCHEMA,
        "mechanism_id": mechanism_id,
        "classification": classification,
        "required_capability": missing,
        "repair_operation": operation,
        "required_inputs": list(required),
        "current_routes": sorted(status["route"] for status in statuses),
        "sufficient_routes": [status["route"] for status in sufficient],
        "insufficient_routes": [
            {
                "route": status["route"],
                "capability": status["capability"],
                "capability_fits": status["capability_fits"],
                "reasons": status["reasons"],
                "falsification": status["falsification"],
            }
            for status in insufficient
        ],
        "route_statuses": statuses,
        "evidence": evidence,
    }


def capability_gap_record(
    requirement: Mapping[str, Any],
    *,
    failure_family: str,
    count: int = 0,
    sources: Sequence[str] = (),
) -> dict[str, Any]:
    """A capability requirement as a backlog record.

    Shaped to feed ``evolution.gaps.record_gaps``: the classification is the
    reason taxonomy, so the store's identity distinguishes "no route" from
    "route exists but is insufficient" instead of collapsing both into one
    standing entry.
    """
    return {
        "failure_family": str(failure_family or ""),
        "classification": str(
            requirement.get("required_capability")
            or requirement.get("classification")
            or "capability_gap"
        ),
        "reason": "; ".join(requirement.get("evidence") or ()) or (
            f"repair operation {requirement.get('repair_operation')!r} is not "
            "reachable with the current capability surface"
        ),
        "count": int(count),
        "sources": sorted({str(item) for item in sources}),
        "routes": list(requirement.get("current_routes") or ()),
        "mechanism_id": str(requirement.get("mechanism_id") or ""),
    }


def plan_capabilities(
    families: Sequence[Mapping[str, Any]],
    *,
    route_failure_records: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Plan every family's mechanism in one pass.

    Returns the per-mechanism requirements plus the gap records they imply, so
    the caller does not have to re-derive which families are gaps: the planner
    decides that, and ``worksheet`` route enumeration stays the input.
    """
    requirements: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    actionable: list[str] = []
    for family in families:
        mechanism = family.get("mechanism")
        if not isinstance(mechanism, Mapping):
            continue
        routes = list((family.get("actionability") or {}).get("routes") or ())
        requirement = analyze_capability_requirement(
            mechanism,
            routes=routes,
            route_failure_records=route_failure_records,
        )
        requirements.append(requirement)
        if requirement["classification"] == "actionable":
            actionable.append(str(requirement["mechanism_id"]))
            continue
        gaps.append(capability_gap_record(
            requirement,
            failure_family=str(family.get("failure_family") or ""),
            count=int(family.get("count") or 0),
            sources=family.get("sources") or (),
        ))
    return {
        "schema_version": CAPABILITY_SCHEMA,
        "requirements": requirements,
        "actionable_mechanisms": actionable,
        "gaps": gaps,
        "capability_surface": capability_surface_report(),
    }
