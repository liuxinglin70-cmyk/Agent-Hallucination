"""Phase 2 self-evolution cycle: propose, gate, promote, ledger.

One cycle consumes a batch of fresh evolution-pool detections:

    batch run tree + runtime bindings
        -> failure worksheet (this cycle's new wrongful convictions)
        -> policy proposals (provider, mechanically validated)
        -> candidate genome (new / amend / retire applied)
        -> replay gate over the CUMULATIVE evidence (every tree seen
           so far): the candidate must dominate the current genome —
           zero true-positive losses overall AND a strict gain (either
           fewer wrongful convictions, or true positives recovered that
           the current genome was already losing)
        -> promotion writes genome_v{N+1} and appends the cycle record

Runtime policy semantics are class-keyed (certificate class -> action);
the definition text documents the boundary for humans and for future
binder calibration, it does not execute. So an executable change is
arming, re-arming or disarming a class action, and the gate attributes
every regression to the proposal that armed the offending class — or,
when the CURRENT genome was already losing a true positive, treats the
retire of the offending policy as the remedy and drops it only when it
fails to cure the loss.

The policy layer can only withhold convictions, so gold-error misses
are recorded per cycle as non-actionable context (L1/evidence surface).

CLI:
    python -m sehd_rise2.evolution.cycle init --library <dir>
    python -m sehd_rise2.evolution.cycle run --library <dir> \
        --batch-run runs/<id> --batch-bindings <file> \
        [--prior-run runs/<id>] [--prior-bindings <file>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.evolution.policy import POLICY_GENOME_SCHEMA
from sehd_rise2.evolution.proposal import (
    build_worksheet,
    generate_proposals,
    load_genome_items,
)
from sehd_rise2.evolution.replay import (
    class_actions,
    load_bindings,
    load_run_tree,
    replay_case,
    surviving_ids,
)

LIBRARY_SCHEMA = "sehd2.policy_library.v1"
CYCLE_RESULT_SCHEMA = "sehd2.policy_cycle.v1"
PHASE1_GENOME = Path("datasets/policy_candidates/v1/policy_genome.json")
PHASE1_BINDINGS = Path(
    "datasets/policy_candidates/v1/runtime_bindings.json",
)
PHASE1_RUN = Path("runs/random100_20260815_142446")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_library(library_dir: Path) -> dict[str, Any]:
    library = _read_json(Path(library_dir) / "library.json")
    if library.get("schema_version") != LIBRARY_SCHEMA:
        raise ValueError(f"{library_dir}: unknown library schema")
    return library


def current_genome_path(library_dir: Path) -> Path:
    library = load_library(library_dir)
    return Path(library_dir) / library["genomes"][-1]["file"]


def merge_trees(run_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        part = load_run_tree(Path(run_dir))
        overlap = set(part) & set(merged)
        if overlap:
            raise ValueError(f"{run_dir}: trees overlap on {overlap}")
        merged.update(part)
    return merged


def merge_bindings(
    paths: list[Path],
) -> dict[tuple[str, str], dict[str, str]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for path in paths:
        bindings, _ = load_bindings(Path(path))
        overlap = set(bindings) & set(merged)
        if overlap:
            raise ValueError(f"{path}: bindings overlap on {overlap}")
        merged.update(bindings)
    return merged


def adjudicate(
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
    items: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-case replay audit under one genome's items ([] = baseline)."""
    actions = class_actions([
        {
            "trigger": {
                "certificate_class": item["target_certificate_class"],
            },
            "action": item["action"],
        }
        for item in items
    ])
    return {
        case_id: replay_case(
            [
                bindings[(case_id, cid)]["certificate_class"]
                for cid in surviving_ids(entry["result"])
            ],
            actions,
        )
        for case_id, entry in run_tree.items()
    }


