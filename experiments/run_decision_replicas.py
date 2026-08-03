#!/usr/bin/env python3
"""Test-time-scaling replica experiment for flip-band cases.

Runs the frozen detector ``k`` times per flip-band case and aggregates the
three-state verdicts with the fail-closed rules from
``src.sehd_v6.detector.replica_aggregation``: majority wins,
ties/disagreement/runtime-invalid replicas stay unresolved.

The replica orchestration itself (per-replica provider-cache isolation,
serial replica execution, aggregation, degradation guard) is the
library-level ``run_replicated_batch`` in ``src.sehd_v6.detector.batch``;
this script is a thin CLI shell over it.

Flip-band case ids come from ``analyze_decision_profile.py`` output, or can
be supplied explicitly with ``--case-ids``. This experiment costs real
provider calls (up to k per case); use ``--max-cases`` as a hard budget cap.

Note on replica diversity: the detector is only stochastic when its provider
routes use temperature > 0. With temperature 0 all replicas would be
identical, so ``run_replicated_batch`` degrades to a single replica and
records the reason instead of burning k identical calls (the report records
this under ``replica_degradation`` / ``deterministic_route_warning``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sehd_v6.detector import load_detector_config
from src.sehd_v6.detector.batch import (
    _all_routes_deterministic,
    load_batch_records,
    run_replicated_batch,
)

# Re-export for backward compatibility: the orchestration moved into the
# library, but existing tests import the collector from this module.
from src.sehd_v6.detector.batch import collect_replica_predictions  # noqa: F401

MAX_K = 5


def _load_case_ids(args: argparse.Namespace) -> list[str]:
    if args.case_ids:
        return list(dict.fromkeys(args.case_ids))
    if args.flip_band and args.flip_band.exists():
        report = json.loads(args.flip_band.read_text(encoding="utf-8"))
        return [str(case_id) for case_id in report.get("flip_band_case_ids") or []]
    raise SystemExit("provide --case-ids or --flip-band <decision_profile_report.json>")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True,
                        help="Batch manifest/JSONL/directory accepted by load_batch_records.")
    parser.add_argument("--flip-band", type=Path, default=None)
    parser.add_argument("--case-ids", nargs="*", default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--max-cases", type=int, default=5,
                        help="Hard budget cap on the number of flip-band cases.")
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--policy", choices=["majority", "consensus"],
                        default="majority")
    parser.add_argument("--output-root", type=Path,
                        default=Path("experiments/results/decision_replicas"))
    parser.add_argument("--output", type=Path,
                        default=Path("experiments/results/decision_replicas_report.json"))
    args = parser.parse_args()

    if not 1 <= args.k <= MAX_K:
        print(f"--k must be in 1..{MAX_K}", file=sys.stderr)
        return 2
    if args.max_cases < 1:
        print("--max-cases must be at least 1", file=sys.stderr)
        return 2

    # Public API resolution keeps the documented precedence chain intact
    # (SEHD_ROUTE_<ROUTE>_* overrides included); a raw json.loads would lose
    # the route-level selectors at the transport boundary.
    config = load_detector_config(args.config)
    case_ids = _load_case_ids(args)[: args.max_cases]
    all_records = load_batch_records(args.records)
    missing = [case_id for case_id in case_ids if case_id not in all_records]
    if missing:
        print(f"cases missing from records manifest: {missing}", file=sys.stderr)
        return 1
    records = {case_id: all_records[case_id] for case_id in case_ids}

    # Library-level orchestration: per-replica provider-cache isolation,
    # fail-closed aggregation, and the temperature-zero degradation guard.
    report = run_replicated_batch(
        records=records,
        config=config,
        output_root=args.output_root,
        replicas=args.k,
        replica_policy=args.policy,
        max_parallel=args.max_parallel,
        resume=True,
    )

    # Keep the historical report field for downstream readers: the warning is
    # true whenever the routes cannot produce replica diversity.
    report["deterministic_route_warning"] = _all_routes_deterministic(config)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cases = report["cases"]
    print(
        f"k={report['replicas_effective']} cases={len(cases)} "
        f"unanimous={report['unanimous_cases']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
