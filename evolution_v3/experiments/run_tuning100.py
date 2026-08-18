"""Execute the 100-case tuning experiment with a cached baseline backend.

Modes:
    preview   -- offline patrol + proposal preview (no LLM for baseline)
    probe     -- run each proposed candidate only on its P nomination cases
    cycle     -- full gate cycle(s) against the 100-case split
    validate  -- run the external 50-case validation set
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector_v2.interventions import build_intervention_registry  # noqa: E402
from evolution_v3.backends import CachedRunBackend  # noqa: E402
from evolution_v3.config import load_config, resolve_path  # noqa: E402
from evolution_v3.contracts import empty_registry, save_json  # noqa: E402
from evolution_v3.engine import EvolutionEngine  # noqa: E402
from evolution_v3.metrics import paired_delta  # noqa: E402
from evolution_v3.splits import EvolutionSplit, load_gold  # noqa: E402
from evolution_v3.worksheet import (  # noqa: E402
    diagnose_failure_families,
    stable_failure_families,
)

TUNING_MANIFEST = (
    ROOT / "datasets" / "eval_splits" / "evolution_v3_tuning100.json"
)
VALIDATION_MANIFEST = (
    ROOT / "datasets" / "eval_splits" / "evolution_v3_validation50.json"
)
RUN100 = ROOT / "runs_v2" / "v2_100_20w"
VAL_BASE_RUN = ROOT / "evolution_v3" / "work" / "valruns" / "evo3_val_base"


def _load_tuning_split(config: dict[str, Any]) -> EvolutionSplit:
    manifest = json.loads(TUNING_MANIFEST.read_text(encoding="utf-8"))
    return EvolutionSplit(
        name="tuning100",
        pool=list(manifest["cases"]),
        sentinel=list(manifest["sentinel"]),
        patrol_pool=list(manifest["patrol"]),
        gain=list(manifest["gate"]),
        mini=True,
        seed=int(manifest["seed"]),
    )


def _make_backend(config: dict[str, Any]) -> CachedRunBackend:
    manifest = json.loads(TUNING_MANIFEST.read_text(encoding="utf-8"))
    config["backend"]["workers"] = 20
    return CachedRunBackend(
        config,
        RUN100,
        list(manifest["cases"]),
    )


def _proposals(config: dict[str, Any], backend, cycle: int):
    split = _load_tuning_split(config)
    rows = backend.evaluate(
        split.patrol_pool,
        dataset_root=config["dataset_root"],
        registry=empty_registry(),
        gold_map={
            case: load_gold(config["dataset_root"], case)
            for case in split.patrol_pool
        },
        workers=20,
    )
    families = stable_failure_families(rows)
    diagnosed = diagnose_failure_families(
        families, rows, dataset_root=config["dataset_root"],
    )
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    batch = engine.proposer.propose(diagnosed, cycle=cycle, max_proposals=3)
    return split, rows, diagnosed, batch


def cmd_preview(args: argparse.Namespace) -> int:
    config = load_config()
    backend = _make_backend(config)
    _split, _rows, diagnosed, batch = _proposals(config, backend, args.cycle)
    print(json.dumps({
        "families": [
            {
                "family": item["failure_family"],
                "count": item["count"],
                "predicate_signals": item["diagnostics"]["predicate_signals"],
            }
            for item in diagnosed
        ],
        "candidates": batch.candidates,
        "joint_groups": batch.joint_groups,
        "notes": batch.notes,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    config = load_config()
    backend = _make_backend(config)
    split, rows, _diagnosed, batch = _proposals(config, backend, args.cycle)
    gold_map = {
        case: load_gold(config["dataset_root"], case)
        for case in split.patrol_pool
    }
    report = []
    for candidate in batch.candidates[: args.limit]:
        family = (candidate.get("provenance") or {}).get("failure_family")
        target_cases = [
            case for case in split.patrol_pool
            if case in set((candidate.get("provenance") or {}).get("failure_cases") or [])
        ]
        if not target_cases:
            target_cases = split.patrol_pool
        baseline = backend.evaluate(
            target_cases,
            dataset_root=config["dataset_root"],
            registry=empty_registry(),
            gold_map=gold_map,
            workers=20,
        )
        active = backend.evaluate(
            target_cases,
            dataset_root=config["dataset_root"],
            registry=build_intervention_registry([candidate]),
            gold_map=gold_map,
            workers=20,
        )
        delta = paired_delta(baseline, active)
        report.append({
            "intervention_id": candidate.get("intervention_id"),
            "family": family,
            "template": candidate.get("hook"),
            "target_cases": target_cases,
            "delta": {
                "marginal_cases": delta["marginal_cases"],
                "new_fn": delta["new_fn"],
                "new_fp": delta["new_fp"],
                "flips": delta["flips"],
            },
        })
        print(json.dumps(report[-1], ensure_ascii=False, indent=2), flush=True)
    save_json(
        resolve_path(config["work_root"])
        / "experiments" / "probe_tuning100.json",
        report,
    )
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    config = load_config()
    config["backend"]["workers"] = 20
    config["budget"]["patrol_cases"] = 40
    config["budget"]["stable_failure_rerun_cases"] = 0
    config["budget"]["max_proposals"] = args.max_proposals
    if args.max_admits is not None:
        config["budget"]["max_admits"] = args.max_admits
    config["gates"]["m_min_marginal_cases"] = args.m_min_marginal
    if args.no_audit:
        config["audit"]["enabled"] = False
    backend = _make_backend(config)
    split = _load_tuning_split(config)
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    report = engine.run_cycle(args.cycle)
    save_json(
        resolve_path(config["work_root"])
        / "experiments" / f"cycle{args.cycle:03d}_tuning100.json",
        report,
    )
    # Windows consoles commonly default to GBK; persist the report before
    # printing and make the human-readable output independent of that locale.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config()
    config["backend"]["workers"] = 20
    manifest = json.loads(VALIDATION_MANIFEST.read_text(encoding="utf-8"))
    cases = list(manifest["cases"])
    gold_map = {
        case: bool(manifest["gold"][case]) for case in cases
    }
    # The 50-case baseline already exists in valruns/evo3_val_base and the
    # user explicitly wants to reuse it. Active arm is a frozen-stage replay
    # of that baseline, so this validation consumes zero new LLM tokens.
    backend = CachedRunBackend(config, VAL_BASE_RUN, cases)
    empty = empty_registry()
    active = json.loads(
        (resolve_path(config["registry_dir"]) / "active.json").read_text(
            encoding="utf-8-sig"
        )
    )
    baseline = backend.evaluate(
        cases,
        dataset_root=config["dataset_root"],
        registry=empty,
        gold_map=gold_map,
        workers=20,
    )
    active_rows = backend.evaluate(
        cases,
        dataset_root=config["dataset_root"],
        registry=active,
        gold_map=gold_map,
        workers=20,
    )
    effect = paired_delta(baseline, active_rows)
    print(json.dumps({
        "baseline_metrics": effect["baseline_metrics"],
        "active_metrics": effect["active_metrics"],
        "effect": {
            "marginal_cases": effect["marginal_cases"],
            "new_fn": effect["new_fn"],
            "new_fp": effect["new_fp"],
            "tp_lost": effect["tp_lost"],
            "unresolved_added": effect["unresolved_added"],
            "flips": effect["flips"],
        },
        "baseline_source": str(VAL_BASE_RUN),
    }, ensure_ascii=False, indent=2))
    save_json(
        resolve_path(config["work_root"])
        / "experiments" / "validation50_frozen_report.json",
        {
            "effect": effect,
            "baseline_source": str(VAL_BASE_RUN),
            "baseline_rows": baseline,
            "active_rows": active_rows,
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("preview")
    p.add_argument("--cycle", type=int, default=1)
    p.set_defaults(func=cmd_preview)
    p = sub.add_parser("probe")
    p.add_argument("--cycle", type=int, default=1)
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_probe)
    p = sub.add_parser("cycle")
    p.add_argument("--cycle", type=int, required=True)
    p.add_argument("--max-proposals", type=int, default=3)
    p.add_argument(
        "--max-admits", type=int, default=None,
        help="maximum qualified interventions admitted in this cycle",
    )
    p.add_argument("--m-min-marginal", type=int, default=2)
    p.add_argument("--no-audit", action="store_true")
    p.set_defaults(func=cmd_cycle)
    p = sub.add_parser("validate")
    p.set_defaults(func=cmd_validate)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
