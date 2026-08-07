"""Concurrent, resumable execution for the standalone detector."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.paca_tcl.detector import FORBIDDEN_INPUT_FIELDS, detect_record
from ..utils.ocse_packet import canonical_json, stable_hash
from .replica_aggregation import POLICIES, aggregate_replicas


BATCH_SCHEMA_VERSION = "agenthallu.standalone_detector_batch.v3"
REPLICA_REPORT_SCHEMA_VERSION = "agenthallu.decision_replicas.v1"
MAX_REPLICAS = 5

# Bounded in-process retry for transient provider failures (timeouts, rate
# limits, connection errors). Deterministic failures (schema violations,
# runtime_valid=false) are never retried.
_TRANSIENT_RETRIES = 2
_TRANSIENT_BACKOFF_SECONDS = 1.0
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "rate limit",
    "429",
    "connection",
    "temporarily unavailable",
    "service unavailable",
)


def _is_transient_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


def _detect_with_transient_retry(detect_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Run ``detect_record`` with a bounded retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(_TRANSIENT_RETRIES + 1):
        try:
            return detect_record(**dict(detect_kwargs))
        except Exception as exc:  # noqa: BLE001 - retried/raised below
            last_exc = exc
            if not _is_transient_error(exc) or attempt >= _TRANSIENT_RETRIES:
                raise
            time.sleep(_TRANSIENT_BACKOFF_SECONDS * (attempt + 1))
    assert last_exc is not None
    raise last_exc


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
    genome_hash: str | None, policy_genome_hash: str | None = None,
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
        "genome_hash": genome_hash,
        "policy_genome_hash": policy_genome_hash,
        "prediction_hash": stable_hash(prediction),
    }
    if policy_genome_hash is not None:
        expected["policy_genome_hash"] = policy_genome_hash
    if any(completion.get(key) != value for key, value in expected.items()):
        return None
    if prediction.get("runtime_valid") is not True:
        return None
    return prediction


def run_batch(
    *, records: Mapping[str, Mapping[str, Any]], config: dict[str, Any],
    output_dir: Path, max_parallel: int = 10, resume: bool = True,
    genome: Any | None = None, genome_source: str | None = None,
    policy_genome: Any | None = None,
) -> dict[str, Any]:
    """Detect independent trajectories concurrently with hash-bound resume."""
    if max_parallel < 1:
        raise ValueError("max_parallel must be at least 1")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_hash = stable_hash(config)
    genome_hash = getattr(genome, "content_hash", None)
    policy_genome_hash = getattr(policy_genome, "content_hash", None)
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
            config_hash=config_hash, genome_hash=genome_hash,
            policy_genome_hash=policy_genome_hash,
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
            detect_kwargs: dict[str, Any] = {
                "record": record, "config": config, "case_id": case_id,
                "output_dir": case_dir / "trace",
            }
            if genome is not None:
                detect_kwargs["genome"] = genome
                detect_kwargs["genome_source"] = genome_source
            if policy_genome is not None:
                detect_kwargs["policy_genome"] = policy_genome
            prediction = _detect_with_transient_retry(detect_kwargs)
            if genome is not None:
                provenance = prediction.get("provenance")
                if not isinstance(provenance, dict):
                    provenance = {}
                prediction = {
                    **prediction,
                    "provenance": {
                        **provenance,
                        "genome_hash": genome_hash,
                        "genome_source": genome_source,
                    },
                }
            if prediction.get("runtime_valid") is not True:
                raise RuntimeError(
                    "detector returned runtime_valid=false; not a completed "
                    "prediction"
                )
            _atomic_json(case_dir / "prediction.json", prediction)
            completion = {
                "schema_version": BATCH_SCHEMA_VERSION,
                "status": "completed",
                "case_id": case_id,
                "input_hash": input_hash,
                "config_hash": config_hash,
                "genome_hash": genome_hash,
                "policy_genome_hash": policy_genome_hash,
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
        "genome_hash": genome_hash,
        "policy_genome_hash": policy_genome_hash,
        "provenance": ({
            "genome_hash": genome_hash, "genome_source": genome_source,
            "policy_genome_hash": policy_genome_hash,
        } if (genome is not None or policy_genome is not None) else None),
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


# ============================================================
# Test-time-scaling replica orchestration (library-level)
# ============================================================


def collect_replica_predictions(
    replica_roots: Sequence[Path], case_ids: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Collect per-case replica predictions from each replica directory.

    ``run_batch`` persists the rows under ``predictions.json``; the returned
    summary intentionally carries no ``predictions`` key.
    """
    predictions_by_case: dict[str, list[dict[str, Any]]] = {
        str(case_id): [] for case_id in case_ids
    }
    for replica_root in replica_roots:
        predictions_path = Path(replica_root) / "predictions.json"
        if not predictions_path.is_file():
            continue
        for row in json.loads(predictions_path.read_text(encoding="utf-8")):
            case_id = str(row.get("case_id"))
            if case_id in predictions_by_case:
                predictions_by_case[case_id].append(row.get("prediction") or {})
    return predictions_by_case


