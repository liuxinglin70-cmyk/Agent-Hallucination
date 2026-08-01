"""BCCSE v5 — Curriculum-based case selection for online evolution.

Orders evolution cases by "learning edge" priority:
1. Unresolved cases first (medium difficulty — detector is uncertain)
2. Boundary resolved cases second (CRS near threshold)
3. High-confidence correct cases last (provide stable_success signal)

Case ordering is a prioritization hint only; commit safety is enforced by
structural validation and source replay.

Inspired by HIVE (ICML 2026): "strongest learning signals are concentrated
at the learning edge defined by the intersection of medium difficulty and
high response entropy."
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def order_evolution_cases(
    case_ids: Sequence[str],
    baseline_predictions: Mapping[str, Mapping[str, Any]],
    canary_ids: Sequence[str] | None = None,
    *,
    use_gold: bool = False,
) -> list[str]:
    """Order evolution cases by curriculum priority.

    Priority ordering:
    1. Unresolved cases (highest learning signal)
    2. Resolved but boundary cases (moderate signal)
    3. High-confidence correct cases (low signal, stable_success)


    Args:
        case_ids: All evolution case IDs.
        baseline_predictions: Baseline predictions keyed by case_ref.

    Returns:
        Ordered list of case_ids for sequential processing.
    """
    # ``canary_ids`` remains accepted for callers of the retired curriculum
    # API, but is intentionally ignored. Canary execution is no longer part
    # of BCCSE promotion or ordering.
    _ = canary_ids
    # Classify cases by priority.
    unresolved = []
    boundary = []
    confident = []

    for cid in case_ids:
        pred = baseline_predictions.get(cid)
        if pred is None:
            unresolved.append(cid)  # Unknown → treat as high priority
            continue

        state = str(pred.get("state") or "")
        if state == "unresolved":
            unresolved.append(cid)
        elif state in ("verified_error", "verified_clean"):
            if use_gold:
                if not isinstance(pred.get("gold_label"), bool):
                    raise ValueError("gold_label must be bool in gold-enhanced mode")
                gold = bool(pred["gold_label"])
                predicted_positive = state == "verified_error"
                informative = predicted_positive != gold
            else:
                from .step_attributor import attribute_failure

                informative = attribute_failure(
                    pred,
                    use_gold=False,
                ).should_evolve
            if informative:
                boundary.append(cid)
            else:
                confident.append(cid)
        else:
            unresolved.append(cid)

    return unresolved + boundary + confident


def get_case_priority_label(
    case_id: str,
    baseline_predictions: Mapping[str, Mapping[str, Any]],
    *,
    use_gold: bool = False,
) -> str:
    """Get the priority label for a case (for logging/reporting)."""
    pred = baseline_predictions.get(case_id)
    if pred is None:
        return "unknown"
    state = str(pred.get("state") or "")
    if state == "unresolved":
        return "unresolved"
    if use_gold:
        if not isinstance(pred.get("gold_label"), bool):
            raise ValueError("gold_label must be bool in gold-enhanced mode")
        gold = bool(pred["gold_label"])
        predicted_positive = state == "verified_error"
        return (
            "boundary_incorrect"
            if predicted_positive != gold
            else "confident_correct"
        )
    from .step_attributor import attribute_failure

    return (
        "boundary_internal"
        if attribute_failure(pred, use_gold=False).should_evolve
        else "confident_internal"
    )
