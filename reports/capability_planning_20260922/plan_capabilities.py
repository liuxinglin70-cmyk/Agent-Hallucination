"""P0.2 dry-run: capability planning and route falsification over archived rows.

Answers the two questions the work package exists for, without touching detector
behaviour and without a provider call:

1. Given the mechanisms P0.1 reconstructed from history, can the runtime
   actually repair them -- and if not, which capability is missing?
2. If a route fires on a mechanism and moves nothing, is that recorded, and does
   the planner then stop offering it?

Run from the repository root:

    python reports/capability_planning_20260922/plan_capabilities.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.capability_planning import (  # noqa: E402
    capability_surface_report,
    plan_capabilities,
)
from evolution.config import load_config  # noqa: E402
from evolution.engine import split_actionable_families  # noqa: E402
from evolution.gaps import gap_identity  # noqa: E402
from evolution.route_failures import (  # noqa: E402
    load_route_failures,
    record_route_failures,
    route_failure_summary,
)
from evolution.splits import load_gold_step  # noqa: E402
from evolution.worksheet import (  # noqa: E402
    diagnose_failure_families,
    excluded_failure_cases,
    mechanism_subgroups,
    refine_failure_families,
    stable_failure_families,
    stable_localization_families,
)

ARCHIVE = REPO / "reports" / "error_scope_20260918" / "attempts"
OUT = pathlib.Path(__file__).resolve().parent / "capability_plan.json"


def load_rows() -> list[dict]:
    rows: dict[str, dict] = {}
    for arm in sorted(ARCHIVE.glob("*/run/checkpoints/cycle*/arms/*")):
        for path in sorted(arm.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            row = payload.get("row") or {}
            case = str(row.get("case") or "")
            if case and case not in rows:
                rows[case] = row
    return list(rows.values())


def main() -> int:
    config = load_config(str(REPO / "evolution" / "configs" / "development.json"))
    rows = load_rows()
    gold_steps = {}
    for row in rows:
        try:
            step = load_gold_step(config["dataset_root"], str(row.get("case")))
        except Exception:
            step = None
        if step is not None:
            gold_steps[str(row.get("case"))] = step

    coarse = [
        *stable_failure_families(rows),
        *stable_localization_families(
            rows, gold_steps=gold_steps, records={},
        ),
    ]
    diagnosed = diagnose_failure_families(
        coarse, rows, dataset_root=config["dataset_root"],
    )
    refined = refine_failure_families(diagnosed)
    excluded = excluded_failure_cases(coarse, refined)
    subgroups, unrepresented = mechanism_subgroups(refined)
    actionable, capability_gaps = split_actionable_families(subgroups)

    plan = plan_capabilities(actionable)

    # The pilot's Attempt-5 shape, applied to the controlling route of every
    # mechanism that has one: two independent observations of "the hook fired,
    # the behaviour moved, the outcome did not". The mechanism is the unit the
    # memory is keyed on, so the simulation runs per mechanism rather than on a
    # fixed route name.
    with tempfile.TemporaryDirectory(prefix="p02_dryrun_") as temporary:
        store = pathlib.Path(temporary) / "route_failures.jsonl"
        simulations = []
        for requirement in plan["requirements"]:
            controlling = [
                route for route in requirement["sufficient_routes"]
                if route in {"recall_trigger", "tracer_directive"}
            ]
            if not controlling:
                continue
            route = controlling[0]
            for cycle in (1, 2):
                record_route_failures(store, cycle=cycle, observations=[{
                    "mechanism_id": requirement["mechanism_id"],
                    "family_id": "",
                    "observation": {
                        "route": route,
                        "status": "insufficient_for_mechanism",
                        "evidence": {
                            "hook_fired": 1.0,
                            "output_changed": 21,
                            "target_outcome_changed": 0,
                        },
                    },
                }])
            simulated = plan_capabilities(
                actionable, route_failure_records=load_route_failures(store),
            )
            for item in simulated["requirements"]:
                if item["mechanism_id"] == requirement["mechanism_id"]:
                    simulations.append({
                        "mechanism_id": item["mechanism_id"],
                        "falsified_route": route,
                        "before": {
                            "classification": requirement["classification"],
                            "sufficient_routes": requirement["sufficient_routes"],
                        },
                        "after": {
                            "classification": item["classification"],
                            "required_capability": item["required_capability"],
                            "sufficient_routes": item["sufficient_routes"],
                            "insufficient_routes": item["insufficient_routes"],
                        },
                    })

    payload = {
        "schema_version": "evolution.capability_plan_dryrun.v1",
        "evidence_grade": "diagnostic-only",
        "note": (
            "Capability planning over the mechanisms P0.1 reconstructed from "
            "archived rows. No detector call, no provider call, no admission."
        ),
        "source_archive": ARCHIVE.relative_to(REPO).as_posix(),
        "capability_surface": capability_surface_report(),
        "families": {
            "coarse": len(coarse),
            "subgroups": len(subgroups),
            "unrepresented": len(unrepresented),
            "excluded_cases": sum(
                item["excluded_count"] for item in excluded
            ),
            "actionable": len(actionable),
            "worksheet_gap_families": len(capability_gaps),
        },
        "requirements": plan["requirements"],
        "planner_gaps": plan["gaps"],
        "gap_identities": [
            gap_identity(
                str(gap.get("failure_family") or ""),
                str(gap.get("classification") or ""),
            )
            for gap in plan["gaps"]
        ],
        "falsification_simulation": simulations,
        "route_failure_summary": route_failure_summary([]),
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "out": OUT.relative_to(REPO).as_posix(),
        "requirements": len(plan["requirements"]),
        "actionable": [
            item["mechanism_id"] for item in plan["requirements"]
            if item["classification"] == "actionable"
        ],
        "gaps": len(plan["gaps"]),
        "gap_classifications": [
            gap["classification"] for gap in plan["gaps"]
        ],
        "falsification_simulation": simulations,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
