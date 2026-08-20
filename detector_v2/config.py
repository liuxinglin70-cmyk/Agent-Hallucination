"""Detector V2 configuration: two provider routes (judge / defense).

Resolution order (highest wins):

1. ``DETECTOR_V2_*`` process environment
2. route block in the config JSON
3. ``provider`` block in the config JSON
4. built-in defaults

The package never reads the old framework's ``SEHD_*`` variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from detector_v2.env import load_local_env

CONFIG_SCHEMA = "detector_v2.config.v1"
ROUTE_NAMES = ("judge", "defense", "recall")

_PROVIDER_DEFAULTS: dict[str, Any] = {
    "endpoint": "https://api.deepseek.com",
    "model": "deepseek-v4-pro",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.0,
    "timeout_seconds": 240,
    "total_timeout_seconds": 480,
    "transport_retries": 2,
    "timeout_growth_factor": 1.5,
    "context_cache": {
        "enabled": True,
        "mode": "automatic",
    },
}

_INT_FIELDS = (
    "max_tokens", "timeout_seconds", "total_timeout_seconds",
    "transport_retries",
)


def default_config_path() -> Path:
    # AutoDL provider is the deployment default; the direct DeepSeek config
    # (configs/detector_v2.json) is retained as an explicit rollback path.
    return Path(__file__).resolve().parent / "configs" / "detector_v2_autodl.json"


def _coerce(route: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(route)
    for key in _INT_FIELDS:
        if key in resolved and not isinstance(resolved[key], int):
            resolved[key] = int(float(resolved[key]))
    for key in ("temperature", "timeout_growth_factor"):
        if key in resolved and not isinstance(resolved[key], (int, float)):
            resolved[key] = float(resolved[key])
    return resolved


def resolve_route(
    name: str,
    route_block: Mapping[str, Any] | None,
    provider_block: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(_PROVIDER_DEFAULTS)
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
    upper = name.upper()
    env_map = {
        "endpoint": os.environ.get("DETECTOR_V2_ENDPOINT"),
        "api_key_env": os.environ.get("DETECTOR_V2_API_KEY_ENV"),
        "model": (
            os.environ.get(f"DETECTOR_V2_{upper}_MODEL")
            or os.environ.get("DETECTOR_V2_MODEL")
        ),
        "temperature": os.environ.get("DETECTOR_V2_TEMPERATURE"),
        "max_tokens": os.environ.get("DETECTOR_V2_MAX_TOKENS"),
        "timeout_seconds": os.environ.get("DETECTOR_V2_TIMEOUT_SECONDS"),
        "total_timeout_seconds": os.environ.get(
            "DETECTOR_V2_TOTAL_TIMEOUT_SECONDS"
        ),
        "transport_retries": os.environ.get("DETECTOR_V2_TRANSPORT_RETRIES"),
    }
    for field, value in env_map.items():
        if value:
            merged[field] = value
    return _coerce(merged)


def load_config(
    path: str | Path | None = None,
    *,
    require_credentials: bool = True,
) -> dict[str, Any]:
    """Load and resolve the detector config (fail-closed on any defect)."""
    load_local_env()
    config_path = Path(path) if path is not None else default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("detector config must be an object")
    schema = str(raw.get("schema_version") or "")
    if schema and schema != CONFIG_SCHEMA:
        raise ValueError(f"unsupported detector config schema: {schema}")
    provider_block = raw.get("provider")
    provider_block = provider_block if isinstance(provider_block, dict) else {}
    resolved: dict[str, Any] = {"schema_version": CONFIG_SCHEMA}
    case_timeout = raw.get("case_timeout_seconds")
    if case_timeout is not None:
        resolved["case_timeout_seconds"] = int(float(case_timeout))
    registry_path = raw.get("intervention_registry")
    if registry_path:
        path = Path(str(registry_path))
        if not path.is_absolute():
            path = (config_path.parent / path).resolve()
        resolved["intervention_registry"] = str(path)
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
