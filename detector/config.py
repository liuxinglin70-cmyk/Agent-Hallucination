"""Detector configuration: judge, defense and recall provider routes.

Resolution order (highest wins):

1. ``DETECTOR_*`` process environment
2. route block in the config JSON
3. ``provider`` block in the config JSON
4. built-in defaults

Provider credentials are referenced by environment-variable name.
"""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from detector.env import load_local_env

CONFIG_SCHEMA = "detector.config.v1"
ROUTE_NAMES = ("judge", "defense", "recall")

# SOURCE: resolve_route
DETECTOR_ENV_KEYS: tuple[str, ...] = (
    "DETECTOR_ENDPOINT",
    "DETECTOR_API_KEY_ENV",
    # The per-route model override, then the generic fallback. Upgrading and
    # tracing run on the judge route, so they have no key of their own.
    *(f"DETECTOR_{name.upper()}_MODEL" for name in ROUTE_NAMES),
    "DETECTOR_MODEL",
    "DETECTOR_TEMPERATURE",
    "DETECTOR_MAX_TOKENS",
    "DETECTOR_TIMEOUT_SECONDS",
    "DETECTOR_TOTAL_TIMEOUT_SECONDS",
    "DETECTOR_TRANSPORT_RETRIES",
)


def detector_env_fingerprint() -> dict[str, str]:
    """The complete provider override key set, including unset values."""
    return {key: str(os.environ.get(key, "")) for key in DETECTOR_ENV_KEYS}

_PROVIDER_DEFAULTS: dict[str, Any] = {
    "endpoint": "https://www.autodl.art/api/v1",
    "model": "GLM-5.3-flash",
    "api_key_env": "AUTODL_API_KEY",
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

_INTEGER_MINIMUMS = {
    "max_tokens": 1, "timeout_seconds": 1, "total_timeout_seconds": 1,
    "transport_retries": 0, "votes": 1, "vote_topup": 0, "vote_concurrency": 1,
}
_ROUTE_FIELDS = frozenset(_PROVIDER_DEFAULTS) | frozenset(_INTEGER_MINIMUMS) | {
    "json_mode", "extra_body",
}
_CAUSAL_DEFAULTS = {
    "mode": "tracer_context", "score_source": "semantic_proxy", "policy_version": "dataset-origin-risk-v2",
    "top_k": 3, "max_projection_events": 16, "max_graph_edges": 64,
    "max_dependency_depth": 8, "max_added_provider_requests_per_case": 1,
    "max_added_prompt_tokens_per_case": 6000, "max_added_output_tokens_per_case": 1500,
    "max_added_seconds_per_case": 30, "counterfactual_mode": "deterministic_only",
    "negative_feedback_enabled": False, "negative_feedback_policy": None,
    "scorer_artifact": None,
}


def default_config_path() -> Path:
    # AutoDL provider is the deployment default; the direct DeepSeek config
    # (configs/deepseek.json) is retained as an explicit rollback path.
    return Path(__file__).resolve().parent / "configs" / "autodl.json"


def _object(value: Any, name: str, allowed: set | frozenset | dict) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{name} has unknown keys: {sorted(unknown)}")
    return deepcopy(dict(value))


def _integer(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str):
        try:
            decimal = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"{name} must be an integer") from None
        if not decimal.is_finite() or decimal != decimal.to_integral_value():
            raise ValueError(f"{name} must be an integer")
        number = int(decimal)
    else:
        raise ValueError(f"{name} must be an integer")
    if number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return number


def _number(value: Any, name: str, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"{name} is outside its allowed range")
    return number


