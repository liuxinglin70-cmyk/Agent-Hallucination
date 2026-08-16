"""Phase 2 self-evolution cycle: propose, gate, promote, ledger.

One cycle consumes a batch of fresh evolution-pool detections:

    batch run tree + runtime bindings
        -> failure worksheet (this cycle's new wrongful convictions)
        -> policy proposals (provider, mechanically validated)
        -> candidate artifact registry (new / amend / retire applied)
        -> replay gate over the CUMULATIVE evidence (every tree seen
           so far): the candidate must dominate the current registry —
           zero true-positive losses overall AND a strict gain (either
           fewer wrongful convictions, or true positives recovered that
           the current registry was already losing)
        -> promotion writes artifacts_v{N+1} and appends the cycle record

Runtime policy semantics are class-keyed (certificate class -> action)
and shared with the detector court (``detector.artifacts``); the gate
attributes every regression to the proposal that armed the offending
class — or, when the CURRENT registry was already losing a true positive,
treats the retire of the offending policy as the remedy and drops it only
when it fails to cure the loss.

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
import json
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.artifacts import (
    ARTIFACT_REGISTRY_SCHEMA,
    POLICY_ACTIONS,
    load_artifact_registry,
    policy_actions_of,
    registry_hash,
    validate_registry,
)
from sehd_rise2.evolution.gates import load_calibration
from sehd_rise2.evolution.proposal import (
    build_worksheet,
    generate_proposals,
)
from sehd_rise2.evolution.replay import (
    baseline_matches_records,
    load_bindings,
    load_run_tree,
    replay_case,
    surviving_ids,
)

LIBRARY_SCHEMA = "sehd2.policy_library.v1"
CYCLE_RESULT_SCHEMA = "sehd2.policy_cycle.v1"
PHASE1_REGISTRY = Path(
    "datasets/policy_candidates/v1/policy_artifacts.json",
)
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


def load_registry_artifacts(path: Path) -> list[dict[str, Any]]:
    """Load one persisted artifact registry; returns its artifact list."""
    return load_artifact_registry(Path(path))["artifacts"]


def load_library(library_dir: Path) -> dict[str, Any]:
    library = _read_json(Path(library_dir) / "library.json")
    if library.get("schema_version") != LIBRARY_SCHEMA:
        raise ValueError(f"{library_dir}: unknown library schema")
    return library


def current_registry_path(library_dir: Path) -> Path:
    library = load_library(library_dir)
    return Path(library_dir) / library["registries"][-1]["file"]


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
    artifacts: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-case replay audit under one registry's artifacts ([] = baseline)."""
    actions = policy_actions_of(artifacts)
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
    artifacts: list[Mapping[str, Any]],
    proposals: list[Mapping[str, Any]],
    *,
    cycle: int,
    worksheet_path: str,
) -> list[dict[str, Any]]:
    """Apply proposals to the registry, returning a new artifact list."""
    by_id = {
        str(artifact["artifact_id"]): dict(artifact)
        for artifact in artifacts
    }
    for proposal in proposals:
        artifact_id = str(proposal["policy_id"])
        kind = str(proposal["kind"])
        if kind == "retire":
            del by_id[artifact_id]
            continue
        trigger = proposal.get("trigger") or {}
        hypothesis = proposal.get("hypothesis") or {}
        boundary = proposal.get("risk_boundary") or {}
        source_names = [
            str(name) for name in hypothesis.get("source_gap_names") or []
        ]
        if not source_names:
            raise ValueError(
                f"proposal {artifact_id}: no source gap name — the gap "
                "block cannot be derived"
            )
        certificate_class = str(proposal.get("target_certificate_class"))
        action = str(proposal.get("action"))
        provenance = {
            "cycle": cycle,
            "worksheet": worksheet_path,
            "failure_cases": sorted({
                str(entry.get("case_id"))
                for entry in hypothesis.get("case_evidence") or []
                if isinstance(entry, Mapping)
            }),
        }
        artifact = {
            "artifact_id": artifact_id,
            "version": (
                "v1" if kind == "new" else _bump(
                    by_id[artifact_id].get("version", "v1"),
                )
            ),
            "kind": "adjudication_policy",
            "execution_point": "adjudication",
            "gap": {
                "category": "policy",
                "name": source_names[0],
                "gap_type": "precision",
                "failure_signature": str(
                    hypothesis.get("statement") or ""
                ),
                "affected_stage": "adjudication",
                "expected_capability": POLICY_ACTIONS[action],
            },
            "trigger": {"certificate_class": certificate_class},
            "intervention": {
                "action": action,
                "exclusions": list(trigger.get("must_not_match") or []),
            },
            "capability_delta": {
                "before_capability": (
                    f"a surviving {certificate_class} certificate convicts "
                    "like any other"
                ),
                "after_capability": (
                    f"a surviving {certificate_class} certificate "
                    f"{POLICY_ACTIONS[action]}"
                ),
            },
            "risk_boundary": {
                "statement": str(boundary.get("statement") or ""),
                "do_not_apply": list(
                    boundary.get("do_not_apply") or []
                ),
            },
            "provenance": provenance,
        }
        if kind == "amend":
            artifact["provenance"] = {
                "amends": dict(by_id[artifact_id].get("provenance") or {}),
                **provenance,
            }
        by_id[artifact_id] = artifact
    return [by_id[key] for key in sorted(by_id)]


