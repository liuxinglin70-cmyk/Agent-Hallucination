"""Measure detector verdict stability between two recorded runs.

The detector's per-case verdict carries irreducible provider churn (pure
test-retest at temperature 0 flips 10-30% of verdicts on identical
inputs, and single-run FP/FN sets overlap with Jaccard ~0.4-0.5). Every
downstream comparison — fix A/B, evolution gates, round baselines — is
only meaningful relative to that noise floor, so it must be measured,
not assumed.

Given two run directories over the same (or overlapping) cases this
tool reports:

- verdict agreement and the disagreement case list;
- FP/FN set overlap per run (needs ``eval_gold.json`` in both);
- census candidate-set stability (mean Jaccard, identical-set count);
- flip anatomy: which stage's output differs per disagreement (census
  set / verification verdict / defense verdict).

Usage:
    python tools/run_stability.py runs/<a> runs/<b> [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Mapping


def _load_run(run_dir: Path) -> dict[str, dict[str, Any]]:
    """case_id -> {prediction, gold, census, certificates, decisions}."""
    gold_rows = {
        str(entry["case"]): entry
        for entry in json.loads(
            (run_dir / "eval_gold.json").read_text(encoding="utf-8"),
        )["cases"]
    }
    out: dict[str, dict[str, Any]] = {}
    for result_path in sorted((run_dir / "cases").glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        case_id = str(result.get("case_id") or "")
        if case_id not in gold_rows:
            continue
        proof = result.get("proof_adjudication") or {}
        decisions = {
            str(cid): (
                decision.get("verdict") if isinstance(decision, Mapping)
                else decision
            )
            for cid, decision in (
                proof.get("defense_decisions") or {}
            ).items()
        }
        out[case_id] = {
            "prediction": gold_rows[case_id]["prediction"],
            "gold": bool(gold_rows[case_id]["gold"]),
            "census": {
                str(candidate["candidate_id"])
                for candidate in proof.get("census") or []
            },
            "certificates": {
                str(cert.get("candidate_id")): cert.get("verdict")
                for cert in proof.get("certificates") or []
            },
            "decisions": decisions,
        }
    return out


def _flip_kind(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Which stage's output differs between two runs of one case."""
    census_changed = a["census"] != b["census"]
    shared = a["census"] & b["census"]
    stages: set[str] = set()
    for cid in shared:
        if a["certificates"].get(cid) != b["certificates"].get(cid):
            stages.add("verification")
        elif a["decisions"].get(cid) != b["decisions"].get(cid):
            stages.add("defense")
    parts = sorted(stages) + (["census"] if census_changed else [])
    return "+".join(parts) if parts else "none"


def stability_report(run_a: Path, run_b: Path) -> dict[str, Any]:
    a, b = _load_run(run_a), _load_run(run_b)
    common = sorted(set(a) & set(b))
    if not common:
        raise SystemExit(f"{run_a} and {run_b} share no cases")
    disagreements = [
        case_id for case_id in common
        if a[case_id]["prediction"] != b[case_id]["prediction"]
    ]
    fp_a = {c for c in common if a[c]["prediction"] and not a[c]["gold"]}
    fp_b = {c for c in common if b[c]["prediction"] and not b[c]["gold"]}
    fn_a = {c for c in common if not a[c]["prediction"] and a[c]["gold"]}
    fn_b = {c for c in common if not b[c]["prediction"] and b[c]["gold"]}
    jaccards = [
        len(a[c]["census"] & b[c]["census"])
        / len(a[c]["census"] | b[c]["census"])
        for c in common if a[c]["census"] | b[c]["census"]
    ]
    return {
        "run_a": str(run_a),
        "run_b": str(run_b),
        "cases_compared": len(common),
        "verdict_agreement": 1 - len(disagreements) / len(common),
        "disagreements": [
            {
                "case_id": case_id,
                "run_a": a[case_id]["prediction"],
                "run_b": b[case_id]["prediction"],
                "flip_kind": _flip_kind(a[case_id], b[case_id]),
            }
            for case_id in disagreements
        ],
        "fp_overlap": {
            "run_a": len(fp_a), "run_b": len(fp_b),
            "shared": len(fp_a & fp_b),
            "jaccard": (
                len(fp_a & fp_b) / len(fp_a | fp_b) if fp_a | fp_b else 1.0
            ),
        },
        "fn_overlap": {
            "run_a": len(fn_a), "run_b": len(fn_b),
            "shared": len(fn_a & fn_b),
            "jaccard": (
                len(fn_a & fn_b) / len(fn_a | fn_b) if fn_a | fn_b else 1.0
            ),
        },
        "census_set_stability": {
            "mean_jaccard": statistics.mean(jaccards) if jaccards else 1.0,
            "identical_sets": sum(1 for j in jaccards if j == 1.0),
            "cases_with_candidates": len(jaccards),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path)
    parser.add_argument("run_b", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = stability_report(args.run_a, args.run_b)
    print(f"cases compared      : {report['cases_compared']}")
    print(f"verdict agreement   : {report['verdict_agreement']:.1%}")
    print(
        f"FP overlap          : {report['fp_overlap']['shared']}/"
        f"{report['fp_overlap']['run_a']} vs "
        f"{report['fp_overlap']['run_b']} "
        f"(Jaccard {report['fp_overlap']['jaccard']:.2f})",
    )
    print(
        f"FN overlap          : {report['fn_overlap']['shared']}/"
        f"{report['fn_overlap']['run_a']} vs "
        f"{report['fn_overlap']['run_b']} "
        f"(Jaccard {report['fn_overlap']['jaccard']:.2f})",
    )
    print(
        f"census set stability: mean Jaccard "
        f"{report['census_set_stability']['mean_jaccard']:.2f}, identical "
        f"in {report['census_set_stability']['identical_sets']}/"
        f"{report['census_set_stability']['cases_with_candidates']}",
    )
    kinds: dict[str, int] = {}
    for row in report["disagreements"]:
        kinds[row["flip_kind"]] = kinds.get(row["flip_kind"], 0) + 1
    print(f"flip anatomy        : {dict(sorted(kinds.items()))}")
    if args.out is not None:
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
