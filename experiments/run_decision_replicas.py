#!/usr/bin/env python3
"""Test-time-scaling replica experiment for flip-band cases.

Runs the frozen detector ``k`` times per flip-band case (each replica with an
isolated provider-cache root so identical payloads do not collapse into a
single cached response) and aggregates the three-state verdicts with the
fail-closed rules from ``src.sehd_v6.detector.replica_aggregation``:
majority wins, ties/disagreement/runtime-invalid replicas stay unresolved.

Flip-band case ids come from ``analyze_decision_profile.py`` output, or can
be supplied explicitly with ``--case-ids``. This script costs real provider
calls (up to k per case); use ``--max-cases`` as a hard budget cap.

Note on replica diversity: the detector is only stochastic when its provider
routes use temperature > 0. With temperature 0 all replicas are identical and
the aggregation degenerates to a single detection (the report records this).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sehd_v6.detector.batch import load_batch_records, run_batch
from src.sehd_v6.detector.replica_aggregation import aggregate_replicas
from src.sehd_v6.utils.ocse_packet import stable_hash

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

    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_hash = stable_hash(config)
    case_ids = _load_case_ids(args)[: args.max_cases]
    all_records = load_batch_records(args.records)
    missing = [case_id for case_id in case_ids if case_id not in all_records]
    if missing:
        print(f"cases missing from records manifest: {missing}", file=sys.stderr)
        return 1
    records = {case_id: all_records[case_id] for case_id in case_ids}

    # Sampled-only warning: with temperature 0 every replica is a cache copy.
    routes = config.get("routes") if isinstance(config.get("routes"), dict) else {}
    temperatures = [
        route.get("temperature")
        for route in routes.values()
        if isinstance(route, dict)
    ]
    deterministic = bool(temperatures) and all(
        temperature in (0, 0.0, None) for temperature in temperatures
    )

    original_global_cache = os.environ.get("PACA_GLOBAL_CACHE_ROOT")
    replica_summaries: list[dict[str, Any]] = []
    try:
        for index in range(args.k):
            replica_root = args.output_root / f"replica_{index}"
            # Isolate the global provider cache per replica so identical
            # payloads cannot collapse k runs into one cached response.
            os.environ["PACA_GLOBAL_CACHE_ROOT"] = str(replica_root / "provider_cache")
            summary = run_batch(
                records=records,
                config=config,
                output_dir=replica_root,
                max_parallel=args.max_parallel,
                resume=True,
            )
            replica_summaries.append(summary)
    finally:
        if original_global_cache is None:
            os.environ.pop("PACA_GLOBAL_CACHE_ROOT", None)
        else:
            os.environ["PACA_GLOBAL_CACHE_ROOT"] = original_global_cache

    predictions_by_case: dict[str, list[dict[str, Any]]] = {
        case_id: [] for case_id in case_ids
    }
    for summary in replica_summaries:
        for row in summary.get("predictions") or []:
            case_id = str(row.get("case_id"))
            if case_id in predictions_by_case:
                predictions_by_case[case_id].append(row.get("prediction") or {})

    cases: list[dict[str, Any]] = []
    for case_id in case_ids:
        replicas = predictions_by_case[case_id]
        if len(replicas) < args.k:
            aggregate = {
                "state": "unresolved",
                "policy": args.policy,
                "replica_count": len(replicas),
                "invalid_replica_indexes": [],
                "replica_states": [
                    str(prediction.get("state") or "unresolved")
                    for prediction in replicas
                ],
                "unanimous": False,
                "runtime_valid": False,
                "note": "incomplete_replicas",
            }
        else:
            aggregate = aggregate_replicas(replicas, policy=args.policy)
        cases.append({"case_id": case_id, "aggregate": aggregate})

    report = {
        "schema_version": "agenthallu.decision_replicas.v1",
        "config_hash": config_hash,
        "k": args.k,
        "policy": args.policy,
        "case_count": len(cases),
        "deterministic_route_warning": deterministic,
        "unanimous_cases": sum(case["aggregate"]["unanimous"] for case in cases),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"k={args.k} cases={len(cases)} unanimous={report['unanimous_cases']} "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
