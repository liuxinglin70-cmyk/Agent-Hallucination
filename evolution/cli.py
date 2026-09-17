"""Command line entry for evolution.

Examples:

    python -m evolution smoke
    python -m evolution cycle --cycle 1 --backend mock --mini
    python -m evolution experiment --cycles 3 --backend detector
    python -m evolution governance --cycle 3 --backend detector
    python -m evolution final
    python -m evolution meta
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from evolution.config import load_config, resolve_path
from evolution.capability_surface import capability_surface_report
from evolution.contracts import empty_registry, save_json
from evolution.engine import EvolutionEngine, run_full_self_evolution
from evolution.ledger import Ledger, load_ledger
from evolution.meta_eval import write_meta_report
from evolution.registry import load_active_registry
from evolution.splits import build_split


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = load_active_registry(config)
    ledger = load_ledger(config)
    if args.reset:
        registry.clear()
    _print_json({
        "active": [str(c.get("intervention_id") or "") for c in registry.entries],
        "registry_hash": registry.registry_hash,
        "ledger_integrity": ledger.verify(),
    })
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.backend == "mock":
        config["proposer"]["mode"] = "offline"
    backend = _mock_backend(config) if args.backend == "mock" else None
    engine = EvolutionEngine(
        config,
        backend=backend,
        mini=bool(args.mini),
    )
    report = engine.run_cycle(int(args.cycle), **({"resume": True} if getattr(args, "resume", False) else {}))
    _print_json(report)
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.backend == "mock":
        config["proposer"]["mode"] = "offline"
    backend = _mock_backend(config) if args.backend == "mock" else None
    engine = EvolutionEngine(config, backend=backend, mini=bool(args.mini))
    report = engine.run_experiment(args.cycles, **({"resume": True} if getattr(args, "resume", False) else {}))
    out = resolve_path(config["work_root"]) / "reports" / "experiment.json"
    save_json(out, report)
    _print_json(report)
    if args.mini and report.get("mini_effect") and not report["mini_effect"].get("passed"):
        return 2
    return 0


def cmd_governance(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.backend == "mock":
        config["proposer"]["mode"] = "offline"
    backend = _mock_backend(config) if args.backend == "mock" else None
    engine = EvolutionEngine(config, backend=backend, mini=bool(args.mini))
    report = engine.run_governance(cycle=args.cycle)
    _print_json(report)
    return 0


def cmd_final(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    engine = EvolutionEngine(config, mini=False)
    report = engine.run_final()
    _print_json(report)
    return 0 if report["verdict"]["overall"] == "pass" else 2


def cmd_full_self_evolution(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = run_full_self_evolution(config=config)
    _print_json(report)
    return 0 if report.get("overall") == "pass" else 2


def cmd_meta(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = write_meta_report(
        _load_cycle_reports(config),
        load_ledger(config),
        resolve_path(config["work_root"]) / "reports" / "meta_eval.json",
    )
    _print_json(report)
    return 0


def _load_cycle_reports(config: dict[str, Any]) -> list[dict[str, Any]]:
    cycles_dir = resolve_path(config["work_root"]) / "cycles"
    reports = []
    if cycles_dir.exists():
        for path in sorted(cycles_dir.glob("cycle*.json")):
            reports.append(json.loads(path.read_text(encoding="utf-8-sig")))
    return reports


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = load_ledger(config).verify()
    _print_json(report)
    return 0 if report.get("valid") else 2


def cmd_preview(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.backend == "mock":
        config["proposer"]["mode"] = "offline"
    backend = _mock_backend(config) if args.backend == "mock" else None
    engine = EvolutionEngine(config, backend=backend, mini=bool(args.mini))
    report = engine.preview_cycle(int(args.cycle))
    _print_json(report)
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = load_active_registry(config)
    _print_json({
        "active": registry.registry,
        "state": registry.state,
    })
    return 0


def cmd_capability_audit(args: argparse.Namespace) -> int:
    _print_json(capability_surface_report())
    return 0


def _mock_backend(config: dict[str, Any]):
    from evolution.backends import make_backend

    return make_backend(config, mode="mock")


def cmd_smoke(args: argparse.Namespace) -> int:
    """Offline end-to-end gate validation with a deterministic toy detector.

    The smoke experiment deliberately plants two FN failure cases in the
    patrol pool and two FN cases in the gain set. A scripted offline
    proposer (same contract the LLM proposer must satisfy) turns the stable
    failure family into a text-boundary scanner intervention; the mock
    backend overrides exactly the planted cases, so the candidate must pass
    STRUCTURAL-HARMLESSNESS-BEHAVIOR-M and be admitted. Both injected defects must be rejected.
    No LLM call and no gold leakage beyond the mock oracle contract.
    """
    config = load_config(args.config)
    config = copy.deepcopy(config)
    # Smoke runs in an isolated workspace so a toy admission never pollutes
    # the real registry/ledger that a formal detector experiment would use.
    import shutil

    smoke_root = Path(config["work_root"]) / "smoke"
    shutil.rmtree(smoke_root, ignore_errors=True)
    config["work_root"] = str(smoke_root)
    config["registry_dir"] = str(smoke_root / "registry")
    config["ledger_path"] = str(smoke_root / "ledger.jsonl")
    split_cfg = config.setdefault("split", {})
    split_cfg.update({
        "sentinel_positives": 3,
        "sentinel_negatives": 3,
        "mini_gain_size": 12,
    })
    budget = config.setdefault("budget", {})
    budget.update({"patrol_cases": 8, "max_proposals": 3, "max_admits": 2})
    split = build_split(config=config, dataset_root=config["dataset_root"], mini=True)

    patrol_true = [
        case for case in split.patrol_pool
        if _gold(config, case) is True
    ][:2]
    gain_true = [
        case for case in split.gain
        if _gold(config, case) is True
    ][:2]
    if len(patrol_true) < 2 or len(gain_true) < 2:
        raise SystemExit(
            "mini split does not contain enough gold-positive cases for smoke; "
            "check datasets/protocols/development.json"
        )
    # Use the whole mini patrol pool so the two planted patrol failures are
    # guaranteed to form a stable failure family.
    budget.update({"patrol_cases": len(split.patrol_pool)})
    base: dict[str, bool | None] = {}
    for case in patrol_true + gain_true:
        base[case] = False  # planted false negatives for the bare base
    smoke_id = "IV-SMOKE-001"
    overrides = {
        smoke_id: {case: True for case in gain_true},
    }

    from evolution.backends import make_backend

    backend = make_backend(
        config, mode="mock", mock_base=base, mock_overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=_smoke_proposer(smoke_id, patrol_true),
    )
    report = engine.run_cycle(1)
    out = resolve_path(config["work_root"]) / "experiments" / "smoke_cycle.json"
    save_json(out, report)
    _print_json(report)
    print(f"\nsmoke report -> {out}")
    passed = (
        report.get("state") == "completed"
        and "IV-SMOKE-001" in (report.get("marginal_selection", {}).get("selected") or [])
        and bool((report.get("audit") or {}).get("passed"))
        and bool((report.get("ledger_integrity") or {}).get("valid"))
    )
    return 0 if passed else 2


def _smoke_proposer(intervention_id: str, failure_cases: list[str]):
    """Scripted offline stand-in with the LLM proposer's contract."""
    from evolution.contracts import make_intervention
    from evolution.proposer import ProposalBatch

    class _ScriptedProposer:
        name = "scripted_smoke"

        def propose(self, families, *, cycle, max_proposals=3):
            candidate = make_intervention(
                intervention_id=intervention_id,
                intervention_type="text_boundary",
                hook="scanner.system",
                packet_features=["has_tool_calls"],
                body={
                    "directive": (
                        "Report every explicitly required action from the "
                        "question that has no matching tool call."
                    ),
                    "not_when": (
                        "Never apply when every required action has a "
                        "matching tool call."
                    ),
                },
                expected_behavior=(
                    "recall scanner gains candidates for missing required "
                    "actions"
                ),
                target_polarity="recall",
                cycle=cycle,
                failure_family=(
                    str(families[0].get("failure_family") or "FAM-smoke")
                    if families else "FAM-smoke"
                ),
                failure_cases=list(failure_cases),
            )
            return ProposalBatch(
                candidates=[candidate],
                joint_groups=[],
                notes=[{
                    "intervention_id": intervention_id,
                    "generator": "scripted_smoke",
                }],
            )

    return _ScriptedProposer()


