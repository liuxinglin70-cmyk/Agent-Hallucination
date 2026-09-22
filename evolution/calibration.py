"""Offline calibration audit over discrete confidence levels.

The detector reports ``judge.confidence`` as one of ``high`` / ``medium`` /
``low``. That is a *level*, not a probability: it has no numeric scale, no
distance between levels and no claim that "high" means any particular rate.

So this module builds a **reliability table** -- per level, how often the level
was right, and how often it abstained -- and deliberately refuses the
continuous metrics the plan lists (Brier, log loss, AUROC, calibration curve,
accuracy-at-coverage, bootstrap CI). Those need a numeric score per case;
computing them over an ordinal label would mean inventing the numbers and
reporting the invention as a measurement. ``CONTINUOUS_METRICS_UNAVAILABLE``
records that gap in the report itself, so a reader sees why a metric is absent
rather than assuming it was skipped.

Every number here is ``calibration-only`` (plan §19 rule 12). Nothing in this
module scores a candidate, admits a version or overrides a gate: it is a
diagnostic view of whether the judge's stated certainty carries information.

The trust question §12.3 asks -- whether a *learned* executor may be given
conviction authority -- is answered by a subset of these numbers (held-out
accuracy, FPR, FNR, abstention, per-source grouping). This module produces
them; deciding on them is a promotion decision, not a calibration one.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

CALIBRATION_SCHEMA = "evolution.calibration_report.v1"
METRIC_ROLE = "calibration-only"

# The vocabulary the runtime can actually emit. Read from the detector's fact
# table rather than restated, so a level added there cannot go unmeasured here.
def confidence_levels() -> tuple[str, ...]:
    from detector.interventions import FACT_NAMES

    return tuple(sorted(FACT_NAMES["judge_confidence"]))

# Ordered strongest first, for reports. A level the runtime emits but this
# order omits is appended rather than dropped.
_LEVEL_ORDER = ("high", "medium", "low")

CONTINUOUS_METRICS_UNAVAILABLE = (
    "Brier, log loss, AUROC, calibration curve, accuracy-at-coverage and "
    "bootstrap CI need a numeric score per case. judge.confidence is an "
    "ordinal level (high/medium/low) with no numeric scale, so computing them "
    "would require inventing the mapping. They stay unimplemented until a "
    "runner emits real probabilities."
)


def _judge_confidence(row: Mapping[str, Any]) -> str:
    """The judge's stated confidence, or ``""`` when the row does not say."""
    result = row.get("result") or {}
    proof = result.get("proof") or {}
    judge = proof.get("judge") or {}
    return str(judge.get("confidence") or "").strip().lower()


def _outcome(row: Mapping[str, Any]) -> str:
    """``correct`` / ``wrong`` / ``abstained`` for one evaluated row.

    Abstention is its own outcome, not a miss: a row the detector declined to
    decide says nothing about whether its stated confidence was warranted, and
    folding it into "wrong" would make a cautious system look miscalibrated.
    """
    gold = row.get("gold")
    prediction = row.get("prediction")
    if gold is not True and gold is not False:
        return "unlabelled"
    if prediction is None:
        return "abstained"
    if str(row.get("authority") or "") != "authoritative":
        return "abstained"
    return "correct" if prediction is gold else "wrong"


