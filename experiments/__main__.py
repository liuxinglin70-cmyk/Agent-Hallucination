"""``python -m experiments`` -- the single entry point for real experiments.

    python -m experiments plan                 # what is pre-registered, and why
    python -m experiments status               # how far each arm actually got
    python -m experiments baseline --workers 4 # measure the baseline arm
    python -m experiments paired               # the effect reading, on real rows

The five scripts this replaces still exist and still work; they are the
archive of how each reading was originally taken. New work should use this
entry point, because it is the only one that reports every requested case as
measured or not measured in one place.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.plan import load_plan
from detector.config import load_config
from experiments.runner import (
    _shared_registry,
    paired_sample,
    plan_status,
    run_measurement_stage,
)


def _print(payload) -> None:
    import json
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_plan(args: argparse.Namespace) -> int:
    plan = load_plan(plan_path=args.plan, measurement_path=args.measurement)
    payload = plan.describe()
    payload["source"] = str(plan.source) if plan.source else None
    payload["notes"] = list(plan.notes)
    _print(payload)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    plan = load_plan(plan_path=args.plan, measurement_path=args.measurement)
    _print(plan_status(plan, args.runs_root))
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    plan = load_plan(plan_path=args.plan, measurement_path=args.measurement)
    cases = plan.measurement.cases
    if args.cases is not None:
        cases = plan.measurement.prefix(args.cases).cases
    outcome = run_measurement_stage(
        plan=plan, stage="baseline", arm=args.arm, cases=cases,
        runs_root=args.runs_root, run_id=args.run_id,
        dataset_root=args.dataset_root,
        config=load_config(args.config, require_credentials=False) if args.config else None,
        interventions_path=args.interventions,
        workers=args.workers, deadline_seconds=args.deadline_seconds,
        retry_unresolved=args.retry_unresolved,
        report_path=args.report,
    )
    _print(outcome.report)
    return 2 if outcome.report.get("stop") else 0


def cmd_paired(args: argparse.Namespace) -> int:
    plan = load_plan(plan_path=args.plan, measurement_path=args.measurement)
    arms = list(args.arm) if args.arm else ["g0", "g1"]
    if len(arms) != 2 or len(set(arms)) != 2:
        raise ValueError("paired requires exactly two distinct arms")
    sample = paired_sample(plan, args.runs_root, arms)
    if sample["size"] == 0:
        _print({
            **sample,
            "reading": None,
            "reason": (
                "no case is measured by every named arm, so no paired reading "
                "exists; this is 'not measured', not 'no effect'"
            ),
        })
        return 0
    from detector.evaluation import evaluate_run
    from evolution.metrics import paired_delta
    runs_root = Path(args.runs_root)
    scored = {}
    for arm in arms:
        scored[arm] = evaluate_run(
            runs_root=runs_root, run_id=arm, dataset_root=args.dataset_root,
            cases=sample["common"],
        )
    baseline, deployed = arms[0], arms[-1]
    delta = paired_delta(scored[baseline]["rows"], scored[deployed]["rows"])
    fixed = int(delta.get("decisive_fixed") or 0)
    broken = int(delta.get("decisive_broken") or 0)
    same_registry = _shared_registry(plan, arms)
    payload = {
        "arms": arms,
        "paired_cases": sample["size"],
        "bias_note": sample["note"],
        "decisive_fixed": fixed,
        "decisive_broken": broken,
        "net_decisive": fixed - broken,
        "rule": plan.stage("effect")["rule"],
        "minimum_detectable": plan.stage("effect")["minimum_detectable"],
        "baseline_metrics": scored[baseline]["metrics"],
        "deployed_metrics": scored[deployed]["metrics"],
    }
    if same_registry:
        # Same configuration on both sides: this is the instrument's jitter.
        # Presented as an effect it would be the most quotable number in the
        # report and the least meaningful, so it is named for what it is and
        # the effect verdict is withheld rather than computed.
        payload.update({
            "reading": "noise_floor",
            "effect_established": None,
            "reason": (
                "every named arm deploys the same registry, so this pair "
                "measures the instrument's own jitter on these cases; it is "
                "not an effect reading and must not be quoted as one"
            ),
            "flips_on_stable_cases": {
                "fixed": fixed, "broken": broken,
            },
        })
    else:
        payload.update({
            "reading": "effect",
            "effect_established": fixed > broken,
        })
    _print(payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiments", description=__doc__)
    parser.add_argument("--plan", default=None)
    parser.add_argument("--measurement", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("plan", help="print the pre-registered plan")

    status = sub.add_parser("status", help="how far each arm has measured")
    status.add_argument("--runs-root", default="artifacts/baseline/runs")

    baseline = sub.add_parser("baseline", help="run a measurement arm")
    baseline.add_argument("--arm", default="g0")
    baseline.add_argument("--config", default=None, help="explicit detector configuration")
    baseline.add_argument("--interventions", default=None, help="registry required by an active arm")
    baseline.add_argument("--run-id", default=None)
    baseline.add_argument("--runs-root", default="artifacts/baseline/runs")
    baseline.add_argument(
        "--dataset-root", default="datasets/raw/agenthallu/AgentHallu",
    )
    baseline.add_argument("--workers", type=int, default=4)
    baseline.add_argument("--cases", type=int, default=None,
                          help="measure only the first N of the frozen order")
    baseline.add_argument("--deadline-seconds", type=float, default=None)
    baseline.add_argument("--retry-unresolved", action="store_true")
    baseline.add_argument("--report", default=None)

    paired = sub.add_parser("paired", help="paired decisive-flip reading")
    paired.add_argument("--arm", action="append", default=None)
    paired.add_argument("--runs-root", default="artifacts/baseline/runs")
    paired.add_argument(
        "--dataset-root", default="datasets/raw/agenthallu/AgentHallu",
    )

    args = parser.parse_args(argv)
    dispatch = {
        "plan": cmd_plan, "status": cmd_status,
        "baseline": cmd_baseline, "paired": cmd_paired,
    }
    try:
        return int(dispatch[args.command](args))
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
