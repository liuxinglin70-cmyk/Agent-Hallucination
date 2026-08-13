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
from sehd_rise.skills.rule import (
    ConditionSpec,
    evaluate_condition_spec,
    normalize_candidate_kind,
    normalize_module_tag,
    project_result_condition_features,
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


def _census_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The merged census rows (the candidate-discovery surface)."""
    rows = result.get("census")
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _prosecution_rows_and_audits(
    result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Focused-initial prosecution rows and their index-aligned audits.

    ``proof_adjudication.error_audits`` aligns with ``prosecution.candidates``
    (the focused initial set), NOT with ``combined_prosecution`` — appeal
    certificates re-enter the merge surface but carry no focused audit row.
    """
    prosecution = result.get("prosecution")
    candidates = (
        prosecution.get("candidates") if isinstance(prosecution, Mapping) else None
    )
    rows = [dict(row) for row in candidates if isinstance(row, Mapping)] if isinstance(candidates, list) else []
    proof = result.get("proof_adjudication")
    audits = proof.get("error_audits") if isinstance(proof, Mapping) else None
    audit_rows = [dict(row) for row in audits if isinstance(row, Mapping)] if isinstance(audits, list) else []
    return rows, audit_rows


_AXIS_CODES = frozenset({"F", "Fa", "P", "S"})

# Condition features that resolve to a census candidate instance.
_CANDIDATE_FEATURES = frozenset({"candidate_kind", "candidate_axis", "module_tag"})
# Condition features that resolve to a certificate instance via its audit.
_CERTIFICATE_FEATURES = frozenset({"missing_field", "failed_invariant"})


def resolve_condition_instance(
    condition_spec: Mapping[str, Any] | None, result: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve a matched condition to the concrete instance it names.

    The result-scoped condition is a closed predicate over court-result
    features; the executor needs the specific existing instance that predicate
    named. It maps the feature to one census candidate or one prosecuted
    certificate deterministically.

    Returns a mapping keyed by identifier kind — ``candidate_id``,
    ``source_candidate_id``, ``certificate_id``, ``origin_event_id`` — with
    the first match in census/audit order. An empty mapping means the
    condition is field-level (no instance) or matched nothing; callers then
    fail closed rather than guessing. Only the atom form resolves; a
    combinator condition has no single named instance.
    """
    spec_dict = sanitize_condition_spec(condition_spec)
    if not spec_dict:
        return {}
    spec = ConditionSpec.from_dict(spec_dict)
    if spec.combinator != "atom":
        return {}

    if spec.feature in _CANDIDATE_FEATURES:
        for row in _census_rows(result):
            if spec.feature == "candidate_kind":
                hit = normalize_candidate_kind(row.get("candidate_kind")) == spec.value
            elif spec.feature == "module_tag":
                hit = normalize_module_tag(row.get("module_tag")) == spec.value
            else:  # candidate_axis
                axes = {
                    str(axis) for axis in (row.get("axes") or [])
                    if str(axis) in _AXIS_CODES
                }
                hit = spec.value in axes
            if hit:
                return {
                    "candidate_id": str(row.get("candidate_id") or ""),
                    "origin_event_id": str(row.get("origin_hypothesis_event_id") or ""),
                }
        return {}

    if spec.feature in _CERTIFICATE_FEATURES:
        rows, audits = _prosecution_rows_and_audits(result)
        for index, row in enumerate(rows):
            audit = audits[index] if index < len(audits) else {}
            values = (
                audit.get("missing_fields") if spec.feature == "missing_field"
                else audit.get("failed_invariants")
            )
            if isinstance(values, (list, tuple, set, frozenset)) and spec.value in {
                str(value) for value in values
            }:
                return {
                    "certificate_id": str(row.get("certificate_id") or ""),
                    "source_candidate_id": str(row.get("source_candidate_id") or ""),
                    "origin_event_id": str(row.get("origin_event_id") or ""),
                }
        return {}


def _mutate_proof(result: dict[str, Any], **updates: Any) -> None:
    proof = result.get("proof_adjudication")
    if isinstance(proof, Mapping):
        result["proof_adjudication"] = {**dict(proof), **updates}
    for key, value in updates.items():
        result[key] = value


def execute_l1_intervention(
    result: Mapping[str, Any], *, spec: ActionSpec, target: str = "",
    condition_spec: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one deterministic L1 transform and return (result, report).

    ``spec`` is a compiled ``ActionSpec`` whose condition the caller already
    evaluated. ``target`` is the condition's resolved instance — the specific
    ``candidate_id`` / ``certificate_id`` / ``origin_event_id`` the matched
    condition named. When ``target`` is empty and ``condition_spec`` is
    supplied, the instance is resolved from the result via
    ``resolve_condition_instance`` (first match in census/audit order). It is
    required for the instance-level transforms (``suppress_candidate`` /
    ``withhold_certificate`` / ``hold_candidate_qualification`` /
    ``reject_non_causal_origin``) and ignored by the field-level transforms.
    Without a resolved instance those transforms fail closed (``fired=False``):
    the executor never guesses which carrier the condition meant.

    The transform mutates a copy of ``result``; the report carries ``fired``,
    ``postcondition``, the opcode and the terminal transition so the effect
    layer can attribute the intervention without provider self-report.
    """
    mutated = dict(result)
    proof = mutated.get("proof_adjudication")
    rows = _certificate_rows(mutated)
    surviving = _surviving_ids(mutated)
    operation = spec.operation
    resolved = resolve_condition_instance(condition_spec, mutated)
    fired = False
    detail = ""

    if operation == "suppress_candidate":
        candidate_id = target or resolved.get("candidate_id") or spec.output_field or ""
        _candidate_rows_surfaces(mutated, candidate_id)
        removed = _candidate_certificate_ids(rows, candidate_id) & set(surviving)
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = f"suppressed_candidate:{candidate_id}"

    elif operation == "withhold_certificate":
        pinned = target or resolved.get("certificate_id") or spec.output_field or ""
        removed = {pinned} & set(surviving)
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = f"withheld_certificate:{sorted(removed)[0]}"

    elif operation == "keep_coverage_incomplete":
        if proof and proof.get("prosecution_coverage") == "complete":
            _mutate_proof(mutated, prosecution_coverage="incomplete")
            fired = True
            detail = "prosecution_coverage_opened:complete->incomplete"

    elif operation == "hold_candidate_qualification":
        # Qualification not positive: the candidate's certificate is not
        # re-emitted, so its surviving carriers are withheld.
        candidate_id = target or resolved.get("candidate_id") or spec.output_field or ""
        removed = _candidate_certificate_ids(rows, candidate_id) & set(surviving)
        if removed:
            surviving = _remove_certificates(mutated, remove=removed)
            fired = True
            detail = f"qualification_held:{candidate_id}"

    elif operation == "reject_non_causal_origin":
        origin = target or resolved.get("origin_event_id") or spec.output_field or ""
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
        origin = _earliest_surviving_origin(mutated, rows, surviving)
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
        "detail": detail or f"unreachable_surface:{operation}",
        "surviving_after": list(surviving),
        "terminal_state": (
            mutated.get("proof_adjudication") or {}
        ).get("state") if isinstance(mutated.get("proof_adjudication"), Mapping) else "",
    }
    return mutated, report


def execute_l1_interventions(
    result: Mapping[str, Any], *, specs: Sequence[ActionSpec],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply L1 transforms in order, returning (result, execution record).

    The execution record mirrors ``policy_evaluation``: ``fired`` and
    ``skipped`` lists plus the terminal transition, so the effect layer can
    consume one deterministic execution surface for the whole intervention set.
    """
    current = dict(result)
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for spec in specs:
        current, report = execute_l1_intervention(current, spec=spec)
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
    if condition_dict:
        features = project_result_condition_features(result, record)
        evaluation = evaluate_condition_spec(condition_dict, features)
        report["matched"] = evaluation.matched
        report["condition_detail"] = evaluation.to_dict()
        if not evaluation.matched:
            report["detail"] = "condition_not_matched"
            return dict(result), report

    transformed, transform_report = execute_l1_intervention(
        result, spec=ActionSpec.from_dict(spec), condition_spec=condition_dict or None,
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
            "detail": report.get("detail"),
        }
        (fired if report.get("fired") else skipped).append(row)
    before = str((result or {}).get("state") or "")
    proof = current.get("proof_adjudication")
    after = str(proof.get("state")) if isinstance(proof, Mapping) else ""
    return current, {
        "fired": fired,
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
    "resolve_condition_instance",
]
