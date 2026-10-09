#!/usr/bin/env python
"""Freeze the longest authoritative measured prefix of a registered order.

All selected runs share one locked snapshot. Missing, failed and degraded
results stop the prefix; positive and negative readings count equally.
The prefix follows the registered order without selecting by correctness.

    python scripts/freeze_measured_prefix.py --baseline artifacts/runs/g0 --order measurement.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.snapshot import run_snapshot  # noqa: E402
from experiments.plan import load_measurement, measured_prefix  # noqa: E402
from detector.result import prediction_state  # noqa: E402
from detector.runtime import atomic_json, exclusive_writer  # noqa: E402
from evolution.splits import load_gold  # noqa: E402

DEFAULT_DATASET_ROOT = ROOT / "datasets/raw/agenthallu/AgentHallu"


def completed_prefix(
    *, baseline: Path, order: list[str],
) -> tuple[list[str], list[str]]:
    """The longest leading run of *order* whose cases all have an authoritative reading.

    Returns ``(prefix, first_blocking)``. The string of the first missing case
    is returned even though the prefix stops there, because a gap in the middle
    means the run is still filling in -- and a reader who only saw the length
    would take the truncated prefix for the intended sample.
    """
    with run_snapshot({"baseline": baseline}, order) as snapshot:
        prefix = measured_prefix(order, [row["case"] for row in snapshot.rows["baseline"]
                                         if prediction_state(row["result"]) is not None])
    return prefix, order[len(prefix):len(prefix) + 1]


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
    with run_snapshot({str(path): path for path in baselines}, order) as snapshot:
        per_arm = {}
        for name, rows in snapshot.rows.items():
            prefix = measured_prefix(order, [row["case"] for row in rows if prediction_state(row["result"]) is not None])
            per_arm[name] = prefix, order[len(prefix):len(prefix) + 1]
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
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT),
                        help="corpus used to count labels in the exported prefix")
    parser.add_argument(
        "--min-cases", type=int, default=0,
        help="refuse to write a prefix shorter than this",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.min_cases < 0:
        parser.error("--min-cases must be nonnegative")
    try:
        measurement = load_measurement(args.order)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    order = measurement.cases
    baselines = [Path(item) for item in args.baseline]
    prefix, detail = common_prefix(baselines=baselines, order=order)
    if len(prefix) < args.min_cases:
        parser.error(
            f"the arms share only {len(prefix)} completed case(s), below "
            f"--min-cases {args.min_cases}; nothing written"
        )
    if args.out and not args.dry_run:
        requested = Path(args.out)
        out = requested.parent.resolve() / requested.name
        if any(out.is_relative_to(path.resolve()) for path in [*baselines, Path(args.dataset_root)]):
            parser.error("prefix output must be outside run roots and the raw corpus")
        try:
            selected = measurement.select(prefix)
            payload = selected.to_manifest(gold={case: load_gold(args.dataset_root, case) for case in prefix})
            payload.update(derived_from=[str(path) for path in baselines],
                           order=str(args.order), scheduling=detail)
            with exclusive_writer(out.parent):
                if out.exists() or out.is_symlink():
                    raise ValueError("prefix output already exists; frozen evidence cannot be replaced")
                atomic_json(out, payload)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    print(json.dumps({
        "prefix_size": len(prefix),
        "order_size": len(order),
        "scheduling": detail,
        "out": None if args.dry_run else args.out,
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
