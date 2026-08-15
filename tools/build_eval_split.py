"""Freeze the Phase 1-D evaluation split over the AgentHallu dataset.

The split is two-way at the dataset level:

- evolution pool — the 100 cases of the decisive random100 run; every
  Phase 0 / Phase 1 artifact (learning cases, policy candidates, oracle
  bindings, binder calibration) derives from its run tree. Replay
  verification also lives there (offline adjudication replay), so it
  consumes no further data.
- frozen evaluation set — fresh cases no run has ever touched, sampled
  once, committed before any detector run, never edited afterwards.

Everything this builder needs is derived mechanically from existing
assets: the contamination boundary is the union of ``runs/*/run.json``
case lists, the canary set is every case the policy candidates declare
as a risk boundary (tp_must_survive / do_not_apply / fn_risk /
non-separable findings), and gold polarity comes from the raw dataset
files (evaluator side only — the detector input path never sees it).

Sampling design: 100 cases, 50 gold-error + 50 gold-clean; within each
polarity, framework strata receive the polarity's seats proportionally
(largest remainder). Equal directional power matters because the
deployed policy layer acts on the clean side (wrongful convictions)
while the regression gate watches the error side.

Usage:
    python tools/build_eval_split.py            # writes datasets/eval_splits/v1
    python tools/build_eval_split.py --check    # verify the frozen assets
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
POLICY_CANDIDATES = (
    REPO / "datasets/policy_candidates/v1/policy_candidates.json"
)
POLICY_GENOME = REPO / "datasets/policy_candidates/v1/policy_genome.json"
OUT_DIR = REPO / "datasets/eval_splits/v1"
SPLIT_SCHEMA = "sehd2.eval_split.v1"
SEED = 20260816
FROZEN_SIZE = 50  # per gold polarity
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
CASE_REF = re.compile(r"([A-Za-z_]+/\d{3}\.json)")


def coerce_gold(value) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return {"true": True, "false": False}.get(normalized)


def dataset_cases() -> dict[str, bool]:
    """case path -> gold polarity (evaluator-side read of the raw files)."""
    cases: dict[str, bool] = {}
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        rel = path.relative_to(DATASET_ROOT).as_posix()
        raw = json.loads(path.read_text(encoding="utf-8"))
        gold = coerce_gold(raw.get("is_hallucination"))
        if gold is None:
            raise ValueError(f"{rel}: missing is_hallucination")
        cases[rel] = gold
    return cases


def contamination_union() -> tuple[dict[str, list[str]], set[str]]:
    """Every case any recorded run touched, grouped by run id."""
    used: dict[str, list[str]] = {}
    for meta_path in sorted((REPO / "runs").glob("*/run.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        run_id = str(meta.get("run_id") or meta_path.parent.name)
        used[run_id] = sorted(str(case) for case in meta.get("case_ids") or [])
    return used, set().union(*used.values()) if used else set()


def largest_remainder(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Proportional integer allocation of ``total`` over ``weights``."""
    scale = total / sum(weights.values())
    exact = {key: value * scale for key, value in weights.items()}
    alloc = {key: int(value) for key, value in exact.items()}
    remainder = total - sum(alloc.values())
    by_fraction = sorted(
        exact, key=lambda key: (-(exact[key] - alloc[key]), key),
    )
    for key in by_fraction[:remainder]:
        alloc[key] += 1
    return alloc


def sample_frozen(fresh: dict[str, bool]) -> list[dict[str, str | bool]]:
    """50 error + 50 clean, framework-proportional inside each polarity."""
    cell: dict[tuple[bool, str], list[str]] = {}
    for case, gold in fresh.items():
        cell.setdefault((gold, case.split("/")[0]), []).append(case)
    rng = random.Random(SEED)
    sampled: list[dict[str, str | bool]] = []
    for gold in (True, False):
        strata = {
            framework: len(cases)
            for (polarity, framework), cases in cell.items()
            if polarity is gold
        }
        if not strata:
            raise ValueError(f"polarity {gold} has no fresh cases")
        seats = largest_remainder(FROZEN_SIZE, strata)
        for framework, count in sorted(seats.items()):
            pool = sorted(cell[(gold, framework)])
            for case in sorted(rng.sample(pool, count)):
                sampled.append({
                    "case_id": case,
                    "framework": framework,
                    "gold": gold,
                })
    return sorted(sampled, key=lambda row: str(row["case_id"]))


