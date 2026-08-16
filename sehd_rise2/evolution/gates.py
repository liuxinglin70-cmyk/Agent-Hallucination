"""Round constraints: runner whitelist and composition tolerance.

Research posture: the loop's evidence is the flip itself (a wrong case
becoming right under the artifact) plus the read-once validation
report — not pre-calibrated statistical tolerances. The only numbers
kept here are the two the machinery cannot infer on its own: which
predicate runners proposals may use this round, and how much net gain
a composed registry may lose to interaction before it is held.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.capabilities import PREDICATE_RUNNERS

GATE_CALIBRATION_SCHEMA = "sehd2.gate_calibration.v2"
DEFAULT_CALIBRATION_PATH = Path(
    "datasets/calibration/gate_calibration.json",
)

_REQUIRED_INT_FIELDS: dict[str, tuple[str, ...]] = {
    "composition": ("max_interaction_loss",),
}

_REQUIRED_RUNNER_LIST_FIELDS: dict[str, tuple[str, ...]] = {
    "capability_proposal": ("predicate_runners",),
}


def validate_calibration(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Closed check of one calibration payload (fail-closed)."""
    if payload.get("schema_version") != GATE_CALIBRATION_SCHEMA:
        raise ValueError(
            f"gate calibration schema {payload.get('schema_version')!r} "
            f"is not {GATE_CALIBRATION_SCHEMA!r}"
        )
    sections = (
        set(_REQUIRED_INT_FIELDS)
        | set(_REQUIRED_RUNNER_LIST_FIELDS)
    )
    for section in sorted(sections):
        block = payload.get(section)
        if not isinstance(block, Mapping):
            raise ValueError(f"gate calibration lacks a {section} block")
        for field in _REQUIRED_INT_FIELDS.get(section, ()):
            value = block.get(field)
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"gate calibration {section}.{field} must be a "
                    "non-negative integer"
                )
        for field in _REQUIRED_RUNNER_LIST_FIELDS.get(section, ()):
            value = block.get(field)
            if not (
                isinstance(value, list)
                and value
                and all(isinstance(item, str) for item in value)
            ):
                raise ValueError(
                    f"gate calibration {section}.{field} must be a "
                    "non-empty list of runner names"
                )
            unknown = set(value) - set(PREDICATE_RUNNERS)
            if unknown:
                raise ValueError(
                    f"gate calibration {section}.{field} names runners "
                    f"outside the closed vocabulary: {sorted(unknown)}"
                )
    return dict(payload)


def load_calibration(path: str | Path | None = None) -> dict[str, Any]:
    """Load the round constraints; the tracked file is the single source."""
    calibration_path = Path(path) if path is not None else (
        DEFAULT_CALIBRATION_PATH
    )
    if not calibration_path.exists():
        raise ValueError(
            f"gate calibration missing at {calibration_path} — the "
            "evolution round refuses to run without its constraints file"
        )
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    return validate_calibration(payload)


__all__ = [
    "DEFAULT_CALIBRATION_PATH",
    "GATE_CALIBRATION_SCHEMA",
    "load_calibration",
    "validate_calibration",
]
