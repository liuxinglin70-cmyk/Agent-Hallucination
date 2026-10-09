"""Effect and noise readings over scored, identity-checked run snapshots."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from evolution.metrics import confusion_rows, flipped_cases, mcnemar_improvement_p, paired_delta


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
        "verdict": {"reading": "noise_floor" if same_execution else "effect",
                    "effect_established": established, "reason": reason,
                    "decisive_fixed": fixed, "decisive_broken": broken,
                    "net_decisive": fixed - broken if fixed is not None else None,
                    "mcnemar_one_sided_p": p_value, "rule": criteria["rule"],
                    "significance_level": criteria["significance_level"]},
    }
