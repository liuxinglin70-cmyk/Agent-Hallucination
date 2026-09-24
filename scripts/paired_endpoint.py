#!/usr/bin/env python
"""The primary endpoint of the large-scale experiment, computed read-only.

Answers one question with two run trees and no new metric code: on the
pre-registered measurement prefix, does the deployed registry produce more
*decisive* corrections than it breaks, relative to the empty registry?

Why ``decisive_fixed``/``decisive_broken`` and not an accuracy difference.
§32.5 found the terminal verdict reading ``correctness_improved``, which counts
``unresolved -> correct`` as an improvement -- so a registry that only stopped
abstaining on five cases scored ``overall = pass`` (p=0.03125), while a
regression where four true positives all became unresolved left the test
insensitive (mcnemar_p = 1.0). The fix restricts the terminal verdict to
decisive flips and requires at least one. This script reads that same pair, so
the experiment's headline number is the one the framework's own fix defines.

Three inputs, because a single baseline read cannot separate a real effect from
jitter:

* ``--baseline`` and ``--baseline-roll`` are two independent rolls of the *same*
  empty-registry configuration. Cases those two disagree on are the
  instrument's own noise, and they are excluded from the endpoint's numerator
  and denominator while still being reported.
* ``--deployed`` is the same prefix under the active registry.

Both arms must cover the same case set. A deployed arm covering fewer cases
would silently shrink its own denominator, so a mismatch is an error, not a
warning. The gold is loaded from the corpus, so no run tree can assert its own
labels.

    python scripts/paired_endpoint.py \
        --baseline artifacts/baseline/runs/g0 \
        --baseline-roll artifacts/baseline/runs/g0_roll2 \
        --deployed artifacts/baseline/runs/g1 \
        --dataset-root datasets/raw/agenthallu/AgentHallu \
        --json artifacts/<date>_real_experiment/endpoint.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.evaluation import evaluate_run  # noqa: E402
from evolution.metrics import paired_delta  # noqa: E402


def _run_id(root: Path) -> str:
    return root.name


def _runs_root(root: Path) -> Path:
    return root.parent


def score(root: Path, dataset_root: Path, cases: Sequence[str]) -> dict[str, Any]:
    return evaluate_run(
        runs_root=_runs_root(root),
        run_id=_run_id(root),
        dataset_root=dataset_root,
        cases=list(cases),
    )


def _verdict(prediction: object) -> str:
    if prediction is True:
        return "T"
    if prediction is False:
        return "F"
    return "U"


def stability(rows_a: Sequence[dict], rows_b: Sequence[dict]) -> dict[str, Any]:
    """Which cases the *baseline* configuration itself reads two ways.

    Read by both rolls and read identically -> stable. Absent from one roll is a
    coverage difference, not jitter, and is named separately rather than pooled
    into the flip count.
    """
    by_a = {str(row["case"]): row for row in rows_a}
    by_b = {str(row["case"]): row for row in rows_b}
    shared = sorted(set(by_a) & set(by_b))
    flips: list[dict[str, Any]] = []
    for case in shared:
        first = _verdict(by_a[case].get("prediction"))
        second = _verdict(by_b[case].get("prediction"))
        if first != second:
            flips.append({"case": case, "roll1": first, "roll2": second})
    one_sided = sorted((set(by_a) | set(by_b)) - set(shared))
    return {
        "read_by_both": len(shared),
        "flipped_cases": [item["case"] for item in flips],
        "flips": flips,
        "flip_rate": (
            round(len(flips) / len(shared), 4) if shared else None
        ),
        "one_sided_cases": one_sided,
        "case_set_mismatch": bool(one_sided),
    }


def endpoint(
    *,
    baseline: Path,
    baseline_roll: Path | None,
    deployed: Path,
    dataset_root: Path,
    json_out: Path | None,
) -> dict[str, Any]:
    base_a = score(baseline, dataset_root, _cases_of(baseline))
    deployed_scored = score(deployed, dataset_root, _cases_of(deployed))
    cases = [str(row["case"]) for row in deployed_scored["rows"]]
    missing = deployed_scored["missing_cases"]
    if missing:
        raise SystemExit(
            f"the deployed arm is missing {len(missing)} case(s): {missing[:5]}"
        )

    noise: dict[str, Any] | None = None
    unstable: list[str] = []
    if baseline_roll is not None:
        base_b = score(baseline_roll, dataset_root, _cases_of(baseline_roll))
        noise = stability(base_a["rows"], base_b["rows"])
        if noise["case_set_mismatch"]:
            raise SystemExit(
                "the two baseline rolls do not cover the same cases; a noise "
                f"floor cannot be pooled: {noise['one_sided_cases'][:5]}"
            )
        unstable = list(noise["flipped_cases"])

    # The endpoint pair is baseline roll 1 against the deployed arm, both
    # scored on the deployed arm's case list so the pairing is exact. The two
    # arms must cover the *same* cases: a deployed arm covering fewer would
    # silently shrink its own denominator, and a baseline missing cases the
    # deployed arm has cannot be paired at all. Both directions are refused --
    # checking only one leaves the other as a quiet way to move the endpoint.
    base_rows = {str(row["case"]): row for row in base_a["rows"]}
    base_cases = set(base_rows)
    deployed_cases = set(cases)
    if deployed_cases != base_cases:
        only_deployed = sorted(deployed_cases - base_cases)
        only_baseline = sorted(base_cases - deployed_cases)
        raise SystemExit(
            "the arms do not cover the same cases, so no paired endpoint "
            f"exists: deployed-only={only_deployed[:5]} "
            f"baseline-only={only_baseline[:5]}"
        )
    baseline_rows = [base_rows[case] for case in cases]
    delta = paired_delta(
        baseline_rows, deployed_scored["rows"], unstable_cases=unstable,
    )

    stable_cases = [case for case in cases if case not in set(unstable)]
    stable_set = set(stable_cases)
    stable_delta = paired_delta(
        [base_rows[case] for case in stable_cases],
        [
            row for row in deployed_scored["rows"]
            if str(row["case"]) in stable_set
        ],
    ) if stable_cases else None

    def _read(block: dict[str, Any] | None) -> dict[str, Any] | None:
        if block is None:
            return None
        return {
            key: block.get(key)
            for key in (
                "paired_n", "decisive_fixed", "decisive_broken",
                "correctness_improved", "correctness_worsened",
                "marginal_cases", "new_errors",
                "tp_lost", "fp_added", "new_fn", "new_fp",
                "unresolved_added", "unresolved_removed",
                "health_regressed", "health_improved",
                "base_all_case_correct", "active_all_case_correct",
                "marginal_cases_on_stable", "marginal_cases_on_unstable",
                "new_errors_on_unstable", "flips", "transitions_by_gold",
            )
        }

    fixed = int(delta.get("decisive_fixed") or 0)
    broken = int(delta.get("decisive_broken") or 0)
    payload: dict[str, Any] = {
        "schema_version": "detector.paired_endpoint.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "arms": {
            "baseline": str(baseline),
            "baseline_roll": str(baseline_roll) if baseline_roll else None,
            "deployed": str(deployed),
            "cases": len(cases),
        },
        "noise_floor": noise,
        "unstable_cases": unstable,
        "all_cases": _read(delta),
        "stable_cases_only": _read(stable_delta),
        "verdict": {
            "decisive_fixed": fixed,
            "decisive_broken": broken,
            "net_decisive": fixed - broken,
            "effect_established": fixed > broken,
            "rule": (
                "decisive_fixed > decisive_broken on the stable cases; "
                "an equal count is not an improvement"
            ),
        },
        "deployed_coverage": {
            "decisive": deployed_scored["metrics"].get("decisive"),
            "unresolved": deployed_scored["metrics"].get("unresolved"),
            "degraded": deployed_scored["metrics"].get("degraded"),
            "no_terminal_decision": deployed_scored["metrics"].get(
                "no_terminal_decision"
            ),
        },
        "localization": deployed_scored["metrics"].get("step_localization"),
        "baseline_localization": base_a["metrics"].get("step_localization"),
    }
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return payload


def _cases_of(root: Path) -> list[str]:
    manifest = root / "run.json"
    if not manifest.is_file():
        raise SystemExit(f"{manifest} not found; not a run tree: {root}")
    return [str(case) for case in json.loads(
        manifest.read_text(encoding="utf-8-sig")
    )["cases"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument(
        "--baseline-roll",
        default=None,
        help="second independent roll of the empty registry, for the noise floor",
    )
    parser.add_argument("--deployed", required=True)
    parser.add_argument(
        "--dataset-root",
        default=str(ROOT / "datasets/raw/agenthallu/AgentHallu"),
    )
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)
    payload = endpoint(
        baseline=Path(args.baseline),
        baseline_roll=Path(args.baseline_roll) if args.baseline_roll else None,
        deployed=Path(args.deployed),
        dataset_root=Path(args.dataset_root),
        json_out=Path(args.json) if args.json else None,
    )
    print(json.dumps({
        "arms": payload["arms"],
        "noise_floor": (
            {
                "read_by_both": payload["noise_floor"]["read_by_both"],
                "flipped_cases": payload["noise_floor"]["flipped_cases"],
                "flip_rate": payload["noise_floor"]["flip_rate"],
            }
            if payload["noise_floor"] else None
        ),
        "all_cases": payload["all_cases"],
        "stable_cases_only": payload["stable_cases_only"],
        "verdict": payload["verdict"],
        "localization": payload["localization"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
