"""One-line-per-check status for the running experiment (read-only).

Prints the handful of numbers that decide whether the run is healthy, so a
check costs one command instead of a page of analysis:

  * rows landed vs expected, and the wave they belong to
  * per-case latency spread and whether anything sits at a deadline
  * unresolved count (a cut row is unresolved, and HARMLESSNESS reads a new
    unresolved as a candidate-caused regression)
  * live worker count and ages against the call ceiling
  * the cycle checkpoint's own elapsed budget
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "output" / "selfevo-run"))

import progress_dashboard as dash  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default="artifacts/selfevo-bigrun5")
    parser.add_argument("--expected", type=int, default=40)
    parser.add_argument("--call-ceiling", type=float, default=2400.0)
    args = parser.parse_args()

    root = Path(args.work_root)
    rows = []
    for path in glob.glob(str(root / "checkpoints" / "**" / "arms" / "*" / "*.json"),
                          recursive=True):
        try:
            rows.append(json.loads(Path(path).read_text(encoding="utf-8-sig"))["row"])
        except (OSError, ValueError, KeyError):
            continue
    elapsed = sorted(
        float(r["elapsed_seconds"]) for r in rows
        if isinstance(r.get("elapsed_seconds"), (int, float))
    )
    unresolved = [r["case"] for r in rows if r.get("prediction") is None]
    correct = sum(
        1 for r in rows
        if r.get("prediction") is not None and r.get("prediction") == r.get("gold")
    )
    snapshot = dash.live_concurrency()
    ages = sorted(round(w["age_seconds"]) for w in snapshot["workers"])

    print(json.dumps({
        "landed": f"{len(rows)}/{args.expected}",
        "correct": f"{correct}/{len(rows)}",
        "unresolved": unresolved or 0,
        "latency_s": {
            "n": len(elapsed),
            "median": round(elapsed[len(elapsed) // 2], 0) if elapsed else None,
            "max": round(elapsed[-1], 0) if elapsed else None,
            "over_900": sum(1 for e in elapsed if e > 900),
            "over_1800": sum(1 for e in elapsed if e > 1800),
            "at_old_3600_cap": sum(1 for e in elapsed if e >= 3599),
        },
        "workers": len(snapshot["workers"]),
        "worker_ages": ages,
        "worker_over_call_ceiling": sum(1 for a in ages if a > args.call_ceiling),
    }, ensure_ascii=False), flush=True)

    checkpoints = sorted(glob.glob(str(root / "checkpoints" / "*" / "checkpoint.json")))
    for path in checkpoints:
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        print(json.dumps({
            "checkpoint": Path(path).parent.name,
            "status": state.get("status"),
            "used_minutes": round(float(state.get("used_seconds") or 0) / 60, 1),
        }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
