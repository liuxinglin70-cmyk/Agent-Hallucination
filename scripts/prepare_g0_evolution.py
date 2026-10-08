"""Freeze a full-693 evolution split and compact G0 readings.

The validation set is chosen by source and gold label across all 693 cases,
without using G0 prediction quality. G0 has readings for 653 of those cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from evolution.splits import load_gold  # noqa: E402

DEFAULT_REPORT = ROOT / "artifacts/full693-20260924T143024Z/measurement/g0/baseline_report.json"
EXPOSURE = ROOT / "datasets/protocols/development_exposure.json"
DATASET = ROOT / "datasets/raw/agenthallu/AgentHallu"
SNAPSHOT = ROOT / "datasets/protocols/g0_baseline_snapshot_20260927.json"
SPLIT = ROOT / "datasets/protocols/evolution_g0_reuse_20260928.json"
SEED = "g0-reuse-20260928-v1"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rank(case: str) -> str:
    return hashlib.sha256(f"{SEED}:{case}".encode("utf-8")).hexdigest()


def _write_frozen(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise ValueError(f"frozen output differs: {path}")
    path.write_text(encoded, encoding="utf-8", newline="\n")


def prepare(report_path: Path, *, validation_size: int = 139) -> tuple[dict, dict]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    exposure = json.loads(EXPOSURE.read_text(encoding="utf-8"))
    corpus = [str(case) for case in exposure["cases"]]
    rows = report["rows"]
    by_case = {str(row["case"]): row for row in rows}
    if len(corpus) != 693 or len(set(corpus)) != 693:
        raise ValueError("expected 693 unique corpus cases")
    if len(rows) != 653 or len(by_case) != 653 or not set(by_case) <= set(corpus):
        raise ValueError("G0 rows must be 653 unique cases inside the corpus")
    if not 0 < validation_size < len(corpus):
        raise ValueError("invalid validation size")
    gold = {case: load_gold(DATASET, case) for case in corpus}
    if any(value is None for value in gold.values()):
        raise ValueError("corpus contains an unknown gold label")
    if any(row["gold"] != gold[str(row["case"])] for row in rows):
        raise ValueError("G0 gold labels disagree with the corpus")

    strata: dict[tuple[str, bool], list[str]] = defaultdict(list)
    for case in corpus:
        strata[(case.split("/", 1)[0], bool(gold[case]))].append(case)
    total = len(corpus)
    quotas = {key: validation_size * len(cases) / total for key, cases in strata.items()}
    allocations = {key: int(quota) for key, quota in quotas.items()}
    remainder = validation_size - sum(allocations.values())
    for key in sorted(strata, key=lambda item: (-(quotas[item] - allocations[item]), item))[:remainder]:
        allocations[key] += 1
    validation = sorted(
        case
        for key, cases in strata.items()
        for case in sorted(cases, key=_rank)[:allocations[key]]
    )
    pool = sorted(set(corpus) - set(validation))
    if len(validation) != validation_size or len(pool) + len(validation) != 693:
        raise AssertionError("split coverage or disjointness failed")
    compact = [{
        key: row.get(key)
        for key in (
            "case", "source", "gold", "prediction", "terminal_prediction",
            "gold_step", "responsible_step", "responsible_step_kind",
            "state", "authority", "elapsed_seconds",
        )
    } for row in sorted(rows, key=lambda row: str(row["case"]))]
    snapshot = {
        "schema_version": "evolution.g0_baseline_snapshot.v1",
        "source": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": _digest(report_path),
        "role": "historical_g0_reference",
        "metrics": {
            "requested": report["coverage"]["requested_cases"],
            "authoritative": report["coverage"]["authoritative"],
            "degraded": report["coverage"]["degraded"],
            "macro_f1": report["metrics"]["macro_f1"],
            "tp": report["metrics"]["tp"],
            "fp": report["metrics"]["fp"],
            "fn": report["metrics"]["fn"],
            "tn": report["metrics"]["tn"],
            "step_localization": report["metrics"]["step_localization"],
        },
        "rows": compact,
    }
    split = {
        "schema_version": "evolution.development_split.v1",
        "purpose": "Full 693-case self-evolution split; validation is disjoint from the current evolution pool.",
        "seed": SEED,
        "corpus_manifest": str(EXPOSURE.relative_to(ROOT)).replace("\\", "/"),
        "corpus_manifest_sha256": _digest(EXPOSURE),
        "g0_snapshot": str(SNAPSHOT.relative_to(ROOT)).replace("\\", "/"),
        "g0_report_sha256": _digest(report_path),
        "validation_selection": "all 693 cases, source x gold stratified; SHA-256 rank; no G0 prediction used",
        "evolution_set": {"size": len(pool), "cases": pool},
        "validation_set": {"size": len(validation), "cases": validation},
        "counts": {
            "corpus": len(corpus),
            "g0_rows": len(rows),
            "g0_authoritative": report["coverage"]["authoritative"],
            "old_non_g0_cases_in_evolution": len(set(pool) - set(by_case)),
            "validation_gold": dict(Counter("positive" if gold[c] else "negative" for c in validation)),
        },
    }
    return snapshot, split


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--validation-size", type=int, default=139)
    args = parser.parse_args()
    snapshot, split = prepare(args.report.resolve(), validation_size=args.validation_size)
    _write_frozen(SNAPSHOT, snapshot)
    _write_frozen(SPLIT, split)
    print(json.dumps(split["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