def _bump(version: str) -> str:
    return f"v{int(version[1:]) + 1}"


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
    artifacts: list[Mapping[str, Any]],
    lost_case: str,
    candidate_audit: Mapping[str, Mapping[str, Any]],
    current_audit: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """The first survivor responsible for one true-positive loss.

    A new/amend proposal arming a class that fired on the lost case
    under the candidate is the culprit when the current registry was not
    already losing the case. When the current registry WAS already losing
    it, the intended remedy is the retire of the policy whose class
    fired — dropping that retire (or any other survivor that re-arms a
    firing class) is the only consistent move.
    """
    candidate_classes = fired_classes(candidate_audit, lost_case)
    current_loses = (
        current_audit[lost_case]["state"] != "verified_error"
    )
    class_of_artifact = {
        str(artifact["artifact_id"]): str(
            artifact["trigger"]["certificate_class"]
        )
        for artifact in artifacts
    }
    for proposal in survivors:
        artifact_id = str(proposal["policy_id"])
        kind = str(proposal["kind"])
        if kind == "retire":
            if current_loses and class_of_artifact.get(
                artifact_id,
            ) in candidate_classes:
                return artifact_id
            continue
        if str(
            proposal.get("target_certificate_class"),
        ) in candidate_classes and not current_loses:
            return artifact_id
    return None


