"""Learning Case schema: one detector failure as a reviewable record.

A learning case is the EVALUATOR-SIDE joining of three single sources of
truth — the frozen run tree (detector facts), the dataset record (gold),
and the human annotation layer (``annotations.json``) — into one file
per case. It contains gold labels and therefore must never travel into
the detector input path: ``packet.validate_record`` rejects any record
carrying ``EVALUATOR_ONLY_FIELDS``, and no evolution-side code may hand
a learning case to the detector.

The annotation content is layered per the Phase 0 review
(failure fact -> failure hypothesis -> capability gap), so a reader can
always tell facts from interpretation:

- ``failure_analysis``    FACTS. Mechanical derivation of failure type,
  stage and the fact sentence; a human may override the stage only with
  a recorded reason.
- ``failure_hypothesis``  HYPOTHESIS. The annotator's working hypothesis
  and its evidence basis (e.g. for a defense-overturn FN: was the
  certificate itself about the gold error, or did the census mis-target
  it and defense rightly overturned?).
- ``capability_analysis`` ABSTRACTION. The capability gap, constrained
  by the vocabulary in gap_taxonomy.py (proposals allowed while the
  taxonomy is unfrozen).
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise2.evolution.diagnosis import (
    FAILED_STAGES,
    GENERALIZATIONS,
    classify_failure,
    prediction_from_state,
)
from sehd_rise2.evolution.gap_taxonomy import (
    GAP_CATEGORIES,
    gap_allowed,
)

LEARNING_CASE_SCHEMA = "sehd2.learning_case.v1"
ANNOTATIONS_SCHEMA = "sehd2.learning_annotations.v1"
FAILURE_CLASSES = frozenset({"FN", "FP", "LOCALIZATION"})


def reclassify(case: Mapping[str, Any]) -> str:
    """Recompute the failure class of an assembled learning case."""
    ground = case.get("ground_truth")
    ground = ground if isinstance(ground, Mapping) else {}
    execution = case.get("detector_execution")
    execution = execution if isinstance(execution, Mapping) else {}
    return classify_failure(
        gold=ground.get("hallucination_label"),
        prediction=prediction_from_state(execution.get("final_decision")),
        gold_step=ground.get("gold_step"),
        responsible_step=execution.get("responsible_step"),
    )


def _gap_of(case: Mapping[str, Any]) -> Mapping[str, Any]:
    capability = case.get("capability_analysis")
    capability = capability if isinstance(capability, Mapping) else {}
    gap = capability.get("capability_gap")
    return gap if isinstance(gap, Mapping) else {}


def validate_learning_case(case: Mapping[str, Any]) -> dict[str, bool]:
    """Validate one assembled learning case; all checks must pass."""
    failure_type = reclassify(case)
    is_failure = failure_type in FAILURE_CLASSES
    analysis = case.get("failure_analysis")
    analysis = analysis if isinstance(analysis, Mapping) else {}
    hypothesis = case.get("failure_hypothesis")
    hypothesis = hypothesis if isinstance(hypothesis, Mapping) else None
    capability = case.get("capability_analysis")
    capability = capability if isinstance(capability, Mapping) else None
    gap = _gap_of(case)
    category = str(gap.get("category") or "")
    hypothesis_text = (
        str((hypothesis or {}).get("initial_hypothesis") or "")
    )
    override = analysis.get("failed_stage_source") == "human_override"
    return {
        "schema_version_known": (
            case.get("schema_version") == LEARNING_CASE_SCHEMA
        ),
        "case_id_present": bool(str(case.get("case_id") or "")),
        "failure_type_consistent": (
            str(analysis.get("failure_type") or "") == failure_type
        ),
        "failed_stage_in_vocabulary": (
            analysis.get("failed_stage") in FAILED_STAGES
        ),
        "fact_layer_complete": (
            bool(str(analysis.get("failure_observation") or ""))
        ),
        "override_reason_present_when_overridden": (
            (not override) or bool(str(analysis.get("override_reason") or ""))
        ),
        "hypothesis_layer_ordered": (
            bool(hypothesis_text) if is_failure else hypothesis is None
        ),
        "capability_layer_ordered": (
            bool(gap) if is_failure else capability is None
        ),
        "capability_category_closed": (
            category in GAP_CATEGORIES if is_failure else True
        ),
        "capability_name_vocabulary": (
            gap_allowed(
                category, str(gap.get("name") or ""),
                status=str(gap.get("status") or "known"),
            ) if is_failure else True
        ),
        "generalization_in_vocabulary": (
            (capability or {}).get("generalization") in (
                None, *GENERALIZATIONS
            ) if is_failure else True
        ),
    }


def validate_annotations(payload: Mapping[str, Any]) -> dict[str, bool]:
    """Validate the annotation file envelope (per-case content is merged
    into learning cases and validated there)."""
    cases = payload.get("cases")
    cases = cases if isinstance(cases, Mapping) else {}
    return {
        "schema_version_known": (
            payload.get("schema_version") == ANNOTATIONS_SCHEMA
        ),
        "annotator_present": bool(str(payload.get("annotator") or "")),
        "case_ids_nonempty": all(
            bool(str(key)) for key in cases
        ),
        "review_status_known": payload.get("review_status") in {
            "first_pass", "human_reviewed",
        },
    }


__all__ = [
    "ANNOTATIONS_SCHEMA",
    "FAILURE_CLASSES",
    "LEARNING_CASE_SCHEMA",
    "reclassify",
    "validate_annotations",
    "validate_learning_case",
]
