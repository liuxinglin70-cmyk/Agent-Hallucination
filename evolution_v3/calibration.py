"""Noise-floor calibration for V2/M gate tolerances.

The design requires the tolerance to be measured and pre-registered, never a
default assumption. This module runs the empty registry twice on the same
calibration cases and records the observed paired flip distribution.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from evolution_v3.backends import DetectionBackend, make_backend
from evolution_v3.config import ensure_dirs, load_config, resolve_path
from evolution_v3.contracts import empty_registry, save_json
from evolution_v3.metrics import paired_delta
from evolution_v3.splits import EvolutionSplit, build_split, load_gold

CALIBRATION_SCHEMA = "evolution_v3.gate_calibration.v1"


def run_noise_calibration(
    config: Mapping[str, Any] | None = None,
    *,
    backend: DetectionBackend | None = None,
    split: EvolutionSplit | None = None,
    mini: bool = False,
) -> dict[str, Any]:
    config = dict(load_config()) if config is None else dict(config)
    ensure_dirs(config)
    backend = backend or make_backend(config)
    split = split or build_split(
        config=config,
        dataset_root=config["dataset_root"],
        mini=mini,
    )
    cases = list(split.gain)
    gold_map = {
        case: load_gold(config["dataset_root"], case) for case in cases
    }
    empty = empty_registry()
    workers = int((config.get("backend") or {}).get("workers", 1))
    first = backend.evaluate(
        cases,
        dataset_root=config["dataset_root"],
        registry=empty,
        gold_map=gold_map,
        workers=workers,
    )
    # The second arm must be a genuine rerun. CachingBackend would otherwise
    # return the first arm from cache and report a fake zero noise floor.
    rerun_backend = getattr(backend, "backend", backend)
    second = rerun_backend.evaluate(
        cases,
        dataset_root=config["dataset_root"],
        registry=empty,
        gold_map=gold_map,
        workers=workers,
    )
    delta = paired_delta(first, second)
    flip_cases = sum(1 for row in delta["rows"] if row["baseline_prediction"] != row["active_prediction"])
    payload = {
        "schema_version": CALIBRATION_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": backend.name,
        "split": "mini" if mini else "phase2_v1",
        "cases": cases,
        "n": len(cases),
        "empty_rerun_flip_cases": flip_cases,
        "empty_rerun_positive_net": max(0, int(delta["marginal_cases"])),
        "empty_rerun_flips": delta["flips"],
        "pre_registered_v2_tolerances": {
            "new_fn_allowed": 0,
            "new_fp_allowed": 0,
            "new_unresolved_allowed": 0,
        },
        "note": (
            "V2 keeps the design's zero-harm sentinel rule. The measured "
            "positive net is the pre-registered floor M must beat on the "
            "same backend before formal cycles."
        ),
    }
    save_json(resolve_path(config["gate_calibration"]), payload)
    return payload


__all__ = ["CALIBRATION_SCHEMA", "run_noise_calibration"]
