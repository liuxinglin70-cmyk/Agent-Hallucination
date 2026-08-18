"""Command line entry for detector_v2.

Commands:
    python -m detector_v2 detect  --record FILE --output-dir OUT
    python -m detector_v2 batch   --dataset-root DIR --cases-file FILE
    python -m detector_v2 evaluate --dataset-root DIR --cases-file FILE \
                                   --run-id ID [--runs-root runs_v2]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from detector_v2.api import Detector
from detector_v2.artifacts import load_artifact_registry
from detector_v2.config import load_config
from detector_v2.interventions import load_intervention_registry
from detector_v2.dataset import project_record
from detector_v2.evaluation import evaluate_run
from detector_v2.runner import DEFAULT_RUNS_ROOT, new_run_id, run_batch


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _load_artifacts(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return load_artifact_registry(path)


def _load_interventions(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return load_intervention_registry(path, require_executable_bounds=True)


def _write_output(output_dir: str, payload: dict[str, Any]) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def cmd_detect(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    detector = Detector(config)
    raw = _load_json(args.record)
    record = project_record(raw)
    result = detector.detect(
        record,
        case_id=args.case_id,
        artifacts=_load_artifacts(args.artifacts),
        interventions=_load_interventions(args.interventions),
    )
    _write_output(args.output_dir, result)
    print(
        f"case={args.case_id} state={result.get('state')} "
        f"authority={result.get('decision_authority')} "
        f"responsible_step={result.get('responsible_step')}",
    )
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    cases = _load_json(args.cases_file)
    if not isinstance(cases, list) or not all(
        isinstance(item, str) for item in cases
    ):
        raise SystemExit("--cases-file must contain a JSON array of case ids")
    config = load_config(args.config)
    artifacts = _load_artifacts(args.artifacts)
    interventions = _load_interventions(args.interventions)
    run_id = args.run_id or new_run_id()
    print(
        f"batch run {run_id}: {len(cases)} cases, workers={args.workers}",
        flush=True,
    )
    summary = run_batch(
        dataset_root=args.dataset_root,
        cases=cases,
        run_id=run_id,
        runs_root=args.runs_root,
        config=config,
        artifacts=artifacts,
        interventions=interventions,
        workers=args.workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    cases = _load_json(args.cases_file)
    report = evaluate_run(
        runs_root=args.runs_root,
        run_id=args.run_id,
        dataset_root=args.dataset_root,
        cases=cases,
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    out = Path(args.runs_root) / args.run_id / "evaluation.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nevaluation -> {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="detector_v2",
        description="Outcome-oriented hallucination detector (V2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="detect one record")
    p_detect.add_argument("--record", required=True)
    p_detect.add_argument("--output-dir", default="detector_v2_out")
    p_detect.add_argument("--case-id", default="case")
    p_detect.add_argument("--config")
    p_detect.add_argument("--artifacts")
    p_detect.add_argument("--interventions")
    p_detect.set_defaults(func=cmd_detect)

    p_batch = sub.add_parser("batch", help="run a batch of cases")
    p_batch.add_argument("--dataset-root", required=True)
    p_batch.add_argument("--cases-file", required=True)
    p_batch.add_argument("--run-id")
    p_batch.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    p_batch.add_argument("--config")
    p_batch.add_argument("--artifacts")
    p_batch.add_argument("--interventions")
    p_batch.add_argument("--workers", type=int, default=8)
    p_batch.set_defaults(func=cmd_batch)

    p_eval = sub.add_parser("evaluate", help="join gold and compute metrics")
    p_eval.add_argument("--dataset-root", required=True)
    p_eval.add_argument("--cases-file", required=True)
    p_eval.add_argument("--run-id", required=True)
    p_eval.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    p_eval.set_defaults(func=cmd_evaluate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
