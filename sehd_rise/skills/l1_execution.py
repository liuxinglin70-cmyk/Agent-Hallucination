"""Deterministic post-court executor for L1 Skill actions.

The L1 Skill layer has exactly seven executable actions left after the
generative opcodes were removed. Their semantics are monotonic suppress/select
transforms over the proof court's already-computed result — the same shape the
L2 policy evaluator uses (``detection.policy.evaluate_policies``). This module
is that deterministic executor: it consumes one court result plus a compiled
``ActionSpec`` (whose condition is already known to match), applies the opcode's
transform to the addressable result surface, and recomputes the terminal state
with the court's own rule.

Transforms and the surface each operates on (handoff §4.1 mapping):

    suppress_candidate          census + cascade surviving certificate ids
    withhold_certificate        surviving_error_certificate_ids (one carrier)
    keep_coverage_incomplete    prosecution_coverage
    hold_candidate_qualification surviving ids of the candidate (no re-emit)
    reject_non_causal_origin    surviving ids of the rejected origin + re-select
    select_earliest_origin      responsible_event_id (earliest surviving origin)
    suppress_later_origin       responsible_event_id (earliest surviving origin)

A suppress transform never establishes clean coverage, so the terminal
recompute is the court rule restricted to the reachable branch:

    surviving non-empty  -> verified_error, earliest surviving origin
    surviving empty       -> verified_clean when coverage_complete AND
                             candidate_terminal (both read from the original
                             terminal_basis), else unresolved

The mutated result carries a ``l1_execution`` record (mirroring
``policy_evaluation``) with the fired opcode, the postcondition and the
before/after terminal, so the effect layer can attribute the intervention
without reading provider self-report.

Pure functions, no I/O, evaluation order equals the input spec order.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise.skills.action import ActionSpec, sanitize_action_spec
from sehd_rise.skills.binding import (
    BINDING_BOUND,
    Binding,
    match_condition_bindings,
    validate_binding_contract,
)
from sehd_rise.skills.rule import (
    sanitize_condition_spec,
)


EXECUTED_L1_OPERATIONS = frozenset({
    "suppress_candidate",
    "withhold_certificate",
    "keep_coverage_incomplete",
    "hold_candidate_qualification",
    "reject_non_causal_origin",
    "select_earliest_origin",
    "suppress_later_origin",
})


def _certificate_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Prosecution certificate rows keyed by the surviving ids.

    The court merges initial + appeal prosecution into ``combined_prosecution``;
    ``prosecution`` is the focused-initial subset. Both carry the same
    ``certificate_id`` / ``source_candidate_id`` / ``origin_event_id`` shape.
    """
    for surface in ("combined_prosecution", "prosecution"):
        payload = result.get(surface)
        if not isinstance(payload, Mapping):
            continue
        rows = payload.get("candidates")
        if not isinstance(rows, list):
            continue
        mapped = [dict(row) for row in rows if isinstance(row, Mapping)]
        if mapped:
            return mapped
    return []


def _surviving_ids(result: Mapping[str, Any]) -> list[str]:
    proof = result.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        return []
    raw = proof.get("surviving_error_certificate_ids")
    return [str(value) for value in raw] if isinstance(raw, list) else []


def _origin_order(result: Mapping[str, Any]) -> dict[str, int]:
    """Origin event id -> temporal index, from the census order where present.

    The census rows carry ``origin_hypothesis_event_id``; their order is the
    pipeline's discovery order, so it is the deterministic temporal fallback
    the court's own ``select_earliest_origin_temporal`` uses.
    """
    order: dict[str, int] = {}
    census = result.get("census")
    rows = census if isinstance(census, list) else []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping):
            origin = str(row.get("origin_hypothesis_event_id") or "")
            if origin and origin not in order:
                order[origin] = index
    return order


def _earliest_surviving_origin(
    result: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    surviving: Sequence[str],
) -> str:
    """Earliest surviving origin by census order, fail-closed to first id."""
    surviving_set = set(surviving)
    origins = [
        (row.get("origin_event_id"), row.get("certificate_id"))
        for row in rows
        if str(row.get("certificate_id") or "") in surviving_set
    ]
    if not origins:
        return ""
    order = _origin_order(result)
    return min(
        origins,
        key=lambda pair: (
            order.get(str(pair[0] or ""), 10**9),
            str(pair[1] or ""),
        ),
    )[0]


