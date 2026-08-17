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
from evolution_v3.backends import CachedRunBackend, DetectorV2Backend  # noqa: E402
from evolution_v3.config import load_config, resolve_path  # noqa: E402
from evolution_v3.contracts import (  # noqa: E402
    empty_registry,
    make_intervention,
    make_registry,
    save_json,
)
from evolution_v3.engine import EvolutionEngine  # noqa: E402
from evolution_v3.metrics import paired_delta  # noqa: E402
from evolution_v3.proposer import ProposalBatch  # noqa: E402
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


def _cycle2_preset(cycle: int) -> ProposalBatch:
    """Cycle-2 rare-family preset.

    The default template proposer only sees >=2-case families inside the
    40-case patrol slice. These candidates cover two single-slice mechanisms
    that are nevertheless present in the full 100-case split and in the
    cached validation baseline. They are pre-committed as one joint group so
    M must show their combined marginal gain; V1/V2/V3 still run normally.
    """
    restore_judge = make_intervention(
        intervention_id="IV-2-001",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        facts=[
            "judge_positive=true",
            "defense_verdict=refuted",
            "judge_confidence=high",
        ],
        body={
            "action": "restore_judge_conviction",
            "action_not_when": (
                "Do not apply when the positive source is recall_upgrade and "
                "required_action_present is satisfied or not_applicable."
            ),
        },
        expected_behavior=(
            "when defense refutes a high-confidence judge conviction, the "
            "guard restores the judge conviction; symmetric with the "
            "recall-upgrade restoration guard already admitted"
        ),
        target_polarity="recall",
        cycle=cycle,
        failure_family="FAM-fn-defense_refuted_judge",
        failure_cases=["Camel/052.json", "BFCL/066.json"],
    )
    anchor_evidence = make_intervention(
        intervention_id="IV-2-002",
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "delivered_anchor_consistency",
            "runner": "deterministic",
            "output_verdicts": ["consistent", "inconsistent", "insufficient"],
        },
        expected_behavior=(
            "when trigger fires, proof gains a delivered_anchor_consistency "
            "mechanical evidence row after the judge"
        ),
        target_polarity="boundary",
        cycle=cycle,
        failure_family="FAM-fp-recall_upgrade_conviction",
        failure_cases=["BFCL/033.json", "BFCL/113.json"],
    )
    withhold = make_intervention(
        intervention_id="IV-2-003",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        facts=[
            "positive_source=recall_upgrade",
            "defense_verdict=substantiated",
            "delivered_anchor_consistency=consistent",
        ],
        body={
            "action": "withhold_conviction",
            "action_not_when": (
                "Do not apply when the judge delivered result does not "
                "mechanically match the final answer anchor."
            ),
        },
        expected_behavior=(
            "when a recall-upgrade positive is substantiated but the judge "
            "delivered result mechanically matches the final answer anchor, "
            "the guard withholds the conviction"
        ),
        target_polarity="precision",
        cycle=cycle,
        failure_family="FAM-fp-recall_upgrade_conviction",
        failure_cases=["BFCL/033.json", "BFCL/113.json"],
    )
    return ProposalBatch(
        candidates=[restore_judge, anchor_evidence, withhold],
        joint_groups=[
            ["IV-2-001", "IV-2-002", "IV-2-003"],
        ],
        notes=[
            {
                "template": "rare_family_symmetric_restore_judge",
                "family": "FAM-fn-defense_refuted_judge",
                "intervention_id": "IV-2-001",
                "quality": 3,
            },
            {
                "template": "recall_upgrade_anchor_consistency_withhold",
                "family": "FAM-fp-recall_upgrade_conviction",
                "intervention_id": "IV-2-003",
                "quality": 2,
            },
        ],
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
    config["gates"]["m_min_marginal_cases"] = args.m_min_marginal
    if args.cycle == 2:
        # Cycle 2 is the rare-family preset: one symmetric recall guard and
        # one precision joint candidate, admitted only as a single unit.
        config["budget"]["max_admits"] = 3
    if args.no_audit:
        config["audit"]["enabled"] = False
    backend = _make_backend(config)
    split = _load_tuning_split(config)
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    preset = _cycle2_preset(args.cycle) if args.cycle == 2 else None
    report = engine.run_cycle(args.cycle, proposal_batch=preset)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    save_json(
        resolve_path(config["work_root"])
        / "experiments" / f"cycle{args.cycle:03d}_tuning100.json",
        report,
    )
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
    p.add_argument("--m-min-marginal", type=int, default=2)
    p.add_argument("--no-audit", action="store_true")
    p.set_defaults(func=cmd_cycle)
    p = sub.add_parser("validate")
    p.add_argument("--no-noise", action="store_true")
    p.set_defaults(func=cmd_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
