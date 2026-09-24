"""Measurement health and cost, independent of detector decision semantics."""
from __future__ import annotations

from typing import Any, Mapping
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
        error_type = str(item.get("error_type") or item.get("code") or "")
        if error_type in FATAL_PROVIDER_ERRORS:
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
    if errors & EXHAUSTED_ERRORS:
        return False
    return measurement_status(result) in {"infrastructure_failure", "fatal_provider"}


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


def result_usage(result: Mapping[str, Any]) -> dict[str, Any]:
    execution = result.get("execution")
    if isinstance(execution, Mapping):
        return {"usage": execution.get("usage"), "source": "request_observations",
                "missing_attempts": execution.get("usage_missing_attempts", 0),
                "attempts": execution.get("provider_attempts", 0)}
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
