#!/usr/bin/env python
"""Cost model for the 693-case baseline, calibrated on archived real readings.

Nothing here estimates from intuition: every number is harvested from a run
that actually called the provider, and each reading carries the file it came
from. The point of the exercise is a defensible answer to "can this be paid
for", which has two halves -- hours and tokens -- and a third question the
numbers themselves raise: *how much does the corpus distribution matter, given
that everything measured so far was selected for difficulty?*

What the model is made of
-------------------------
``per_case``   One row per real per-case wall clock: the case id, seconds, and
               where it came from. Capped rows (a case killed at exactly the
               provider or batch deadline) are flagged, because a timeout is
               not a short case.
``batch``      Whole-batch readings: wall clock for a known case list. These
               are the only measurements that contain the tail *and* the
               scheduling inefficiency, so they calibrate throughput directly.
``tokens``     Per-case token totals where the envelope recorded them.

Three estimators, deliberately not averaged into one number
-----------------------------------------------------------
``work``       T(W) = ceil(n/W) * mu. Pure throughput: the work divided by the
               workers. Assumes the tail never binds and packing is perfect, so
               it is a *floor* on a well-behaved corpus.
``simulated``  Greedy list scheduling over a bootstrap resample of the observed
               per-case distribution, in submission order (which is what
               ``ThreadPoolExecutor`` with a fixed worker count does). This
               keeps the observed tail but cannot invent a tail it never saw,
               so it is the *middle* reading.
``observed``   Scale the measured batch throughput (cases per wall minute) to
               693. This contains everything -- tail, queueing, retry churn --
               and is the only estimator that can come out *worse* than the
               others. It is also the one most distorted by the batch it was
               measured on.

Reporting all three, spread out, is the honest form: the corpus's real
distribution is unknown, and the three answers differ by an order of magnitude
depending on which part of it is assumed to look like the archive.

    python scripts/cost_model_693.py
    python scripts/cost_model_693.py --json artifacts/cost_model_693.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_SIZE = 693
SEED = 20260923
#: Bootstrap resamples per worker setting. Checked at 800/1500/4000 draws on the
#: real sample: p50 moves by under 0.3% across that range, so the low end buys
#: the same answer for a quarter of the runtime. The count is reported in the
#: model so a reader knows how smooth the quantiles are.
BOOTSTRAP_DRAWS = 1200
WORKER_SETTINGS = (1, 4, 8, 16)

#: Wall clock a case was cut at, by the deadline that cut it. A reading equal to
#: one of these is a timeout, not a duration, and counting it as a duration both
#: overstates the typical case and hides the outage that produced it.
CASE_CAPS = {
    900.0: "provider.timeout_seconds (900) in detector/configs/autodl.json",
    3600.0: "batch deadline used by earlier runs (3600s)",
    5400.0: "case_timeout_seconds of the 2026-09-18 error-scope protocol",
    10800.0: "case_timeout_seconds (10800) in detector/configs/autodl.json",
}

AUDIT_CASES = (
    "BFCL/041.json", "Magentic_One/085.json", "Octotools/005.json",
    "OpenDeepSearch/067.json", "OpenDeepSearch/077.json", "SmolAgents/019.json",
    "OpenDeepSearch/088.json", "Magentic_One/049.json", "OpenManus/064.json",
    "Octotools/030.json", "OpenDeepSearch/002.json", "OpenDeepSearch/063.json",
)


def harvest() -> dict[str, Any]:
    """Read the frozen numeric sample consolidated into the experiment log."""
    source = ROOT / "docs" / "experiment-log.md"
    text = source.read_text(encoding="utf-8")
    start = text.index("<!-- cost-calibration:start -->")
    start = text.index("```json\n", start) + len("```json\n")
    end = text.index("\n```\n<!-- cost-calibration:end -->", start)
    return json.loads(text[start:end])


def classify(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Split readings into usable durations and the rows that are not one."""
    measured: list[dict[str, Any]] = []
    zero: list[str] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        seconds = float(row.get("seconds") or 0.0)
        if seconds <= 0.0:
            # A zero-second row is a cache hit or a batch-deadline skip. Both
            # are real events and neither is a duration, so neither may enter
            # the distribution.
            zero.append(str(row.get("case")))
            continue
        cap = next(
            (reason for value, reason in CASE_CAPS.items()
             if abs(seconds - value) < 1.0),
            None,
        )
        entry = dict(row)
        entry["capped_at"] = cap
        if cap is not None:
            skipped.append({
                "case": entry["case"], "seconds": seconds,
                "reason": cap, "source": entry["source"],
            })
            # Still recorded as a lower bound, never as a measurement.
            continue
        measured.append(entry)
    return {"measured": measured, "zero_second_rows": zero, "capped": skipped}


