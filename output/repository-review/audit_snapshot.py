"""Read-only audit of stored R4-R6 evidence; no model calls or registry writes."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "reports/rsi_pilot_20260919"
CLEAN = {"BFCL/013.json", "BFCL/018.json", "BFCL/020.json", "BFCL/053.json"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    records = []
    identities = {}
    stages = defaultdict(lambda: {"good": 0, "total": 0})
    for rnd in ("round4", "round5", "round6"):
        identities[rnd] = read(PILOT / rnd / "v001/identity.json")
        for path in sorted((PILOT / rnd).rglob("results/*.json")):
            row = read(path)
            if "case_id" not in row:
                continue
            rel = path.relative_to(PILOT / rnd)
            arm = "h0" if "h0" in rel.parts else "candidate"
            expected = row["case_id"] not in CLEAN
            good = row.get("authority") == "authoritative" and row.get("prediction") is expected
            entry = {"round": rnd, "stage": rel.parts[0], "arm": arm,
                     "case_id": row["case_id"], "good": good,
                     "responsible_step": row.get("responsible_step"),
                     "authority": row.get("authority"),
                     "path": path.relative_to(ROOT).as_posix()}
            records.append(entry)
            count = stages[f"{rnd}/{rel.parts[0]}/{arm}"]
            count["good"] += int(good)
            count["total"] += 1

    def aggregate(rows):
        result = {}
        for arm in ("h0", "candidate"):
            selected = [r for r in rows if r["arm"] == arm]
            result[arm] = {"good": sum(r["good"] for r in selected), "total": len(selected)}
        return result

    paired = [r for r in records if r["stage"] != "v001"]
    integrity = read(ROOT / "datasets/integrity.json")
    base = ROOT / integrity["root"]
    actual = {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in base.rglob("*") if p.is_file()}
    result = {
        "scope": "Stored R4-R6 results directories. Baseline screening directories excluded; screening counted separately. Good means authoritative and correct binary label, not step alignment.",
        "identities": identities,
        "stage_counts": dict(sorted(stages.items())),
        "paired_stage_totals": aggregate(paired),
        "including_all_candidate_screening": aggregate(records),
        "excluding_r6_screening": aggregate([r for r in records if not (r["round"] == "round6" and r["stage"] == "v001")]),
        "case_counts_paired": {cid: aggregate([r for r in paired if r["case_id"] == cid]) for cid in sorted({r["case_id"] for r in records})},
        "dataset_integrity": {"matches": actual == integrity["files"], "files": len(actual), "cases": integrity["case_count"]},
        "records": records,
    }
    destination = Path(__file__).with_name("audit_snapshot.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("records", "identities", "case_counts_paired")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