def _terminal_basis_facts(result: Mapping[str, Any]) -> dict[str, bool]:
    """Original coverage_complete / candidate_terminal from the court basis."""
    proof = result.get("proof_adjudication")
    basis = proof.get("terminal_basis") if isinstance(proof, Mapping) else None
    decisive = basis.get("decisive_facts") if isinstance(basis, Mapping) else {}
    return {
        "coverage_complete": bool(decisive.get("coverage_complete")),
        "candidate_terminal": bool(decisive.get("candidate_terminal")),
    }


def _recompute_terminal(
    result: Mapping[str, Any],
    surviving: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str | None]:
    """Recompute terminal + responsible origin under the court's rule."""
    if surviving:
        origin = _earliest_surviving_origin(result, rows, surviving)
        return "verified_error", origin or None
    facts = _terminal_basis_facts(result)
    if facts["coverage_complete"] and facts["candidate_terminal"]:
        return "verified_clean", None
    return "unresolved", None


def _remove_certificates(
    result: Mapping[str, Any], *, remove: set[str],
) -> list[str]:
    """Return the surviving id list with ``remove`` carriers withheld."""
    return [value for value in _surviving_ids(result) if value not in remove]


def _candidate_certificate_ids(
    rows: Sequence[Mapping[str, Any]], candidate_id: str,
) -> set[str]:
    return {
        str(row.get("certificate_id") or "")
        for row in rows
        if str(row.get("source_candidate_id") or "") == candidate_id
    }


def _candidate_rows_surfaces(result: dict[str, Any], candidate_id: str) -> None:
    """Remove a candidate from every census surface (best effort)."""
    for key in ("census", "paca_census", "construct_census", "deterministic_process_census"):
        rows = result.get(key)
        if isinstance(rows, list):
            result[key] = [
                dict(row) for row in rows
                if not (isinstance(row, Mapping) and str(row.get("candidate_id") or "") == candidate_id)
            ]


def _mutate_proof(result: dict[str, Any], **updates: Any) -> None:
    proof = result.get("proof_adjudication")
    if isinstance(proof, Mapping):
        result["proof_adjudication"] = {**dict(proof), **updates}
    for key, value in updates.items():
        result[key] = value


