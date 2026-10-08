"""Batch runner: bounded scheduling, per-attempt records, resumable runs.

Three things this file is responsible for, and one it deliberately is not.

It *is* responsible for turning a case list into a run tree that can be read
later without ambiguity: one directory per case, one file per attempt, and a
manifest that says which code, config, registry and case set produced them.
An attempt that was interrupted mid-write must never be readable as a
measurement, so results are written to a fresh attempt directory and the
canonical ``result.json`` is only ever replaced by a completed attempt.

It *is* responsible for the run stopping for the right reason. A credential
that the provider rejects, a quota that is exhausted, or a run-scoped deadline
are not per-case accidents: continuing to submit cases after one of them
converts a single diagnosable failure into thousands of identical ones and
burns the wall clock the deadline was meant to protect. Those conditions stop
the batch and are recorded as the run's stop reason.

It *is* responsible for not launching work it cannot finish. Submission is
bounded by the worker count (an unbounded submit list queues the whole corpus
and makes a stop meaningless) and, when a deadline is given, no case starts
after it.

It is *not* responsible for deciding what a result means. Whether a row is a
decision, a degradation, or an outage is answered by
``detector.measurement.measurement_status``; this file only records and counts
that answer.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from detector.api import Detector
from detector.config import load_config
from detector.dataset import load_case
from detector.identity import build_execution_identity, digest, source_identity
from detector.interventions import load_intervention_registry
from detector.packet import build_packet
from detector.measurement import (
    fatal_failure,
    measurement_status,
    needs_remeasurement,
    result_usage,
    retry_in_run,
)
from detector.provider import accumulate_usage, case_deadline_ctx
from detector.result import RESULT_SCHEMA
from detector.runtime import (
    RunControl,
    RunStopped,
    atomic_json,
    bounded_map,
    current_control,
    deadline_scope,
    exclusive_writer,
    remaining_seconds,
    run_scope,
    utc_now,
)

DEFAULT_RUNS_ROOT = Path("artifacts/detector/runs")
RUN_SCHEMA = "detector.run_manifest.v3"
SUMMARY_SCHEMA = "detector.run_summary.v2"


def new_run_id(prefix: str = "detector") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def safe_case_id(case_id: str) -> str:
    return (
        str(case_id).replace("/", "__").replace("\\", "__")
        .replace(":", "_") or "case"
    )


def _write_json(path: Path, payload: Any) -> None:
    atomic_json(path, payload)


def _case_set_digest(cases: Sequence[str]) -> str:
    """Order is part of the identity: a re-sorted list is a different sample."""
    return digest({"cases": [str(case) for case in cases]})


def _cached_envelope_is_reusable(
    result: Mapping[str, Any], *, retry_unresolved: bool,
) -> bool:
    """Whether an already-written ``result.json`` may stand in for a fresh run.

    *Is this envelope ours?* -- a file whose schema is not this runner's result
    schema is some other tool's output, and reusing it would silently mix two
    measurements in one run tree. *Is it a decision about the case?* -- an
    outage row records what the provider or the clock was doing during one
    window, not what the detector says about the case, so a resume that treats
    it as finished freezes the batch at that window's conditions. Re-measuring
    those rows is opt-in rather than assumed because a caller who wants the
    outage recorded keeps the default.
    """
    if str(result.get("schema_version") or "") != RESULT_SCHEMA:
        raise ValueError(
            "cached result is not a "
            f"{RESULT_SCHEMA} envelope: found "
            f"{result.get('schema_version')!r}; refusing to reuse it"
        )
    if retry_unresolved and needs_remeasurement(result):
        return False
    return True


def detect_with_transport_retry(
    detector: Detector,
    record: Mapping[str, Any],
    *,
    case_id: str,
    interventions: Mapping[str, Any] | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """Whole-detection retry for outage-shaped results.

    The retry re-runs the case under the *same* case deadline, so three
    attempts cannot add up to three case budgets. A fatal condition is not
    retried here either: the per-stage classification already stopped the run
    for it, and re-asking a provider that rejected the credential spends three
    times the time to produce the same refusal. A case that exhausted its own
    budget is also not retried in this run -- see ``retry_in_run``.
    """
    result: dict[str, Any] = {}
    if attempts <= 1:
        return detector.detect(record, case_id=case_id, interventions=interventions)
    # One case budget covers every attempt. Without this wrapper each retry
    # re-opened ``case_timeout_seconds`` on its own, so "one case budget" was
    # really "three case budgets" -- a 1h case could occupy a worker for 3h and
    # still be recorded as a single case's elapsed time.
    with case_deadline_ctx(
        (detector.config or {}).get("case_timeout_seconds")
    ):
        for _ in range(max(1, int(attempts))):
            result = detector.detect(
                record, case_id=case_id, interventions=interventions,
            )
            if not retry_in_run(result):
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
    attempt_id: str | None = None,
) -> Path:
    """Write one completed attempt; the canonical row is the last completed one.

    ``attempts/<attempt_id>/`` is written first and never overwritten, so a
    re-run keeps the earlier measurement instead of replacing the evidence for
    what this case previously produced. ``result.json`` beside it is the
    reader's path and always holds a *completed* attempt.
    """
    case_dir = Path(runs_root) / run_id / "cases" / safe_case_id(case_id)
    _write_json(case_dir / "input.json", input_record)
    if attempt_id:
        attempt_dir = case_dir / "attempts" / attempt_id
        _write_json(attempt_dir / "result.json", result)
        (attempt_dir / "elapsed_seconds.json").write_text(
            json.dumps({"elapsed_seconds": elapsed_seconds, "attempt_id": attempt_id}) + "\n",
            encoding="utf-8",
        )
    _write_json(case_dir / "result.json", result)
    (case_dir / "elapsed_seconds.json").write_text(
        json.dumps({"elapsed_seconds": elapsed_seconds, "attempt_id": attempt_id}) + "\n",
        encoding="utf-8",
    )
    return case_dir


def _attempt_id(index: int) -> str:
    return f"{int(index):03d}_{time.strftime('%Y%m%dT%H%M%S')}"


def _not_started(case_id: str) -> dict[str, Any]:
    """The row for a case the batch never began.

    Deliberately *not* written to disk: a case that did not run has no
    measurement to preserve, and writing an ``unresolved`` envelope would put
    an outage-shaped row where a later resume looks for finished work. It
    exists only so the summary can name the case.
    """
    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": "not_started",
        "occurrence_state": "not_started",
        "decision_authority": "not_measured",
        "runtime_valid": False,
        "responsible_step": None,
        "proof": {},
        "stage_status": {},
        "providers": {},
        "authority_failure_reasons": [{
            "component": "batch", "code": "not_started",
            "error_type": "batch_deadline_exceeded",
            "message": "the batch deadline passed before this case started",
        }],
        "provenance": {},
    }


def _previous_attempts(case_dir: Path) -> int:
    attempts = case_dir / "attempts"
    if not attempts.is_dir():
        return 0
    return sum(1 for item in attempts.iterdir() if item.is_dir())


def _manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The fields a resume must match; provenance fields are not identity."""
    return {
        key: manifest[key] for key in (
            "schema_version", "run_id", "cases", "case_set_digest",
            "config_hash", "interventions_hash", "source_identity", "execution_identities",
        ) if key in manifest
    }