def _gold(config: dict[str, Any], case: str) -> bool | None:
    from evolution.splits import load_gold

    return load_gold(config["dataset_root"], case)


def cmd_capability_smoke(args: argparse.Namespace) -> int:
    """Run the offline capability-evolution gain experiment.

    This is a different validation from ``smoke``: the proposer defines a new
    typed-DSL predicate at runtime, the STRUCTURAL gate executes it on the patrol
    family, and the real InterventionPlan interpreter turns the admitted
    evidence+guard pair into measurable recall gain. No LLM call; the report
    is marked backend=mock and is not a real detector quality number.
    """
    config = load_config(args.config)
    smoke_root = resolve_path(config["work_root"]) / "capability_smoke"
    from evolution.smoke import run_capability_smoke

    report = run_capability_smoke(smoke_root)
    _print_json(report)
    selected = set(report.get("marginal_selection", {}).get("selected") or [])
    effect = report.get("active_effect_on_g") or {}
    mini = report.get("mini_effect") or {}
    passed = (
        {"IV-1-001", "IV-1-002"} <= selected
        and int(effect.get("marginal_cases") or 0) >= 2
        and int(effect.get("new_errors") or 0) == 0
        and bool(mini.get("passed"))
    )
    print(
        f"\ncapability-smoke verdict: {'PASS' if passed else 'FAIL'}; "
        f"selected={sorted(selected)}; "
        f"gain={effect.get('marginal_cases')}; "
        f"heldout={mini.get('effect', {}).get('marginal_cases')}; "
        f"report -> {smoke_root / 'capability_smoke_report.json'}"
    )
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evolution",
        description="Intervention-based self-evolution framework for detector.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--config")
    p.add_argument("--reset", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("cycle", help="run one evolution cycle")
    p.add_argument("--resume", action="store_true", help="resume a fingerprint-matched checkpoint without redrawing completed arms")
    p.add_argument("--cycle", type=int, required=True)
    p.add_argument("--backend", choices=["detector", "mock"], default=None)
    p.add_argument("--mini", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_cycle)

    p = sub.add_parser("experiment", help="run multiple evolution cycles")
    p.add_argument("--resume", action="store_true", help="reuse completed cycles and resume checkpointed gates")
    p.add_argument("--cycles", type=int, default=None)
    p.add_argument("--backend", choices=["detector", "mock"], default=None)
    p.add_argument("--mini", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser(
        "governance",
        help="post-cycle attribution, retirement and rollback accounting",
    )
    p.add_argument("--cycle", type=int, default=None)
    p.add_argument("--backend", choices=["detector", "mock"], default=None)
    p.add_argument("--mini", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_governance)

    p = sub.add_parser(
        "final", help="evaluate an explicitly configured independent sealed holdout"
    )
    p.add_argument("--config")
    p.set_defaults(func=cmd_final)

    p = sub.add_parser(
        "full-self-evolution",
        help="run evolution and evaluate the disjoint sealed holdout",
    )
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_full_self_evolution)

    p = sub.add_parser("meta", help="meta-evaluation of the evolution mechanism")
    p.add_argument("--mini", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_meta)

    p = sub.add_parser("verify-ledger", help="verify append-only ledger hash chain")
    p.add_argument("--config")
    p.set_defaults(func=cmd_verify_ledger)

    p = sub.add_parser(
        "preview",
        help="patrol and draft proposals without spending gate budget",
    )
    p.add_argument("--cycle", type=int, required=True)
    p.add_argument("--backend", choices=["detector", "mock"], default=None)
    p.add_argument("--mini", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("registry", help="show active registry")
    p.add_argument("--config")
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser(
        "capability-audit",
        help="report the executable evolution surface and its hard limits",
    )
    p.set_defaults(func=cmd_capability_audit)

    p = sub.add_parser("smoke", help="offline one-cycle gate validation")
    p.add_argument("--config")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser(
        "capability-smoke",
        help="offline self-evolution gain validation with a new typed-DSL predicate",
    )
    p.add_argument("--config")
    p.set_defaults(func=cmd_capability_smoke)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
