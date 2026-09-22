"""Which localization failure signals are available WITHOUT reading gold steps?

Read-only. Scans archived detector results for signals a development loop could
legitimately use to form a localization task without the evaluator-side
annotation leaking into the detector or the proposer:

1. step/evidence self-consistency — the recorded responsible step vs the step
   its own cited evidence refs point at;
2. cross-repetition instability — the same case measured repeatedly naming
   different responsible steps;
3. gold disagreement — the evaluator-only reading, reported here for contrast
   (this is what must NOT be used to build a task).
"""

from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from evolution.splits import load_gold, load_gold_step  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")
REF = re.compile(r"event[_-]?0*(\d+)")


def archived() -> dict[str, list[tuple[str, dict]]]:
    out: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    for path in glob.glob(os.path.join(REPO, "reports", "**", "results", "*.json"),
                          recursive=True):
        base = os.path.basename(path)
        if base in {"summary.json", "identity.json"}:
            continue
        try:
            payload = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict) or "prediction" not in payload:
            continue
        out[base.replace("__", "/")].append((path, payload))
    return out


def main() -> int:
    data = archived()
    print(f"cases with archived results: {len(data)}\n")

    signal1: list[tuple[str, int, list[int], str]] = []
    signal2: list[tuple[str, list[int], int]] = []
    signal3: list[tuple[str, int, list[int], int]] = []

    for case_id, rows in sorted(data.items()):
        gold = load_gold(ROOT, case_id)
        gold_step = load_gold_step(ROOT, case_id)
        steps = collections.Counter()
        for _path, payload in rows:
            if payload.get("prediction") is not True:
                continue
            step = payload.get("responsible_step")
            if step is None:
                continue
            steps[int(step)] += 1
            # signal 1: does the row's own evidence refs agree with its step?
            result = payload.get("result") or {}
            proof = result.get("proof") or {}
            tracer = (
                proof.get("step_tracer")
                or (proof.get("hearings") or [{}])[0].get("tracer_result", {})
                .get("parsed")
                or {}
            )
            refs = tracer.get("evidence_refs") or []
            nums = sorted({int(m.group(1)) for ref in refs
                           for m in [REF.fullmatch(str(ref))] if m})
            if nums and int(step) not in nums:
                signal1.append((case_id, int(step), nums, str(refs)[:60]))
        if len(steps) > 1:
            signal2.append((case_id, sorted(steps), sum(steps.values())))
        if (
            gold is True and gold_step is not None and steps
            and set(steps) != {gold_step}
        ):
            signal3.append((case_id, gold_step, sorted(steps), sum(steps.values())))

    print("=== signal 1: responsible step outside its own cited evidence refs ===")
    for case_id, step, nums, refs in signal1:
        print(f"  {case_id:18s} step={step} refs->{nums}  {refs}")
    print(f"  total: {len(signal1)} rows\n")

    print("=== signal 2: cross-repetition instability (label-free) ===")
    for case_id, steps, n in signal2:
        print(f"  {case_id:18s} steps={steps} over {n} correct convictions")
    print(f"  total: {len(signal2)} cases\n")

    print("=== signal 3: gold disagreement (evaluator-only, NOT usable for tasks) ===")
    for case_id, gold_step, steps, n in signal3:
        print(f"  {case_id:18s} gold={gold_step} detector={steps} over {n} convictions")
    print(f"  total: {len(signal3)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
