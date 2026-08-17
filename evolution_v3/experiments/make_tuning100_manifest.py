"""Freeze the 100-case tuning split and the 50-case external validation set.

- Tuning 100 = S(20 correct) + P(40 patrol/nomination) + G(40 V3/M).
  No report set is cut from the 100 cases.
- Validation 50 is sampled from phase2 evolution_pool minus the 100 cases,
  never from final_verification.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector_v2.result import prediction_state
from evolution_v3.config import load_config
from evolution_v3.contracts import save_json
from evolution_v3.splits import _quota_sample, load_gold
from evolution_v3.worksheet import failure_family

CASES_100 = ROOT / "detector_v2" / "experiments" / "v2_100_cases.json"
RUN_100 = ROOT / "runs_v2" / "v2_100_20w"
OUT_TUNING = ROOT / "datasets" / "eval_splits" / "evolution_v3_tuning100.json"
OUT_VAL = ROOT / "datasets" / "eval_splits" / "evolution_v3_validation50.json"
PHASE2 = ROOT / "datasets" / "eval_splits" / "phase2_v1" / "manifest.json"
SEED_TUNING = 20260817
SEED_VALIDATION = 20260818


def _load_cached_case(case: str, run_root: Path) -> dict[str, Any]:
    safe = case.replace("/", "__")
    return json.loads(
        (run_root / "cases" / safe / "result.json").read_text(
            encoding="utf-8-sig"
        )
    )


def _load_gold_map(config: dict[str, Any], cases: list[str]) -> dict[str, bool]:
    return {
        case: bool(load_gold(config["dataset_root"], case))
        for case in cases
    }


def build_tuning_manifest(config: dict[str, Any]) -> dict[str, Any]:
    cases = json.loads(CASES_100.read_text(encoding="utf-8"))
    gold = _load_gold_map(config, cases)
    results = {case: _load_cached_case(case, RUN_100) for case in cases}
    pred = {case: prediction_state(results[case]) for case in cases}
    correct = [c for c in cases if pred[c] == gold[c]]
    errors = [c for c in cases if pred[c] != gold[c]]

    rng = random.Random(SEED_TUNING)
    sentinel_pos = _quota_sample(
        [c for c in correct if gold[c]], 10, rng,
    )
    sentinel_neg = _quota_sample(
        [c for c in correct if not gold[c]], 10, rng,
    )
    sentinel = sorted(set(sentinel_pos + sentinel_neg))

    # Put at least two cases of each stable failure family into P for
    # nomination and at least two of each into G for V3/M measurement.
    families: dict[str, list[str]] = {}
    for case in errors:
        row = {
            "case": case,
            "gold": gold[case],
            "prediction": pred[case],
            "result": results[case],
        }
        family = failure_family(row)
        families.setdefault(family, []).append(case)
    patrol_errors: list[str] = []
    gate_errors: list[str] = []
    stable = sorted(
        families.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    for family, members in stable:
        members = list(members)
        rng.shuffle(members)
        if len(members) < 2:
            gate_errors.extend(members)
            continue
        patrol_errors.extend(members[:2])
        gate_errors.extend(members[2:])
    patrol_extra = _quota_sample(
        [c for c in cases if c not in set(sentinel) and c not in set(patrol_errors + gate_errors)],
        40 - len(patrol_errors),
        rng,
    )
    patrol = sorted(set(patrol_errors + patrol_extra))
    gate = sorted(
        set(cases) - set(sentinel) - set(patrol)
    )
    manifest = {
        "schema_version": "evolution_v3.tuning100.v1",
        "created_from": str(RUN_100),
        "seed": SEED_TUNING,
        "cases": cases,
        "sentinel": sentinel,
        "patrol": patrol,
        "gate": gate,
        "family_seed": {
            family: {
                "count": len(members),
                "patrol": [c for c in members if c in set(patrol_errors)],
                "gate": [c for c in members if c in set(gate_errors)],
            }
            for family, members in families.items()
            if len(members) >= 2
        },
        "sizes": {
            "total": len(cases),
            "sentinel": len(sentinel),
            "patrol": len(patrol),
            "gate": len(gate),
        },
    }
    save_json(OUT_TUNING, manifest)
    return manifest


def build_validation_manifest(config: dict[str, Any]) -> dict[str, Any]:
    cases_100 = set(json.loads(CASES_100.read_text(encoding="utf-8")))
    phase2 = json.loads(PHASE2.read_text(encoding="utf-8"))
    pool = set(phase2["evolution_pool"]["cases"]) - cases_100
    gold = _load_gold_map(config, sorted(pool))
    rng = random.Random(SEED_VALIDATION)
    pos = _quota_sample(
        sorted(c for c in pool if gold[c]), 25, rng,
    )
    neg = _quota_sample(
        sorted(c for c in pool if not gold[c]), 25, rng,
    )
    validation = sorted(set(pos + neg))
    manifest = {
        "schema_version": "evolution_v3.validation50.v1",
        "seed": SEED_VALIDATION,
        "cases": validation,
        "gold": {case: gold[case] for case in validation},
        "sizes": {"total": len(validation)},
        "rule": (
            "sampled from phase2_v1 evolution_pool minus the 100 tuning "
            "cases; final_verification is never touched"
        ),
    }
    save_json(OUT_VAL, manifest)
    return manifest


def main() -> int:
    config = load_config()
    tuning = build_tuning_manifest(config)
    validation = build_validation_manifest(config)
    print(json.dumps({
        "tuning_sizes": tuning["sizes"],
        "validation_cases": len(validation["cases"]),
        "validation_positives": sum(
            1 for case in validation["cases"] if validation["gold"][case]
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
