"""Explicit per-request output ceilings shared by stages and retries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

REQUEST_BUDGET_DEFAULTS = {
    "max_output_tokens_per_request": None,
    "long_input_threshold_characters": 48000,
    "long_output_tokens_per_request": None,
}

@dataclass(frozen=True)
class RequestBudget:
    input_characters: int
    long_input: bool
    output_token_limit: int | None


def bound_request_route(
    route: Mapping[str, Any], *, system: str, user: str,
) -> tuple[dict[str, Any], RequestBudget]:
    input_characters = len(system) + len(user)
    limits = {**REQUEST_BUDGET_DEFAULTS, **route}
    for key in REQUEST_BUDGET_DEFAULTS:
        if limits[key] is None and key != "long_input_threshold_characters":
            continue
        if type(limits[key]) is not int or limits[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if route.get("max_tokens") is not None and (type(route["max_tokens"]) is not int or route["max_tokens"] < 1):
        raise ValueError("max_tokens must be a positive integer")
    long_input = input_characters >= int(limits["long_input_threshold_characters"])
    configured = [route.get("max_tokens"), limits["max_output_tokens_per_request"]]
    if long_input:
        configured.append(limits["long_output_tokens_per_request"])
    ceilings = [int(value) for value in configured if value is not None]
    ceiling = min(ceilings) if ceilings else None
    return {**route, "max_tokens": ceiling}, RequestBudget(input_characters, long_input, ceiling)
