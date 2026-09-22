"""Read-only analysis of the framework validation run.

Recomputes every number the report quotes from the run's own artifacts
(ledger, cycle reports, gate verdicts, arm rows). It never writes to the
registry and never calls a provider, so the report's readings can be
re-derived by anyone.

Usage:
    python output/selfevo-run/analyze_run.py --work-root artifacts/selfevo-bigrun \
        [--json out.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

REPO = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, str(REPO))


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None


def gate_table(gates) -> dict:
    """One line per gate: verdict, evaluability and the reason it stopped."""
    if not isinstance(gates, dict):
        return {}
    table = {}
    for name, verdict in gates.items():
        if not isinstance(verdict, dict):
            continue
        table[name] = {
            "passed": verdict.get("passed"),
            "evaluable": verdict.get("evaluable"),
            "status": verdict.get("status"),
            "reason": str(verdict.get("reason") or "")[:200],
        }
    return table


def analyze(work_root: Path) -> dict:
    ledger = load_jsonl(work_root / "ledger.jsonl")
    events = collections.Counter(str(e.get("event") or "") for e in ledger)
    cycles_dir = work_root / "cycles"
    cycle_files = sorted(cycles_dir.glob("cycle*.json")) if cycles_dir.is_dir() else []

    cycles = []
    for path in cycle_files:
        report = load_json(path)
        if not isinstance(report, dict):
            continue
        patrol = report.get("patrol") or {}
        metrics = patrol.get("metrics") or {}
        instrument = report.get("instrument") or {}
        proposals = report.get("proposals") or []
        gates = report.get("gates") or {}
        cycles.append({
            "cycle": report.get("cycle"),
            "state": report.get("state"),
            "patrol_size": patrol.get("patrol_size"),
            "confusion": {
                key: metrics.get(key)
                for key in ("tp", "fp", "fn", "tn", "unresolved")
            },
            "detection_families": len(patrol.get("detection_families") or []),
            "localization_families": len(patrol.get("localization_families") or []),
            "capability_gaps": len(report.get("capability_backlog") or []),
            "gain_opportunity": instrument.get("gain_opportunity"),
            "unmeasurable_polarities": instrument.get("unmeasurable_polarities"),
            "proposals": len(proposals),
            "proposal_ids": [
                str(p.get("intervention_id") or p.get("candidate_id") or "")
                for p in proposals
            ],
            "gates": gate_table(gates),
            "active_after_cycle": report.get("active_after_cycle"),
            "marginal_selection": report.get("marginal_selection"),
            "mini_effect": (report.get("mini_effect") or {}).get("passed"),
        })

    experiment = load_json(work_root / "reports" / "experiment.json")
    active_registry = load_json(work_root / "registry" / "active.json")

    return {
        "work_root": str(work_root),
        "ledger_events": len(ledger),
        "event_counts": dict(sorted(events.items())),
        "ledger_integrity": load_json(work_root / "reports" / "defect_audit_verdict.json"),
        "cycles": cycles,
        "experiment": experiment,
        "active_registry": active_registry,
        "admissions": [
            e.get("payload") for e in ledger if e.get("event") == "admitted"
        ],
        "rejections": [
            {
                "cycle": (e.get("payload") or {}).get("cycle"),
                "gate": (e.get("payload") or {}).get("gate"),
                "reason": str((e.get("payload") or {}).get("reason") or "")[:200],
                "intervention_id": (e.get("payload") or {}).get("intervention_id"),
            }
            for e in ledger if e.get("event") == "rejected"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--json")
    args = parser.parse_args()
    report = analyze(Path(args.work_root))
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
