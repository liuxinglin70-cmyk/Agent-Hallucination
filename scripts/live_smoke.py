"""Two synthetic cases through real batch/evaluate CLIs; bounded live API use.

This is an integration probe, never a benchmark or a generalization claim.
Credentials are read by the detector and are not written to the report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.config import load_config
from detector.runner import safe_case_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "detector/configs/autodl.json"))
    args = parser.parse_args()
    timestamp = datetime.now(timezone.utc)
    run_id = timestamp.strftime("live_smoke_%Y%m%dT%H%M%S%fZ")
    root = ROOT / "artifacts" / "integration" / run_id
    root.mkdir(parents=True)
    output = ROOT / "reports" / "live"
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "passed": False,
              "started_at_utc": timestamp.isoformat(),
              "scope": "Two synthetic arithmetic cases; integration check only, not detector accuracy evaluation.",
              "run_dir": root.relative_to(ROOT).as_posix()}

    def write_report() -> None:
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_report()
    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        report.update(status="blocked", reason=str(exc))
        write_report()
        print("Live configuration/credentials unavailable; see reports/live/summary.json")
        return 2
    # Preserve configured models and vote counts; bound transport time and retries.
    config["case_timeout_seconds"] = 180
    config.pop("intervention_registry", None)
    for name, route in config.items():
        if name.endswith("_route"):
            route.update(timeout_seconds=45, total_timeout_seconds=45,
                         transport_retries=0, vote_topup=0)
    report["routes"] = {name: {k: route.get(k) for k in
                          ("model", "endpoint", "votes", "timeout_seconds", "transport_retries")}
                        for name, route in config.items() if name.endswith("_route")}
    report["limits"] = {"cases": 2, "workers": 2, "case_timeout_seconds": 180,
                        "request_timeout_seconds": 45, "transport_retries": 0, "vote_topup": 0}
    config_path = root / "detector.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    report["resolved_config_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    source_files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((ROOT / "detector").glob("*.py"))}
    report["detector_source_files"] = source_files
    report["probe_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    data = root / "data" / "synthetic"
    data.mkdir(parents=True)
    cases = []
    for name, answer, gold in (("clean", "4", False), ("error", "5", True)):
        case = f"synthetic/{name}.json"
        cases.append(case)
        (data / f"{name}.json").write_text(json.dumps({
            "question": "What is 2 + 2? Give the numerical result.",
            "history": [{"step": 1, "role": "assistant", "content": answer}],
            "is_hallucination": gold, "hallucination_step": 1 if gold else None,
            "true_answer": "4",
        }), encoding="utf-8")
    cases_path = root / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    common = ["--dataset-root", str(data.parent), "--cases-file", str(cases_path),
              "--runs-root", str(root / "runs"), "--run-id", "probe"]
    report["commands"] = []
    write_report()
    for name, arguments in (
        ("batch", ["-m", "detector", "batch", *common, "--config", str(config_path), "--workers", "2"]),
        ("evaluate", ["-m", "detector", "evaluate", *common]),
    ):
        try:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
            (output / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            report["commands"].append({"name": name, "exit_code": result.returncode})
            print(f"live {name}: exit={result.returncode}", flush=True)
            if result.returncode:
                report.update(status="failed", reason=f"{name} command failed")
                write_report()
                return 2
        except subprocess.TimeoutExpired:
            report.update(status="failed", reason=f"{name} exceeded 240-second wall-clock limit")
            write_report()
            return 2
    evaluation_path = root / "runs/probe/evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    (output / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    for case in cases:
        result_path = root / "runs/probe/cases" / safe_case_id(case) / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        results.append({"case": case, "state": result.get("state"),
                        "decision_authority": result.get("decision_authority"),
                        "responsible_step": result.get("responsible_step"),
                        "stage_status": result.get("stage_status"),
                        "expected_state": "verified_clean" if "clean" in case else "verified_error"})
    passed = all(row["state"] == row["expected_state"] and row["decision_authority"] == "authoritative" for row in results)
    report.update(status="completed", passed=passed, results=results,
                  finished_at_utc=datetime.now(timezone.utc).isoformat())
    report["source_unchanged"] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest
                                    for p, digest in source_files.items())
    report["passed"] = passed and report["source_unchanged"]
    write_report()
    print(json.dumps({"passed": report["passed"], "results": [{k: r[k] for k in ("case", "state", "decision_authority")} for r in results]}), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