def _validate_route(route: dict[str, Any]) -> dict[str, Any]:
    resolved = deepcopy(route)
    for key in ("endpoint", "model", "api_key_env"):
        if not isinstance(resolved.get(key), str) or not resolved[key].strip():
            raise ValueError(f"detector route {key} must be a non-empty string")
        resolved[key] = resolved[key].strip()
    for key, minimum in _INTEGER_MINIMUMS.items():
        if key in resolved and resolved[key] is not None:
            resolved[key] = _integer(resolved[key], key, minimum)
    resolved["temperature"] = _number(resolved["temperature"], "temperature", 0, 2)
    resolved["timeout_growth_factor"] = _number(resolved["timeout_growth_factor"], "timeout_growth_factor", 1)
    if "json_mode" in resolved and not isinstance(resolved["json_mode"], bool):
        raise ValueError("json_mode must be a boolean")
    cache = _object(resolved["context_cache"], "context_cache", {"enabled", "mode", "key_prefix", "retention"})
    if "enabled" in cache and not isinstance(cache["enabled"], bool):
        raise ValueError("context_cache.enabled must be a boolean")
    mode = cache.get("mode", "automatic")
    if not isinstance(mode, str) or mode not in {"automatic", "prompt_cache_key"}:
        raise ValueError("context_cache.mode is unsupported")
    resolved["context_cache"] = cache
    if "extra_body" in resolved:
        if not isinstance(resolved["extra_body"], Mapping):
            raise ValueError("extra_body must be an object")
        try:
            json.dumps(resolved["extra_body"], allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("extra_body must contain finite JSON values") from None
    return resolved


def validate_causal_config(raw: Any, *, base_dir: str | Path) -> dict[str, Any]:
    policy = {**_CAUSAL_DEFAULTS, **_object(raw, "causal_audit", _CAUSAL_DEFAULTS)}
    for key, choices in {
        "mode": {"off", "shadow", "tracer_context"},
        "score_source": {"semantic_proxy", "teacher_forced_ppl"},
        "counterfactual_mode": {"deterministic_only", "model_hypothesis"},
    }.items():
        if not isinstance(policy[key], str) or policy[key] not in choices:
            raise ValueError(f"causal_audit.{key} is unsupported")
    if not isinstance(policy["policy_version"], str) or not policy["policy_version"].strip():
        raise ValueError("causal_audit.policy_version must be a non-empty string")
    for key, default in _CAUSAL_DEFAULTS.items():
        if isinstance(default, int) and not isinstance(default, bool):
            policy[key] = _integer(policy[key], f"causal_audit.{key}", 0 if key.startswith("max_added_") else 1)
    if not isinstance(policy["negative_feedback_enabled"], bool):
        raise ValueError("causal_audit.negative_feedback_enabled must be a boolean")
    feedback = policy["negative_feedback_policy"]
    if feedback is not None and not isinstance(feedback, Mapping):
        raise ValueError("causal_audit.negative_feedback_policy must be an object or null")
    if policy["negative_feedback_enabled"] and not feedback:
        raise ValueError("causal_audit.negative_feedback_policy is required when enabled")
    if policy["scorer_artifact"] is not None:
        if not isinstance(policy["scorer_artifact"], (str, Path)) or not str(policy["scorer_artifact"]):
            raise ValueError("causal_audit.scorer_artifact must name an existing artifact")
        artifact = Path(policy["scorer_artifact"])
        artifact = artifact if artifact.is_absolute() else Path(base_dir) / artifact
        artifact = artifact.resolve()
        if not artifact.exists():
            raise ValueError(f"causal_audit.scorer_artifact does not exist: {artifact}")
        policy["scorer_artifact"] = str(artifact)
    return policy


def resolve_route(
    name: str,
    route_block: Mapping[str, Any] | None,
    provider_block: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if name not in ROUTE_NAMES:
        raise ValueError(f"unknown detector route: {name}")
    provider_block = _object(provider_block if provider_block is not None else {}, "provider", _ROUTE_FIELDS)
    route_block = _object(route_block if route_block is not None else {}, f"{name}_route", _ROUTE_FIELDS)
    merged = deepcopy(_PROVIDER_DEFAULTS)
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
        "endpoint": os.environ.get("DETECTOR_ENDPOINT"),
        "api_key_env": os.environ.get("DETECTOR_API_KEY_ENV"),
        "model": (
            os.environ.get(f"DETECTOR_{upper}_MODEL")
            or os.environ.get("DETECTOR_MODEL")
        ),
        "temperature": os.environ.get("DETECTOR_TEMPERATURE"),
        "max_tokens": os.environ.get("DETECTOR_MAX_TOKENS"),
        "timeout_seconds": os.environ.get("DETECTOR_TIMEOUT_SECONDS"),
        "total_timeout_seconds": os.environ.get(
            "DETECTOR_TOTAL_TIMEOUT_SECONDS"
        ),
        "transport_retries": os.environ.get("DETECTOR_TRANSPORT_RETRIES"),
    }
    for field, value in env_map.items():
        if value:
            merged[field] = value
    return _validate_route(merged)


def validate_config(
    raw: Any, *, base_dir: str | Path | None = None,
    require_credentials: bool = False,
) -> dict[str, Any]:
    """Resolve and validate both file and programmatic detector configurations."""
    raw = _object(raw, "detector config", {
        "schema_version", "provider", "case_timeout_seconds", "intervention_registry",
        "causal_audit", *(f"{name}_route" for name in ROUTE_NAMES),
    })
    schema = raw.get("schema_version", CONFIG_SCHEMA)
    if schema != CONFIG_SCHEMA:
        raise ValueError(f"unsupported detector config schema: {schema}")
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    resolved: dict[str, Any] = {"schema_version": CONFIG_SCHEMA}
    if raw.get("case_timeout_seconds") is not None:
        resolved["case_timeout_seconds"] = _integer(raw["case_timeout_seconds"], "case_timeout_seconds", 1)
    registry_path = raw.get("intervention_registry")
    if registry_path is not None:
        if not isinstance(registry_path, (str, Path)) or not str(registry_path):
            raise ValueError("intervention_registry must be a non-empty path")
        path = Path(registry_path)
        resolved["intervention_registry"] = str((path if path.is_absolute() else base / path).resolve())
    for name in ROUTE_NAMES:
        resolved[f"{name}_route"] = resolve_route(name, raw.get(f"{name}_route"), raw.get("provider"))
    resolved["causal_audit"] = validate_causal_config(raw.get("causal_audit", {}), base_dir=base)
    if require_credentials:
        for name in ROUTE_NAMES:
            env_key = resolved[f"{name}_route"]["api_key_env"]
            if not os.environ.get(env_key):
                raise ValueError(f"detector route {name}: credential env var {env_key} is not set")
    return resolved


def load_config(
    path: str | Path | None = None,
    *, require_credentials: bool = True,
) -> dict[str, Any]:
    """Read a config file; credential-free parsing does not load repository secrets."""
    if require_credentials:
        load_local_env()
    config_path = Path(path) if path is not None else default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return validate_config(raw, base_dir=config_path.parent, require_credentials=require_credentials)
