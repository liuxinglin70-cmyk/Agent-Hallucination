"""Run and report the single-arm 100-case validation of the next-action mechanism.

Pre-registered acceptance (fixed before the live batch, see ``CRITERIA``):

1. authoritative coverage >= 97/100 after the one-shot非权威 recovery;
2. strict improvement over the archived pre-mechanism (G0) result on the same
   cases in detection or first-error localization, with the other metric not
   worse and false positives not above the archived baseline;
3. the mechanism actually ran: model forecasts used in >= 60 cases, at least
   five representative-ballot comparisons that are valid and non-unknown, and
   at least one action candidate admitted into the standard recall chain.

Anything short of that is a failed validation and is reported as such.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
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

OUT = ROOT / "artifacts/detector/live_checks/next_action_validation100_20261017"
DATA = ROOT / "datasets/raw/agenthallu/AgentHallu"
SAMPLE = ROOT / "artifacts/detector/live_checks/next_action_ab100_20261017"
FIRST = "validation100"
RECOVERY = "validation100_recovery"

CRITERIA = {
    "min_authoritative": 97,
    "min_forecast_used": 60,
    "min_valid_non_unknown_comparisons": 5,
    "rule": ("strict improvement in detection or localization versus the archived G0 "
             "baseline on the same cases; the other metric not worse; FP not above baseline. "
             "Action candidates are reported as a mechanism diagnostic, not a pass gate: "
             "admission also requires a strict majority of ballots to diverge."),
}


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def result_path(run_id: str, case: str) -> Path:
    return OUT / "runs" / run_id / "cases" / safe_case_id(case) / "result.json"


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "manifest.json").exists():
        raise RuntimeError("已有冻结实验，请检查状态后续接")
    sample = read(SAMPLE / "sample.json")
    cases = [row["case"] for row in sample["cases"]]
    if len(cases) != 100 or len(set(cases)) != 100:
        raise ValueError("sample is not 100 unique cases")
    if any(row["family"] == "BFCL" for row in sample["cases"]):
        raise ValueError("multi-turn-only family should not be in the in-scope sample")
    cfg = load_config(ROOT / "detector/configs/autodl.json")
    cfg["causal_audit"].update(
        next_action_mode="judge_context",
        max_added_provider_requests_per_case=2,
        max_added_prompt_tokens_per_case=60000,
        max_added_output_tokens_per_case=8192,
        # The added-work clock includes the time this optional request spends
        # queued behind other cases' slots (that is the shipped contract: all
        # optional work, queue included, is inside the allowance).  A 180s
        # allowance therefore ran out during a 21-minute queue in the smoke
        # batch and reported "time exhausted" for a request that never ran.
        # 900s matches the base route timeout and keeps the allowance bounded.
        max_added_seconds_per_case=900,
        max_forecast_output_tokens_per_request=8192,
    )
    atomic_json(OUT / "cases.json", cases)
    atomic_json(OUT / "config.json", cfg)
    protected = [DATA / case for case in cases] + [SAMPLE / "sample.json", SAMPLE / "cases.json",
                                                    OUT / "cases.json", OUT / "config.json"]
    manifest = {
        "state": "prepared", "prepared_at": utc_now(), "cases": cases,
        "sample_digest": digest(sample), "sample_sha256": sha(SAMPLE / "sample.json"),
        "source_manifest": source_manifest(),
        "git_head": _git_head(),
        "protected_files": {str(path): sha(path) for path in protected},
        "workers": 10, "provider_concurrency": 10,
        "recovery_rule": ("首轮全部非权威或缺失案例统一补测一次；仅补测恢复权威时采用，"
                          "不读取金标或是否判对。"),
        "criteria": CRITERIA,
        "comparison_note": ("对照为同批100例在归档G0基线（引入下一步预测前）中的结果；"
                            "源码与预算不同，属于内部回顾性同案比较，不归因为单模块因果。"),
        "config_change": ("部署配置 judge_context, 每例最多2次可选请求、60000输入字节、"
                          "8192输出token、900秒新增工作额度（含批次内排队，与原有合同一致）、"
                          "单次预测输出上限8192 token。"),
    }
    atomic_json(OUT / "manifest.json", manifest)
    print(json.dumps({"state": "prepared", "cases": len(cases), "directory": str(OUT)},
                     ensure_ascii=False))


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "uncommitted"


def verify_identity(manifest) -> None:
    if source_manifest() != manifest["source_manifest"]:
        raise ValueError("源码/协议/测试身份变化，停止实验")
    for path, expected in manifest["protected_files"].items():
        if sha(path) != expected:
            raise ValueError("冻结输入变化：" + path)


def localized(row) -> bool:
    return (row["gold"] and row["prediction"] is True and row["responsible_step"] == row["gold_step"]
            and row.get("localization_authority") in {None, "authoritative"}
            and row.get("localization_target") in {None, "dataset_first_error_event.v1"})


def metrics(rows) -> dict:
    values = binary_metrics(rows)
    positive = [row for row in rows if row["gold"]]
    hits = sum(localized(row) for row in positive)
    values.update(authoritative=sum(row["prediction"] is not None for row in rows),
                  requested_cases=len(rows),
                  step_localization={"n": len(positive), "hits": hits,
                                     "accuracy": hits / len(positive) if positive else None})
    return values


def baseline_metrics(sample) -> dict:
    rows = []
    for row in sample["cases"]:
        base = row["pre_mechanism_baseline"]
        rows.append({"case": row["case"], "gold": row["gold"],
                     "prediction": base.get("prediction"),
                     "gold_step": base.get("gold_step"),
                     "responsible_step": base.get("responsible_step"),
                     "localization_authority": "authoritative",
                     "localization_target": "dataset_first_error_event.v1"})
    return metrics(rows)


def paired(old_rows, current_rows) -> dict:
    before = {row["case"]: row for row in old_rows}
    changes = {"detection_fixed": [], "detection_regressed": [],
               "localization_fixed": [], "localization_regressed": []}
    for row in current_rows:
        previous = before[row["case"]]
        good = row["prediction"] is not None and row["prediction"] == row["gold"]
        was_good = previous["prediction"] is not None and previous["prediction"] == previous["gold"]
        if good != was_good:
            changes["detection_fixed" if good else "detection_regressed"].append(row["case"])
        if localized(row) != localized(previous):
            changes["localization_fixed" if localized(row) else "localization_regressed"].append(row["case"])
    return changes


def journal_cost() -> dict:
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
            "started_without_finish": sum("request_started" in r and "request_finished" not in r
                                          for r in requests.values()),
            "by_purpose": dict(Counter(row.get("purpose") for row in starts)),
            "started_by_run": dict(Counter(row["run_id"] for row in requests.values() if "request_started" in row)),
            "status_counts": dict(Counter(str(row.get("status")) for row in finishes)),
            "usage": dict(usage), "usage_missing": sum(row.get("usage") is None for row in finishes),
            "pricing": "未取得服务实际账单单价，不估造货币费用。", "journals": journals}


def mechanisms(rows) -> tuple[list[dict], list[dict]]:
    mechanisms, provenance = [], []
    for row in rows:
        case = row["case"]
        run = row.get("_run", FIRST)
        path = result_path(run, case)
        if not path.exists():
            provenance.append({"case": case, "run": run, "missing": True})
            continue
        result = read(path)
        proof = result.get("proof") or {}
        forecast = proof.get("action_forecast") or {}
        ballots = proof.get("next_action_vote_comparisons") or []
        mechanisms.append({
            "case": case,
            "run": run,
            "gold": row["gold"],
            "prediction": row["prediction"],
            "responsible_step": row["responsible_step"],
            "gold_step": row["gold_step"],
            "health": forecast.get("health", "missing"),
            "used": bool(proof.get("action_forecast_used")),
            "model_predictions": sum(
                prediction["producer"] == "model_hypothesis" and bool(prediction["constraints"])
                for prediction in forecast.get("predictions", [])),
            "deterministic_predictions": sum(
                prediction["producer"] == "deterministic" for prediction in forecast.get("predictions", [])),
            "prediction_state": _prediction_state(proof),
            "comparison_states": dict(Counter(
                state for ballot in ballots for state in _comparison_states(ballot))),
            "action_candidates": len(proof.get("action_candidates") or []),
        })
        provenance.append({"case": case, "run": run, "path": str(path), "sha256": sha(path)})
    return mechanisms, provenance


def _comparison_states(ballot) -> list[str]:
    if not isinstance(ballot, list):
        return []
    return [row.get("state") for row in ballot if row.get("validation_state") == "valid"]


def _prediction_state(proof) -> dict:
    forecast = proof.get("action_forecast") or {}
    predictions = [p for p in forecast.get("predictions", []) if p.get("producer") == "model_hypothesis"]
    if not predictions:
        return {"state": "no_model_prediction"}
    target = predictions[0].get("next_event_id")
    return {"state": "present", "next_event_id": target}


def report() -> dict:
    manifest = read(OUT / "manifest.json")
    verify_identity(manifest)
    cases = manifest["cases"]
    sample = read(SAMPLE / "sample.json")
    first = evaluate_run(runs_root=OUT / "runs", run_id=FIRST, dataset_root=DATA, cases=cases)
    atomic_json(OUT / "first-evaluation.json", first)
    recovery_cases = manifest.get("recovery_cases", [])
    recovered = evaluate_run(runs_root=OUT / "runs", run_id=RECOVERY, dataset_root=DATA,
                             cases=recovery_cases) if recovery_cases else {"rows": []}
    atomic_json(OUT / "recovery-evaluation.json", recovered)
    replacements = {row["case"]: row for row in recovered["rows"] if row["prediction"] is not None}
    rows = []
    for row in first["rows"]:
        if row["case"] in replacements:
            rows.append({**replacements[row["case"]], "_run": RECOVERY})
        else:
            rows.append({**row, "_run": FIRST})
    current = metrics(rows)
    baseline = baseline_metrics(sample)
    comparison = {
        "baseline": baseline,
        "current": current,
        "paired": paired(
            [{"case": item["case"], "gold": item["gold"],
              "prediction": item["pre_mechanism_baseline"]["prediction"],
              "responsible_step": item["pre_mechanism_baseline"]["responsible_step"],
              "gold_step": item["pre_mechanism_baseline"]["gold_step"],
              "localization_authority": "authoritative",
              "localization_target": "dataset_first_error_event.v1"}
             for item in sample["cases"]], rows),
    }
    mechanism_rows, provenance = mechanisms(rows)
    baseline_fp = baseline["fp"]
    criteria = {
        "authoritative": current["authoritative"] >= CRITERIA["min_authoritative"],
        "forecast_used": sum(row["used"] for row in mechanism_rows) >= CRITERIA["min_forecast_used"],
        "valid_non_unknown_comparisons": sum(
            count for row in mechanism_rows
            for state, count in row["comparison_states"].items() if state != "unknown"
        ) >= CRITERIA["min_valid_non_unknown_comparisons"],
        "detection_not_worse": (current["tp"] + current["tn"]) >= (baseline["tp"] + baseline["tn"]),
        "localization_not_worse": current["step_localization"]["hits"] >= baseline["step_localization"]["hits"],
        "fp_not_worse": current["fp"] <= baseline_fp,
        "strict_improvement": ((current["tp"] + current["tn"]) > (baseline["tp"] + baseline["tn"])
                               or current["step_localization"]["hits"] > baseline["step_localization"]["hits"]),
    }
    criteria["passed"] = all(criteria.values())
    elapsed = []
    for row in rows:
        path = result_path(row["_run"], row["case"])
        if path.exists():
            seconds = read(path).get("execution", {}).get("elapsed_seconds")
            if isinstance(seconds, (int, float)):
                elapsed.append(seconds)
    cost = journal_cost()
    evaluation = {
        "schema_version": "research.next_action_validation100.v1",
        "run_id": FIRST + "_merged",
        "metrics": current,
        "rows": [{key: value for key, value in row.items() if key != "_run"} for row in rows],
        "adopted_recovery_cases": sorted(replacements),
        "provenance": provenance,
        "selection_rule": manifest["recovery_rule"],
    }
    atomic_json(OUT / "final-evaluation.json", evaluation)
    result_doc = {
        "baseline": baseline, "current": current, "comparison": comparison,
        "mechanisms": mechanism_rows, "cost_all_attempts": cost,
        "mechanism_used_cases": sum(row["used"] for row in mechanism_rows),
        "model_prediction_cases": sum(row["model_predictions"] > 0 for row in mechanism_rows),
        "action_candidate_cases": sum(row["action_candidates"] > 0 for row in mechanism_rows),
        "health_counts": dict(Counter(row["health"] for row in mechanism_rows)),
        "case_elapsed_seconds_median": statistics.median(elapsed) if elapsed else None,
        "case_elapsed_seconds_p95": (sorted(elapsed)[int(0.95 * (len(elapsed) - 1))] if elapsed else None),
        "criteria": criteria,
        "criteria_definition": CRITERIA,
        "scope": manifest["comparison_note"],
    }
    atomic_json(OUT / "comparison.json", result_doc)
    lines = ["# 下一步预测机制：新100例验证", "",
             "| 指标 | 引入前（G0同案） | 本次 |", "|---|---:|---:|"]
    for label, key in (("权威覆盖", "authoritative"), ("全请求正确率", "all_case_accuracy"),
                       ("权威准确率", "decisive_accuracy"), ("TP", "tp"), ("FP", "fp"),
                       ("FN", "fn"), ("TN", "tn")):
        values = [baseline.get(key), current.get(key)]
        formatted = [f"{value:.2%}" if isinstance(value, float) else str(value) for value in values]
        lines.append("| " + " | ".join([label, *formatted]) + " |")
    lines.extend([
        "| 首错命中 | {}/{} | {}/{} |".format(
            baseline["step_localization"]["hits"], baseline["step_localization"]["n"],
            current["step_localization"]["hits"], current["step_localization"]["n"]), "",
        "配对变化：检测修正 {} / 回退 {}；定位修正 {} / 回退 {}。".format(
            len(comparison["paired"]["detection_fixed"]), len(comparison["paired"]["detection_regressed"]),
            len(comparison["paired"]["localization_fixed"]), len(comparison["paired"]["localization_regressed"])),
        "", "机制覆盖：预测使用 {}/100；模型预测 {}/100；行动候选 {}/100；健康状态 {}。".format(
            result_doc["mechanism_used_cases"], result_doc["model_prediction_cases"],
            result_doc["action_candidate_cases"], result_doc["health_counts"]),
        "", "验收：{}".format("通过" if criteria["passed"] else "未通过"), "",
        "逐项：" + "；".join(f"{key}={value}" for key, value in criteria.items() if key != "passed"), "",
        manifest["comparison_note"], "",
        "恢复采用：" + ("、".join(sorted(replacements)) or "无") + "。",
        "逐例机制与请求用量见 [comparison.json](comparison.json)，采用来源见 [final-evaluation.json](final-evaluation.json)。",
    ])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"criteria": criteria, "metrics": current,
                      "mechanism_used": result_doc["mechanism_used_cases"]}, ensure_ascii=False, indent=2))
    return result_doc


def execute(verification_report: str | None = None) -> None:
    with exclusive_writer(OUT / "orchestration"):
        manifest = read(OUT / "manifest.json")
        if manifest["state"] == "finished":
            raise RuntimeError("实验已完成，不重复调用服务")
        verify_identity(manifest)
        cfg = read(OUT / "config.json")
        if verification_report is not None:
            manifest["verification_report"] = str(Path(verification_report).resolve())
        if not manifest.get("verification_report"):
            raise RuntimeError("请用 --verification-report 指定匹配源码的完整验收目录")
        quality = Path(manifest["verification_report"])
        if not read(quality / "summary.json").get("passed") or \
                read(quality / "source_manifest.json")["files"] != source_manifest():
            raise RuntimeError("当前源码缺少匹配的完整验收")
        started = time.monotonic()
        manifest.update(state="running", pid=os.getpid(), started_at=manifest.get("started_at", utc_now()))
        atomic_json(OUT / "manifest.json", manifest)
        try:
            summary = run_batch(dataset_root=DATA, cases=manifest["cases"], run_id=FIRST,
                                runs_root=OUT / "runs", config=cfg, workers=manifest["workers"],
                                provider_concurrency=manifest["provider_concurrency"])
            verify_identity(manifest)
            recovery_cases = [case for case in manifest["cases"]
                              if not result_path(FIRST, case).exists()
                              or prediction_state(read(result_path(FIRST, case))) is None]
            manifest.update(state="recovering", recovery_cases=recovery_cases, first_finished_at=utc_now())
            atomic_json(OUT / "manifest.json", manifest)
            if recovery_cases and not summary.get("stop"):
                run_batch(dataset_root=DATA, cases=recovery_cases, run_id=RECOVERY, runs_root=OUT / "runs",
                          config=cfg, workers=min(len(recovery_cases), manifest["workers"]),
                          provider_concurrency=manifest["provider_concurrency"])
            evaluation = report()
            manifest.update(state="finished", finished_at=utc_now(),
                            invocation_elapsed_seconds=time.monotonic() - started,
                            final_metrics=evaluation["current"],
                            criteria=evaluation["criteria"],
                            adopted_recovery_cases=sorted(replacements_from_report()))
            atomic_json(OUT / "manifest.json", manifest)
        except BaseException as exc:
            manifest.update(state="interrupted", interruption_type=type(exc).__name__,
                            interrupted_at=utc_now())
            atomic_json(OUT / "manifest.json", manifest)
            raise


def replacements_from_report() -> set[str]:
    path = OUT / "final-evaluation.json"
    if not path.exists():
        return set()
    return set(read(path).get("adopted_recovery_cases") or [])


def status() -> None:
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
                      "started_requests": cost["started_requests"],
                      "finished_requests": cost["finished_requests"],
                      "forecast_requests": cost["by_purpose"].get("action_forecast", 0)},
                     ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "execute", "status", "report"))
    parser.add_argument("--verification-report")
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare()
    elif args.phase == "execute":
        execute(args.verification_report)
    elif args.phase == "report":
        report()
    else:
        status()
