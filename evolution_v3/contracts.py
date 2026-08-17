"""Evolution-side intervention contracts.

All schema validation and hashing are delegated to ``detector_v2.interventions``
so a deployable payload can never drift from the executor that actually runs
it. The evolution package only adds V1-facing text/claim helpers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from detector_v2.interventions import (
    build_intervention_registry,
    intervention_content_hash,
    load_intervention_registry,
    validate_intervention,
)

_CASE_ID_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_-]{0,40}(?:/|__)\d{3}\.json\b",
    re.IGNORECASE,
)


def empty_registry() -> dict[str, Any]:
    return build_intervention_registry([])


def make_registry(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return build_intervention_registry(entries)


def make_intervention(
    *,
    intervention_id: str,
    intervention_type: str,
    hook: str,
    packet_features: Sequence[str],
    body: Mapping[str, Any],
    expected_behavior: str,
    target_polarity: str,
    cycle: int,
    failure_family: str,
    failure_cases: Sequence[str],
    facts: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "detector_v2.intervention.v1",
        "intervention_id": intervention_id,
        "type": intervention_type,
        "hook": hook,
        "trigger": {
            "packet_features": [str(x) for x in packet_features],
            "facts": [str(x) for x in (facts or ())],
        },
        "body": dict(body),
        "claim": {
            "expected_behavior": str(expected_behavior),
            "target_polarity": str(target_polarity),
        },
        "provenance": {
            "cycle": int(cycle),
            "failure_family": str(failure_family),
            "failure_cases": [str(x) for x in failure_cases],
        },
    }
    validate_intervention(payload)
    payload["content_hash"] = intervention_content_hash(payload)
    return payload


def validate_candidate(entry: Mapping[str, Any]) -> dict[str, Any]:
    validate_intervention(entry)
    return dict(entry)


def load_registry(path: str | Path) -> dict[str, Any]:
    return load_intervention_registry(path)


def iter_strings(value: Any, *, keys: Iterable[str] | None = None) -> list[str]:
    """Depth-first strings, optionally restricted to selected dict keys."""
    rows: list[str] = []
    if isinstance(value, str):
        rows.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if keys is None or key in keys:
                rows.extend(iter_strings(item, keys=keys))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            rows.extend(iter_strings(item, keys=keys))
    return rows


def candidate_prose_text(entry: Mapping[str, Any]) -> str:
    body = entry.get("body") or {}
    claim = entry.get("claim") or {}
    strings = [
        str(body.get("directive") or ""),
        str(body.get("not_when") or ""),
        str(body.get("action_not_when") or ""),
        str(body.get("predicate") or ""),
        str(body.get("runner") or ""),
        str(body.get("action") or ""),
        str(claim.get("expected_behavior") or ""),
        str(claim.get("target_polarity") or ""),
    ]
    return "\n".join(strings)


def candidate_contains_case_id(entry: Mapping[str, Any]) -> str | None:
    text = candidate_prose_text(entry)
    match = _CASE_ID_RE.search(text)
    return match.group(0) if match else None


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    }


def jaccard(left: str, right: str) -> float:
    a = tokenize(left)
    b = tokenize(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "candidate_contains_case_id",
    "candidate_prose_text",
    "empty_registry",
    "jaccard",
    "load_json",
    "load_registry",
    "make_intervention",
    "make_registry",
    "save_json",
    "validate_candidate",
]
