"""Offline diagnosis from saved run artifacts; no model calls or gate changes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.gates import _family_contexts, _run_program_on_family


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    output = {"method": "offline saved-arm comparison and predicate replay", "cycles": []}
    dataset = str(ROOT / "datasets/raw/agenthallu/AgentHallu")
    for number in (1, 2):
        report = read(ROOT / f"artifacts/evolution_real/cycles/cycle{number:03}.json")
        baseline = {}
        deployed = {}
        for path in sorted((ROOT / f"artifacts/evolution_real/checkpoints/cycle{number:03}/arms").glob("*/*.json")):
            row = read(path)["row"]
            deployment = row.get("result", {}).get("proof", {}).get("deployment", {})
            ids = tuple(sorted(deployment.get("active_interventions") or []))
            if not ids:
                baseline[row["case"]] = row
            else:
                deployed.setdefault(ids, []).append(row)
        cycle = {"cycle": number, "instrument": report["instrument"], "structural_replay": [], "units": []}
        seen = set()
        for gate in report["gates"]:
            candidate = gate["candidate"]
            if not gate["structural"]["passed"]:
                cases = candidate["provenance"]["failure_cases"]
                contexts = _family_contexts(cases, list(baseline.values()))
                program = candidate["body"]["predicate_definition"]
                original = _run_program_on_family(program, cases, dataset, contexts)
                for case in cases:
                    contexts[case]["judge_parsed"] = baseline[case]["result"]["proof"]["judge"]
                restored = _run_program_on_family(program, cases, dataset, contexts)
                cycle["structural_replay"].append({
                    "id": candidate["intervention_id"], "cases": cases,
                    "original": original, "with_saved_judge_context": restored,
                    "interpretation": "predicate applicability only; not admission or measured benefit",
                })
                continue
            ids = tuple(sorted([candidate["intervention_id"], *gate.get("partner_ids", [])]))
            if ids in seen:
                continue
            seen.add(ids)
            changes = []
            for row in gate["candidate_rows"]:
                base = baseline[row["case"]]
                if base["prediction"] != row["prediction"]:
                    changes.append({
                        "case": row["case"], "gold": row["gold"],
                        "baseline": base["prediction"], "candidate": row["prediction"],
                        "kind": "correction" if row["prediction"] is row["gold"] else "regression",
                        "partition": "S" if row["case"] in report["split"]["sentinel"] else "G",
                    })
            events = []
            for row in deployed.get(ids, []):
                proof = row["result"]["proof"]
                for event in proof.get("deployment", {}).get("fired", []):
                    events.append({"case": row["case"], "event": event.get("event"),
                                   "recall_candidates": len(proof.get("recall_candidates") or [])})
            cycle["units"].append({"ids": ids, "n": len(gate["candidate_rows"]),
                                   "changes": changes, "events": events})
        output["cycles"].append(cycle)
    evaluation = read(ROOT / "artifacts/detector_dev40/dev40/evaluation.json")
    rows = evaluation["rows"]
    positives = [r for r in rows if r["gold"] is True]
    tp = [r for r in positives if r["prediction"] is True]
    hits = sum(r.get("gold_step") == r.get("responsible_step") for r in positives)
    output["detector"] = {
        "metrics": evaluation["metrics"], "localization_hits": hits,
        "all_gold_positive_n": len(positives), "detected_positive_n": len(tp),
        "conditional_localization": sum(r.get("gold_step") == r.get("responsible_step") for r in tp) / len(tp),
        "fn_cases": [r["case"] for r in positives if r["prediction"] is False],
    }
    destination = Path(__file__).with_name("diagnostic_recheck.json")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)
    for cycle in output["cycles"]:
        print("cycle", cycle["cycle"], "structural context replays", len(cycle["structural_replay"]))
        for unit in cycle["units"]:
            print(unit["ids"], "corrections", sum(x["kind"] == "correction" for x in unit["changes"]),
                  "regressions", sum(x["kind"] == "regression" for x in unit["changes"]))


if __name__ == "__main__":
    main()
