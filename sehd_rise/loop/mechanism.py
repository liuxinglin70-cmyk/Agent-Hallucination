"""FailureMechanismClassifier: FailureObservation -> FN family.

The forward-recall families share owner modules (``candidate_trigger`` hosts
four of them), so the family can never be the first table match for an
owner. This classifier descends the observation instead: reason_code proves
forward recall, the owner selects the family candidate set, and closed
mechanism evidence — the causal edge identity, cross-claim dependency refs,
missing/malformed fields, and the label-free trajectory locus hints — picks
the family. The owner table only supplies the fallback for a plain
observation, which is exactly ``missing_claim_candidate`` and its per-owner
analogues, not a wrong-family collapse.

The family vocabulary and the owner coverage surface stay in
``skills.capabilities``; this module is the loop-layer consumer that turns a
typed observation into the curriculum's family label.
"""

from __future__ import annotations

from typing import Callable

from sehd_rise.causal.cut import REASON_GOLD_FALSE_NEGATIVE_CLEAN
from sehd_rise.learning.protocol import FailureObservation
from sehd_rise.skills.anchors import NUMERIC_LOCUS
from sehd_rise.skills.capabilities import (
    DEFAULT_FN_FAMILY_BY_OWNER,
    fn_families_for_owner,
)


def _tool_grounded(observation: FailureObservation) -> bool:
    return any(
        hint.startswith("tool_result_") for hint in observation.trajectory_hints
    )


def _cross_claim(observation: FailureObservation) -> bool:
    return bool(observation.dependency_refs)


def _numeric_locus(observation: FailureObservation) -> bool:
    return NUMERIC_LOCUS in observation.trajectory_hints


def _evidence_acquisition(observation: FailureObservation) -> bool:
    return bool(observation.missing_fields or observation.dependency_refs)


def _coverage_blind_spot(observation: FailureObservation) -> bool:
    return "coverage" in str(observation.blocking_predicate).lower()


def _missing_route(observation: FailureObservation) -> bool:
    return observation.candidate_kind in ("", "unknown")


def _missing_certificate(observation: FailureObservation) -> bool:
    return (
        "certificate_id" in observation.missing_fields
        or observation.output_object == "verification_certificate"
    )


# Specificity-ordered matchers: a family only fires for owners that host it
# (checked by the caller), so the global order encodes "tool grounding beats
# cross-claim beats numeric locus" without per-owner branches.
_FAMILY_MATCHERS: tuple[tuple[str, Callable[[FailureObservation], bool]], ...] = (
    ("tool_grounding_requirement", _tool_grounded),
    ("cross_claim_dependency", _cross_claim),
    ("temporal_numeric_entity_consistency", _numeric_locus),
    ("missing_evidence_acquisition", _evidence_acquisition),
    ("coverage_blind_spot", _coverage_blind_spot),
    ("missing_verifier_route", _missing_route),
    ("missing_certificate_path", _missing_certificate),
)


def classify_failure_mechanism(observation: FailureObservation) -> str:
    """Map one failure observation onto its FN family, or "" for non-FN.

    Deterministic and pure: the same observation always yields the same
    family. Non-forward-recall shapes and owners outside the declared FN
    surface get no family, so the curriculum never attaches a wrong label.
    """
    if observation.reason_code != REASON_GOLD_FALSE_NEGATIVE_CLEAN:
        return ""
    families = fn_families_for_owner(observation.failure_owner)
    if not families:
        return ""
    for family, matcher in _FAMILY_MATCHERS:
        if family in families and matcher(observation):
            return family
    return DEFAULT_FN_FAMILY_BY_OWNER.get(observation.failure_owner, families[0])


__all__ = [
    "classify_failure_mechanism",
]
