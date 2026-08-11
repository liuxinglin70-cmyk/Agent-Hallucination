"""Executable L1 intervention specifications.

Natural-language conditions remain the generalization surface, while every
action is compiled from the module/action vocabulary into an explicit provider
route, schema object, output field and postcondition.  Reflection therefore
does not invent execution semantics or a separate expected-effect story.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .certificate_identity import certificate_carrier_movement
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
    # ``verification_certificate`` and its observable is the proof court's
    # prosecution-side certificate surface.  Defense routes therefore cannot
    # execute its actions coherently — a withhold rule injected there makes
    # the defender decline its own certificates, which collapses defense
    # coverage on a gold-clean case and moves the module score against the
    # gold polarity (observed on SmolAgents/029: defense 1.0->0.3,
    # local_score 5.3->7.0 on an FP).  Defense-side verification stays owned
    # by ``clean_defense_boundary``.
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
    "enforce_certificate_invariant": "certificate_invariant_repaired",
    "emit_error_certificate": "object_present",
    "keep_coverage_incomplete": "coverage_not_complete",
    "route_candidate_to_verifier": "route_assignment_present",
    "qualify_candidate": "qualification_positive",
    "hold_candidate_qualification": "qualification_not_positive",
    "verify_violation": "verification_state_present",
    # First-principles fix B: ``withhold_certificate`` semantics are
    # fingerprint-targeted (the rule removes ONE certificate matching its
    # condition), not whole-surface (all surviving certificates gone).
    # The old ``error_certificate_absent`` postcondition required
    # ``len(surviving_error_certificate_ids) == 0`` which is unreachable
    # whenever multiple certificates carry the verdict and the rule only
    # withholds one — the verifier faithfully withheld its targeted
    # certificate yet the postcondition still reported
    # ``error_certificate_survived`` and the runner demoted the edit to
    # ``intervention_not_executed`` even though the proof court's
    # surviving set shrank (reproduced in probe_fp_run1).
    # ``targeted_certificate_withheld`` evaluates the rule's actual effect
    # against any prior surviving id set the postcondition caller can
    # supply (verifier replay vs the source baseline), and otherwise falls
    # back to the strict whole-surface check for single-carrier cases.
    "withhold_certificate": "targeted_certificate_withheld",
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
    "atomic_verifier_instruction": ("prosecution_certificates", "increase"),
    "clean_defense_boundary": ("defense_certificates", "increase"),
    "causal_link_test": ("causal_field_coverage", "increase"),
    "earliest_origin_tiebreak": ("responsible_step", "shift"),
    "abstention_rule": ("state", "shift"),
}

_OPERATION_EFFECT: Mapping[str, tuple[str, str]] = {
    "suppress_candidate": ("candidate_count", "decrease"),
    "keep_coverage_incomplete": ("prosecution_coverage", "opened"),
    "hold_candidate_qualification": ("prosecution_certificates", "decrease"),
    # Claim-level carrier movement, not the whole-surface count: a withhold
    # removes ONE surviving claim, and positional certificate ids are
    # re-minted on every provider re-sampling, so only the content
    # fingerprint surviving set measures the rule's actual effect
    # (certificate_identity.py).
    "withhold_certificate": ("surviving_certificates", "decrease"),
    "reject_non_causal_origin": ("responsible_step", "removed"),
    "suppress_later_origin": ("responsible_step", "shift"),
}

_MODULE_OPERATION_EFFECT: Mapping[tuple[str, str], tuple[str, str]] = {
    (
        "atomic_verifier_instruction", "require_evidence_field",
    ): ("causal_field_coverage", "increase"),
    # An emitted error certificate is observable as a NEW claim-level
    # carrier in the surviving set (same fingerprint identity as
    # withhold_certificate), not as a whole-surface count shift.
    (
        "atomic_verifier_instruction", "emit_certificate",
    ): ("surviving_certificates", "increase"),
    (
        "atomic_verifier_instruction", "enforce_certificate_invariant",
    ): ("target_certificate_invariant_failure", "decrease"),
    (
        "clean_defense_boundary", "enforce_certificate_invariant",
    ): ("target_certificate_invariant_failure", "decrease"),
}

# Operations whose runtime effect can move an over-conviction toward clean.
# Generation guidance and admission validation consume this same table so the
# model cannot be told that an operation is legal while the gate rejects it.
ERROR_TO_CLEAN_ACTIONS: Mapping[str, frozenset[str]] = {
    "evidence_obligation": frozenset({"keep_coverage_incomplete"}),
    "atomic_verifier_instruction": frozenset({"withhold_certificate"}),
    "clean_defense_boundary": frozenset({
        "emit_defense_certificate", "emit_boundary_finding",
        "require_evidence_field", "enforce_certificate_invariant",
    }),
    "abstention_rule": frozenset({"emit_boundary_finding"}),
    "causal_link_test": frozenset({"reject_non_causal_origin"}),
    "candidate_trigger": frozenset({"suppress_candidate"}),
    "candidate_to_verifier_route": frozenset({"hold_candidate_qualification"}),
}

CLEAN_TO_ERROR_ACTIONS: Mapping[str, frozenset[str]] = {
    "candidate_trigger": frozenset({"emit_candidate", "emit_origin_census"}),
    "evidence_obligation": frozenset({
        "require_evidence_field", "populate_evidence_field",
        "emit_error_certificate",
    }),
    "candidate_to_verifier_route": frozenset({
        "route_candidate_to_verifier", "qualify_candidate",
    }),
    "atomic_verifier_instruction": frozenset({
        "verify_violation", "emit_certificate", "require_evidence_field",
        "enforce_certificate_invariant",
    }),
    "causal_link_test": frozenset({"test_causal_link", "populate_impact_chain"}),
    "earliest_origin_tiebreak": frozenset({"select_earliest_origin"}),
}


def render_direction_action_contract(
    direction: str, modules: Sequence[str],
) -> str:
    """Render the executable opcode subset for one task direction."""
    if direction in {"error_to_clean", "unresolved_to_clean"}:
        contract = ERROR_TO_CLEAN_ACTIONS
    elif direction in {"clean_to_error", "unresolved_to_error"}:
        contract = CLEAN_TO_ERROR_ACTIONS
    else:
        return ""
    lines = []
    for module in modules:
        actions = sorted(contract.get(str(module), frozenset()))
        if actions:
            lines.append(f"- {module}: " + ", ".join(actions))
    return "\n".join(lines)


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
    "error_certificate_absent",
    "targeted_certificate_withheld",
    "causal_status_present",
    "origin_not_selected",
    "responsible_origin_present",
    "later_origin_not_selected",
    "route_assignment_present",
    "qualification_positive",
    "qualification_not_positive",
    "verification_state_present",
    "certificate_invariant_repaired",
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


def _surface_projection(
    result: Mapping[str, Any],
    output_object: str,
    fields: Sequence[str],
) -> tuple[tuple[str, ...], ...] | None:
    """Project an observable surface into a stable before/after signature."""
    rows = _surface_rows(result, output_object)
    if rows is None:
        return None
    return tuple(sorted(
        tuple(str(row.get(field) or "") for field in fields)
        for row in rows
    ))


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


def _field_obligation_count(
    spec: ActionSpec, result: Mapping[str, Any],
) -> int | None:
    rows = _surface_rows(result, spec.output_object)
    if rows is None:
        return None
    if not spec.output_field:
        return None
    return sum(
        1
        for row in rows
        if row.get(spec.output_field) not in (None, "", [], {})
    )


def _failed_invariant_count(
    result: Mapping[str, Any], module_id: str, invariant: str,
) -> int | None:
    """Count proof-court audits that fail one named certificate invariant.

    The proof court owns validation truth.  Reflection therefore measures an
    invariant repair against court audits instead of pretending the invariant
    is a provider JSON field.
    """
    proof = result.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        return None
    audit_key = (
        "defense_audits"
        if module_id == "clean_defense_boundary" else "error_audits"
    )
    audits = proof.get(audit_key)
    if not isinstance(audits, list):
        return None
    return sum(
        1
        for audit in audits
        if isinstance(audit, Mapping)
        and invariant in {
            str(item) for item in audit.get("failed_checks") or ()
        }
    )


def evaluate_action_spec_postcondition(
    spec: ActionSpec,
    result: Mapping[str, Any],
    *,
    baseline_surviving_ids: Sequence[str] | None = None,
    baseline_result: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Deterministically verify the spec's output obligation on a result.

    This is the D1 execution evidence: provider self-report (rule_effects)
    stays advisory telemetry, while a rule's declared postcondition is
    checked against the actual replay result.  Fail-closed — an obligation
    that the result cannot expose is reported unsatisfied, never assumed met.

    ``baseline_result`` carries the full source-baseline detection result
    (BEFORE the candidate genome ran).  The ``withhold_certificate``
    postcondition then compares CLAIM-LEVEL carrier fingerprints
    (``certificate_identity.certificate_carrier_movement``): the rule
    executed iff at least one baseline carrier claim is no longer surviving
    in the replay.  Positional certificate ids are re-minted on every
    provider re-sampling (appeal_error_* <-> initial_error_*), so an id-set
    comparison reads a pure rename as a removal; the fingerprint comparison
    is rename-invariant.

    ``baseline_surviving_ids`` is the legacy fallback: only the baseline id
    list, without the rows needed to resolve claim identity.  The
    comparison then degrades to the id-set form, which cannot separate a
    rename from a withhold and is retained only for callers that have no
    baseline result.  When no baseline at all is available, the
    postcondition falls back to the strict whole-surface form (zero
    surviving certificates — the only honest verdict-level claim a
    single-carrier ``withhold_certificate`` can make without a delta).
    """
    result = result if isinstance(result, Mapping) else {}
    postcondition = spec.postcondition
    if postcondition not in _POSTCONDITION_CHECKS:
        return False, f"unmapped_postcondition:{postcondition}"

    if postcondition in ("object_present", "object_absent"):
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        if baseline_result is None:
            return False, "baseline_result_required_for_object_transition"
        before_rows = _surface_rows(baseline_result, spec.output_object)
        if before_rows is None:
            return False, f"baseline_unobservable:{spec.output_object}"
        before_count, after_count = len(before_rows), len(rows)
        if postcondition == "object_present":
            satisfied = after_count > before_count
            return satisfied, (
                f"object_added:{before_count}->{after_count}"
                if satisfied else f"object_not_added:{before_count}->{after_count}"
            )
        satisfied = after_count < before_count
        return satisfied, (
            f"object_removed:{before_count}->{after_count}"
            if satisfied else f"object_not_removed:{before_count}->{after_count}"
        )

    if postcondition in ("field_present", "field_nonempty"):
        satisfied, detail = _field_obligation_met(spec, result)
        if not satisfied:
            return False, detail
        if baseline_result is None:
            return False, "baseline_result_required_for_field_transition"
        before_count = _field_obligation_count(spec, baseline_result)
        after_count = _field_obligation_count(spec, result)
        if before_count is None or after_count is None:
            return False, "field_obligation_delta_unobservable"
        if after_count <= before_count:
            return False, (
                f"field_obligation_not_added:{spec.output_field}:"
                f"{before_count}->{after_count}"
            )
        return True, (
            f"field_obligation_added:{spec.output_field}:"
            f"{before_count}->{after_count}"
        )

    if postcondition == "certificate_invariant_repaired":
        if baseline_result is None:
            return False, "baseline_result_required_for_invariant_repair"
        before = _failed_invariant_count(
            baseline_result, spec.module_id, spec.output_field,
        )
        after = _failed_invariant_count(
            result, spec.module_id, spec.output_field,
        )
        if before is None or after is None:
            return False, "certificate_audits_unreported"
        satisfied = before > after
        return satisfied, (
            f"certificate_invariant_repaired:{spec.output_field}:{before}->{after}"
            if satisfied else
            f"certificate_invariant_not_repaired:{spec.output_field}:{before}->{after}"
        )

    if postcondition == "coverage_not_complete":
        proof = result.get("proof_adjudication") or {}
        if not isinstance(proof, Mapping) or not proof:
            return False, "proof_adjudication_absent"
        if baseline_result is None:
            return False, "baseline_result_required_for_coverage_transition"
        baseline_proof = baseline_result.get("proof_adjudication") or {}
        if not isinstance(baseline_proof, Mapping) or not baseline_proof:
            return False, "baseline_proof_adjudication_absent"
        before = str(baseline_proof.get("prosecution_coverage") or "")
        after = str(proof.get("prosecution_coverage") or "")
        # ``keep_coverage_incomplete`` is owned by the prosecution-side
        # evidence module. Defense incompleteness is a foreign surface and
        # cannot witness that this action executed.
        satisfied = before == "complete" and after != "complete"
        return satisfied, (
            f"prosecution_coverage_opened:{before}->{after}"
            if satisfied else
            f"prosecution_coverage_not_opened:{before}->{after}"
        )

    if postcondition == "error_certificate_absent":
        proof = result.get("proof_adjudication")
        if not isinstance(proof, Mapping) or not proof:
            return False, "proof_adjudication_absent"
        # Withholding is the atomic verifier's error_to_clean repair: the
        # verifier declines to certify, so no error certificate survives the
        # court.  The observable is the surviving set, NOT the quarantined
        # set — quarantine is the court's audit rejecting a malformed
        # artifact, a provider-corruption signal that no rule should aim to
        # produce.  A withheld certificate leaves the prosecution surface
        # empty; a certificate that survives means the verifier still
        # certified and the repair did not run.  A court that never reported
        # the surviving set cannot witness the obligation, so it fails
        # closed rather than reading absence as success.
        surviving = proof.get("surviving_error_certificate_ids")
        if not isinstance(surviving, list):
            return False, "surviving_certificates_unreported"
        if baseline_result is None:
            return False, "baseline_result_required_for_certificate_removal"
        baseline_proof = baseline_result.get("proof_adjudication")
        baseline_surviving = (
            baseline_proof.get("surviving_error_certificate_ids")
            if isinstance(baseline_proof, Mapping) else None
        )
        if not isinstance(baseline_surviving, list):
            return False, "baseline_surviving_certificates_unreported"
        satisfied = bool(baseline_surviving) and len(surviving) == 0
        return satisfied, (
            "error_certificate_absent" if satisfied
            else "error_certificate_survived"
        )

    if postcondition == "targeted_certificate_withheld":
        # ``withhold_certificate`` semantics are fingerprint-targeted — the
        # rule removes ONE certificate matching its triggering condition.
        # The strongest deterministic claim is before-vs-after on the
        # court's surviving set.  With a full baseline result the comparison
        # runs on claim-level fingerprints (rename-invariant); with only a
        # baseline id list it degrades to the positional id-set form, which
        # cannot separate a rename from a removal; with no baseline at all
        # it degrades to the whole-surface form (zero surviving
        # certificates), which is correct for single-carrier cases but
        # unreachable for a single rule in a multi-carrier case — exactly
        # why callers must thread the baseline through.
        proof = result.get("proof_adjudication")
        if not isinstance(proof, Mapping) or not proof:
            return False, "proof_adjudication_absent"
        surviving = proof.get("surviving_error_certificate_ids")
        if not isinstance(surviving, list):
            return False, "surviving_certificates_unreported"

        if baseline_result is not None:
            # Claim-level authority path.  A pure positional rename yields
            # zero withheld fingerprints, so the postcondition fails closed
            # exactly when the provider re-minted ids without removing any
            # claim — the drift pattern the id-set form misread as success.
            # An unresolvable carrier (row truncated from the merge surface)
            # fails closed as well: an identity that cannot be verified is
            # never credited as removed.
            movement = certificate_carrier_movement(baseline_result, result)
            if movement is None:
                return False, "carrier_identity_unreported"
            if not movement["resolved"]:
                return False, "carrier_identity_unresolved"
            withheld = movement["withheld"]
            if withheld:
                return True, (
                    f"targeted_certificate_withheld:{len(withheld)}_claim(s)"
                )
            return False, "no_baseline_carrier_withheld"

        surviving_set = {str(value) for value in surviving}
        if baseline_surviving_ids is not None:
            baseline_set = {str(value) for value in baseline_surviving_ids}
            withheld = baseline_set - surviving_set
            if withheld:
                return True, (
                    "targeted_certificate_withheld:"
                    + ",".join(sorted(withheld)[:8])
                )
            if not baseline_set:
                # No baseline carrier to begin with: the rule fires only if
                # the replay verifier still emitted no certificates for the
                # targeted fingerprint observable.  Fall back to the
                # whole-surface check on the replay side.
                satisfied = len(surviving_set) == 0
                return satisfied, (
                    "targeted_certificate_withheld"
                    if satisfied else "rule_did_not_remove_any_carrier"
                )
            return False, (
                "no_baseline_carrier_withheld:"
                + ",".join(sorted(baseline_set & surviving_set)[:8])
            )
        # No baseline supplied: fall back to the strict whole-surface form.
        satisfied = len(surviving_set) == 0
        return satisfied, (
            "targeted_certificate_withheld"
            if satisfied else "error_certificate_survived"
        )

    if postcondition == "causal_status_present":
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        if baseline_result is None:
            return False, "baseline_result_required_for_causal_transition"
        fields = (
            "candidate_id", "source_candidate_id", "origin_event_id",
            "propagation_state", "benchmark_causal_status",
            "causal_sufficiency_score", "is_root_cause",
        )
        before = _surface_projection(
            baseline_result, spec.output_object, fields,
        )
        after = _surface_projection(result, spec.output_object, fields)
        if before is None or after is None:
            return False, "causal_surface_unobservable"
        satisfied = bool(rows) and after != before
        return satisfied, (
            "causal_surface_changed" if satisfied else "causal_surface_unchanged"
        )

    if postcondition == "route_assignment_present":
        # The candidate was routed to a verifier: the qualification surface
        # must carry assignment rows (candidate + responsibility states).
        rows = _surface_rows(result, spec.output_object)
        if rows is None:
            return False, f"unobservable_in_result:{spec.output_object}"
        present = any(
            str(row.get("candidate_id") or "")
            and any(
                str(row.get(field) or "")
                for field in ("violation_state", "actor_state", "boundary_state")
            )
            for row in rows
        )
        if baseline_result is None:
            return False, "baseline_result_required_for_route_transition"
        fields = (
            "candidate_id", "violation_state", "actor_state", "boundary_state",
        )
        before = _surface_projection(
            baseline_result, spec.output_object, fields,
        )
        after = _surface_projection(result, spec.output_object, fields)
        satisfied = present and before is not None and after != before
        return satisfied, (
            "route_assignment_changed" if satisfied else "route_assignment_unchanged"
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
        if baseline_result is None:
            return False, "baseline_result_required_for_qualification_transition"
        before_rows = _surface_rows(baseline_result, spec.output_object)
        if before_rows is None:
            return False, "baseline_qualification_surface_unobservable"
        before_positive = any(
            str(row.get("violation_state") or "") == "verified_violation"
            for row in before_rows
        )
        satisfied = (
            (not before_positive and positive)
            if postcondition == "qualification_positive"
            else (before_positive and not positive)
        )
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
        present = any(
            str(row.get("benchmark_causal_status") or "") in {
                "causes_incorrect_result",
                "propagates_to_incorrect_result",
                "process_only",
                "unresolved",
            }
            for row in rows
        )
        if baseline_result is None:
            return False, "baseline_result_required_for_verification_transition"
        fields = (
            "source_candidate_id", "origin_event_id", "benchmark_causal_status",
        )
        before = _surface_projection(
            baseline_result, spec.output_object, fields,
        )
        after = _surface_projection(result, spec.output_object, fields)
        satisfied = present and before is not None and after != before
        return satisfied, (
            "verification_state_present"
            if satisfied else "verification_state_absent"
        )

    if postcondition == "responsible_origin_present":
        responsible = result.get("responsible_event_id")
        if baseline_result is None:
            return False, "baseline_result_required_for_origin_transition"
        before = baseline_result.get("responsible_event_id")
        satisfied = bool(responsible) and responsible != before
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
        if baseline_result is None:
            return False, "baseline_result_required_for_origin_transition"
        before = baseline_result.get("responsible_event_id")
        after = result.get("responsible_event_id")
        satisfied = bool(before) and after != before
        return satisfied, (
            f"responsible_origin_changed:{before}->{after}"
            if satisfied else f"responsible_origin_unchanged:{before}->{after}"
        )

    return False, f"unmapped_postcondition:{postcondition}"


def classify_execution_evidence(
    spec: ActionSpec | None,
    result: Mapping[str, Any],
    observable_moved: bool,
    *,
    baseline_surviving_ids: Sequence[str] | None = None,
    baseline_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the execution evidence of one edit on one replay result.

    The closed execution classes implement the D1 acceptance taxonomy:
    an edit whose declared output obligation holds on the result reports
    ``postcondition_satisfied``; one whose obligation fails reports
    ``postcondition_not_satisfied``; an edit without an ActionSpec reports
    ``action_spec_absent``.

    The postcondition is evaluated whenever a spec is present.  It reads the
    result surface directly, so it is the authoritative evidence and must
    not be gated behind ``observable_moved`` — that flag comes from matching
    contract tokens against the delta vocabulary, and a lexical miss there
    would otherwise discard a verified result-level obligation.  The flag is
    still reported, because an obligation that holds without any observable
    movement may have already held before the edit.
    """
    if spec is None:
        return {
            "execution_class": "action_spec_absent",
            "postcondition_satisfied": False,
            "postcondition_detail": "action_spec_absent",
            "observable_moved": bool(observable_moved),
        }
    satisfied, detail = evaluate_action_spec_postcondition(
        spec,
        result,
        baseline_surviving_ids=baseline_surviving_ids,
        baseline_result=baseline_result,
    )
    execution_class = (
        "postcondition_satisfied"
        if satisfied
        else "postcondition_not_satisfied"
    )
    return {
        "execution_class": execution_class,
        "postcondition_satisfied": satisfied,
        "postcondition_detail": detail,
        "observable_moved": bool(observable_moved),
    }
