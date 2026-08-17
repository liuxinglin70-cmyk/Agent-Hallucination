"""Minimal batch runner: runs cases concurrently and records the run tree."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector_v2.api import Detector
from detector_v2.config import load_config
from detector_v2.dataset import load_case

DEFAULT_RUNS_ROOT = Path("runs_v2")


def new_run_id(prefix: str = "detector_v2") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def safe_case_id(case_id: str) -> str:
    return (
        str(case_id).replace("/", "__").replace("\\", "__")
        .replace(":", "_") or "case"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _is_transport_unresolved(result: Mapping[str, Any]) -> bool:
    if result.get("state") != "unresolved":
        return False
    reasons = result.get("authority_failure_reasons") or []
    return any(
        str(reason.get("error_type")) in {"provider_error", "invalid_json"}
        for reason in reasons
        if isinstance(reason, Mapping)
    )


def detect_with_transport_retry(
    detector: Detector,
    record: Mapping[str, Any],
    *,
    case_id: str,
    artifacts: Mapping[str, Any] | None,
    interventions: Mapping[str, Any] | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """Whole-detection retry for transport-only unresolved results."""
    result: dict[str, Any] = {}
    for _ in range(max(1, attempts)):
        if interventions is None:
            result = detector.detect(
                record, case_id=case_id, artifacts=artifacts,
            )
        else:
            result = detector.detect(
                record, case_id=case_id, artifacts=artifacts,
                interventions=interventions,
            )
        if not _is_transport_unresolved(result):
            return result
    return result


def record_case(
    *,
    runs_root: str | Path,
    run_id: str,
    case_id: str,
    input_record: Mapping[str, Any],
    result: Mapping[str, Any],
    elapsed_seconds: float,
) -> Path:
    case_dir = Path(runs_root) / run_id / "cases" / safe_case_id(case_id)
    _write_json(case_dir / "input.json", input_record)
    _write_json(case_dir / "result.json", result)
    (case_dir / "elapsed_seconds.json").write_text(
        json.dumps({"elapsed_seconds": elapsed_seconds}) + "\n",
        encoding="utf-8",
    )
    return case_dir


def run_batch(
    *,
    dataset_root: str | Path,
    cases: Sequence[str],
    run_id: str | None = None,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    config: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, Any] | None = None,
    interventions: Mapping[str, Any] | None = None,
    workers: int = 8,
) -> dict[str, Any]:
    """Run *cases* concurrently; skip cases whose result already exists."""
    config = config or load_config()
    detector = Detector(config)
    run_id = run_id or new_run_id()
    case_root = Path(runs_root) / run_id / "cases"
    summaries: list[dict[str, Any]] = []

    def run(case_id: str) -> dict[str, Any]:
        out_path = case_root / safe_case_id(case_id) / "result.json"
        if out_path.exists():
            result = json.loads(out_path.read_text(encoding="utf-8-sig"))
            summaries.append(result)
            return result
        record, _gold = load_case(dataset_root, case_id)
        started = time.monotonic()
        result = detect_with_transport_retry(
            detector, record, case_id=case_id, artifacts=artifacts,
            interventions=interventions,
        )
        record_case(
            runs_root=runs_root,
            run_id=run_id,
            case_id=case_id,
            input_record=record,
            result=result,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        summaries.append(result)
        print(
            f"  [{case_id}] state={result.get('state')} "
            f"authority={result.get('decision_authority')}",
            flush=True,
        )
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, case_id) for case_id in cases]
        for future in as_completed(futures):
            future.result()

    summary = {
        "schema_version": "detector_v2.run_summary.v1",
        "run_id": run_id,
        "cases": list(cases),
        "completed": len(summaries),
        "state_counts": {
            state: sum(
                1 for row in summaries if row.get("state") == state
            )
            for state in ("verified_error", "verified_clean", "unresolved")
        },
    }
    _write_json(Path(runs_root) / run_id / "summary.json", summary)
    return summary
