import json
from pathlib import Path

RUNS = Path("runs_v2")

def safe(cid):
    return cid.replace("/", "__")

def inspect(case, rid):
    p = RUNS / rid / "cases" / safe(case) / "result.json"
    r = json.loads(p.read_text(encoding="utf-8"))
    print(f"  [{rid}] state={r['state']} auth={r['decision_authority']}")
    ss = r.get("stage_status", {})
    print("    stages:", {k: (v or {}).get("status") for k, v in ss.items()})
    fr = r.get("proof", {}).get("authority_failure_reasons") or []
    print("    failures:", json.dumps(fr, ensure_ascii=False)[:500])
    pr = r.get("providers", {})
    for k, v in pr.items():
        if v:
            print(f"    provider {k}: attempts={v.get('attempts')} vote={json.dumps(v.get('vote'), ensure_ascii=False)} status={v.get('status')}")

for case, runs in [
    ("Camel/037.json", ["v4_qg_fresh10b_a1", "v4_qg_fresh10b_a2"]),
    ("Magentic_One/039.json", ["v4_qg_fresh10b_a1", "v4_qg_fresh10b_a2"]),
    ("OpenManus/067.json", ["v4_qg_fresh10b_a1", "v4_qg_fresh10b_a2"]),
    ("OpenDeepSearch/053.json", ["v4_qg_fresh10b_a1", "v4_qg_fresh10b_a2"]),
    ("Camel/050.json", ["v4_qg_fresh10_a1", "v4_qg_fresh10_a2"]),
    ("Octotools/015.json", ["v4_qg_fresh10_a1", "v4_qg_fresh10_a2"]),
]:
    print("=====", case)
    for rid in runs:
        inspect(case, rid)