def reliability_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    level_of: Any = None,
) -> dict[str, Any]:
    """Per-confidence-level accuracy over evaluated rows.

    ``level_of`` overrides how a level is read from a row; it exists so a
    learned runner's own score field can be audited through the same table
    without this module growing a second implementation.

    Counts are exact integers. Rates are ``None`` rather than ``0.0`` when the
    denominator is empty: "no cases at this level" and "every case at this
    level was wrong" are different findings, and a zero would merge them.
    """
    reader = level_of or _judge_confidence
    buckets: dict[str, dict[str, int]] = {}
    unstated = 0
    for row in rows:
        level = str(reader(row) or "").strip().lower()
        if not level:
            unstated += 1
            continue
        bucket = buckets.setdefault(level, {
            "n": 0, "correct": 0, "wrong": 0, "abstained": 0, "unlabelled": 0,
        })
        outcome = _outcome(row)
        if outcome == "unlabelled":
            bucket["unlabelled"] += 1
            continue
        bucket["n"] += 1
        bucket[outcome] += 1
    levels = [
        level for level in _LEVEL_ORDER if level in buckets
    ] + sorted(set(buckets) - set(_LEVEL_ORDER))
    entries = []
    for level in levels:
        bucket = buckets[level]
        decided = bucket["correct"] + bucket["wrong"]
        entries.append({
            "level": level,
            **bucket,
            # ``accuracy`` is over *decided* rows, which is the question "when
            # this level commits, how often is it right".
            "accuracy": (
                round(bucket["correct"] / decided, 6) if decided else None
            ),
            # ``coverage`` is the share of decided rows this level accounts
            # for -- the discrete stand-in for accuracy-at-coverage.
            "decided": decided,
            "abstention_rate": (
                round(bucket["abstained"] / bucket["n"], 6) if bucket["n"] else None
            ),
            "error_rate": (
                round(bucket["wrong"] / decided, 6) if decided else None
            ),
        })
    total_decided = sum(entry["decided"] for entry in entries)
    for entry in entries:
        entry["coverage"] = (
            round(entry["decided"] / total_decided, 6) if total_decided else None
        )
    return {
        "levels": entries,
        "unstated_level_rows": unstated,
        "decided_rows": total_decided,
    }


def per_source_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    level_of: Any = None,
) -> dict[str, Any]:
    """The same table split by case source.

    A level can be well calibrated overall and badly calibrated on one source;
    the split is what makes that visible instead of averaged away.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        case = str(row.get("case") or "")
        source = case.split("/", 1)[0] if "/" in case else "(unknown)"
        grouped.setdefault(source, []).append(row)
    return {
        "sources": {
            source: reliability_table(items, level_of=level_of)
            for source, items in sorted(grouped.items())
        },
        "source_count": len(grouped),
    }


def monotonicity(table: Mapping[str, Any]) -> dict[str, Any]:
    """Whether stronger levels were actually more accurate.

    Reported as a finding rather than enforced: a non-monotone reliability
    table is informative (it means the level is not a usable ordering) and
    silently reordering the levels to hide it would destroy the only thing the
    audit is for.
    """
    rated = [
        entry for entry in table.get("levels") or ()
        if entry.get("accuracy") is not None and entry.get("decided")
    ]
    order = {level: index for index, level in enumerate(_LEVEL_ORDER)}
    rated.sort(key=lambda entry: order.get(entry["level"], len(order)))
    violations = [
        f"{rated[index]['level']} ({rated[index]['accuracy']}) < "
        f"{rated[index + 1]['level']} ({rated[index + 1]['accuracy']})"
        for index in range(len(rated) - 1)
        if rated[index]["accuracy"] < rated[index + 1]["accuracy"]
    ]
    return {
        "levels_compared": len(rated),
        "monotone": not violations,
        "violations": violations,
    }


def calibration_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    level_of: Any = None,
) -> dict[str, Any]:
    """The full audit: levels, per source, and what could not be measured."""
    overall = reliability_table(rows, level_of=level_of)
    return {
        "schema_version": CALIBRATION_SCHEMA,
        "metric_role": METRIC_ROLE,
        "rows": len(rows),
        "overall": overall,
        "per_source": per_source_table(rows, level_of=level_of),
        "monotonicity": monotonicity(overall),
        "continuous_metrics_unavailable": CONTINUOUS_METRICS_UNAVAILABLE,
        "confidence_levels": list(confidence_levels()),
        "note": (
            "Discrete reliability table over the judge's stated confidence. "
            "Calibration-only: this report scores nothing and admits nothing. "
            "A well-calibrated level is not evidence that a candidate repaired "
            "a mechanism."
        ),
    }


__all__ = [
    "CALIBRATION_SCHEMA",
    "CONTINUOUS_METRICS_UNAVAILABLE",
    "METRIC_ROLE",
    "calibration_report",
    "confidence_levels",
    "monotonicity",
    "per_source_table",
    "reliability_table",
]
