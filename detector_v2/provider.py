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
from http.client import IncompleteRead
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

_DIRECT_OPENER = urlrequest.build_opener(urlrequest.ProxyHandler({}))

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
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "detector_v2.provider_worker"],
            input=request_payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.001, float(timeout)),
            check=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("isolated transport exceeded request deadline") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[:200]
        raise OSError(f"isolated transport failed: {detail or completed.returncode}")
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
        status = int(result["status"])
        raw = base64.b64decode(str(result["body_b64"]), validate=True)
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("isolated transport returned an invalid envelope") from exc
    return status, raw


class ProviderError(RuntimeError):
    """A provider call failed after exhausting its retry budget."""

    def __init__(
        self,
        route_name: str,
        message: str,
        *,
        error_type: str = "provider_error",
        attempts: int = 0,
    ) -> None:
        super().__init__(f"route {route_name}: {message}")
        self.route_name = route_name
        self.error_type = error_type
        self.attempts = attempts
        self.message = message


def extract_json_object(text: str) -> Any | None:
    """Return the first balanced JSON object in *text*, or ``None``."""
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
    if not stripped.startswith("{"):
        start = stripped.find("{")
        if start < 0:
            return None
        stripped = stripped[start:]
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(stripped):
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
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[: index + 1])
                except ValueError:
                    return None
    return None


def _endpoint_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


def provider_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
) -> tuple[Any, dict[str, Any]]:
    """One counted provider call; raises :class:`ProviderError` on failure."""
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
            prefix = str(context_cache.get("key_prefix") or "detector-v2")
            payload["prompt_cache_key"] = f"{prefix}:{purpose}"
            retention = context_cache.get("retention")
            if retention:
                payload["prompt_cache_retention"] = str(retention)
    if route.get("max_tokens") is not None:
        payload["max_tokens"] = int(route["max_tokens"])
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    retries = int(route.get("transport_retries", 2))
    growth = max(1.0, float(route.get("timeout_growth_factor", 1.5)))
    per_attempt = int(route.get("timeout_seconds", 120))
    total_timeout = max(1.0, float(route.get("total_timeout_seconds", 360)))
    deadline = time.monotonic() + total_timeout
    last_error = ""
    attempts = 0
    needs_recovery = False
    def retry_delay(attempt: int) -> bool:
        """Sleep only while the shared request deadline still permits it."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(30.0, 2.0 ** attempt, remaining))
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
        try:
            status, raw = _isolated_transport(
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
            )
        except (
            urlerror.URLError,
            TimeoutError,
            OSError,
            ValueError,
            IncompleteRead,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            last_error = f"provider returned non-JSON body: {raw[:160]!r}"
            needs_recovery = True
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        if status != 200 or not data.get("choices"):
            detail = data.get("error") or {}
            last_error = f"http {status}: {str(detail)[:200]}"
            if attempt < retries and not retry_delay(attempt):
                last_error = "total timeout exceeded"
                break
            continue
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = str(message.get("content") or "")
        parsed = extract_json_object(content)
        usage = dict(data.get("usage") or {})
        meta: dict[str, Any] = {
            "attempts": attempts,
            "usage": usage,
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
            "raw_content_preview": content[:300],
        }
        if parsed is None:
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
    error_type = "invalid_json" if "JSON" in last_error else "provider_error"
    raise ProviderError(
        route_name, last_error or "unknown failure",
        error_type=error_type, attempts=attempts,
    )