def execute_l1_intervention(
    result: Mapping[str, Any], *, spec: ActionSpec,
    bindings: Sequence[Binding] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one deterministic L1 transform and return (result, report).

    ``bindings`` must already satisfy the capability's typed binding contract.
    The executor never resolves a first match and never treats a schema field
    name as an entity id.

    The transform mutates a copy of ``result``; the report carries ``fired``,
    ``postcondition``, the opcode and the terminal transition so the effect
    layer can attribute the intervention without provider self-report.
    """
    mutated = dict(result)
    proof = mutated.get("proof_adjudication")
    rows = _certificate_rows(mutated)
    surviving = _surviving_ids(mutated)
    operation = spec.operation
    binding_status, binding_detail = validate_binding_contract(
        bindings,
        domain=spec.input_binding_domain,
        required_fields=spec.required_binding_fields,
        cardinality=spec.cardinality,
    )
    fired = False
    detail = ""

    if binding_status != BINDING_BOUND:
        return mutated, {
            "operation": operation,
            "fired": False,
            "postcondition": spec.postcondition,
            "binding_status": binding_status,
            "binding_detail": binding_detail,
            "bindings": [binding.to_dict() for binding in bindings],
            "detail": binding_detail,
            "surviving_after": list(surviving),
            "terminal_state": str((proof or {}).get("state") or ""),
        }

    identifiers = [dict(binding.identifiers) for binding in bindings]

    if operation == "suppress_candidate":
        candidate_ids = {
            value for item in identifiers for value in (item.get("candidate_id"),)
            if value
        }
        for candidate_id in candidate_ids:
            _candidate_rows_surfaces(mutated, candidate_id)
        removed = set().union(*(
            _candidate_certificate_ids(rows, candidate_id)
            for candidate_id in candidate_ids
        )) & set(surviving) if candidate_ids else set()
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = "suppressed_candidates:" + ",".join(sorted(candidate_ids))

    elif operation == "withhold_certificate":
        pinned = identifiers[0].get("certificate_id", "")
        removed = {pinned} & set(surviving)
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = f"withheld_certificate:{sorted(removed)[0]}"

    elif operation == "keep_coverage_incomplete":
        sides = {item.get("coverage_side") for item in identifiers}
        if "prosecution" in sides and proof and proof.get("prosecution_coverage") == "complete":
            _mutate_proof(mutated, prosecution_coverage="incomplete")
            fired = True
            detail = "prosecution_coverage_opened:complete->incomplete"

    elif operation == "hold_candidate_qualification":
        # Qualification not positive: the candidate's certificate is not
        # re-emitted, so its surviving carriers are withheld.
        candidate_ids = {
            item.get("candidate_id", "") for item in identifiers
            if item.get("candidate_id")
        }
        removed = set().union(*(
            _candidate_certificate_ids(rows, candidate_id)
            for candidate_id in candidate_ids
        )) & set(surviving) if candidate_ids else set()
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = "qualifications_held:" + ",".join(sorted(candidate_ids))

    elif operation == "reject_non_causal_origin":
        origin = identifiers[0].get("origin_event_id", "")
        removed = {
            str(row.get("certificate_id") or "")
            for row in rows
            if str(row.get("origin_event_id") or "") == origin
        } & set(surviving)
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = f"rejected_non_causal_origin:{origin}"

    elif operation in ("select_earliest_origin", "suppress_later_origin"):
        allowed_origins = {
            item.get("origin_event_id", "") for item in identifiers
            if item.get("origin_event_id")
        }
        bound_rows = [
            row for row in rows
            if str(row.get("origin_event_id") or "") in allowed_origins
        ]
        origin = _earliest_surviving_origin(mutated, bound_rows, surviving)
        current = str(
            (proof or {}).get("responsible_event_id")
            if isinstance(proof, Mapping) else ""
        )
        if origin and origin != current:
            _mutate_proof(
                mutated, responsible_event_id=origin,
                state="verified_error",
            )
            fired = True
            detail = f"responsible_origin_selected:{origin}"

    if operation in EXECUTED_L1_OPERATIONS:
        state, responsible = _recompute_terminal(mutated, surviving, rows)
        before = str((proof or {}).get("state") if isinstance(proof, Mapping) else "")
        _mutate_proof(
            mutated,
            surviving_error_certificate_ids=list(surviving),
            state=state,
            responsible_event_id=responsible or None,
        )
        if operation == "keep_coverage_incomplete" and state == "verified_clean":
            # Opening prosecution coverage cannot leave a clean verdict; the
            # recompute already handles it, but keep the invariant explicit.
            state = "unresolved"
            _mutate_proof(mutated, state=state, responsible_event_id=None)
        detail = detail or f"{operation}_terminal:{before}->{state}"

    report = {
        "operation": operation,
        "fired": bool(fired),
        "postcondition": spec.postcondition,
        "binding_status": binding_status,
        "binding_detail": binding_detail,
        "bindings": [binding.to_dict() for binding in bindings],
        "detail": detail or f"unreachable_surface:{operation}",
        "surviving_after": list(surviving),
        "terminal_state": (
            mutated.get("proof_adjudication") or {}
        ).get("state") if isinstance(mutated.get("proof_adjudication"), Mapping) else "",
    }
    return mutated, report


def execute_l1_interventions(
    result: Mapping[str, Any], *, specs: Sequence[ActionSpec],
    bindings: Sequence[Sequence[Binding]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply L1 transforms in order, returning (result, execution record).

    The execution record mirrors ``policy_evaluation``: ``fired`` and
    ``skipped`` lists plus the terminal transition, so the effect layer can
    consume one deterministic execution surface for the whole intervention set.
    """
    current = dict(result)
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        bound = bindings[index] if index < len(bindings) else ()
        current, report = execute_l1_intervention(current, spec=spec, bindings=bound)
        bucket = fired if report["fired"] else skipped
        bucket.append({
            "operation": report["operation"],
            "postcondition": report["postcondition"],
            "detail": report["detail"],
        })
    proof = current.get("proof_adjudication")
    before = (
        result.get("state") if isinstance(result, Mapping) else ""
    )
    after = (
        proof.get("state") if isinstance(proof, Mapping) else ""
    )
    return current, {
        "fired": fired,
        "skipped": skipped,
        "terminal_state": after,
        "terminal_transition": f"{before}->{after}",
    }


def evaluate_l1_rule(
    *,
    condition_spec: Mapping[str, Any] | None,
    action_spec: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate one L1 rule deterministically against a court result.

    Plan B ties a rule's closed condition to its closed transform without a
    provider re-run: project the result-side condition features, evaluate the
    ``condition_spec`` against them, and when it matches apply the
    ``action_spec`` transform (instance-resolved) to a copy of the result.

    Returns ``(transformed_result, report)``. ``transformed_result`` is the
    input unchanged when the condition does not match or no action compiles;
    the report carries ``fired`` / ``matched`` / ``postcondition`` /
    ``terminal_transition`` so the effect layer can attribute the intervention
    from one deterministic execution surface.
    """
    spec = sanitize_action_spec(action_spec)
    report: dict[str, Any] = {
        "operation": (spec.get("operation") if spec else ""),
        "fired": False,
        "matched": True,
        "postcondition": (spec.get("postcondition") if spec else ""),
        "terminal_transition": "",
    }
    if not spec:
        report["matched"] = False
        report["detail"] = "action_spec_absent"
        return dict(result), report

    condition_dict = sanitize_condition_spec(condition_spec)
    action = ActionSpec.from_dict(spec)
    binding_match = match_condition_bindings(
        condition_dict,
        result,
        domain=action.input_binding_domain,
        cardinality=action.cardinality,
        record=record,
    )
    report["binding_status"] = binding_match.status
    report["binding_proof"] = binding_match.to_dict()
    report["matched"] = bool(binding_match.bindings)
    if not binding_match.bound:
        report["detail"] = binding_match.reason
        return dict(result), report

    transformed, transform_report = execute_l1_intervention(
        result, spec=action, bindings=binding_match.bindings,
    )
    report.update(transform_report)
    report["matched"] = True
    before = str((result or {}).get("state") or "")
    after = str(
        (transformed.get("proof_adjudication") or {}).get("state")
        if isinstance(transformed.get("proof_adjudication"), Mapping) else ""
    )
    report["terminal_transition"] = f"{before}->{after}"
    return transformed, report


def execute_l1_rules(
    result: Mapping[str, Any],
    rules: Sequence[tuple[str, str, Mapping[str, Any] | None, Mapping[str, Any] | None]],
    *,
    record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply an ordered L1 rule set deterministically to a court result.

    ``rules`` is a sequence of ``(module_id, rule_id, condition_spec,
    action_spec)`` in genome order (deterministic priority). Each rule is
    evaluated against the evolving result; the returned execution record
    mirrors ``policy_evaluation`` with ``fired`` / ``skipped`` lists plus the
    terminal transition.
    """
    current = dict(result)
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    bound: list[dict[str, Any]] = []
    for module_id, rule_id, condition_spec, action_spec in rules:
        current, report = evaluate_l1_rule(
            condition_spec=condition_spec,
            action_spec=action_spec,
            result=current,
            record=record,
        )
        row = {
            "rule_id": rule_id,
            "module_id": module_id,
            "operation": report.get("operation"),
            "postcondition": report.get("postcondition"),
            "matched": report.get("matched"),
            "binding_status": report.get("binding_status"),
            "binding_proof": report.get("binding_proof"),
            "detail": report.get("detail"),
        }
        if report.get("binding_status") == BINDING_BOUND:
            bound.append(dict(row))
        (fired if report.get("fired") else skipped).append(row)
    before = str((result or {}).get("state") or "")
    proof = current.get("proof_adjudication")
    after = str(proof.get("state")) if isinstance(proof, Mapping) else ""
    return current, {
        "fired": fired,
        "bound": bound,
        "skipped": skipped,
        "terminal_state": after,
        "terminal_transition": f"{before}->{after}",
    }


__all__ = [
    "EXECUTED_L1_OPERATIONS",
    "evaluate_l1_rule",
    "execute_l1_intervention",
    "execute_l1_interventions",
    "execute_l1_rules",
]
