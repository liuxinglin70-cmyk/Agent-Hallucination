"""Build the promotion batch by a rule fixed before any measurement.

Rule, fixed in advance and not adjusted after seeing any H0 or candidate result:

* benefit (the frozen evaluation set that decides the objective): the first
  ``--benefit`` gold-positive BFCL cases in case-ID order that were neither
  measured nor reviewed during the RSI pilot — i.e. candidate-unused;
* protection (the paired set the HARMLESSNESS gate protects): the first
  ``--protection`` gold-negative BFCL cases in case-ID order under the same
  exclusion.

Selection is by case id and label only. No candidate behaviour, no base
detector output and no mechanism fit enters the choice, which is what makes the
resulting batch a legitimate promotion set rather than a post-hoc pick.
"""

from __future__ import annotations

import argparse
import glob
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")

# Excluded because the pilot already measured or reviewed them (see
# reports/rsi_pilot_20260919/ and output/rsi-planning/*.frozen.json).
PILOT_MEASURED = {
    "BFCL/002.json", "BFCL/003.json", "BFCL/008.json", "BFCL/013.json",
    "BFCL/017.json", "BFCL/018.json", "BFCL/020.json", "BFCL/024.json",
    "BFCL/028.json", "BFCL/041.json", "BFCL/042.json", "BFCL/053.json",
    "BFCL/054.json", "BFCL/062.json", "BFCL/082.json", "BFCL/118.json",
    "BFCL/129.json", "BFCL/148.json", "BFCL/153.json",
}
PILOT_REVIEWED_OUT = {
    f"BFCL/{n:03d}.json" for n in
    [6, 21, 25, 49, 59, 87, 94, 104, 112, 115, 123, 126, 130, 133, 138,
     140, 144, 146, 151, 155, 156, 158, 159, 161]
}
EXCLUDED = PILOT_MEASURED | PILOT_REVIEWED_OUT


def gold(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benefit", type=int, default=14)
    parser.add_argument("--protection", type=int, default=6)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    positives: list[str] = []
    negatives: list[str] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "BFCL", "*.json"))):
        case_id = "BFCL/" + os.path.basename(path)
        if case_id in EXCLUDED:
            continue
        raw = json.load(open(path, encoding="utf-8-sig"))
        if gold(raw.get("is_hallucination")):
            positives.append(case_id)
        else:
            negatives.append(case_id)
    benefit = positives[: args.benefit]
    protection = negatives[: args.protection]
    if len(benefit) < args.benefit or len(protection) < args.protection:
        raise SystemExit(
            f"only {len(positives)} unused positives and {len(negatives)} "
            "unused negatives available"
        )
    payload = {
        "rule": (
            "first candidate-unused BFCL cases in case-ID order by label; no "
            "candidate output, no base-detector output and no mechanism fit "
            "entered the selection"
        ),
        "cases": [*benefit, *protection],
        "roles": {
            **{case: "benefit" for case in benefit},
            **{case: "protection" for case in protection},
        },
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"benefit   ({len(benefit)}): {benefit}")
    print(f"protection({len(protection)}): {protection}")
    print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
