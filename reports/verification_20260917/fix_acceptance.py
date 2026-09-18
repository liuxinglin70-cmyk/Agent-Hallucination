"""Accept evaluator fixes against saved real observations, without model calls."""
import hashlib
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.interventions import validate_intervention
from evolution.backends import DetectionBackend
from evolution.config import load_config
from evolution.engine import EvolutionEngine, terminal_classification
from evolution.gates import harmlessness_gate, structural_gate
from evolution.splits import EvolutionSplit
from evolution.memory import record_episode_correction, memory_summary


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def historical_hashes():
    paths = list((ROOT / "artifacts/evolution_real").rglob("*"))
    paths += list((ROOT / "artifacts/evolution/memory").glob("*.jsonl"))
    paths += [ROOT / "reports/verification_20260917/diagnostic_recheck.json"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--correct-memory", action="store_true",
                        help="append reviewed corrections; preserve original episodes and ledger")
    args = parser.parse_args()
    before = historical_hashes()
    output = {"scope": "offline evaluator acceptance; no admission or model rerun", "cycles": []}
    baseline_by_cycle = {}
    for number in (1, 2):
        report = read(ROOT / f"artifacts/evolution_real/cycles/cycle{number:03}.json")
        baseline = {}
        for path in (ROOT / f"artifacts/evolution_real/checkpoints/cycle{number:03}/arms").glob("*/*.json"):
            row = read(path)["row"]
            if not row["result"]["proof"]["deployment"].get("active_interventions"):
                baseline[row["case"]] = row
        baseline_by_cycle[number] = baseline
        result = {"cycle": number, "structural_replay": [], "harmlessness_reclassification": []}
        for old in report["gates"]:
            candidate = old["candidate"]
            if not old["structural"]["passed"]:
                verdict = structural_gate(candidate, active_entries=[], config=load_config(),
                    diagnosed_families=report["patrol"]["stable_failure_families"],
                    dataset_root=str(ROOT / "datasets/raw/agenthallu/AgentHallu"),
                    patrol_rows=list(baseline.values()))
                assert verdict["passed"], verdict
                result["structural_replay"].append({"id": candidate["intervention_id"],
                    "previous_passed": False, "fixed_passed": True,
                    "interpretation": "old rejection invalidated; efficacy remains unmeasured"})
            else:
                sentinel = report["split"]["sentinel"]
                verdict = harmlessness_gate(
                    baseline_rows=[baseline[c] for c in sentinel],
                    candidate_rows=[r for r in old["candidate_rows"] if r["case"] in sentinel],
                    config=load_config())
                classification = terminal_classification({"candidate": candidate,
                    "harmlessness": verdict}, admitted=False)
                expected = "rejected" if candidate["intervention_id"] in {"IV-1-003", "IV-1-004"} else "not_evaluated"
                assert classification == expected, (candidate["intervention_id"], verdict)
                result["harmlessness_reclassification"].append({"id": candidate["intervention_id"],
                    "partner_ids": old.get("partner_ids", []), "outcome": classification,
                    "reason": verdict["reason"], "new_fn": verdict["delta"]["new_fn"]})
        output["cycles"].append(result)

    second = read(ROOT / "artifacts/evolution_real/cycles/cycle002.json")
    invalid = second["gates"][0]["candidate"]
    try:
        validate_intervention(invalid, require_executable_bounds=True)
    except ValueError as exc:
        assert "context fields unavailable at defense.pre" in str(exc)
        output["hook_contract"] = str(exc)
    else:
        raise AssertionError("defense.pre illegally accepted judge-only fields")

    first = read(ROOT / "artifacts/evolution_real/cycles/cycle001.json")
    baseline = baseline_by_cycle[1]
    seen = []

    class SavedOnlyBackend(DetectionBackend):
        name = "saved_real_observations"

        def evaluate(self, cases, **kwargs):
            assert not (kwargs.get("registry") or {}).get("interventions")
            assert set(cases) <= set(first["split"]["sentinel"])
            seen.extend(cases)
            return [baseline[c] for c in cases]

        evaluate_fresh = evaluate

    class NoProposer:
        name = "forbidden"

        def propose(self, *args, **kwargs):
            raise AssertionError("preflight failed to stop proposer")

    with tempfile.TemporaryDirectory(prefix="evaluator-acceptance-") as directory:
        config = load_config()
        config["dataset_root"] = str(ROOT / "datasets/raw/agenthallu/AgentHallu")
        for key, suffix in {"work_root": "work", "registry_dir": "registry",
                            "ledger_path": "ledger.jsonl", "harness_dir": "harness",
                            "memory_root": "memory"}.items():
            config[key] = str(Path(directory) / suffix)
        split = EvolutionSplit(name="saved_real", pool=sorted(baseline),
            sentinel=first["split"]["sentinel"], gain=first["split"]["gain"],
            patrol_pool=sorted(set(baseline) - set(first["split"]["sentinel"]) - set(first["split"]["gain"])),
            mini=True)
        engine = EvolutionEngine(config, backend=SavedOnlyBackend(), split=split,
                                 proposer=NoProposer(), mini=True)
        experiment = engine.run_experiment(cycles=2)
        assert len(experiment["cycles"]) == 1
        stop = experiment["cycles"][0]
        assert stop["state"] == "blocked_evaluation_preflight"
        assert stop["preflight"]["sentinel"]["protected_tp"] == 2
        assert stop["preflight"]["sentinel"]["protected_tn"] == 1
        assert engine.ledger.verify()["valid"]
        assert not engine.registry.entries
        output["saved_run_preflight"] = {"state": stop["state"], "preflight": stop["preflight"],
            "observations_read": len(seen), "proposer_calls": 0,
            "candidate_arms": 0, "cycles_started": 1, "requested_cycles": 2}
    assert before == historical_hashes(), "historical artifacts changed during acceptance"
    output["historical_files_verified_unchanged"] = len(before)
    if args.correct_memory:
        memory_root = ROOT / "artifacts/evolution/memory"
        raw_episodes = [json.loads(line) for line in (memory_root / "episodic.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
        corrections = []
        for cycle in output["cycles"]:
            source = ROOT / f"artifacts/evolution_real/cycles/cycle{cycle['cycle']:03}.json"
            source_report = read(source)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            for item in cycle["structural_replay"] + cycle["harmlessness_reclassification"]:
                old = next(g for g in source_report["gates"] if g["intervention_id"] == item["id"])
                gate = "STRUCTURAL" if not old["structural"]["passed"] else "HARMLESSNESS"
                originals = [episode for episode in raw_episodes
                    if episode["cycle"] == cycle["cycle"] and episode["intervention_id"] == item["id"]
                    and episode["failure_family"] == old["candidate"]["provenance"]["failure_family"]
                    and episode["gate"] == gate
                    and episode["reason"] == old[gate.lower()]["reason"]]
                assert len(originals) == 1, (item["id"], "ambiguous or absent source episode")
                reason = item.get("reason") or (
                    "offline review: original STRUCTURAL context omitted judge fields; "
                    "saved-context replay passes; efficacy remains unmeasured")
                if item.get("partner_ids") and item.get("outcome") == "rejected":
                    reason = "joint unit only (no solo attribution): " + reason
                corrections.append(record_episode_correction(memory_root, originals[0],
                    unevaluable=item.get("outcome") != "rejected", reason=reason,
                    evidence=f"{source.relative_to(ROOT)} sha256={source_hash}; fix_acceptance.py offline replay"))
        after = historical_hashes()
        assert all(after.get(path) == digest for path, digest in before.items())
        output["memory_corrections"] = {"records": len(corrections),
            "summary": memory_summary(memory_root), "original_files_unchanged": True}
    path = Path(__file__).with_name("fix_acceptance.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
    print("3 structural replays passed; harmlessness: 3 unmeasured, 2 harmful joint-member reports")
    print("saved-real preflight: 2 TP + 1 TN, stopped before proposer, no second cycle")
    print(f"Historical files unchanged: {len(before)}")


if __name__ == "__main__":
    main()
