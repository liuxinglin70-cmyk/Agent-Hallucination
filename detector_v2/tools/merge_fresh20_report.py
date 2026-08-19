import json
from pathlib import Path
from detector_v2.evaluation import binary_metrics
from detector_v2.dataset import load_case
from detector_v2.result import prediction_state
from detector_v2.runner import safe_case_id

ROOT = Path("datasets/raw/agenthallu/AgentHallu")
RUNS = Path("runs_v2")

MANIFESTS = {
    "v4_qg_fresh10_a1": "detector_v2/experiments/v2_fresh10_cases.json",
    "v4_qg_fresh10_a2": "detector_v2/experiments/v2_fresh10_cases.json",
    "v4_qg_fresh10b_a1": "detector_v2/experiments/v2_fresh10b_cases.json",
    "v4_qg_fresh10b_a2": "detector_v2/experiments/v2_fresh10b_cases.json",
}

def rows_for(rid, cases):
    out = []
    for case_id in cases:
        result_path = RUNS / rid / "cases" / safe_case_id(case_id) / "result.json"
        _rec, gold = load_case(ROOT, case_id)
        raw = json.loads((ROOT / case_id).read_text(encoding="utf-8-sig"))
        gs = raw.get("hallucination_step")
        gs = int(gs) if str(gs or "").isdigit() else None
        lab = gold.get("is_hallucination")
        if isinstance(lab, bool):
            gold_bool = lab
        elif isinstance(lab, str) and lab.strip().lower() in {"true", "false"}:
            gold_bool = lab.strip().lower() == "true"
        else:
            raise ValueError(f"bad label {lab!r} for {case_id}")
        if not result_path.exists():
            out.append({"case": case_id, "gold": gold_bool, "prediction": None,
                        "gold_step": gs, "responsible_step": None,
                        "state": "missing_result", "authority": "invalid"})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        out.append({"case": case_id, "gold": gold_bool, "prediction": prediction_state(result),
                    "gold_step": gs, "responsible_step": result.get("responsible_step"),
                    "state": result.get("state"), "authority": result.get("decision_authority")})
    return out

sets = {}
for rid, mf in MANIFESTS.items():
    cases = json.loads(Path(mf).read_text(encoding="utf-8-sig"))
    sets[rid] = rows_for(rid, cases)

a1 = sets["v4_qg_fresh10_a1"] + sets["v4_qg_fresh10b_a1"]
a2 = sets["v4_qg_fresh10_a2"] + sets["v4_qg_fresh10b_a2"]

for name, rows in [("fresh10_a1", sets["v4_qg_fresh10_a1"]),
                   ("fresh10_a2", sets["v4_qg_fresh10_a2"]),
                   ("fresh10b_a1", sets["v4_qg_fresh10b_a1"]),
                   ("fresh10b_a2", sets["v4_qg_fresh10b_a2"]),
                   ("merged20_a1", a1), ("merged20_a2", a2)]:
    m = binary_metrics(rows)
    h = t = 0
    for r in rows:
        if r["gold"] and r["gold_step"] is not None:
            t += 1
            if r["prediction"] is True and r["responsible_step"] == r["gold_step"]:
                h += 1
    print(name, "decisive=%d/%d acc=%.3f allcase=%.3f TP%d FP%d FN%d TN%d "
          "P=%.3f R=%.3f F1=%.3f step=%d/%d" % (
              m["decisive"], len(rows), m["decisive_accuracy"], m["all_case_accuracy"],
              m["tp"], m["fp"], m["fn"], m["tn"],
              m["positive_precision"], m["positive_recall"], m["positive_f1"], h, t))

agree = 0
dis = []
for r1, r2 in zip(a1, a2):
    if r1["prediction"] == r2["prediction"]:
        agree += 1
    else:
        dis.append((r1["case"], r1["prediction"], r2["prediction"]))
print("merged20 a1 vs a2 consistency:", agree, "/ 20")
for d in dis:
    print("  DISAGREE", d)

# save merged report
out = RUNS / "v4_qg_fresh20_merged" / "evaluation.json"
out.parent.mkdir(parents=True, exist_ok=True)
merged = {
    "run_ids": ["v4_qg_fresh10_a1", "v4_qg_fresh10b_a1",
                "v4_qg_fresh10_a2", "v4_qg_fresh10b_a2"],
    "metrics_a1": binary_metrics(a1),
    "metrics_a2": binary_metrics(a2),
    "consistency_20": agree,
    "disagreements": [{"case": c, "a1": p1, "a2": p2} for c, p1, p2 in dis],
}
out.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("saved ->", out)