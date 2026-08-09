"""Executable L1 intervention specifications.

Natural-language conditions remain the generalization surface, while every
action is compiled from the module/action vocabulary into an explicit provider
route, schema object, output field and postcondition.  Reflection therefore
does not invent execution semantics or a separate expected-effect story.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .module_charter import (
    MODULE_ACTION_OPCODES,
    MODULE_OPERATION_TARGETS,
    validate_action_surface,
)


MODULE_ROUTE_IDS: Mapping[str, tuple[str, ...]] = {
    "candidate_trigger": (
        "candidate_fallback_route", "construct_census_route",
    ),
    "evidence_obligation": (
        "propagation_route", "focused_prosecution_route",
        "appeal_prosecutor_route",
    ),
    "candidate_to_verifier_route": ("qualification_route",),
    # The atomic verifier audits ERROR candidates only: its output object is
    # ``verification_certificate`` and its quarantine observable is the proof
    # court's prosecution-side ``quarantined_error_certificate_ids``.  Defense
    # routes therefore cannot execute its actions coherently — a quarantine
    # rule injected there makes the defender quarantine its own certificates,
    # which collapses defense coverage on a gold-clean case and moves the
    # module score against the gold polarity (observed on SmolAgents/029:
    # defense 1.0->0.3, local_score 5.3->7.0 on an FP).  Defense-side
    # verification stays owned by ``clean_defense_boundary``.
    "atomic_verifier_instruction": (
        "qualification_route", "focused_prosecution_route",
        "appeal_prosecutor_route",
    ),
    "clean_defense_boundary": (
        "defense_route", "appeal_defender_route", "boundary_route",
    ),
    "causal_link_test": (
        "propagation_route", "focused_prosecution_route",
        "appeal_prosecutor_route", "boundary_route", "counterfactual_route",
    ),
    "earliest_origin_tiebreak": ("boundary_route", "counterfactual_route"),
    "abstention_rule": ("boundary_route",),
}

MODULE_OUTPUT_OBJECT: Mapping[str, str] = {
    "candidate_trigger": "candidate",
    "evidence_obligation": "error_certificate",
    "candidate_to_verifier_route": "candidate_qualification",
    "atomic_verifier_instruction": "verification_certificate",
    "clean_defense_boundary": "defense_certificate_or_boundary",
    "causal_link_test": "causal_certificate_or_boundary",
    "earliest_origin_tiebreak": "boundary_finding",
    "abstention_rule": "boundary_finding",
}

_OPERATION_POSTCONDITION: Mapping[str, str] = {
    "emit_candidate": "object_present",
    "suppress_candidate": "object_absent",
    "emit_origin_census": "object_present",
    "require_evidence_field": "field_nonempty",
    "populate_evidence_field": "field_nonempty",
    "emit_error_certificate": "object_present",
    "keep_coverage_incomplete": "coverage_not_complete",
    "route_candidate_to_verifier": "route_assignment_present",
    "qualify_candidate": "qualification_positive",
    "hold_candidate_qualification": "qualification_not_positive",
    "verify_violation": "verification_state_present",
    "quarantine_certificate": "object_quarantined",
    "emit_certificate": "object_present",
    "emit_defense_certificate": "object_present",
    "emit_boundary_finding": "field_present",
    "test_causal_link": "causal_status_present",
    "reject_non_causal_origin": "origin_not_selected",
    "populate_impact_chain": "field_nonempty",
    "select_earliest_origin": "responsible_origin_present",
    "suppress_later_origin": "later_origin_not_selected",
}

_MODULE_EFFECT: Mapping[str, tuple[str, str]] = {
    "candidate_trigger": ("candidate_count", "increase"),
    "evidence_obligation": ("causal_field_coverage", "increase"),
    "candidate_to_verifier_route": ("prosecution_certificates", "increase"),
    "atomic_verifier_instruction": ("quarantined_certificates", "increase"),
    "clean_defense_boundary": ("defense_certificates", "increase"),
    "causal_link_test": ("causal_field_coverage", "increase"),
    "earliest_origin_tiebreak": ("responsible_step", "shift"),
    "abstention_rule": ("state", "shift"),
}

_OPERATION_EFFECT: Mapping[str, tuple[str, str]] = {
    "suppress_candidate": ("candidate_count", "decrease"),
    "keep_coverage_incomplete": ("prosecution_coverage", "opened"),
    "hold_candidate_qualification": ("prosecution_certificates", "decrease"),
    "quarantine_certificate": ("quarantined_certificates", "increase"),
    "reject_non_causal_origin": ("responsible_step", "removed"),
    "suppress_later_origin": ("responsible_step", "shift"),
}

_MODULE_OPERATION_EFFECT: Mapping[tuple[str, str], tuple[str, str]] = {
    (
        "atomic_verifier_instruction", "require_evidence_field",
    ): ("causal_field_coverage", "increase"),
}


@dataclass(frozen=True)
class ActionSpec:
    module_id: str
    route_ids: tuple[str, ...]
    output_object: str
    output_field: str
    operation: str
    postcondition: str
    observable_field: str
    expected_direction: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["route_ids"] = list(self.route_ids)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionSpec":
        return cls(
            module_id=str(value.get("module_id") or ""),
            route_ids=tuple(str(item) for item in value.get("route_ids") or ()),
            output_object=str(value.get("output_object") or ""),
            output_field=str(value.get("output_field") or ""),
            operation=str(value.get("operation") or ""),
            postcondition=str(value.get("postcondition") or ""),
            observable_field=str(value.get("observable_field") or ""),
            expected_direction=str(value.get("expected_direction") or ""),
        )

    def effect_contract(self, condition: str) -> dict[str, str]:
        return {
            "observable_field": self.observable_field,
            "expected_direction": self.expected_direction,
            "expected_scope": "all_cases",
            "trigger_condition": " ".join(str(condition).split())[:240],
        }

    def expected_effect_text(self) -> str:
        return (
            f"{self.observable_field} must {self.expected_direction} when "
            f"{self.postcondition} is enforced"
        )

    def removal_effect_contract(self, condition: str) -> dict[str, str]:
        inverse = {
            "increase": "decrease",
            "decrease": "increase",
            "opened": "closed",
            "closed": "opened",
            "removed": "added",
            "added": "removed",
            "shift": "shift",
        }
        direction = inverse.get(self.expected_direction, "shift")
        contract = self.effect_contract(condition)
        contract["expected_direction"] = direction
        return contract

    def removal_effect_text(self) -> str:
        direction = self.removal_effect_contract("removed rule condition")[
            "expected_direction"
        ]
        return (
            f"{self.observable_field} must {direction} when the intervention "
            "is removed"
        )


def compile_action_spec(module_id: str, action: str) -> ActionSpec:
    """Compile one validated opcode slot into its executable specification."""
    valid, reason = validate_action_surface(module_id, action)
    if not valid:
        raise ValueError(reason)
    operation, separator, raw_target = str(action).lower().partition(":")
    operation = operation.strip()
    output_field = raw_target.strip() if separator else ""
    postcondition = _OPERATION_POSTCONDITION.get(operation)
    if not postcondition:
        raise ValueError(f"action_spec_operation_unmapped:{operation}")
    observable_field, direction = _MODULE_OPERATION_EFFECT.get(
        (module_id, operation),
        _OPERATION_EFFECT.get(operation, _MODULE_EFFECT[module_id]),
    )
    return ActionSpec(
        module_id=module_id,
        route_ids=MODULE_ROUTE_IDS[module_id],
        output_object=MODULE_OUTPUT_OBJECT[module_id],
        output_field=output_field,
        operation=operation,
        postcondition=postcondition,
        observable_field=observable_field,
        expected_direction=direction,
    )


def render_action_spec_contract(module_id: str) -> str:
    """Render the executable intervention choices used by reflection.

    The prompt and the runtime compiler share these operation semantics, so
    reflection selects an output intervention instead of inventing a natural-
    language effect claim disconnected from provider output.
    """
    lines: list[str] = []
    for operation, template in MODULE_ACTION_OPCODES.get(module_id, ()):
        targets = MODULE_OPERATION_TARGETS.get(module_id, {}).get(operation, ())
        if not targets:
            continue
        spec = compile_action_spec(module_id, f"{operation}: {targets[0]}")
        lines.append(
            f"- {operation}: {template}; output_object={spec.output_object}; "
            f"postcondition={spec.postcondition}; "
            f"observable_effect={spec.observable_field}:{spec.expected_direction}"
        )
        lines.append("  valid targets: " + ", ".join(targets))
    return "\n".join(lines) if lines else "(no executable actions)"


def sanitize_action_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    spec = ActionSpec.from_dict(value)
    try:
        canonical = compile_action_spec(
            spec.module_id,
            f"{spec.operation}: {spec.output_field}"
            if spec.output_field else spec.operation,
        )
    except (KeyError, ValueError):
        return {}
    return canonical.to_dict() if spec == canonical else {}


# ============================================================
# Deterministic postcondition evaluation (D1 execution evidence)
# ============================================================

# postcondition -> result-observable semantics.  A postcondition with no
# mapping here cannot be verified against the final detection result and
# evaluates fail-closed to ``unmapped_postcondition``: an obligation that
# the result schema does not expose is never silently treated as met.
_POSTCONDITION_CHECKS = frozenset({
    "object_present",
    "object_absent",
    "field_present",
    "field_nonempty",
    "coverage_not_complete",
    "object_quarantined",
    "causal_status_present",
    "origin_not_selected",
    "responsible_origin_present",
    "later_origin_not_selected",
    "route_assignment_present",
    "qualification_positive",
    "qualification_not_positive",
    "verification_state_present",
})

# output_object -> result surface row access.  ``verification_certificate``
# is the atomic-verifier's output; the certificates it verifies are the
# prosecution certificate rows of the final result, and their quarantine
# state is the proof court's ``quarantined_error_certificate_ids``.
# ``causal_certificate_or_boundary`` is the causal-link test's output: the
# propagation findings (``propagations`` rows with a propagation_state) and
# the pairwise counterfactual origin rankings (surfaced by the engine).
_OBJECT_SURFACE_KEYS: Mapping[str, tuple[str, ...]] = {
    "candidate": ("paca_census", "construct_census", "census"),
    "error_certificate": ("combined_prosecution", "prosecution"),
    "verification_certificate": ("combined_prosecution", "prosecution"),
    "defense_certificate_or_boundary": ("combined_defense", "defense"),
    "causal_certificate_or_boundary": (
        "propagations", "counterfactual_rankings",
    ),
    "candidate_qualification": ("qualifications",),
}


def _surface_rows(
    result: Mapping[str, Any], output_object: str,
) -> list[Mapping[str, Any]] | None:
    """Rows of the surface an output_object maps to, or None if the surface
    is not observable in the final result."""
    if output_object == "boundary_finding":
        boundary = result.get("boundary_path")
        return [dict(boundary)] if isinstance(boundary, Mapping) and boundary else []
    keys = _OBJECT_SURFACE_KEYS.get(output_object)
    if keys is None:
        return None
    # The first key that yields rows wins; an empty earlier key must not
    # shadow a populated later one (paca_census may be empty while
    # construct_census carries the rows).
    for key in keys:
        payload = result.get(key)
        if isinstance(payload, list):
            rows = [dict(row) for row in payload if isinstance(row, Mapping)]
            if rows:
                return rows
            continue
        if not isinstance(payload, Mapping):
            continue
        rows = payload.get("candidates") or payload.get("certificates") or []
        if isinstance(rows, list):
            rows = [dict(row) for row in rows if isinstance(row, Mapping)]
            if rows:
                return rows
        # A mapping-of-rows surface (e.g. counterfactual_rankings keyed by
        # origin) contributes its values as the rows.
        rows = [dict(row) for row in payload.values() if isinstance(row, Mapping)]
        if rows:
            return rows
    return []


def _field_obligation_met(
    spec: ActionSpec, result: Mapping[str, Any],
) -> tuple[bool, str]:
    rows = _surface_rows(result, spec.output_object)
    if rows is None:
        return False, f"unobservable_in_result:{spec.output_object}"
    if not spec.output_field:
        return False, "output_field_unspecified"
    for row in rows:
        value = row.get(spec.output_field)
        if value not in (None, "", [], {}):
            return True, "field_obligation_satisfied"
    return False, "field_obligation_absent"


def evaluate_action_spec_postcondition(
    spec: ActionSpec,
    result: Mapping[str, Any],
) -> tuple[bool, str]:
    """Deterministically verify the spec's output obligation on a result.

    This is the D1 execution evidence: provider self-report (rule_effects)
    stays advisory telemetry, while a rule's declared postcondition is
    checked against the actual replay result.  Fail-closed — an obligation
    the result cannot expose is reported unsatisfied, never assumed met.
    """
    result = result if isinstance(result, Mapping) else {}
    postcondition = spec.postcondition
    if postcondition not in _POSTCONDITION_CHECKS:
        return False, f"unmapped_postcondition:{postcondition}"

    if postcondition in ("object_present", "object_absent"):
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        present = bool(rows)
        # The detail reports what was OBSERVED, so a failed obligation
        # carries the reason the check failed.
        satisfied = present if postcondition == "object_present" else not present
        return satisfied, ("object_present" if present else "object_absent")

    if postcondition in ("field_present", "field_nonempty"):
        satisfied, detail = _field_obligation_met(spec, result)
        return satisfied, detail

    if postcondition == "coverage_not_complete":
        proof = result.get("proof_adjudication") or {}
        if not isinstance(proof, Mapping):
            return False, "proof_adjudication_absent"
        prosecution = str(proof.get("prosecution_coverage") or "")
        defense = str(proof.get("defense_coverage") or "")
        satisfied = not (prosecution == "complete" and defense == "complete")
        return satisfied, (
            "coverage_not_complete" if satisfied else "coverage_complete"
        )

    if postcondition == "object_quarantined":
        proof = result.get("proof_adjudication") or {}
        if not isinstance(proof, Mapping):
            return False, "proof_adjudication_absent"
        # The quarantine obligation acts on the module's certificate
        # surface; the spec names the certificate-id FIELD, not a concrete
        # value, so the deterministic check is the side's quarantined set
        # being non-empty (the observable ``quarantined_certificates``
        # contract).  An empty set means the provider quarantined nothing.
        quarantined_ids_key = {
            "error_certificate": "quarantined_error_certificate_ids",
            "verification_certificate": "quarantined_error_certificate_ids",
            "defense_certificate_or_boundary": (
                "quarantined_defense_certificate_ids"
            ),
        }.get(spec.output_object)
        if quarantined_ids_key is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        quarantined = proof.get(quarantined_ids_key) or []
        satisfied = (
            isinstance(quarantined, list) and len(quarantined) > 0
        )
        return satisfied, (
            "object_quarantined" if satisfied else "object_not_quarantined"
        )

    if postcondition == "causal_status_present":
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        satisfied = bool(rows)
        return satisfied, (
            "causal_findings_present" if satisfied else "no_causal_findings"
        )

    if postcondition == "route_assignment_present":
        # The candidate was routed to a verifier: the qualification surface
        # must carry assignment rows (candidate + responsibility states).
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        satisfied = any(
            str(row.get("candidate_id") or "")
            and any(
                str(row.get(field) or "")
                for field in ("violation_state", "actor_state", "boundary_state")
            )
            for row in rows
        )
        return satisfied, (
            "route_assignment_present" if satisfied else "route_assignment_absent"
        )

    if postcondition in ("qualification_positive", "qualification_not_positive"):
        # The qualification declares whether a candidate passed to
        # verification: positive = at least one verified_violation row.
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        positive = any(
            str(row.get("violation_state") or "") == "verified_violation"
            for row in rows
        )
        satisfied = positive if postcondition == "qualification_positive" else not positive
        return satisfied, (
            "qualification_positive" if satisfied else "qualification_not_positive"
        )

    if postcondition == "verification_state_present":
        # The atomic verification produced a verdict state on the
        # certificate surface: at least one certificate carries a causal
        # status from the closed vocabulary.
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        satisfied = any(
            str(row.get("benchmark_causal_status") or "") in {
                "causes_incorrect_result",
                "propagates_to_incorrect_result",
                "process_only",
                "unresolved",
            }
            for row in rows
        )
        return satisfied, (
            "verification_state_present"
            if satisfied else "verification_state_absent"
        )

    if postcondition == "responsible_origin_present":
        responsible = result.get("responsible_event_id")
        satisfied = bool(responsible)
        return satisfied, (
            "responsible_origin_present"
            if satisfied else "responsible_origin_absent"
        )

    if postcondition in ("origin_not_selected", "later_origin_not_selected"):
        # The rejection/suppression outcome is observable as the origin
        # selection decision itself: pairwise counterfactual rankings were
        # produced and the court selected one responsible origin (the
        # rejected or later one is by construction not the winner).  The
        # spec cannot carry a concrete rejected id (its target is the
        # responsible_event_id FIELD), so the strongest deterministic claim
        # is the selection evidence.
        rankings = result.get("counterfactual_rankings")
        rankings_used = (
            isinstance(rankings, Mapping) and len(rankings) > 0
        )
        responsible = bool(result.get("responsible_event_id"))
        satisfied = rankings_used and responsible
        return satisfied, (
            "origin_selection_evidence_present"
            if satisfied else "origin_selection_evidence_absent"
        )

    return False, f"unmapped_postcondition:{postcondition}"


def classify_execution_evidence(
    spec: ActionSpec | None,
    result: Mapping[str, Any],
    observable_moved: bool,
) -> dict[str, Any]:
    """Classify the execution evidence of one edit on one replay result.

    The closed execution classes implement the D1 acceptance taxonomy:
    an edit that moved an observable output either satisfied its declared
    output obligation (``postcondition_satisfied``) or did not
    (``postcondition_not_satisfied``); an edit with no observable movement
    reports ``no_observable_movement``; an edit without an ActionSpec
    reports ``action_spec_absent``.
    """
    if spec is None:
        return {
            "execution_class": "action_spec_absent",
            "postcondition_satisfied": False,
            "postcondition_detail": "action_spec_absent",
        }
    satisfied, detail = evaluate_action_spec_postcondition(spec, result)
    if not observable_moved:
        execution_class = "no_observable_movement"
    else:
        execution_class = (
            "postcondition_satisfied"
            if satisfied
            else "postcondition_not_satisfied"
        )
    return {
        "execution_class": execution_class,
        "postcondition_satisfied": satisfied,
        "postcondition_detail": detail,
    }
