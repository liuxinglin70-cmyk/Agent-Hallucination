"""Read saved detector requests without replaying cases or contacting providers."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3)


def timeline_summary(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    timed = 0
    for row in requests:
        start, end = row.get("started_seconds"), row.get("finished_seconds")
        if (type(start) in (int, float) and type(end) in (int, float)
                and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
            groups[int(row.get("case_attempt") or 1)].append((float(start), float(end)))
            timed += 1
    union_seconds = work_seconds = 0.0
    peak = 0
    for intervals in groups.values():
        events = sorted([(start, 1) for start, end in intervals if end > start]
                        + [(end, -1) for start, end in intervals if end > start])
        active = 0
        previous = 0.0
        for when, change in events:
            if active:
                union_seconds += when - previous
            active += change
            peak = max(peak, active)
            previous = when
        work_seconds += sum(end - start for start, end in intervals)
    return {"timed_requests": timed, "untimed_requests": len(requests) - timed,
            "request_interval_union_seconds": round(union_seconds, 3),
            "overlap_work_seconds": round(work_seconds - union_seconds, 3),
            "peak_overlapping_request_intervals": peak}


def summarize_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stages: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    wall = []
    missing_execution = 0
    duplicate_ids = 0
    timelines = []
    for result in results:
        execution = result.get("execution") or {}
        requests = execution.get("requests")
        if not isinstance(requests, list):
            missing_execution += 1
            continue
        if isinstance(execution.get("elapsed_seconds"), (int, float)):
            wall.append(float(execution["elapsed_seconds"]))
        ids = [row["request_id"] for row in requests if row.get("request_id")]
        duplicate_ids += len(ids) - len(set(ids))
        timelines.append(timeline_summary(requests))
        for request in requests:
            stages[str(request.get("purpose") or "unknown")].append(request)
    by_purpose = {}
    for purpose, requests in sorted(stages.items()):
        elapsed = [float(row["elapsed_seconds"]) for row in requests
                   if isinstance(row.get("elapsed_seconds"), (int, float))]
        queued = [float(row["queue_seconds"]) for row in requests
                  if isinstance(row.get("queue_seconds"), (int, float))]
        by_purpose[purpose] = {
            "requests": len(requests), "timed_requests": len(elapsed),
            "request_seconds_sum": round(sum(elapsed), 3),
            "queue_seconds_sum": round(sum(queued), 3), "queue_observations": len(queued),
            "latency_median_seconds": round(statistics.median(elapsed), 3) if elapsed else None,
            "latency_p95_seconds": percentile(elapsed, 0.95),
            "transport_retry_requests": sum(int(row.get("attempt") or 1) > 1 for row in requests),
            "status_counts": dict(Counter(str(row.get("status") or "unknown") for row in requests)),
            "usage_missing": sum(not isinstance(row.get("usage"), Mapping) for row in requests),
            "completion_tokens": sum(float((row.get("usage") or {}).get("completion_tokens") or 0) for row in requests),
        }
    request_count = sum(row["requests"] for row in by_purpose.values())
    return {
        "schema_version": "detector.call_analysis.v1", "cases": len(results),
        "cases_without_request_observations": missing_execution, "duplicate_request_ids": duplicate_ids,
        "requests": request_count,
        "repair_requests": sum(row["requests"] for purpose, row in by_purpose.items() if "repair" in purpose),
        "case_seconds_sum": round(sum(wall), 3),
        "case_latency_median_seconds": round(statistics.median(wall), 3) if wall else None,
        "case_latency_p95_seconds": percentile(wall, 0.95),
        "by_purpose": by_purpose,
        "timeline": {
            "timed_requests": sum(row["timed_requests"] for row in timelines),
            "untimed_requests": sum(row["untimed_requests"] for row in timelines),
            "request_interval_union_seconds_sum": round(sum(row["request_interval_union_seconds"] for row in timelines), 3),
            "overlap_work_seconds_sum": round(sum(row["overlap_work_seconds"] for row in timelines), 3),
            "peak_per_case_request_intervals": max((row["peak_overlapping_request_intervals"] for row in timelines), default=0),
        },
        "interpretation": "Request seconds and case seconds are work sums, not batch wall time. Timeline unions are per case attempt and include queue waits; they are neither provider occupancy nor the full DAG critical path. Historical observations without complete interval timestamps remain untimed.",
    }


def analyze_run(run_dir: Path) -> dict[str, Any]:
    paths = sorted((run_dir / "cases").glob("*/result.json"))
    if not paths:
        raise ValueError(f"no canonical case results under {run_dir}")
    results = []
    inputs = []
    for path in paths:
        data = path.read_bytes()
        results.append(json.loads(data.decode("utf-8-sig")))
        inputs.append({"path": str(path.relative_to(run_dir)), "sha256": hashlib.sha256(data).hexdigest()})
    return {"run_dir": str(run_dir.resolve()), "inputs": inputs, **summarize_results(results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"runs": [analyze_run(path) for path in args.run_dirs]}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
