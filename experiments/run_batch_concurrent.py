#!/usr/bin/env python3
"""Concurrent ten-by-one gold self-evolution batch harness.

Goal: verify the experiment framework end-to-end by running ten independent
single-case evolution processes in parallel, each from one raw trajectory
with gold labels enabled, then collecting per-run summaries.

Each batch reads its manifest (produced by ``select_batch_cases.py``) so that
every batch consumes a distinct, stratified case set.

Usage (from repo root):

    python experiments/select_batch_cases.py --batch 1 --size 10
    python experiments/run_batch_concurrent.py --batch 1

Artifacts (all under experiments/results/batch<B>_gold/):

    batch_manifest.json    stratified case selection for this batch
    records/<run>.json     single-record input per run
    <run>/run.log          child process stdout+stderr
    <run>/evolution_result.json  framework audit trail (per child)
    batch_summary.json     aggregated summary
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
CONFIG = ROOT / "src" / "sehd_v6" / "configs" / "paca_tcl_detector_v3_5.json"

_PARSER = argparse.ArgumentParser(description=__doc__)
_PARSER.add_argument("--batch", type=int, required=True)
_PARSER.add_argument(
    "--summarize-only",
    action="store_true",
    help="rebuild and validate batch_summary.json from existing run artifacts",
)
_ARGS = _PARSER.parse_args()
BATCH = _ARGS.batch
RESULTS_ROOT = ROOT / "experiments" / "results" / f"batch{BATCH}_gold"


def load_manifest() -> list[dict]:
    manifest_path = RESULTS_ROOT / "batch_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"missing manifest {manifest_path}; run "
            f"python experiments/select_batch_cases.py --batch {BATCH} first"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest.get("cases") or manifest


def build_records(cases: list[dict]) -> dict[str, Path]:
    records_dir = RESULTS_ROOT / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for case in cases:
        family, number = case["case_id"].split("/", 1)
        source = DATASET_ROOT / family / f"{number}.json"
        raw = json.loads(source.read_text(encoding="utf-8"))
        # Keep gold fields: evaluator-only fields must survive to the runner.
        payload = {case["case_id"]: raw}
        run_name = f"{family}_{number}".replace("/", "_").lower()
        path = records_dir / f"{run_name}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        paths[run_name] = path
    return paths


def launch(run_name: str, records_path: Path) -> tuple[subprocess.Popen, Path]:
    output_dir = RESULTS_ROOT / run_name
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    log_handle = log_path.open("w", encoding="utf-8")
    cmd = [
        sys.executable, "-u", "-m", "src.sehd_v6.evolution",
        "--records", str(records_path),
        "--config", str(CONFIG),
        "--output-dir", str(output_dir),
        "--run-id", f"batch{BATCH}_{run_name}",
        "--use-gold", "--fitness-use-gold",
        "--reflection-candidates", "1",
    ]
    env = dict(os.environ)
    env.setdefault(
        "PACA_GLOBAL_CACHE_ROOT",
        str((ROOT / "experiments" / "cache" / "provider").resolve()),
    )
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                "https_proxy", "all_proxy"):
        env.pop(key, None)
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT, env=env,
    )
    return proc, log_path


def refuse_overwrite(cases: list[dict]) -> bool:
    """True when any run output already exists; refuse rather than clobber."""
    for case in cases:
        family, number = case["case_id"].split("/", 1)
        run_name = f"{family}_{number}".replace("/", "_").lower()
        result = RESULTS_ROOT / run_name / "evolution_result.json"
        if result.is_file():
            print(f"[harness] existing result found: {result} — rerun would "
                  f"overwrite it; move it away or delete first", flush=True)
            return True
    return False


def summarize(output_dir: Path) -> dict:
    result_path = output_dir / "evolution_result.json"
    if not result_path.is_file():
        return {"status": "missing_result"}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    audit = result.get("evolution_audit", {})
    l2_audit = audit.get("l2_policy_audit")
    steps = result.get("steps", [])
    step_details = []
    for step in steps:
        episode_attribution = (
            (step.get("episode") or {}).get("attribution") or {}
        )
        proposal = step.get("edit_proposal") or {}
        mutation = step.get("mutation_applied") or {}
        edit = proposal.get("edit") or mutation.get("edit") or {}
        fitness = step.get("fitness_check") or {}
        assessment = fitness.get("assessment") or {}
        step_details.append({
            "case_id": step.get("case_id"),
            "attempt_index": step.get("attempt_index"),
            "detection_state": step.get("detection_state"),
            "trigger": step.get("trigger_source"),
            "decision": step.get("gate_decision"),
            "module": (
                proposal.get("target_module")
                or mutation.get("target_module")
                or edit.get("target_module")
            ),
            "operation": (
                proposal.get("operation")
                or mutation.get("operation")
                or edit.get("operation")
            ),
            "fitness": (
                assessment.get("decision")
                or fitness.get("fitness_class")
                or fitness.get("fitness_decision")
            ),
            "fitness_reasons": (
                assessment.get("reasons")
                or fitness.get("reasons")
                or fitness.get("reason_codes")
            ),
            "attribute_layer": episode_attribution.get("layer"),
            "attribute_artifact": episode_attribution.get("artifact"),
            "attribute_reason": episode_attribution.get("reason_code"),
        })
    return {
        "status": "done",
        "duration_seconds": result.get("duration_seconds"),
        "total_cases": result.get("total_cases"),
        "triggered_cases": result.get("triggered_cases"),
        "edits_accepted": result.get("edits_accepted"),
        "edits_rejected": result.get("edits_rejected"),
        "stable_cases": result.get("stable_cases"),
        "halted": result.get("halted"),
        "halt_reason": result.get("halt_reason"),
        "runtime_invalid_cases": result.get("runtime_invalid_cases"),
        "policy_edits": len(l2_audit) if isinstance(l2_audit, list) else None,
        "steps": step_details,
        "supervision_mode": result.get("supervision_mode"),
    }


def main() -> int:
    started = time.time()
    cases = load_manifest()
    print(f"[harness] batch {BATCH}: {len(cases)} cases", flush=True)
    for case in cases:
        print(f"  {case['case_id']}: gold={case['gold']} "
              f"category={case.get('category') or '-'}", flush=True)
    if _ARGS.summarize_only:
        summaries = {}
        for case in cases:
            family, number = case["case_id"].split("/", 1)
            run_name = f"{family}_{number}".replace("/", "_").lower()
            summaries[run_name] = summarize(RESULTS_ROOT / run_name)
        report_path = RESULTS_ROOT / "batch_summary.json"
        report_path.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
        failed = [
            name for name, summary in summaries.items()
            if summary.get("status") != "done"
            or int(summary.get("runtime_invalid_cases") or 0) > 0
        ]
        if failed:
            print(f"[harness] batch gate failed: {failed}", flush=True)
            return 1
        print("[harness] batch gate passed", flush=True)
        return 0
    if refuse_overwrite(cases):
        return 2
    records_paths = build_records(cases)
    print(f"[harness] built {len(records_paths)} single-record inputs under "
          f"{RESULTS_ROOT / 'records'}", flush=True)
    print(f"[harness] launching {len(cases)} evolution processes concurrently",
          flush=True)

    procs: dict[str, tuple[subprocess.Popen, Path]] = {
        run_name: launch(run_name, path)
        for run_name, path in records_paths.items()
    }
    for run_name, (proc, _) in procs.items():
        print(f"[harness] {run_name}: pid={proc.pid}", flush=True)

    while True:
        running = [name for name, (proc, _) in procs.items() if proc.poll() is None]
        if not running:
            break
        print(f"[harness] running={len(running)} {sorted(running)} "
              f"elapsed={int(time.time() - started)}s", flush=True)
        time.sleep(30)

    print(f"[harness] all processes exited in {int(time.time() - started)}s",
          flush=True)
    for run_name, (proc, log_path) in procs.items():
        print(f"[harness] {run_name}: exit={proc.returncode} log={log_path}",
              flush=True)

    summary = {
        run_name: summarize(RESULTS_ROOT / run_name)
        for run_name in records_paths
    }
    report_path = RESULTS_ROOT / "batch_summary.json"
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    failed = [
        name for name, (proc, _) in procs.items()
        if proc.returncode != 0
        or summary[name].get("status") != "done"
        or int(summary[name].get("runtime_invalid_cases") or 0) > 0
    ]
    if failed:
        print(f"[harness] batch gate failed: {failed}", flush=True)
        return 1
    print("[harness] batch gate passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
