"""Frozen real-provider path and repeatability check on the real dataset.

Runs a fixed, small case list through the real ``detector batch``/``evaluate``
CLIs several times and records, per case and per repetition: terminal state,
decision authority, responsible step, wall-clock seconds and provider tokens.
The base is frozen before the first repetition (resolved config, detector
sources, protocol and case list are hashed) and re-verified after the last one,
so a result cannot silently belong to a different revision.

This is a small-scale path and consistency probe. It is not an accuracy
benchmark, and the cases have already been used for development.
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

DEFAULT_PROTOCOL = ROOT / "datasets" / "protocols" / "real_path_check.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_manifest() -> dict[str, str]:
    files: list[Path] = []
    for folder in ("detector", "evolution", "scripts"):
        files.extend(
            p for p in (ROOT / folder).rglob("*.py") if p.is_file()
        )
    for extra in ("pyproject.toml", "datasets/protocols/real_path_check.json",
                  "detector/configs/autodl.json"):
        files.append(ROOT / extra)
    return {
        p.relative_to(ROOT).as_posix(): _digest(p)
        for p in sorted(files) if p.is_file()
    }


def _case_metrics(run_root: Path, run_id: str, case_id: str) -> dict:
    case_dir = Path(run_root) / run_id / "cases" / safe_case_id(case_id)
    result_path = case_dir / "result.json"
    row: dict = {
        "case": case_id,
        "state": "missing_result",
        "authority": "invalid",
        "responsible_step": None,
        "elapsed_seconds": None,
        "tokens": None,
        "stages": {},
        "model": None,
    }
    if not result_path.exists():
        return row
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    elapsed_path = case_dir / "elapsed_seconds.json"
    elapsed = None
    if elapsed_path.exists():
        elapsed = json.loads(
            elapsed_path.read_text(encoding="utf-8")
        ).get("elapsed_seconds")
    tokens = 0
    provider_models = set()
    for provider in (result.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        usage = provider.get("usage") or {}
        tokens += int(usage.get("total_tokens") or 0)
        if provider.get("model"):
            provider_models.add(str(provider["model"]))
    row.update({
        "state": result.get("state"),
        "authority": result.get("decision_authority"),
        "responsible_step": result.get("responsible_step"),
        "runtime_valid": result.get("runtime_valid"),
        "elapsed_seconds": elapsed,
        "tokens": tokens,
        "stages": {
            key: value.get("state")
            for key, value in (result.get("stage_status") or {}).items()
            if isinstance(value, dict)
        },
        "model": ",".join(sorted(provider_models)) or None,
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    protocol_path = Path(args.protocol)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8-sig"))
    repetitions = int(args.repetitions or protocol["budget"]["repetitions"])
    workers = int(args.workers or protocol["budget"]["workers"])
    cases = [str(item) for item in protocol["cases"]]
    dataset_root = ROOT / protocol["dataset_root"]

    timestamp = datetime.now(timezone.utc)
    run_id = timestamp.strftime("real_path_%Y%m%dT%H%M%S%fZ")
    root = ROOT / "artifacts" / "real" / run_id
    runs_root = root / "runs"
    runs_root.mkdir(parents=True)
    out = ROOT / "artifacts" / "real"
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "status": "running",
        "passed": False,
        "started_at_utc": timestamp.isoformat(),
        "scope": protocol["purpose"],
        "run_dir": root.relative_to(ROOT).as_posix(),
        "limits": {
            "repetitions": repetitions, "cases": len(cases),
            "workers": workers,
            "case_timeout_seconds": protocol["budget"]["case_timeout_seconds"],
        },
    }

    def write_report() -> None:
        (out / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    write_report()
    try:
        config = load_config(ROOT / protocol["detector_config"])
    except (ValueError, OSError) as exc:
        report.update(status="blocked", reason=str(exc))
        write_report()
        print("Real configuration/credentials unavailable; see artifacts/real/summary.json")
        return 2

    # Freeze the base: pin the resolved routes, then hash every source file the
    # run depends on so the report cannot describe a different revision.
    config["case_timeout_seconds"] = int(
        protocol["budget"]["case_timeout_seconds"]
    )
    config.pop("intervention_registry", None)
    frozen_config_path = root / "detector.json"
    frozen_config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    before = _source_manifest()
    report["freeze"] = {
        "resolved_config_sha256": _digest(frozen_config_path),
        "protocol_sha256": _digest(protocol_path),
        "source_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "case_list": cases,
        "routes": {
            name: {
                key: route.get(key)
                for key in ("model", "endpoint", "votes", "vote_topup",
                            "max_tokens", "json_mode", "extra_body")
            }
            for name, route in config.items() if name.endswith("_route")
        },
    }
    cases_path = root / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    write_report()

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    common = [
        "--dataset-root", str(dataset_root),
        "--cases-file", str(cases_path),
        "--runs-root", str(runs_root),
    ]
    report["commands"] = []
    repetitions_rows: list[dict] = []
    write_report()

    for index in range(1, repetitions + 1):
        rep_id = f"rep{index}"
        for name, arguments in (
            ("batch", ["-m", "detector", "batch", *common,
                       "--run-id", rep_id, "--config",
                       str(frozen_config_path), "--workers", str(workers)]),
            ("evaluate", ["-m", "detector", "evaluate", *common,
                          "--run-id", rep_id]),
        ):
            label = f"{rep_id}_{name}"
            try:
                result = subprocess.run(
                    [sys.executable, *arguments], cwd=ROOT, env=env,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=3600,
                )
            except subprocess.TimeoutExpired:
                report.update(
                    status="failed",
                    reason=f"{label} exceeded the 3600-second wall-clock limit",
                )
                write_report()
                return 2
            (out / f"{label}.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            report["commands"].append(
                {"name": label, "exit_code": result.returncode}
            )
            print(f"{label}: exit={result.returncode}", flush=True)
            write_report()
            if result.returncode:
                report.update(status="failed", reason=f"{label} command failed")
                write_report()
                return 2

        evaluation_path = runs_root / rep_id / "evaluation.json"
        evaluation = json.loads(
            evaluation_path.read_text(encoding="utf-8-sig")
        )
        (out / f"{rep_id}_evaluation.json").write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rows = [
            _case_metrics(runs_root, rep_id, case) for case in cases
        ]
        repetitions_rows.append({
            "repetition": index,
            "metrics": evaluation.get("metrics"),
            "cases": rows,
        })

    # Recall-upgrade phase. The frozen routes use 3 judge votes, and a
    # unanimous judge-negative case never scans, so the recall/upgrade branch
    # would stay unexercised. Setting judge votes to 1 makes every
    # judge-negative case scan, which drives run_recall_scan and, when a
    # candidate appears, run_causal_upgrade on the real service.
    recall_cases = [str(item) for item in protocol.get("recall_cases") or []]
    report["recall_phase"] = {"cases": recall_cases, "ran": False}
    if recall_cases:
        recall_config = json.loads(json.dumps(config))
        recall_config["judge_route"]["votes"] = 1
        recall_config["judge_route"]["vote_topup"] = 0
        recall_config_path = root / "detector_recall.json"
        recall_config_path.write_text(
            json.dumps(recall_config, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        recall_cases_path = root / "recall_cases.json"
        recall_cases_path.write_text(
            json.dumps(recall_cases), encoding="utf-8"
        )
        recall_common = [
            "--dataset-root", str(dataset_root),
            "--cases-file", str(recall_cases_path),
            "--runs-root", str(runs_root),
        ]
        for name, arguments in (
            ("batch", ["-m", "detector", "batch", *recall_common,
                       "--config", str(recall_config_path),
                       "--run-id", "recall", "--workers", str(workers)]),
            ("evaluate", ["-m", "detector", "evaluate", *recall_common,
                          "--run-id", "recall"]),
        ):
            label = f"recall_{name}"
            result = subprocess.run(
                [sys.executable, *arguments], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=3600,
            )
            (out / f"{label}.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            report["commands"].append(
                {"name": label, "exit_code": result.returncode}
            )
            print(f"{label}: exit={result.returncode}", flush=True)
            write_report()
            if result.returncode:
                report.update(status="failed", reason=f"{label} command failed")
                write_report()
                return 2
        recall_rows = [
            _case_metrics(runs_root, "recall", case) for case in recall_cases
        ]
        recall_evaluation_path = runs_root / "recall" / "evaluation.json"
        if recall_evaluation_path.exists():
            # The forced single-vote judge changes verdicts, so these rows are
            # path-coverage evidence only, never an accuracy result.
            (out / "recall_evaluation.json").write_text(
                recall_evaluation_path.read_text(encoding="utf-8-sig"),
                encoding="utf-8",
            )
        fired = {
            stage for row in recall_rows for stage, state in row["stages"].items()
            if stage in {"recall_scanner", "causal_upgrade"}
            and str(state) not in {"not_needed", "not_rendered", "skipped"}
        }
        report["recall_phase"] = {
            "cases": recall_cases,
            "ran": bool(fired),
            "config_sha256": _digest(recall_config_path),
            "stages_observed": sorted(fired),
            "rows": recall_rows,
        }

    # Repeatability: a case is consistent when its (state, authority,
    # responsible_step) triple is identical in every repetition.
    by_case: dict[str, list[dict]] = {case: [] for case in cases}
    for repetition in repetitions_rows:
        for row in repetition["cases"]:
            by_case[row["case"]].append(row)
    consistency = []
    for case, rows in by_case.items():
        triples = {
            (row["state"], row["authority"], row["responsible_step"])
            for row in rows
        }
        consistency.append({
            "case": case,
            "consistent": len(triples) == 1,
            "states": [row["state"] for row in rows],
            "authorities": [row["authority"] for row in rows],
            "responsible_steps": [row["responsible_step"] for row in rows],
        })

    all_rows = [row for repetition in repetitions_rows
                for row in repetition["cases"]]
    elapsed = [
        row["elapsed_seconds"] for row in all_rows
        if isinstance(row["elapsed_seconds"], (int, float))
    ]
    token_rows = [
        row["tokens"] for row in all_rows if isinstance(row["tokens"], int)
    ]
    report["repetitions"] = repetitions_rows
    report["consistency"] = consistency
    report["summary"] = {
        "consistent_cases": sum(1 for row in consistency if row["consistent"]),
        "total_cases": len(consistency),
        "agreement_rate": (
            sum(1 for row in consistency if row["consistent"])
            / len(consistency) if consistency else None
        ),
        "unresolved": sum(1 for row in all_rows if row["state"] == "unresolved"),
        "missing_results": sum(
            1 for row in all_rows if row["state"] == "missing_result"
        ),
        "authoritative": sum(
            1 for row in all_rows if row["authority"] == "authoritative"
        ),
        "stages_fired": sorted({
            f"{stage}:{state}"
            for row in all_rows for stage, state in row["stages"].items()
            if str(state) not in {"not_needed", "not_rendered", "skipped"}
        }),
        "elapsed_seconds": {
            "min": min(elapsed) if elapsed else None,
            "max": max(elapsed) if elapsed else None,
            "mean": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        },
        "tokens": {
            "total": sum(token_rows) if token_rows else None,
            "mean_per_case": (
                round(sum(token_rows) / len(token_rows), 1) if token_rows else None
            ),
        },
    }
    recall_rows = (report.get("recall_phase") or {}).get("rows") or []
    report["summary"]["recall_phase"] = {
        "cases": len(recall_rows),
        "unresolved": sum(1 for row in recall_rows if row["state"] == "unresolved"),
        "scanner_ran": sum(
            1 for row in recall_rows
            if str(row["stages"].get("recall_scanner")) not in
            {"None", "not_needed", "not_rendered", "skipped"}
        ),
        "upgrade_ran": sum(
            1 for row in recall_rows
            if str(row["stages"].get("causal_upgrade")) not in
            {"None", "not_needed", "not_rendered", "skipped"}
        ),
    }
    after = _source_manifest()
    report["source_unchanged"] = before == after
    # A pass means the frozen base ran to completion with no unresolved or
    # missing case, byte-identical sources across the run, and the
    # recall/upgrade branch actually exercised. It deliberately says nothing
    # about accuracy: the case list is small and already exposed.
    recall_summary = report["summary"]["recall_phase"]
    report.update(
        status="completed",
        passed=(
            report["source_unchanged"]
            and report["summary"]["unresolved"] == 0
            and report["summary"]["missing_results"] == 0
            and recall_summary["scanner_ran"] > 0
            and recall_summary["upgrade_ran"] > 0
        ),
        finished_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    write_report()
    print(json.dumps({
        "passed": report["passed"],
        "consistency": report["summary"]["agreement_rate"],
        "unresolved": report["summary"]["unresolved"],
        "stages_fired": report["summary"]["stages_fired"],
        "recall_phase": report["summary"]["recall_phase"],
    }, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
