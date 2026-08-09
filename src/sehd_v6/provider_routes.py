"""Central provider-route resolution from process or repository ``.env``.

Resolution precedence is route-specific, purpose-specific, global, then the
loaded configuration. Credentials are never materialized in a route: only the
name of the environment variable holding the key is selected.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from .env_loader import load_local_env


OFFICIAL_ENDPOINT = "https://api.deepseek.com"
OFFICIAL_MODEL = "deepseek-v4-flash"
OFFICIAL_API_KEY_ENV = "DEEPSEEK_API_KEY"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def route_purpose(route_name: str | None) -> str:
    """Map a config/route name to its environment selector purpose."""

    name = str(route_name or "").lower()
    if "mutation" in name or "reflection" in name:
        return "mutation"
    if "judge" in name or "appeal" in name or "boundary" in name:
        return "judge"
    if "embedding" in name or "embed" in name:
        return "embedding"
    return "detector"


_NUMERIC_FIELDS: dict[str, type[int] | type[float]] = {
    "temperature": float,
    "max_tokens": int,
    "transport_retries": int,
    "timeout_seconds": int,
    "total_timeout_seconds": int,
}


def _prefix(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return f"SEHD_{normalized}"


def _env_value(
    environ: Mapping[str, str],
    prefixes: tuple[str, ...],
    suffix: str,
) -> str | None:
    for prefix in prefixes:
        value = environ.get(f"{prefix}_{suffix}")
        if value is not None and value.strip():
            return value.strip()
    for name in (f"SEHD_PROVIDER_{suffix}", f"SEHD_{suffix}"):
        value = environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def resolve_provider_route(
    route: Mapping[str, Any],
    *,
    purpose: str,
    route_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve one route without exposing or copying credential values.

    Supported selectors are ``SEHD_ROUTE_<NAME>_*``, ``SEHD_<PURPOSE>_*``,
    and ``SEHD_PROVIDER_*``. For example, ``defense_route`` can use
    ``SEHD_ROUTE_DEFENSE_MODEL`` while all detector routes can share
    ``SEHD_DETECTOR_API_KEY_ENV``.
    """

    load_local_env()
    env = os.environ if environ is None else environ
    resolved = dict(route)
    prefixes: list[str] = []
    if route_name:
        name = route_name[:-6] if route_name.lower().endswith("_route") else route_name
        prefixes.append(_prefix(f"route_{name}"))
    prefixes.extend((_prefix(purpose), "SEHD_PROVIDER"))
    ordered = tuple(prefixes)

    for field, suffix in (
        ("endpoint", "ENDPOINT"),
        ("model", "MODEL"),
        ("api_key_environment", "API_KEY_ENV"),
        ("temperature", "TEMPERATURE"),
        ("max_tokens", "MAX_TOKENS"),
        ("transport_retries", "TRANSPORT_RETRIES"),
        ("timeout_seconds", "TIMEOUT_SECONDS"),
        ("total_timeout_seconds", "TOTAL_TIMEOUT_SECONDS"),
    ):
        value = _env_value(env, ordered, suffix)
        if value is None:
            continue
        converter = _NUMERIC_FIELDS.get(field)
        try:
            resolved[field] = converter(value) if converter else value
        except ValueError as exc:
            raise ValueError(
                f"Invalid provider route value for {field}: {value!r}"
            ) from exc

    if not resolved.get("endpoint"):
        resolved["endpoint"] = OFFICIAL_ENDPOINT
    if not resolved.get("model"):
        resolved["model"] = OFFICIAL_MODEL

    # Keep an explicitly configured deployment credential fail-closed. Only a
    # route with no selector and no explicit key can use the official default
    # environment. An explicit per-route/purpose selector is the
    # highest-precedence key source; it must never be silently replaced by the
    # official default below (a user-supplied selector would be dead
    # otherwise).
    selected_key_env = str(resolved.get("api_key_environment") or "")
    explicit_key_selector = _env_value(env, ordered, "API_KEY_ENV")
    if explicit_key_selector:
        if not _ENV_NAME.fullmatch(explicit_key_selector):
            raise ValueError(
                f"Invalid provider api-key environment name: "
                f"{explicit_key_selector!r}"
            )
        resolved["api_key_environment"] = explicit_key_selector
    elif (
        not selected_key_env
        and str(resolved.get("endpoint") or "").rstrip("/") == OFFICIAL_ENDPOINT
    ):
        resolved["api_key_environment"] = OFFICIAL_API_KEY_ENV
    return resolved


