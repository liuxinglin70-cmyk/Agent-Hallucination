"""BCCSE v5 — Curriculum-based case selection for online evolution.

Orders evolution cases by "learning edge" priority:
1. Unresolved cases first (medium difficulty — detector is uncertain)
2. Boundary resolved cases second (CRS near threshold)
3. High-confidence correct cases last (provide stable_success signal)

Canary cases are interleaved in the first batch for early safety monitoring.

Inspired by HIVE (ICML 2026): "strongest learning signals are concentrated
at the learning edge defined by the intersection of medium difficulty and
high response entropy."
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def order_evolution_cases(
    case_ids: Sequence[str],
    baseline_predictions: Mapping[str, Mapping[str, Any]],
    canary_ids: Sequence[str],
    *,
    use_gold: bool = False,
) -> list[str]:
    """Order evolution cases by curriculum priority.

    Priority ordering:
    1. Unresolved cases (highest learning signal)
    2. Resolved but boundary cases (moderate signal)
    3. High-confidence correct cases (low signal, stable_success)

    Canary cases are placed at evenly-spaced positions within the first batch
    to ensure early safety monitoring.

    Args:
        case_ids: All evolution case IDs (includes canary).
        baseline_predictions: Baseline predictions keyed by case_ref.
        canary_ids: Canary case IDs (subset of case_ids).

    Returns:
        Ordered list of case_ids for sequential processing.
    """
    canary_set = set(canary_ids)
    non_canary = [cid for cid in case_ids if cid not in canary_set]
    canary_list = [cid for cid in case_ids if cid in canary_set]

    # Classify non-canary cases by priority
    unresolved = []
    boundary = []
    confident = []

    for cid in non_canary:
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

    # Interleave canary cases into the first portion
    # Place canaries at positions 0, 5, 10, 15, 20, 25 (every 5th position)
    ordered_non_canary = unresolved + boundary + confident
    result: list[str] = []
    canary_idx = 0
    canary_interval = max(1, len(ordered_non_canary) // (len(canary_list) + 1))

    for i, cid in enumerate(ordered_non_canary):
        if canary_idx < len(canary_list) and i > 0 and i % canary_interval == 0:
            result.append(canary_list[canary_idx])
            canary_idx += 1
        result.append(cid)

    # Append remaining canaries at the end
    while canary_idx < len(canary_list):
        result.append(canary_list[canary_idx])
        canary_idx += 1

    return result


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
