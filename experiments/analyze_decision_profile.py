#!/usr/bin/env python3
"""Multi-threshold decision profile analysis (offline, zero API calls).

Reads cached attribution blocks from evolution artifacts and recomputes the
three-state verdict at several CRS operating points. This exposes how much of
the current score is an artifact of the frozen single-threshold policy and
identifies the "flip band": cases whose verdict changes as the operating point
moves. The flip-band case list feeds the test-time-scaling replica experiment
(``run_decision_replicas.py``).

CRS is a diagnostic projection, not the proof-court verdict engine, so every
recomputed verdict is explicitly marked as an operating-point simulation.
Cases whose CRS projection is incomplete (``crs_complete=False`` or missing
aggregate) are reported separately and never counted as decisive.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_TAUS: tuple[float, ...] = (0.20, 0.28, 0.35, 0.45, 0.55)

# Default source layout: every protocol output directory that carries
# evolution steps with attribution blocks.
DEFAULT_SOURCE_GLOBS: tuple[str, ...] = (
    "experiments/protocols/*/evolution_result.json",
    "experiments/protocols/*/evolution_checkpoint.json",
)

VERDICT_ERROR = "verified_error"
VERDICT_CLEAN = "verified_clean"
VERDICT_UNRESOLVED = "unresolved"


def _coverage_established(value: Any, state: Any, tau: float) -> bool:
    """Whether one side's evidence is established at operating point tau.

    Categorical v3.5 readings expose ``complete``; quantitative readings are
    compared against the same operating point as the aggregate.
    """
    if str(state or "").strip().lower() == "complete":
        return True
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return numeric >= tau


def verdict_at_operating_point(
    crs_aggregate: Any,
    *,
    prosecution_coverage: Any = None,
    prosecution_coverage_state: Any = None,
    defense_coverage: Any = None,
    defense_coverage_state: Any = None,
    tau: float,
) -> str:
    """Recompute the three-state verdict at CRS operating point ``tau``.

    Rules (operating-point simulation only, never a real verdict):
    - ``aggregate >= tau`` AND prosecution established -> verified_error;
    - defense established AND ``aggregate < tau`` -> verified_clean;
    - otherwise unresolved (fail-closed, including missing aggregate).
    """
    try:
        aggregate = float(crs_aggregate)
    except (TypeError, ValueError):
        return VERDICT_UNRESOLVED
    prosecution_established = _coverage_established(
        prosecution_coverage, prosecution_coverage_state, tau
    )
    defense_established = _coverage_established(
        defense_coverage, defense_coverage_state, tau
    )
    if aggregate >= tau and prosecution_established:
        return VERDICT_ERROR
    if defense_established and aggregate < tau:
        return VERDICT_CLEAN
    return VERDICT_UNRESOLVED


def flip_band_case_ids(profiles: Sequence[Mapping[str, Any]]) -> list[str]:
    """Cases whose recomputed verdict is not stable across operating points."""
    flips: list[str] = []
    for profile in profiles:
        verdicts = set(profile.get("verdicts_by_tau", {}).values())
        if len(verdicts) > 1:
            flips.append(str(profile["case_id"]))
    return flips


def _coerce_gold(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _extract_profiles(sources: Sequence[Path]) -> list[dict[str, Any]]:
    """Collect one attribution profile per unique case_id.

    Labeled profiles beat unlabeled ones: checkpoints are incremental
    snapshots that often omit gold, while evolution_result.json keeps the
    final labeled attribution.
    """
    best: dict[str, dict[str, Any]] = {}
    for source in sources:
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        steps = payload.get("steps") if isinstance(payload, Mapping) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            attribution = step.get("attribution")
            if not isinstance(attribution, Mapping):
                continue
            case_id = str(step.get("case_id") or attribution.get("case_id") or "")
            if not case_id:
                continue
            gold = _coerce_gold(attribution.get("gold_label"))
            existing = best.get(case_id)
            if existing is not None and existing["gold_label"] is not None:
                continue  # a labeled profile already wins
            best[case_id] = {
                "case_id": case_id,
                "source": str(source),
                "state": str(attribution.get("state") or VERDICT_UNRESOLVED),
                "crs_aggregate": attribution.get("crs_aggregate"),
                "crs_scores": attribution.get("crs_scores") or {},
                "crs_complete": bool(attribution.get("crs_complete")),
                "crs_source": str(attribution.get("crs_source") or ""),
                "prosecution_coverage": attribution.get("prosecution_coverage"),
                "prosecution_coverage_state": attribution.get(
                    "prosecution_coverage_state"
                ),
                "defense_coverage": attribution.get("defense_coverage"),
                "defense_coverage_state": attribution.get("defense_coverage_state"),
                "candidate_count": attribution.get("candidate_count"),
                "gold_label": gold,
                "diagnostic_only": bool(attribution.get("diagnostic_only")),
            }
    return list(best.values())


def _outcome(verdict: str, gold: bool | None) -> str:
    if verdict == VERDICT_UNRESOLVED:
        if gold is None:
            return "unresolved_unlabeled"
        return "unresolved_should_error" if gold else "unresolved_should_clean"
    if gold is None:
        return "decisive_unlabeled"
    predicted_error = verdict == VERDICT_ERROR
    if predicted_error and gold:
        return "tp"
    if predicted_error:
        return "fp"
    if gold:
        return "fn"
    return "tn"


def _metrics_for_tau(
    profiles: Sequence[Mapping[str, Any]], tau: float
) -> dict[str, Any]:
    confusion: Counter[str] = Counter()
    for profile in profiles:
        verdict = verdict_at_operating_point(
            profile.get("crs_aggregate"),
            prosecution_coverage=profile.get("prosecution_coverage"),
            prosecution_coverage_state=profile.get("prosecution_coverage_state"),
            defense_coverage=profile.get("defense_coverage"),
            defense_coverage_state=profile.get("defense_coverage_state"),
            tau=tau,
        )
        confusion[_outcome(verdict, profile.get("gold_label"))] += 1
    labeled_decisive = (
        confusion["tp"] + confusion["tn"] + confusion["fp"] + confusion["fn"]
    )
    labeled_total = labeled_decisive + (
        confusion["unresolved_should_error"] + confusion["unresolved_should_clean"]
    )
    return {
        "tau": tau,
        "confusion": dict(confusion),
        "decision_coverage": (
            labeled_decisive / labeled_total if labeled_total else None
        ),
        "selective_accuracy": (
            (confusion["tp"] + confusion["tn"]) / labeled_decisive
            if labeled_decisive
            else None
        ),
        "unresolved_should_clean_rate": (
            confusion["unresolved_should_clean"] / labeled_total
            if labeled_total
            else None
        ),
        "unresolved_should_error_rate": (
            confusion["unresolved_should_error"] / labeled_total
            if labeled_total
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Explicit source JSON paths (repeatable). Overrides --root globs.",
    )
    parser.add_argument("--root", type=Path, default=None,
                        help="Root for default source globs (default: repo root).")
    parser.add_argument(
        "--gold-reference",
        type=Path,
        default=None,
        help=(
            "Optional case_id->{gold_label} JSON used to backfill labels "
            "(e.g. experiments/results/baseline_predictions_by_id.json)."
        ),
    )
    parser.add_argument(
        "--taus",
        type=float,
        nargs="+",
        default=list(DEFAULT_TAUS),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/decision_profile_report.json"),
    )
    args = parser.parse_args()

    if args.source:
        sources = [Path(path) for path in args.source]
    else:
        root = args.root or Path(__file__).resolve().parents[1]
        sources = []
        for pattern in DEFAULT_SOURCE_GLOBS:
            sources.extend(sorted(root.glob(pattern)))
    sources = [path for path in sources if path.exists()]
    if not sources:
        print("no evolution artifacts found", file=sys.stderr)
        return 1

    for tau in args.taus:
        if not 0.0 <= tau <= 1.0:
            print(f"tau out of range: {tau}", file=sys.stderr)
            return 1

    profiles = _extract_profiles(sources)
    if args.gold_reference and args.gold_reference.exists():
        reference = json.loads(args.gold_reference.read_text(encoding="utf-8"))
        for profile in profiles:
            if profile["gold_label"] is None:
                entry = reference.get(profile["case_id"])
                if isinstance(entry, Mapping):
                    profile["gold_label"] = _coerce_gold(entry.get("gold_label"))
    reliable = [p for p in profiles if p["crs_complete"] and not p["diagnostic_only"]]
    excluded = [p for p in profiles if p not in reliable]

    for profile in reliable:
        profile["verdicts_by_tau"] = {
            str(tau): verdict_at_operating_point(
                profile.get("crs_aggregate"),
                prosecution_coverage=profile.get("prosecution_coverage"),
                prosecution_coverage_state=profile.get("prosecution_coverage_state"),
                defense_coverage=profile.get("defense_coverage"),
                defense_coverage_state=profile.get("defense_coverage_state"),
                tau=tau,
            )
            for tau in args.taus
        }
    flip_ids = flip_band_case_ids(reliable)

    report = {
        "generated_by": "analyze_decision_profile.py",
        "note": (
            "Operating-point simulation over the frozen CRS diagnostic "
            "projection; not a re-run of the v3.5 proof court."
        ),
        "taus": list(args.taus),
        "source_files": [str(path) for path in sources],
        "case_count": len(reliable),
        "excluded_case_count": len(excluded),
        "excluded_case_ids": [p["case_id"] for p in excluded],
        "metrics_by_tau": [
            _metrics_for_tau(reliable, tau) for tau in args.taus
        ],
        "flip_band_case_ids": flip_ids,
        "flip_band_size": len(flip_ids),
        "profiles": [
            {
                "case_id": p["case_id"],
                "state": p["state"],
                "crs_aggregate": p["crs_aggregate"],
                "crs_source": p["crs_source"],
                "gold_label": p["gold_label"],
                "verdicts_by_tau": p["verdicts_by_tau"],
            }
            for p in reliable
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"cases={len(reliable)} excluded={len(excluded)} "
        f"flip_band={len(flip_ids)} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
