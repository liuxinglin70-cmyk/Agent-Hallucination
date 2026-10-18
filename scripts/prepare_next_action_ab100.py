"""Freeze a new 100-case sample for the single-arm next-action validation.

Selection law (pre-registered):
- universe: the corpus minus every case that appears in a recent live run
  tree under ``artifacts/detector/live_checks`` or in the recent evaluation
  results under ``docs/results`` (the 113 cases already spent);
- eligible: a case must also carry a row in the archived pre-next-action
  baseline (``docs/results/full693-20260927/baseline_report.json``), so the
  same-case comparison against the detector before this mechanism exists;
- exactly 62 positives and 38 negatives, matching the prior validation mix;
- within each label, families are allocated proportionally to what remains
  available (largest remainder), then cases are drawn without replacement
  with a fixed seed.

The sample file records the universe, exclusions, every drawn case, and its
archived pre-mechanism baseline row.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.packet import build_packet  # noqa: E402
from detector.semantic_trace import task_sources  # noqa: E402

DATASET = ROOT / "datasets/raw/agenthallu/AgentHallu"
LIVE = ROOT / "artifacts/detector/live_checks"
RESULTS = ROOT / "docs/results"
BASELINE = RESULTS / "full693-20260927/baseline_report.json"
OUT = LIVE / "next_action_ab100_20261017"
SEED = "next-action-ab100-20261017"
POSITIVES = 62
NEGATIVES = 38


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label_of(raw) -> str:
    value = raw.get("is_hallucination")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower()
    raise ValueError(f"unusable is_hallucination label: {value!r}")


def recent_cases() -> set[str]:
    """Cases spent in the live runs and their archived evaluations."""
    used: set[str] = set()
    for path in LIVE.rglob("*.json"):
        try:
            data = read(path)
        except (ValueError, OSError):
            continue
        candidates = None
        if isinstance(data, dict):
            for key in ("cases", "case_list"):
                value = data.get(key)
                if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
                    candidates = value
                    break
            if candidates is None and isinstance(data.get("run"), dict):
                value = data["run"].get("cases")
                if isinstance(value, list):
                    candidates = value
        if candidates:
            used.update(candidates)
    for path in RESULTS.rglob("*.json"):
        if "full693-20260927" in path.parts:
            # The archived G0 report is the pre-mechanism reference for the new
            # sample; counting it as "already spent" would empty the universe.
            continue
        try:
            data = read(path)
        except (ValueError, OSError):
            continue
        rows = data.get("rows") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "case" in rows[0]:
            used.update(str(row["case"]) for row in rows)
    return used


def allocate(total: int, availability: dict[str, int]) -> dict[str, int]:
    """Proportional allocation with largest remainders, never above availability."""
    populations = sum(availability.values())
    if populations < total:
        raise ValueError("not enough cases for the requested sample")
    exact = {family: total * count / populations for family, count in availability.items()}
    allocation = {family: min(availability[family], int(value)) for family, value in exact.items()}
    remainder = total - sum(allocation.values())
    order = sorted(exact, key=lambda family: (-(exact[family] - int(exact[family])), family))
    while remainder > 0:
        progressed = False
        for family in order:
            if remainder == 0:
                break
            if allocation[family] < availability[family]:
                allocation[family] += 1
                remainder -= 1
                progressed = True
        if not progressed:
            break
    if sum(allocation.values()) != total:
        raise ValueError("allocation did not reach the requested total")
    return allocation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    if (out / "cases.json").exists():
        raise SystemExit(f"frozen sample already exists: {out / 'cases.json'}")

    corpus = {path.relative_to(DATASET).as_posix(): read(path)
              for path in sorted(DATASET.glob("*/*.json"))}
    used = recent_cases()
    unknown_used = sorted(used - set(corpus))
    if unknown_used:
        raise ValueError(f"used-case list names {len(unknown_used)} cases absent from the corpus")
    baseline = read(BASELINE)
    baseline_rows = {row["case"]: row for row in baseline["rows"]}
    remaining = {case: raw for case, raw in corpus.items() if case not in used}
    eligible = {case: raw for case, raw in remaining.items() if case in baseline_rows}
    # The mechanism forecasts from "the task turn active at this prefix".  A
    # multi-turn record whose history carries no turn metadata (the BFCL
    # recorder) cannot be aligned, so the forecast stage deliberately refuses
    # to emit a request there rather than risk feeding a later turn's
    # instruction into an earlier prefix.  Those records are therefore outside
    # the mechanism's scope and are excluded before any label or outcome is
    # looked at; leaving them in would make a quarter of the sample unable to
    # exercise the feature under test.
    single_turn = {}
    for case, raw in eligible.items():
        packet = build_packet({"question": raw.get("question"), "history": raw.get("history")})
        if len(task_sources(packet)) <= 1:
            single_turn[case] = raw
    eligible = single_turn
    labels = {"true": [], "false": []}
    for case, raw in eligible.items():
        labels[label_of(raw)].append(case)
    if len(labels["true"]) < POSITIVES or len(labels["false"]) < NEGATIVES:
        raise ValueError(f"not enough eligible cases: {len(labels['true'])} positive, {len(labels['false'])} negative")

    rng = random.Random(SEED)
    sample: list[str] = []
    families = sorted({case.split("/")[0] for case in eligible})
    for label, wanted in (("true", POSITIVES), ("false", NEGATIVES)):
        pool = labels[label]
        availability = Counter(case.split("/")[0] for case in pool)
        allocation = allocate(wanted, {family: availability[family] for family in families})
        for family in families:
            group = sorted(case for case in pool if case.startswith(family + "/"))
            sample.extend(sorted(rng.sample(group, allocation[family])))
    sample.sort()
    if len(sample) != 100 or len(set(sample)) != 100:
        raise ValueError("sample is not 100 unique cases")
    if set(sample) & used:
        raise ValueError("sample overlaps an earlier run")

    out.mkdir(parents=True, exist_ok=False)
    case_rows = [{
        "case": case,
        "family": case.split("/")[0],
        "gold": label_of(corpus[case]) == "true",
        "sha256": sha(DATASET / case),
        "pre_mechanism_baseline": {
            key: baseline_rows[case].get(key)
            for key in ("prediction", "terminal_prediction", "responsible_step",
                        "gold_step", "state", "authority")
        },
    } for case in sample]
    exclusion = {"count": len(used), "digest": hashlib.sha256(
        json.dumps(sorted(used), sort_keys=True).encode()).hexdigest()}
    (out / "sample.json").write_text(json.dumps({
        "schema_version": "research.next_action_single_arm_100_sample.v1",
        "created_from": "datasets/raw/agenthallu/AgentHallu",
        "selection": ("62 positives + 38 negatives; unseen in recent live runs; "
                      "present in the pre-next-action G0 baseline; "
                      "single-task-turn records only (multi-turn records without "
                      "turn metadata are outside the forecast stage's contract); "
                      "family-proportional within label; fixed seed"),
        "seed": SEED,
        "corpus_cases": len(corpus),
        "remaining_unseen_cases": len(remaining),
        "eligible_with_baseline": len(eligible),
        "excluded_used_cases": exclusion,
        "baseline_source": str(BASELINE.relative_to(ROOT)),
        "baseline_sha256": sha(BASELINE),
        "label_counts": dict(Counter(row["gold"] for row in case_rows)),
        "family_counts": dict(Counter(row["family"] for row in case_rows)),
        "cases": case_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "cases.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    positive = sum(row["gold"] for row in case_rows)
    print(json.dumps({
        "directory": str(out),
        "cases": len(sample),
        "positives": positive,
        "negatives": len(sample) - positive,
        "families": dict(Counter(row["family"] for row in case_rows)),
        "excluded_used": len(used),
        "remaining_unseen": len(remaining),
        "eligible_with_baseline": len(eligible),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
