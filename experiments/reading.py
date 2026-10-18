"""Effect and noise readings over scored, identity-checked run snapshots."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from evolution.metrics import confusion_rows, flipped_cases, mcnemar_improvement_p, paired_delta


def _clean_case_flips(
    baseline: Sequence[Mapping[str, Any]], deployed: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Count the two directions a clean case can move, over identical pairs.

    ``paired_delta`` reports ``fp_added`` (a clean case that becomes positive)
    but has no mirror for the case that stops being one, because the gates only
    ever needed the harm. The parity reading needs both directions, so the
    removal is counted here from the same rows under the same conditions.
    """
    added = removed = 0
    for row in confusion_rows(baseline, deployed):
        if row["gold"]:
            continue
        before, after = row["baseline_prediction"], row["active_prediction"]
        if before is not True and after is True:
            added += 1
        elif before is True and after is not True:
            removed += 1
    return {"added": added, "removed": removed}


def false_positive_parity(
    counts: Mapping[str, int], *, significance_level: float,
) -> dict[str, Any]:
    """Whether the false-positive movement is larger than the pairing can explain.

    An absolute rule ("fp must not exceed the baseline") compares two counts as
    if each were exact. On a sample with 38 clean cases the standard error of
    that difference is more than two cases, so the rule rejects a mechanism that
    broke three clean cases and fixed one at the same probability it rejects one
    that broke nothing -- and it accepts a tie that hides a 3-for-1 regression.

    The paired reading replaces the count comparison with a one-sided exact
    binomial test on the discordant clean cases only (``fp_added`` versus
    ``fp_removed``). It fails only when the additions *significantly* exceed the
    removals. Both raw counts stay in the result: a rule that cannot be checked
    against the numbers it used is not a rule.

    ``not_worse`` is ``True`` when additions do not exceed removals, whatever
    the sample size, so a tied or improved count never needs statistical cover.
    """
    added = int(counts.get("added") or 0)
    removed = int(counts.get("removed") or 0)
    discordant = added + removed
    if added <= removed:
        return {"not_worse": True, "fp_added": added, "fp_removed": removed,
                "discordant": discordant, "one_sided_p": 1.0,
                "reason": "clean-case additions do not exceed removals"}
    # Reuse the decisive-pair test rather than restating a binomial tail: the
    # statistics are identical, and a second implementation could drift.
    p_value = mcnemar_improvement_p({"decisive_fixed": added, "decisive_broken": removed})
    return {"not_worse": not p_value < significance_level, "fp_added": added,
            "fp_removed": removed, "discordant": discordant, "one_sided_p": p_value,
            "reason": "clean-case additions significantly exceed removals"
            if p_value < significance_level else
            "clean-case additions exceed removals but not beyond the pairing's noise"}


def read_pair(
    baseline: Sequence[Mapping[str, Any]],
    deployed: Sequence[Mapping[str, Any]],
    *, baseline_roll: Sequence[Mapping[str, Any]] | None,
    same_execution: bool, criteria: Mapping[str, Any],
) -> dict[str, Any]:
    all_cases = paired_delta(baseline, deployed)
    noise = None
    stable = None
    unstable = None
    if baseline_roll is not None:
        paired = confusion_rows(baseline, baseline_roll)
        unstable = flipped_cases(baseline, baseline_roll)
        unstable_set = set(unstable)
        stable = paired_delta(
            [row for row in baseline if row["case"] not in unstable_set],
            [row for row in deployed if row["case"] not in unstable_set],
        )
        noise = {"read_by_both": len(paired), "flipped_cases": unstable,
                 "flip_rate": len(unstable) / len(paired) if paired else None}
    fixed = stable["decisive_fixed"] if stable is not None else None
    broken = stable["decisive_broken"] if stable is not None else None
    p_value = mcnemar_improvement_p(stable) if stable is not None and stable["paired_n"] else None
    if stable is not None:
        clean_flips = _clean_case_flips(
            [row for row in baseline if row["case"] not in set(unstable or ())],
            [row for row in deployed if row["case"] not in set(unstable or ())],
        )
    else:
        clean_flips = _clean_case_flips(baseline, deployed)
    fp_parity = false_positive_parity(
        clean_flips, significance_level=criteria["significance_level"],
    )
    if same_execution:
        established, reason = None, "identical recorded execution inputs measure the instrument's noise"
    elif baseline_roll is None:
        established, reason = None, "baseline stability was not measured by an independent roll"
    elif not stable["paired_n"]:
        established, reason = None, "no stable paired case remains"
    else:
        established = fixed > broken and p_value < criteria["significance_level"]
        reason = "stable decisive corrections and the one-sided significance threshold determine the effect"
    return {
        "all_cases": all_cases, "stable_cases_only": stable,
        "noise_floor": noise, "unstable_cases": unstable,
        "false_positive_parity": fp_parity,
        "verdict": {"reading": "noise_floor" if same_execution else "effect",
                    "effect_established": established, "reason": reason,
                    "decisive_fixed": fixed, "decisive_broken": broken,
                    "net_decisive": fixed - broken if fixed is not None else None,
                    "mcnemar_one_sided_p": p_value, "rule": criteria["rule"],
                    "significance_level": criteria["significance_level"],
                    "false_positives_not_worse": fp_parity["not_worse"],
                    "false_positive_rule": criteria["false_positive_rule"]},
    }
