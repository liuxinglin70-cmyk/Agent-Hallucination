"""Gate calibration: the single source of every promotion tolerance.

The evolution loop promotes on cumulative paired evidence (G2); the
numbers below say how much noise that evidence may carry. They start at
absolute-zero discipline (the measured-safe defaults) and are
recalibrated ONLY from a Round-baseline stability double-run
(``tools/run_stability.py`` over two same-code runs), never from the
frozen verification split — that split is the full-experiment acceptance
surface and must stay outside the loop.

A ``test_retest_flip_rate`` of ``null`` means the noise floor has not
been measured on the current verdict surface; the paired gates then keep
their absolute readings (no statistical slack is inferred).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

GATE_CALIBRATION_SCHEMA = "sehd2.gate_calibration.v1"
DEFAULT_CALIBRATION_PATH = Path(
    "datasets/calibration/gate_calibration.json",
)

_REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "convention_g2": (
        "max_paired_sentinel_loss", "max_paired_clean_conviction",
        "max_reproduced_discovery_harm", "min_paired_net_gain",
    ),
    "policy_cycle": ("max_added_fn_vs_baseline", "max_tp_lost"),
}


def validate_calibration(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Closed check of one calibration payload (fail-closed)."""
    if payload.get("schema_version") != GATE_CALIBRATION_SCHEMA:
        raise ValueError(
            f"gate calibration schema {payload.get('schema_version')!r} "
            f"is not {GATE_CALIBRATION_SCHEMA!r}"
        )
    flip_rate = payload.get("test_retest_flip_rate")
    if flip_rate is not None and not (
        isinstance(flip_rate, (int, float)) and 0 <= flip_rate <= 1
    ):
        raise ValueError(
            "test_retest_flip_rate must be null or a rate in [0, 1]"
        )
    for section, fields in _REQUIRED_SECTIONS.items():
        block = payload.get(section)
        if not isinstance(block, Mapping):
            raise ValueError(f"gate calibration lacks a {section} block")
        for field in fields:
            value = block.get(field)
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"gate calibration {section}.{field} must be a "
                    "non-negative integer"
                )
    return dict(payload)


def load_calibration(path: str | Path | None = None) -> dict[str, Any]:
    """Load the gate calibration; the tracked file is the single source."""
    calibration_path = Path(path) if path is not None else (
        DEFAULT_CALIBRATION_PATH
    )
    if not calibration_path.exists():
        raise ValueError(
            f"gate calibration missing at {calibration_path} — the "
            "promotion gates refuse to run uncalibrated"
        )
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    return validate_calibration(payload)


__all__ = [
    "DEFAULT_CALIBRATION_PATH",
    "GATE_CALIBRATION_SCHEMA",
    "load_calibration",
    "validate_calibration",
]
