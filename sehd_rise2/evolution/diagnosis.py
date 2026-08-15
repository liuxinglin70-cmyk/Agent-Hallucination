"""Mechanical failure classification and the Meta Skill diagnosis protocol.

Two contracts live here:

1. ``classify_failure`` / ``derive_failure_stage`` / ``localization_facts``
   — pure functions over the frozen run-tree facts. They answer WHERE the
   error was lost (a fact), never WHY (that is the hypothesis layer's
   job, and for defense-overturn cases the fact layer deliberately does
   NOT decide between "defense too lenient" and "certificate was wrong
   in the first place" — both are compatible with the same facts).
2. ``validate_capability_diagnosis`` — the Meta Skill output protocol
   ``sehd2.capability_diagnosis.v1``. Per the Phase 0 review the protocol
   carries NO free-floating confidence score: it carries
   ``diagnosis_source`` (mechanical | human | mixed), because an
   unanchored confidence would pollute later evaluation.

View discipline: the ONLINE Meta Skill must consume a label-free view;
the OFFLINE protocol defined here is gold-aligned by construction (the
gold-bearing container is the Learning Case, see learning_case.py).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise2.evolution.gap_taxonomy import (
    CATEGORY_EVOLUTION_TARGET,
    GAP_CATEGORIES,
    gap_allowed,
)

FAILURE_TYPES = frozenset({"FN", "FP", "LOCALIZATION", "CORRECT", "INFRA"})
FAILED_STAGES = frozenset({
    "census", "verification", "defense", "adjudication",
    "undetermined", "infrastructural", "none",
})
DIAGNOSIS_SOURCES = frozenset({"mechanical", "human", "mixed"})
GENERALIZATIONS = frozenset({"cross_case", "case_specific"})
DIAGNOSIS_SCHEMA = "sehd2.capability_diagnosis.v1"


def prediction_from_state(state: Any) -> bool | None:
    """Polarity of a terminal state; ``None`` when not decisive."""
    text = str(state or "")
    if text == "verified_error":
        return True
    if text == "verified_clean":
        return False
    return None


def classify_failure(
    *, gold: bool | None, prediction: bool | None,
    gold_step: int | None, responsible_step: int | None,
) -> str:
    """Failure class of one case against gold polarity and localization."""
    if prediction is None or gold is None:
        return "INFRA"
    if gold and not prediction:
        return "FN"
    if not gold and prediction:
        return "FP"
    if gold_step is not None and responsible_step != gold_step:
        return "LOCALIZATION"
    return "CORRECT"


def derive_failure_stage(
    *, failure_type: str, decisive_facts: Mapping[str, Any],
) -> tuple[str, str]:
    """Mechanical failure stage + the fact sentence that justifies it."""
    candidates = int(decisive_facts.get("candidate_count") or 0)
    supported = int(decisive_facts.get("supported_certificate_count") or 0)
    errors = int(decisive_facts.get("error_certificate_count") or 0)
    if failure_type == "INFRA":
        return (
            "infrastructural",
            "terminal state unresolved: no decisive verdict was produced",
        )
    if failure_type == "CORRECT":
        return ("none", "polarity and localization both match gold")
    if failure_type == "FN":
        if candidates == 0:
            return (
                "census",
                "census proposed no candidate; the gold error never "
                "entered the evidence pipeline",
            )
        if supported == 0:
            return (
                "verification",
                f"{candidates} candidate(s) proposed; every certificate "
                "rejected at verification",
            )
        if errors == 0:
            return (
                "defense",
                f"{supported} supported certificate(s) overturned at defense",
            )
        return (
            "undetermined",
            "gold error unconvicted despite a surviving certificate",
        )
    if failure_type == "FP":
        return (
            "undetermined",
            "conviction chain complete (census -> supported -> upheld); "
            "the failing link needs semantic attribution",
        )
    return ("adjudication", "polarity correct; responsible step != gold step")


def localization_facts(
    *, gold_step: int | None, loci: Sequence[Mapping[str, Any]],
) -> str:
    """Mechanical attribution hint for LOCALIZATION cases.

    ``loci`` items carry ``candidate_id``, ``position`` (event position
    of the candidate's locus) and ``upheld`` (bool). Distinguishes the
    three mechanically separable shapes: the gold step was an upheld
    locus that lost the earliest-locus selection; it was a candidate
    locus that died earlier in the chain; or no candidate ever pointed
    at it.
    """
    if gold_step is None:
        return "gold step unknown: localization cannot be scored"
    at_gold = [
        str(item.get("candidate_id")) for item in loci
        if int(item.get("position") or 0) == gold_step
    ]
    upheld_at_gold = [
        str(item.get("candidate_id")) for item in loci
        if int(item.get("position") or 0) == gold_step and item.get("upheld")
    ]
    if upheld_at_gold:
        return (
            f"gold step {gold_step} was an upheld locus "
            f"({','.join(upheld_at_gold)}) but the earliest-locus "
            "selection picked another step"
        )
    if at_gold:
        return (
            f"gold step {gold_step} was a candidate locus "
            f"({','.join(at_gold)}) that did not survive the chain"
        )
    return f"no candidate ever carried the gold step {gold_step} as locus"


def validate_capability_diagnosis(
    diagnosis: Mapping[str, Any],
) -> dict[str, bool]:
    """Validate one ``sehd2.capability_diagnosis.v1`` object."""
    gap = diagnosis.get("capability_gap")
    gap = gap if isinstance(gap, Mapping) else {}
    category = str(gap.get("category") or "")
    category_closed = category in GAP_CATEGORIES
    name_ok = gap_allowed(
        category, str(gap.get("name") or ""),
        status=str(gap.get("status") or "known"),
    )
    return {
        "schema_version_known": (
            diagnosis.get("schema_version") == DIAGNOSIS_SCHEMA
        ),
        "case_id_present": bool(str(diagnosis.get("case_id") or "")),
        "failure_type_in_vocabulary": (
            diagnosis.get("failure_type") in FAILURE_TYPES
        ),
        "failed_stage_in_vocabulary": (
            diagnosis.get("failed_stage") in FAILED_STAGES
        ),
        "diagnosis_source_in_vocabulary": (
            diagnosis.get("diagnosis_source") in DIAGNOSIS_SOURCES
        ),
        "capability_category_closed": category_closed,
        "capability_name_vocabulary": name_ok,
        "evolution_target_consistent": (
            diagnosis.get("recommended_evolution_target")
            == CATEGORY_EVOLUTION_TARGET.get(category)
        ),
        "generalization_in_vocabulary": (
            diagnosis.get("generalization") in (None, *GENERALIZATIONS)
        ),
    }


__all__ = [
    "DIAGNOSIS_SCHEMA",
    "DIAGNOSIS_SOURCES",
    "FAILED_STAGES",
    "FAILURE_TYPES",
    "GENERALIZATIONS",
    "classify_failure",
    "derive_failure_stage",
    "localization_facts",
    "prediction_from_state",
    "validate_capability_diagnosis",
]
