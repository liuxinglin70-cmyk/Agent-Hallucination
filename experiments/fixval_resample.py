"""Fix-val round resampling: fully fresh stratified split per round.

Each round draws 100 untouched cases (60 evolution + 40 validation) from
the pool that never entered any recorded run: source proportions by
largest remainder within each polarity, fixed seed, per-case lists in a
manifest committed before any round runs. Resampling each round is the
anti-overfit structure: the framework's memory accumulates across
rounds, the data never does.

Usage:
    python -m experiments.fixval_resample --round 1 [--seed 20260816]
"""

from __future__ import annotations

import argparse
import glob
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

DATASET_ROOT = Path("datasets/raw/agenthallu/AgentHallu")
SCHEMA = "sehd2.fixval_split.v1"


def _source(case_id: str) -> str:
    return case_id.rsplit("/", 1)[0]


def _gold_polarity(case_id: str) -> bool:
    raw = json.loads(
        (DATASET_ROOT / case_id).read_text(encoding="utf-8"),
    )
    value = raw.get("is_hallucination")
    if isinstance(value, bool):
        return value
    return {"true": True, "false": False}.get(
        str(value).strip().lower(),
    ) is True


def _allocate(counts: Counter[str], seats: int) -> dict[str, int]:
    """Proportional source quotas by largest remainder."""
    total = sum(counts.values())
    raw = {
        source: seats * n / total for source, n in counts.items()
    }
    quota = {source: int(value) for source, value in raw.items()}
    remaining = seats - sum(quota.values())
    by_remainder = sorted(
        counts, key=lambda s: (raw[s] - quota[s], counts[s]),
        reverse=True,
    )
    for source in by_remainder[:remaining]:
        quota[source] += 1
    return {s: n for s, n in quota.items() if n > 0}


def _stratified_sample(
    pool: list[str], seats: int, rng: random.Random,
) -> list[str]:
    by_source: dict[str, list[str]] = {}
    for case_id in pool:
        by_source.setdefault(_source(case_id), []).append(case_id)
    counts = Counter({s: len(v) for s, v in by_source.items()})
    quota = _allocate(counts, seats)
    picked: list[str] = []
    for source, n in sorted(quota.items()):
        candidates = sorted(by_source[source])
        rng.shuffle(candidates)
        picked.extend(candidates[:n])
    return sorted(picked)


def resample(round_no: int, seed: int) -> dict[str, Any]:
    contamination: set[str] = set()
    for run_json in glob.glob("runs/*/run.json"):
        payload = json.loads(Path(run_json).read_text(encoding="utf-8"))
        contamination.update(
            str(case) for case in payload.get("case_ids") or []
        )
    everything = sorted(
        f"{path.parent.name}/{path.name}"
        for path in DATASET_ROOT.glob("*/*.json")
    )
    fresh = [case for case in everything if case not in contamination]
    fresh_error = sorted(c for c in fresh if _gold_polarity(c))
    fresh_clean = sorted(c for c in fresh if not _gold_polarity(c))
    rng = random.Random(seed)
    validation = sorted(
        _stratified_sample(fresh_error, 24, rng)
        + _stratified_sample(fresh_clean, 16, rng),
    )
    evolution = sorted(
        _stratified_sample(
            sorted(set(fresh_error) - set(validation)), 36, rng,
        )
        + _stratified_sample(
            sorted(set(fresh_clean) - set(validation)), 24, rng,
        ),
    )
    overlap = set(evolution) & set(validation)
    if overlap:
        raise ValueError(f"split overlap: {overlap}")

    def block(cases: list[str]) -> dict[str, Any]:
        return {
            "cases": cases,
            "size": len(cases),
            "polarity": {
                "error": sum(1 for c in cases if _gold_polarity(c)),
                "clean": sum(1 for c in cases if not _gold_polarity(c)),
            },
        }

    return {
        "schema_version": SCHEMA,
        "round": round_no,
        "seed": seed,
        "contamination_union_size": len(contamination),
        "fresh_universe": {
            "error": len(fresh_error),
            "clean": len(fresh_clean),
        },
        "stratification": (
            "source proportions by largest remainder within each "
            "polarity; every case never entered a recorded run"
        ),
        "evolution_set": block(evolution),
        "validation_set": block(validation),
        "freeze_rule": (
            "committed before any round detector run; never edited — "
            "a changed split requires a new version directory"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    seed = args.seed or 20260816 + args.round
    manifest = resample(args.round, seed)
    import datetime
    manifest["created_at"] = datetime.datetime.now().isoformat(
        timespec="seconds",
    )
    out_dir = Path(
        f"datasets/eval_splits/fixval_round{args.round}_v1",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "evolution_cases.json").write_text(
        json.dumps(manifest["evolution_set"]["cases"]) + "\n",
        encoding="utf-8",
    )
    (out_dir / "validation_cases.json").write_text(
        json.dumps(manifest["validation_set"]["cases"]) + "\n",
        encoding="utf-8",
    )
    print(
        f"round {args.round}: evolution {manifest['evolution_set']['size']} "
        f"({manifest['evolution_set']['polarity']}), validation "
        f"{manifest['validation_set']['size']} "
        f"({manifest['validation_set']['polarity']}) -> {out_dir}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
