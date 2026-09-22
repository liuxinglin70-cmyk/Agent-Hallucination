"""Read-only: would the measured errors form a diagnosable family?

Family identity is the mechanism signature, not the outcome kind: two
false negatives that arose by different paths are two families of one, and
``stable_failure_families`` keeps only groups of ``min_cases`` or more. So a
run can fail to produce a positive self-evolution result for a reason that has
nothing to do with the mechanism under test -- the errors were simply
heterogeneous. That distinction decides whether a "no family" outcome is a
finding about the framework or an artifact of the sample.

This computes the real signature for every archived error row and reports how
the errors group. It reads only.

    python output/selfevo-run/family_signature_scan.py --work-root artifacts/selfevo-bigrun
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.worksheet import (  # noqa: E402
    _mechanism_signature,
    failure_family,
)


def load(work_root: Path) -> list[dict]:
    rows = []
    for path in sorted(work_root.glob("checkpoints/**/arms/*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        row = payload.get("row")
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default="artifacts/selfevo-bigrun")
    parser.add_argument("--min-cases", type=int, default=2)
    args = parser.parse_args()

    rows = [r for r in load(Path(args.work_root)) if r.get("arm") or True]
    # One row per case: the arms repeat cases, and a family counts cases.
    by_case: dict[str, dict] = {}
    for row in rows:
        case = str(row.get("case") or "")
        by_case.setdefault(case, row)

    errors = []
    for case, row in by_case.items():
        gold, pred = row.get("gold"), row.get("prediction")
        if gold is not None and pred is not None and pred != gold:
            errors.append(row)
        elif pred is None:
            errors.append(row)

    print(json.dumps({
        "cases_seen": len(by_case),
        "errors": len(errors),
        "basis": "prediction != gold, plus unresolved (prediction is None)",
    }, ensure_ascii=False))

    if not errors:
        print(json.dumps({
            "verdict": "no_errors_in_this_sample",
            "meaning": "this arm made no error at all, so it can form no "
                       "failure family; a family needs a member",
        }, ensure_ascii=False))
        return 0

    families = collections.Counter()
    print(f"\n{'case':26s} {'gold':>5s} {'pred':>5s} {'family':34s} signature")
    for row in sorted(errors, key=lambda r: str(r.get("case"))):
        signature = _mechanism_signature(row)
        family = failure_family(row)
        families[family] += 1
        compact = ",".join(
            f"{k}={signature[k]}" for k in
            ("judge_positive", "upgrade_positive", "defense_verdict", "positive_source")
        )
        print(f"{str(row.get('case')):26s} {str(row.get('gold')):>5s} "
              f"{str(row.get('prediction')):>5s} {family:34s} {compact}")

    stable = {f: n for f, n in families.items() if n >= args.min_cases}
    print()
    print(json.dumps({
        "distinct_families": len(families),
        "families": dict(families),
        "families_with_at_least_min_cases": stable,
        "would_form_a_family": bool(stable),
        "reading": (
            "a family of >= %d would exist" % args.min_cases if stable
            else "every error has its own signature, so no family of >= %d "
                 "forms from this sample: the errors are heterogeneous, which "
                 "is a sample property and not evidence about the mechanism"
                 % args.min_cases
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
