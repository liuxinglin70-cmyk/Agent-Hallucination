"""Meta-evaluation of the evolution mechanism itself.

Meta-evaluation is read-only: it never changes the registry, gate parameters,
ledger, or the final verification set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution_v3.contracts import save_json
from evolution_v3.ledger import Ledger


def _rows_from_reports(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        cycle = report.get("cycle")
        for item in report.get("proposals") or []:
            rows.append({
                "cycle": cycle,
                "intervention_id": item.get("intervention_id"),
                "type": item.get("type"),
                "hook": item.get("hook"),
                "failure_family": item.get("failure_family"),
                "proposed": True,
                "admitted": item.get("intervention_id") in (
                    report.get("active_after_cycle") or []
                ),
            })
        for gate in report.get("gates") or []:
            row = next(
                (
                    r for r in rows
                    if r.get("cycle") == cycle
                    and r.get("intervention_id") == gate.get("intervention_id")
                ),
                None,
            )
            if row is None:
                continue
            row["v1"] = (gate.get("v1") or {}).get("passed")
            row["v2"] = (gate.get("v2") or {}).get("passed")
            row["v3"] = (gate.get("v3") or {}).get("passed")
        selection = report.get("marginal_selection") or {}
        selected_marginals = {
            str(item.get("intervention_id") or ""): (
                item.get("delta") or {}
            ).get("marginal_cases")
            for item in selection.get("selected_attribution") or []
        }
        for item in selection.get("reports") or []:
            candidate = item.get("candidate") or {}
            row = next(
                (
                    r for r in rows
                    if r.get("cycle") == cycle
                    and r.get("intervention_id") == candidate.get("intervention_id")
                ),
                None,
            )
            if row is None:
                continue
            row["m_marginal_cases"] = (item.get("delta") or {}).get(
                "marginal_cases"
            )
            row["m_passed"] = item.get("passed")
        for item in selection.get("joint_reports") or []:
            for candidate_id in item.get("candidate_ids") or []:
                row = next(
                    (
                        r for r in rows
                        if r.get("cycle") == cycle
                        and r.get("intervention_id") == candidate_id
                    ),
                    None,
                )
                if row is None:
                    continue
                row["m_marginal_cases"] = (item.get("delta") or {}).get(
                    "marginal_cases"
                )
                row["m_passed"] = item.get("passed")
                row["joint_group"] = list(item.get("candidate_ids") or [])
        for intervention_id, marginal in selected_marginals.items():
            row = next(
                (
                    item for item in rows
                    if item.get("cycle") == cycle
                    and item.get("intervention_id") == intervention_id
                ),
                None,
            )
            if row is not None:
                row["m_marginal_cases"] = marginal
                row["m_attribution"] = "leave_one_out_final_configuration"
    return rows


def compute_meta(
    cycle_reports: Sequence[Mapping[str, Any]],
    ledger: Ledger | None = None,
) -> dict[str, Any]:
    rows = _rows_from_reports(cycle_reports)
    audits = [
        report.get("audit") or {}
        for report in cycle_reports
        if report.get("audit") is not None
    ]
    audit_passed = all(
        bool(audit.get("passed")) for audit in audits
        if audit.get("enabled", True)
    )
    marginals = [
        int(row.get("m_marginal_cases") or 0)
        for row in rows
        if row.get("m_marginal_cases") is not None
    ]
    proposed = [row for row in rows if row.get("proposed")]
    reached_m = [
        row for row in proposed
        if row.get("v1") is True
        and row.get("v2") is True
        and row.get("v3") is True
        and row.get("m_passed") is not None
    ]
    proposer_false_pass = [
        row for row in reached_m if row.get("m_passed") is False
    ]
    proposer_false_pass_rate = (
        len(proposer_false_pass) / len(reached_m) if reached_m else 0.0
    )
    admitted = [row for row in rows if row.get("admitted")]
    library_drift = [
        report.get("empty_vs_active_on_g") or {}
        for report in cycle_reports
    ]
    net_contributions = [
        int(item.get("marginal_cases") or 0) for item in library_drift
    ]
    ledger_integrity = ledger.verify() if ledger is not None else {
        "valid": None,
        "records": 0,
    }
    process_quality = {
        "patrol_cycles": len(cycle_reports),
        "stable_families_per_cycle": [
            len((report.get("patrol") or {}).get("stable_failure_families") or [])
            for report in cycle_reports
        ],
        "bootstrap_used_per_cycle": [
            (report.get("patrol") or {}).get("bootstrap", {}).get("bootstrap_count", 0)
            for report in cycle_reports
        ],
        "empty_proposal_reasons": [
            (report.get("proposal_summary") or {}).get("empty_proposal_reason")
            for report in cycle_reports
            if (report.get("proposal_summary") or {}).get("empty_proposal_reason")
        ],
        "proposals_total": len(proposed),
        "admitted_total": len(admitted),
    }
    return {
        "defect_injection_audit": {
            "cycles": len(audits),
            "passed": audit_passed,
            "action_threshold": "freeze on any defect passing the gate",
            "verdict": "pass" if audit_passed else "freeze",
        },
        "marginal_distribution": {
            "values": marginals,
            "all_nonpositive": bool(marginals and max(marginals) <= 0),
            "action_threshold": "all <= 0 with non-empty proposals -> fix proposer",
        },
        "proposer_false_pass_rate": {
            "value": proposer_false_pass_rate,
            "n_proposals": len(proposed),
            "n_reached_marginal_gate": len(reached_m),
            "n_false_pass": len(proposer_false_pass),
            "action_threshold": "two consecutive cycles >= 0.5 -> pause synthesis",
        },
        "library_drift": {
            "net_contribution_history": net_contributions,
            "action_threshold": (
                "two consecutive cycles < 0 -> rollback to last positive snapshot"
            ),
        },
        "process_quality": process_quality,
        "cross_source_transfer": {
            "warning": (
                "benchmark-specialist intervention detected when gain is "
                "positive in one source and negative in all others"
            ),
            "computed_in_final": True,
        },
        "ledger_integrity": ledger_integrity,
    }


def write_meta_report(
    cycle_reports: Sequence[Mapping[str, Any]],
    ledger: Ledger,
    output_path: str | Path,
) -> dict[str, Any]:
    report = compute_meta(cycle_reports, ledger=ledger)
    save_json(output_path, report)
    return report


__all__ = ["compute_meta", "write_meta_report"]
