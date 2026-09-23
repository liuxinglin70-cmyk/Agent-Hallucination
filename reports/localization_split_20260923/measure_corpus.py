"""Full-corpus read-out for the localization split and the conflict rule.

Reads the archived real-run records only. Rebuilds each archived conviction's
hearing queue exactly the way detector/api.py builds it, replays it through the
pure adjudicator, and reports:

  * how many convictions the corpus holds, and how many of those rows blame an
    action step vs a content step (structural rule, detector/packet.step_kind);
  * the per-kind hit rate against the gold hallucination_step, with the overall
    rate beside it for comparison;
  * every row whose adopted step contradicts the step the row mirrors in its
    tracer slot -- the state the audit found reading as authoritative.

No provider is called; every input is read from disk.
"""
from __future__ import annotations

import collections
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from detector.adjudication import (  # noqa: E402
    LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT,
    adjudicate,
)
from detector.api import _validate_frozen_stages  # noqa: E402
from detector.packet import build_packet, step_kind  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
ARCHIVE = "reports/rsi_pilot_20260919/**/results/*.json"
POSITIVE_KEYS = ("source", "step", "reason", "evidence_refs", "quote",
                 "delivered_result", "correct_result")


def replay(envelope):
    case = str(envelope["case_id"])
    raw = json.loads((DATASET_ROOT / case).read_text(encoding="utf-8-sig"))
    packet = build_packet({"question": raw["question"], "history": raw["history"]})
    proof, status = envelope["proof"], envelope.get("stage_status") or {}
    archived = [h for h in proof.get("hearings") or [] if isinstance(h, dict)]
    norm = _validate_frozen_stages({
        "judge_result": {"ok": True, "parsed": proof["judge"]},
        "judge_status": status.get("judge") or {"state": "ok"},
        "hearings": [{
            "source": h.get("source"), "candidate_id": h.get("candidate_id"),
            "positive": dict(h.get("positive") or {}),
            "tracer_result": h.get("tracer_result"),
            "tracer_status": h.get("tracer_status"),
            "defense_result": h.get("defense_result"),
            "defense_status": h.get("defense_status"),
        } for h in archived],
    }, packet, None)
    hearings = []
    for row in norm["hearings"]:
        positive = dict(row.get("positive") or {})
        hearings.append({
            **{key: positive.get(key) for key in POSITIVE_KEYS},
            "source": row.get("source"), "candidate_id": row.get("candidate_id"),
            "tracer_result": row.get("tracer_result"),
            "tracer_status": row.get("tracer_status"),
            "defense_result": row.get("defense_result"),
            "defense_status": row.get("defense_status"),
        })
    upgrades = list(proof.get("causal_upgrades") or [])
    if not upgrades and proof.get("causal_upgrade"):
        upgrades = [proof["causal_upgrade"]]
    first = hearings[0] if hearings else {}
    replayed = adjudicate(
        packet=packet,
        judge_result=norm["judge_result"], judge_status=norm["judge_status"],
        recall_result={"ok": True, "candidates": list(proof.get("recall_candidates") or [])},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result={"ok": True, "upgrades": list(proof.get("causal_upgrade_rows") or []),
                        "upgraded_candidate": proof.get("causal_upgrade"),
                        "upgraded_candidates": upgrades},
        upgrade_status={"stage": "causal_upgrade", "state": "ok"},
        tracer_result=first.get("tracer_result"), tracer_status=first.get("tracer_status"),
        defense_result=first.get("defense_result"), defense_status=first.get("defense_status"),
        hearings=hearings,
    )
    return replayed, packet, raw


def main():
    totals = collections.Counter()
    hits = collections.Counter()
    gold_kinds = collections.Counter()
    conflicts = []
    authority_changed = 0
    step_changed = []
    rows = []
    for path in sorted(glob.glob(str(ROOT / ARCHIVE), recursive=True)):
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        envelope = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        if envelope.get("state") != "verified_error":
            continue
        replayed, packet, raw = replay(envelope)
        assert replayed["state"] == "verified_error", path
        if replayed["decision_authority"] != envelope["decision_authority"]:
            authority_changed += 1
        if replayed["responsible_step"] != envelope["responsible_step"]:
            step_changed.append((str(envelope["case_id"]),
                                 envelope["responsible_step"],
                                 replayed["responsible_step"]))
        step = replayed["responsible_step"]
        gold = raw.get("hallucination_step")
        gold_step = int(gold) if str(gold).isdigit() else None
        kind = step_kind(packet, step)
        totals[kind] += 1
        if gold_step is not None:
            hits[kind] += int(step == gold_step)
        gold_kinds[step_kind(packet, gold_step) if gold_step else None] += 1
        mirrored = ((envelope.get("proof") or {}).get("step_tracer") or {}).get("step")
        if isinstance(mirrored, int) and mirrored != step:
            conflicts.append({
                "case": str(envelope["case_id"]),
                "mirrored_tracer_step": mirrored,
                "adopted_step": step,
                "kind": kind,
                "gold_step": gold_step,
                "hit": gold_step is not None and step == gold_step,
                "authority_before": envelope["decision_authority"],
                "authority_after": replayed["decision_authority"],
                "localization_authority_after": replayed["localization_authority"],
                "disagreements": (replayed.get("localization_conflict") or {}).get("disagreements"),
            })
        rows.append({
            "case": str(envelope["case_id"]), "step": step, "kind": kind,
            "gold_step": gold_step, "hit": gold_step is not None and step == gold_step,
            "localization_authority": replayed["localization_authority"],
        })
    total = sum(totals.values())
    report = {
        "corpus": "reports/rsi_pilot_20260919/**/results/*.json",
        "convictions": total,
        "kind_totals": {str(k): v for k, v in totals.items()},
        "kind_hits": {str(k): v for k, v in hits.items()},
        "kind_accuracy": {
            str(k): (hits[k] / totals[k]) if totals[k] else None for k in totals
        },
        "overall_hits": sum(hits.values()),
        "overall_accuracy": (sum(hits.values()) / total) if total else None,
        "gold_step_kinds": {str(k): v for k, v in gold_kinds.items()},
        "conflict_rows": conflicts,
        "conflict_count": len(conflicts),
        "authoritative_rows_after": sum(
            1 for r in rows
            if r["localization_authority"] != LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT
        ),
        "decision_authority_changed_by_fix": authority_changed,
        "adopted_step_changed_by_fix": step_changed,
    }
    out = ROOT / "reports" / "localization_split_20260923" / "corpus_readings.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