def _idle(
    survivors: list[Mapping[str, Any]],
    artifacts: list[Mapping[str, Any]],
    current_audit: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> set[str]:
    """Survivors that earn nothing on the cumulative evidence.

    A new/amend proposal earns its slot by fixing at least one
    wrongful conviction the current registry does not fix; a retire
    earns its slot by recovering a true positive the current registry
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
    class_of_artifact = {
        str(artifact["artifact_id"]): str(
            artifact["trigger"]["certificate_class"]
        )
        for artifact in artifacts
    }
    idle: set[str] = set()
    for proposal in survivors:
        artifact_id = str(proposal["policy_id"])
        if str(proposal["kind"]) == "retire":
            armed = class_of_artifact.get(artifact_id)
            earned = any(
                armed in fired_classes(current_audit, case_id)
                for case_id in recovered
            )
            if not earned:
                idle.add(artifact_id)
            continue
        target = str(proposal.get("target_certificate_class"))
        if not fixed_by_class.get(target):
            idle.add(artifact_id)
    return idle


def run_cycle(
    *,
    library_dir: Path,
    batch_run: Path,
    batch_bindings: Path,
    prior_runs: list[Path],
    prior_bindings: list[Path],
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    calibration = dict(
        calibration if calibration is not None else load_calibration()
    )
    gate_limits = dict(calibration["policy_cycle"])
    library = load_library(library_dir)
    cycle = len(library["cycles"]) + 1
    registry_path = current_registry_path(library_dir)
    artifacts = load_registry_artifacts(registry_path)

    run_dirs = [Path(PHASE1_RUN), *prior_runs, Path(batch_run)]
    binding_files = [
        Path(PHASE1_BINDINGS), *prior_bindings, Path(batch_bindings),
    ]
    tree = merge_trees(run_dirs)
    bindings = merge_bindings(binding_files)
    # Fail closed before any provider call: an incompletely bound tree
    # silently degrades the worksheet's class evidence and would only
    # crash at replay time.
    unbound = sorted(
        f"{case_id}/{candidate_id}"
        for case_id, entry in tree.items()
        for candidate_id in surviving_ids(entry["result"])
        if (case_id, candidate_id) not in bindings
    )
    if unbound:
        raise ValueError(
            f"bindings do not cover {len(unbound)} surviving "
            f"certificates (first: {', '.join(unbound[:5])})"
        )
    if not baseline_matches_records(tree):
        raise ValueError(
            "recorded states do not reproduce under the no-policy "
            "replay — the tree cannot ground a cycle"
        )
    batch_tree = load_run_tree(Path(batch_run))
    batch_only, _ = load_bindings(Path(batch_bindings))

    worksheet = build_worksheet(
        run_tree=batch_tree,
        bindings=batch_only,
        genome_artifacts=artifacts,
        source=f"cycle {cycle} batch {batch_run}",
    )
    worksheet_path = (
        Path(library_dir) / "cycles" / f"c{cycle}_worksheet.json"
    )
    _write_json(worksheet_path, worksheet)

    accepted, meta = generate_proposals(
        worksheet=worksheet, registry_path=registry_path,
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
    current_audit = adjudicate(tree, bindings, artifacts)
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
        candidate_artifacts = apply_proposals(
            artifacts, survivors, cycle=cycle,
            worksheet_path=str(worksheet_path),
        )
        candidate_audit = adjudicate(tree, bindings, candidate_artifacts)
        candidate_metrics = gate_metrics(tree, candidate_audit)
        # Repository promotion gate (README): accuracy >= baseline,
        # FP clearly reduced, FN increase within the calibrated
        # tolerance, no must-survive violations. Against the CURRENT
        # registry: the candidate must not lose a true positive the
        # current registry keeps beyond the calibrated tolerance, must
        # not add more false negatives than the baseline tolerance, and
        # must strictly improve FP or recover current TP losses. The
        # tolerances come from the gate calibration (noise-floor
        # referenced, never from the frozen verification split).
        new_fn_vs_baseline = (
            candidate_metrics["fn"] - baseline_metrics["fn"]
        )
        dominates = (
            len(candidate_metrics["tp_lost"])
            <= gate_limits["max_tp_lost"]
            and new_fn_vs_baseline
            <= gate_limits["max_added_fn_vs_baseline"]
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
                    survivors=survivors, artifacts=artifacts,
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
                reason = "no strict improvement over the current registry"
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
            survivors, artifacts, current_audit,
            current_metrics, candidate_metrics,
        )
        if idle:
            for policy_id in sorted(idle):
                dropped.append({
                    "policy_id": policy_id, "reason": (
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
            candidate_artifacts=candidate_artifacts,
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
    candidate_artifacts: list[Mapping[str, Any]],
    survivors: list[Mapping[str, Any]],
    dropped: list[dict[str, str]], meta: Mapping[str, Any],
    worksheet: Mapping[str, Any], baseline: Mapping[str, Any],
    current: Mapping[str, Any], batch_run: Path,
    batch_bindings: Path, prior_runs: list[Path],
    prior_bindings: list[Path],
) -> dict[str, Any]:
    registry = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": [dict(artifact) for artifact in candidate_artifacts],
    }
    registry["content_hash"] = registry_hash(registry)
    version = len(library["registries"]) + 1
    registry_file = f"artifacts/registry_v{version}.json"
    _write_json(library_dir / registry_file, registry)

    result = _outcome_record(
        cycle=cycle, outcome=f"promoted {registry_file}", meta=meta,
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
    result["registry"] = {
        "file": registry_file,
        "content_hash": registry["content_hash"],
        "artifact_ids": [
            artifact["artifact_id"] for artifact in candidate_artifacts
        ],
    }
    _write_json(
        library_dir / "cycles" / f"c{cycle}_result.json", result,
    )

    active = {
        str(artifact["artifact_id"]) for artifact in candidate_artifacts
    }
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
        "registry": result["registry"],
    })
    library["registries"].append({
        "file": registry_file,
        "content_hash": registry["content_hash"],
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
        "registry": library["registries"][-1],
    })
    _write_json(library_dir / "library.json", library)
    return result


def init_library(library_dir: Path) -> dict[str, Any]:
    """Seed the library from the frozen Phase 1 artifact registry."""
    registry = _read_json(PHASE1_REGISTRY)
    validate_registry(registry)
    library_dir = Path(library_dir)
    _write_json(library_dir / "artifacts" / "registry_v1.json", registry)
    library = {
        "schema_version": LIBRARY_SCHEMA,
        "registries": [{
            "file": "artifacts/registry_v1.json",
            "content_hash": registry["content_hash"],
            "cycle": 0,
            "origin": "phase 1-C.5 frozen registry (P2-P5)",
        }],
        "policies": {
            str(artifact["artifact_id"]): {
                "status": "active",
                "born_cycle": 0,
                "events": [{
                    "cycle": 0, "event": "seeded",
                    "origin": "phase 1-C.5 frozen registry",
                }],
            }
            for artifact in registry["artifacts"]
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
    init_parser = sub.add_parser("init", help="seed from the frozen registry")
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
