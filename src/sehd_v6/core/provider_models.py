"""Canonical provider model names shared by route validators."""

from __future__ import annotations


MODEL_ALIASES = {
    "DeepSeek-V4-Flash": "deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "DeepSeek-V4-Pro": "deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek-v4-pro",
}

def canonical_model_name(model: str) -> str:
    """Return the provider wire name or raise for an unregistered model."""

    value = str(model or "")
    try:
        return MODEL_ALIASES[value]
    except KeyError as exc:
        raise ValueError(f"Disallowed provider model: {value!r}") from exc