def canary_cases() -> list[dict[str, object]]:
    """Every case the policy candidates declare as a risk boundary."""
    payload = json.loads(POLICY_CANDIDATES.read_text(encoding="utf-8"))
    mentions: dict[str, set[str]] = {}

    def note(case: str, source: str) -> None:
        mentions.setdefault(case, set()).add(source)

    for policy in payload.get("policies") or []:
        policy_id = str(policy.get("policy_id"))
        effect = policy.get("expected_effect") or {}
        for entry in effect.get("tp_must_survive") or []:
            note(str(entry.get("case_id")), f"{policy_id}:tp_must_survive")
        for case in CASE_REF.findall(str(effect.get("fn_risk") or "")):
            note(case, f"{policy_id}:fn_risk")
        boundary = policy.get("risk_boundary") or {}
        for item in [
            *(boundary.get("do_not_apply") or []),
            str(boundary.get("statement") or ""),
        ]:
            for case in CASE_REF.findall(str(item)):
                note(case, f"{policy_id}:do_not_apply")
    for finding in payload.get("non_separable_findings") or []:
        for case in CASE_REF.findall(json.dumps(finding)):
            note(case, "NS-1:non_separable")
    rows: list[dict[str, object]] = []
    for case in sorted(mentions):
        rows.append({
            "case_id": case,
            "policy_references": sorted(mentions[case]),
            "gate": "must_stay_convicted",
        })
    return rows


