"""Dependency-free HTTPS transport for OpenAI-compatible chat completions.

Key behaviour (a deliberate fix over the old detector):

- every response is a fresh HTTP request; no local response replay;
- a response without a usable JSON object is treated as ``invalid_json``
  after the recovery attempt, and the call RAISES instead of returning
  ``None`` to a contract parser;
- transport errors, HTTP errors and invalid JSON are retried with backoff;
- credentials are read by env-var name only.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from uuid import uuid4
from http.client import IncompleteRead
from pathlib import Path
from typing import Any, Callable, Mapping
from contextvars import ContextVar
from urllib import error as urlerror
from urllib import request as urlrequest
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from detector.trajectory_budget import bound_request_route
from detector.runtime import (
    CaseDeadlineExceeded, CaseExecutionError, RunStopped, check_request_deadline, current_added_request_budget,
    current_added_budget, current_control, current_deadline, deadline_scope, observe_request, record_request, request_deadline,
)

_DIRECT_OPENER = urlrequest.build_opener(urlrequest.ProxyHandler({}))
_VIEW_FALLBACK: ContextVar[str | Callable[[], str] | None] = ContextVar("provider_view_fallback", default=None)


@contextmanager
def provider_fallback_scope(user: str | Callable[[], str] | None):
    token = _VIEW_FALLBACK.set(user)
    try:
        yield
    finally:
        _VIEW_FALLBACK.reset(token)

_JSON_RECOVERY_INSTRUCTION = (
    "\n\nJSON_RECOVERY: Your previous response carried no usable JSON body. "
    "Return the COMPLETE JSON object now, with every required field and no "
    "commentary. Start directly with {."
)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def _isolated_transport(
    *,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    """Run one HTTP exchange in a killable child process.

    Some Windows socket reads can remain blocked after urllib's timeout has
    elapsed.  The parent therefore owns the hard deadline and kills this
    child if it expires; retries cannot accumulate unbounded wall-clock time.
    Credentials are sent over the child's stdin, never command-line args.
    """
    request_payload = json.dumps({
        "url": url,
        "headers": dict(headers),
        "body_b64": base64.b64encode(body).decode("ascii"),
        "timeout": max(0.001, float(timeout)),
    }).encode("utf-8")
    child_env = os.environ.copy()
    for key in _PROXY_ENV_KEYS:
        child_env.pop(key, None)
    package_root = str(Path(__file__).resolve().parent.parent)
    existing_pythonpath = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = (
        package_root + os.pathsep + existing_pythonpath
        if existing_pythonpath else package_root
    )
    deadline = time.monotonic() + max(0.001, float(timeout))
    process = subprocess.Popen(
            [sys.executable, "-m", "detector.provider_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env,
    )
    try:
        while True:
            control = current_control()
            if control is not None:
                control.check()
            check_request_deadline()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("isolated transport exceeded request deadline")
            try:
                stdout, stderr = process.communicate(request_payload, timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                request_payload = None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
    if process.returncode != 0:
        # Keep the whole traceback: a truncated one hides the real cause and
        # blocked classification of model errors versus service faults.
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise OSError(
            f"isolated transport failed: {detail or process.returncode}"
        )
    try:
        result = json.loads(stdout.decode("utf-8"))
        status = int(result["status"])
        raw = base64.b64decode(str(result["body_b64"]), validate=True)
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("isolated transport returned an invalid envelope") from exc
    return status, raw


class ProviderError(CaseExecutionError):
    """A provider call failed after exhausting its retry budget."""

    def __init__(
        self,
        route_name: str,
        message: str,
        *,
        error_type: str = "provider_error",
        attempts: int = 0,
        http_status: int | None = None,
        retryable: bool = True,
        usage_total: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"route {route_name}: {message}")
        self.route_name = route_name
        self.error_type = error_type
        self.attempts = attempts
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.usage_total = dict(usage_total or {})

    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route_name, "message": self.message,
                "error_type": self.error_type, "http_status": self.http_status,
                "retryable": self.retryable, "attempts": self.attempts,
                **({"usage_total": dict(self.usage_total)} if self.usage_total else {})}


FATAL_PROVIDER_ERRORS = frozenset({
    "missing_credential", "authentication_error", "permission_error",
    "invalid_request", "quota_exhausted", "model_unavailable", "run_cancelled",
})


def raise_if_terminal(exc: ProviderError) -> None:
    if not exc.retryable or exc.error_type in FATAL_PROVIDER_ERRORS:
        raise exc


def extract_json_object(text: str) -> Any | None:
    """Return the first complete JSON value (object or array) in *text*.

    A reply that is entirely one JSON value — object or array — is returned
    whole; otherwise the first balanced object/array embedded in prose is
    extracted. Callers validate the shape they expect.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    starts = [i for i in (stripped.find("{"), stripped.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[start : index + 1])
                except ValueError:
                    return None
    return None


def case_deadline_ctx(seconds: float | None) -> Any:
    """Nested stages and whole-case retries may shorten, never reset, a deadline."""
    return deadline_scope(seconds)


def required_quorum(votes: int) -> int:
    """Strict majority of the *configured* ballot count.

    ``majority_vote`` counts only cast ballots, so without a floor an
    abstention-heavy vote shrinks its own quorum and a single surviving
    ballot becomes a "strict majority". The floor is what actually keeps
    transport jitter from changing a verdict between runs.
    """
    return max(1, int(votes) // 2 + 1)


def accumulate_usage(total: dict[str, Any], usage: Mapping[str, Any]) -> None:
    """Fold one ballot's token usage into a running total, in place.

    A voting stage reports the *last* ballot's provider meta as its sample, so
    ``usage`` alone undercounts the stage's real burn by the ballot
    multiplicity. That undercount is what made the 2026-09-21 run look like it
    spent 120k completion tokens when its three-ballot judge and defense had
    actually spent several times that, and it is the number a run's cost has to
    be sized from. Adding the ballots up needs no schema for the details
    blocks, so every numeric leaf is summed by its own path: a provider that
    reports a new counter gets it totalled without a code change.
    """
    for key, value in usage.items():
        if isinstance(value, Mapping):
            bucket = total.get(key)
            if not isinstance(bucket, dict):
                bucket = {}
                total[key] = bucket
            accumulate_usage(bucket, value)
        elif isinstance(value, bool):
            # A boolean is an int in Python; summing one would be nonsense.
            continue
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value


def merge_provider_meta(previous: Mapping[str, Any], latest: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the latest sample and the total cost of all calls in one stage."""
    if not previous:
        return dict(latest)
    merged = {**previous, **latest}
    merged["attempts"] = int(previous.get("attempts") or 0) + int(latest.get("attempts") or 0)
    usage: dict[str, Any] = {}
    for meta in (previous, latest):
        accumulate_usage(usage, meta.get("usage_total") or meta.get("usage") or {})
    if usage:
        merged["usage_total"] = usage
    return merged


def majority_vote(values, *, on_tie):
    """Strict-majority vote with a deterministic tie law.

    ``None`` ballots are abstentions (a sample that produced no valid
    verdict) and are not counted. A value wins only with more than half of
    the cast ballots; any other outcome — a tie, no strict majority, or no
    cast ballots — resolves to ``on_tie``. The winner never depends on
    ballot order, so sequential sampling cannot bias the outcome.

    Returns ``(winner, cast_ballots)``.
    """
    counts: dict[Any, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    cast = sum(counts.values())
    for value, count in counts.items():
        if count * 2 > cast:
            return value, cast
    return on_tie, cast


def _endpoint_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


def provider_call(
    *, route: Mapping[str, Any], system: str, user: str,
    purpose: str, cache_tag: str = "", fallback_user: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    fallback = fallback_user if fallback_user is not None else _VIEW_FALLBACK.get()
    original_route = dict(route)
    added_output_tokens = int(route.get("max_tokens") or 1500)
    route, request_budget = bound_request_route(route, system=system, user=user)
    control = current_control()
    try:
        if control is not None:
            control.check()
        check_request_deadline()
        budget = current_added_request_budget()
        if budget is not None:
            output_tokens = min(added_output_tokens, route["max_tokens"]) if route["max_tokens"] is not None else added_output_tokens
            route = {**route, "transport_retries": 0, "max_tokens": budget.reserve(
                system=system, user=user, output_tokens=output_tokens,
                extra_input=route.get("extra_body"),
            )}
        case_budget = current_added_budget()
        with case_budget.baseline_wait() if case_budget is not None and budget is None else nullcontext():
            result = _provider_call(route=route, system=system, user=user,
                                    purpose=purpose, cache_tag=cache_tag)
        check_request_deadline()
        parsed, meta = result
        return parsed, {**meta, "request_budget": {**asdict(request_budget),
                                                  "output_token_limit": route["max_tokens"]}}
    except CaseDeadlineExceeded as exc:
        raise ProviderError(
            str(route.get("_route_name") or purpose), str(exc),
            error_type="case_deadline_exceeded",
            retryable=False,
        ) from exc
    except ProviderError as exc:
        if exc.error_type == "context_length_exceeded" and fallback is not None:
            fallback_text = fallback() if callable(fallback) else fallback
            if fallback_text == user:
                raise
            try:
                with provider_fallback_scope(None):
                    parsed, meta = provider_call(route=original_route, system=system, user=fallback_text,
                                                 purpose=purpose + "_view_fallback", cache_tag=cache_tag)
            except ProviderError as failure:
                failure.attempts += exc.attempts
                total = dict(exc.usage_total)
                accumulate_usage(total, failure.usage_total)
                failure.usage_total = total
                raise
            return parsed, {**merge_provider_meta({"attempts": exc.attempts, "usage_total": exc.usage_total}, meta),
                            "evidence_view_fallback": "provider_context_limit"}
        if control is not None and exc.error_type in FATAL_PROVIDER_ERRORS:
            control.stop(exc.as_dict())
        raise


def _provider_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    cache_tag: str = "",
) -> tuple[Any, dict[str, Any]]:
    """One counted provider call; raises :class:`ProviderError` on failure.

    ``cache_tag`` extends the prompt-cache key with the identity of inputs
    that ride outside the prompt text (an evidence snapshot digest), so a
    cached prefix can never be replayed across different evidence.
    """
    route_name = str(route.get("_route_name") or purpose)
    url = _endpoint_url(str(route.get("endpoint") or ""))
    api_key = os.environ.get(str(route.get("api_key_env") or ""), "")
    if not api_key:
        raise ProviderError(
            route_name,
            f"credential env var {route.get('api_key_env')!r} is not set",
            error_type="missing_credential",
        )
    payload = {
        "model": str(route.get("model") or ""),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": float(route.get("temperature", 0.0)),
        "stream": False,
    }
    extra_body = route.get("extra_body")
    if isinstance(extra_body, Mapping):
        # Route-scoped provider options (for example DeepSeek thinking mode).
        # Do not let an extension replace the request identity or messages.
        for key, value in extra_body.items():
            if key not in {"model", "messages", "stream"}:
                payload[str(key)] = value
    context_cache = route.get("context_cache")
    if isinstance(context_cache, Mapping) and bool(
        context_cache.get("enabled", True)
    ):
        # DeepSeek-compatible providers cache repeated prompt prefixes
        # automatically. OpenAI-compatible providers can additionally accept
        # explicit cache-key hints; only send those when the route opts in so
        # strict gateways are not given unsupported request fields.
        mode = str(context_cache.get("mode") or "automatic")
        if mode == "prompt_cache_key":
            prefix = str(context_cache.get("key_prefix") or "detector")
            key = f"{prefix}:{purpose}"
            if cache_tag:
                key = f"{key}:{cache_tag}"
            payload["prompt_cache_key"] = key
            retention = context_cache.get("retention")
            if retention:
                payload["prompt_cache_retention"] = str(retention)
    if route.get("max_tokens") is not None:
        payload["max_tokens"] = int(route["max_tokens"])
    if bool(route.get("json_mode", False)):
        # Providers that accept OpenAI-style JSON mode constrain the reply
        # to a single JSON value; opt-in per route so strict gateways that
        # reject the field are unaffected.
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    retries = int(route.get("transport_retries", 2))
    growth = max(1.0, float(route.get("timeout_growth_factor", 1.5)))
    per_attempt = int(route.get("timeout_seconds", 120))
    total_timeout = max(1.0, float(route.get("total_timeout_seconds", 360)))
    check_request_deadline()
    case_deadline = current_deadline()
    effective_deadline = request_deadline()
    if effective_deadline is not None:
        remaining_case = effective_deadline - time.monotonic()
        if remaining_case <= 0:
            check_request_deadline()
        total_timeout = min(total_timeout, remaining_case)
    deadline = time.monotonic() + total_timeout
    last_error = ""
    attempts = 0
    needs_recovery = False
    call_usage: dict[str, Any] = {}
    last_status: int | None = None
    control = current_control()
    def retry_delay(attempt: int) -> bool:
        """Sleep only while the shared request deadline still permits it."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        delay = min(30.0, 2.0 ** attempt, remaining)
        if control is None:
            time.sleep(delay)
        else:
            control.stopped.wait(delay)
            control.check()
        check_request_deadline()
        return deadline - time.monotonic() > 0

    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = "total timeout exceeded"
            break
        attempts += 1
        timeout = min(
            float(max(1, int(per_attempt * (growth ** attempt)))),
            remaining,
        )
        effective_user = (
            user + _JSON_RECOVERY_INSTRUCTION if needs_recovery else user
        )
        body = json.dumps(
            {
                **payload,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": effective_user},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        if control is not None:
            control.check()
        request_started = time.perf_counter()
        request_id = uuid4().hex
        observe_request({"event": "request_queued", "request_id": request_id,
                         "purpose": purpose, "attempt": attempts, "timeout_seconds": timeout})
        queue_seconds = 0.0
        transport_started = False
        try:
            with control.request_slot(total_deadline=deadline) if control is not None else nullcontext():
                queue_seconds = time.perf_counter() - request_started
                check_request_deadline()
                timeout = min(timeout, max(0.001, deadline - time.monotonic()))
                observe_request({"event": "request_started", "request_id": request_id,
                                 "purpose": purpose, "attempt": attempts,
                                 "queue_seconds": queue_seconds, "timeout_seconds": timeout})
                transport_started = True
                status, raw = _isolated_transport(
                    url=url, headers=headers, body=body, timeout=timeout,
                )
        except CaseDeadlineExceeded:
            if transport_started:
                record_request({"request_id": request_id, "purpose": purpose, "attempt": attempts,
                                "elapsed_seconds": time.perf_counter() - request_started,
                                "queue_seconds": queue_seconds, "status": "deadline_exceeded", "usage": None})
            raise
        except RunStopped as exc:
            if transport_started:
                record_request({"request_id": request_id, "purpose": purpose, "attempt": attempts,
                                "elapsed_seconds": time.perf_counter() - request_started,
                                "queue_seconds": queue_seconds, "status": "cancelled",
                                "error_type": exc.reason.get("error_type", "stopped"), "usage": None})
            raise
        except (
            urlerror.URLError,
            TimeoutError,
            OSError,
            ValueError,
            IncompleteRead,
        ) as exc:
            if not transport_started:
                queue_seconds = time.perf_counter() - request_started
            record_request({"request_id": request_id, "purpose": purpose, "attempt": attempts,
                            "elapsed_seconds": time.perf_counter() - request_started,
                            "queue_seconds": queue_seconds, "status": "transport_error",
                            "error_type": type(exc).__name__, "usage": None})
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, Mapping):
            data = {}
        observed_usage = data.get("usage")
        record_request({"request_id": request_id, "purpose": purpose, "attempt": attempts,
                        "elapsed_seconds": time.perf_counter() - request_started,
                        "queue_seconds": queue_seconds, "http_status": status,
                        "status": "response", "model": str(data.get("model") or route.get("model") or ""),
                        "usage": dict(observed_usage) if isinstance(observed_usage, Mapping) else None})
        if isinstance(observed_usage, Mapping):
            accumulate_usage(call_usage, observed_usage)
        last_status = status
        if status in {400, 401, 402, 403, 404, 422}:
            error = data.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            message = str(error.get("message") or "") if isinstance(error, Mapping) else str(error or "")
            context_limit = ((isinstance(code, str) and code in {"context_length_exceeded", "input_too_long"})
                             or any(term in message.lower() for term in (
                                 "maximum context length", "max context length", "input is too long", "too many input tokens")))
            if status in {400, 422} and context_limit:
                raise ProviderError(route_name, "provider context limit exceeded",
                                    error_type="context_length_exceeded", attempts=attempts,
                                    http_status=status, retryable=False, usage_total=call_usage)
            category = {401: "authentication_error", 403: "permission_error",
                        402: "quota_exhausted", 404: "model_unavailable"}.get(status, "invalid_request")
            raise ProviderError(route_name, f"http {status}: {str(data.get('error') or 'request refused')[:200]}",
                                error_type=category, attempts=attempts, http_status=status, retryable=False)
        if not data:
            last_error = "provider returned non-JSON body"
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        if status != 200 or not data.get("choices"):
            detail = data.get("error") or {}
            last_error = f"http {status}: {str(detail)[:200]}"
            if status == 429 and isinstance(detail, Mapping) and str(detail.get("code") or detail.get("type")) in {"insufficient_quota", "quota_exhausted"}:
                raise ProviderError(route_name, last_error, error_type="quota_exhausted",
                                    attempts=attempts, http_status=status, retryable=False)
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        content = content if isinstance(content, str) else ""
        finish_reason = str(choice.get("finish_reason") or "")
        parsed = extract_json_object(content)
        usage = dict(observed_usage) if isinstance(observed_usage, Mapping) else {}
        meta: dict[str, Any] = {
            "attempts": attempts,
            "usage": usage,
            "usage_total": dict(call_usage),
            "cache_hit": bool(usage.get("prompt_cache_hit_tokens")),
            "prompt_cache_hit_tokens": int(
                usage.get("prompt_cache_hit_tokens") or 0
            ),
            "prompt_cache_miss_tokens": int(
                usage.get("prompt_cache_miss_tokens") or 0
            ),
            "prompt_cache_read_tokens": int(
                usage.get("prompt_cache_read_tokens")
                or usage.get("cache_read_input_tokens")
                or 0
            ),
            "prompt_cache_write_tokens": int(
                usage.get("prompt_cache_write_tokens")
                or usage.get("cache_creation_input_tokens")
                or 0
            ),
            "purpose": purpose,
            "model": str(data.get("model") or route.get("model") or ""),
            "finish_reason": finish_reason or None,
            "raw_content_preview": content[:300],
        }
        if parsed is None:
            if finish_reason == "length":
                # Truncated output can never re-parse as JSON; retrying the
                # same prompt with the same cap is wasted spend.
                raise ProviderError(
                    route_name,
                    f"output truncated at max_tokens "
                    f"({usage.get('completion_tokens')} tokens)",
                    error_type="truncated_output",
                    attempts=attempts, usage_total=call_usage,
                )
            if not needs_recovery:
                needs_recovery = True
                last_error = "provider reply carried no usable JSON object"
                if attempt < retries and not retry_delay(attempt):
                    last_error = "total timeout exceeded"
                    break
                continue
            last_error = "provider reply carried no usable JSON object"
            break
        return parsed, meta
    expired_case = case_deadline is not None and time.monotonic() >= case_deadline
    if not expired_case:
        check_request_deadline()
    error_type = ("case_deadline_exceeded" if expired_case else
                  "invalid_json" if "JSON" in last_error else
                  "rate_limited" if last_status == 429 else "provider_error")
    raise ProviderError(
        route_name, last_error or "unknown failure",
        error_type=error_type, attempts=attempts, http_status=last_status,
        retryable=not expired_case,
    )
