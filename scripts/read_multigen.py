#!/usr/bin/env python
"""Read a multi-generation evolution run and answer P2: why did candidates stop?

§16 E2 requires that a run producing no admission answer five questions rather
than reporting "0 promoted": is the mechanism unrepairable, is the action class
underpowered, did candidate design fail, was there no formal gain opportunity,
or did harmlessness fail? §23.3 P0.4 turned those into a machine-readable block.

The reason this is a separate reader and not a summary written by hand: §32.3
found three places where "not measured" was read as "falsified" -- a candidate
arm that fail-closed on every case was written into the append-only episodic
memory as `falsified: true` and permanently banned from re-proposal. A run whose
arm never executed must read `not_evaluated`, and the only way to keep that
distinction honest is to read it from the artefact that records it
(``terminal_classification``) instead of from a prose summary.

    python scripts/read_multigen.py \
        --run artifacts/selfevo-real-multigen-20260923 \
        --json artifacts/<date>_real_experiment/multigen.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.engine import terminal_classification  # noqa: E402

# Codes a promotion attempt can report, mapped to the question they answer.
DIAGNOSIS_QUESTIONS = {
    "mechanism_unrepairable": "是 mechanism 不可修？",
    "action_class_insufficient": "是 action class 能力不足？",
    "candidate_design_failed": "是 candidate 设计失败？",
    "no_gain_opportunity": "是 formal gain 无机会？",
    "harmlessness_failed": "是 harmlessness 失败？",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def read_run(run: Path) -> dict[str, Any]:
    ledger = _read_jsonl(run / "ledger.jsonl")
    cycles: list[dict[str, Any]] = []
    for path in sorted((run / "cycles").glob("cycle*.json")):
        cycles.append(json.loads(path.read_text(encoding="utf-8-sig")))

    per_cycle: list[dict[str, Any]] = []
    for report in cycles:
        instrument = report.get("instrument") or {}
        # The per-candidate verdict lives under ``gates``: one entry per
        # candidate that reached a gate, carrying the four gate blocks. The
        # ``proposals`` list is the drafted candidates, which is a different
        # set -- a run can draft three and put zero through a gate (that is
        # exactly what §31 run 1 did). Reading proposals as candidates would
        # report verdicts for candidates that were never scored.
        gate_reports = report.get("gates") or []
        rows: list[dict[str, Any]] = []
        for item in gate_reports:
            item = dict(item)
            classification = item.get("terminal_classification")
            if not classification:
                classification = terminal_classification(
                    item, admitted=bool(item.get("admitted")),
                )
            failing = [
                gate for gate in ("structural", "harmlessness", "behavior", "m")
                if (item.get(gate) or {}).get("passed") is False
            ]
            rows.append({
                "candidate": item.get("intervention_id") or item.get("id"),
                "classification": classification,
                "failing_gates": failing,
                "admitted": bool(item.get("admitted")),
                "diagnosis": item.get("promotion_diagnosis"),
                "terminal_reason": item.get("terminal_reason"),
                # An arm that never ran is named here, because it is the
                # difference between "the mechanism is bad" and "the machine
                # ran out of clock" -- §32.3's whole point. The markers are the
                # ones ``gate_rejection_is_unevaluable`` actually reads; reading
                # a key the engine never sets would report "measured" forever.
                "unmeasurable_markers": {
                    key: (item.get(gate) or {}).get(key)
                    for gate in ("structural", "harmlessness", "behavior", "m")
                    for key in ("infrastructure_failure", "stability_limited",
                                "harm_entirely_on_unstable", "evaluable")
                    if isinstance(item.get(gate), Mapping)
                    if (item.get(gate) or {}).get(key) is not None
                },
                "arm_failed_closed": any(
                    (item.get(gate) or {}).get("infrastructure_failure")
                    for gate in ("structural", "harmlessness", "behavior", "m")
                    if isinstance(item.get(gate), Mapping)
                ),
                "gate_reasons": {
                    gate: str((item.get(gate) or {}).get("reason") or "")
                    for gate in ("structural", "harmlessness", "behavior", "m")
                    if isinstance(item.get(gate), Mapping)
                },
            })
        per_cycle.append({
            "cycle": report.get("cycle"),
            "state": report.get("state"),
            "round_path": report.get("round_path"),
            "patrol": report.get("patrol"),
            "mechanisms": report.get("mechanisms"),
            "capability_plan": report.get("capability_plan"),
            "route_failures": report.get("route_failures"),
            "noise_floor": instrument.get("noise_floor"),
            "stability_limited_units": list(
                instrument.get("stability_limited_units") or ()
            ),
            "terminal_classification": dict(
                instrument.get("terminal_classification") or {}
            ),
            "development": report.get("development"),
            "proposals_drafted": len(report.get("proposals") or ()),
            "candidates": rows,
            "active_after_cycle": list(report.get("active_after_cycle") or ()),
            "admitted": [
                str(item.get("intervention_id") or "")
                for item in gate_reports
                if item.get("admitted")
            ],
        })

    events = [str(record.get("event") or "") for record in ledger]
    classifications = [row["classification"] for cycle in per_cycle
                       for row in cycle["candidates"]]
    counts: dict[str, int] = {}
    for name in classifications:
        counts[name] = counts.get(name, 0) + 1

    # The five questions, asked once against every candidate that did not pass.
    unanswered = [
        row for cycle in per_cycle for row in cycle["candidates"]
        if not row["admitted"] and not row["diagnosis"]
    ]
    return {
        "schema_version": "detector.multigen_reading.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run": str(run),
        "ledger": {
            "records": len(ledger),
            "events": {name: events.count(name) for name in sorted(set(events))},
        },
        "cycles": per_cycle,
        "summary": {
            "cycles_run": len(per_cycle),
            "candidates_total": len(classifications),
            "classifications": counts,
            "admitted": sum(len(cycle["admitted"]) for cycle in per_cycle),
            "candidates_without_a_diagnosis": len(unanswered),
            "five_questions": {
                code: {
                    "question": question,
                    "count": sum(
                        1 for cycle in per_cycle for row in cycle["candidates"]
                        if (row.get("diagnosis") or {}).get("code") == code
                    ),
                }
                for code, question in DIAGNOSIS_QUESTIONS.items()
            },
        },
        # P2's honest condition: a run with 0 admissions answers the five
        # questions only if every non-admitted candidate carries a code.
        "p2_answered": bool(per_cycle) and not unanswered,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="work_root of the run")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)
    payload = read_run(Path(args.run))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