def run_batch(
    *,
    dataset_root: str | Path,
    cases: Sequence[str],
    run_id: str | None = None,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    config: Mapping[str, Any] | None = None,
    interventions: Mapping[str, Any] | None = None,
    workers: int = 8,
    retry_unresolved: bool = False,
    deadline_seconds: float | None = None,
    control: RunControl | None = None,
) -> dict[str, Any]:
    """Run *cases* concurrently; skip cases a previous attempt already decided.

    ``deadline_seconds`` bounds the whole batch. A case that has not started
    when the deadline passes is recorded as ``not_started`` rather than being
    launched or silently dropped, so an interrupted batch is distinguishable
    from a corpus that simply had fewer cases.

    ``control`` carries a run-scoped stop signal and a provider-wide
    in-flight cap. When it trips -- a rejected credential, an exhausted quota,
    a cancelled run -- the batch stops submitting new work instead of
    reproducing the same failure once per remaining case.
    """
    if len(cases) != len(set(map(str, cases))):
        raise ValueError("batch cases must be unique")
    normalized_paths = {}
    for case_id in cases:
        safe = safe_case_id(case_id)
        previous = normalized_paths.setdefault(safe, str(case_id))
        if previous != str(case_id):
            raise ValueError(
                f"case ids collide in result path {safe!r}: {previous!r} and {case_id!r}"
            )
    config = config if config is not None else load_config()
    detector = Detector(config)
    config = detector.config
    if interventions is None and config.get("intervention_registry"):
        interventions = load_intervention_registry(config["intervention_registry"])
    source = source_identity("detector")
    execution_identities = {}
    for case in cases:
        record, _gold = load_case(dataset_root, case)
        execution_identities[case] = build_execution_identity(
            source=source, config=config, record=record,
            packet=build_packet(record), registry=interventions,
        )
    run_id = run_id or new_run_id()
    run_dir = Path(runs_root) / run_id
    case_root = run_dir / "cases"
    manifest_path = run_dir / "run.json"
    identity = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "cases": list(cases),
        "case_set_digest": _case_set_digest(cases),
        "config_hash": digest(config),
        "interventions_hash": digest(interventions),
        "source_identity": source,
        "execution_identities": execution_identities,
    }
    with exclusive_writer(run_dir):
        previous_manifest: dict[str, Any] | None = None
        if manifest_path.exists():
            raw_previous = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            previous_manifest = raw_previous if isinstance(raw_previous, dict) else None
            if not previous_manifest or _manifest_identity(previous_manifest) != identity:
                raise ValueError(
                    f"run {run_id!r} already exists with a different manifest"
                )
        else:
            orphaned = sorted(case_root.glob("*/result.json"))
            if orphaned:
                raise ValueError(
                    f"run {run_id!r} holds {len(orphaned)} case result(s) but no "
                    "run.json manifest; refusing to reuse results whose "
                    "configuration cannot be verified. Restore run.json, or point "
                    "the run at a new run_id."
                )
        attempt_id = _attempt_id((previous_manifest or {}).get("attempts", 0) + 1)
        manifest = {
            **identity,
            "attempt_id": attempt_id,
            "parent_attempt": (previous_manifest or {}).get("attempt_id"),
            "attempts": int((previous_manifest or {}).get("attempts", 0)) + 1,
            "started_at": utc_now(),
            "workers": int(workers),
            "deadline_seconds": deadline_seconds,
            "dataset_root": str(dataset_root),
        }
        _write_json(manifest_path, manifest)
        rows: list[dict[str, Any]] = []
        stop_reason: dict[str, Any] = {}

        def run(case_id: str) -> dict[str, Any]:
            remaining = remaining_seconds()
            if remaining is not None and remaining <= 0:
                # The case is recorded as not started rather than launched and
                # cut off: a partially-run detection is not a reading, and a
                # summary that only counted completions could not tell this
                # batch apart from one with fewer cases.
                return {"case": case_id, "result": _not_started(case_id),
                        "elapsed_seconds": 0.0, "source": "not_started"}
            effective_control.check()
            record, _gold = load_case(dataset_root, case_id)
            execution_identity = build_execution_identity(
                source=source, config=config, record=record,
                packet=build_packet(record), registry=interventions,
            )
            if execution_identity != execution_identities[case_id]:
                raise ValueError(f"case {case_id!r} changed after run identity was established")
            out_path = case_root / safe_case_id(case_id) / "result.json"
            if out_path.exists():
                result = json.loads(out_path.read_text(encoding="utf-8-sig"))
                if str(result.get("case_id") or "") != str(case_id):
                    raise ValueError(
                        f"cached result at {out_path} belongs to "
                        f"{result.get('case_id')!r}, not {case_id!r}"
                    )
                reusable = _cached_envelope_is_reusable(result, retry_unresolved=retry_unresolved)
                if (result.get("provenance") or {}).get("execution_identity") != execution_identity:
                    raise ValueError(f"cached result execution identity mismatch: {case_id}")
                if reusable:
                    print(f"  [{case_id}] reused cached {result.get('state')} row",
                          flush=True)
                    return {"case": case_id, "result": result, "elapsed_seconds": 0.0,
                            "source": "cached"}
                print(f"  [{case_id}] re-running cached {result.get('state')} row",
                      flush=True)
            started = time.monotonic()
            result = detect_with_transport_retry(
                detector, record, case_id=case_id,
                interventions=interventions,
            )
            observed_identity = (result.get("provenance") or {}).get("execution_identity")
            if observed_identity is not None and observed_identity != execution_identity:
                raise ValueError(f"detector execution identity changed during case {case_id!r}")
            result["provenance"] = {**(result.get("provenance") or {}),
                                    "execution_identity": execution_identity}
            elapsed = round(time.monotonic() - started, 1)
            case_dir = case_root / safe_case_id(case_id)
            index = _previous_attempts(case_dir) + 1
            record_case(
                runs_root=runs_root, run_id=run_id, case_id=case_id,
                input_record=record, result=result, elapsed_seconds=elapsed,
                attempt_id=_attempt_id(index),
            )
            print(
                f"  [{case_id}] state={result.get('state')} "
                f"authority={result.get('decision_authority')}",
                flush=True,
            )
            return {"case": case_id, "result": result, "elapsed_seconds": elapsed,
                    "source": "measured"}

        def note_stop(row: Mapping[str, Any]) -> None:
            nonlocal stop_reason
            fatal = fatal_failure(row["result"])
            if fatal and not stop_reason:
                stop_reason = {
                    "error_type": str(fatal.get("error_type") or "fatal_provider"),
                    "component": str(fatal.get("component") or ""),
                    "message": str(fatal.get("message") or fatal.get("error") or ""),
                    "case": str(row["case"]),
                }
                effective_control.stop(stop_reason)

        effective_control = control or RunControl(max(1, int(workers)))
        with deadline_scope(deadline_seconds), run_scope(effective_control):
            try:
                for row in bounded_map(run, cases, workers=int(workers),
                                       control=effective_control):
                    rows.append(row)
                    note_stop(row)
            except RunStopped as exc:
                # A stop is a recorded outcome, not a crash: the cases that
                # never started must be readable as "did not start", and the
                # reason must survive into the summary.
                stop_reason = stop_reason or dict(exc.reason) or {
                    "error_type": "stopped", "message": str(exc),
                }
            except BaseException as exc:
                if not stop_reason:
                    stop_reason = {"error_type": type(exc).__name__,
                                   "message": str(exc)[:400]}
                _write_json(run_dir / "summary.json", _summarize(
                    run_id=run_id, cases=list(cases), rows=rows,
                    manifest=manifest, stop_reason=stop_reason,
                ))
                raise
        summary = _summarize(
            run_id=run_id, cases=list(cases), rows=rows,
            manifest=manifest, stop_reason=stop_reason,
        )
        _write_json(run_dir / "summary.json", summary)
        return summary


