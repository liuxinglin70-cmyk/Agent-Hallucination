"""Provider transport, census helpers and JSON I/O for the proof-court detector.

Provider transport is a direct HTTPS call to the configured gateway.  System
proxies (environment variables or the platform registry) add an extra failure
surface — connection resets, timeouts — with no benefit for a direct endpoint,
so every provider request bypasses them explicitly.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

from sehd_rise.detection.court.contract import AXES as PACKET_AXES
from sehd_rise.detection.court.contract import COVERAGE_STATES
from sehd_rise.platform.env import load_local_env
from sehd_rise.platform.gold import coerce_gold_label
from sehd_rise.platform.jsonio import canonical_json, stable_hash
from sehd_rise.platform.models import canonical_model_name
from sehd_rise.platform.routes import (
    provider_thinking_control,
    resolve_provider_credential,
    resolve_provider_route,
)

load_local_env()

DIRECT_PROVIDER_OPENER = urlrequest.build_opener(urlrequest.ProxyHandler({}))


def _read_response_body(response: Any, budget_seconds: int) -> bytes:
    """Read one provider response body to EOF under a hard monotonic deadline.

    ``OpenerDirector.open(timeout=...)`` only bounds a single socket
    operation; a provider that streams a slow or stalled body keeps a plain
    ``response.read()`` blocking past the configured total-timeout deadline,
    which silently hangs a concurrent worker.

    The body is accumulated in bounded chunks: ``HTTPResponse.read1``
    performs at most one raw ``recv`` per call (it returns as soon as one
    network chunk is available rather than filling the requested size), so we
    loop on it until it returns ``b""`` at genuine EOF. Before each chunk the
    underlying socket timeout is recomputed from the remaining budget, which
    makes the total deadline authoritative for the whole body instead of only
    the connect/first-byte window; a stalled read then surfaces as
    ``TimeoutError``/``OSError`` and feeds the normal retry machinery.

    The socket is resolved once up front: after the first ``read1`` the
    ``HTTPResponse`` may clear its ``fp`` reference, so re-deriving it (or
    falling back to ``response.read()``) mid-loop would both lose the chunk
    already buffered and mis-read the remainder. The reader always drains to
    EOF so no partially-read body is ever mistaken for an empty one.
    """
    deadline = time.monotonic() + max(1, int(budget_seconds))
    fp = getattr(response, "fp", None)
    sock = getattr(fp, "raw", None)
    sock = getattr(sock, "_sock", None) or getattr(fp, "_sock", None)
    can_timeout = sock is not None and hasattr(sock, "settimeout")

    chunks: list[bytes] = []
    if not hasattr(response, "read1"):
        # A plain file-like body without ``read1`` (e.g. an in-memory buffer)
        # is already bounded in memory; a single ``read()`` drains it and the
        # deadline machinery adds nothing.
        return response.read()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                "provider response body read exceeded the total timeout budget"
            )
        if can_timeout:
            try:
                sock.settimeout(max(1, int(remaining)))
            except OSError:
                pass
        chunk = response.read1(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _global_cache_root() -> Path | None:
    """Global provider cache root, or None when the cache is disabled.

    The cache is opt-in: unset ``PACA_GLOBAL_CACHE_ROOT`` disables it.  The
    explicit values ``off`` / ``disabled`` / ``none`` (case-insensitive) also
    disable it, so a caller can pin the cache off in the process environment
    even when a ``.env`` file would otherwise re-inject a root through
    ``load_local_env``.
    """
    raw = os.environ.get("PACA_GLOBAL_CACHE_ROOT")
    if not raw or not raw.strip():
        return None
    if str(raw).strip().lower() in {"off", "disabled", "none"}:
        return None
    return Path(raw.strip())


def _global_cache_path(request_hash: str) -> Path | None:
    """Global cache file path (two-level sharding to bound directory size)."""
    root = _global_cache_root()
    if root is None:
        return None
    return root / request_hash[:2] / f"{request_hash}.json"


def _local_cache_path(cache_dir: Path, request_hash: str) -> Path:
    """Run-local provider cache file path.

    ``cache_dir`` is the per-run cache directory supplied by the caller
    (e.g. ``output_dir / "provider_cache"``).  Two-level directory
    dispersion matches the global cache layout so both caches share the
    same sharding scheme.
    """
    return Path(cache_dir) / request_hash[:2] / f"{request_hash}.json"


def _provider_repair_payload(
    payload: dict[str, Any],
    system: str,
    user: str,
) -> tuple[bytes, str]:
    """Build a bounded repair request body for truncated JSON.

    The repair re-sends the same system prompt with a compact instruction
    appended to the user prompt, so the canonical body differs from the
    original request and the cache key differs. Returns (body, user).
    """
    repair_user = user + (
        "\n\nJSON_RECOVERY: Your previous response carried no usable JSON "
        "body: it was either truncated before the closing brace or spent the "
        "whole budget on reasoning. Return the COMPLETE JSON object now, with "
        "every required field and no commentary. Keep it minimal and start "
        "directly with {."
    )
    repair_payload = dict(payload)
    repair_payload["messages"] = [
        {"role": "system", "content": system},
        {"role": "user", "content": repair_user},
    ]
    return canonical_json(repair_payload).encode("utf-8"), repair_user


def _provider_repair_call(
    *,
    route: dict[str, Any],
    api_key: str,
    payload: bytes,
    cache_dir: Path,
    request_hash: str,
    request_timeout: int | None = None,
) -> dict[str, Any] | None:
    """One bounded repair provider call for a truncated JSON response.

    The repair body has a different cache key, so a repair result is never
    confused with the original request. Any provider/parse failure returns
    None, and the caller fail-closes with the original error.
    """
    repair_hash = hashlib.sha256(
        b"provider_repair|" + request_hash.encode("utf-8") + payload
    ).hexdigest()
    local_target = _local_cache_path(cache_dir, repair_hash)
    if local_target.is_file():
        try:
            cached = read_json(local_target)
            if cached.get("request_hash") == repair_hash:
                return cached.get("parsed")
        except Exception:
            pass
    req = urlrequest.Request(
        route["endpoint"].rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        # The repair must stay inside the same total-timeout deadline as the
        # original attempt: a fresh full window would let a run exceed its
        # declared budget by one extra repair call.
        repair_timeout = max(
            1,
            int(request_timeout or route.get("timeout_seconds", 120)),
        )
        with DIRECT_PROVIDER_OPENER.open(req, timeout=repair_timeout) as response:
            raw = _read_response_body(response, repair_timeout)
        value = json.loads(raw.decode("utf-8"))
        content = value["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        cached = {
            "request_hash": repair_hash,
            "response_hash": hashlib.sha256(raw).hexdigest(),
            "parsed": parsed,
            "usage": value.get("usage") or {},
            "latency_seconds": 0.0,
            "contains_credentials": False,
        }
        try:
            _atomic_write_cache(local_target, cached)
        except Exception:
            pass
        return parsed
    except Exception:
        return None


def _atomic_write_cache(path: Path, value: Any) -> None:
    """Atomic cache write safe under concurrent workers.

    ``os.replace`` makes readers see either the complete previous file or the
    complete next one; the temporary name carries pid + thread id so two
    workers never collide on the same staging file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def private_ref(case_id: str, prefix: str) -> str:
    return prefix + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:20]