def gate_metrics(
    run_tree: Mapping[str, Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Gold-joined outcome of one adjudication over the tree."""
    baseline = {
        case_id: entry["result"].get("state")
        for case_id, entry in run_tree.items()
    }
    tp = fp = fn = tn = 0
    tp_lost: list[str] = []
    fp_fixed: list[str] = []
    fired: dict[str, list[str]] = {}
    for case_id, entry in sorted(run_tree.items()):
        gold = entry["gold"]
        convicted = audit[case_id]["state"] == "verified_error"
        if gold and convicted:
            tp += 1
        elif not gold and convicted:
            fp += 1
        elif gold and not convicted:
            fn += 1
            if baseline[case_id] == "verified_error":
                tp_lost.append(case_id)
        else:
            tn += 1
        if not gold and baseline[case_id] == "verified_error" and (
            not convicted
        ):
            fp_fixed.append(case_id)
        for record in audit[case_id]["policy_evaluation"]["fired"]:
            fired.setdefault(
                record["certificate_class"], [],
            ).append(case_id)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / len(run_tree), 4),
        "tp_lost": sorted(tp_lost),
        "fp_fixed": sorted(fp_fixed),
        "fired_by_class": {
            key: sorted(value) for key, value in fired.items()
        },
    }


def apply_proposals(
    items: list[Mapping[str, Any]],
    proposals: list[Mapping[str, Any]],
    *,
    cycle: int,
    worksheet_path: str,
) -> list[dict[str, Any]]:
    """Apply proposals to the genome items, returning a new item list."""
    by_id = {
        str(item["policy_id"]): dict(item) for item in items
    }
    for proposal in proposals:
        policy_id = str(proposal["policy_id"])
        kind = str(proposal["kind"])
        if kind == "retire":
            del by_id[policy_id]
            continue
        trigger = proposal.get("trigger") or {}
        provenance = {
            "cycle": cycle,
            "worksheet": worksheet_path,
            "failure_cases": sorted({
                str(entry.get("case_id"))
                for entry in (
                    proposal.get("hypothesis") or {}
                ).get("case_evidence") or []
                if isinstance(entry, Mapping)
            }),
        }
        item = {
            "policy_id": policy_id,
            "version": (
                "v1" if kind == "new" else _bump(
                    by_id[policy_id].get("version", "v1"),
                )
            ),
            "target_certificate_class": str(
                proposal.get("target_certificate_class"),
            ),
            "precondition": str(trigger.get("definition") or ""),
            "exclusions": list(trigger.get("must_not_match") or []),
            "action": str(proposal.get("action")),
            "risk_boundary": {
                "statement": str(
                    (proposal.get("risk_boundary") or {}).get("statement")
                    or "",
                ),
                "do_not_apply": list(
                    (proposal.get("risk_boundary") or {}).get(
                        "do_not_apply",
                    ) or [],
                ),
            },
            "provenance": provenance,
        }
        if kind == "amend":
            item["provenance"] = {
                "amends": dict(by_id[policy_id].get("provenance") or {}),
                **provenance,
            }
        by_id[policy_id] = item
    return [by_id[key] for key in sorted(by_id)]


def _bump(version: str) -> str:
    return f"v{int(version[1:]) + 1}"


def genome_hash(payload: Mapping[str, Any]) -> str:
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    canonical = json.dumps(
        hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fired_classes(
    audit: Mapping[str, Mapping[str, Any]], case_id: str,
) -> set[str]:
    return {
        str(record["certificate_class"])
        for record in audit[case_id]["policy_evaluation"]["fired"]
    }


def _culprit(
    *,
    survivors: list[Mapping[str, Any]],
    items: list[Mapping[str, Any]],
    lost_case: str,
    candidate_audit: Mapping[str, Mapping[str, Any]],
    current_audit: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """The first survivor responsible for one true-positive loss.

    A new/amend proposal arming a class that fired on the lost case
    under the candidate is the culprit when the current genome was not
    already losing the case. When the current genome WAS already losing
    it, the intended remedy is the retire of the policy whose class
    fired — dropping that retire (or any other survivor that re-arms a
    firing class) is the only consistent move.
    """
    candidate_classes = fired_classes(candidate_audit, lost_case)
    current_loses = (
        current_audit[lost_case]["state"] != "verified_error"
    )
    class_of_policy = {
        str(item["policy_id"]): str(item["target_certificate_class"])
        for item in items
    }
    for proposal in survivors:
        policy_id = str(proposal["policy_id"])
        kind = str(proposal["kind"])
        if kind == "retire":
            if current_loses and class_of_policy.get(
                policy_id,
            ) in candidate_classes:
                return policy_id
            continue
        if str(
            proposal.get("target_certificate_class"),
        ) in candidate_classes and not current_loses:
            return policy_id
    return None


def _idle(
    survivors: list[Mapping[str, Any]],
    items: list[Mapping[str, Any]],
    current_audit: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> set[str]:
    """Survivors that earn nothing on the cumulative evidence.

    A new/amend proposal earns its slot by fixing at least one
    wrongful conviction the current genome does not fix; a retire
    earns its slot by recovering a true positive the current genome
    was losing (the recovered case fired the retired policy's class).
    """
    fixed_by_class: dict[str, set[str]] = {}
    for case_id in candidate["fp_fixed"]:
        if case_id in current["fp_fixed"]:
            continue
        for certificate_class, cases in candidate[
            "fired_by_class"
        ].items():
            if case_id in cases:
                fixed_by_class.setdefault(
                    certificate_class, set(),
                ).add(case_id)
    recovered = set(current["tp_lost"]) - set(candidate["tp_lost"])
    class_of_policy = {
        str(item["policy_id"]): str(item["target_certificate_class"])
        for item in items
    }
    idle: set[str] = set()
    for proposal in survivors:
        policy_id = str(proposal["policy_id"])
        if str(proposal["kind"]) == "retire":
            armed = class_of_policy.get(policy_id)
            earned = any(
                armed in fired_classes(current_audit, case_id)
                for case_id in recovered
            )
            if not earned:
                idle.add(policy_id)
            continue
        target = str(proposal.get("target_certificate_class"))
        if not fixed_by_class.get(target):
            idle.add(policy_id)
    return idle


def run_cycle(
    *,
    library_dir: Path,
    batch_run: Path,
    batch_bindings: Path,
    prior_runs: list[Path],
    prior_bindings: list[Path],
) -> dict[str, Any]:
    library = load_library(library_dir)
    cycle = len(library["cycles"]) + 1
    genome_path = current_genome_path(library_dir)
    items = load_genome_items(genome_path)

    run_dirs = [Path(PHASE1_RUN), *prior_runs, Path(batch_run)]
    binding_files = [
        Path(PHASE1_BINDINGS), *prior_bindings, Path(batch_bindings),
    ]
    tree = merge_trees(run_dirs)
    bindings = merge_bindings(binding_files)
    batch_tree = load_run_tree(Path(batch_run))
    batch_only, _ = load_bindings(Path(batch_bindings))

    worksheet = build_worksheet(
        run_tree=batch_tree,
        bindings=batch_only,
        genome_items=items,
        source=f"cycle {cycle} batch {batch_run}",
    )
    worksheet_path = (
        Path(library_dir) / "cycles" / f"c{cycle}_worksheet.json"
    )
    _write_json(worksheet_path, worksheet)

    accepted, meta = generate_proposals(
        worksheet=worksheet, genome_path=genome_path,
    )
    proposals_path = (
        Path(library_dir) / "cycles" / f"c{cycle}_proposals.json"
    )
    _write_json(proposals_path, {
        "schema_version": "sehd2.policy_proposals.v1",
        "cycle": cycle,
        "proposals": accepted,
        "generation_meta": meta,
    })

    baseline_metrics = gate_metrics(tree, adjudicate(tree, bindings, []))
    current_audit = adjudicate(tree, bindings, items)
    current_metrics = gate_metrics(tree, current_audit)

    survivors = list(accepted)
    dropped: list[dict[str, str]] = []
    context = {
        "library": library, "library_dir": Path(library_dir),
        "cycle": cycle, "meta": meta, "worksheet": worksheet,
        "baseline": baseline_metrics, "current": current_metrics,
        "batch_run": Path(batch_run),
        "batch_bindings": Path(batch_bindings),
        "prior_runs": prior_runs, "prior_bindings": prior_bindings,
    }
    while survivors:
        candidate_items = apply_proposals(
            items, survivors, cycle=cycle,
            worksheet_path=str(worksheet_path),
        )
        candidate_audit = adjudicate(tree, bindings, candidate_items)
        candidate_metrics = gate_metrics(tree, candidate_audit)
        # Repository promotion gate (README): accuracy >= baseline,
        # FP clearly reduced, FN increase <= 1, no must-survive
        # violations. Against the CURRENT genome: the candidate must
        # not lose a true positive the current genome keeps, must not
        # add more than one false negative the baseline did not have,
        # and must strictly improve FP or recover current TP losses.
        new_fn_vs_baseline = (
            candidate_metrics["fn"] - baseline_metrics["fn"]
        )
        dominates = (
            not candidate_metrics["tp_lost"]
            and new_fn_vs_baseline <= 1
            and (
                candidate_metrics["fp"] < current_metrics["fp"]
                or len(candidate_metrics["tp_lost"]) < len(
                    current_metrics["tp_lost"],
                )
            )
        )
        if not dominates:
            if candidate_metrics["tp_lost"]:
                lost = candidate_metrics["tp_lost"][0]
                culprit = _culprit(
                    survivors=survivors, items=items,
                    lost_case=lost,
                    candidate_audit=candidate_audit,
                    current_audit=current_audit,
                )
                reason = (
                    f"involved in the true-positive loss {lost}"
                    if culprit else (
                        f"unattributable true-positive loss {lost}"
                    )
                )
            else:
                culprit = None
                reason = "no strict improvement over the current genome"
            if culprit is None:
                dropped.append({
                    "policy_id": "<batch>", "reason": reason,
                })
                survivors = []
                break
            survivors = [
                proposal for proposal in survivors
                if str(proposal["policy_id"]) != culprit
            ]
            dropped.append({"policy_id": culprit, "reason": reason})
            continue
        idle = _idle(
            survivors, items, current_audit,
            current_metrics, candidate_metrics,
        )
        if idle:
            for policy_id in sorted(idle):
                dropped.append({
                    "policy_id": policy_id,
                    "reason": (
                        "no attributed fix on the cumulative evidence"
                    ),
                })
            survivors = [
                proposal for proposal in survivors
                if str(proposal["policy_id"]) not in idle
            ]
            continue
        return _promote(
            candidate_metrics=candidate_metrics,
            candidate_items=candidate_items,
            survivors=survivors,
            dropped=dropped,
            **context,
        )
    return _no_change(dropped=dropped, **context)


def _outcome_record(
    *, cycle: int, outcome: str, meta: Mapping[str, Any],
    worksheet: Mapping[str, Any], survivors: list[str],
    dropped: list[Mapping[str, str]], gate: dict[str, Any],
    batch_run: Path, batch_bindings: Path,
    prior_runs: list[Path], prior_bindings: list[Path],
) -> dict[str, Any]:
    return {
        "schema_version": CYCLE_RESULT_SCHEMA,
        "cycle": cycle,
        "outcome": outcome,
        "batch_run": str(batch_run),
        "batch_bindings": str(batch_bindings),
        "prior_runs": [str(path) for path in prior_runs],
        "prior_bindings": [str(path) for path in prior_bindings],
        "worksheet": {
            "wrongful_convictions": len(
                worksheet["wrongful_convictions"],
            ),
            "tp_sentinels": len(worksheet["tp_sentinels"]),
            "recorded_misses": len(worksheet["recorded_misses"]),
        },
        "generation_meta": {
            key: meta.get(key) for key in (
                "model", "raw_proposals", "accepted", "rejections",
            )
        },
        "survivors": survivors,
        "dropped": [dict(record) for record in dropped],
        "gate": gate,
    }


def _metric_view(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: metrics[key] for key in (
            "tp", "fp", "fn", "tn", "accuracy", "tp_lost", "fp_fixed",
        )
    }


def _promote(
    *, library: dict[str, Any], library_dir: Path, cycle: int,
    candidate_metrics: Mapping[str, Any],
    candidate_items: list[Mapping[str, Any]],
    survivors: list[Mapping[str, Any]],
    dropped: list[dict[str, str]], meta: Mapping[str, Any],
    worksheet: Mapping[str, Any], baseline: Mapping[str, Any],
    current: Mapping[str, Any], batch_run: Path,
    batch_bindings: Path, prior_runs: list[Path],
    prior_bindings: list[Path],
) -> dict[str, Any]:
    genome = {
        "schema_version": POLICY_GENOME_SCHEMA,
        "items": [dict(item) for item in candidate_items],
    }
    genome["content_hash"] = genome_hash(genome)
    version = len(library["genomes"]) + 1
    genome_file = f"genomes/genome_v{version}.json"
    _write_json(library_dir / genome_file, genome)

    result = _outcome_record(
        cycle=cycle, outcome=f"promoted {genome_file}", meta=meta,
        worksheet=worksheet,
        survivors=[str(proposal["policy_id"]) for proposal in survivors],
        dropped=dropped,
        gate={
            "baseline": _metric_view(baseline),
            "current": _metric_view(current),
            "candidate": {
                **_metric_view(candidate_metrics),
                "fired_by_class": candidate_metrics["fired_by_class"],
            },
        },
        batch_run=batch_run, batch_bindings=batch_bindings,
        prior_runs=prior_runs, prior_bindings=prior_bindings,
    )
    result["genome"] = {
        "file": genome_file,
        "content_hash": genome["content_hash"],
        "policy_ids": [item["policy_id"] for item in candidate_items],
    }
    _write_json(
        library_dir / "cycles" / f"c{cycle}_result.json", result,
    )

    active = {str(item["policy_id"]) for item in candidate_items}
    for policy_id, facts in library["policies"].items():
        facts["status"] = (
            "active" if policy_id in active else "retired"
        )
    for proposal in survivors:
        policy_id = str(proposal["policy_id"])
        kind = str(proposal["kind"])
        if kind == "new":
            library["policies"][policy_id] = {
                "status": "active", "born_cycle": cycle,
                "events": [{"cycle": cycle, "event": "born"}],
            }
        else:
            library["policies"][policy_id]["events"].append({
                "cycle": cycle,
                "event": "amended" if kind == "amend" else "retired",
            })
    library["cycles"].append({
        "cycle": cycle, "outcome": result["outcome"],
        "batch_run": str(batch_run),
        "batch_bindings": str(batch_bindings),
        "prior_runs": [str(path) for path in prior_runs],
        "prior_bindings": [str(path) for path in prior_bindings],
        "survivors": result["survivors"], "dropped": dropped,
        "genome": result["genome"],
    })
    library["genomes"].append({
        "file": genome_file,
        "content_hash": genome["content_hash"],
        "cycle": cycle,
        "origin": f"cycle {cycle} promotion",
    })
    _write_json(library_dir / "library.json", library)
    return result


def _no_change(
    *, library: dict[str, Any], library_dir: Path, cycle: int,
    dropped: list[dict[str, str]], meta: Mapping[str, Any],
    worksheet: Mapping[str, Any], baseline: Mapping[str, Any],
    current: Mapping[str, Any], batch_run: Path,
    batch_bindings: Path, prior_runs: list[Path],
    prior_bindings: list[Path],
) -> dict[str, Any]:
    result = _outcome_record(
        cycle=cycle, outcome="no_change", meta=meta, worksheet=worksheet,
        survivors=[], dropped=dropped,
        gate={
            "baseline": _metric_view(baseline),
            "current": _metric_view(current),
        },
        batch_run=batch_run, batch_bindings=batch_bindings,
        prior_runs=prior_runs, prior_bindings=prior_bindings,
    )
    _write_json(
        library_dir / "cycles" / f"c{cycle}_result.json", result,
    )
    library["cycles"].append({
        "cycle": cycle, "outcome": "no_change",
        "batch_run": str(batch_run),
        "batch_bindings": str(batch_bindings),
        "prior_runs": [str(path) for path in prior_runs],
        "prior_bindings": [str(path) for path in prior_bindings],
        "survivors": [], "dropped": dropped,
        "genome": library["genomes"][-1],
    })
    _write_json(library_dir / "library.json", library)
    return result


def init_library(library_dir: Path) -> dict[str, Any]:
    """Seed the library from the frozen Phase 1 genome."""
    genome = _read_json(PHASE1_GENOME)
    library_dir = Path(library_dir)
    _write_json(library_dir / "genomes" / "genome_v1.json", genome)
    library = {
        "schema_version": LIBRARY_SCHEMA,
        "genomes": [{
            "file": "genomes/genome_v1.json",
            "content_hash": genome["content_hash"],
            "cycle": 0,
            "origin": "phase 1-C.5 frozen genome (P2-P5)",
        }],
        "policies": {
            str(item["policy_id"]): {
                "status": "active",
                "born_cycle": 0,
                "events": [{
                    "cycle": 0, "event": "seeded",
                    "origin": "phase 1-C.5 frozen genome",
                }],
            }
            for item in genome["items"]
        },
        "prior_evidence": {
            "runs": [str(PHASE1_RUN)],
            "bindings": [str(PHASE1_BINDINGS)],
        },
        "cycles": [],
    }
    _write_json(library_dir / "library.json", library)
    return library


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser("init", help="seed from the frozen genome")
    init_parser.add_argument("--library", type=Path, required=True)
    run_parser = sub.add_parser("run", help="execute one evolution cycle")
    run_parser.add_argument("--library", type=Path, required=True)
    run_parser.add_argument("--batch-run", type=Path, required=True)
    run_parser.add_argument("--batch-bindings", type=Path, required=True)
    run_parser.add_argument(
        "--prior-run", type=Path, action="append", default=[],
    )
    run_parser.add_argument(
        "--prior-bindings", type=Path, action="append", default=[],
    )
    args = parser.parse_args()
    if args.command == "init":
        library = init_library(args.library)
        print(
            f"library seeded -> {args.library / 'library.json'} "
            f"({len(library['policies'])} policies)"
        )
        return 0
    result = run_cycle(
        library_dir=args.library,
        batch_run=args.batch_run,
        batch_bindings=args.batch_bindings,
        prior_runs=args.prior_run,
        prior_bindings=args.prior_bindings,
    )
    print(
        f"cycle {result['cycle']}: {result['outcome']} | "
        f"survivors={result['survivors']} "
        f"dropped={len(result['dropped'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
