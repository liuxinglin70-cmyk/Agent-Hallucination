#!/usr/bin/env python
"""Freeze the measured prefix, so the deployed arm can be paired against it.

The baseline walks the pre-registered order and stops wherever the clock runs
out. The deployed arm must then be run on *exactly* the cases the baseline
actually finished -- not on round numbers, and not on "roughly the same" cases.

Two reasons it is derived from the finished results rather than chosen:

* ``run_batch`` refuses a resumed run whose ``run.json`` case list differs, so
  an inexact list is not a measurement, it is a crash.
* Picking a convenient subset after seeing the baseline is the selection the
  pre-registered order exists to prevent. The prefix rule ("everything the
  baseline completed, in registered order") cannot be steered.

Only the *presence* of a result is read here, never its verdict, so the prefix
is fixed before anyone knows how the two arms will differ.

    python scripts/freeze_measured_prefix.py \
        --baseline artifacts/baseline/runs/g0 \
        --order datasets/protocols/measurement_v1.json \
        --out artifacts/baseline/runs/prefix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.runner import safe_case_id  # noqa: E402


def completed_prefix(
    *, baseline: Path, order: list[str],
) -> tuple[list[str], list[str]]:
    """The longest leading run of *order* whose cases all have a result.

    Returns ``(prefix, first_blocking)``. The string of the first missing case
    is returned even though the prefix stops there, because a gap in the middle
    means the run is still filling in -- and a reader who only saw the length
    would take the truncated prefix for the intended sample.
    """
    prefix: list[str] = []
    for case in order:
        result = (
            baseline / "cases" / safe_case_id(case) / "result.json"
        )
        if not result.is_file():
            return prefix, [case]
        prefix.append(case)
    return prefix, []


def common_prefix(*, baselines: list[Path], order: list[str]) -> tuple[list[str], dict]:
    """The longest leading run of *order* every arm has finished.

    The paired endpoint refuses to compare arms over different case sets, and
    two independent rolls of the same order finish at different points (they
    are separate processes racing the same slow cases). Intersecting them here
    is what keeps the pairing exact without anybody hand-picking cases: an arm
    that is a few cases ahead simply does not extend the measurable prefix.

    Also reports, per arm, how much further it got -- the gap is a scheduling
    fact worth seeing, not something to average away.
    """
    per_arm = {
        str(path): completed_prefix(baseline=path, order=order)
        for path in baselines
    }
    lengths = {path: len(prefix) for path, (prefix, _) in per_arm.items()}
    if not lengths:
        return [], {"per_arm": {}, "blocking": {}}
    shared = min(lengths.values())
    prefix = order[:shared]
    return prefix, {
        "per_arm_completed": lengths,
        "per_arm_first_missing": {
            path: blocking for path, (_, blocking) in per_arm.items()
        },
        "shortest_arm": min(lengths, key=lambda path: lengths[path]),
        "beyond_prefix": {
            path: length - shared for path, length in lengths.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", required=True, action="append",
        help="a run tree; repeat to intersect several arms (the paired "
             "endpoint needs the same case set on every arm)",
    )
    parser.add_argument(
        "--order", default=str(ROOT / "datasets/protocols/measurement_v1.json"),
    )
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--min-cases", type=int, default=0,
        help="refuse to write a prefix shorter than this",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    order = [
        str(case)
        for case in json.loads(
            Path(args.order).read_text(encoding="utf-8-sig")
        )["cases"]
    ]
    baselines = [Path(item) for item in args.baseline]
    prefix, detail = common_prefix(baselines=baselines, order=order)
    if len(prefix) < args.min_cases:
        raise SystemExit(
            f"the arms share only {len(prefix)} completed case(s), below "
            f"--min-cases {args.min_cases}; nothing written"
        )
    if args.out and not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({
                "schema_version": "detector.measurement_manifest.v1",
                "policy": (
                    "The prefix of the pre-registered order that every arm "
                    "completed. Derived from result presence only, never from a "
                    "verdict, so the arms are paired over the same cases "
                    "without anyone choosing them."
                ),
                "derived_from": [str(item) for item in baselines],
                "order": str(args.order),
                "prefix_size": len(prefix),
                "scheduling": detail,
                "cases": prefix,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "prefix_size": len(prefix),
        "order_size": len(order),
        "scheduling": detail,
        "out": None if args.dry_run else args.out,
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
