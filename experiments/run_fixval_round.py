"""Fix-val round orchestrator: baseline -> evolve -> deploy -> report.

One round over a fresh resampled split (100 = 60 evolution + 40
validation), driven by four subcommands:

    baseline   run the repaired detector WITHOUT evolved conventions
               over all 100 cases (the round's pre-evolution anchor)
    evolve     miss triage + wrongful-conviction worksheet ->
               dual-boundary convention extraction -> G1 gate ->
               G2 per-convention targeted replay (evolution set only)
    deploy     run the detector WITH the deployable convention genome
               100 cases (the post-evolution arm)
    report     paired comparison on the validation set (read once per
               round) plus the evolution-set checkpoint decomposition

Usage:
    python -m experiments.run_fixval_round baseline --round 1
    python -m experiments.run_fixval_round evolve --round 1
    python -m experiments.run_fixval_round deploy --round 1
    python -m experiments.run_fixval_round report --round 1
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS
from sehd_rise2.detector.result import prediction_state
from sehd_rise2.detector.runs import (
    default_runs_root,
    new_run_id,
    record_case_run,
    summarize_run,
    write_run_meta,
)
from sehd_rise2.evolution.convention_evolution import (
    build_convention_genome,
    extract_conventions,
    gate_conventions,
    load_convention_genome,
    load_run_tree,
    triage_misses,
    validate_conventions,
)

DATASET_ROOT = Path("datasets/raw/agenthallu/AgentHallu")
WORKERS = 20


def split_dir(round_no: int) -> Path:
    return Path(f"datasets/eval_splits/fixval_round{round_no}_v1")


def _case_list(round_no: int, which: str) -> list[str]:
    name = "evolution_cases" if which == "evolution" else "validation_cases"
    return json.loads(
        (split_dir(round_no) / f"{name}.json").read_text(encoding="utf-8"),
    )


def _all_cases(round_no: int) -> list[str]:
    return sorted(
        set(_case_list(round_no, "evolution"))
        | set(_case_list(round_no, "validation")),
    )


def _load_gold(case_path: str) -> tuple[dict, bool | None]:
    raw = json.loads(
        (DATASET_ROOT / case_path).read_text(encoding="utf-8"),
    )
    value = raw.get("is_hallucination")
    if isinstance(value, bool):
        gold = value
    else:
        gold = {"true": True, "false": False}.get(
            str(value).strip().lower(),
        )
    clean = {
        key: item for key, item in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }
    return clean, gold


def gold_join(run_id: str, cases: list[str]) -> dict:
    rows = []
    for case in sorted(cases):
        result = json.loads(
            (
                default_runs_root() / run_id / "cases"
                / case.replace("/", "__") / "result.json"
            ).read_text(encoding="utf-8"),
        )
        _clean, gold = _load_gold(case)
        rows.append({
            "case": case,
            "gold": gold,
            "state": result.get("state"),
            "prediction": prediction_state(result),
            "runtime_valid": result.get("runtime_valid"),
        })
    decisive = [row for row in rows if row["prediction"] is not None]
    correct = sum(
        1 for row in decisive if row["prediction"] == row["gold"]
    )
    return {
        "schema_version": "sehd2.eval_gold.v1",
        "run_id": run_id,
        "cases": rows,
        "decisive_cases": len(decisive),
        "unresolved_cases": len(rows) - len(decisive),
        "accuracy": (
            round(correct / len(decisive), 4) if decisive else None
        ),
        "tp": sum(
            1 for r in decisive if r["gold"] and r["prediction"] is True
        ),
        "fp": sum(
            1 for r in decisive if not r["gold"] and r["prediction"] is True
        ),
        "fn": sum(
            1 for r in decisive if r["gold"] and r["prediction"] is False
        ),
        "tn": sum(
            1 for r in decisive if not r["gold"] and r["prediction"] is False
        ),
    }


def _run_cases(
    run_id: str, cases: list[str],
    evolved_conventions: dict[str, Any] | None = None,
) -> None:
    detector = Detector(load_config())
    run_dir = default_runs_root() / run_id / "cases"

    def run(case: str) -> str:
        if (run_dir / case.replace("/", "__") / "result.json").exists():
            return case
        clean, _gold = _load_gold(case)
        started = time.monotonic()
        result = detector.detect(
            clean, case_id=case,
            evolved_conventions=evolved_conventions,
        )
        for _attempt in range(3):
            if result.get("state") != "unresolved":
                break
            transport = any(
                str(reason.get("error_type")) in {
                    "provider_error", "transport_error", "invalid_json",
                }
                for reason in result.get("authority_failure_reasons") or []
            )
            if not transport:
                break
            result = detector.detect(
                clean, case_id=case,
                evolved_conventions=evolved_conventions,
            )
        record_case_run(
            runs_root=default_runs_root(), run_id=run_id, case_id=case,
            input_record=clean, result=result,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        print(
            f"  [case] {case:<32} state={result.get('state'):<15} "
            f"evolved={result.get('proof_adjudication', {}).get('active_evolved_conventions')}",
            flush=True,
        )
        return case

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(run, case) for case in cases]
        for future in as_completed(futures):
            future.result()


def _existing_run(prefix: str) -> str:
    matches = sorted(
        path.name for path in default_runs_root().glob(f"{prefix}_*")
        if (path / "eval_gold.json").exists()
    )
    if not matches:
        raise SystemExit(f"no completed run found for prefix {prefix!r}")
    return matches[-1]


def _finalize_run(run_id: str, cases: list[str]) -> dict:
    write_run_meta(
        runs_root=default_runs_root(), run_id=run_id,
        config=load_config(), case_ids=cases,
    )
    summarize_run(default_runs_root(), run_id)
    report = gold_join(run_id, cases)
    (default_runs_root() / run_id / "eval_gold.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    condensed = {k: v for k, v in report.items() if k != "cases"}
    print(json.dumps(condensed, ensure_ascii=False, indent=1), flush=True)
    return report


def cmd_baseline(round_no: int, workers: int) -> None:
    global WORKERS
    WORKERS = workers
    cases = _all_cases(round_no)
    run_id = new_run_id(f"fixval_r{round_no}_baseline")
    print(f"baseline run {run_id}: {len(cases)} cases", flush=True)
    _run_cases(run_id, cases)
    _finalize_run(run_id, cases)


def cmd_evolve(round_no: int) -> None:
    baseline_id = _existing_run(f"fixval_r{round_no}_baseline")
    baseline_dir = default_runs_root() / baseline_id
    evolution_cases = set(_case_list(round_no, "evolution"))
    full_tree = load_run_tree(baseline_dir)
    tree = {
        case_id: entry
        for case_id, entry in full_tree.items()
        if case_id in evolution_cases
    }
    misses = triage_misses(tree)
    by_attr: dict[str, int] = {}
    for row in misses:
        by_attr[row["attribution"]] = by_attr.get(
            row["attribution"], 0,
        ) + 1
    print(
        f"evolution-set misses: {len(misses)} by attribution {by_attr}",
        flush=True,
    )
    raw, meta = extract_conventions(
        run_tree=tree, dataset_root=DATASET_ROOT,
    )
    accepted, rejections = gate_conventions(
        raw, run_tree=tree,
    )
    print(
        f"extraction: {meta['raw_conventions']} raw -> "
        f"{len(accepted)} gated in, {len(rejections)} rejected",
        flush=True,
    )
    out_dir = split_dir(round_no)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "conventions_raw.json").write_text(
        json.dumps(
            {"conventions": raw, "meta": meta},
            ensure_ascii=False, indent=2,
        ) + "\n", encoding="utf-8",
    )
    (out_dir / "conventions_gated.json").write_text(
        json.dumps(
            {"conventions": accepted, "rejections": rejections},
            ensure_ascii=False, indent=2,
        ) + "\n", encoding="utf-8",
    )
    if not accepted:
        genome = build_convention_genome([])
        (out_dir / "convention_genome.json").write_text(
            json.dumps(genome, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("no conventions passed G1; empty genome written", flush=True)
        return
    report = validate_conventions(
        run_tree=tree, conventions=accepted, dataset_root=DATASET_ROOT,
        out_dir=out_dir, workers=WORKERS,
    )
    deployable = report["deployable"]
    genome = build_convention_genome([
        entry for entry in accepted
        if str(entry["convention_id"]) in deployable
    ])
    (out_dir / "convention_genome.json").write_text(
        json.dumps(genome, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"deployable conventions: {deployable} -> "
        f"{out_dir / 'convention_genome.json'}", flush=True,
    )


def cmd_deploy(round_no: int, workers: int) -> None:
    global WORKERS
    WORKERS = workers
    out_dir = split_dir(round_no)
    genome_path = out_dir / "convention_genome.json"
    evolved = (
        load_convention_genome(genome_path)
        if genome_path.exists() else None
    )
    if evolved is None:
        raise SystemExit(f"no convention genome at {genome_path}")
    cases = _all_cases(round_no)
    run_id = new_run_id(f"fixval_r{round_no}_deployed")
    print(
        f"deployed run {run_id}: {len(cases)} cases, "
        f"{len(evolved.get('conventions') or [])} evolved conventions",
        flush=True,
    )
    _run_cases(run_id, cases, evolved_conventions=evolved)
    _finalize_run(run_id, cases)


def _paired_report(
    baseline: dict, deployed: dict, which: str,
) -> dict[str, Any]:
    base_rows = {row["case"]: row for row in baseline["cases"]}
    dep_rows = {row["case"]: row for row in deployed["cases"]}
    shared = sorted(set(base_rows) & set(dep_rows))
    flips = []
    fn_fixed = fp_fixed = tp_lost = fp_added = fn_added = 0
    for case in shared:
        before = base_rows[case]
        after = dep_rows[case]
        before_pred = before["prediction"]
        after_pred = after["prediction"]
        if before_pred == after_pred:
            continue
        entry = {
            "case": case,
            "gold": before["gold"],
            "before": before_pred,
            "after": after_pred,
        }
        flips.append(entry)
        if before["gold"] and before_pred is False and after_pred is True:
            fn_fixed += 1
        elif not before["gold"] and before_pred is True and after_pred is False:
            fp_fixed += 1
        elif before["gold"] and before_pred is True and after_pred is False:
            tp_lost += 1
        elif not before["gold"] and before_pred is False and after_pred is True:
            fp_added += 1
        else:
            fn_added += 1
    return {
        "which": which,
        "cases": len(shared),
        "baseline": {
            k: baseline[k] for k in ("accuracy", "tp", "fp", "fn", "tn")
        },
        "deployed": {
            k: deployed[k] for k in ("accuracy", "tp", "fp", "fn", "tn")
        },
        "flips": {
            "fn_fixed": fn_fixed,
            "fp_fixed": fp_fixed,
            "tp_lost": tp_lost,
            "fp_added": fp_added,
            "other": fn_added,
            "net_improvement": fn_fixed + fp_fixed - tp_lost - fp_added,
        },
        "flip_table": flips,
    }


def cmd_report(round_no: int) -> None:
    baseline_id = _existing_run(f"fixval_r{round_no}_baseline")
    deployed_id = _existing_run(f"fixval_r{round_no}_deployed")
    baseline = json.loads(
        (default_runs_root() / baseline_id / "eval_gold.json").read_text(
            encoding="utf-8",
        ),
    )
    deployed = json.loads(
        (default_runs_root() / deployed_id / "eval_gold.json").read_text(
            encoding="utf-8",
        ),
    )
    evolution_cases = set(_case_list(round_no, "evolution"))
    validation_cases = set(_case_list(round_no, "validation"))

    def subset(report: dict, cases: set[str]) -> dict:
        rows = [r for r in report["cases"] if r["case"] in cases]
        decisive = [r for r in rows if r["prediction"] is not None]
        correct = sum(
            1 for r in decisive if r["prediction"] == r["gold"]
        )
        return {
            "cases": rows,
            "accuracy": (
                round(correct / len(decisive), 4) if decisive else None
            ),
            "tp": sum(1 for r in decisive if r["gold"] and r["prediction"]),
            "fp": sum(
                1 for r in decisive
                if not r["gold"] and r["prediction"] is True
            ),
            "fn": sum(
                1 for r in decisive
                if r["gold"] and r["prediction"] is False
            ),
            "tn": sum(
                1 for r in decisive
                if not r["gold"] and r["prediction"] is False
            ),
        }

    report = {
        "schema_version": "sehd2.fixval_round_report.v1",
        "round": round_no,
        "baseline_run": baseline_id,
        "deployed_run": deployed_id,
        "validation": _paired_report(
            subset(baseline, validation_cases),
            subset(deployed, validation_cases),
            "validation (read once)",
        ),
        "evolution_checkpoint": _paired_report(
            subset(baseline, evolution_cases),
            subset(deployed, evolution_cases),
            "evolution (decomposition)",
        ),
    }
    out = split_dir(round_no) / "round_report.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for section in ("validation", "evolution_checkpoint"):
        view = report[section]
        print(
            f"\n== {view['which']} ==\n"
            f"baseline {view['baseline']} \n"
            f"deployed {view['deployed']}\n"
            f"flips    {view['flips']}",
        )
    print(f"\nreport -> {out}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["baseline", "evolve", "deploy", "report"],
    )
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    if args.command == "baseline":
        cmd_baseline(args.round, args.workers)
    elif args.command == "evolve":
        cmd_evolve(args.round)
    elif args.command == "deploy":
        cmd_deploy(args.round, args.workers)
    else:
        cmd_report(args.round)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
