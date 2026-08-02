"""Concurrent, resumable execution for the standalone detector."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from ..core.paca_tcl.detector import FORBIDDEN_INPUT_FIELDS, detect_record
from ..utils.ocse_packet import canonical_json, stable_hash


BATCH_SCHEMA_VERSION = "agenthallu.standalone_detector_batch.v1"


def load_batch_records(path: Path) -> dict[str, dict[str, Any]]:
    """Load records from one JSON/JSONL file or a directory of JSON files."""
    path = Path(path)
    if path.is_dir():
        records: dict[str, dict[str, Any]] = {}
        for record_path in sorted(path.rglob("*.json")):
            value = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or "history" not in value:
                continue
            case_id = record_path.relative_to(path).with_suffix("").as_posix()
            records[case_id] = value
        if not records:
            raise ValueError(f"no trajectory JSON files found under {path}")
        return records

    if path.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("batch input must contain a JSON object")
        if isinstance(value.get("cases"), list):
            records = {}
            for index, item in enumerate(value["cases"], start=1):
                if not isinstance(item, dict):
                    raise ValueError(f"batch manifest case {index} must be an object")
                case_id = str(item.get("case_id") or f"case_{index:04d}")
                record_path = Path(str(item.get("record_path") or ""))
                if not record_path.is_absolute():
                    record_path = path.parent / record_path
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or "history" not in record:
                    raise ValueError(f"batch manifest record is not a trajectory: {record_path}")
                if case_id in records:
                    raise ValueError(f"duplicate batch case_id: {case_id}")
                records[case_id] = record
            return records
        if "history" in value:
            rows = [value]
        elif all(isinstance(row, dict) for row in value.values()):
            return {str(case_id): dict(row) for case_id, row in value.items()}
        else:
            raise ValueError("batch JSON mapping values must be trajectory objects")

    records = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"batch record {index} must be an object")
        case_id = str(row.get("case_id") or f"case_{index:04d}")
        record = row.get("record") if isinstance(row.get("record"), dict) else row
        if case_id in records:
            raise ValueError(f"duplicate batch case_id: {case_id}")
        records[case_id] = dict(record)
    return records


def label_free_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Remove dataset annotations before crossing the detector boundary."""
    return {
        str(key): value for key, value in record.items()
        if str(key) not in FORBIDDEN_INPUT_FIELDS
    }


def _case_directory(root: Path, case_id: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id).strip("._") or "case"
    digest = stable_hash(case_id)[:12]
    return root / "cases" / f"{slug[:80]}_{digest}"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.{id(value)}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _completed_prediction(
    case_dir: Path, *, case_id: str, input_hash: str, config_hash: str,
) -> dict[str, Any] | None:
    completion_path = case_dir / "completion.json"
    prediction_path = case_dir / "prediction.json"
    if not completion_path.is_file() or not prediction_path.is_file():
        return None
    try:
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(completion, dict) or not isinstance(prediction, dict):
        return None
    expected = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "status": "completed",
        "case_id": case_id,
        "input_hash": input_hash,
        "config_hash": config_hash,
        "prediction_hash": stable_hash(prediction),
    }
    if any(completion.get(key) != value for key, value in expected.items()):
        return None
    return prediction


def run_batch(
    *, records: Mapping[str, Mapping[str, Any]], config: dict[str, Any],
    output_dir: Path, max_parallel: int = 10, resume: bool = True,
) -> dict[str, Any]:
    """Detect independent trajectories concurrently with hash-bound resume."""
    if max_parallel < 1:
        raise ValueError("max_parallel must be at least 1")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_hash = stable_hash(config)
    started = time.time()
    results: dict[str, dict[str, Any]] = {}
    statuses: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, dict[str, Any], str, Path]] = []

    for case_id, raw_record in records.items():
        clean = label_free_record(raw_record)
        input_hash = stable_hash(clean)
        case_dir = _case_directory(output_dir, str(case_id))
        resumed = _completed_prediction(
            case_dir, case_id=str(case_id), input_hash=input_hash,
            config_hash=config_hash,
        ) if resume else None
        if resumed is not None:
            results[str(case_id)] = resumed
            statuses[str(case_id)] = {
                "status": "resumed", "case_dir": str(case_dir),
                "input_hash": input_hash,
            }
        else:
            pending.append((str(case_id), clean, input_hash, case_dir))

    def execute(item: tuple[str, dict[str, Any], str, Path]) -> tuple[str, dict[str, Any]]:
        case_id, record, input_hash, case_dir = item
        case_started = time.time()
        try:
            prediction = detect_record(
                record=record, config=config, case_id=case_id,
                output_dir=case_dir / "trace",
            )
            _atomic_json(case_dir / "prediction.json", prediction)
            completion = {
                "schema_version": BATCH_SCHEMA_VERSION,
                "status": "completed",
                "case_id": case_id,
                "input_hash": input_hash,
                "config_hash": config_hash,
                "prediction_hash": stable_hash(prediction),
                "duration_seconds": round(time.time() - case_started, 3),
            }
            _atomic_json(case_dir / "completion.json", completion)
            return case_id, {
                "status": "completed", "prediction": prediction,
                "case_dir": str(case_dir), "input_hash": input_hash,
                "duration_seconds": completion["duration_seconds"],
            }
        except Exception as exc:
            failure = {
                "schema_version": BATCH_SCHEMA_VERSION,
                "status": "failed", "case_id": case_id,
                "input_hash": input_hash, "config_hash": config_hash,
                "error_type": type(exc).__name__, "error": str(exc)[:2000],
                "duration_seconds": round(time.time() - case_started, 3),
            }
            _atomic_json(case_dir / "failure.json", failure)
            return case_id, {**failure, "case_dir": str(case_dir)}

    if pending:
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(pending))) as executor:
            futures = {executor.submit(execute, item): item[0] for item in pending}
            for future in as_completed(futures):
                case_id, status = future.result()
                statuses[case_id] = status
                if status["status"] == "completed":
                    results[case_id] = status.pop("prediction")

    ordered_ids = list(map(str, records))
    predictions = [
        {"case_id": case_id, "prediction": results[case_id]}
        for case_id in ordered_ids if case_id in results
    ]
    state_counts = Counter(
        str(row["prediction"].get("state") or "unresolved") for row in predictions
    )
    summary = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "config_hash": config_hash,
        "requested_case_count": len(records),
        "completed_case_count": len(predictions),
        "failed_case_count": sum(
            status.get("status") == "failed" for status in statuses.values()
        ),
        "resumed_case_count": sum(
            status.get("status") == "resumed" for status in statuses.values()
        ),
        "max_parallel": max_parallel,
        "duration_seconds": round(time.time() - started, 3),
        "state_counts": dict(sorted(state_counts.items())),
        "cases": {case_id: statuses[case_id] for case_id in ordered_ids},
    }
    _atomic_json(output_dir / "predictions.json", predictions)
    _atomic_json(output_dir / "summary.json", summary)
    return summary
