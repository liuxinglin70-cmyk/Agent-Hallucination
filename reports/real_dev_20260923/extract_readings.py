#!/usr/bin/env python
"""Extract the development-loop real run's readings. Read-only.

    python reports/real_dev_20260923/extract_readings.py [run_root]

Reads a completed (or partially completed) run directory and prints the
machine-readable readings this report needs. It re-runs nothing, calls no
provider, and writes nothing into the run directory.

The reading this run exists for is ``round_path`` plus the development block:
the first real cycle in which the development stage ran inside the cycle. So the
extractor separates the readings that are *about the stage*:

* did the cycle take the development path at all, and onto which mechanism;
* how many versions it produced, their L0/L1 verdicts and why each was refused;
* what reached the four gates, and whether the stage changed that;
* whether the loop's own budget stayed inside its caps;
* whether a resumed or single-shot path is being reported by mistake.

It also reports the store paths the cycle was supposed to write (§28.6
``cycle_end`` now has a run path), distinguishing "no content to write" from
"the file is missing", because those two are not the same finding.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime
from typing import Any, Mapping

DEFAULT_ROOT = pathlib.Path("artifacts/selfevo-real-dev-20260923")
FINAL_STATES = {"completed", "blocked_evaluation_preflight"}


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


def store_reading(path: pathlib.Path) -> dict[str, Any]:
    """Present / records for one store, kept apart on purpose.

    ``present=False, records=None`` means the evidence does not contain this
    file; ``present=True, records=0`` means it exists and is empty. Collapsing
    those two into a single ``0`` once hid a missing archive behind an
    expected-empty one.
    """
    if not path.exists():
        return {"present": False, "records": None, "path": str(path)}
    rows = read_jsonl(path) if path.suffix == ".jsonl" else None
    if rows is None:
        payload = load(path)
        rows = len(payload) if isinstance(payload, list) else (
            1 if payload is not None else 0
        )
    return {"present": True, "records": len(rows), "path": str(path)}


def timing_section(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the cycle's wall clock went, against its own cap.

    Added because the first version of this extractor dropped the ledger's
    ``timestamp`` field (it read ``at``), so a cycle that ran out of wall clock
    looked like an ordinary round. The audit and the patrol share one deadline,
    and a cycle whose audit alone consumes it will fail-close its patrol into
    ``unresolved`` rows -- which changes what every later reading means.
    """
    def parse(value: Any):
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    stamps = [
        (str(record.get("event") or ""), parse(record.get("timestamp")))
        for record in ledger
    ]
    stamps = [(name, at) for name, at in stamps if at is not None]
    if not stamps:
        return {"available": False}
    start = stamps[0][1]
    phases = [
        {"event": name, "at": at.isoformat(),
         "minutes_since_start": round((at - start).total_seconds() / 60, 2),
         "gap_minutes": round((at - stamps[index - 1][1]).total_seconds() / 60, 2)
         if index else 0.0}
        for index, (name, at) in enumerate(stamps)
    ]
    return {
        "available": True,
        "start_utc": start.isoformat(),
        "end_utc": stamps[-1][1].isoformat(),
        "total_minutes": round((stamps[-1][1] - start).total_seconds() / 60, 2),
        "phases": phases,
    }


def contract_section(root: pathlib.Path) -> dict[str, Any]:
    checkpoint = load(root / "checkpoints" / "cycle001" / "checkpoint.json") or {}
    contract = checkpoint.get("contract") or {}
    split = contract.get("split") or {}
    return {
        "config_hash": contract.get("config"),
        "source_hash": contract.get("source"),
        "detector_config_hash": contract.get("detector_config"),
        "split": {
            key: split.get(key)
            for key in (
                "name", "seed", "pool_size", "sentinel_size", "gain_size",
                "gain_polarity", "patrol_pool_size", "validation_size",
                "final_size", "final_reads",
            )
        },
        "sentinel": split.get("sentinel"),
        "gain": split.get("gain"),
        "registry_before": checkpoint.get("registry_before"),
    }


