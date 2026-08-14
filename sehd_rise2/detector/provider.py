"""Provider transport: one direct HTTPS call with retries and JSON repair.

A minimal, dependency-free transport: direct HTTPS to the configured
gateway, progressive per-attempt timeouts, bounded retries with backoff,
one JSON-recovery pass when the model's reply carries no usable JSON body,
and a fail-closed ``ProviderError`` carrying machine-readable diagnostics.
System proxies are bypassed explicitly (a direct endpoint gains nothing
from them and they add a failure surface).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

DIRECT_PROVIDER_OPENER = urlrequest.build_opener(urlrequest.ProxyHandler({}))

JSON_RECOVERY_INSTRUCTION = (
    "\n\nJSON_RECOVERY: Your previous response carried no usable JSON body. "
    "Return the COMPLETE JSON object now, with every required field and no "
    "commentary. Start directly with {."
)


class ProviderError(RuntimeError):
    """A provider call failed after exhausting its retry budget."""

    def __init__(
        self, route_name: str, message: str,
        *, error_type: str = "provider_error", attempts: int = 0,
    ) -> None:
        super().__init__(f"route {route_name}: {message}")
        self.route_name = route_name
        self.error_type = error_type
        self.attempts = attempts
        self.message = message


def extract_json_object(text: str) -> Any | None:
    """Extract the first balanced JSON object from a model reply, or None."""
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


def provider_call(
    *, route: Mapping[str, Any], system: str, user: str, purpose: str,
) -> tuple[Any, dict[str, Any]]:
    """One counted provider call; raises ``ProviderError`` when it fails.

    Returns ``(parsed, meta)`` where ``parsed`` is the extracted JSON object
    (None when the provider returned no usable JSON after recovery) and
    ``meta`` carries usage, attempts and cache_hit.
    """
    route_name = str(route.get("_route_name") or purpose)
    endpoint = str(route.get("endpoint") or "").rstrip("/")
    url = (
        endpoint
        if endpoint.endswith("/chat/completions")
        else endpoint + "/chat/completions"
    )
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
        "max_tokens": int(route.get("max_tokens", 4096)),
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    retries = int(route.get("transport_retries", 2))
    growth = max(1.0, float(route.get("timeout_growth_factor", 1.5)))
    per_attempt = int(route.get("timeout_seconds", 120))
    cap = int(route.get("total_timeout_seconds", 360))
    last_error = ""
    attempts = 0
    needs_recovery = False
    for attempt in range(retries + 1):
        attempts += 1
        timeout = min(max(1, int(per_attempt * (growth ** attempt))), cap)
        effective_user = (
            user + JSON_RECOVERY_INSTRUCTION if needs_recovery else user
        )
        effective_payload = dict(payload)
        effective_payload["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": effective_user},
        ]
        request_body = json.dumps(
            effective_payload, ensure_ascii=False,
        ).encode("utf-8")
        try:
            request = urlrequest.Request(
                url, data=request_body, headers=headers, method="POST",
            )
            with DIRECT_PROVIDER_OPENER.open(request, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 0) or 0)
                raw = resp.read()
        except (urlerror.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(30.0, 2.0 ** attempt))
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            last_error = f"provider returned non-JSON body: {raw[:160]!r}"
            needs_recovery = True
            if attempt < retries:
                time.sleep(min(30.0, 2.0 ** attempt))
            continue
        if status != 200 or not data.get("choices"):
            detail = data.get("error") or {}
            last_error = f"http {status}: {str(detail)[:200]}"
            if attempt < retries:
                time.sleep(min(30.0, 2.0 ** attempt))
            continue
        content = ""
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = str(message.get("content") or "")
        parsed = extract_json_object(content)
        meta: dict[str, Any] = {
            "attempts": attempts,
            "usage": dict(data.get("usage") or {}),
            "cache_hit": False,
            "purpose": purpose,
            "model": str(data.get("model") or route.get("model") or ""),
        }
        if parsed is None and not needs_recovery:
            needs_recovery = True
            last_error = "provider reply carried no usable JSON object"
            if attempt < retries:
                time.sleep(min(30.0, 2.0 ** attempt))
            continue
        return parsed, meta
    error_type = (
        "invalid_json" if "JSON" in last_error else "provider_error"
    )
    raise ProviderError(
        route_name, last_error or "unknown failure",
        error_type=error_type, attempts=attempts,
    )
