"""Build a clean, question-group-aware B and full evaluation boundary.

The B-stage cases are permanently excluded from the subsequent full holdout.
The script is deterministic and records raw hashes, question-group hashes, and
the polarity/source distribution so the boundary can be audited before any
provider call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO / "datasets/raw/agenthallu/AgentHallu"
OUT_DIR = REPO / "datasets/eval_splits/real_eval_v1"
SEED = 20260820
B_POOL_SIZE = 50
B_VALIDATION_SIZE = 20


def _hash_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _question_hash(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True,
    )
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cases() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        case = path.relative_to(DATASET_ROOT).as_posix()
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        label = str(raw.get("is_hallucination")).strip().lower()
        if label not in {"true", "false"}:
            raise ValueError(f"{case}: invalid is_hallucination")
        result[case] = {
            "case_id": case,
            "gold": label == "true",
            "source": case.split("/", 1)[0],
            "question_group": _question_hash(raw.get("question")),
            "raw_sha256": _hash_bytes(path),
        }
    if len(result) != 693:
        raise ValueError(f"expected 693 cases, found {len(result)}")
    return result


def _pick_groups(
    groups: list[list[str]],
    target: int,
    rng: random.Random,
) -> list[str]:
    """Pick exactly target cases without splitting any question group."""
    shuffled = list(groups)
    rng.shuffle(shuffled)
    possible: dict[int, list[list[str]]] = {0: []}
    for group in shuffled:
        size = len(group)
        for total in sorted(list(possible), reverse=True):
            new_total = total + size
            if new_total > target or new_total in possible:
                continue
            possible[new_total] = [*possible[total], group]
        if target in possible:
            break
    if target not in possible:
        raise ValueError(f"cannot select exactly {target} cases by question group")
    return [case for group in possible[target] for case in group]


def _select(
    records: dict[str, dict[str, Any]],
    target: int,
    rng: random.Random,
    used: set[str],
    *,
    balanced: bool,
) -> list[str]:
    used_groups = {
        str(records[case]["question_group"])
        for case in used
    }
    available = [
        item for case, item in records.items()
        if case not in used
        and str(item["question_group"]) not in used_groups
    ]
    selected: list[str] = []
    labels = (True, False) if balanced else (None,)
    per_label = target // 2 if balanced else target
    for label in labels:
        subset = [
            item for item in available
            if label is None or item["gold"] is label
        ]
        by_group: dict[str, list[str]] = defaultdict(list)
        for item in subset:
            by_group[str(item["question_group"])].append(str(item["case_id"]))
        picked = _pick_groups(list(by_group.values()), per_label, rng)
        selected.extend(picked)
        used.update(picked)
    return selected


def build(seed: int = SEED) -> dict[str, Any]:
    records = _cases()
    rng = random.Random(seed)
    used: set[str] = set()
    validation = _select(records, B_VALIDATION_SIZE, rng, used, balanced=True)
    # The development pool is not used to evaluate B efficacy.  It is kept
    # polarity-balanced enough for failure-family discovery, but source and
    # question groups remain untouched by construction.
    development = _select(records, B_POOL_SIZE, rng, used, balanced=False)
    used_groups = {str(records[case]["question_group"]) for case in used}
    full = sorted(
        case for case, item in records.items()
        if str(item["question_group"]) not in used_groups
    )
    validation_groups = {
        str(records[case]["question_group"]) for case in validation
    }
    development_groups = {
        str(records[case]["question_group"]) for case in development
    }
    if validation_groups & development_groups:
        raise ValueError("question group crossed B development/validation boundary")
    row = lambda case: records[case]
    return {
        "schema_version": "evolution_v3.real_eval_split.v1",
        "seed": seed,
        "dataset": "AgentHallu",
        "dataset_size": len(records),
        "boundary_rule": (
            "question-group-aware; B development and validation are excluded "
            "from the subsequent full holdout"
        ),
        "b_stage": {
            "evolution_set": {
                "cases": sorted(development),
                "size": len(development),
                "records": [row(case) for case in sorted(development)],
            },
            "validation_set": {
                "cases": sorted(validation),
                "size": len(validation),
                "records": [row(case) for case in sorted(validation)],
            },
        },
        # Compatibility projection consumed by build_split(full): the B pool
        # is the only development side; the full stage is read exactly once.
        "evolution_pool": {
            "cases": sorted(development),
            "size": len(development),
        },
        "final_verification": {
            "cases": [row(case) for case in full],
            "size": len(full),
        },
        "full_stage": {
            "cases": full,
            "size": len(full),
            "records": [row(case) for case in full],
        },
        "distribution": {
            "b_evolution": dict(Counter(str(row(case)["gold"]) for case in development)),
            "b_validation": dict(Counter(str(row(case)["gold"]) for case in validation)),
            "full": dict(Counter(str(row(case)["gold"]) for case in full)),
            "full_by_source": dict(Counter(row(case)["source"] for case in full)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    manifest = build(int(args.seed) if args.seed is not None else SEED)
    out_dir = Path(args.output_dir) if args.output_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    if args.check and path.exists():
        previous = json.loads(path.read_text(encoding="utf-8-sig"))
        if previous != manifest:
            raise SystemExit("real_eval_v1 manifest drift detected")
    else:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "manifest": str(path),
        "b_evolution": manifest["b_stage"]["evolution_set"]["size"],
        "b_validation": manifest["b_stage"]["validation_set"]["size"],
        "full": manifest["full_stage"]["size"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