def development_section(report: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the cycle report says about this round's development stage."""
    block = report.get("development")
    if not isinstance(block, Mapping):
        return {
            "round_path": report.get("round_path"),
            "present": False,
            "note": (
                "the cycle report carries no development block: either the "
                "loop was off (round_path single_shot) or the round stopped "
                "before the stage (round_path not_reached)"
            ),
        }
    runs = []
    for run in block.get("runs") or ():
        versions = []
        for item in run.get("versions") or ():
            versions.append({
                "version_id": item.get("version_id"),
                "parent_version_id": item.get("parent_version_id"),
                "status": item.get("status"),
                "l0_passed": item.get("l0_passed"),
                "l1_passed": item.get("l1_passed"),
                "l1_reasons": list(item.get("l1_reasons") or ()),
            })
        runs.append({
            "failure_family": run.get("failure_family"),
            "target_kind": run.get("target_kind"),
            "mechanism_id": run.get("mechanism_id"),
            "status": run.get("status"),
            "stopped": run.get("stopped"),
            "selected_version_id": run.get("selected_version_id"),
            "versions": versions,
            "target_cases": list(run.get("target_cases") or ()),
            "neighbor_cases": list(run.get("neighbor_cases") or ()),
            "budget": run.get("budget"),
            "frozen": run.get("frozen"),
            "archive_policy": run.get("archive_policy"),
            "archive": (
                {
                    key: run["archive"].get(key)
                    for key in ("archived", "policy", "recorded", "reason")
                }
                if isinstance(run.get("archive"), Mapping) else None
            ),
        })
    return {
        "round_path": report.get("round_path"),
        "present": True,
        "schema_version": block.get("schema_version"),
        "targets": block.get("targets"),
        "candidate_count": block.get("candidate_count"),
        "frozen_versions": block.get("frozen_versions"),
        "skipped": block.get("skipped"),
        "reason": block.get("reason"),
        "metric_role": block.get("metric_role"),
        "runs": runs,
        "version_total": sum(len(run["versions"]) for run in runs),
        "versions_failed_l0": sum(
            1 for run in runs for item in run["versions"]
            if item["l0_passed"] is False
        ),
        "versions_failed_l1": sum(
            1 for run in runs for item in run["versions"]
            if item["l1_passed"] is False
        ),
        "versions_passed": sum(
            1 for run in runs for item in run["versions"]
            if item["l1_passed"] is True
        ),
    }


def gate_section(report: Mapping[str, Any]) -> dict[str, Any]:
    entries = []
    for item in report.get("gates") or ():
        if not isinstance(item, Mapping):
            continue
        steps = item.get("gates")
        if isinstance(steps, Mapping):
            verdicts = {
                str(key): bool((step or {}).get("passed"))
                for key, step in steps.items()
            }
        else:
            verdicts = {
                str(key): bool(value) for key, value in item.items()
                if key not in {"intervention_id", "candidate", "reason"}
            }
        entries.append({
            "intervention_id": item.get("intervention_id"),
            "passed": verdicts,
            "reason": item.get("reason"),
        })
    return {
        "candidates_reaching_the_gates": len(entries),
        "entries": entries,
        "admitted": [
            str(item) for item in
            (report.get("marginal_selection") or {}).get("selected") or ()
        ],
        "active_after_cycle": [
            str(item) for item in report.get("active_after_cycle") or ()
        ],
    }


def provider_usage(root: pathlib.Path) -> dict[str, Any]:
    """Tokens and calls the run recorded, if it recorded them."""
    usage: dict[str, Any] = {}
    for path in sorted(root.rglob("usage*.json*")):
        payload = load(path) if path.suffix != ".jsonl" else (
            read_jsonl(path)
        )
        if payload:
            usage[str(path.relative_to(root))] = payload
    return usage


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    if not root.exists():
        print(json.dumps({
            "run_root": str(root),
            "exists": False,
            "note": "the run directory does not exist yet",
        }, indent=2, ensure_ascii=False))
        return 2
    ledger = read_jsonl(root / "ledger.jsonl")
    report = load(root / "cycles" / "cycle001.json") or {}
    development = development_section(report)
    gate = gate_section(report)
    effect = report.get("active_effect_on_g") or {}
    patrol = report.get("patrol") or {}
    instrument = report.get("instrument") or {}
    readings = {
        "run_root": str(root),
        "exists": True,
        "state": report.get("state"),
        "state_is_final": report.get("state") in FINAL_STATES,
        "contract": contract_section(root),
        "ledger": {
            "events": [
                {"timestamp": record.get("timestamp"), "event": record.get("event")}
                for record in ledger
            ],
            "count": len(ledger),
            "has_development_event": any(
                record.get("event") == "development" for record in ledger
            ),
        },
        "timing": timing_section(ledger),
        "development_event": next(
            (record.get("payload") for record in ledger
             if record.get("event") == "development"),
            None,
        ),
        "round_path": report.get("round_path"),
        "development": development,
        "gates": gate,
        # The claim the stage makes: it changes what reaches the gates. These
        # two numbers together are that claim as a reading.
        "stage_effect": {
            "versions_produced": development.get("version_total"),
            "versions_passed": development.get("versions_passed"),
            "candidates_reaching_the_gates": gate["candidates_reaching_the_gates"],
            # The claim the stage makes, as two readings that must move
            # together: versions it falsified, and candidates the gates then
            # saw. A run where the first is above zero and the second is zero
            # is the stage doing its job.
            "versions_falsified": development.get("versions_failed_l1"),
            "gates_saw_nothing_when_nothing_passed": (
                development.get("versions_passed") == 0
                and gate["candidates_reaching_the_gates"] == 0
                if development.get("present") else None
            ),
            "proposals_in_cycle": len(report.get("proposals") or ()),
        },
        "patrol": {
            # The worksheet keeps the confusion counts at the top level, not
            # under a ``confusion`` key; reading the wrong path reported
            # ``null`` for every count and hid that this patrol ran out of wall
            # clock. The ``errors`` list is the other half: it names the
            # fail-closed reason per case.
            "confusion": {
                key: patrol.get(key)
                for key in ("tp", "tn", "fn", "fp", "unresolved")
            },
            "patrol_size": patrol.get("patrol_size"),
            # A count of rows that carry an error, not a list of them.
            "error_rows": patrol.get("errors"),
            "bootstrap": patrol.get("bootstrap"),
            "family_evidence": patrol.get("family_evidence"),
            "stable_failure_families": [
                {
                    "failure_family": item.get("failure_family"),
                    "count": item.get("count"),
                    "mechanism_id": (item.get("mechanism") or {}).get("mechanism_id"),
                    "classification": (item.get("actionability") or {}).get(
                        "classification"
                    ),
                }
                for item in report.get("patrol", {}).get(
                    "stable_failure_families",
                ) or ()
            ],
            "mechanisms": (patrol.get("mechanisms") or {}).get("count"),
            "capability_gaps": len(report.get("capability_plan", {}).get("gaps") or ())
            if isinstance(report.get("capability_plan"), Mapping) else None,
        },
        "instrument": {
            "gain_opportunity": instrument.get("gain_opportunity"),
            "unmeasurable_polarities": instrument.get("unmeasurable_polarities"),
            "instrument_limited": instrument.get("instrument_limited"),
        },
        "effect_on_gain_set": {
            key: effect.get(key)
            for key in (
                "paired_n", "base_all_case_correct", "active_all_case_correct",
                "marginal_cases", "new_errors", "unresolved_added",
                "health_regressed",
            )
        },
        "stores": {
            "route_failures": store_reading(root / "harness" / "route_failures.jsonl"),
            "gaps": store_reading(root / "harness" / "gaps.jsonl"),
            "candidates": {
                "root_exists": (root / "candidates").exists(),
                "files": [
                    str(path.relative_to(root)) for path in
                    sorted((root / "candidates").rglob("*.json"))
                ] if (root / "candidates").exists() else [],
            },
            "components": {
                "root_exists": (root / "components").exists(),
                "files": [
                    str(path.relative_to(root)) for path in
                    sorted((root / "components").rglob("*.json"))
                ] if (root / "components").exists() else [],
            },
            "memory_episodic": store_reading(root / "memory" / "episodic.jsonl"),
            "development_runs": [
                str(path.relative_to(root)) for path in
                sorted((root / "development").rglob("development.json"))
            ] if (root / "development").exists() else [],
        },
        "capability_backlog": report.get("capability_backlog"),
        "route_failures_report": report.get("route_failures"),
        "harness": report.get("harness"),
        "provider_usage": provider_usage(root),
        "ledger_integrity": report.get("ledger_integrity"),
    }
    print(json.dumps(readings, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
