"""Minimal detector configuration: three provider routes + env overrides.

Routes mirror the minimal court stages (census / verification / defense).
Every route resolves endpoint/model/credential from, in precedence order:

1. ``SEHD_ROUTE_<NAME>_<FIELD>``  (route-specific env override)
2. ``SEHD_DETECTOR_<FIELD>``      (detector-level env override)
3. ``SEHD_PROVIDER_<FIELD>``      (provider-level env override)
4. the route block in the config JSON
5. the ``provider`` block in the config JSON
6. built-in DeepSeek defaults

Credentials are read by env-var name only; credential VALUES never enter
config files or code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.env import load_local_env

DETECTOR_CONFIG_SCHEMA = "sehd2.detector_config.v1"

ROUTE_NAMES: tuple[str, ...] = ("census", "verification", "defense")

_ENV_FIELDS: tuple[str, ...] = (
    "ENDPOINT", "MODEL", "API_KEY_ENV", "TEMPERATURE", "MAX_TOKENS",
    "TIMEOUT_SECONDS", "TOTAL_TIMEOUT_SECONDS", "TRANSPORT_RETRIES",
)

_PROVIDER_DEFAULTS: dict[str, Any] = {
    "endpoint": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.0,
    # No max_tokens default: a reasoning model shares the output budget
    # between thinking and the reply, so capping it truncates JSON bodies.
    # When the route carries no max_tokens the provider default applies.
    "timeout_seconds": 120,
    "total_timeout_seconds": 360,
    "transport_retries": 2,
    "timeout_growth_factor": 1.5,
}

_INT_FIELDS = (
    "max_tokens", "timeout_seconds", "total_timeout_seconds",
    "transport_retries",
)


def default_config_path() -> Path:
    """Path of the packaged detector config (deterministic, cwd-independent)."""
    return Path(__file__).resolve().parent / "configs" / "detector_v1.json"


def _coerce(route: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(route)
    for key in _INT_FIELDS:
        if key in resolved and not isinstance(resolved[key], int):
            resolved[key] = int(float(resolved[key]))
    if "temperature" in resolved and not isinstance(
        resolved["temperature"], (int, float)
    ):
        resolved["temperature"] = float(resolved["temperature"])
    if "timeout_growth_factor" in resolved and not isinstance(
        resolved["timeout_growth_factor"], (int, float)
    ):
        resolved["timeout_growth_factor"] = float(
            resolved["timeout_growth_factor"]
        )
    return resolved


def resolve_route(
    name: str,
    route_block: Mapping[str, Any] | None,
    provider_block: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve one route to a complete provider-call contract."""
    merged: dict[str, Any] = dict(_PROVIDER_DEFAULTS)
    if provider_block:
        merged.update({
            key: value for key, value in provider_block.items()
            if value is not None
        })
    if route_block:
        merged.update({
            key: value for key, value in route_block.items()
            if value is not None
        })
    for field in _ENV_FIELDS:
        value = (
            os.environ.get(f"SEHD_ROUTE_{name.upper()}_{field}")
            or os.environ.get(f"SEHD_DETECTOR_{field}")
            or os.environ.get(f"SEHD_PROVIDER_{field}")
        )
        if value:
            merged[field.lower()] = value
    return _coerce(merged)


def load_config(
    path: str | Path | None = None, *, require_credentials: bool = True,
) -> dict[str, Any]:
    """Load and resolve the detector config (fail-closed on any defect)."""
    load_local_env()
    config_path = Path(path) if path is not None else default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("detector config must be an object")
    schema = str(raw.get("schema_version") or "")
    if schema and schema != DETECTOR_CONFIG_SCHEMA:
        raise ValueError(f"unsupported detector config schema: {schema}")
    provider_block = raw.get("provider")
    provider_block = provider_block if isinstance(provider_block, dict) else {}
    resolved: dict[str, Any] = {
        "schema_version": DETECTOR_CONFIG_SCHEMA,
    }
    for name in ROUTE_NAMES:
        route_block = raw.get(f"{name}_route")
        route_block = route_block if isinstance(route_block, dict) else {}
        route = resolve_route(name, route_block, provider_block)
        missing = [
            field for field in ("endpoint", "model", "api_key_env")
            if not str(route.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(
                f"detector route {name} missing fields: {', '.join(missing)}"
            )
        resolved[f"{name}_route"] = route
    if require_credentials:
        for name in ROUTE_NAMES:
            env_key = str(resolved[f"{name}_route"]["api_key_env"])
            if not os.environ.get(env_key):
                raise ValueError(
                    f"detector route {name}: credential env var "
                    f"{env_key} is not set"
                )
    return resolved
