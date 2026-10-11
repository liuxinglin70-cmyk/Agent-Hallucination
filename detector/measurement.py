"""Measurement health and cost, independent of detector decision semantics."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4
from detector.identity import digest
from detector.provider import FATAL_PROVIDER_ERRORS, accumulate_usage
from detector.result import RESULT_SCHEMA, prediction_state

#: Provider-side faults that a *later* attempt in the same run can plausibly
#: survive. These are the rows ``run_batch --retry-unresolved`` re-measures.
TRANSIENT_ERRORS = frozenset({
    "provider_error", "rate_limited", "invalid_json", "Timeout", "TimeoutError",
})

#: Failures that mean "this case exhausted its own wall-clock budget".
#: Recorded as infrastructure failures -- they are not verdicts -- but they are
#: deliberately NOT retried inside the same run: the budget that just ran out is
#: the budget the retry would use, so re-asking only spends the remaining time
#: to reach the same answer. A later run with fewer competing cases can retry
#: them, which is what ``retry_unresolved`` is for.
EXHAUSTED_ERRORS = frozenset({
    "case_deadline_exceeded", "batch_deadline_exceeded",
    "cancelled", "run_cancelled", "stopped",
})


def failure_reasons(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in result.get("authority_failure_reasons") or () if isinstance(item, Mapping)]


def _error_types(result: Mapping[str, Any]) -> set[str]:
    """Every error identifier a failure row carries, not just one of them.

    A row names its fault twice: ``error_type`` holds the Python-side class
    (``Timeout``) and ``code`` the semantic one
    (``batch_deadline_exceeded``). Reading only the first classified an
    exhausted build deadline as a retryable transport fault, because
    ``Timeout`` is also what a transient socket timeout looks like.
    """
    values: set[str] = set()
    for item in failure_reasons(result):
        for key in ("error_type", "code"):
            text = str(item.get(key) or "")
            if text:
                values.add(text)
    return values


def fatal_failure(result: Mapping[str, Any]) -> dict[str, Any] | None:
    for item in failure_reasons(result):
        if any(str(item.get(key) or "") in FATAL_PROVIDER_ERRORS
               for key in ("error_type", "code")):
            return dict(item)
    return None


def measurement_status(result: Mapping[str, Any]) -> str:
    if str(result.get("schema_version")) != RESULT_SCHEMA:
        return "invalid_envelope"
    # A case the batch never began is not an outage: nothing was attempted, so
    # there is no reading to be missing. It must not be grouped with the rows
    # the provider failed to answer, because its remedy is scheduling rather
    # than connectivity.
    if str(result.get("state") or "") == "not_started":
        return "not_started"
    if prediction_state(result) is not None:
        return "authoritative"
    if fatal_failure(result):
        return "fatal_provider"
    errors = _error_types(result)
    if errors & (TRANSIENT_ERRORS | EXHAUSTED_ERRORS):
        return "infrastructure_failure"
    codes = {str(item.get("code") or "") for item in failure_reasons(result)}
    if codes & {"provider_error", "recall_unavailable"}:
        return "infrastructure_failure"
    if str(result.get("decision_authority") or "").startswith("degraded"):
        return "degraded"
    return "semantic_unresolved"


def retry_in_run(result: Mapping[str, Any]) -> bool:
    """Whether re-asking *inside this run* could produce a decision.

    A provider fault can -- the next attempt is a fresh transport call. An
    exhausted case budget cannot: the budget a retry would spend is the one
    that just ran out, which is why the same-run retry excludes it. Across
    runs the answer flips (see ``needs_remeasurement``): hours later, with
    different competing load, the same case is worth asking again.
    """
    errors = _error_types(result)
    if (errors & EXHAUSTED_ERRORS or fatal_failure(result)
            or any(reason.get("retryable") is False for reason in failure_reasons(result))):
        return False
    return measurement_status(result) == "infrastructure_failure"


def needs_remeasurement(result: Mapping[str, Any]) -> bool:
    """Whether a *later* invocation should measure this row again.

    This is the predicate behind ``--retry-unresolved``. It is broader than
    ``retry_in_run`` on purpose: an outage row records what the provider or the
    clock was doing during one window, not what the detector says about the
    case, so a resume that treats it as finished freezes the batch at that
    window's conditions.
    """
    status = measurement_status(result)
    if status == "invalid_envelope":
        return False
    return status in {"infrastructure_failure", "fatal_provider", "not_started"}


def request_execution(
    requests: Sequence[Mapping[str, Any]], *, started_at: str, finished_at: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for request in requests:
        if isinstance(request.get("usage"), Mapping):
            accumulate_usage(usage, request["usage"])
    return {
        "schema_version": "detector.execution.v1", "started_at": started_at,
        "measurement_id": uuid4().hex,
        "finished_at": finished_at, "elapsed_seconds": elapsed_seconds,
        "requests": list(requests), "provider_attempts": len(requests),
        "usage": usage or None,
        "usage_source": "request_observations",
        "usage_missing_attempts": sum(request.get("usage") is None for request in requests),
    }


def _measurement_sequence_id(values: Sequence[Any]) -> str | None:
    if not values or not all(
        isinstance(value, str) and len(value) == 32
        and all(char in "0123456789abcdef" for char in value)
        for value in values
    ):
        return None
    return digest({"schema_version": "detector.measurement_sequence.v1", "attempts": list(values)})


def measurement_sources(execution: Mapping[str, Any]) -> tuple[str, ...]:
    """Verified original invocation identities; unknown lineage yields no sources."""
    if execution.get("schema_version") == "detector.execution.v1":
        value = execution.get("measurement_id")
        return (value,) if _measurement_sequence_id([value]) is not None else ()
    if execution.get("schema_version") != "detector.execution.v2":
        return ()
    attempts = execution.get("case_attempts")
    if not isinstance(attempts, list) or not attempts:
        return ()
    values = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or not isinstance(attempt.get("execution"), Mapping):
            return ()
        child = attempt["execution"]
        if child.get("schema_version") != "detector.execution.v1":
            return ()
        sources = measurement_sources(child)
        if not sources:
            return ()
        values.extend(sources)
    if len(set(values)) != len(values) or execution.get("measurement_id") != _measurement_sequence_id(values):
        return ()
    return tuple(values)


def merge_usage_sources(sources: Sequence[Any]) -> str:
    if all(source == "request_observations" for source in sources):
        return "request_observations"
    if all(source in ("request_observations", "legacy_stage_metadata_lower_bound") for source in sources):
        return "legacy_stage_metadata_lower_bound"
    return "unknown"


def _execution_totals(executions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for execution in executions:
        if isinstance(execution.get("usage"), Mapping):
            accumulate_usage(usage, execution["usage"])
    totals: dict[str, Any] = {"usage": usage or None}
    for key in ("provider_attempts", "usage_missing_attempts"):
        values = [execution.get(key) for execution in executions]
        totals[key] = sum(values) if all(type(value) is int for value in values) else None
    return totals


def _execution_usage_source(execution: Mapping[str, Any]) -> str:
    attempts = execution.get("case_attempts", [])
    if not isinstance(attempts, list):
        return "unknown"
    children = [attempt.get("execution") if isinstance(attempt, Mapping) else None for attempt in attempts]
    if not all(isinstance(child, Mapping) for child in children):
        return "unknown"
    if children:
        totals = _execution_totals(children)
        if (execution.get("usage") or None) != totals["usage"]:
            return "unknown"
        for key in ("provider_attempts", "usage_missing_attempts"):
            if (execution.get(key) != totals[key]
                    or (totals[key] is not None and type(execution.get(key)) is not int)):
                return "unknown"
    sources = []
    if "usage_source" in execution:
        sources.append(execution["usage_source"])
    elif not attempts:
        requests = execution.get("requests")
        if not isinstance(requests, list) or not all(isinstance(request, Mapping) for request in requests):
            return "unknown"
        usage: dict[str, Any] = {}
        for request in requests:
            if isinstance(request.get("usage"), Mapping):
                accumulate_usage(usage, request["usage"])
        if (execution.get("provider_attempts") != len(requests)
                or execution.get("usage_missing_attempts") != sum(request.get("usage") is None for request in requests)
                or (execution.get("usage") or {}) != usage):
            return "unknown"
        sources.append("request_observations")
    sources.extend(_execution_usage_source(child) for child in children)
    return merge_usage_sources(sources)


def case_execution(
    attempts: Sequence[Mapping[str, Any]], *, started_at: str, finished_at: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    executions = [attempt["execution"] for attempt in attempts]
    requests: list[dict[str, Any]] = []
    measurement_ids: list[Any] = []
    for number, attempt in enumerate(attempts, start=1):
        execution = attempt["execution"]
        measurement_ids.append(execution.get("measurement_id"))
        requests.extend({**request, "case_attempt": number}
                        for request in execution.get("requests") or ())
    return {
        "schema_version": "detector.execution.v2", "started_at": started_at,
        # Aggregation must not turn cached attempts into fresh measurements.
        "measurement_id": _measurement_sequence_id(measurement_ids),
        "finished_at": finished_at, "elapsed_seconds": elapsed_seconds,
        "case_attempts": list(attempts), "requests": requests,
        **_execution_totals(executions),
        "usage_source": merge_usage_sources([_execution_usage_source(execution) for execution in executions]),
    }


def result_usage(result: Mapping[str, Any]) -> dict[str, Any]:
    execution = result.get("execution")
    if isinstance(execution, Mapping):
        return {"usage": execution.get("usage"), "source": _execution_usage_source(execution),
                "missing_attempts": execution.get("usage_missing_attempts"),
                "attempts": execution.get("provider_attempts")}
    providers = result.get("providers") or {}
    hearings = (result.get("proof") or {}).get("hearings") or []
    values: list[Mapping[str, Any]] = []
    hearing_stages = set()
    for hearing in hearings:
        if not isinstance(hearing, Mapping):
            continue
        for stage, key in (("step_tracer", "tracer_result"), ("defense", "defense_result")):
            data = hearing.get(key) or {}
            provider = data.get("provider") if isinstance(data, Mapping) else None
            if isinstance(provider, Mapping):
                values.append(provider)
                hearing_stages.add(stage)
    values += [value for key, value in providers.items()
               if key not in hearing_stages and isinstance(value, Mapping)]
    total: dict[str, Any] = {}
    for provider in values:
        usage = provider.get("usage_total") or provider.get("usage")
        if isinstance(usage, Mapping):
            accumulate_usage(total, usage)
    return {"usage": total or None, "source": "legacy_stage_metadata_lower_bound",
            "missing_attempts": None, "attempts": None}


def cost_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    attempts: list[int | None] = []
    missing: list[int | None] = []
    sources: list[str] = []
    elapsed = 0.0
    for row in rows:
        reading = result_usage(row["result"])
        if isinstance(reading["usage"], Mapping):
            accumulate_usage(usage, reading["usage"])
        attempts.append(reading["attempts"])
        missing.append(reading["missing_attempts"])
        sources.append(reading["source"])
        elapsed += float(row.get("elapsed_seconds") or 0)
    return {"elapsed_seconds": round(elapsed, 1), "usage": usage or None,
            "usage_source": merge_usage_sources(sources),
            "provider_attempts": sum(attempts) if all(type(n) is int for n in attempts) else None,
            "usage_missing_attempts": sum(n for n in missing if type(n) is int),
            "usage_unknown_cases": sum(n is None for n in missing)}
