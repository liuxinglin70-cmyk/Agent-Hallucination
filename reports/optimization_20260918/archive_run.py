"""Archive a terminal run and audit the requested invariants without rerunning it."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.evidence import validate_snapshot
from evolution.config import load_config
from evolution.gates import sentinel_coverage
from evolution.ledger import load_ledger

OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    config = load_config(OUT / "run_config.json")
    work = Path(config["work_root"])
    experiment = read(work / "reports/experiment.json")
    cycles = experiment["cycles"]
    assert cycles, "No completed cycle record"
    states = [c["state"] for c in cycles]
    assert states[-1] in {"completed", "blocked_evaluation_preflight"}, states
    if states[-1] == "completed":
        assert len(cycles) == 2, "Both authorized cycles must finish"
    archive = OUT / "run"
    archive.mkdir(exist_ok=False)
    for name in ("reports", "cycles", "checkpoints", "registry", "harness", "memory"):
        if (work / name).is_dir():
            shutil.copytree(work / name, archive / name)
    shutil.copyfile(work / "ledger.jsonl", archive / "ledger.jsonl")
    protocol = read(OUT / "protocol.json")
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest
               for p, digest in protocol["source_sha256"].items()), "Frozen input changed"
    original = load_config(ROOT / "evolution/configs/development.json")
    assert all(config[k] == original[k] for k in ("gates", "split", "budget"))
    coverage = read(OUT / "coverage_mini/retrieval.json")
    for directory in ("coverage_mini", "coverage_diagnostic"):
        for row in read(OUT / directory / "retrieval.json")["rows"]:
            if row["covered"]:
                validate_snapshot(read(OUT / directory / row["snapshot"]))
    ledger = load_ledger(config).verify()
    assert ledger["valid"]
    rows = [read(p)["row"] for p in (work / "checkpoints/cycle001/arms").rglob("*.json")]
    sentinel = set(cycles[0]["split"].get("sentinel", []))
    # A preflight stop has exactly one baseline arm; no proposal/control rerolls.
    baseline = [r for r in rows if r["case"] in sentinel]
    preflight_recomputed = sentinel_coverage(baseline, config) if len(baseline) == len(sentinel) else None
    compact = [{k: r.get(k) for k in ("case", "gold", "prediction", "state", "authority")}
               | {"responsible_step": r["result"].get("responsible_step"),
                  "stage_status": r["result"].get("stage_status"),
                  "usage_by_stage": {k: v.get("usage") for k, v in r["result"].get("providers", {}).items()},
                  "authority_failure_reasons": r["result"].get("authority_failure_reasons", [])}
               for r in baseline]
    audit = dict(states=states, frozen_inputs_unchanged=True, original_gates_split_budget=True,
                 snapshot_schema_valid=True, mini_coverage=dict(covered=coverage["covered"],total=coverage["total"]),
                 ledger=ledger, preflight=cycles[0].get("preflight"),
                 recomputed_sentinel=preflight_recomputed, baseline=compact,
                 completed_two_cycles=len(cycles)==2 and all(s=="completed" for s in states))
    (OUT / "completion_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"states": states, "ledger": ledger}, ensure_ascii=False))


if __name__ == "__main__":
    main()