def build() -> dict[str, object]:
    cases = dataset_cases()
    used_by_run, contaminated = contamination_union()
    evolution_pool = used_by_run[EVOLUTION_RUN]
    fresh = {
        case: gold for case, gold in cases.items()
        if case not in contaminated
    }
    frozen = sample_frozen(fresh)
    canaries = canary_cases()
    for row in canaries:
        case = str(row["case_id"])
        if case not in cases:
            raise ValueError(f"canary {case} not in dataset")
        if not cases[case]:
            row["gate"] = "monitor_only"
    genome = json.loads(POLICY_GENOME.read_text(encoding="utf-8"))
    polarity_check = Counter(row["gold"] for row in frozen)
    if polarity_check != {True: FROZEN_SIZE, False: FROZEN_SIZE}:
        raise ValueError(f"frozen polarity imbalance: {polarity_check}")
    overlap = {
        str(row["case_id"]) for row in frozen
    } & contaminated
    if overlap:
        raise ValueError(f"frozen set touches used cases: {sorted(overlap)}")
    return {
        "schema_version": SPLIT_SCHEMA,
        "created_at": _now(),
        "seed": SEED,
        "dataset": "AgentHallu (693 cases)",
        "sampling": (
            f"{FROZEN_SIZE} gold-error + {FROZEN_SIZE} gold-clean; "
            "framework strata receive each polarity's seats proportionally "
            "to the fresh universe (largest remainder); the clean side gets "
            "equal power because the deployed policy layer acts on "
            "wrongful convictions while the regression gate watches the "
            "error side"
        ),
        "freeze_rule": (
            "committed before any detector run; v1 is never edited — a "
            "changed split requires a new version directory"
        ),
        "evolution_pool": {
            "run": EVOLUTION_RUN,
            "cases": evolution_pool,
        },
        "contamination": {
            "rule": "union of runs/*/run.json case lists",
            "by_run": used_by_run,
            "total": len(contaminated),
        },
        "fresh_universe_size": len(fresh),
        "frozen_eval": frozen,
        "frozen_eval_size": len(frozen),
        "canary": {
            "rule": (
                "risk cases declared by the policy candidates (all from "
                "the evolution pool); gold-error canaries must stay "
                "convicted under the deployed chain, gold-clean canaries "
                "are monitor-only"
            ),
            "cases": canaries,
        },
        "deployment": {
            "detector": FROZEN_DETECTOR,
            "policy_genome": {
                "content_hash": genome.get("content_hash"),
                "policy_ids": [
                    item.get("policy_id") for item in genome.get("items") or []
                ],
            },
        },
    }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def write_report(manifest: dict[str, object], out_dir: Path) -> None:
    frozen = manifest["frozen_eval"]
    by_framework: Counter[str] = Counter(
        str(row["framework"]) for row in frozen  # type: ignore[arg-type]
    )
    canaries = manifest["canary"]["cases"]  # type: ignore[index]
    lines = [
        "# Phase 1-D Frozen Evaluation Split (v1)", "",
        f"- created: {manifest['created_at']}",  # type: ignore[arg-type]
        f"- seed: {manifest['seed']}",  # type: ignore[arg-type]
        f"- evolution pool: {len(manifest['evolution_pool']['cases'])} cases"  # type: ignore[index]
        f" (run {manifest['evolution_pool']['run']})",  # type: ignore[index]
        f"- contamination boundary: {manifest['contamination']['total']}"  # type: ignore[index]
        " cases across all recorded runs",
        f"- fresh universe: {manifest['fresh_universe_size']} cases",
        f"- frozen evaluation set: {manifest['frozen_eval_size']}"  # type: ignore[arg-type]
        " cases (50 gold-error / 50 gold-clean)", "",
        "## Frozen set by framework", "",
        "| framework | seats |", "|---|---|",
    ]
    lines += [
        f"| {framework} | {count} |"
        for framework, count in sorted(by_framework.items())
    ]
    lines += [
        "", "## Canary set (deployment-form regression gate)", "",
        "| case | gate | declared by |", "|---|---|---|",
    ]
    lines += [
        f"| {row['case_id']} | {row['gate']} | "  # type: ignore[index]
        + "; ".join(row["policy_references"])  # type: ignore[index]
        for row in canaries  # type: ignore[union-attr]
    ]
    lines += [
        "", "## Deployment form", "",
        "- detector routes: census=deepseek-v4-flash,"
        " verification/defense=deepseek-v4-pro"
        " (config fingerprint 76d6fd60b069420c),",
        "- binder: deepseek-v4-flash with the calibrated discriminative"
        " class definitions and structured abstention,",
        f"- policy genome: {', '.join(
            str(pid) for pid in manifest['deployment']['policy_genome']['policy_ids']  # type: ignore[index,union-attr]
        )}.", "",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def check(out_dir: Path) -> int:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    frozen_ids = [str(row["case_id"]) for row in manifest["frozen_eval"]]
    problems: list[str] = []
    if len(frozen_ids) != len(set(frozen_ids)):
        problems.append("frozen set contains duplicates")
    cases = dataset_cases()
    used_by_run, contaminated = contamination_union()
    overlap = set(frozen_ids) & contaminated
    if overlap:
        problems.append(f"frozen set touches used cases: {sorted(overlap)}")
    polarity = Counter(cases[case] for case in frozen_ids)
    if polarity != {True: FROZEN_SIZE, False: FROZEN_SIZE}:
        problems.append(f"polarity imbalance: {polarity}")
    for row in manifest["canary"]["cases"]:
        if str(row["case_id"]) not in cases:
            problems.append(f"unknown canary {row['case_id']}")
    rebuilt = canary_cases()
    if [r["case_id"] for r in rebuilt] != [
        r["case_id"] for r in manifest["canary"]["cases"]
    ]:
        problems.append("canary set no longer matches the policy candidates")
    for problem in problems:
        print(f"FAIL {problem}")
    print("frozen split check:", "ok" if not problems else "FAILED")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="verify the frozen assets against current inputs",
    )
    args = parser.parse_args()
    if args.check:
        return check(OUT_DIR)
    manifest = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )
    write_report(manifest, OUT_DIR)
    print(
        f"frozen split -> {OUT_DIR / 'manifest.json'} "
        f"({manifest['frozen_eval_size']} eval cases, "
        f"{len(manifest['canary']['cases'])} canaries)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
