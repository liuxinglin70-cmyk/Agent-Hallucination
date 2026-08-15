"""Freeze the Phase 2 self-evolution split over the full AgentHallu set.

Phase 2 consumes the complete 693-case dataset with no exclusions —
every case participates, including everything earlier phases ran. The
split is two-way:

- evolution pool — drives the self-evolution cycles (failure collection,
  hypothesis extraction, replay validation). The 100 cases of the
  decisive random100 run are forced into this pool: the deployed
  P2-P5 policies were trained on their failures, so keeping them out of
  verification is what makes the verification claim honest.
- final verification set — 300 fresh-stratified cases (150 gold-error +
  150 gold-clean, framework-proportional within polarity) that no
  evolution decision may touch; only the final report reads them.

Committed before any Phase 2 detector run; v1 is never edited.

Usage:
    python tools/build_phase2_split.py            # write + freeze
    python tools/build_phase2_split.py --check    # verify frozen assets
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO / "datasets/raw/agenthallu/AgentHallu"
P1D_MANIFEST = REPO / "datasets/eval_splits/v1/manifest.json"
OUT_DIR = REPO / "datasets/eval_splits/phase2_v1"
SPLIT_SCHEMA = "sehd2.eval_split.phase2.v1"
SEED = 20260817
VERIFY_SIZE = 150  # per gold polarity
EVOLUTION_RUN = "random100_20260815_142446"
FROZEN_DETECTOR = {
    "config_fingerprint": "76d6fd60b069420c",
    "env": {
        "SEHD_ROUTE_CENSUS_MODEL": "deepseek-v4-flash",
        "SEHD_ROUTE_VERIFICATION_MODEL": "deepseek-v4-pro",
        "SEHD_ROUTE_DEFENSE_MODEL": "deepseek-v4-pro",
        "SEHD_ROUTE_VERIFICATION_TIMEOUT_SECONDS": "240",
        "SEHD_ROUTE_DEFENSE_TIMEOUT_SECONDS": "240",
    },
    "binder_route_model": "deepseek-v4-flash",
}


def coerce_gold(value) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"unusable is_hallucination value: {value!r}")
    return normalized == "true"


def dataset_cases() -> dict[str, bool]:
    cases: dict[str, bool] = {}
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        rel = path.relative_to(DATASET_ROOT).as_posix()
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases[rel] = coerce_gold(raw.get("is_hallucination"))
    return cases


def largest_remainder(total: int, weights: dict[str, int]) -> dict[str, int]:
    scale = total / sum(weights.values())
    exact = {key: value * scale for key, value in weights.items()}
    alloc = {key: int(value) for key, value in exact.items()}
    remainder = total - sum(alloc.values())
    ordered = sorted(exact, key=lambda key: (-(exact[key] - alloc[key]), key))
    for key in ordered[:remainder]:
        alloc[key] += 1
    return alloc


def build() -> dict[str, object]:
    cases = dataset_cases()
    if len(cases) != 693:
        raise ValueError(f"expected 693 dataset cases, found {len(cases)}")
    p1d = json.loads(P1D_MANIFEST.read_text(encoding="utf-8"))
    trained = sorted(
        set(str(case) for case in p1d["evolution_pool"]["cases"]),
    )
    if len(trained) != 100:
        raise ValueError("P1-D evolution pool is not the 100-case run")

    eligible = {
        case: gold for case, gold in cases.items() if case not in trained
    }
    rng = random.Random(SEED)
    verification: list[dict[str, object]] = []
    for gold in (True, False):
        strata: dict[str, list[str]] = {}
        for case, polarity in eligible.items():
            if polarity is gold:
                strata.setdefault(case.split("/")[0], []).append(case)
        seats = largest_remainder(
            VERIFY_SIZE,
            {fw: len(pool) for fw, pool in strata.items()},
        )
        for framework, count in sorted(seats.items()):
            for case in sorted(rng.sample(sorted(strata[framework]), count)):
                verification.append({
                    "case_id": case,
                    "framework": framework,
                    "gold": gold,
                })
    verification.sort(key=lambda row: str(row["case_id"]))
    verification_ids = {str(row["case_id"]) for row in verification}
    evolution = sorted(set(cases) - verification_ids)

    polarity = Counter(row["gold"] for row in verification)
    if polarity != {True: VERIFY_SIZE, False: VERIFY_SIZE}:
        raise ValueError(f"verification polarity imbalance: {polarity}")
    if set(trained) & verification_ids:
        raise ValueError("verification set overlaps policy-training cases")
    if len(evolution) + len(verification) != 693:
        raise ValueError("split does not partition the dataset")

    genome = json.loads(
        (REPO / "datasets/policy_candidates/v1/policy_genome.json").read_text(
            encoding="utf-8",
        )
    )
    return {
        "schema_version": SPLIT_SCHEMA,
        "created_at": _now(),
        "seed": SEED,
        "dataset": "AgentHallu (all 693 cases, no exclusions)",
        "evolution_pool": {
            "cases": evolution,
            "forced_in": {
                "cases": trained,
                "why": (
                    "the P2-P5 policies were trained on these random100 "
                    "failures; they must stay on the evolution side for "
                    "the verification claim to hold"
                ),
            },
        },
        "final_verification": {
            "cases": verification,
            "rule": (
                "no evolution decision may read these cases; only the "
                "final evaluation joins their gold"
            ),
        },
        "deployment": {
            "detector": FROZEN_DETECTOR,
            "initial_genome": {
                "content_hash": genome.get("content_hash"),
                "policy_ids": [
                    item.get("policy_id")
                    for item in genome.get("items") or []
                ],
            },
        },
        "freeze_rule": (
            "committed before any Phase 2 detector run; v1 is never edited"
        ),
    }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def write_report(manifest: dict[str, object]) -> None:
    verification = manifest["final_verification"]["cases"]
    by_framework = Counter(
        str(row["framework"]) for row in verification
    )
    lines = [
        "# Phase 2 Self-Evolution Split (v1)", "",
        f"- created: {manifest['created_at']}",
        f"- seed: {manifest['seed']}",
        f"- evolution pool: "
        f"{len(manifest['evolution_pool']['cases'])} cases "
        "(includes the 100 policy-training cases)",
        f"- final verification: {len(verification)} cases "
        "(150 gold-error / 150 gold-clean)", "",
        "## Verification set by framework", "",
        "| framework | seats |", "|---|---|",
    ]
    lines += [
        f"| {framework} | {count} |"
        for framework, count in sorted(by_framework.items())
    ]
    lines.append("")
    (OUT_DIR / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def check() -> int:
    manifest = json.loads(
        (OUT_DIR / "manifest.json").read_text(encoding="utf-8"),
    )
    cases = dataset_cases()
    problems: list[str] = []
    evolution = set(
        str(case) for case in manifest["evolution_pool"]["cases"]
    )
    verification = {
        str(row["case_id"])
        for row in manifest["final_verification"]["cases"]
    }
    if evolution & verification:
        problems.append("pool overlap")
    if evolution | verification != set(cases):
        problems.append("split does not cover the dataset")
    trained = set(
        str(case) for case in
        manifest["evolution_pool"]["forced_in"]["cases"]
    )
    if trained & verification:
        problems.append("verification touches policy-training cases")
    polarity = Counter(cases[case] for case in verification)
    if polarity != {True: VERIFY_SIZE, False: VERIFY_SIZE}:
        problems.append(f"polarity imbalance: {polarity}")
    for problem in problems:
        print(f"FAIL {problem}")
    print("phase2 split check:", "ok" if not problems else "FAILED")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check()
    manifest = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )
    write_report(manifest)
    print(
        f"phase2 split -> {OUT_DIR / 'manifest.json'} "
        f"({len(manifest['evolution_pool']['cases'])} evolution / "
        f"{len(manifest['final_verification']['cases'])} verification)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
