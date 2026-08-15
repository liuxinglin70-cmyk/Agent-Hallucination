"""Audit run-record packet ID integrity + evidence reference validity.

Two modes:

1. ``audit-runs``: for every case under ``runs/<run_id>/cases/`` verify
   - packet invariants (ordinal positions, unique ids, parent prefix, and
     that ``rsplit("_tool_", 1)`` recovers the parent),
   - census candidate ``locus_event_id`` / ``evidence_refs`` membership in
     the packet id universe,
   - certificate/defense candidate linkage,
   - ``responsible_event_id`` validity and gold localization when the run
     carries ``eval_gold.json``.

2. ``measure-cap``: compile real dataset trajectories into packets and
   render them, reporting how much of the trajectory actually reaches the
   provider under the 24k-char render cap.

Usage:
    python tools/audit_packet_ids.py audit-runs [--runs-root runs]
    python tools/audit_packet_ids.py measure-cap [--dataset datasets/raw/agenthallu/AgentHallu]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sehd_rise2.detector.packet import (
    build_packet,
    render_full_view,
    render_index_view,
)

EVENT_RE = re.compile(r"^event_\d+$")
INTERACTION_RE = re.compile(r"^event_\d+_tool_\d+$")


def _split_parent(interaction_id: str) -> str:
    return str(interaction_id).rsplit("_tool_", 1)[0]


def audit_one_case(case_dir: Path, gold: dict[str, Any] | None) -> dict[str, Any]:
    result_path = case_dir / "result.json"
    packet_path = case_dir / "packet.json"
    issues: list[str] = []
    if not result_path.is_file() or not packet_path.is_file():
        return {
            "case_id": str(case_dir.name), "events": 0, "interactions": 0,
            "census_candidates": 0, "interaction_refs": 0,
            "responsible_event_id": None, "responsible_step": None,
            "state": None, "issues": ["missing result.json or packet.json"],
            "localization": {},
        }
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    events = packet.get("events") or []
    universe: set[str] = set()
    event_positions: dict[str, int] = {}
    for event in events:
        eid = str(event.get("event_id") or "")
        universe.add(eid)
        event_positions[eid] = int(event.get("position") or 0)
        for interaction in event.get("tool_interactions") or []:
            universe.add(str(interaction.get("interaction_id") or ""))
    # --- packet invariants ---
    ids = [str(e.get("event_id") or "") for e in events]
    if ids != [f"event_{i:03d}" for i in range(1, len(ids) + 1)]:
        issues.append(f"event ids not sequential: {ids[:5]}...")
    positions = [int(e.get("position") or 0) for e in events]
    if positions != list(range(1, len(events) + 1)):
        issues.append(f"positions not 1..N: {positions}")
    for event in events:
        eid = str(event.get("event_id") or "")
        for interaction in event.get("tool_interactions") or []:
            iid = str(interaction.get("interaction_id") or "")
            if not INTERACTION_RE.match(iid):
                issues.append(f"interaction id shape: {iid!r}")
            if not iid.startswith(eid + "_tool_"):
                issues.append(f"parent prefix violated: {iid!r} not under {eid!r}")
            if _split_parent(iid) != eid:
                issues.append(f"rsplit parent mismatch: {iid!r} -> {_split_parent(iid)!r} != {eid!r}")
    all_ids = sorted(universe)
    if len(all_ids) != len(set(all_ids)):
        issues.append("duplicate ids in universe")
    # --- census references ---
    proof = result.get("proof_adjudication") or {}
    census = proof.get("census") or []
    candidates = [str(c.get("candidate_id") or "") for c in census]
    if len(candidates) != len(set(candidates)):
        issues.append("duplicate census candidate ids")
    bad_locus: list[str] = []
    bad_refs: list[str] = []
    interaction_refs = 0
    for c in census:
        locus = str(c.get("locus_event_id") or "")
        if locus and locus not in universe:
            bad_locus.append(locus)
        for ref in c.get("evidence_refs") or []:
            ref = str(ref)
            if ref and ref not in universe:
                bad_refs.append(ref)
            if ref and "_tool_" in ref:
                interaction_refs += 1
    if bad_locus:
        issues.append("census locus ids not in packet: " + ",".join(sorted(set(bad_locus))[:5]))
    if bad_refs:
        issues.append("census evidence refs not in packet: " + ",".join(sorted(set(bad_refs))[:5]))
    # --- certificate / defense linkage ---
    certificates = proof.get("certificates") or []
    cert_ids = [str(c.get("candidate_id") or "") for c in certificates]
    if set(cert_ids) - set(candidates):
        issues.append(
            "certificates for unknown candidates: "
            + ",".join(sorted(set(cert_ids) - set(candidates)))
        )
    decisions = proof.get("defense_decisions") or {}
    supported = [cid for cid in cert_ids if str(
        next((c.get("verdict") for c in certificates if str(c.get("candidate_id")) == cid), "")
    ) == "supported"]
    if set(decisions) - set(supported):
        issues.append(
            "defense decisions for non-supported certificates: "
            + ",".join(sorted(set(decisions) - set(supported)))
        )
    # --- responsible_event_id ---
    responsible = result.get("responsible_event_id")
    if responsible:
        if responsible not in universe and _split_parent(responsible) not in universe:
            issues.append(f"responsible_event_id not in packet: {responsible!r}")
    # --- gold localization ---
    localization: dict[str, Any] = {}
    if gold is not None:
        case_key = str(result.get("case_id") or "")
        row = gold.get("cases", {}).get(case_key) if isinstance(gold.get("cases"), dict) else next(
            (c for c in gold.get("cases") or [] if str(c.get("case")) == case_key), None
        )
        if row and row.get("gold_step") is not None:
            gold_step = int(row["gold_step"])
            resp_step = result.get("responsible_step")
            localization = {
                "gold_step": gold_step,
                "predicted_step": resp_step,
                "exact": resp_step == gold_step,
                "offset": (resp_step - gold_step) if resp_step is not None else None,
            }
    return {
        "case_id": result.get("case_id"),
        "events": len(events),
        "interactions": len(universe) - len(events),
        "census_candidates": len(candidates),
        "interaction_refs": interaction_refs,
        "responsible_event_id": responsible,
        "responsible_step": result.get("responsible_step"),
        "state": result.get("state"),
        "issues": issues,
        "localization": localization,
    }


def audit_runs(runs_root: Path) -> None:
    total_issues = 0
    for run_dir in sorted(runs_root.iterdir()):
        cases_dir = run_dir / "cases"
        if not cases_dir.is_dir():
            continue
        gold: dict[str, Any] | None = None
        gold_path = run_dir / "eval_gold.json"
        if gold_path.is_file():
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
        print(f"\n=== run: {run_dir.name} ===")
        for result_path in sorted(cases_dir.rglob("result.json")):
            case_dir = result_path.parent
            audit = audit_one_case(case_dir, gold)
            total_issues += len(audit["issues"])
            loc = audit["localization"]
            loc_txt = (
                f"loc={loc['predicted_step']}/{loc['gold_step']}"
                + (" EXACT" if loc.get("exact") else f" offset={loc['offset']}")
                if loc else ""
            )
            print(
                f"  {audit['case_id']:<32} events={audit['events']:>3} "
                f"tools={audit['interactions']:>3} cand={audit['census_candidates']:>2} "
                f"tool_refs={audit['interaction_refs']:>2} "
                f"state={str(audit['state']):<15} {loc_txt}"
            )
            for issue in audit["issues"]:
                print(f"    ISSUE: {issue}")
    print(f"\ntotal issues across runs: {total_issues}")


def measure_cap(dataset: Path, limit: int = 12) -> None:
    files = sorted(dataset.rglob("*.json"))
    measured = 0
    for path in files:
        if measured >= limit:
            break
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            packet = build_packet(record)
        except Exception as exc:  # noqa: BLE001
            continue
        raw_chars = path.stat().st_size
        history = record.get("history") or []
        events_total = len(packet.get("events") or [])
        full_text, full_meta = render_full_view(packet)
        index_text, _index_meta = render_index_view(packet)
        print(
            f"{path.name:<24} raw={raw_chars:>9,}B  steps={len(history):>3}  "
            f"events={events_total:>3}  full_visible={len(full_meta['rendered_event_ids']):>3} "
            f"({len(full_meta['rendered_event_ids']) / max(1, events_total):>5.0%})  "
            f"full_truncated={full_meta['packet_truncated']}  "
            f"index_chars={len(index_text):>7,}"
        )
        measured += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit-runs", "measure-cap"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--dataset", type=Path, default=Path("datasets/raw/agenthallu/AgentHallu"))
    args = parser.parse_args()
    if args.mode == "audit-runs":
        audit_runs(args.runs_root)
    else:
        measure_cap(args.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
