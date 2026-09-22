"""Counterfactual replay: what would the four gates have said with a valid batch?

Read-only. No model calls. Uses the archived arms of the 2026-09-21 promotion
run (reports/promotion_20260921/) and re-derives the gate verdicts under a
repartitioned, mixed-polarity protection set, calling the same pure gate
functions the real run calls.

This is a diagnostic, not a result: the repartition is chosen after seeing the
rows, so it cannot certify anything. It exists to answer one question — was the
run's `promotion_unmeasurable` caused by the candidate or by the batch — and to
give the next run a concrete prediction to falsify.
"""

from __future__ import annotations

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
    gain_opportunity,
    localization_opportunity,
    paired_delta,
    paired_localization_delta,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(REPO, "reports", "promotion_20260921")


def load(name):
    return json.load(open(os.path.join(ARCHIVE, name), encoding="utf-8-sig"))


def main() -> int:
    protocol = load("protocol/promotion-protocol-real.json")
    control = {r["case"]: r for r in load("run/arms/control.json")["rows"]}
    candidate = {r["case"]: r for r in load("run/arms/candidate.json")["rows"]}
    config = load_config(os.path.join(REPO, "artifacts/promotion-real/config.json"))
    gold_steps = protocol.get("gold_steps") or {}
    artifact = json.load(open(
        os.path.join(ARCHIVE, "..", "rsi_pilot_20260919", "round4", "v001",
                     "candidate.json"),
        encoding="utf-8-sig",
    ))["candidate"][0]
    polarity = str(artifact["claim"]["target_polarity"])

    positives = sorted(c for c, r in control.items() if r["gold"] is True)
    negatives = sorted(c for c, r in control.items() if r["gold"] is False)
    control_correct_pos = [c for c in positives if control[c]["prediction"] is True]
    control_correct_neg = [c for c in negatives if control[c]["prediction"] is False]

    print(f"batch: {len(positives)} positives, {len(negatives)} negatives")
    print(f"control arm correct: {len(control_correct_pos)} positives, "
          f"{len(control_correct_neg)} negatives")
    print(f"claim polarity: {polarity}\n")

    # The old batch put every positive in benefit and every negative in
    # protection. A satisfiable HARMLESSNESS floor needs >=2 control-correct
    # positives in protection and >=2 control-correct negatives in protection.
    results = []
    for n_prot_pos in range(2, min(5, len(control_correct_pos)) + 1):
        for n_prot_neg in range(2, min(4, len(control_correct_neg)) + 1):
            # Worst case for the candidate: protection keeps the two FPs it
            # fixed, so the improvement it earned cannot be counted anywhere.
            prot = [*control_correct_pos[:n_prot_pos], *negatives[:n_prot_neg]]
            benefit = [c for c in control.items() if c[0] not in set(prot)]
            benefit = sorted(c for c, _ in benefit)

            ctrl_protection = [control[c] for c in prot]
            cand_protection = [candidate[c] for c in prot]
            ctrl_benefit = [control[c] for c in benefit]
            cand_benefit = [candidate[c] for c in benefit]

            coverage = sentinel_coverage(ctrl_protection, config)
            if not coverage["evaluable"]:
                continue

            harmless = harmlessness_gate(
                baseline_rows=ctrl_protection,
                candidate_rows=cand_protection,
                config=config,
            )
            if not harmless["passed"]:
                results.append((n_prot_pos, n_prot_neg, "HARMLESSNESS", 
                                harmless["reason"][:70], "", ""))
                continue

            behavior = behavior_gate(
                artifact,
                baseline_rows=[*ctrl_protection, *ctrl_benefit],
                candidate_rows=[*cand_protection, *cand_benefit],
                config=config,
            )
            detection = paired_delta(ctrl_benefit, cand_benefit)
            m_passed, m_failures = meets_m(detection, config=config)
            localization = paired_localization_delta(
                ctrl_benefit, cand_benefit, gold_steps=gold_steps,
            )
            loc_passed, loc_failures = meets_localization(
                localization, config=config, detection_delta=detection,
                target_kind="detection",
            )
            results.append((
                n_prot_pos, n_prot_neg,
                "BEHAVIOR" if not behavior["passed"] else "M",
                (behavior["reason"] if not behavior["passed"]
                 else "; ".join(m_failures))[:70],
                f"marginal={detection['marginal_cases']} "
                f"tp_lost={detection['tp_lost']} fp_added={detection['fp_added']}",
                f"joint_delta={localization['joint_delta']} "
                f"joint_broken={localization['joint_broken']}",
            ))

    seen = set()
    print(f"{'prot (+)':>8s} {'prot (-)':>8s} {'stops at':>11s} "
          f"{'reason':<72s}")
    for row in results:
        key = (row[2], row[3])
        label = "" if key not in seen else "  (same)"
        seen.add(key)
        print(f"{row[0]:8d} {row[1]:8d} {row[2]:>11s} {row[3]:<72s}{label}")
        if not label:
            print(f"{'':8s} {'':8s} {'':>11s} {row[4]:<40s} {row[5]}")

    print()
    print("unchanged: the FULL batch (protection = the 6 negatives as frozen)")
    coverage = sentinel_coverage(
        [control[c] for c in negatives], config,
    )
    print(f"  coverage {coverage['counts'] if 'counts' in coverage else coverage}")
    opp = gain_opportunity([control[c] for c in positives])
    loc_opp = localization_opportunity(
        [control[c] for c in positives], gold_steps=gold_steps,
    )
    print(f"  benefit opportunity: {opp}   localization: {loc_opp}")
    print(f"  localization on the full benefit set: joint_delta="
          f"{paired_localization_delta([control[c] for c in positives], [candidate[c] for c in positives], gold_steps=gold_steps)['joint_delta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
