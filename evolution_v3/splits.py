"""Frozen data layout: sentinel S, marginal G, patrol pool P, read-once final.

The final 300 verification cases are guarded by :class:`FinalHoldout`. The
evolution engine never joins their gold; only the dedicated final command can
read them, exactly once.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from detector_v2.dataset import load_case

from evolution_v3.contracts import load_json


def bool_gold(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() == "true"


def load_gold(dataset_root: str | Path, case_id: str) -> bool | None:
    _record, gold = load_case(dataset_root, case_id)
    return bool_gold(gold.get("is_hallucination"))


def load_record(dataset_root: str | Path, case_id: str) -> dict[str, Any]:
    record, _gold = load_case(dataset_root, case_id)
    return record


def _source(case_id: str) -> str:
    return case_id.split("/", 1)[0]


def _quota_sample(
    cases: Sequence[str],
    n: int,
    rng: random.Random,
) -> list[str]:
    if n <= 0 or not cases:
        return []
    if n >= len(cases):
        return list(cases)
    by_source: dict[str, list[str]] = {}
    for case in cases:
        by_source.setdefault(_source(case), []).append(case)
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    total = len(cases)
    allocated = 0
    for source, items in by_source.items():
        exact = n * len(items) / total
        floor = int(exact)
        quotas[source] = floor
        allocated += floor
        remainders.append((exact - floor, source))
    for _fraction, source in sorted(remainders, reverse=True):
        if allocated >= n:
            break
        quotas[source] += 1
        allocated += 1
    picked: list[str] = []
    for source, items in sorted(by_source.items()):
        picked.extend(rng.sample(items, min(quotas[source], len(items))))
    rng.shuffle(picked)
    return picked


@dataclass
class EvolutionSplit:
    name: str
    pool: list[str] = field(default_factory=list)
    sentinel: list[str] = field(default_factory=list)
    gain: list[str] = field(default_factory=list)
    patrol_pool: list[str] = field(default_factory=list)
    final: list[dict[str, Any]] = field(default_factory=list)
    final_holdout: "FinalHoldout | None" = None
    mini: bool = False
    seed: int = 20260817

    def patrol_sample(self, cycle: int, n: int) -> list[str]:
        if n >= len(self.patrol_pool):
            return list(self.patrol_pool)
        rng = random.Random(self.seed + cycle * 7919)
        return rng.sample(self.patrol_pool, n)


class FinalHoldout:
    """Read-once guard for the 300 final verification cases.

    Besides the in-process counter, a marker file is written on first read so
    a fresh process (``python -m evolution_v3 final`` again) is refused too.
    """

    def __init__(
        self,
        cases: Sequence[Mapping[str, Any]],
        marker_path: str | Path | None = None,
    ) -> None:
        self.cases = [dict(item) for item in cases]
        self.marker_path = Path(marker_path) if marker_path else None
        self.reads = 0
        self.read_reasons: list[str] = []

    def read(self, reason: str = "final_verification") -> list[dict[str, Any]]:
        if self.marker_path is not None and self.marker_path.exists():
            previous = {}
            try:
                previous = json.loads(
                    self.marker_path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                pass
            raise RuntimeError(
                "final verification read-once violation: marker already "
                f"exists at {self.marker_path} "
                f"(previous_read={previous.get('reason')})"
            )
        self.reads += 1
        self.read_reasons.append(reason)
        if self.reads > 1:
            raise RuntimeError(
                "final verification read-once violation: "
                f"reads={self.reads}, reasons={self.read_reasons}"
            )
        if self.marker_path is not None:
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            case_ids = sorted(str(item.get("case_id") or "") for item in self.cases)
            self.marker_path.write_text(
                json.dumps(
                    {
                        "reason": reason,
                        "reads": self.reads,
                        "case_ids_hash": hashlib.sha256(
                            "\n".join(case_ids).encode("utf-8")
                        ).hexdigest(),
                        "n_cases": len(self.cases),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        return [dict(item) for item in self.cases]


def _mini_pool(manifest: Mapping[str, Any], size: int) -> list[str]:
    evolution_set = manifest.get("evolution_set") or {}
    cases = list(evolution_set.get("cases") or [])
    declared_size = evolution_set.get("size")
    if declared_size is not None and int(declared_size) != len(cases):
        raise ValueError(
            "mini manifest evolution_set.size does not match its case list"
        )
    if len(cases) != len(set(cases)):
        raise ValueError("mini manifest contains duplicate case ids")
    if size > len(cases):
        raise ValueError(
            f"mini_pool_size {size} exceeds manifest size {len(cases)}"
        )
    return cases[:size]


def _full_pool(manifest: Mapping[str, Any]) -> list[str]:
    pool = manifest.get("evolution_pool") or {}
    cases = list(pool.get("cases") or [])
    if not cases or len(cases) != len(set(cases)):
        raise ValueError("full manifest evolution_pool must be non-empty and unique")
    return cases


def _final_cases(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    final = manifest.get("final_verification") or {}
    return [dict(item) for item in (final.get("cases") or [])]


def build_split(
    *,
    config: Mapping[str, Any],
    dataset_root: str | Path,
    mini: bool = False,
) -> EvolutionSplit:
    split_cfg = config.get("split") or {}
    manifest_path = config["mini_split_manifest"] if mini else config["split_manifest"]
    manifest = load_json(manifest_path)
    seed = int(split_cfg.get("seed", 20260817))
    rng = random.Random(seed)
    if mini:
        pool = _mini_pool(manifest, int(split_cfg.get("mini_pool_size", 32)))
        final: list[dict[str, Any]] = []
    else:
        pool = _full_pool(manifest)
        final = _final_cases(manifest)
        final_ids = [str(item.get("case_id") or "") for item in final]
        if not final_ids or any(not case for case in final_ids):
            raise ValueError("final_verification must contain valid case ids")
        if len(final_ids) != len(set(final_ids)):
            raise ValueError("final_verification contains duplicate case ids")
        overlap = sorted(set(pool) & set(final_ids))
        if overlap:
            raise ValueError(
                f"evolution pool overlaps final verification: {overlap[:5]}"
            )

    gold = {
        case: load_gold(dataset_root, case)
        for case in pool
    }
    positives = [case for case in pool if gold.get(case) is True]
    negatives = [case for case in pool if gold.get(case) is False]
    unknown = [case for case in pool if gold.get(case) is None]
    sentinel_pos = _quota_sample(
        positives,
        int(split_cfg.get("sentinel_positives", 10)),
        rng,
    )
    sentinel_neg = _quota_sample(
        negatives,
        int(split_cfg.get("sentinel_negatives", 10)),
        rng,
    )
    sentinel = sorted(set(sentinel_pos + sentinel_neg))
    remaining = [
        case for case in pool
        if case not in set(sentinel) and case not in set(unknown)
    ]
    gain_size = (
        int(split_cfg.get("mini_gain_size", 16)) if mini
        else int(split_cfg.get("gain_set_size", 80))
    )
    gain = _quota_sample(remaining, gain_size, rng)
    patrol_pool = [
        case for case in remaining if case not in set(gain)
    ]
    marker_path = None
    if not mini:
        marker_path = (
            Path(str(config.get("work_root") or ".")) / "final"
            / "READ_ONCE_MARKER.json"
        )
    holdout = None if mini else FinalHoldout(final, marker_path=marker_path)
    return EvolutionSplit(
        name="mini" if mini else "phase2_v1",
        pool=pool,
        sentinel=sentinel,
        gain=gain,
        patrol_pool=patrol_pool,
        final=final,
        final_holdout=holdout,
        mini=mini,
        seed=seed,
    )


def split_manifest_report(split: EvolutionSplit) -> dict[str, Any]:
    return {
        "name": split.name,
        "mini": split.mini,
        "seed": split.seed,
        "pool_size": len(split.pool),
        "sentinel": split.sentinel,
        "sentinel_size": len(split.sentinel),
        "gain": split.gain,
        "gain_size": len(split.gain),
        "patrol_pool_size": len(split.patrol_pool),
        "final_size": len(split.final),
        "final_reads": (
            split.final_holdout.reads if split.final_holdout else 0
        ),
    }


__all__ = [
    "EvolutionSplit",
    "FinalHoldout",
    "bool_gold",
    "build_split",
    "load_gold",
    "load_record",
    "split_manifest_report",
]
