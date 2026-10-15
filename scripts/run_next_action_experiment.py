"""冻结、运行、恢复并汇报同一100例的下一步预测实验。"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.config import load_config
from detector.evaluation import binary_metrics, evaluate_run
from detector.identity import digest
from detector.result import prediction_state
from detector.runner import run_batch, safe_case_id
from detector.runtime import atomic_json, exclusive_writer, utc_now
from scripts.verify import source_manifest

OUT = ROOT / "artifacts/detector/live_checks/next_action100_20261015"
DATA = ROOT / "datasets/raw/agenthallu/AgentHallu"
HISTORY = ROOT / "docs/results/localization100-20261015"
FIRST = "next_action100_quote_fixed"
RECOVERY = "next_action100_quote_fixed_recovery"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def result_path(run_id, case):
    return OUT / "runs" / run_id / "cases" / safe_case_id(case) / "result.json"


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "manifest.json").exists():
        raise RuntimeError("已有冻结实验，请检查状态后续接")
    previous = ROOT / "artifacts/detector/live_checks/live100_20261014_full"
    cases = read(previous / "cases.json")
    old_rows = read(HISTORY / "baseline-evaluation.json")["rows"]
    recent_rows = read(HISTORY / "final-evaluation.json")["rows"]
    if len(cases) != 100 or len(set(cases)) != 100 or set(cases) != {r["case"] for r in old_rows} or set(cases) != {r["case"] for r in recent_rows}:
        raise ValueError("前两次与本次清单不一致")
    cfg = read(ROOT / "detector/configs/autodl.json")
    cfg["causal_audit"].update(next_action_mode="judge_context", max_added_provider_requests_per_case=1,
                               max_added_prompt_tokens_per_case=60000, max_added_output_tokens_per_case=4096,
                               max_added_seconds_per_case=180)
    atomic_json(OUT / "cases.json", cases)
    atomic_json(OUT / "config.json", cfg)
    protected = [DATA / case for case in cases] + [HISTORY / "baseline-evaluation.json", HISTORY / "final-evaluation.json",
                                                 previous / "cases.json", OUT / "cases.json", OUT / "config.json"]
    manifest = {"state": "prepared", "prepared_at": utc_now(), "cases": cases,
                "baseline_commit": "1137a50", "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_manifest": source_manifest(), "protected_files": {str(path): sha(path) for path in protected},
                "workers": 10, "provider_concurrency": 10,
                "recovery_rule": "首轮全部非权威/缺失案例统一补测一次；仅补测恢复权威时采用，否则保留首轮；选择不读取金标或正确性。",
                "comparison_scope": "同组100例历史内部比较；源码、输出额度和新增预算不同，不能归因为单模块因果增益。",
                "config_change": "默认修复后的三路配置；新增下一步预测judge_context，每例最多1次请求、60000 UTF-8字节额度、4096输出token额度、180秒新增工作额度。"}
    atomic_json(OUT / "manifest.json", manifest)
    print(json.dumps({"state": "prepared", "cases": len(cases), "directory": str(OUT)}, ensure_ascii=False))


def verify_identity(manifest):
    if source_manifest() != manifest["source_manifest"]:
        raise ValueError("源码/协议/测试身份变化，停止实验")
    for path, expected in manifest["protected_files"].items():
        if sha(path) != expected:
            raise ValueError("冻结输入变化：" + path)


def metrics(rows):
    values = binary_metrics(rows)
    positive = [row for row in rows if row["gold"]]
    hits = sum(localized(row) for row in positive)
    values.update(authoritative=sum(row["prediction"] is not None for row in rows),
                  requested_cases=len(rows), step_localization={"n": len(positive), "hits": hits,
                                                               "accuracy": hits / len(positive) if positive else None})
    return values


def localized(row):
    return (row["gold"] and row["prediction"] is True and row["responsible_step"] == row["gold_step"]
            and row.get("localization_authority") in {None, "authoritative"}
            and row.get("localization_target") in {None, "dataset_first_error_event.v1"})


def paired(old, current):
    before = {row["case"]: row for row in old}
    changes = {"detection_fixed": [], "detection_regressed": [], "localization_fixed": [], "localization_regressed": []}
    for row in current:
        previous = before[row["case"]]
        good = row["prediction"] is not None and row["prediction"] == row["gold"]
        was_good = previous["prediction"] is not None and previous["prediction"] == previous["gold"]
        if good != was_good:
            changes["detection_fixed" if good else "detection_regressed"].append(row["case"])
        if localized(row) != localized(previous):
            changes["localization_fixed" if localized(row) else "localization_regressed"].append(row["case"])
    return changes


def journal_cost():
    requests = {}
    journals = []
    for path in sorted((OUT / "runs").rglob("requests.jsonl")):
        journals.append({"path": str(path.relative_to(OUT)), "sha256": sha(path)})
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = row.get("request_id")
            if key:
                requests.setdefault(key, {"run_id": path.relative_to(OUT / "runs").parts[0]}).update({row["event"]: row})
    starts = [r["request_started"] for r in requests.values() if "request_started" in r]
    finishes = [r["request_finished"] for r in requests.values() if "request_finished" in r]
    usage = Counter()
    for row in finishes:
        usage.update({key: value for key, value in (row.get("usage") or {}).items()
                      if isinstance(value, (int, float)) and not isinstance(value, bool)})
    return {"started_requests": len(starts), "finished_requests": len(finishes),
            "started_without_finish": sum("request_started" in r and "request_finished" not in r for r in requests.values()),
            "by_purpose": dict(Counter(row.get("purpose") for row in starts)),
            "started_by_run": dict(Counter(row["run_id"] for row in requests.values() if "request_started" in row)),
            "status_counts": dict(Counter(str(row.get("status")) for row in finishes)),
            "usage": dict(usage), "usage_missing": sum(row.get("usage") is None for row in finishes),
            "pricing": "未取得服务实际账单单价，不估造货币费用。", "journals": journals}


def report(manifest):
    verify_identity(manifest)
    cases = manifest["cases"]
    first = evaluate_run(runs_root=OUT / "runs", run_id=FIRST, dataset_root=DATA, cases=cases)
    atomic_json(OUT / "first-evaluation.json", first)
    recovery_cases = manifest.get("recovery_cases", [])
    recovered = evaluate_run(runs_root=OUT / "runs", run_id=RECOVERY, dataset_root=DATA,
                             cases=recovery_cases) if recovery_cases else {"rows": []}
    atomic_json(OUT / "recovery-evaluation.json", recovered)
    replacements = {row["case"]: row for row in recovered["rows"] if row["prediction"] is not None}
    rows = [replacements.get(row["case"], row) for row in first["rows"]]
    mechanisms, provenance = [], []
    elapsed = []
    for row in rows:
        case = row["case"]
        run = RECOVERY if case in replacements else FIRST
        path = result_path(run, case)
        if not path.exists():
            provenance.append({"case": case, "run": run, "missing": True})
            continue
        result = read(path)
        forecast = result.get("proof", {}).get("action_forecast") or {}
        comparisons = (result.get("proof", {}).get("judge") or {}).get("next_action_comparisons") or []
        mechanisms.append({"case": case, "health": forecast.get("health", "missing"),
                           "used": result.get("proof", {}).get("action_forecast_used", False),
                           "model_predictions": sum(p["producer"] == "model_hypothesis" and bool(p["constraints"])
                                                    for p in forecast.get("predictions", [])),
                           "deterministic_predictions": sum(p["producer"] == "deterministic" for p in forecast.get("predictions", [])),
                           "comparison_states": dict(Counter(c["state"] for c in comparisons))})
        provenance.append({"case": case, "run": run, "path": str(path), "sha256": sha(path)})
        seconds = result.get("execution", {}).get("elapsed_seconds")
        if isinstance(seconds, (int, float)):
            elapsed.append(seconds)
    old = read(HISTORY / "baseline-evaluation.json")
    recent = read(HISTORY / "final-evaluation.json")
    evaluation = {"schema_version": "research.authority_recovery_evaluation.v1", "run_id": FIRST + "_merged",
                  "metrics": metrics(rows), "rows": rows, "adopted_recovery_cases": list(replacements),
                  "provenance": provenance, "selection_rule": manifest["recovery_rule"]}
    atomic_json(OUT / "final-evaluation.json", evaluation)
    cost = journal_cost()
    comparison = {"historical": old["metrics"], "recent": recent["metrics"], "current": evaluation["metrics"],
                  "paired_historical": paired(old["rows"], rows), "paired_recent": paired(recent["rows"], rows),
                  "cost_all_attempts": cost, "mechanisms": mechanisms,
                  "mechanism_used_cases": sum(row["used"] for row in mechanisms),
                  "case_elapsed_seconds_median": statistics.median(elapsed) if elapsed else None,
                  "scope": manifest["comparison_scope"]}
    atomic_json(OUT / "comparison.json", comparison)
    lines = ["# 下一步预测：同组100例真实实验", "", "| 指标 | 历史恢复 | 最近恢复 | 本次 |", "|---|---:|---:|---:|"]
    for label, key in (("权威覆盖", "authoritative"), ("全请求正确率", "all_case_accuracy"),
                       ("权威准确率", "decisive_accuracy"), ("TP", "tp"), ("FP", "fp"), ("FN", "fn"), ("TN", "tn")):
        values = [comparison[name].get(key) for name in ("historical", "recent", "current")]
        formatted = [f"{value:.2%}" if isinstance(value, float) else str(value) for value in values]
        lines.append("| " + " | ".join([label, *formatted]) + " |")
    step = [comparison[name]["step_localization"] for name in ("historical", "recent", "current")]
    lines.extend(["| 首错命中 | " + " | ".join(f"{v['hits']}/{v['n']}" for v in step) + " |", "",
                  f"实际采用预测上下文的案例数：{comparison['mechanism_used_cases']}/100；全部尝试请求数：{cost['started_requests']}；"
                  f"预测请求数：{cost['by_purpose'].get('action_forecast', 0)}；缺少结束记录：{cost['started_without_finish']}。", "",
                  "恢复采用：" + ("、".join(replacements) or "无") + "。", "",
                  manifest["comparison_scope"], "", "配对变化、全部请求用量和逐例机制：[comparison.json](comparison.json)。",
                  "首轮与补测原件分别保留，采用身份见[final-evaluation.json](final-evaluation.json)。"])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return evaluation


def execute(verification_report=None):
    with exclusive_writer(OUT / "orchestration"):
        manifest = read(OUT / "manifest.json")
        if manifest["state"] == "finished":
            raise RuntimeError("实验已完成，不重复调用服务")
        verify_identity(manifest)
        cfg = load_config(OUT / "config.json")
        if verification_report is not None:
            manifest["verification_report"] = str(Path(verification_report).resolve())
        if not manifest.get("verification_report"):
            raise RuntimeError("请用--verification-report指定匹配源码的完整验收目录")
        quality = Path(manifest["verification_report"])
        if not read(quality / "summary.json").get("passed") or read(quality / "source_manifest.json")["files"] != source_manifest():
            raise RuntimeError("当前源码缺少匹配的完整验收")
        started = time.monotonic()
        manifest.update(state="running", pid=os.getpid(), started_at=manifest.get("started_at", utc_now()))
        atomic_json(OUT / "manifest.json", manifest)
        try:
            summary = run_batch(dataset_root=DATA, cases=manifest["cases"], run_id=FIRST, runs_root=OUT / "runs",
                                config=cfg, workers=manifest["workers"], provider_concurrency=manifest["provider_concurrency"])
            verify_identity(manifest)
            recovery_cases = [case for case in manifest["cases"]
                              if not result_path(FIRST, case).exists() or prediction_state(read(result_path(FIRST, case))) is None]
            manifest.update(state="recovering", recovery_cases=recovery_cases, first_finished_at=utc_now())
            atomic_json(OUT / "manifest.json", manifest)
            if recovery_cases and not summary.get("stop"):
                run_batch(dataset_root=DATA, cases=recovery_cases, run_id=RECOVERY, runs_root=OUT / "runs", config=cfg,
                          workers=min(len(recovery_cases), manifest["workers"]), provider_concurrency=manifest["provider_concurrency"])
            evaluation = report(manifest)
            manifest.update(state="finished", finished_at=utc_now(), invocation_elapsed_seconds=time.monotonic() - started,
                            final_metrics=evaluation["metrics"], adopted_recovery_cases=evaluation["adopted_recovery_cases"])
            atomic_json(OUT / "manifest.json", manifest)
        except BaseException as exc:
            manifest.update(state="interrupted", interruption_type=type(exc).__name__, interrupted_at=utc_now())
            atomic_json(OUT / "manifest.json", manifest)
            raise


def status():
    manifest = read(OUT / "manifest.json")
    states, authorities = Counter(), Counter()
    for case in manifest["cases"]:
        path = result_path(FIRST, case)
        if path.exists():
            result = read(path)
            states[str(result.get("state"))] += 1
            authorities[str(result.get("decision_authority"))] += 1
    cost = journal_cost()
    print(json.dumps({"state": manifest["state"], "completed_first": sum(states.values()), "target": 100,
                      "states": dict(states), "authority": dict(authorities),
                      "started_requests": cost["started_requests"], "finished_requests": cost["finished_requests"],
                      "current_run_started_requests": cost["started_by_run"].get(FIRST, 0),
                      "forecast_requests": cost["by_purpose"].get("action_forecast", 0)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "execute", "status", "report"))
    parser.add_argument("--verification-report")
    args = parser.parse_args()
    if args.phase == "report":
        report(read(OUT / "manifest.json"))
    elif args.phase == "execute":
        execute(args.verification_report)
    else:
        {"prepare": prepare, "status": status}[args.phase]()
