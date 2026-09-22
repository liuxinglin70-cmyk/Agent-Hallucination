"""Diagnostic: per-case localization reading, split by arm (h0 vs candidate).

Read-only. Uses only archived result files under reports/. Answers one
question: for a case whose detection the candidate fixed, did the
responsible step also become correct?
"""

from __future__ import annotations

import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from evolution.splits import load_gold, load_gold_step  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")


def arm_of(path: str) -> str:
    norm = path.replace("\\", "/")
    for version in ("v001", "v002", "v003"):
        if f"/{version}/" in norm:
            return version
    if "/h0/" in norm or "/baseline/" in norm:
        return "h0"
    return "other"


def rows_for(case_id: str):
    pattern = os.path.join(REPO, "reports", "**", "results",
                           case_id.replace("/", "__"))
    for path in glob.glob(pattern, recursive=True):
        try:
            payload = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("prediction") is None:
            continue
        yield path, payload


def main() -> int:
    cases = sys.argv[1:] or [
        "BFCL/008.json", "BFCL/153.json", "BFCL/002.json",
        "BFCL/082.json", "BFCL/148.json",
    ]
    for case_id in cases:
        gold = load_gold(ROOT, case_id)
        gold_step = load_gold_step(ROOT, case_id)
        print("=" * 90)
        print(f"{case_id}  gold={gold}  gold_step={gold_step}")
        buckets: dict[str, collections.Counter] = collections.defaultdict(
            collections.Counter
        )
        convictions: dict[str, int] = collections.Counter()
        for path, payload in rows_for(case_id):
            arm = arm_of(path)
            if payload.get("prediction") is True:
                convictions[arm] += 1
                if gold is True:
                    step = payload.get("responsible_step")
                    if gold_step is not None and step is not None:
                        tag = (
                            "OK" if int(step) == int(gold_step)
                            else f"wrong(step {step})"
                        )
                    else:
                        tag = "unannotated"
                    buckets[arm][tag] += 1
        for arm in sorted(buckets):
            print(f"  {arm:8s} convictions={convictions[arm]:3d} "
                  f"localization={dict(buckets[arm])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
