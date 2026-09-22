"""Exhaustive counterfactual: every satisfiable repartition of the archived batch.

Read-only, no model calls. For each protection set that could have satisfied the
HARMLESSNESS coverage floor (>=2 control-correct positives and >=2
control-correct negatives), re-run the real gate sequence over the archived
rows of the 2026-09-21 promotion run and record where the sequence stops.

Answers: was `promotion_unmeasurable` forced by the candidate, or by the batch?
"""

from __future__ import annotations

import collections
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from evolution.config import load_config  # noqa: E402
from evolution.gates import (  # noqa: E402
    behavior_gate,
    harmlessness_gate,
    meets_localization,
    meets_m,
    sentinel_coverage,
)
from evolution.metrics import (  # noqa: E402
    paired_delta,
    paired_localization_delta,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(REPO, "reports", "promotion_20260921")


def main() -> int:
    protocol = json.load(open(
        os.path.join(ARCHIVE, "protocol/promotion-protocol-real.json"),
        encoding="utf-8-sig",
    ))
    control = {r["case"]: r for r in json.load(open(
        os.path.join(ARCHIVE, "run/arms/control.json"), encoding="utf-8-sig",
    ))["rows"]}
    candidate = {r["case"]: r for r in json.load(open(
        os.path.join(ARCHIVE, "run/arms/candidate.json"), encoding="utf-8-sig",
    ))["rows"]}
    config = load_config(os.path.join(REPO, "artifacts/promotion-real/config.json"))
    gold_steps = protocol.get("gold_steps") or {}
    artifact = json.load(open(
        os.path.join(ARCHIVE, "..", "rsi_pilot_20260919", "round4", "v001",
                     "candidate.json"),
        encoding="utf-8-sig",
    ))["candidate"][0]

    positives = sorted(c for c, r in control.items() if r["gold"] is True)
    negatives = sorted(c for c, r in control.items() if r["gold"] is False)
    ok_pos = [c for c in positives if control[c]["prediction"] is True]
    ok_neg = [c for c in negatives if control[c]["prediction"] is False]

    # The floor needs at least harmlessness_min_protected_tp / _tn
    # control-correct rows of each polarity; larger protection sets are allowed.
    stops: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    totals = 0
    for n_pos in range(2, len(ok_pos) + 1):
        for n_neg in range(2, len(ok_neg) + 1):
            for prot_pos in itertools.combinations(ok_pos, n_pos):
                for prot_neg in itertools.combinations(ok_neg, n_neg):
                    prot = set(prot_pos) | set(prot_neg)
                    benefit = [c for c in [*positives, *negatives]
                               if c not in prot]


                    ctrl_prot = [control[c] for c in sorted(prot)]
                    cand_prot = [candidate[c] for c in sorted(prot)]
                    ctrl_ben = [control[c] for c in benefit]
                    cand_ben = [candidate[c] for c in benefit]
                    coverage = sentinel_coverage(ctrl_prot, config)
                    if not coverage["evaluable"]:
                        continue
                    harmless = harmlessness_gate(
                        baseline_rows=ctrl_prot,
                        candidate_rows=cand_prot,
                        config=config,
                    )
                    if not harmless["passed"]:
                        stops["HARMLESSNESS"] += 1
                        reasons[harmless["reason"][:60]] += 1
                        totals += 1
                        continue
                    behavior = behavior_gate(
                        artifact,
                        baseline_rows=[*ctrl_prot, *ctrl_ben],
                        candidate_rows=[*cand_prot, *cand_ben],
                        config=config,
                    )
                    totals += 1
                    if not behavior["passed"]:
                        stops["BEHAVIOR"] += 1
                        reasons[behavior["reason"][:60]] += 1
                        continue
                    detection = paired_delta(ctrl_ben, cand_ben)
                    m_passed, m_failures = meets_m(detection, config=config)
                    if not m_passed:
                        stops["M"] += 1
                        reasons["; ".join(m_failures)[:60]] += 1
                    else:
                        stops["ADMITTED"] += 1

    print(f"satisfiable repartitions evaluated: {totals}")
    print(f"terminal stage distribution: {dict(stops)}\n")
    for reason, count in reasons.most_common():
        print(f"  {count:5d}x  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
