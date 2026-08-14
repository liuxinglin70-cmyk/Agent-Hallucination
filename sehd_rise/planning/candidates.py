"""Candidate prevalidation for the L1/L2 planners.

Pure admission checks moved off the old evolution god-object. They run
before the critic so ranking only ever sees executable candidates
(ownership, operation and slot shape mirror the post-critic gate), and
every discarded candidate keeps its reject reason in the audit trail.

The checks consume closed projections only: the candidate dict, the legal
owner set, the repair frontier (FailureEdge dicts), the deterministic
polarity vocabulary and the policy candidate view. Failure attribution is
read through a mapping view (``layer`` / ``artifact`` or ``owner`` /
``reason_code``) so both the causal episode snapshot and the plain
attribution dict can drive the same identity check.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from sehd_rise.causal.cut import allowed_l1_modules as _allowed_l1_modules
from sehd_rise.skills.action import (
    CLEAN_TO_ERROR_ACTIONS,
    ERROR_TO_CLEAN_ACTIONS,
    compile_action_spec,
)
from sehd_rise.skills.binding import (
    BINDING_BOUND,
    BINDING_NOT_FOUND,
    BindingMatch,
    match_condition_bindings,
    predicate_matches_binding,
    project_binding_universe,
)
from sehd_rise.skills.capabilities import OWNER_BINDING_DOMAINS
from sehd_rise.skills.genome import (
    EXPECTED_DIRECTIONS,
    EXPECTED_SCOPES,
    _detect_rule_direction,
    validate_expected_effect_contract,
    validate_runtime_rule,
)
from sehd_rise.skills.policy import PolicyEdit
from sehd_rise.skills.rule import (
    RuleSpec,
    evaluate_condition_spec,
    render_rule,
    sanitize_route_guidance,
    validate_condition_spec,
    validate_condition_spec_for_domain,
    validate_rule_spec,
)
from sehd_rise.skills.vocabulary.charters import MODULE_OBSERVABLE_FIELDS
from sehd_rise.skills.vocabulary.contracts import (
    ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY,
    ACTION_DOWNGRADE_COVERAGE,
    ACTION_INVALIDATE_COVERAGE_CLAIM,
    ERROR_CHECK_FIELDS,
    semantic_overlap,
)


# Gold polarity constraint for L1 owner hypotheses. A false negative (gold
# says error, detector verdict clean) may only be owned by modules that can
# tighten error detection; a false positive (gold says clean, detector
# verdict error) may only be owned by modules that can relax or bound the
# error claim. Structural invariant: failure attribution never crosses the
# gold polarity side.
_FN_POLARITY_MODULES = frozenset(CLEAN_TO_ERROR_ACTIONS)
_FP_POLARITY_MODULES = frozenset(ERROR_TO_CLEAN_ACTIONS)

# Error-type string vocabulary (the classifier's closed ``error_type``
# values, kept as strings so this layer needs no typing import).
_ERRORWARD_ERROR_TYPES = frozenset({
    "false_negative",
    "false_negative_clean",
    "false_negative_unresolved",
    "unresolved_should_error",
})
_CLEANWARD_ERROR_TYPES = frozenset({
    "false_positive",
    "unresolved_should_clean",
})


def _polarity_pool(error_type: str | None) -> frozenset[str] | None:
    """Return the editable module side required by the failure direction."""
    if error_type in _ERRORWARD_ERROR_TYPES:
        return _FN_POLARITY_MODULES
    if error_type in _CLEANWARD_ERROR_TYPES:
        return _FP_POLARITY_MODULES
    return None


def _episode_matches_failure(
    episode_payload: Mapping[str, Any] | None,
    attribution: Mapping[str, Any],
    evolution_direction: str,
) -> bool:
    """Whether a retry still addresses the same causal failure identity."""
    if not isinstance(episode_payload, Mapping) or "episode_id" not in episode_payload:
        return False
    prior_attribution = episode_payload.get("attribution") or {}
    prior_detection = episode_payload.get("detection") or {}
    if not isinstance(prior_attribution, Mapping) or not isinstance(
        prior_detection, Mapping
    ):
        return False
    prior_owner = str(
        prior_attribution.get("owner_module")
        or prior_attribution.get("artifact")
        or ""
    )
    target_owner = str(
        attribution.get("artifact") or attribution.get("owner") or ""
    )
    return (
        str(prior_attribution.get("layer") or "") == str(attribution.get("layer") or "")
        and prior_owner == target_owner
        and str(prior_attribution.get("reason_code") or "")
        == str(attribution.get("reason_code") or "")
        and str(prior_detection.get("evolution_direction") or "")
        == str(evolution_direction or "")
    )


def _policy_rejection_signature(
    decision: str,
    reason: str,
    proposal: PolicyEdit | None,
) -> str:
    """Render one L2 rejection with the proposal's closed-vocabulary
    semantic signature, so the next policy reflection can recognise and
    avoid the same ineffective edit instead of only the decision label."""
    signature = f"{decision} ({reason})"
    if proposal is None:
        return signature
    if proposal.new_item is not None:
        item = proposal.new_item
        params = ",".join(
            f"{key}={value}"
            for key, value in sorted(item.params.items())
        )
        signature += (
            f" proposal={str(proposal.operation)}:"
            f"{item.precondition}->{item.action}({params})"
        )
    else:
        signature += f" proposal={str(proposal.operation)}"
    return signature[:500]


def _normalize_candidate_from_repair_plan(
    candidate: Mapping[str, Any],
    repair_frontier: Sequence[Mapping[str, Any]],
    plans: Sequence[Any] = (),
) -> dict[str, Any]:
    """Fill framework-known execution fields that the reflection model no
    longer generates.

    With RepairPlan slots, the model returns only ``repair_edge_id`` +
    ``operation`` (+ optional ``output_field``). The framework derives:

    - ``target_module`` = the selected edge's ``owner_module``
    - ``action`` = f"{operation}: {output_field or edge.field}" (the opcode
      slot ``compile_action_spec`` accepts)

    When exactly one RepairPlan exists, every one of those fields is
    framework-known and the model's omissions or malformed restatements are
    repaired deterministically (an action missing its ``: target`` gets the
    plan's unique opcode + edge field) — the model owns only the
    applicability condition. A candidate that already carries valid
    ``target_module`` and ``action`` is returned unchanged; ambiguity (more
    than one plan) leaves everything untouched and
    ``_prevalidate_candidate`` rejects fail-closed.
    """
    if not isinstance(candidate, Mapping):
        return dict(candidate) if isinstance(candidate, dict) else {}
    normalized = dict(candidate)
    unique_plan = plans[0] if len(tuple(plans)) == 1 else None
    unique_edge = None
    unique_op = ""
    if unique_plan is not None:
        edge = getattr(unique_plan, "target_causal_edge", None)
        edge_dict = edge.to_dict() if hasattr(edge, "to_dict") else None
        if isinstance(edge_dict, Mapping):
            unique_edge = edge_dict
        legal = tuple(getattr(unique_plan, "legal_operations", ()) or ())
        unique_op = (
            str(getattr(unique_plan, "auto_selected_operation", "") or "")
            or (legal[0] if len(legal) == 1 else "")
        )
    selected_edge_id = str(normalized.get("repair_edge_id") or "")
    if not selected_edge_id and unique_edge:
        selected_edge_id = str(unique_edge.get("edge_id") or "")
        normalized["repair_edge_id"] = selected_edge_id
    if not selected_edge_id:
        return normalized
    selected_edge = next(
        (
            dict(edge) for edge in repair_frontier
            if str(edge.get("edge_id") or "") == selected_edge_id
        ),
        unique_edge,
    )
    if selected_edge is None:
        return normalized
    if not str(normalized.get("target_module") or "").strip():
        edge_owner = str(selected_edge.get("owner_module") or "")
        if edge_owner:
            normalized["target_module"] = edge_owner
    edit_operation = str(normalized.get("operation") or "").strip()
    opcode = str(normalized.get("opcode") or "").strip()
    action = str(normalized.get("action") or "").strip()
    if edit_operation in {"add_rule", "modify_rule"} and not action:
        if opcode:
            edge_field = str(normalized.get("output_field") or "") or str(
                selected_edge.get("field") or ""
            )
            if edge_field:
                normalized["action"] = f"{opcode}: {edge_field}"
        elif unique_op:
            edge_field = str(
                normalized.get("output_field") or ""
            ) or str(selected_edge.get("field") or "")
            normalized["opcode"] = unique_op
            if edge_field:
                normalized["output_field"] = edge_field
                normalized["action"] = f"{unique_op}: {edge_field}"
    elif action and ":" not in action and unique_op:
        # The model named the opcode but dropped the framework-known target:
        # the unique plan restores the deterministic action string.
        if action.split(":", 1)[0].strip() == unique_op:
            edge_field = str(
                normalized.get("output_field") or ""
            ) or str(selected_edge.get("field") or "")
            if edge_field:
                normalized["output_field"] = edge_field
                normalized["action"] = f"{unique_op}: {edge_field}"
    return normalized


def _repair_targets_for(
    target_module: str,
    edge_field: str,
) -> set[str]:
    """The edge field plus the registered certificate-check expansion."""
    repair_targets = {edge_field}
    if target_module == "atomic_verifier_instruction":
        repair_targets.update(ERROR_CHECK_FIELDS.get(edge_field, ()))
    return repair_targets


def _resolve_source_binding(
    candidate: Mapping[str, Any],
    source_result: Mapping[str, Any],
    *,
    domain: str,
    cardinality: str,
    record: Mapping[str, Any] | None = None,
) -> BindingMatch:
    """Resolve the source-case entity by exact id when the model named one.

    Entity identity and the generalization predicate are two different things:
    the model may name ``target_entity_id`` to say *which* entity this training
    sample edits, while ``condition_spec`` describes *when the skill reuses*.
    For an ``exactly_one`` capability, binding the named id directly is
    unambiguous by construction and can never return BINDING_AMBIGUOUS. When no
    id is named, the predicate evaluates over the universe as before.
    """
    target_entity_id = str(candidate.get("target_entity_id") or "").strip()
    if not target_entity_id:
        return match_condition_bindings(
            candidate.get("condition_spec"),
            source_result,
            domain=domain,
            cardinality=cardinality,
            record=record,
        )
    universe = project_binding_universe(
        source_result, domain=domain, record=record,
    )
    binding = next(
        (item for item in universe if item.entity_id == target_entity_id),
        None,
    )
    if binding is None:
        return BindingMatch(
            BINDING_NOT_FOUND, domain, cardinality, (),
            f"target_entity_not_found:{domain}:{target_entity_id}",
        )
    if not predicate_matches_binding(candidate.get("condition_spec"), binding):
        return BindingMatch(
            BINDING_NOT_FOUND, domain, cardinality, (),
            f"condition_not_consistent_with_target_entity:{target_entity_id}",
        )
    return BindingMatch(
        BINDING_BOUND, domain, cardinality, (binding,),
        "target_entity_grounded",
    )


def _prevalidate_route_guidance_candidate(
    candidate: Mapping[str, Any],
    target_module: str,
    repair_frontier: Sequence[Mapping[str, Any]],
) -> str | None:
    """Admission for a route-guidance candidate (no deterministic backend).

    The intervention is a firewall-checked guidance block for the owner's
    producer route, so there is no action spec or source binding to validate:
    admission is the label-free applicability predicate plus a guidance text
    that crosses the D1 authority firewall, bound to the selected repair edge.

    The applicability predicate is still entity-scoped: it must be fully
    bindable within at least one of the owner's binding domains, or it can
    never fire on any entity and the guidance never reaches a provider route.
    """
    condition_ok, condition_reason = validate_condition_spec(
        candidate.get("condition_spec")
    )
    if not condition_ok:
        return condition_reason
    domains = OWNER_BINDING_DOMAINS.get(target_module, ())
    if not any(
        validate_condition_spec_for_domain(
            candidate.get("condition_spec"), domain,
        )[0]
        for domain in domains
    ):
        return f"condition_feature_not_bindable_in_owner_domains:{target_module}"
    guidance = sanitize_route_guidance(candidate.get("local_guidance"))
    if not guidance:
        return "route_guidance_rejected_by_firewall"
    if repair_frontier:
        selected_edge_id = str(candidate.get("repair_edge_id") or "")
        if not selected_edge_id:
            return "repair_edge_id_missing"
        selected_edge = next(
            (
                dict(edge) for edge in repair_frontier
                if str(edge.get("edge_id") or "") == selected_edge_id
            ),
            None,
        )
        if selected_edge is None:
            return f"repair_edge_id_unknown:{selected_edge_id}"
        edge_owner = str(selected_edge.get("owner_module") or "")
        if edge_owner and edge_owner != target_module:
            return f"repair_edge_owner_mismatch:{edge_owner}->{target_module}"
    return None


def _prevalidate_candidate(
    candidate: Mapping[str, Any],
    allowed_modules: Sequence[str],
    *,
    error_type: str | None = None,
    failure_edge_field: str = "",
    repair_frontier: Sequence[Mapping[str, Any]] = (),
    runtime_condition_features: Mapping[str, Any] | None = None,
    source_result: Mapping[str, Any] | None = None,
    source_record: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the reject reason for a candidate that cannot pass the
    admission gate, or None when it is structurally eligible.

    ``error_type`` is the closed classifier ``error_type`` string (e.g.
    ``"false_positive"``), carried here so the polarity filter can run
    without a typing import.
    """
    target_module = str(candidate.get("target_module") or "")
    operation = str(candidate.get("operation") or "")
    if operation == "no_edit":
        return None
    if target_module not in allowed_modules:
        return f"target_module_outside_allowed:{target_module or 'missing'}"
    if operation not in {"add_rule", "modify_rule", "remove_rule"}:
        return f"unknown_operation:{operation or 'missing'}"
    if operation in {"add_rule", "modify_rule"} and str(
        candidate.get("local_guidance") or ""
    ).strip():
        return _prevalidate_route_guidance_candidate(
            candidate, target_module, repair_frontier,
        )
    if operation == "remove_rule":
        rule_index = candidate.get("rule_index")
        if rule_index is None:
            return "remove_rule_missing_rule_index"
        if repair_frontier:
            selected_edge_id = str(candidate.get("repair_edge_id") or "")
            if not selected_edge_id:
                return "remove_rule_edge_id_missing"
            selected_edge = next(
                (
                    dict(edge) for edge in repair_frontier
                    if str(edge.get("edge_id") or "") == selected_edge_id
                ),
                None,
            )
            if selected_edge is None:
                return f"remove_rule_edge_id_unknown:{selected_edge_id}"
            edge_owner = str(selected_edge.get("owner_module") or "")
            if edge_owner and edge_owner != target_module:
                return (
                    "remove_rule_edge_owner_mismatch:"
                    f"{edge_owner}->{target_module}"
                )
    if operation in {"add_rule", "modify_rule"} and not all(
        str(candidate.get(key) or "").strip()
        for key in ("condition", "action", "evidence_anchor")
    ):
        return "rule_spec:missing_slots"
    if operation in {"add_rule", "modify_rule"}:
        condition_ok, condition_reason = validate_condition_spec(
            candidate.get("condition_spec")
        )
        if not condition_ok:
            return condition_reason
        condition = str(candidate.get("condition") or "")
        action = str(candidate.get("action") or "")
        evidence_anchor = str(candidate.get("evidence_anchor") or "")
        rendered = render_rule(RuleSpec(
            condition=condition,
            action=action,
            evidence_anchor=evidence_anchor,
            module_id=target_module,
            direction="process",
        ))
        spec_ok, spec_reason = validate_rule_spec(RuleSpec(
            condition=condition,
            action=action,
            evidence_anchor=evidence_anchor,
            module_id=target_module,
            direction=_detect_rule_direction(rendered),
        ))
        if not spec_ok:
            return f"rule_spec:{spec_reason}"
        runtime_ok, runtime_reason = validate_runtime_rule(
            rendered,
            target_module,
            condition_scope=condition,
        )
        if not runtime_ok:
            return f"runtime_rule_contract:{runtime_reason}"
        try:
            action_spec = compile_action_spec(target_module, action)
        except (KeyError, ValueError) as exc:
            return f"action_spec:{exc}"
        # Domain-scoped binding check: a condition that names a feature the
        # capability's binding domain cannot project (e.g. candidate_kind on a
        # claim_anchor construct_candidate) passes the global contract but can
        # never bind, so reject it here with a precise reason instead of
        # surfacing it later as BINDING_NOT_FOUND / BINDING_AMBIGUOUS.
        domain_ok, domain_reason = validate_condition_spec_for_domain(
            candidate.get("condition_spec"), action_spec.input_binding_domain,
        )
        if not domain_ok:
            return domain_reason
        polarity_pool = _polarity_pool(error_type)
        if polarity_pool is not None:
            allowed_operations = (
                ERROR_TO_CLEAN_ACTIONS.get(target_module, frozenset())
                if error_type in _CLEANWARD_ERROR_TYPES
                else CLEAN_TO_ERROR_ACTIONS.get(target_module, frozenset())
            )
            if action_spec.operation not in allowed_operations:
                return (
                    f"action_polarity_mismatch:{'error_to_clean' if error_type in _CLEANWARD_ERROR_TYPES else 'clean_to_error'}:"
                    f"{target_module}:{action_spec.operation}"
                )
        if repair_frontier:
            selected_edge_id = str(candidate.get("repair_edge_id") or "")
            if not selected_edge_id:
                return "repair_edge_id_missing"
            selected_edge = next(
                (
                    dict(edge) for edge in repair_frontier
                    if str(edge.get("edge_id") or "") == selected_edge_id
                ),
                None,
            )
            if selected_edge is None:
                return f"repair_edge_id_unknown:{selected_edge_id}"
            edge_owner = str(selected_edge.get("owner_module") or "")
            if edge_owner and edge_owner != target_module:
                return (
                    "repair_edge_owner_mismatch:"
                    f"{edge_owner}->{target_module}"
                )
            edge_field = str(selected_edge.get("field") or "")
            repair_targets = _repair_targets_for(target_module, edge_field)
            if action_spec.output_field not in repair_targets:
                return (
                    "action_not_bound_to_repair_edge:"
                    f"{selected_edge_id}:{edge_field}->{action_spec.output_field}"
                )
        elif failure_edge_field:
            repair_targets = _repair_targets_for(target_module, failure_edge_field)
            if action_spec.output_field not in repair_targets:
                return (
                    "action_not_bound_to_failure_edge:"
                    f"{failure_edge_field}->{action_spec.output_field}"
                )
        if isinstance(source_result, Mapping) and source_result:
            binding_match = _resolve_source_binding(
                candidate, source_result,
                domain=action_spec.input_binding_domain,
                cardinality=action_spec.cardinality,
                record=source_record,
            )
            if not binding_match.bound:
                return binding_match.status
            # Source binding proof is audit metadata, never an executable id
            # baked into the generalized ConditionSpec.
            if isinstance(candidate, dict):
                candidate["binding_proof"] = binding_match.to_dict()
        # Compatibility callers that only have the bounded aggregate snapshot
        # can still reject an obviously false atom. Formal evolution always
        # passes source_result and therefore uses entity grounding above.
        source_features = (
            runtime_condition_features
            if isinstance(runtime_condition_features, Mapping) else {}
        )
        if source_features and not source_result:
            evaluation = evaluate_condition_spec(
                candidate.get("condition_spec"), source_features,
            )
            if not evaluation.matched:
                return "condition_not_observed_on_source_route"
    if operation == "remove_rule":
        contract = candidate.get("expected_effect_contract")
        if isinstance(contract, Mapping) and contract:
            shape_ok, shape_reason = validate_expected_effect_contract(contract)
            if not shape_ok:
                return f"invalid_intervention_contract:{shape_reason}"
            contract_field = str(contract.get("observable_field") or "")
            if contract_field and contract_field not in MODULE_OBSERVABLE_FIELDS.get(
                target_module, ()
            ):
                return f"invalid_intervention_contract:field_not_owned:{contract_field}"
            if (
                str(contract.get("expected_direction") or "") not in EXPECTED_DIRECTIONS
                or str(contract.get("expected_scope") or "") not in EXPECTED_SCOPES
                or not str(contract.get("trigger_condition") or "").strip()
            ):
                return "invalid_intervention_contract:field_shape_mismatch"
    return None


# The L2 selection prompt is gold-free, so gold constrains candidates only
# through this deterministic mirror of policy_evaluator's terminal
# transition. ``side`` is None for the parameterless boundary consumption.
# Only the error-gold direction is filtered: batch01-10 observed cleanward
# candidates burning proposal+replay on error-gold cases (5/5
# state_change_against_gold_polarity rejects), while clean-gold
# anti-candidates never appeared and stay on the replay fitness gate.
_ANTI_GOLD_ACTIONS_ON_ERROR_GOLD: frozenset[tuple[str, str | None]] = frozenset({
    (ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY, None),
    (ACTION_INVALIDATE_COVERAGE_CLAIM, "prosecution"),
    (ACTION_DOWNGRADE_COVERAGE, "prosecution"),
})


def _filter_policy_candidates_by_gold(
    candidates: Sequence[PolicyEdit], gold_label: bool | None,
) -> list[PolicyEdit]:
    """Drop closed-vocabulary policy candidates whose runtime effect moves
    the terminal toward clean on an error-gold case, before the selection
    prompt is built. Gold-free runs (None) and clean-gold cases return the
    candidates unchanged: the L2 prompt stays label-free by construction
    and the replay fitness gate owns the clean-gold direction."""
    if gold_label is not True:
        return list(candidates)
    kept: list[PolicyEdit] = []
    for candidate in candidates:
        item = candidate.new_item
        if item is None:
            kept.append(candidate)
            continue
        side = item.params.get("side") if item.params else None
        if (item.action, side) in _ANTI_GOLD_ACTIONS_ON_ERROR_GOLD:
            continue
        kept.append(candidate)
    return kept


def _generation_module_requirement(
    allowed_modules: Sequence[str],
    generation_index: int,
    covered_modules: Sequence[str] = (),
) -> str:
    """Assign the first independent samples to distinct legal owners.

    A multi-owner failure needs at least one hypothesis from each owner before
    the critic compares candidates. Additional samples remain unconstrained so
    they can explore a genuinely different intervention on either surface.
    """
    if len(allowed_modules) <= 1 or generation_index < 0:
        return ""
    if generation_index < len(allowed_modules):
        return str(allowed_modules[generation_index])
    covered = {str(module) for module in covered_modules}
    for module in allowed_modules:
        if str(module) not in covered:
            return str(module)
    return ""


def _candidate_direction_signature(candidate: Mapping[str, Any]) -> str:
    """Return the validated intervention surface used for round diversity."""
    return "|".join((
        str(candidate.get("target_module") or ""),
        str(candidate.get("operation") or ""),
        str(candidate.get("action") or ""),
    ))


def _rejected_proposal_duplicate(
    rejected: Sequence[Mapping[str, Any]],
    *,
    target_module: str,
    new_content: str,
) -> bool:
    """Return True when a retry repeats an already-rejected source proposal.

    A duplicate is defined by the same owner module AND a near-identical rule
    (same content hash or high token overlap). This is the enforcement arm of
    the source-scoped diversity contract: the reflection prompt says "do not
    repeat", and this gate makes that a hard rejection before any replay.
    """
    if not rejected or not new_content:
        return False
    new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:12]
    for prior in rejected:
        if str(prior.get("target_module") or "") != target_module:
            continue
        prior_hash = str(prior.get("content_hash") or "")
        if prior_hash and prior_hash == new_hash:
            return True
        prior_content = str(prior.get("content") or "")
        if prior_content and new_content:
            try:
                if semantic_overlap(prior_content, new_content) >= 0.85:
                    return True
            except Exception:
                pass
    return False


__all__ = [
    "_allowed_l1_modules",
    "_candidate_direction_signature",
    "_episode_matches_failure",
    "_filter_policy_candidates_by_gold",
    "_generation_module_requirement",
    "_normalize_candidate_from_repair_plan",
    "_policy_rejection_signature",
    "_prevalidate_candidate",
    "_rejected_proposal_duplicate",
]
