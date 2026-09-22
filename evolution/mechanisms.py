"""Task-level failure mechanisms, separated from detector execution paths.

``worksheet._mechanism_signature`` answers "where in the detection flow did the
verdict go wrong". That is a property of the pipeline, not of the error: two
cases can share a byte-identical execution path and still need different
repairs (an omitted tool action is not repaired by comparing literal spans),
and two cases can arrive from different sources and need exactly the same one.

This module derives the second object -- what the task obliged, what the agent
delivered against it, which stage failed to react, and which repair operation
could close the gap -- so that a proposer receives a mechanism instead of a
path.

Identity discipline (plan §4.2). A mechanism key is built only from:

* the objective (detection / localization),
* the task obligation kind,
* the observed deviation kind,
* the failing stage and its failure mode,
* the repair operation.

It never reads ``row["case"]``, ``row["gold"]``, a gold step, a source name or
any literal drawn from a trajectory, so renaming a case or a source cannot move
a case between mechanisms. ``evolution/tests/test_mechanisms.py`` pins each of
those properties.

Evidence is bound by field and role rather than by event id: an event id is
case-specific, and carrying one in the record would make the record -- and any
key derived from it -- unstable across exactly the cases a mechanism is meant
to unify.

All readers here consume the label-free member summaries that
``worksheet.diagnose_failure_families`` already computes (judge contract kinds,
stage states, mechanical predicate verdicts). ``positive_source`` is absent
from those summaries, so a false conviction is attributed from the pair
(judge positive, upgrade positive): a conviction with neither must have come
through the mechanical guard route, which is the only remaining positive
source in the terminal vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

MECHANISM_SCHEMA = "evolution.failure_mechanism.v1"

# Task obligations. The first five are the judge's closed contract vocabulary
# (``detector.semantics.CONTRACT_KINDS``); ``completion_claim`` is the
# structural reading of ``final_delivery_present == missing``, and
# ``unspecified`` is the honest answer when neither reading is available.
OBLIGATION_KINDS = (
    "completion_claim",
    "required_action",
    "verbatim_content",
    "exact_value",
    "exact_form",
    "required_channel",
    "unspecified",
)

# What the agent delivered against that obligation. ``none`` is a reading, not
# a missing value: a false conviction has no task-level deviation at all, and
# saying so is what keeps its repair aimed at the convicting stage rather than
# at the agent's delivery.
DEVIATION_KINDS = (
    "false_completion",
    "omission",
    "mismatch",
    "unsupported_claim",
    "mislocation",
    "missing_step",
    "none",
    "unclassified",
)

# Obligation -> deviation. The obligation says what was required, the deviation
# says how the delivery departed from it; deriving the second from the first
# keeps the two readings from contradicting each other.
_DEVIATION_BY_OBLIGATION = {
    "completion_claim": "false_completion",
    "required_action": "omission",
    "verbatim_content": "mismatch",
    "exact_value": "mismatch",
    "exact_form": "mismatch",
    "required_channel": "mismatch",
}

OBJECTIVES = ("detection", "localization")

# The stage names a mechanism's ``detector_failure.stage`` may take. These are
# the runtime ``stage_status`` keys -- ``recall_scanner``, not ``recall`` --
# because the hook dependency graph (``evolution.backends``) is keyed the same
# way, and a mechanism whose stage name does not match that vocabulary cannot
# be checked for reachability at all. An earlier revision used the shorter
# ``recall``, which made every recall mechanism look unreachable to L0.
FAILURE_STAGES = (
    "judge",
    "recall_scanner",
    "causal_upgrade",
    "step_tracer",
    "defense",
    "adjudication",
)

# Repair operations the runtime can currently express. ``unavailable`` is
# spelled out rather than left as ``None``: a mechanism whose operation is
# absent is a capability gap (plan §5), and a gap must be a readable value, not
# a missing one.
REPAIR_OPERATIONS = (
    "route_recall",
    "observe_deviation",
    "repair_causal_upgrade",
    "repair_defense_boundary",
    "withhold_conviction",
    "tighten_recall_flow",
    "tighten_predicate",
    "constrain_localization",
    "repair_localization_source",
    "unavailable",
)

# (failure stage, failure mode) -> repair operation. A table rather than a
# branch: two mechanisms are separated by a row here, and the row can be read
# against the route vocabulary in one screen.
_OPERATION_BY_FAILURE = {
    ("recall_scanner", "not_reached"): "route_recall",
    ("causal_upgrade", "no_causal_link"): "observe_deviation",
    ("causal_upgrade", "missing_rows"): "repair_causal_upgrade",
    ("causal_upgrade", "false_upgrade"): "tighten_recall_flow",
    ("defense", "refuted_judge"): "repair_defense_boundary",
    ("defense", "refuted_upgrade"): "repair_defense_boundary",
    ("judge", "negative_verdict"): "observe_deviation",
    ("judge", "mechanical_false_positive"): "tighten_predicate",
    ("adjudication", "false_conviction"): "withhold_conviction",
    ("step_tracer", "unhostable_step"): "constrain_localization",
}

# The coarse family prefix encodes the same failure reading, derived from the
# same label-free fields (``worksheet._coarse_failure_family``). Reusing it
# when no member summary is available keeps one opinion in the system instead
# of a second one that could drift from the first. Order matters: the first
# matching prefix wins.
_FAILURE_BY_FAMILY_PREFIX = (
    ("FAM-fn-upgrade_missing_rows", ("causal_upgrade", "missing_rows")),
    ("FAM-fn-defense_refuted_recall_upgrade", ("defense", "refuted_upgrade")),
    ("FAM-fn-defense_refuted_judge", ("defense", "refuted_judge")),
    ("FAM-fn-judge_false_no_recall_candidates", ("recall_scanner", "not_reached")),
    ("FAM-fn-judge_false_upgrade_no_upgrade",
     ("causal_upgrade", "no_causal_link")),
    ("FAM-fp-recall_upgrade_conviction", ("causal_upgrade", "false_upgrade")),
    ("FAM-fp-judge_conviction", ("adjudication", "false_conviction")),
    ("FAM-loc-", ("step_tracer", "unhostable_step")),
)

# Static descriptor text. Every string is a vocabulary token, never a literal
# taken from a trajectory, so the same mechanism reads identically across the
# cases it groups.
_OBLIGATION_TEXT = {
    "completion_claim": (
        "final_delivery",
        "reported_completion_is_supported_by_a_delivery_anchor",
    ),
    "required_action": ("tool_action", "the_required_action_was_performed"),
    "verbatim_content": (
        "delivered_text", "the_required_span_is_delivered_verbatim",
    ),
    "exact_value": (
        "delivered_value", "the_delivered_value_equals_the_required_one",
    ),
    "exact_form": (
        "delivered_form", "the_delivered_form_matches_the_required_one",
    ),
    "required_channel": (
        "delivery_channel", "the_required_channel_was_used",
    ),
    "unspecified": ("task_contract", "unclassified_obligation"),
}

_DEVIATION_TEXT = {
    "false_completion": (
        "completion_reported_without_a_delivery_anchor",
        "supported_completion",
    ),
    "omission": ("required_action_absent", "required_action_present"),
    "mismatch": (
        "delivered_content_deviates_from_the_required_one", "exact_delivery",
    ),
    "unsupported_claim": (
        "claim_without_resolving_evidence", "claim_backed_by_evidence",
    ),
    "mislocation": (
        "named_step_cannot_host_the_error",
        "responsible_step_can_host_the_error",
    ),
    "missing_step": (
        "no_responsible_step_named", "responsible_step_named",
    ),
    "none": ("no_task_level_deviation", "no_task_level_deviation"),
    "unclassified": ("unclassified_deviation", "unclassified_deviation"),
}

# What a repair of this class must be able to read, and which capability it
# needs. P0.2 consumes this; keeping it next to the operation table means the
# two cannot drift apart.
#
# Names are the runtime vocabulary, not prose: ``required_inputs`` are
# ``detector.predicate_dsl.FIELD_TYPES`` entries (the only values a deterministic
# observation can actually compare) and ``required_capabilities`` are the keys
# of ``evolution.capability_planning.CAPABILITY_SURFACE``. A planner that has to
# map a requirement onto the runtime needs names it can look up, and a prose
# phrase like "expected literal span" cannot be looked up.
_REPAIR_DETAILS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "route_recall": (
        ("question_text", "tool_names", "packet_features"),
        ("control_flow",),
    ),
    "observe_deviation": (
        (
            "question_text", "tool_arguments", "tool_observations",
            "prose_texts", "last_answer_tool_arguments",
        ),
        ("observation",),
    ),
    "repair_causal_upgrade": (
        ("tool_arguments", "tool_observations", "event_texts"),
        ("observation", "prompt"),
    ),
    "repair_defense_boundary": (
        ("context_quote", "context_evidence_refs", "tool_observations"),
        ("guard", "prompt"),
    ),
    "withhold_conviction": (
        (
            "context_judge_positive", "context_judge_confidence",
            "last_answer_tool_observation",
        ),
        ("guard",),
    ),
    "tighten_recall_flow": (
        ("question_text", "tool_names"),
        ("observation", "control_flow"),
    ),
    "tighten_predicate": (
        ("tool_arguments", "tool_observations", "last_answer_tool_arguments"),
        ("observation",),
    ),
    "constrain_localization": (
        ("event_texts", "tool_arguments", "prose_texts"),
        ("localization_operator", "prompt"),
    ),
    "repair_localization_source": (
        ("event_texts", "prose_texts"),
        ("localization_operator", "observation"),
    ),
    "unavailable": ((), ()),
}

# Obligation-specific inputs for one operation, when the generic list would
# overstate what the repair needs. ``observe_deviation`` on a
# ``completion_claim`` asks whether a delivery anchor exists -- a structural
# question about packet shape -- while on ``exact_value`` it asks whether two
# values agree, which the runtime cannot decide.
_OPERATION_INPUTS_BY_OBLIGATION: dict[tuple[str, str], tuple[str, ...]] = {
    ("observe_deviation", "completion_claim"): (
        "prose_texts", "last_prose_text", "answer_anchor_status",
        "tool_names", "tool_arguments",
    ),
    ("observe_deviation", "required_action"): (
        "question_text", "tool_names", "tool_arguments",
    ),
}

# Obligations whose deviation a deterministic observation can decide with the
# current operator set. Everything else needs a semantic judgment: the runtime
# can test literal containment, but it cannot decide that two values are equal
# after normalization, nor that a claim is supported by an observation.
_MECHANICAL_OBLIGATIONS = frozenset({
    "required_action",
    "verbatim_content",
    "completion_claim",
})

_OBLIGATION_DEPENDENT_OPERATIONS = frozenset({
    "observe_deviation",
    "repair_causal_upgrade",
    "tighten_predicate",
})

_INHERENTLY_SEMANTIC_OPERATIONS = frozenset({
    "repair_defense_boundary",
})

# Shortcuts a repair may never take. Carried in the record so a candidate
# drafted from it inherits the prohibition explicitly.
FORBIDDEN_SHORTCUTS = (
    "case_id",
    "gold_label",
    "gold_step",
    "dataset_annotation",
)

# Predicate verdicts that count as a violation reading. Mirrors
# ``worksheet._VIOLATION_VERDICTS``; kept local so this module stays a leaf.
_VIOLATION_VERDICTS = frozenset({
    "violated", "unresolved", "uncovered", "inconsistent", "missing",
})

# Obligation precedence when several readings apply to one member. Mechanical
# evidence outranks a declared contract because it is an observation about this
# case rather than a reading of the task; among observations the more specific
# structural reading wins.
_OBLIGATION_PRECEDENCE = (
    "completion_claim",
    "required_action",
    "verbatim_content",
    "exact_value",
    "exact_form",
    "required_channel",
)

_OBLIGATION_BY_PREDICATE = {
    "required_action_present": "required_action",
    "verbatim_fidelity": "verbatim_content",
    "final_delivery_present": "completion_claim",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def operation_for(stage: str, failure_mode: str) -> str:
    """The repair operation a (stage, mode) pair calls for.

    ``unavailable`` is returned for a pair outside the table: that is a real
    reading of the action space, not an error, and it is what makes a
    capability gap recordable instead of silently dropped.
    """
    return _OPERATION_BY_FAILURE.get((stage, failure_mode), "unavailable")


def repair_requirements(
    mechanism: Mapping[str, Any],
) -> dict[str, Any]:
    """What one mechanism's repair needs: operation, inputs, capabilities.

    The single accessor for the repair vocabulary, so a planner never has to
    reach into this module's tables and the two cannot drift apart. Inputs are
    runtime field names (``detector.predicate_dsl.FIELD_TYPES``) and
    capabilities are the runtime capability names
    (``evolution.capability_planning.CAPABILITY_SURFACE`` keys).
    """
    hypothesis = mechanism.get("repair_hypothesis") or {}
    operation = _norm(hypothesis.get("operation")) or "unavailable"
    obligation = _norm((mechanism.get("task_obligation") or {}).get("kind"))
    default_inputs, default_capabilities = _REPAIR_DETAILS.get(
        operation, ((), ()),
    )
    declared = tuple(
        str(field) for field in hypothesis.get("required_inputs") or ()
    )
    inputs = _OPERATION_INPUTS_BY_OBLIGATION.get(
        (operation, obligation), declared or default_inputs,
    )
    capabilities = tuple(
        str(item) for item in hypothesis.get("required_capabilities") or ()
    ) or default_capabilities
    semantic = operation in _INHERENTLY_SEMANTIC_OPERATIONS or (
        operation in _OBLIGATION_DEPENDENT_OPERATIONS
        and obligation not in _MECHANICAL_OBLIGATIONS
    )
    return {
        "operation": operation,
        "required_inputs": list(inputs),
        "required_capabilities": list(capabilities),
        "semantic_relation": semantic,
    }


def obligation_for(
    member: Mapping[str, Any] | None,
) -> tuple[str, tuple[str, ...]]:
    """The task obligation of one member, plus the fields it was read from.

    Mechanical violations outrank declared contract kinds: a predicate that
    fires on this case is evidence about this case, while a contract list is
    the judge's reading of the task text.
    """
    predicates = (member or {}).get("predicates") or {}
    bindings: list[str] = []
    kind = ""
    if _norm(predicates.get("final_delivery_present")) == "missing":
        kind = "completion_claim"
        bindings.append("predicate.final_delivery_present")
    if not kind:
        for predicate, obligation in (
            ("required_action_present", "required_action"),
            ("verbatim_fidelity", "verbatim_content"),
        ):
            if _norm(predicates.get(predicate)) in _VIOLATION_VERDICTS:
                kind = obligation
                bindings.append(f"predicate.{predicate}")
                break
    if not kind:
        declared = {
            _norm(item) for item in (member or {}).get("contracts") or ()
        }
        for obligation in _OBLIGATION_PRECEDENCE[1:]:
            if obligation in declared:
                kind = obligation
                bindings.append("judge.contracts")
                break
    if not kind and _norm(predicates.get("evidence_refs_resolve")) in (
        _VIOLATION_VERDICTS
    ):
        # No obligation reading, but the citation itself does not resolve:
        # that is a deviation of the unsupported-claim family.
        bindings.append("predicate.evidence_refs_resolve")
        return "unspecified", tuple(bindings)
    return (kind or "unspecified"), tuple(bindings or ["judge.contracts"])


def _deviation_for(
    obligation: str,
    *,
    objective: str,
    local_state: str,
    member: Mapping[str, Any] | None,
    no_task_deviation: bool,
) -> str:
    if objective == "localization":
        return "missing_step" if local_state == "step_missing" else "mislocation"
    if no_task_deviation:
        return "none"
    if obligation in _DEVIATION_BY_OBLIGATION:
        return _DEVIATION_BY_OBLIGATION[obligation]
    predicates = (member or {}).get("predicates") or {}
    if _norm(predicates.get("evidence_refs_resolve")) in _VIOLATION_VERDICTS:
        return "unsupported_claim"
    return "unclassified"


def _failure_from_member(
    member: Mapping[str, Any],
    *,
    objective: str,
    no_task_deviation: bool,
) -> tuple[str, str]:
    """The failing stage and mode, read from one member's stage summary.

    Order is the diagnosis order: a refuted defence explains the outcome
    before the recall net does, because the refutation is what discarded an
    otherwise positive finding.
    """
    if objective == "localization":
        if bool(member.get("upgrade_positive")):
            return "causal_upgrade", "unhostable_step"
        return "step_tracer", "unhostable_step"
    if no_task_deviation:
        # A false conviction. The positive source is not in the member
        # summary, but the remaining source is deducible: with neither a
        # positive judge nor a positive upgrade there is only the mechanical
        # guard route left in the terminal vocabulary.
        if not bool(member.get("judge_positive")) and not bool(
            member.get("upgrade_positive")
        ):
            return "judge", "mechanical_false_positive"
        if not bool(member.get("judge_positive")):
            return "causal_upgrade", "false_upgrade"
        return "adjudication", "false_conviction"
    defense = _norm(member.get("defense_verdict"))
    if defense == "refuted":
        if bool(member.get("upgrade_positive")):
            return "defense", "refuted_upgrade"
        if bool(member.get("judge_positive")):
            return "defense", "refuted_judge"
    recall_path = _norm(member.get("recall_path"))
    if recall_path in {"not_run", "not_needed"} and not bool(
        member.get("judge_positive")
    ):
        return "recall_scanner", "not_reached"
    if recall_path == "candidates" and not bool(member.get("upgrade_positive")):
        return "causal_upgrade", "no_causal_link"
    return "judge", "negative_verdict"


def _failure_from_path(
    family_id: str,
    *,
    objective: str,
    positive_source: str,
    upgrade_positive: bool,
) -> tuple[str, str]:
    """The failing stage and mode of a family with no member summaries.

    Falls back to the coarse family prefix and the path signature, both of
    which were derived from the same label-free fields.
    """
    for prefix, failure in _FAILURE_BY_FAMILY_PREFIX:
        if family_id.startswith(prefix):
            return failure
    if objective == "localization":
        return "step_tracer", "unhostable_step"
    if positive_source == "recall_upgrade" or (
        not positive_source and upgrade_positive
    ):
        return "causal_upgrade", "false_upgrade"
    return "judge", "negative_verdict"


def member_reading(
    member: Mapping[str, Any],
    *,
    family_id: str,
    objective: str,
    local_state: str = "mislocated",
) -> dict[str, Any]:
    """One member's mechanism reading, from its label-free summary.

    This is the object subgroups are grouped by: two members belong to the same
    mechanism exactly when their readings agree on obligation, deviation,
    failing stage and therefore repair operation.
    """
    obligation, bindings = obligation_for(member)
    # A detection family is a false-conviction family when the coarse id says
    # so; the member summary itself carries no gold, so the reading comes from
    # the family the member was grouped into.
    no_task_deviation = (
        objective == "detection" and family_id.startswith("FAM-fp-")
    )
    stage, mode = _failure_from_member(
        member,
        objective=objective,
        no_task_deviation=no_task_deviation,
    )
    return {
        "task_obligation_kind": obligation,
        "obligation_bindings": bindings,
        "deviation_kind": _deviation_for(
            obligation,
            objective=objective,
            local_state=local_state,
            member=member,
            no_task_deviation=no_task_deviation,
        ),
        "failure": (stage, mode),
        "local_state": local_state,
    }


def _repair_obligation(obligation: Any, deviation: Any) -> str:
    """The obligation as it participates in a repair.

    A false conviction has no task-level deviation, so the obligation cannot
    affect what repairs it and is normalised away. One rule, used by both the
    grouping signature and the unanimity reading, so a subgroup can never be
    split by a field that does not change its repair.
    """
    if _norm(deviation) == "none":
        return "not_applicable_to_repair"
    return _norm(obligation)


def reading_signature(reading: Mapping[str, Any]) -> tuple[str, ...]:
    """Hashable identity of a reading, including the operation it implies."""
    failure = reading.get("failure") or ("", "")
    stage, mode = str(failure[0]), str(failure[1])
    deviation = _norm(reading.get("deviation_kind"))
    return (
        _repair_obligation(reading.get("task_obligation_kind"), deviation),
        deviation,
        stage,
        mode,
        operation_for(stage, mode),
    )


def _readings(
    family_id: str,
    *,
    objective: str,
    members: Sequence[Mapping[str, Any]],
    path_signature: Mapping[str, Any] | None,
    local_states: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """The mechanism reading of each member, or one family-level reading."""
    if not members:
        # ``positive_source`` is the string ``"none"`` when the case was not
        # convicted at all -- that is a miss, not a false conviction, and the
        # two need opposite repairs. Reading the sentinel as a source would
        # label every fallback miss as "no deviation".
        positive_source = _norm((path_signature or {}).get("positive_source"))
        convicted = positive_source not in {"", "none"}
        return [{
            "task_obligation_kind": "unspecified",
            "obligation_bindings": ("judge.contracts",),
            "deviation_kind": (
                "mislocation" if objective == "localization"
                else "none" if convicted
                else "unclassified"
            ),
            "failure": _failure_from_path(
                family_id,
                objective=objective,
                positive_source=positive_source,
                upgrade_positive=bool(
                    (path_signature or {}).get("upgrade_positive")
                ),
            ),
        }]
    return [
        member_reading(
            member,
            family_id=family_id,
            objective=objective,
            local_state=(
                local_states[index]
                if index < len(local_states) else "mislocated"
            ),
        )
        for index, member in enumerate(members)
    ]


def _assemble(
    family_id: str,
    *,
    objective: str,
    reading: Mapping[str, Any],
    path_signature: Mapping[str, Any] | None,
) -> dict[str, Any]:
    obligation = _norm(reading.get("task_obligation_kind")) or "unspecified"
    deviation = _norm(reading.get("deviation_kind")) or "unclassified"
    stage, mode = reading.get("failure") or ("judge", "negative_verdict")
    operation = operation_for(stage, mode)
    required_inputs, required_capabilities = _REPAIR_DETAILS[operation]
    required_inputs = _OPERATION_INPUTS_BY_OBLIGATION.get(
        (operation, obligation), required_inputs,
    )
    subject, relation = _OBLIGATION_TEXT.get(
        obligation, _OBLIGATION_TEXT["unspecified"],
    )
    actual, expected = _DEVIATION_TEXT.get(
        deviation, _DEVIATION_TEXT["unclassified"],
    )
    bindings = [
        {"field": _norm(field), "role": "obligation"}
        for field in reading.get("obligation_bindings") or ()
    ]
    bindings.append({"field": "proof.stage_status", "role": "detector_failure"})
    if path_signature:
        bindings.append({
            "field": "proof.terminal_basis.decisive_facts",
            "role": "detector_failure",
        })
    return {
        "schema_version": MECHANISM_SCHEMA,
        "family_id": family_id,
        "objective": objective,
        "task_obligation": {
            "kind": obligation,
            "subject": subject,
            "expected_relation": relation,
        },
        "observed_deviation": {
            "kind": deviation,
            "actual": actual,
            "expected": expected,
        },
        "evidence_bindings": bindings,
        "detector_failure": {
            "stage": stage,
            "failure_mode": mode,
            "path_signature": dict(path_signature or {}),
        },
        "repair_hypothesis": {
            "operation": operation,
            "required_inputs": list(required_inputs),
            "required_capabilities": list(required_capabilities),
            "forbidden_shortcuts": list(FORBIDDEN_SHORTCUTS),
        },
    }


def infer_mechanism(
    family_id: str,
    *,
    target_kind: str = "detection",
    members: Sequence[Mapping[str, Any]] = (),
    path_signature: Mapping[str, Any] | None = None,
    local_states: Sequence[str] = (),
    member_cases: Sequence[str] = (),
    normal_neighbors: Sequence[str] = (),
    count: int | None = None,
) -> dict[str, Any]:
    """The mechanism of one family, or of one subgroup of it.

    ``members`` are the label-free member summaries produced by
    ``worksheet.diagnose_failure_families``. Without them the reading degrades
    to the coarse family prefix plus the path signature, which is reported as
    ``confidence_basis == "path_only"`` rather than presented as a diagnosis.
    """
    objective = (
        "localization"
        if target_kind == "localization" or family_id.startswith("FAM-loc-")
        else "detection"
    )
    readings = _readings(
        family_id,
        objective=objective,
        members=members,
        path_signature=path_signature,
        local_states=local_states,
    )
    signatures = [reading_signature(item) for item in readings]
    unique = sorted(set(signatures))
    chosen = signatures[0]
    mechanism = _assemble(
        family_id,
        objective=objective,
        reading=readings[0],
        path_signature=path_signature,
    )
    mechanism["mechanism_id"] = mechanism_key(mechanism)
    mechanism["member_cases"] = [str(case) for case in member_cases]
    mechanism["normal_neighbors"] = [str(case) for case in normal_neighbors]
    total = int(count if count is not None else len(members))
    unanimous = len(unique) == 1
    if not members:
        status, basis = "hypothesis", "path_only"
    elif total <= 1:
        status, basis = "single_case_research_only", "structural"
    elif unanimous:
        status, basis = "confirmed", "multi_case"
    else:
        status, basis = "ambiguous", "mixed"
    mechanism["status"] = status
    mechanism["confidence_basis"] = basis
    mechanism["reading_count"] = len(readings)
    mechanism["reading_unanimous"] = unanimous
    if not unanimous:
        mechanism["observed_readings"] = [list(item) for item in unique]
    # ``chosen`` is the reading the identity was minted from; keeping it out of
    # the record's public fields but reachable makes the grouping auditable
    # without inventing a second copy of the obligation/deviation pair.
    mechanism["signature"] = list(chosen)
    return mechanism


def repair_operation_signature(mechanism: Mapping[str, Any]) -> tuple[str, ...]:
    """The grouping key of a mechanism: what would have to change to repair it.

    Deliberately excludes the case set, the source, the family id and the
    detector path. A shared path is neither necessary nor sufficient for two
    cases to belong to one mechanism.

    The task obligation participates only when the *delivery* is what deviated.
    A false conviction (``deviation == "none"``) has no task-level deviation at
    all -- nothing the agent delivered is wrong -- so its repair is entirely on
    the detector's side and is the same whatever the task asked for. Keeping the
    obligation in the key there split one mechanism into as many pieces as
    there were contract kinds; the dry-run over archived rows showed exactly
    that, with two false convictions separated only by ``exact_value`` versus
    ``required_action`` while both needed the conviction withheld.
    """
    failure = mechanism.get("detector_failure") or {}
    deviation = _norm(
        (mechanism.get("observed_deviation") or {}).get("kind")
    )
    return (
        _norm(mechanism.get("objective")),
        _repair_obligation(
            (mechanism.get("task_obligation") or {}).get("kind"), deviation,
        ),
        deviation,
        _norm(failure.get("stage")),
        _norm(failure.get("failure_mode")),
        _norm((mechanism.get("repair_hypothesis") or {}).get("operation")),
    )


def mechanism_key(mechanism: Mapping[str, Any]) -> str:
    """Stable id of a mechanism: ``M-`` plus the hash of its repair signature."""
    payload = json.dumps(
        repair_operation_signature(mechanism),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"M-{_sha(payload)}"


def mechanism_compatible(
    left: Mapping[str, Any], right: Mapping[str, Any],
) -> bool:
    """Whether two mechanisms are the same one.

    Equality of the repair signature. Membership is therefore decided by what
    would repair the case, not by which stages happened to run.
    """
    return repair_operation_signature(left) == repair_operation_signature(right)


def mechanism_prompt_view(
    mechanism: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Proposer-facing rendering of a mechanism.

    ``member_cases`` and ``normal_neighbors`` are dropped here and not merely
    because they are long: they are case identifiers, and this is the one
    function whose output is allowed to reach proposer prose.
    """
    if not mechanism:
        return None
    failure = mechanism.get("detector_failure") or {}
    return {
        "mechanism_id": mechanism.get("mechanism_id"),
        "family_id": mechanism.get("family_id"),
        "objective": mechanism.get("objective"),
        "status": mechanism.get("status"),
        "confidence_basis": mechanism.get("confidence_basis"),
        "task_obligation": mechanism.get("task_obligation"),
        "observed_deviation": mechanism.get("observed_deviation"),
        "detector_failure": {
            "stage": failure.get("stage"),
            "failure_mode": failure.get("failure_mode"),
        },
        "repair_hypothesis": mechanism.get("repair_hypothesis"),
        "member_count": len(mechanism.get("member_cases") or ()),
    }


__all__ = [
    "DEVIATION_KINDS",
    "FORBIDDEN_SHORTCUTS",
    "MECHANISM_SCHEMA",
    "OBJECTIVES",
    "OBLIGATION_KINDS",
    "REPAIR_OPERATIONS",
    "infer_mechanism",
    "mechanism_compatible",
    "mechanism_key",
    "mechanism_prompt_view",
    "member_reading",
    "obligation_for",
    "operation_for",
    "reading_signature",
    "repair_operation_signature",
]