def resolve_provider_credential(
    route: Mapping[str, Any], *, purpose: str | None = None
) -> str:
    """Return a credential or fail without exposing its value."""

    load_local_env()
    name = str(route.get("api_key_environment") or "").strip()
    if not _ENV_NAME.fullmatch(name):
        raise RuntimeError("Provider route has no valid api-key environment name")
    value = os.environ.get(name)
    if not value:
        label = str(purpose or route.get("route_purpose") or "provider")
        raise RuntimeError(
            f"Missing provider credential for purpose={label!r}; "
            f"set {name} in .env or the process environment"
        )
    return value


def has_provider_credential(route: Mapping[str, Any]) -> bool:
    """Check credential availability without returning or logging it."""

    load_local_env()
    name = str(route.get("api_key_environment") or "").strip()
    return bool(_ENV_NAME.fullmatch(name) and os.environ.get(name))


def provider_thinking_control(
    route: Mapping[str, Any], *, purpose: str
) -> dict[str, str] | None:
    """Resolve a bounded thinking toggle for strict JSON provider calls.

    DeepSeek V4 defaults to thinking mode, and the gateway honors that
    default regardless of which deployment fronts it: an unconstrained
    mutation request can spend nearly its entire output budget in
    ``reasoning_content`` (observed: ~4500 reasoning tokens against a ~980
    token answer on the autodl.art gateway). When the reasoning occasionally
    crosses the budget the response is truncated before any ``content`` is
    emitted (``finish_reason=length``, empty body), which the strict-JSON
    transport then fail-closes as an unparseable response. The mutation/
    reflection family (including the L2 closed-vocabulary policy reflection)
    produces strict machine-readable JSON and never needs chain-of-thought,
    so it defaults to non-thinking mode for any DeepSeek V4 model on any
    gateway; an explicit route setting still wins.
    """
    configured = route.get("thinking")
    if isinstance(configured, Mapping):
        value = str(configured.get("type") or "").strip().lower()
    else:
        value = str(configured or "").strip().lower()
    if value:
        if value not in {"enabled", "disabled"}:
            raise ValueError("provider thinking mode must be enabled or disabled")
        return {"type": value}
    model = str(route.get("model") or "").lower()
    if (
        purpose
        in {
            "mutation",
            "reflection_critic",
            "reflection_diagnosis",
            "policy_reflection",
            "reflection_pro",
        }
        and model.startswith("deepseek-v4-")
    ):
        return {"type": "disabled"}
    return None


def resolve_config_provider_routes(
    config: Mapping[str, Any],
    *,
    purpose: str = "detector",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a config copy with every ``*_route`` resolved consistently."""

    resolved = dict(config)
    for name, value in config.items():
        if name.endswith("_route") and isinstance(value, Mapping):
            selected_purpose = route_purpose(name) if purpose == "detector" else purpose
            route = resolve_provider_route(
                value,
                purpose=selected_purpose,
                route_name=name,
                environ=environ,
            )
            # Prevent a later transport boundary from re-resolving a config
            # route under the generic detector purpose and overriding a
            # route-specific judge/mutation selector.
            route["_provider_route_name"] = name
            route["_provider_purpose"] = selected_purpose
            route["_provider_route_resolved"] = True
            resolved[name] = route
    return resolved