def parse_gold_label(value: Any) -> bool:
    """Parse an evaluator label; missing labels are not clean examples.

    Delegates the token vocabulary to the single coercion authority
    (``platform.gold.coerce_gold_label``) and keeps the fail-fast
    contract: an absent or unrecognizable label raises instead of silently
    becoming a clean example.
    """
    if value is None:
        raise ValueError("gold label is required for evaluation")
    coerced = coerce_gold_label(value)
    if coerced is None:
        raise ValueError("unrecognized gold label")
    return coerced


def _event_for_source_step(built: dict[str, Any], step: Any) -> str | None:
    normalized_step = str(step).strip() if step is not None else None
    for event in built["events"]:
        event_step = event.get("source_step_id")
        if event_step == step or (
            normalized_step is not None
            and str(event_step).strip() == normalized_step
        ):
            return str(event["event_id"])
    if normalized_step and normalized_step.isdigit():
        position = int(normalized_step)
        if 1 <= position <= len(built["events"]):
            return str(built["events"][position - 1]["event_id"])
    return None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def load_paca_census(
    *, case_id: str, built: dict[str, Any], cache_root: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if cache_root is None:
        return [], {"status": "disabled", "reason": "no census cache configured"}
    source = cache_root / private_ref(case_id, "p0_") / "paca_v1_canary_results.json"
    if not source.is_file():
        return [], {"status": "missing", "path": str(source)}
    try:
        value = read_json(source)
    except Exception as exc:
        # A corrupt or unreadable cache must not kill the whole detection:
        # the callers fall back to the provider census on any non-complete
        # audit, which is exactly the intended degraded path.
        return [], {
            "status": "invalid", "path": str(source),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    try:
        outputs = value.get("outputs") or {}
        if not isinstance(outputs, Mapping):
            return [], {"status": "invalid", "path": str(source)}
        stage1 = outputs.get("stage1_scanning")
        raw_candidates = (
            stage1.get("candidates") if isinstance(stage1, Mapping) else None
        )
    except Exception as exc:
        return [], {
            "status": "invalid", "path": str(source),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    if not isinstance(raw_candidates, list):
        return [], {"status": "invalid", "path": str(source)}
    try:
        converted: list[dict[str, Any]] = []
        for index, row in enumerate(raw_candidates, start=1):
            if not isinstance(row, dict):
                continue
            origin = _event_for_source_step(built, row.get("root_step_id"))
            if not origin:
                continue
            dependencies = []
            for step in row.get("information_dependencies") or []:
                ref = _event_for_source_step(built, step)
                if ref and ref not in dependencies:
                    dependencies.append(ref)
            converted.append({
                "candidate_id": f"paca_candidate_{index:03d}",
                "origin_hypothesis_event_id": origin,
                "trigger_event_id": origin,
                "axes": [str(row.get("hallucination_dimension") or "F")],
                "candidate_kind": str(row.get("candidate_kind") or "unmatched"),
                "claim_or_commitment": str(row.get("information_introduced") or row.get("observation") or "candidate deviation"),
                "dependency_event_ids": dependencies,
                "module_tag": str(row.get("module_tag") or "Unknown"),
                "observation": str(row.get("observation") or ""),
                "is_decision_point": _parse_bool(row.get("is_decision_point", False)),
                "source_step_id": next(
                    (
                        event.get("source_step_id")
                        for event in built["events"]
                        if str(event.get("event_id")) == origin
                    ),
                    None,
                ),
            })
    except Exception as exc:
        return [], {
            "status": "invalid", "path": str(source),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    return converted, {
        "status": "complete" if len(converted) == len(raw_candidates) else "partial",
        "raw_candidate_count": len(raw_candidates),
        "converted_candidate_count": len(converted),
        "source_hash": stable_hash(raw_candidates),
    }


def candidate_system_prompt() -> str:
    return """You perform PACA-TCL dependency-aware candidate census. Enumerate distinct potential hallucination origins in the complete trajectory without deciding the final label.

Scan all four axes: F factual claims/numbers/entities/citations; Fa instruction/context/logic faithfulness; P planning, retrieval, omission, tool arguments, grounding and execution state; S authorization, relevance and scope. Prefer the earliest event that INTRODUCES a claim, commitment, invalid argument, unsupported assumption, or consequential omission. Do not select only a later symptom or final answer when an earlier origin is addressable. Ordinary tool failures, exploration and recovered deviations may still enter the census; later layers decide their boundary.

Return at most 8 deduplicated candidates and do not force a candidate when none is defensible. Use event IDs only. Return JSON:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"candidate_id":"candidate_001","origin_hypothesis_event_id":"event_001","trigger_event_id":"event_001","axes":["F"],"candidate_kind":"short_typed_name","claim_or_commitment":"specific introduced state","dependency_event_ids":[],"module_tag":"Planning|Retrieval|Reasoning|Tool-Use|Human-Interaction|Loop-Detection|Redundancy-Check","observation":"why this event enters the census","is_decision_point":true}]}.
Coverage is complete only after scanning the whole supplied trajectory. Never use dataset identity, source, labels or outside facts."""


def provider_call(*, route: dict[str, Any], system: str, user: str, cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return provider_call_with_schema(
        route=route, system=system, user=user, cache_dir=cache_dir,
        cache_prefix="v1_census", require_candidates=True,
    )


def provider_call_with_schema(
    *,
    route: dict[str, Any],
    system: str,
    user: str,
    cache_dir: Path,
    cache_prefix: str = "v1_census",
    require_candidates: bool = True,
    purpose: str = "detector",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Provider call with configurable schema validation.

    Args:
        require_candidates: If True (default), validate that the response
            contains a ``candidates`` list (the census/ledger schema). Set
            to False for routes that return other top-level keys, e.g.
            the counterfactual localization route which returns
            ``{"rankings": {...}}``.
    """
    route = dict(route)
    if not route.get("_provider_route_resolved"):
        route = resolve_provider_route(route, purpose=purpose)
    # Config routes may be shared by worker threads; transport preparation
    # must never mutate the frozen config object, so the copy above is
    # always the working instance.
    transport_purpose = str(route.get("_provider_purpose") or purpose)
    try:
        route["model"] = canonical_model_name(str(route.get("model") or ""))
    except ValueError:
        pass
    # The wire payload mirrors the resolved route exactly: temperature and
    # max_tokens are carried through so configured output budgets and
    # determinism settings reach the provider unchanged.
    payload = {
        "model": route["model"],
        "temperature": float(route.get("temperature", 0)),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    # The DeepSeek-V4 models reject response_format on every gateway tested
    # so far while still returning JSON when the system prompt requests it,
    # so the parameter is dropped for the whole model family instead of
    # maintaining per-gateway rules.
    if not str(route["model"]).lower().startswith("deepseek-v4"):
        payload["response_format"] = {"type": "json_object"}
    if route.get("max_tokens") is not None:
        payload["max_tokens"] = int(route["max_tokens"])
    thinking = provider_thinking_control(route, purpose=transport_purpose)
    if thinking is not None:
        payload["thinking"] = thinking
    body = canonical_json(payload).encode("utf-8")
    # The cache key carries cache_prefix so two callers issuing an identical
    # payload under different schema contracts (candidates envelope vs. free
    # JSON object) can never share a cached response.
    request_hash = hashlib.sha256(cache_prefix.encode("utf-8") + b"|" + (route["endpoint"] + "|" + route["model"] + "|").encode("utf-8") + body).hexdigest()

    # Run-local cache: reuse responses made inside this run's cache
    # directory.  This is the cache the evolution runner seeds through
    # ``genome_dir/<cache_name>`` so replays of the same genome + case
    # reuse identical provider responses without touching the global cache.
    local_target = _local_cache_path(cache_dir, request_hash)
    if local_target.is_file():
        try:
            cached = read_json(local_target)
            if cached.get("request_hash") == request_hash:
                parsed = cached.get("parsed")
                if not isinstance(parsed, dict):
                    raise ValueError("cached provider response is not an object")
                if require_candidates and not isinstance(parsed.get("candidates"), list):
                    raise ValueError("cached provider ledger schema invalid")
                return parsed, {
                    "cache_hit": True,
                    "request_hash": request_hash,
                    "usage": cached.get("usage") or {},
                    "cache_source": "local",
                    "transport_attempts": int(cached.get("transport_attempts") or 0),
                    "repair_used": bool(cached.get("repair_used")),
                }
        except Exception:
            pass  # corrupt cache file: fall through to the provider

    # Global cache lookup: shared across every output_dir of this machine.
    global_target = _global_cache_path(request_hash)
    if global_target is not None and global_target.is_file():
        try:
            cached = read_json(global_target)
            if cached.get("request_hash") == request_hash:
                parsed = cached.get("parsed")
                if not isinstance(parsed, dict):
                    raise ValueError("cached provider response is not an object")
                if require_candidates and not isinstance(parsed.get("candidates"), list):
                    raise ValueError("cached provider ledger schema invalid")
                return parsed, {
                    "cache_hit": True,
                    "request_hash": request_hash,
                    "usage": cached.get("usage") or {},
                    "cache_source": "global",
                    "transport_attempts": int(cached.get("transport_attempts") or 0),
                    "repair_used": bool(cached.get("repair_used")),
                }
        except Exception:
            pass  # corrupt cache file: fall through to the provider

    api_key = resolve_provider_credential(route, purpose=transport_purpose)
    retries = int(route.get("transport_retries", 2))
    per_attempt_timeout = max(1, int(route.get("timeout_seconds", 300)))
    # The retry budget must cover every attempt: a total deadline equal to a
    # single per-attempt window burns the whole budget on the first timeout
    # and silently disables every configured transport retry.  Default the
    # total budget to per-attempt times (retries + 1) so each attempt owns
    # its own timeout window unless an explicit route override is supplied.
    total_timeout = route.get(
        "total_timeout_seconds", per_attempt_timeout * (retries + 1)
    )
    deadline = (
        time.monotonic() + max(1, int(total_timeout))
        if total_timeout is not None
        else None
    )
    last_error: Exception | None = None
    transport_attempts = 0
    repair_used = False
    for attempt in range(retries + 1):
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = TimeoutError(
                    "provider total timeout exceeded before attempt"
                )
                break
            request_timeout = max(1, min(per_attempt_timeout, int(remaining)))
        else:
            request_timeout = per_attempt_timeout
        try:
            transport_attempts += 1
            req = urlrequest.Request(
                route["endpoint"].rstrip("/") + "/chat/completions",
                data=body,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                method="POST",
            )
            started = time.time()
            with DIRECT_PROVIDER_OPENER.open(req, timeout=request_timeout) as response:
                raw = _read_response_body(response, request_timeout)
            value = json.loads(raw.decode("utf-8"))
            choice = value["choices"][0]
            message = choice["message"]
            content = message.get("content")
            usable_content = (
                content.strip() if isinstance(content, str) and content.strip()
                else ""
            )
            try:
                if not usable_content:
                    raise json.JSONDecodeError("empty provider body", "", 0)
                parsed = json.loads(usable_content)
            except json.JSONDecodeError:
                # One bounded provider repair. A response carrying no usable
                # JSON body is repairable only with a DIFFERENT request body
                # (cache key differs), re-sending the same system prompt with
                # a compact continuation instruction. Failure remains
                # fail-closed: the original error is raised.
                repair_body, _repair_user = _provider_repair_payload(
                    payload, system, user,
                )
                try:
                    transport_attempts += 1
                    repair_value = _provider_repair_call(
                        route=route, api_key=api_key,
                        payload=repair_body,
                        cache_dir=cache_dir, request_hash=request_hash,
                        request_timeout=request_timeout,
                    )
                except Exception as exc:
                    raise ValueError(
                        "provider_empty_content:"
                        f"finish_reason={choice.get('finish_reason') or 'unknown'},"
                        "reasoning_content_present="
                        f"{bool(message.get('reasoning_content'))}"
                    ) from exc
                if repair_value is None:
                    raise ValueError(
                        "provider_empty_content:"
                        f"finish_reason={choice.get('finish_reason') or 'unknown'},"
                        "reasoning_content_present="
                        f"{bool(message.get('reasoning_content'))}"
                    )
                parsed = repair_value
                repair_used = True
                choice = {"finish_reason": "repair"}
            if not isinstance(parsed, dict):
                raise ValueError("provider response is not a JSON object")
            if require_candidates and not isinstance(parsed.get("candidates"), list):
                raise ValueError("provider ledger schema invalid: missing candidates")
            cached = {
                "request_hash": request_hash,
                "response_hash": hashlib.sha256(raw).hexdigest(),
                "parsed": parsed,
                "usage": value.get("usage") or {},
                "latency_seconds": time.time() - started,
                "contains_credentials": False,
                "transport_attempts": transport_attempts,
                "repair_used": repair_used,
            }
            if global_target is not None:
                try:
                    _atomic_write_cache(global_target, cached)
                except Exception:
                    pass  # a failed cache write must not break detection
            try:
                _atomic_write_cache(local_target, cached)
            except Exception:
                pass
            return parsed, {
                "cache_hit": False,
                "request_hash": request_hash,
                "usage": cached["usage"],
                "latency_seconds": cached["latency_seconds"],
                "cache_source": "local",
                "transport_attempts": transport_attempts,
                "repair_used": repair_used,
            }
        except (
            urlerror.URLError,
            OSError,
            http.client.HTTPException,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            last_error = exc
            if isinstance(exc, ValueError):
                # A contract failure (empty content, non-object response,
                # missing ledger schema) is deterministic for a fixed request
                # body: retransmitting it only multiplies cost, and the
                # bounded JSON repair already had its single chance on this
                # attempt. Fail closed immediately.
                break
            retryable = not isinstance(exc, urlerror.HTTPError) or exc.code in {
                408, 425, 429, 500, 502, 503, 504,
            }
            if not retryable:
                # Fail fast: auth/validation errors will not heal on retry.
                break
            if attempt < retries:
                backoff = float(2**attempt)
                if isinstance(exc, urlerror.HTTPError) and exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        backoff = max(backoff, min(30.0, float(retry_after)))
                    except (TypeError, ValueError):
                        # Jitter prevents concurrent workers from retrying together.
                        backoff += random.uniform(0.0, 1.0)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    backoff = min(backoff, remaining)
                if backoff > 0:
                    time.sleep(backoff)
    assert last_error is not None
    raise last_error


def json_object_provider_call(
    *,
    route: dict[str, Any],
    system: str,
    user: str,
    cache_dir: Path,
    purpose: str = "detector",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call a provider for an unconstrained JSON object.

    Some routes do not use the ``candidates`` envelope. Keeping this wrapper
    beside the cached provider implementation gives those callers the same
    deterministic transport without pretending they implement a ledger schema.
    """
    return provider_call_with_schema(
        route=route,
        system=system,
        user=user,
        cache_dir=cache_dir,
        cache_prefix="generic_json_object",
        require_candidates=False,
        purpose=purpose,
    )


def validate_provider_census(value: dict[str, Any], built: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coverage = str(value.get("coverage_state") or "")
    rows = value.get("candidates")
    # The dependency and trigger reference universes are the framework-wide
    # addressable surface (event ids + tool interaction ids, see
    # ``_addressable_refs``), not the event-only set: a candidate
    # legitimately depends on the result of a tool call, and the triggering
    # unit can be the specific tool interaction. Only the origin hypothesis
    # must remain an event id: it is the ordering anchor for earliest-origin
    # localization and the source-step mapping.
    event_ids = {str(event["event_id"]) for event in built["events"]}
    interaction_origins = {
        str(interaction["interaction_id"]): str(event["event_id"])
        for event in built["events"]
        for interaction in event.get("tool_interactions") or []
    }
    addressable = set(_addressable_refs(built))
    if coverage not in COVERAGE_STATES or not isinstance(rows, list) or len(rows) > 8:
        return [], {"status": "invalid_top_level", "coverage_state": coverage}
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    normalized_interaction_origins = 0
    required = {
        "candidate_id", "origin_hypothesis_event_id", "trigger_event_id", "axes",
        "candidate_kind", "claim_or_commitment", "dependency_event_ids", "module_tag",
        "observation", "is_decision_point",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            failures.append({"index": index, "reason": "not_object"})
            continue
        candidate_id = str(row.get("candidate_id") or "")
        axes = row.get("axes")
        dependencies = row.get("dependency_event_ids")
        raw_origin = str(row.get("origin_hypothesis_event_id") or "")
        origin = (
            raw_origin
            if raw_origin in event_ids
            else interaction_origins.get(raw_origin, "")
        )
        checks = {
            "required": required <= set(row),
            "candidate_id": bool(candidate_id) and candidate_id not in seen,
            "origin": bool(origin),
            "trigger": str(row.get("trigger_event_id") or "") in addressable,
            "axes": isinstance(axes, list) and bool(axes) and set(map(str, axes)) <= PACKET_AXES,
            "dependencies": isinstance(dependencies, list) and all(str(ref) in addressable for ref in dependencies),
            "candidate_kind": bool(str(row.get("candidate_kind") or "").strip()),
            "claim": bool(str(row.get("claim_or_commitment") or "").strip()),
            "observation": bool(str(row.get("observation") or "").strip()),
            "decision_point": isinstance(row.get("is_decision_point"), bool),
        }
        if all(checks.values()):
            normalized_row = dict(row)
            normalized_row["origin_hypothesis_event_id"] = origin
            if origin != raw_origin:
                normalized_interaction_origins += 1
            normalized_row["source_step_id"] = next(
                (
                    event.get("source_step_id")
                    for event in built["events"]
                    if str(event.get("event_id")) == origin
                ),
                None,
            )
            valid.append(normalized_row)
            seen.add(candidate_id)
        else:
            failures.append({"index": index, "failed_checks": sorted(key for key, passed in checks.items() if not passed)})
    status = "complete" if not failures and coverage == "complete" else "partial"
    return valid, {
        "status": status,
        "coverage_state": coverage,
        "raw_candidate_count": len(rows),
        "converted_candidate_count": len(valid),
        "normalized_interaction_origin_count": normalized_interaction_origins,
        "failures": failures,
        "source_hash": stable_hash(rows),
    }


def _addressable_refs(built: dict[str, Any]) -> list[str]:
    refs = []
    for event in built["events"]:
        refs.append(str(event["event_id"]))
        refs.extend(str(item["interaction_id"]) for item in event.get("tool_interactions") or [])
    return refs


def candidate_provider_user_prompt(built: dict[str, Any]) -> str:
    user = (
        "TRAJECTORY_PACKET=\n" + canonical_json(built) +
        "\nADDRESSABLE_EVENT_IDS=\n" + canonical_json([event["event_id"] for event in built["events"]])
    )
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in user:
            raise ValueError("candidate provider input firewall violation: " + field)
    return user


def _responsible_step(event_id: str | None, built: dict[str, Any]) -> int | None:
    if not event_id:
        return None
    for event in built["events"]:
        if event["event_id"] == event_id:
            value = event.get("source_step_id")
            if isinstance(value, int):
                return value
            text = str(value or "").strip()
            return int(text) if text.isdigit() else int(event["position"])
    return None