def _summarize(
    *,
    run_id: str,
    cases: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    stop_reason: Mapping[str, Any],
) -> dict[str, Any]:
    by_case = {str(row["case"]): row for row in rows}
    statuses: dict[str, int] = {}
    states: dict[str, int] = {}
    usage: dict[str, Any] = {}
    usage_missing = 0
    elapsed = 0.0
    for row in rows:
        result = row["result"]
        status = measurement_status(result)
        statuses[status] = statuses.get(status, 0) + 1
        state = str(result.get("state") or "unknown")
        states[state] = states.get(state, 0) + 1
        elapsed += float(row.get("elapsed_seconds") or 0.0)
        reading = result_usage(result)
        if isinstance(reading.get("usage"), Mapping):
            accumulate_usage(usage, reading["usage"])
        usage_missing += int(reading.get("missing_attempts") or 0)
    missing = [case for case in cases if str(case) not in by_case]
    if missing and not stop_reason:
        stop_reason = {
            "error_type": "incomplete_batch",
            "message": f"{len(missing)} case(s) produced no row",
        }
    not_started = sorted(
        str(row["case"]) for row in rows
        if str(row["result"].get("state") or "") == "not_started"
    ) + missing
    return {
        "schema_version": SUMMARY_SCHEMA,
        "run_id": run_id,
        "cases": list(cases),
        "completed": len(rows) - len(not_started),
        "state_counts": {
            state: states.get(state, 0)
            for state in ("verified_error", "verified_clean", "unresolved")
        },
        "measurement": {
            "status_counts": statuses,
            "authoritative": statuses.get("authoritative", 0),
            "measured": len(rows) - statuses.get("invalid_envelope", 0)
            - statuses.get("not_started", 0),
            "not_started": len(not_started),
            "not_started_cases": not_started,
            "elapsed_seconds": round(elapsed, 1),
            "attempt_id": manifest.get("attempt_id"),
            "attempts": manifest.get("attempts"),
            "deadline_seconds": manifest.get("deadline_seconds"),
            "usage": usage or None,
            "usage_missing_attempts": usage_missing,
            "peak_provider_inflight": getattr(current_control(), "peak_requests", None),
        },
        "stop": dict(stop_reason),
        "finished_at": utc_now(),
    }
