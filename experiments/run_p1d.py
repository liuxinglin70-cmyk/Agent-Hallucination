"""Phase 1-D live runs: the frozen detector over canary / frozen cases.

One process per case set (``--set canary`` or ``--set frozen``). The
frozen deployment form from the split manifest is asserted before any
call: the resolved config fingerprint must equal the decisive random100
run's, so the fresh evaluation runs the same detector that produced the
evolution pool.

Every case is persisted to the run tree as it completes
(``record_case_run``), so an interrupted run resumes by re-running the
same command. The evaluator-side gold join lands in
``runs/<run_id>/eval_gold.json`` after the last case.

Usage:
    python -m experiments.run_p1d --set canary
    python -m experiments.run_p1d --set frozen
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS
from sehd_rise2.detector.result import prediction_state
from sehd_rise2.detector.runs import (
    config_fingerprint,
    default_runs_root,
    new_run_id,
    record_case_run,
    summarize_run,
    write_run_meta,
)

MANIFEST = Path("datasets/eval_splits/v1/manifest.json")
DATASET_ROOT = Path("datasets/raw/agenthallu/AgentHallu")
RUN_PREFIX = {"canary": "p1d_canary", "frozen": "p1d_frozen100"}
WORKERS = 20


def load_gold(case_path: str) -> tuple[dict, bool | None, int | None]:
    raw = json.loads((DATASET_ROOT / case_path).read_text(encoding="utf-8"))
    value = raw.get("is_hallucination")
    if isinstance(value, bool):
        gold = value
    else:
        gold = {"true": True, "false": False}.get(
            str(value).strip().lower(),
        )
    raw_step = raw.get("hallucination_step")
    gold_step = int(raw_step) if str(raw_step or "").isdigit() else None
    clean = {
        key: item for key, item in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }
    return clean, gold, gold_step


def case_list(manifest: dict, which: str) -> list[str]:
    if which == "canary":
        return [
            str(row["case_id"])
            for row in manifest["canary"]["cases"]
        ]
    return [str(row["case_id"]) for row in manifest["frozen_eval"]]


def gold_join(run_id: str, cases: list[str]) -> dict:
    rows = []
    for case in sorted(cases):
        case_dir = (
            default_runs_root() / run_id / "cases"
            / case.replace("/", "__")
        )
        result = json.loads(
            (case_dir / "result.json").read_text(encoding="utf-8"),
        )
        _, gold, gold_step = load_gold(case)
        rows.append({
            "case": case,
            "gold": gold,
            "gold_step": gold_step,
            "state": result.get("state"),
            "prediction": prediction_state(result),
            "responsible_step": result.get("responsible_step"),
            "runtime_valid": result.get("runtime_valid"),
            "seconds": (json.loads(
                (case_dir / "audit.json").read_text(encoding="utf-8"),
            ) or {}).get("elapsed_seconds"),
            "tokens": sum(
                int((meta.get("usage") or {}).get("total_tokens") or 0)
                for meta in (result.get("providers") or {}).values()
                if isinstance(meta, dict)
            ),
        })
    decisive = [row for row in rows if row["prediction"] is not None]
    correct = sum(
        1 for row in decisive if row["prediction"] == row["gold"]
    )
    report = {
        "schema_version": "sehd2.eval_gold.v1",
        "run_id": run_id,
        "cases": rows,
        "decisive_cases": len(decisive),
        "unresolved_cases": len(rows) - len(decisive),
        "polarity_accuracy_decisive": (
            round(correct / len(decisive), 4) if decisive else None
        ),
        "tp": sum(
            1 for row in decisive
            if row["gold"] and row["prediction"] is True
        ),
        "fp": sum(
            1 for row in decisive
            if not row["gold"] and row["prediction"] is True
        ),
        "fn": sum(
            1 for row in decisive
            if row["gold"] and row["prediction"] is False
        ),
        "tn": sum(
            1 for row in decisive
            if not row["gold"] and row["prediction"] is False
        ),
    }
    path = default_runs_root() / run_id / "eval_gold.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _drop_transport_failures(run_dir: Path) -> int:
    """Remove recorded unresolved cases whose failure was transport-side.

    Only provider/transport errors are retried — an evidence-view budget
    failure is a deterministic frozen-detector limitation and stays
    recorded as an honest unresolved outcome.
    """
    dropped = 0
    for result_path in sorted(run_dir.glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("state") != "unresolved":
            continue
        reasons = result.get("authority_failure_reasons") or []
        transport = any(
            str(reason.get("error_type")) in {
                "provider_error", "transport_error",
            }
            for reason in reasons
        )
        if transport:
            shutil.rmtree(result_path.parent)
            dropped += 1
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--set", choices=sorted(RUN_PREFIX))
    select.add_argument(
        "--cases-file", type=Path,
        help="explicit JSON case list (used by the Phase 2 orchestrator)",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST,
        help="split manifest carrying the frozen deployment form",
    )
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument(
        "--detect-only", action="store_true",
        help="record cases only; the caller owns run meta and gold join"
             " (used by the Phase 2 orchestrator)",
    )
    parser.add_argument(
        "--retry-unresolved", action="store_true",
        help="delete recorded cases whose unresolved state came from a "
             "transport/provider error so the resumable loop re-detects "
             "them (deterministic authority failures stay recorded)",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    deployment = manifest["deployment"]["detector"]
    import os
    saved = {
        key: os.environ[key] for key in deployment["env"]
        if key in os.environ
    }
    os.environ.update(deployment["env"])
    resolved = load_config()
    fingerprint = config_fingerprint(resolved)
    if fingerprint != deployment["config_fingerprint"]:
        print(
            f"FAIL frozen config fingerprint {fingerprint} != "
            f"{deployment['config_fingerprint']}"
        )
        return 1
    detector = Detector(config=resolved)
    for key, value in saved.items():
        os.environ[key] = value

    if args.cases_file is not None:
        cases = json.loads(args.cases_file.read_text(encoding="utf-8"))
        if not args.run_id:
            print("--cases-file requires --run-id")
            return 1
    else:
        cases = case_list(manifest, args.set)
    run_id = args.run_id or new_run_id(RUN_PREFIX[args.set])
    run_dir = default_runs_root() / run_id / "cases"
    if args.retry_unresolved:
        retried = _drop_transport_failures(run_dir)
        print(
            f"retry: dropped {retried} transport-failed case records",
            flush=True,
        )
    pending = [
        case for case in cases
        if not (run_dir / case.replace("/", "__") / "result.json").exists()
    ]
    print(
        f"{args.set}: {len(cases)} cases ({len(pending)} to run, "
        f"{len(cases) - len(pending)} already recorded), run_id={run_id}, "
        f"fingerprint={fingerprint}", flush=True,
    )

    def run(case: str) -> str:
        clean, gold, _ = load_gold(case)
        started = time.monotonic()
        try:
            result = detector.detect(clean, case_id=case)
        except Exception as exc:  # noqa: BLE001
            print(f"  [EXC ] {case}: {type(exc).__name__}: {exc}", flush=True)
            return case
        elapsed = round(time.monotonic() - started, 1)
        record_case_run(
            runs_root=default_runs_root(), run_id=run_id, case_id=case,
            input_record=clean, result=result, elapsed_seconds=elapsed,
        )
        print(
            f"  [case] {case:<32} gold={str(gold):<5} "
            f"state={result.get('state'):<15} sec={elapsed}", flush=True,
        )
        return case

    wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run, case) for case in pending]
        for future in as_completed(futures):
            future.result()

    missing = [
        case for case in cases
        if not (run_dir / case.replace("/", "__") / "result.json").exists()
    ]
    if missing:
        print(f"incomplete: {len(missing)} cases without records", flush=True)
        return 1
    if args.detect_only:
        print(f"detection complete for {len(cases)} cases", flush=True)
        return 0
    write_run_meta(
        runs_root=default_runs_root(), run_id=run_id, config=resolved,
        case_ids=cases,
    )
    summarize_run(default_runs_root(), run_id)
    report = gold_join(run_id, cases)
    condensed = {
        key: value for key, value in report.items() if key != "cases"
    }
    print(
        f"done run_id={run_id} wall={time.monotonic() - wall:.0f}s\n"
        + json.dumps(condensed, ensure_ascii=False, indent=1), flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
