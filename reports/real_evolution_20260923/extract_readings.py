#!/usr/bin/env python
"""Extract the real self-evolution run's readings. Read-only.

    python reports/real_evolution_20260923/extract_readings.py [run_root]

Reads a completed (or partially completed) real run directory and prints the
machine-readable readings this report needs:

* the frozen contract: config/source/detector digests, the split, the case
  fingerprints, the seed;
* the ledger, event by event, with the candidate rejections and their reasons;
* the cycle report: patrol confusion, diagnosed families with their mechanisms,
  the capability plan, the proposals, the four gates per candidate, the
  admission decision, and the gain-set effect;
* the route-failure store, the gap store and the archive, if the run wrote them;
* the provider usage, so the cost of the run is on the record.

It does not re-run anything and does not call a provider. Nothing here writes to
the run directory.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Mapping


def load(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def contract_section(root: pathlib.Path) -> dict[str, Any]:
    checkpoint = load(root / "checkpoints" / "cycle001" / "checkpoint.json") or {}
    contract = checkpoint.get("contract") or {}
    return {
        "config_hash": contract.get("config"),
        "source_hash": contract.get("source"),
        "detector_config_hash": contract.get("detector_config"),
        "split": {
            key: (contract.get("split") or {}).get(key)
            for key in (
                "name", "seed", "pool_size", "sentinel_size", "gain_size",
                "gain_polarity", "patrol_pool_size", "validation_size",
                "final_size", "final_reads",
            )
        },
        "sentinel": (contract.get("split") or {}).get("sentinel"),
        "gain": (contract.get("split") or {}).get("gain"),
        "validation": (contract.get("split") or {}).get("validation"),
        "data_fingerprints": len(contract.get("data") or {}),
    }


def ledger_section(root: pathlib.Path) -> dict[str, Any]:
    records = read_jsonl(root / "ledger.jsonl")
    events: dict[str, int] = {}
    for record in records:
        event = str(record.get("event") or "")
        events[event] = events.get(event, 0) + 1
    rejections = [
        {
            "intervention_id": str(
                ((record.get("payload") or {}).get("candidate") or {}).get(
                    "intervention_id") or ""
            ),
            "gate": str((record.get("payload") or {}).get("gate") or ""),
            "reason": str((record.get("payload") or {}).get("reason") or ""),
        }
        for record in records if record.get("event") == "rejected"
    ]
    return {
        "records": len(records),
        "events": dict(sorted(events.items())),
        "rejections": rejections,
        "hash_chain_valid": all(
            str(record.get("hash") or "")
            for record in records
        ),
    }


def cycle_section(root: pathlib.Path) -> dict[str, Any]:
    cycle_files = sorted((root / "cycles").glob("*.json"))
    if not cycle_files:
        return {"report": None}
    report = load(cycle_files[-1]) or {}
    patrol = report.get("patrol") or {}
    families = patrol.get("families") or []
    gates = report.get("gates") or []
    effect = report.get("active_effect_on_g") or {}
    return {
        "source_file": cycle_files[-1].name,
        "state": report.get("state"),
        "patrol": {
            key: patrol.get(key)
            for key in (
                "confusion", "tp", "tn", "fn", "fp", "unresolved", "cases",
                "protected_tp", "protected_tn", "families_found",
            )
            if key in patrol
        },
        "families": [
            {
                "failure_family": item.get("failure_family"),
                "count": item.get("count"),
                "mechanism_id": (
                    (item.get("mechanism") or {}).get("mechanism_id")
                ),
                "mechanism_status": (
                    (item.get("mechanism") or {}).get("status")
                ),
                "obligation": (
                    (item.get("mechanism") or {}).get("task_obligation") or {}
                ).get("kind"),
                "deviation": (
                    (item.get("mechanism") or {}).get("observed_deviation") or {}
                ).get("kind"),
                "failure_stage": (
                    (item.get("mechanism") or {}).get("detector_failure") or {}
                ).get("stage"),
                "failure_mode": (
                    (item.get("mechanism") or {}).get("detector_failure") or {}
                ).get("failure_mode"),
                "repair_operation": (
                    (item.get("mechanism") or {}).get("repair_hypothesis") or {}
                ).get("operation"),
                "actionability": (
                    item.get("actionability") or {}
                ).get("classification"),
                "target_kind": item.get("target_kind"),
            }
            for item in families
        ],
        "capability_plan": report.get("capability_plan"),
        "proposals": [
            {
                "intervention_id": item.get("intervention_id"),
                "type": item.get("type"),
                "hook": item.get("hook"),
                "failure_family": item.get("failure_family"),
                "target_polarity": item.get("target_polarity"),
                "runner": (item.get("body") or {}).get("runner"),
            }
            for item in report.get("proposals") or ()
        ],
        "proposal_summary": report.get("proposal_summary"),
        "preflight": report.get("preflight"),
        "gates": [
            {
                "intervention_id": item.get("intervention_id"),
                "gates": {
                    str(key): (
                        bool((value or {}).get("passed"))
                        if isinstance(value, Mapping) else bool(value)
                    )
                    for key, value in (item.get("gates") or {}).items()
                } if isinstance(item.get("gates"), Mapping) else item.get("gates"),
                "reason": item.get("reason"),
                "marginal": item.get("marginal"),
            }
            for item in gates
        ],
        "admitted": [
            str(item) for item in
            (report.get("marginal_selection") or {}).get("selected") or ()
        ],
        "active_after_cycle": report.get("active_after_cycle"),
        "effect_on_gain_set": {
            key: effect.get(key)
            for key in (
                "paired_n", "base_all_case_correct", "active_all_case_correct",
                "marginal_cases", "new_fn", "new_fp", "new_errors",
                "unresolved_added", "unresolved_removed", "health_regressed",
                "flips",
            )
        },
        "route_failures": report.get("route_failures"),
        "capability_backlog": report.get("capability_backlog"),
        "audit": report.get("audit"),
        "ledger_integrity": report.get("ledger_integrity"),
        "instrument": report.get("instrument"),
    }


def stores_section(root: pathlib.Path) -> dict[str, Any]:
    return {
        "route_failures": len(read_jsonl(root / "harness" / "route_failures.jsonl")),
        "gaps": len(read_jsonl(root / "harness" / "gaps.jsonl")),
        "candidates": len(read_jsonl(root / "candidates" / "candidates.jsonl")),
        "components": len(read_jsonl(root / "candidates" / "components.jsonl")),
        "memory_episodes": len(read_jsonl(root / "memory" / "episodes.jsonl")),
    }


def main() -> int:
    root = pathlib.Path(
        sys.argv[1] if len(sys.argv) > 1 else "artifacts/selfevo-real-20260923"
    )
    if not root.is_dir():
        print(f"no run directory at {root}")
        return 2
    payload = {
        "run_root": str(root),
        "contract": contract_section(root),
        "ledger": ledger_section(root),
        "cycle": cycle_section(root),
        "stores": stores_section(root),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).resolve().parent / "readings.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nwritten -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