def _all_routes_deterministic(config: Mapping[str, Any]) -> bool:
    """Whether every provider route runs at temperature 0 (or unset).

    Detector routes live as top-level ``*_route`` keys. With all routes
    deterministic, k replicas collapse into k identical cache copies, so
    replica diversity is impossible without an explicit temperature change.
    """
    temperatures = [
        route.get("temperature")
        for name, route in config.items()
        if name.endswith("_route") and isinstance(route, Mapping)
    ]
    return bool(temperatures) and all(
        temperature in (0, 0.0, None) for temperature in temperatures
    )


def run_replicated_batch(
    *,
    records: Mapping[str, Mapping[str, Any]],
    config: dict[str, Any],
    output_root: Path,
    replicas: int = 1,
    replica_policy: str = "majority",
    max_parallel: int = 10,
    resume: bool = True,
    genome: Any | None = None, genome_source: str | None = None,
    policy_genome: Any | None = None,
) -> dict[str, Any]:
    """Run the detector k times per case and aggregate fail-closed.

    Each replica executes under an isolated global provider-cache root so
    identical payloads do not collapse into one cached response; replicas are
    strictly serial because ``PACA_GLOBAL_CACHE_ROOT`` is process-global and
    ``run_batch`` already parallelizes across cases internally. Verdicts are
    combined with ``aggregate_replicas`` (majority/consensus, fail-closed).

    Degradation guard: when every route is deterministic (temperature 0 or
    unset), requesting replicas > 1 would burn k identical provider calls,
    so the run degrades to a single replica and records the reason. The
    temperature is never raised implicitly.

    ``run_batch`` itself is untouched: its hash-bound resume and artifact
    schema stay exactly as before.
    """
    if not 1 <= replicas <= MAX_REPLICAS:
        raise ValueError(f"replicas must be in 1..{MAX_REPLICAS}")
    if replica_policy not in POLICIES:
        raise ValueError(f"unknown replica policy: {replica_policy!r}")
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    replica_degradation: dict[str, Any] | None = None
    effective_replicas = replicas
    if replicas > 1 and _all_routes_deterministic(config):
        effective_replicas = 1
        replica_degradation = {
            "reason": "temperature_zero",
            "requested": replicas,
        }

    original_global_cache = os.environ.get("PACA_GLOBAL_CACHE_ROOT")
    replica_roots: list[Path] = []
    try:
        for index in range(effective_replicas):
            replica_root = output_root / f"replica_{index}"
            # Isolate the global provider cache per replica so identical
            # payloads cannot collapse k runs into one cached response.
            os.environ["PACA_GLOBAL_CACHE_ROOT"] = str(
                replica_root / "provider_cache"
            )
            batch_kwargs: dict[str, Any] = {
                "records": records,
                "config": config,
                "output_dir": replica_root,
                "max_parallel": max_parallel,
                "resume": resume,
            }
            if genome is not None:
                batch_kwargs["genome"] = genome
                batch_kwargs["genome_source"] = genome_source
            if policy_genome is not None:
                batch_kwargs["policy_genome"] = policy_genome
            run_batch(**batch_kwargs)
            replica_roots.append(replica_root)
    finally:
        if original_global_cache is None:
            os.environ.pop("PACA_GLOBAL_CACHE_ROOT", None)
        else:
            os.environ["PACA_GLOBAL_CACHE_ROOT"] = original_global_cache

    case_ids = [str(case_id) for case_id in records]
    predictions_by_case = collect_replica_predictions(replica_roots, case_ids)

    cases: list[dict[str, Any]] = []
    for case_id in case_ids:
        replica_predictions = predictions_by_case[case_id]
        if len(replica_predictions) < effective_replicas:
            aggregate = {
                "state": "unresolved",
                "policy": replica_policy,
                "replica_count": len(replica_predictions),
                "invalid_replica_indexes": [],
                "replica_states": [
                    str(prediction.get("state") or "unresolved")
                    for prediction in replica_predictions
                ],
                "unanimous": False,
                "runtime_valid": False,
                "note": "incomplete_replicas",
            }
        else:
            aggregate = aggregate_replicas(
                replica_predictions, policy=replica_policy
            )
        cases.append({"case_id": case_id, "aggregate": aggregate})

    report = {
        "schema_version": REPLICA_REPORT_SCHEMA_VERSION,
        "config_hash": stable_hash(config),
        "genome_hash": getattr(genome, "content_hash", None),
        "policy_genome_hash": getattr(policy_genome, "content_hash", None),
        "provenance": ({
            "genome_hash": getattr(genome, "content_hash", None),
            "policy_genome_hash": getattr(policy_genome, "content_hash", None),
            "genome_source": genome_source,
        } if (genome is not None or policy_genome is not None) else None),
        "k": replicas,
        "replicas_effective": effective_replicas,
        "replica_degradation": replica_degradation,
        # Kept in the library report so aggregated_report.json and the CLI
        # artifact share one field set under the same schema_version.
        "deterministic_route_warning": _all_routes_deterministic(config),
        "policy": replica_policy,
        "case_count": len(cases),
        "unanimous_cases": sum(
            case["aggregate"]["unanimous"] for case in cases
        ),
        "cases": cases,
    }
    _atomic_json(output_root / "aggregated_report.json", report)
    return report
