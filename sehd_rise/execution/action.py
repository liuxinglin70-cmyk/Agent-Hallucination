"""Deterministic execution evidence: action-spec postcondition evaluation.

A rule's declared postcondition is checked against the actual replay
result. This is the D1 acceptance taxonomy: an obligation that the
result cannot expose is reported unsatisfied, never assumed met.

The ``ActionSpec`` data model and the opcode/postcondition/effect tables
live in ``skills/action.py``; this module owns only the result-surface
readers and the postcondition classifiers.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise.skills.action import ActionSpec
from sehd_rise.detection.court.identity import (
    certificate_carrier_movement,
    certificate_claim_fingerprint,
    surviving_carrier_fingerprints,
)


# postcondition -> result-observable semantics.  A postcondition with no
# mapping here cannot be verified against the final detection result and
# evaluates fail-closed to ``unmapped_postcondition``: an obligation that
# the result schema does not expose is never silently treated as met.
POSTCONDITION_CHECKS = frozenset({
    "object_present",
    "object_absent",
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
    "candidate_materialized_for_bound_anchor",
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
    # shadow a populated later one (the paca_census surface may be empty
    # while the construct_census surface carries the rows).
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


def evaluate_action_spec_postcondition(
    spec: ActionSpec,
    result: Mapping[str, Any],
    *,
    baseline_surviving_ids: Sequence[str] | None = None,
    baseline_result: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Deterministically verify the spec's output obligation on a result.

    A rule's declared postcondition is checked against the actual replay
    result. Fail-closed — an obligation that the result cannot expose is
    reported unsatisfied, never assumed met.

    ``baseline_result`` carries the full source-baseline detection result
    (BEFORE the candidate genome ran).  The ``withhold_certificate``
    postcondition then compares CLAIM-LEVEL carrier fingerprints
    (``certificate_identity.certificate_carrier_movement``): the rule
    executed iff at least one baseline carrier claim is no longer surviving
    in the replay.  Positional certificate ids are re-minted on every
    provider re-sampling (appeal_error_* <-> initial_error_*), so an id-set
    comparison reads a pure rename as a removal; the fingerprint comparison
    is rename-invariant.

    ``baseline_surviving_ids`` carries only the baseline id list, without
    the rows needed to resolve claim identity.  The comparison then
    degrades to the id-set form, which cannot separate a rename from a
    withhold.  When no baseline at all is available, the postcondition falls
    back to the strict whole-surface form (zero surviving certificates —
    the only honest verdict-level claim a single-carrier
    ``withhold_certificate`` can make without a delta).
    """
    result = result if isinstance(result, Mapping) else {}
    postcondition = spec.postcondition
    if postcondition not in POSTCONDITION_CHECKS:
        return False, f"unmapped_postcondition:{postcondition}"

    if postcondition == "candidate_materialized_for_bound_anchor":
        proofs = result.get("l1_execution")
        proof_rows = (
            proofs.get("constructive_execution_proofs")
            if isinstance(proofs, Mapping) else None
        )
        if not isinstance(proof_rows, list):
            return False, "constructive_execution_proof_absent"
        matched = [
            row for row in proof_rows
            if isinstance(row, Mapping)
            and row.get("constructor_invoked") is True
            and row.get("constructed_candidate_id")
            and row.get("candidate_anchor_id") == row.get("source_anchor_id")
        ]
        return bool(matched), (
            "candidate_materialized_for_bound_anchor"
            if matched else "candidate_not_materialized_for_bound_anchor"
        )

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


__all__ = [
    "POSTCONDITION_CHECKS",
    "certificate_carrier_movement",
    "certificate_claim_fingerprint",
    "classify_execution_evidence",
    "evaluate_action_spec_postcondition",
    "surviving_carrier_fingerprints",
]
