"""Measure the detector's own jitter from repeated rolls -- offline.

This is the calibration tool for the noise floor. The detector is not
deterministic at temperature 0: in the RSI pilot ``BFCL/008`` and ``BFCL/024``
read 6/2 across 8 repeats of one configuration, same packet hash, same
interventions hash. A framework whose admission can rest on one case has to
know that number before its readings can be believed, and only a real set of
repeats can produce it -- ``evolution.metrics.flipped_cases`` gives the same
reading from the cycle's own two rolls, which is enough to *flag* a case but
not enough to *quantify* one that only disagrees once in eight.

Two modes, both read-only:

    # 1. N independent rolls of one configuration -> per-case tallies
    python scripts/measure_noise_floor.py rolls \
        artifacts/run_rep01 artifacts/run_rep02 artifacts/run_rep03

    # 2. what one evolution cycle already published, from its report
    python scripts/measure_noise_floor.py cycle artifacts/.../cycle001.json

A "roll" is a detector run tree -- a directory holding ``cases/<case>/result.json``,
which is what the detector's own run writer produces (see
``artifacts/rsi_pilot_20260919/*/cases``). Rolls must cover the same case set;
the tool reports a case read by fewer rolls than the rest rather than pooling it
silently.

Nothing here calls a model service. It reads files that already exist, so it is
safe to run at any time, including while a real run is in progress -- it opens
each result file once and never writes.

A run tree carries no gold label, so ``rolls`` mode reports the verdict
transition table and the flip set but leaves the gold-based quantities
(``marginal_cases``, ``new_errors``) as ``None``. The cycle's own floor has
gold, because its control rows come from the evaluation harness that joins
``gold_map``; ``cycle`` mode reports that reading.

Exit code: 0 always, unless a path cannot be read (then 2). The reading is the
output, not a verdict, and a caller that wants a gate on it should use the
numbers in the JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evolution.metrics import (  # noqa: E402
    flipped_cases,
    noise_floor_flipped_cases,
    repeated_roll_disagreement,
)


def read_roll(root: Path, *, case_ids: list[str] | None = None) -> list[dict]:
    """Every case's terminal row from one run tree, in deterministic order.

    Missing results are omitted rather than filled with ``None``: a case that
    was never run is not a case that read "unresolved", and inventing a row for
    it would put a fabricated observation into a jitter measurement.
    """
    cases_dir = root / "cases"
    if not cases_dir.is_dir():
        raise SystemExit(f"no cases directory under {root}")
    wanted = {str(case) for case in case_ids} if case_ids else None
    rows: list[dict] = []
    for path in sorted(cases_dir.iterdir()):
        if not path.is_dir():
            continue
        result_path = path / "result.json"
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        case_id = str(result.get("case_id") or path.name.replace("__", "/"))
        if wanted is not None and case_id not in wanted:
            continue
        rows.append({
            "case": case_id,
            "prediction": {
                "verified_error": True,
                "verified_clean": False,
            }.get(str(result.get("state") or "")),
            "state": result.get("state"),
            "authority": result.get("decision_authority"),
            "responsible_step": result.get("responsible_step"),
            "result": result,
        })
    return rows


def roll_case_ids(root: Path) -> list[str]:
    return sorted(str(row["case"]) for row in read_roll(root))


def report_rolls(roots: list[Path]) -> dict:
    """The per-case disagreement across N rolls, plus the widest pair's flips.

    Two different things are kept apart, because conflating them would report a
    coverage gap as jitter:

    * ``disagreeing_cases`` -- read by every roll, read differently by at least
      two. This is the instrument's own jitter, and it is the number the cycle's
      two-roll floor measures;
    * ``one_sided_cases`` -- absent from at least one roll. That is a coverage
      difference in how the runs were built, not a statement about the
      detector, and pooling it into the jitter count would inflate it.
    """
    rolls = [read_roll(root) for root in roots]
    counts = [len(roll) for roll in rolls]
    reading = repeated_roll_disagreement(rolls)
    one_sided = reading_stable_case_set(rolls)
    disagreeing = [
        item["case"] for item in reading["cases"] if item["unstable"]
    ]
    read_by_every_roll = [
        item["case"] for item in reading["cases"] if item["reads"] == len(rolls)
    ]
    out = {
        "mode": "rolls",
        "rolls": [str(root) for root in roots],
        "roll_count": len(rolls),
        "cases_per_roll": counts,
        "total_cases": len(reading["cases"]),
        "read_by_every_roll": read_by_every_roll,
        "disagreeing_cases": disagreeing,
        "one_sided_cases": sorted(one_sided),
        # Rolls that do not cover the same case set cannot be pooled into one
        # tally, so the difference is named instead of averaged over.
        "case_set_mismatch": len(set(counts)) > 1 or bool(one_sided),
        **reading,
    }
    if len(rolls) >= 2:
        # The same reading the cycle computes: two rolls of one configuration,
        # paired case by case. A raw run tree carries no gold label, so the
        # gold-based quantities (``marginal_cases``, ``new_errors``) cannot be
        # derived here and are reported as ``None`` -- the verdict-transition
        # table is what a label-free pair can support. The cycle's own floor has
        # gold, because its control rows come from the evaluation harness which
        # joins ``gold_map``; read `cycle` mode for that reading.
        best = None
        for index in range(len(rolls)):
            for other in range(index + 1, len(rolls)):
                flipped = flipped_cases(rolls[index], rolls[other])
                if best is None or len(flipped) > len(best["flipped_cases"]):
                    best = {
                        "rolls": [index + 1, other + 1],
                        "flipped_cases": flipped,
                        "transitions": _transitions(
                            rolls[index], rolls[other],
                        ),
                        "gold": False,
                        "marginal_cases": None,
                        "new_errors": None,
                    }
        out["widest_pair"] = best
    return out


def _transitions(
    baseline_rows: list[dict], active_rows: list[dict],
) -> dict[str, int]:
    """The verdict-transition table of one pair, without needing gold.

    ``paired_delta`` refuses rows whose gold is not boolean, which is exactly
    what a raw run tree provides. This is the part of its reading that does not
    depend on labels, so the label-free mode can still say *how* the two rolls
    differed instead of only how many cases.
    """
    baseline = {str(row["case"]): row for row in baseline_rows}
    counts: dict[str, int] = {}
    for row in active_rows:
        other = baseline.get(str(row["case"]))
        if other is None:
            continue
        first = _verdict_token(other.get("prediction"))
        second = _verdict_token(row.get("prediction"))
        key = f"{first}->{second}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _verdict_token(prediction: object) -> str:
    if prediction is True:
        return "T"
    if prediction is False:
        return "F"
    return "U"


def reading_stable_case_set(rolls: list[list[dict]]) -> list[str]:
    """Cases not present in every roll."""
    if not rolls:
        return []
    everywhere = {str(row["case"]) for row in rolls[0]}
    for roll in rolls[1:]:
        everywhere &= {str(row["case"]) for row in roll}
    union: set[str] = set()
    for roll in rolls:
        union |= {str(row["case"]) for row in roll}
    return sorted(union - everywhere)


def report_cycle(path: Path) -> dict:
    """What one cycle report already says about its own instrument."""
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    instrument = report.get("instrument") or {}
    block = instrument.get("noise_floor")
    if block is None:
        return {
            "mode": "cycle",
            "path": str(path),
            "cycle": report.get("cycle"),
            "state": report.get("state"),
            "noise_floor": None,
            "note": (
                "this report predates the noise-floor field; it was produced "
                "by a version that computed the reroll and dropped it"
            ),
        }
    return {
        "mode": "cycle",
        "path": str(path),
        "cycle": report.get("cycle"),
        "state": report.get("state"),
        "round_path": report.get("round_path"),
        "noise_floor": block,
        # The block's own flip set, read through the one helper that decides
        # which *states* of the block license a case list. A "not_measured"
        # round names no cases, and going around the helper would hand it the
        # same confidence as a measured one.
        "unstable_cases": noise_floor_flipped_cases(block),
        "stability_limited_units": list(
            instrument.get("stability_limited_units") or ()
        ),
        "terminal_classification": dict(
            instrument.get("terminal_classification") or {}
        ),
        "fresh_control": report.get("fresh_control"),
    }


def render(payload: dict) -> str:
    lines: list[str] = []
    if payload["mode"] == "cycle":
        block = payload.get("noise_floor")
        lines.append(f"cycle report: {payload['path']}")
        lines.append(
            f"  cycle={payload.get('cycle')} state={payload.get('state')} "
            f"round_path={payload.get('round_path')}"
        )
        if block is None:
            lines.append(f"  noise_floor: absent ({payload.get('note')})")
            return "\n".join(lines)
        lines.append(
            f"  noise_floor: {block['status']} applicable={block['applicable']} "
            f"rolls={block['rolls']}"
        )
        if block.get("marginal_cases") is None:
            lines.append(f"    note: {block.get('reason')}")
        else:
            lines.append(
                f"    marginal_cases={block['marginal_cases']} "
                f"new_errors={block['new_errors']} "
                f"unresolved_added={block['unresolved_added']} "
                f"cases_flipped={block['cases_flipped']}"
            )
        for case in block.get("flipped_cases") or ():
            lines.append(f"    unstable case: {case}")
        for identifier in payload.get("stability_limited_units") or ():
            lines.append(f"    unit limited by instability: {identifier}")
        return "\n".join(lines)

    lines.append(
        f"{payload['roll_count']} roll(s); cases per roll: "
        f"{payload['cases_per_roll']}"
    )
    lines.append(
        f"  cases: {payload['total_cases']} total, "
        f"{len(payload['read_by_every_roll'])} read by every roll, "
        f"{payload['unstable_case_count']} disagreeing"
        + (
            f" ({payload['unstable_share']:.1%} of the total)"
            if payload.get("unstable_share") is not None else ""
        )
    )
    if payload["case_set_mismatch"]:
        lines.append(
            "  WARNING: rolls do not cover the same case set. Cases read by "
            "some rolls only are listed separately below; they are a coverage "
            "difference, not measured jitter."
        )
    for item in payload["cases"]:
        if not item["unstable"]:
            continue
        tallies = ", ".join(
            f"{state}={count}" for state, count in item["tallies"].items()
        )
        lines.append(
            f"  disagreeing  {item['case']} ({item['reads']} rolls): {tallies}"
        )
    if payload["one_sided_cases"]:
        lines.append(
            f"  one-sided ({len(payload['one_sided_cases'])} case(s), not "
            "counted as jitter): "
            + ", ".join(payload["one_sided_cases"][:8])
            + ("..." if len(payload["one_sided_cases"]) > 8 else "")
        )
    widest = payload.get("widest_pair")
    if widest:
        lines.append(
            f"  widest pair: rolls {widest['rolls'][0]} vs "
            f"{widest['rolls'][1]} -- {len(widest['flipped_cases'])} case(s) "
            f"unpaired or changed, transitions {widest['transitions']}"
        )
    if not payload["disagreeing_cases"]:
        lines.append(
            "  no case read by every roll disagreed: the floor reads zero for "
            "this case set. That is a statement about these rolls only -- a "
            "case that disagrees once in eight can pass a pair."
        )
    lines.append(
        "  A case listed above may not be scored alone: read it as an "
        "instrument limit, not as evidence about any candidate."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the detector's own jitter offline, from repeated rolls of "
            "one configuration or from a cycle report."
        ),
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    rolls = sub.add_parser("rolls", help="N independent run trees of one config")
    rolls.add_argument("roots", nargs="+", help="run tree(s) holding cases/<id>/result.json")
    rolls.add_argument("--json", action="store_true", help="print the payload as JSON")
    cycle = sub.add_parser("cycle", help="read what one cycle report published")
    cycle.add_argument("report", help="path to cycleNNN.json")
    cycle.add_argument("--json", action="store_true", help="print the payload as JSON")
    args = parser.parse_args(argv)

    if args.mode == "rolls":
        roots = [Path(item) for item in args.roots]
        for root in roots:
            if not root.exists():
                print(f"missing run tree: {root}", file=sys.stderr)
                return 2
        payload = report_rolls(roots)
    else:
        path = Path(args.report)
        if not path.is_file():
            print(f"missing cycle report: {path}", file=sys.stderr)
            return 2
        payload = report_cycle(path)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
