"""Freeze the same 95 cases, verify code offline, then measure the authorized live run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.config import load_config
from detector.evaluation import evaluate_run
from detector.identity import digest, source_identity
from detector.runtime import atomic_json, utc_now
from evolution.metrics import binary_metrics
from scripts.verify import source_manifest

OUT = ROOT / "artifacts/detector/deep_optimization_20261013/live95"
DATA = ROOT / "datasets/raw/agenthallu/AgentHallu"
RECOVERED = ROOT / "artifacts/detector/live_checks/live100_20261013_recovered/evaluation.json"
HISTORICAL = ROOT / "docs/results/full693-20260927/baseline_report.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare() -> None:
    if (OUT / "manifest.json").exists():
        raise RuntimeError("95-case protocol already frozen; refusing replacement")
    historical, recovered = read(HISTORICAL), read(RECOVERED)
    historical_by_case = {row["case"]: row for row in historical["rows"]}
    rows = [row for row in recovered["rows"] if row["case"] in historical_by_case]
    if len(rows) != 95 or len({row["case"] for row in rows}) != 95:
        raise ValueError("historical/recovered intersection is not exactly 95 distinct cases")
    cases = [row["case"] for row in rows]
    overlap = [row["case"] for row in rows if row["gold"] is True and row["prediction"] is True
               and historical_by_case[row["case"]]["prediction"] is True]
    raw = read(ROOT / "detector/configs/autodl.json")
    for route in ("judge_route", "defense_route"):
        raw[route]["vote_concurrency"] = 1
    OUT.mkdir(parents=True, exist_ok=True)
    atomic_json(OUT / "config.json", raw)
    atomic_json(OUT / "cases.json", cases)
    config = load_config(OUT / "config.json", require_credentials=False)
    atomic_json(OUT / "baseline95.json", {"rows": rows, "metrics": binary_metrics(rows)})
    atomic_json(OUT / "manifest.json", {
        "schema_version": "detector.deep_optimization_protocol.v1", "state": "prepared",
        "prepared_at_utc": utc_now(), "cases": cases, "workers": 10, "provider_inflight_limit": 10,
        "run_id": OUT.name + "_deep_optimization_20261013", "overlap_positive_cohort": overlap,
        "selection": "historical/recovered case-id intersection in recovered order; labels unused for selection",
        "source_identity": source_identity("detector", "evolution"), "source_manifest": source_manifest(),
        "config_digest": digest(config), "raw_case_sha256": {case: file_hash(DATA / case) for case in cases},
        "historical_sha256": file_hash(HISTORICAL), "recovered_sha256": file_hash(RECOVERED),
        "acceptance": {"decisive_accuracy_min": 0.72, "localization_hits_must_exceed": 20,
                       "localization_denominator": 57, "overlap_original_hit_loss_max": 0},
        "measurement": "one fresh uniform-version run; unresolved rows stay unresolved; no gold-based retry selection",
    })


def verify_identity(manifest) -> None:
    if (source_manifest() != manifest["source_manifest"]
            or source_identity("detector", "evolution") != manifest["source_identity"]):
        raise RuntimeError("source changed after protocol freeze")
    if digest(load_config(OUT / "config.json", require_credentials=False)) != manifest["config_digest"]:
        raise RuntimeError("effective configuration changed after protocol freeze")
    for case, expected in manifest["raw_case_sha256"].items():
        if file_hash(DATA / case) != expected:
            raise RuntimeError("raw case changed after protocol freeze")
    if file_hash(HISTORICAL) != manifest["historical_sha256"] or file_hash(RECOVERED) != manifest["recovered_sha256"]:
        raise RuntimeError("historical evaluation changed after protocol freeze")


def localization_hit(row) -> bool:
    return (row["gold"] is True and row["prediction"] is True
            and row.get("authority") == "authoritative"
            and row.get("localization_authority") in (None, "authoritative")
            and row.get("responsible_step") == row.get("gold_step") and row.get("gold_step") is not None)


def report(manifest) -> dict:
    current = evaluate_run(runs_root=OUT / "runs", run_id=manifest["run_id"], dataset_root=DATA, cases=manifest["cases"])
    atomic_json(OUT / "evaluation.json", current)
    baseline = read(OUT / "baseline95.json")
    before = {row["case"]: row for row in baseline["rows"]}
    after = {row["case"]: row for row in current["rows"]}
    cohort = manifest["overlap_positive_cohort"]
    lost = [case for case in cohort if localization_hit(before[case]) and not localization_hit(after[case])]
    later = [case for case in cohort if after[case].get("responsible_step") is not None
             and before[case].get("responsible_step") is not None
             and after[case]["responsible_step"] > before[case]["responsible_step"]]
    new_hits = sum(localization_hit(row) for row in current["rows"])
    missing_cohort = [case for case in cohort if after[case]["prediction"] is not True
                      or after[case].get("localization_authority") != "authoritative"
                      or after[case].get("responsible_step") is None]
    acceptance = {
        "detection_accuracy": (current["metrics"]["decisive_accuracy"] or 0) >= 0.72,
        "localization_improved": new_hits > 20,
        "overlap_original_hits_preserved": not lost,
        "overlap_no_later_movement": not later,
        "overlap_localization_available": not missing_cohort,
        "full_request_coverage": current["metrics"]["found_results"] == 95,
    }
    comparison = {"baseline": baseline["metrics"], "current": current["metrics"],
                  "baseline_localization": {"hits": 20, "n": 57},
                  "current_localization": {"hits": new_hits, "n": 57},
                  "overlap_cohort": cohort, "overlap_hit_losses": lost, "overlap_later_movements": later,
                  "overlap_unavailable_localizations": missing_cohort,
                  "acceptance": acceptance, "passed": all(acceptance.values()),
                  "rows": [{"case": case, "before": before[case], "after": after[case]} for case in manifest["cases"]]}
    atomic_json(OUT / "comparison.json", comparison)
    lines = ["# 同集95例优化前后对比", "", "新运行使用统一源码；历史恢复版为混合源码/预算。差异不能单独归因于某一模块。", "",
             "| 指标 | 恢复版95例 | 优化后95例 |", "|---|---:|---:|"]
    for label, key in (("权威覆盖", "decisive"), ("权威检测准确率", "decisive_accuracy"),
                       ("全请求正确率", "all_case_accuracy"), ("TP", "tp"), ("FP", "fp"), ("FN", "fn"), ("TN", "tn")):
        values = [comparison[version][key] for version in ("baseline", "current")]
        formatted = [f"{value:.2%}" if isinstance(value, float) else str(value) for value in values]
        lines.append(f"| {label} | {formatted[0]} | {formatted[1]} |")
    lines.extend([f"| 首错定位 | 20/57（35.09%） | {new_hits}/57（{new_hits / 57:.2%}） |", "",
                  f"固定重合真阳性{len(cohort)}例，原命中损失{len(lost)}例，后移{len(later)}例。", "",
                  "验收状态：" + json.dumps(acceptance, ensure_ascii=False), "",
                  "逐例与完整指标：[comparison.json](comparison.json)；原始运行：[evaluation.json](evaluation.json)。"])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return comparison


def execute() -> None:
    manifest = read(OUT / "manifest.json")
    if manifest["state"] != "prepared":
        raise RuntimeError("orchestration already started; inspect the existing process, never duplicate it")
    verify_identity(manifest)
    manifest.update(state="offline_verification", started_at_utc=utc_now())
    atomic_json(OUT / "manifest.json", manifest)
    with (OUT / "verification_console.log").open("w", encoding="utf-8") as log:
        code = subprocess.call([sys.executable, "scripts/verify.py", "--scope", "all"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    output = (OUT / "verification_console.log").read_text(encoding="utf-8")
    location = next((line.removeprefix("report: ") for line in output.splitlines() if line.startswith("report: ")), None)
    quality = read(Path(location) / "summary.json") if location else {}
    verified_digest = hashlib.sha256(json.dumps(source_manifest(), sort_keys=True).encode()).hexdigest()
    if (code != 0 or quality.get("passed") is not True or quality.get("scope") != "all"
            or quality.get("source_sha256") != verified_digest or len(quality.get("checks") or []) != 18
            or any(quality.get("tests", {}).get(key) != 0 for key in ("failures", "errors", "skipped"))):
        manifest.update(state="offline_failed", verification_exit_code=code, verification_report=location)
        atomic_json(OUT / "manifest.json", manifest)
        return
    verify_identity(manifest)
    manifest.update(state="live_running", verification_report=location, live_started_at_utc=utc_now())
    atomic_json(OUT / "manifest.json", manifest)
    with (OUT / "live_console.log").open("w", encoding="utf-8") as log:
        code = subprocess.call([sys.executable, "-m", "detector", "batch", "--dataset-root", str(DATA),
                                "--cases-file", str(OUT / "cases.json"), "--config", str(OUT / "config.json"),
                                "--runs-root", str(OUT / "runs"), "--run-id", manifest["run_id"], "--workers", "10"],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    verify_identity(manifest)
    result = report(manifest)
    manifest.update(state="finished", finished_at_utc=utc_now(), live_exit_code=code, acceptance_passed=result["passed"])
    atomic_json(OUT / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "execute", "report"))
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("attempt must be positive")
    if args.attempt > 1:
        OUT = OUT.with_name(f"live95_attempt{args.attempt}")
    phase = args.phase
    if phase == "prepare":
        prepare()
    elif phase == "execute":
        execute()
    else:
        report(read(OUT / "manifest.json"))