def percentile(values: Sequence[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def simulate_makespan(
    seconds: Sequence[float],
    *,
    corpus_size: int,
    workers: int,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Greedy list scheduling over bootstrap resamples of the observed sample.

    The runner submits every case at once to a fixed-size pool, so the schedule
    is "each case goes to the first worker that frees up", in submission order.
    Simulating exactly that keeps the observed tail and the observed variance,
    and it reports a range rather than a point because the corpus draw is
    unknown.
    """
    rng = random.Random(seed)
    sample = [float(value) for value in seconds if float(value) > 0]
    if not sample:
        return {"schedulable": False, "reason": "no per-case readings"}
    makespans: list[float] = []
    for _ in range(draws):
        jobs = [rng.choice(sample) for _ in range(corpus_size)]
        load = [0.0] * max(1, workers)
        for job in jobs:
            index = min(range(len(load)), key=lambda i: (load[i], i))
            load[index] += job
        makespans.append(max(load) / 60.0)
    makespans.sort()
    return {
        "schedulable": True,
        "draws": draws,
        "workers": workers,
        "p10_minutes": round(percentile(makespans, 0.10) or 0.0, 1),
        "p50_minutes": round(percentile(makespans, 0.50) or 0.0, 1),
        "p90_minutes": round(percentile(makespans, 0.90) or 0.0, 1),
        "max_minutes": round(makespans[-1], 1),
        "sample_max_minutes": round(max(sample) / 60.0, 1),
    }


def _hours(minutes: float | None) -> float | None:
    return None if minutes is None else round(minutes / 60.0, 1)


def observed_throughput(
    *,
    batches: Sequence[Mapping[str, Any]],
    measured: Sequence[Mapping[str, Any]],
    prose: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Turn each whole-batch reading into a throughput and an efficiency.

    A batch's naive projection (``wall / cases * 693``) is not scale invariant:
    a 12-case batch whose wall clock is one 94-minute case says little about a
    693-case batch, where that same case overlaps with 692 others. What *does*
    transfer is the batch's **packing efficiency** -- measured wall divided by
    that batch's own ideal wall, ``max(sum of its cases / workers, its longest
    case)``.

    Computing the efficiency needs the batch's per-case costs, which only the
    audit batch has (its rows are archived, plus the prose reading of the one
    case that was served from cache during that batch). The patrol anchor
    therefore carries its naive projection only, marked as such.
    """
    by_case: dict[str, float] = {}
    for row in list(measured) + list(prose):
        case = str(row["case"])
        seconds = float(row["seconds"])
        by_case[case] = max(by_case.get(case, 0.0), seconds)

    result: dict[str, Any] = {}
    for batch in batches:
        wall = batch.get("wall_minutes")
        cases = batch.get("cases") or 0
        if not wall or not cases:
            continue
        workers = int(batch.get("workers") or 8)
        entry: dict[str, Any] = {
            "cases": cases,
            "wall_minutes": wall,
            "workers": workers,
            "minutes_per_case_wall": round(wall / cases, 2),
            "projected_minutes_693": round(wall / cases * CORPUS_SIZE, 1),
            "projected_hours_693": _hours(wall / cases * CORPUS_SIZE),
            "projected_days_693": round(wall / cases * CORPUS_SIZE / 1440, 1),
            "source": batch["source"],
            "detail": batch.get("detail"),
            "naive_projection_caveat": (
                "scaling a batch wall clock by case count assumes every batch"
                " has the same shape as this one; read packing_efficiency for"
                " the part that does transfer"
            ),
        }
        if batch.get("name") == "audit":
            known = {
                case: by_case[case] / 60.0
                for case in AUDIT_CASES if case in by_case
            }
            missing = [case for case in AUDIT_CASES if case not in by_case]
            work = sum(known.values())
            longest = max(known.values()) if known else 0.0
            ideal = max(work / workers, longest)
            entry["per_case_costs_known"] = len(known)
            entry["cases_without_a_reading"] = missing
            entry["sum_case_minutes"] = round(work, 1)
            entry["longest_case_minutes"] = round(longest, 1)
            entry["ideal_wall_minutes"] = round(ideal, 1)
            entry["packing_efficiency"] = (
                round(ideal / wall, 3) if wall else None
            )
        result[batch["name"]] = entry
    return result


def tail_analysis(
    *,
    seconds: Sequence[float],
    prose_seconds: Sequence[float],
    workers: int,
    work_floor_minutes: float | None,
) -> dict[str, Any]:
    """Which bound decides the makespan: the tail, or the total work.

    "The slowest case sets the floor" is true and usually irrelevant at scale.
    This computes both sides so which one binds is visible instead of assumed:
    the slowest case is a floor of its own duration at any worker count; k slow
    cases are a floor of ``ceil(k/W) * t``; the corpus total is a floor of
    ``n * mean / W``.
    """
    slowest = max(list(seconds) + list(prose_seconds) + [0.0]) / 60.0
    tail_rate = 1.0 / len(AUDIT_CASES)
    slow_cases = max(1, int(round(CORPUS_SIZE * tail_rate)))
    tail_bound = math.ceil(slow_cases / workers) * 93.8
    return {
        "workers": workers,
        "slowest_reading_minutes": round(slowest, 1),
        "single_slow_case_bound_minutes": 93.8,
        "single_slow_case_bound_note": (
            "one case cannot be split, so no worker count makes a batch that"
            " contains it shorter than that case"
        ),
        "observed_tail_rate": {
            "slow_cases_in_audit_set": 1,
            "audit_set_size": len(AUDIT_CASES),
            "rate": round(tail_rate, 4),
            "basis": "Magentic_One/049 measured at 93.8 min in run 2 and 36.0"
                     " min in run 1; only the slower reading is used here",
        },
        "extrapolated_slow_cases_in_693": slow_cases,
        "tail_bound_minutes": round(tail_bound, 1),
        "tail_bound_hours": round(tail_bound / 60.0, 1),
        "work_floor_minutes": (
            None if work_floor_minutes is None else round(work_floor_minutes, 1)
        ),
        "binding_bound": (
            "work"
            if (work_floor_minutes is not None
                and work_floor_minutes >= tail_bound)
            else "tail"
        ),
        "reading": (
            "at this worker count the 94-minute tail does not set the floor;"
            " the corpus total does. The tail only binds once the number of"
            " ~94-minute cases exceeds the worker count"
        ),
    }


def sensitivity(*, machine_only: Sequence[float]) -> dict[str, Any]:
    """The estimates recomputed without the prose-only readings.

    61% of the sample's total minutes live in four numbers that were transcribed
    from report prose. If a reader does not accept those, the answer changes,
    and a cost model whose conclusion flips on four sentences should say so
    rather than bury it.
    """
    minutes = [value / 60.0 for value in machine_only if value > 0]
    if not minutes:
        return {"available": False}
    mean = statistics.fmean(minutes)
    block: dict[str, Any] = {
        "available": True,
        "readings": len(minutes),
        "mean_minutes": round(mean, 1),
        "median_minutes": round(statistics.median(minutes), 1),
        "max_minutes": round(max(minutes), 1),
        "sum_minutes": round(sum(minutes), 1),
        "estimates": {},
    }
    for workers in WORKER_SETTINGS:
        work = math.ceil(CORPUS_SIZE / workers) * mean
        block["estimates"][f"W{workers}"] = {
            "work_floor_minutes": round(work, 1),
            "work_floor_hours": _hours(work),
            "simulated_makespan": simulate_makespan(
                machine_only, corpus_size=CORPUS_SIZE, workers=workers,
                draws=BOOTSTRAP_DRAWS, seed=SEED,
            ),
        }
    return block


def token_model(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Token readings per case from the preserved numeric sample.

    Stage metadata is a lower bound when request observations are absent.
    Missing usage is excluded rather than treated as a free case.
    """
    per_case: list[dict[str, Any]] = []
    for row in rows:
        tokens = row.get("tokens")
        if not isinstance(tokens, (int, float)) or tokens <= 0:
            continue
        per_case.append({
            "case": row["case"], "tokens": int(tokens), "source": row["source"],
        })
    values = [row["tokens"] for row in per_case]
    if not values:
        return {"measured_cases": 0, "note": "no token readings available"}
    total = sum(values)
    return {
        "measured_cases": len(per_case),
        "mean_per_case": round(total / len(values), 1),
        "p50_per_case": round(percentile(values, 0.50) or 0.0, 1),
        "max_per_case": max(values),
        "corpus_estimate": round(total / len(values) * CORPUS_SIZE),
        "corpus_estimate_millions": round(
            total / len(values) * CORPUS_SIZE / 1e6, 1,
        ),
        "rows": sorted(per_case, key=lambda row: -row["tokens"]),
    }


def build_model() -> dict[str, Any]:
    harvested = harvest()
    classified = classify(harvested["per_case"])
    measured = classified["measured"]
    seconds = [float(row["seconds"]) for row in measured]
    minutes = [value / 60.0 for value in seconds]
    mean_minutes = statistics.fmean(minutes) if minutes else None
    median_minutes = statistics.median(minutes) if minutes else None
    max_minutes = max(minutes) if minutes else None
    unique_cases = sorted({str(row["case"]) for row in measured})

    estimates: dict[str, Any] = {}
    for workers in WORKER_SETTINGS:
        work_minutes = (
            None if mean_minutes is None
            else math.ceil(CORPUS_SIZE / workers) * mean_minutes
        )
        estimates[f"W{workers}"] = {
            "workers": workers,
            "work_lower_bound_minutes": (
                None if work_minutes is None else round(work_minutes, 1)
            ),
            "work_lower_bound_hours": _hours(work_minutes),
            "simulated_makespan": simulate_makespan(
                seconds, corpus_size=CORPUS_SIZE, workers=workers,
            ),
            "tail": tail_analysis(
                seconds=seconds,
                prose_seconds=[
                    float(row["seconds"])
                    for row in harvested["per_case"] if row.get("prose_only")
                ],
                workers=workers,
                work_floor_minutes=work_minutes,
            ),
        }

    prose = [
        row for row in harvested["per_case"] if row.get("prose_only")
    ]
    throughput = observed_throughput(
        batches=harvested["batches"], measured=measured, prose=prose,
    )
    # The one number that transfers from the batch anchors back onto the
    # simulated corpus estimate: how much slower the real batch was than its
    # own ideal schedule.
    efficiency = (throughput.get("audit") or {}).get("packing_efficiency")
    for block in estimates.values():
        sim = block["simulated_makespan"]
        adjusted = None
        if efficiency and sim.get("p50_minutes"):
            adjusted = sim["p50_minutes"] / efficiency
        block["efficiency_adjusted_minutes"] = (
            None if adjusted is None else round(adjusted, 1)
        )
        block["efficiency_adjusted_hours"] = _hours(adjusted)
        block["efficiency_adjusted_note"] = (
            None if efficiency is None else
            "simulated p50 / packing efficiency %.3f measured on the audit"
            " batch (%s -> %s min vs its own ideal)"
            % (
                efficiency,
                (throughput.get("audit") or {}).get("ideal_wall_minutes"),
                (throughput.get("audit") or {}).get("wall_minutes"),
            )
        )

    return {
        "schema_version": "detector.cost_model.v1",
        "corpus_size": CORPUS_SIZE,
        "provider": {
            "config": "detector/configs/autodl.json",
            "model": "GLM-5.3-flash",
            "endpoint": "https://www.autodl.art/api/v1",
            "case_timeout_seconds": 10800,
            "provider_timeout_seconds": 900,
            "total_timeout_seconds": 2400,
            "judge_votes": 3,
            "judge_vote_topup": 2,
            "defense_votes": 3,
            "defense_vote_topup": 2,
        },
        "sample": {
            "per_case_readings": len(measured),
            "unique_cases": len(unique_cases),
            "cases": unique_cases,
            "mean_minutes": None if mean_minutes is None else round(mean_minutes, 1),
            "median_minutes": (
                None if median_minutes is None else round(median_minutes, 1)
            ),
            "p90_minutes": (
                None if not minutes
                else round(percentile(minutes, 0.90) or 0.0, 1)
            ),
            "max_minutes": None if max_minutes is None else round(max_minutes, 1),
            "sum_minutes": round(sum(minutes), 1) if minutes else None,
            "zero_second_rows": classified["zero_second_rows"],
            "capped_rows": classified["capped"],
            "prose_only_rows": [
                row for row in harvested["per_case"] if row.get("prose_only")
            ],
            "readings": sorted(measured, key=lambda row: -row["seconds"]),
        },
        "estimates": estimates,
        "observed_batch_throughput": throughput,
        "sensitivity": sensitivity(
            machine_only=[
                float(row["seconds"]) for row in measured
                if not row.get("prose_only")
            ],
        ),
        "tokens": token_model(harvested["per_case"]),
        "notes": harvested["notes"],
        "caveats": [
            f"The per-case sample is {len(unique_cases)} unique cases out of 693,"
            " and every one of them was pulled from a patrol/audit set that the"
            " framework selects for difficulty. The corpus mean is plausibly far"
            " below this sample's mean, so read the work floor as a floor on a"
            " well-behaved corpus rather than a forecast.",
            "The four prose-only readings (Magentic_One/049 twice, Camel/018,"
            " Magentic_One/050) are the slowest rows in the sample and the only"
            " rows whose source is a report sentence rather than a JSON file."
            " They decide most of the tail; see 'sensitivity' for the estimate"
            " computed without them.",
            "One case (Magentic_One/049) was measured at 36.0 and 93.8 minutes"
            " under the same config -- a 2.6x spread with no code change. Any"
            " single-case point estimate carries that much runway.",
            "The provider is a shared endpoint with no rate-limit contract. If it"
            " throttles, throughput falls below every estimate here and the only"
            " correction is to lower the worker count and re-measure.",
            "The batch anchors each contain one batch. The audit anchor is"
            " dominated by its slowest case, which is a property of that case"
            " list, not of the corpus.",
            "Token totals are provider-reported and cover the stages that"
            " recorded usage; a case whose envelope lost a stage's usage is"
            " excluded rather than counted as cheap.",
        ],
    }


def _table(model: Mapping[str, Any]) -> str:
    lines = []
    lines.append("workers | work floor (n*mu/W)        | simulated makespan p10/p50/p90")
    lines.append("--------+------------------------------+-------------------------------")
    for key, block in model["estimates"].items():
        sim = block["simulated_makespan"]
        floor = block["work_lower_bound_minutes"]
        lines.append(
            "%7s | %10s h (%8s min) | %8s / %8s / %8s h"
            % (
                block["workers"],
                block["work_lower_bound_hours"],
                floor,
                round((sim.get("p10_minutes") or 0) / 60, 1),
                round((sim.get("p50_minutes") or 0) / 60, 1),
                round((sim.get("p90_minutes") or 0) / 60, 1),
            )
        )
    lines.append("")
    lines.append("bound that decides the makespan (tail vs total work)")
    lines.append("workers | tail bound (h) | work floor (h) | binding")
    lines.append("--------+----------------+----------------+--------")
    for block in model["estimates"].values():
        tail = block["tail"]
        lines.append(
            "%7s | %14s | %14s | %s"
            % (
                block["workers"],
                tail["tail_bound_hours"],
                round((tail["work_floor_minutes"] or 0) / 60.0, 1),
                tail["binding_bound"],
            )
        )
    lines.append("")
    lines.append("efficiency-adjusted (simulated p50 / audit packing efficiency)")
    lines.append("workers | hours | note")
    lines.append("--------+-------+-----")
    for block in model["estimates"].values():
        lines.append(
            "%7s | %5s | %s"
            % (block["workers"], block["efficiency_adjusted_hours"],
               block["efficiency_adjusted_note"])
        )
    lines.append("")
    lines.append("sensitivity: the same estimates without the prose-only readings")
    lines.append("workers | work floor (h) | simulated p10/p50/p90 (h)")
    lines.append("--------+----------------+---------------------------")
    sensitivity_block = model.get("sensitivity") or {}
    for key, block in (sensitivity_block.get("estimates") or {}).items():
        sim = block["simulated_makespan"]
        lines.append(
            "%7s | %14s | %s / %s / %s"
            % (key, block["work_floor_hours"],
               round((sim.get("p10_minutes") or 0) / 60, 1),
               round((sim.get("p50_minutes") or 0) / 60, 1),
               round((sim.get("p90_minutes") or 0) / 60, 1))
        )
    lines.append("")
    lines.append("observed batch throughput, projected to 693 cases")
    lines.append("name     | cases | wall min | min/case | 693 -> hours (days) | packing eff")
    lines.append("---------+-------+----------+----------+--------------------+------------")
    for name, block in model["observed_batch_throughput"].items():
        lines.append(
            "%-8s | %5d | %8s | %8s | %s h (%s d) | %s vs ideal %s min"
            % (name, block["cases"], block["wall_minutes"],
               block["minutes_per_case_wall"], block["projected_hours_693"],
               block["projected_days_693"], block.get("packing_efficiency", "-"),
               block.get("ideal_wall_minutes", "-"))
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default=None, help="write the model as JSON")
    parser.add_argument("--text", action="store_true", help="also print tables")
    args = parser.parse_args(argv)
    model = build_model()
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(model, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"model -> {out}")
    sample = model["sample"]
    print(json.dumps({
        "per_case_readings": sample["per_case_readings"],
        "unique_cases": sample["unique_cases"],
        "mean_minutes": sample["mean_minutes"],
        "median_minutes": sample["median_minutes"],
        "p90_minutes": sample["p90_minutes"],
        "max_minutes": sample["max_minutes"],
        "sum_minutes": sample["sum_minutes"],
        "zero_second_rows": len(sample["zero_second_rows"]),
        "capped_rows": sample["capped_rows"],
        "estimates_hours": {
            key: {
                "work_floor": block["work_lower_bound_hours"],
                "sim_p10": round(
                    (block["simulated_makespan"].get("p10_minutes") or 0) / 60, 1,
                ),
                "sim_p50": round(
                    (block["simulated_makespan"].get("p50_minutes") or 0) / 60, 1,
                ),
                "sim_p90": round(
                    (block["simulated_makespan"].get("p90_minutes") or 0) / 60, 1,
                ),
                "efficiency_adjusted": block["efficiency_adjusted_hours"],
                "binding_bound": block["tail"]["binding_bound"],
            }
            for key, block in model["estimates"].items()
        },
        "sensitivity_hours": {
            key: {
                "work_floor": block["work_floor_hours"],
                "sim_p50": round(
                    (block["simulated_makespan"].get("p50_minutes") or 0) / 60, 1,
                ),
            }
            for key, block in (
                (model.get("sensitivity") or {}).get("estimates") or {}
            ).items()
        },
        "tokens": {
            key: value for key, value in model["tokens"].items()
            if key != "rows"
        },
    }, ensure_ascii=False, indent=2))
    if args.text:
        print()
        print(_table(model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
